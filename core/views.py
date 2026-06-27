from django.http import JsonResponse, FileResponse, Http404
from django.utils import timezone
from django.db import transaction
from django.db import models
from django.db.models import Q, F
from rest_framework.views import APIView
from rest_framework.response import Response
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
import os
import json
from .models import Accountant, PayrollSendLog, DeliveryConfirmation
from .models import AttendanceFixRequest
from .serializers import AccountantSerializer, PayrollSendLogSerializer, DeliveryConfirmationSerializer

from django.utils.timezone import localdate

from datetime import datetime, timedelta
from django.utils import timezone
from django.db import models
from .models import DriverLocation, PayrollSendLog, StopPhoto

from .models import (
    CompanySettings, Manager, Driver, Truck,
    DailySchedule, Stop, Attendance, CraneSession,
    Payroll, NotificationLog, Document,
    TrackingLink, StopTask, Package, DeliverySheet, StopDocument,
)
from .serializers import AttendanceFixRequestSerializer
from .serializers import PackageSerializer, DeliverySheetSerializer
from . import location_url_parser
from .serializers import (
    CompanySettingsSerializer,
    ManagerSerializer, ManagerLoginSerializer,
    DriverSerializer, DriverListSerializer, DriverLoginSerializer,
    TruckSerializer, TruckListSerializer,
    DailyScheduleSerializer, DailyScheduleCreateSerializer,
    StopSerializer, StopUpdateSerializer, StopPhotoSerializer, StopDocumentSerializer,
    AttendanceSerializer, ClockInSerializer, ClockOutSerializer,
    CraneSessionSerializer, CraneStartSerializer, CraneEndSerializer,
    PayrollSerializer, PayrollSummarySerializer,
    NotificationLogSerializer, DocumentSerializer,
)
from .firebase_sync import publish_event
from .permissions import IsManager, IsAdmin, IsDriver, IsManagerOrDriver
from .auth_utils import (
    generate_token, store_manager_token,
    store_driver_token, revoke_token
)
from .firebase import (
    notify_manager_stop_skipped,
    notify_manager_stop_done,
    notify_manager_day_summary,
    notify_driver_payslip_ready,
    notify_driver_schedule_assigned,
    notify_driver_schedule_updated,
)
from .payroll_engine import generate_payroll

from django.shortcuts import get_object_or_404

from .models import ChildOfDriver, PayrollConfig, Payslip
from .serializers import (
    ChildOfDriverSerializer, PayrollConfigSerializer,
    PayslipSerializer, PayslipSummarySerializer
)

from .payroll_calc import generate_payslip, generate_all_payslips
import datetime

RELEASES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'releases')
# ──────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────

class ManagerLoginView(APIView):
    def post(self, request):
        ser = ManagerLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            manager = Manager.objects.get(username=ser.validated_data['username'], is_active=True)
        except Manager.DoesNotExist:
            return Response({'error': 'Invalid credentials'}, status=401)
        if not manager.check_password(ser.validated_data['password']):
            return Response({'error': 'Invalid credentials'}, status=401)
        token = generate_token()
        store_manager_token(token, manager.id)
        return Response({
            'token': token,
            'manager': ManagerSerializer(manager).data,
        })


class ManagerLogoutView(APIView):
    permission_classes = [IsManager]
    def post(self, request):
        token = request.headers.get('Authorization', '').replace('Token ', '')
        revoke_token(token, 'manager')
        return Response({'detail': 'Logged out'})


class VerifyRegistrationCodeView(APIView):
    """
    Step 1 of signup: validate the registration code.
    Code lives in .env (REGISTRATION_CODE) — never in the database.
    Returns 200 + company name on success.
    """
    def post(self, request):
        from django.conf import settings as django_settings

        code = request.data.get('code', '').strip()
        if not code:
            return Response({'error': 'Code is required.'}, status=400)

        env_code    = django_settings.REGISTRATION_CODE.strip()
        reg_enabled = django_settings.REGISTRATION_ENABLED

        if not reg_enabled:
            return Response({'error': 'Registration is currently disabled.'}, status=403)
        if not env_code:
            return Response({'error': 'No registration code configured on server. Contact your admin.'}, status=403)
        if env_code != code:
            return Response({'error': 'Invalid registration code.'}, status=403)

        company = CompanySettings.objects.first()
        company_name = company.company_name if company else 'TruckForce'
        return Response({'valid': True, 'company_name': company_name})


class ManagerRegisterView(APIView):
    """
    Step 2 of signup: create the manager account.
    Re-validates the code from .env on every request.
    New accounts are always role='manager' — no self-promotion to admin.
    """
    def post(self, request):
        from django.conf import settings as django_settings

        code        = request.data.get('registration_code', '').strip()
        env_code    = django_settings.REGISTRATION_CODE.strip()
        reg_enabled = django_settings.REGISTRATION_ENABLED

        if not reg_enabled:
            return Response({'error': 'Registration is disabled.'}, status=403)
        if not code or env_code != code:
            return Response({'error': 'Invalid registration code.'}, status=403)

        username = request.data.get('username', '').strip()
        if not username:
            return Response({'error': 'Username is required.'}, status=400)
        if Manager.objects.filter(username=username).exists():
            return Response({'error': 'Username already taken.'}, status=400)

        password = request.data.get('password', '')
        if len(password) < 6:
            return Response({'error': 'Password must be at least 6 characters.'}, status=400)

        data = {k: v for k, v in request.data.items()
                if k not in ('registration_code', 'password')}
        data['role'] = 'manager'

        ser = ManagerSerializer(data=data)
        ser.is_valid(raise_exception=True)
        manager = ser.save()
        manager.set_password(password)
        manager.save()

        token = generate_token()
        store_manager_token(token, manager.id)
        return Response({
            'token':   token,
            'manager': ManagerSerializer(manager).data,
        }, status=201)


class DriverLoginView(APIView):
    def post(self, request):
        ser = DriverLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            driver = Driver.objects.get(username=ser.validated_data['username'], is_active=True)
        except Driver.DoesNotExist:
            return Response({'error': 'Invalid credentials'}, status=401)
        if not driver.check_password(ser.validated_data['password']):
            return Response({'error': 'Invalid credentials'}, status=401)
        token = generate_token()
        store_driver_token(token, driver.id)
        return Response({
            'can_self_manage': (driver.can_self_manage or
                __import__('os').environ.get('SOLO_MODE', '').lower()
                in ('1', 'true', 'yes')),
            'token': token,
            'driver': DriverSerializer(driver).data,
        })


class DriverLogoutView(APIView):
    permission_classes = [IsDriver]
    def post(self, request):
        token = request.headers.get('Authorization', '').replace('Token ', '')
        revoke_token(token, 'driver')
        return Response({'detail': 'Logged out'})


# ──────────────────────────────────────────────
# COMPANY SETTINGS
# ──────────────────────────────────────────────

class CompanySettingsView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        from django.conf import settings as django_settings
        obj = CompanySettings.objects.first()
        if not obj:
            return Response({})
        data = CompanySettingsSerializer(obj).data
        # Mapbox public token — env-only (per-tenant Railway variable).
        # The driver app fetches it here after login to run the in-app
        # map/navigation. pk.* tokens are public by design, so exposing
        # to authenticated users is safe.
        data['mapbox_token'] = getattr(django_settings, 'MAPBOX_TOKEN', '')
        # Google geocoding key — same per-tenant env pattern. Used by the
        # app's search bar for Israeli address accuracy. Restricted to the
        # Geocoding API server-side, safe to hand to authenticated users.
        data['google_geocoding_key'] = getattr(django_settings, 'GOOGLE_GEOCODING_KEY', '')
        # Only admins see the current registration code — read from env, never DB
        if hasattr(request, 'manager') and request.manager is not None \
                and request.manager.role == 'admin':
            data['registration_code']    = django_settings.REGISTRATION_CODE
            data['registration_enabled'] = django_settings.REGISTRATION_ENABLED
        return Response(data)

    def put(self, request):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        # Strip out env-only fields — they can't be saved to DB
        safe_data = {k: v for k, v in request.data.items()
                     if k not in ('mapbox_token', 'google_geocoding_key',
                                  'registration_code', 'registration_enabled',
                                  'company_logo')}
        obj = CompanySettings.objects.first()
        # Logo arrives as a multipart file (the serializer's company_logo is
        # a read-only URL field, so the file is handled directly here).
        logo = request.FILES.get('company_logo')
        if logo is not None:
            obj.company_logo = logo
            obj.save(update_fields=['company_logo'])
        ser = CompanySettingsSerializer(obj, data=safe_data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)


# ──────────────────────────────────────────────
# MANAGER CRUD
# ──────────────────────────────────────────────

class ManagerListCreateView(APIView):
    permission_classes = [IsAdmin]

    def get(self, request):
        managers = Manager.objects.all().order_by('full_name')
        return Response(ManagerSerializer(managers, many=True).data)

    def post(self, request):
        ser = ManagerSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=201)


class ManagerDetailView(APIView):
    permission_classes = [IsAdmin]

    def get_object(self, pk):
        try:
            return Manager.objects.get(pk=pk)
        except Manager.DoesNotExist:
            return None

    def get(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        return Response(ManagerSerializer(obj).data)

    def put(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        ser = ManagerSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data)

    def delete(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        obj.delete()
        return Response(status=204)


# ──────────────────────────────────────────────
# DRIVER CRUD
# ──────────────────────────────────────────────

class DriverListCreateView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        qs = Driver.objects.all().order_by('full_name')
        active = request.query_params.get('active')
        if active is not None:
            qs = qs.filter(is_active=active.lower() == 'true')
        return Response(DriverListSerializer(qs, many=True).data)

    def post(self, request):
        ser = DriverSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=201)


class DriverDetailView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get_object(self, pk):
        try:
            return Driver.objects.get(pk=pk)
        except Driver.DoesNotExist:
            return None

    def get(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        # Driver can only see their own profile
        if hasattr(request, 'driver') and request.driver is not None and request.driver.id != obj.id:
            return Response({'error': 'Forbidden'}, status=403)
        return Response(DriverSerializer(obj).data)

    def put(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        ser = DriverSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        publish_event('drivers_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(ser.data)

    def delete(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        obj.is_active = False
        obj.save()
        publish_event('drivers_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(status=204)


class DriverUpdateFCMView(APIView):
    """Driver updates their own FCM token after app launch.

    A single physical device only ever has one FCM token. If a different
    driver previously logged in on the same phone, that driver's record
    still points at the same token — and would receive notifications meant
    for nobody. Before we save the token to the current driver, clear it
    from any *other* driver record so the token belongs to exactly one
    driver at a time.
    """
    permission_classes = [IsDriver]

    def post(self, request):
        token = request.data.get('fcm_token', '')
        if token:
            # Detach this token from any other driver who used to own it.
            # update() is one SQL statement and doesn't fire signals — fast
            # and safe even if zero matches.
            stolen = (Driver.objects
                      .filter(fcm_token=token)
                      .exclude(pk=request.driver.pk)
                      .update(fcm_token=''))
            if stolen:
                print(f"[FCM-REGISTER] reclaimed token from {stolen} other driver(s) "
                      f"for driver={request.driver.id}", flush=True)
        request.driver.fcm_token = token
        request.driver.save(update_fields=['fcm_token'])
        return Response({'detail': 'FCM token updated'})


# ──────────────────────────────────────────────
# TRUCK CRUD
# ──────────────────────────────────────────────

class TruckListCreateView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        qs = Truck.objects.all().order_by('brand', 'model')
        return Response(TruckListSerializer(qs, many=True).data)

    def post(self, request):
        ser = TruckSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=201)


class TruckDetailView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get_object(self, pk):
        try:
            return Truck.objects.get(pk=pk)
        except Truck.DoesNotExist:
            return None

    def get(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        return Response(TruckSerializer(obj).data)

    def put(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        ser = TruckSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        publish_event('trucks_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(ser.data)

    def delete(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        obj.status = 'retired'
        obj.save()
        return Response(status=204)


# ──────────────────────────────────────────────
# DAILY SCHEDULE
# ──────────────────────────────────────────────

class ScheduleListCreateView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        qs = DailySchedule.objects.all()
        date_str  = request.query_params.get('date')
        driver_id = request.query_params.get('driver')
        if date_str:
            qs = qs.filter(date=date_str)
        if driver_id:
            qs = qs.filter(driver_id=driver_id)
        return Response(DailyScheduleSerializer(qs, many=True).data)

    def post(self, request):
        print(f"[SCHEDULE-CREATE] payload keys: {list(request.data.keys())}", flush=True)
        if 'stops' in request.data:
            print(f"[SCHEDULE-CREATE] stops count: {len(request.data['stops'])}", flush=True)
            for i, s in enumerate(request.data['stops']):
                print(f"[SCHEDULE-CREATE]   stop #{i+1}: {s}", flush=True)
        ser = DailyScheduleCreateSerializer(data=request.data)
        if not ser.is_valid():
            print(f"[SCHEDULE-CREATE] VALIDATION ERRORS: {ser.errors}", flush=True)
            return Response(ser.errors, status=400)
        schedule = ser.save()
        print(f"[SCHEDULE-CREATE] ✓ created schedule id={schedule.id}", flush=True)
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))

        # Notify the driver — fire and forget. Wrap in try so any FCM
        # failure (network, bad token) never fails the schedule create.
        try:
            notify_driver_schedule_assigned(schedule.driver, schedule)
        except Exception as e:
            print(f"[SCHEDULE-CREATE] FCM notify failed: {e}", flush=True)

        return Response(DailyScheduleSerializer(schedule).data, status=201)


class ScheduleDetailView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get_object(self, pk):
        try:
            return DailySchedule.objects.get(pk=pk)
        except DailySchedule.DoesNotExist:
            return None

    def get(self, request, pk):
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None \
                and obj.driver_id != request.driver.id:
            return Response({'error': 'Forbidden'}, status=403)
        return Response(DailyScheduleSerializer(obj).data)

    def put(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)

        stops_before = obj.stops.count()  # capture before save
        ser = DailyScheduleSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))

        # Build a short human-readable summary of what changed so the
        # driver's notification body is useful. We only know the stop
        # count before/after — anything more would require deep-diffing
        # the JSON payload, which is overkill here.
        obj.refresh_from_db()
        stops_after = obj.stops.count()
        summary = _schedule_change_summary(stops_before, stops_after)
        try:
            notify_driver_schedule_updated(obj.driver, obj, summary)
        except Exception as e:
            print(f"[SCHEDULE-UPDATE] FCM notify failed: {e}", flush=True)

        return Response(ser.data)

    def delete(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)

        # Capture before delete so we can notify
        driver = obj.driver
        date = obj.date

        obj.delete()

        try:
            from .firebase import _log_and_send_driver
            title = "🗓️ סידור עבודה בוטל"
            body  = f"הסידור שלך לתאריך {date.strftime('%d/%m/%Y')} בוטל"
            data  = {
                "type": "schedule_cancelled",
                "date": date.isoformat(),
            }
            _log_and_send_driver(driver, 'schedule_changed', title, body, data)
        except Exception as e:
            print(f"[SCHEDULE-DELETE] FCM notify failed: {e}", flush=True)

        return Response(status=204)

    def patch(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)

        stops_before = obj.stops.count()
        ser = DailyScheduleSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))

        obj.refresh_from_db()
        stops_after = obj.stops.count()
        summary = _schedule_change_summary(stops_before, stops_after)
        try:
            notify_driver_schedule_updated(obj.driver, obj, summary)
        except Exception as e:
            print(f"[SCHEDULE-UPDATE] FCM notify failed: {e}", flush=True)

        return Response(ser.data)


def _schedule_change_summary(before: int, after: int) -> str:
    """Short human phrase describing how the stops list changed."""
    if after > before:
        n = after - before
        word = "עצירה" if n == 1 else "עצירות"
        return f"נוספה {word}" if n == 1 else f"נוספו {n} {word}"
    if after < before:
        n = before - after
        word = "עצירה" if n == 1 else "עצירות"
        return f"הוסרה {word}" if n == 1 else f"הוסרו {n} {word}"
    return "פרטי העצירות עודכנו"


class DriverTodayScheduleView(APIView):
    """Driver gets their own today's schedule."""
    permission_classes = [IsDriver]

    def get(self, request):
        today = localdate()
        try:
            schedule = DailySchedule.objects.get(driver=request.driver, date=today)
        except DailySchedule.DoesNotExist:
            return Response({'detail': 'No schedule for today'}, status=404)
        return Response(DailyScheduleSerializer(schedule).data)


class DriverScheduleByDateView(APIView):
    """Driver fetches their schedule for any date (past, present, or future)."""
    permission_classes = [IsDriver]

    def get(self, request, date):
        try:
            from datetime import date as date_cls
            d = date_cls.fromisoformat(date)
        except (ValueError, TypeError):
            return Response({'error': 'Invalid date format (expected YYYY-MM-DD)'}, status=400)
        try:
            schedule = DailySchedule.objects.get(driver=request.driver, date=d)
        except DailySchedule.DoesNotExist:
            return Response({'detail': 'No schedule for this date'}, status=404)
        return Response(DailyScheduleSerializer(schedule).data)


# ──────────────────────────────────────────────
# STOP CHECK-IN
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# STOPS — Manager CRUD (add/edit/delete individual stops)
# ──────────────────────────────────────────────

class StopDetailView(APIView):
    """Manager: edit or delete an individual stop."""
    permission_classes = [IsManager]

    def patch(self, request, pk):
        try:
            stop = Stop.objects.get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        # Editable fields for managers
        EDITABLE = {
            'order', 'site_name', 'address',
            'latitude', 'longitude',
            'expected_arrival', 'notes',
        }
        for field, value in request.data.items():
            if field in EDITABLE:
                setattr(stop, field, value)
        stop.save()
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(StopSerializer(stop).data)

    def delete(self, request, pk):
        try:
            stop = Stop.objects.get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        stop.delete()
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(status=204)


def _self_managing_driver(request):
    """The authenticated driver, when allowed to manage their own
    schedules. Two ways in: the per-driver flag, OR a server-wide
    SOLO_MODE env var — set on dedicated solo-driver Railway servers so
    every driver there self-manages with zero per-driver setup."""
    import os
    d = getattr(request, 'driver', None)
    if d is None:
        return None
    solo_server = os.environ.get('SOLO_MODE', '').lower() in ('1', 'true', 'yes')
    return d if (solo_server or getattr(d, 'can_self_manage', False)) else None


class DriverSelfScheduleView(APIView):
    """Solo-driver mode: a driver with can_self_manage creates a
    schedule FOR THEMSELVES (today or a future date) and adds stops to
    it. Address coordinates come from the phone (Mapbox geocoding)."""
    permission_classes = [IsManagerOrDriver]

    def post(self, request):
        # Any driver may add stops to HIS OWN day — basic ability, no
        # special permission. (He still can't touch anyone else's route.)
        driver = getattr(request, 'driver', None)
        if driver is None:
            return Response({'error': 'Drivers only'}, status=403)
        date_str = request.data.get('date')
        if not date_str:
            return Response({'error': 'date required'}, status=400)
        schedule, _created = DailySchedule.objects.get_or_create(
            driver=driver, date=date_str,
            defaults={'truck': driver.assigned_truck
                      if hasattr(driver, 'assigned_truck') else None})
        stops = request.data.get('stops') or []
        existing = schedule.stops.count()
        created = []
        for idx, st in enumerate(stops):
            if not st.get('site_name'):
                continue
            stop = Stop.objects.create(
                schedule=schedule,
                order=existing + idx + 1,
                site_name=st.get('site_name', ''),
                address=st.get('address', ''),
                latitude=st.get('latitude'),
                longitude=st.get('longitude'),
                notes=st.get('notes', ''),
                contact_name=st.get('contact_name', ''),
                contact_phone=st.get('contact_phone', ''),
                contact_email=st.get('contact_email', ''),
                items=st.get('items', ''),
                stop_type=st.get('stop_type', 'delivery'),
            )
            created.append(stop.id)
        return Response({
            'schedule': schedule.id,
            'created_stops': created,
        }, status=201)


class ScheduleStopsAddView(APIView):
    """Manager: add a new stop to an existing schedule."""
    permission_classes = [IsManager]

    def post(self, request, schedule_id):
        try:
            schedule = DailySchedule.objects.get(pk=schedule_id)
        except DailySchedule.DoesNotExist:
            return Response({'error': 'Schedule not found'}, status=404)

        data = dict(request.data)
        # Default order = end of list
        if 'order' not in data or not data.get('order'):
            data['order'] = (schedule.stops.aggregate(
                models.Max('order')
            )['order__max'] or 0) + 1

        EDITABLE = {
            'order', 'site_name', 'address',
            'latitude', 'longitude',
            'expected_arrival', 'notes',
            # Preserved so a reassigned (cloned) stop keeps its full detail.
            'stop_type', 'items', 'contact_name', 'contact_phone',
            'allow_driver_reorder',
        }
        clean = {k: v for k, v in data.items() if k in EDITABLE}
        if not clean.get('site_name'):
            return Response({'error': 'site_name is required'}, status=400)

        stop = Stop.objects.create(schedule=schedule, **clean)
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(StopSerializer(stop).data, status=201)


def _notify_stop_completion(driver, stop, status):
    """Notify all managers (FCM) and publish a desktop event when a driver
    marks a stop done or skipped. This drives the manager's done/missed
    toast notifications on the desktop, and FCM pushes for managers on mobile.
    Failures here never break the stop update."""
    managers = Manager.objects.filter(is_active=True)
    for m in managers:
        try:
            if status == 'skipped':
                notify_manager_stop_skipped(m, driver, stop)
            elif status == 'done':
                notify_manager_stop_done(m, driver, stop)
        except Exception as e:
            print(f"[STOP-NOTIFY] FCM to manager failed: {e}", flush=True)
    try:
        publish_event(
            'stop_done' if status == 'done' else 'stop_skipped',
            payload={
                'stop_id':     stop.id,
                'site_name':   getattr(stop, 'site_name', '') or '',
                'driver_name': getattr(driver, 'full_name', '') or '',
                'status':      status,
                'order':       getattr(stop, 'order', None),
                'skip_reason': getattr(stop, 'skip_reason', '') or '',
            },
            by_user_id=getattr(driver, 'id', None),
        )
    except Exception as e:
        print(f"[STOP-NOTIFY] publish_event failed: {e}", flush=True)


class StopUpdateView(APIView):
    """Driver marks a stop as done or skipped."""
    permission_classes = [IsDriver]

    def patch(self, request, pk):
        try:
            stop = Stop.objects.get(pk=pk, schedule__driver=request.driver)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        ser = StopUpdateSerializer(stop, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)

        new_status = request.data.get('status')
        now = timezone.now()
        if new_status == 'done':
            stop.completed_at = now
            # Auto-set actual_arrival if not already set by GPS detection
            if not stop.actual_arrival:
                stop.actual_arrival = now
        elif new_status == 'skipped':
            stop.completed_at = now
        elif new_status == 'pending':
            # UNDO — only allowed within 30 minutes of the action
            if stop.completed_at:
                age = (now - stop.completed_at).total_seconds() / 60
                if age > 30:
                    return Response(
                        {'error': f'Cannot undo: stop completed {int(age)} minutes ago (limit 30)'},
                        status=400
                    )
            stop.completed_at   = None
            stop.actual_arrival = None
            stop.skip_reason    = ''

        ser.save()

        # Notify managers (toast on desktop + FCM) for done / skipped stops.
        if new_status in ('done', 'skipped'):
            _notify_stop_completion(request.driver, stop, new_status)

        # Check if all stops are resolved → update schedule status
        schedule = stop.schedule
        stops    = schedule.stops.all()
        if all(s.status in ('done', 'skipped') for s in stops):
            missed = stops.filter(status='skipped').count()
            schedule.status = 'partial' if missed > 0 else 'completed'
            schedule.save()
            if missed > 0:
                managers = Manager.objects.filter(is_active=True)
                for manager in managers:
                    notify_manager_day_summary(manager, request.driver, schedule)

        publish_event('schedules_changed', by_user_id=getattr(getattr(request, 'driver', None), 'id', None))
        return Response(StopSerializer(stop).data)


class DriverReorderStopsView(APIView):
    """Driver reorders pending + flexible stops in their own schedule.

    Request body: {"stop_ids": [3, 1, 2, 5, 4]}  -- the new order.
    Rules enforced:
      - Only the schedule's own driver can reorder.
      - Only stops with status=pending AND allow_driver_reorder=True may move.
      - Stops that are done/skipped or have allow_driver_reorder=False keep
        their current `order` value (they're locked in place).
    """
    permission_classes = [IsDriver]

    def post(self, request, pk):
        try:
            schedule = DailySchedule.objects.get(pk=pk, driver=request.driver)
        except DailySchedule.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        stop_ids_raw = request.data.get('stop_ids', [])
        if not isinstance(stop_ids_raw, list):
            return Response({'error': 'stop_ids must be a list'}, status=400)
        try:
            stop_ids = [int(x) for x in stop_ids_raw]
        except (ValueError, TypeError):
            return Response({'error': 'stop_ids must be integers'}, status=400)

        # Load all stops for this schedule
        stops = list(schedule.stops.all())
        by_id = {s.id: s for s in stops}

        # Validation: all submitted IDs must belong to this schedule
        for sid in stop_ids:
            if sid not in by_id:
                return Response(
                    {'error': f'Stop {sid} does not belong to this schedule'},
                    status=400,
                )

        # Figure out which stops are LOCKED (won't move)
        locked = [s for s in stops
                  if s.status != 'pending' or not s.allow_driver_reorder]
        movable = [s for s in stops
                   if s.status == 'pending' and s.allow_driver_reorder]
        movable_ids = {s.id for s in movable}

        # From the submitted sequence, extract only the movable ids in order
        # (ignore any that were locked but accidentally included)
        submitted_movable = [sid for sid in stop_ids if sid in movable_ids]

        # Safety: must match size of movable set (no drops, no dupes)
        if set(submitted_movable) != movable_ids:
            return Response(
                {'error': 'Submitted order must contain exactly the set of movable stops'},
                status=400,
            )

        # Build the new full ordering:
        #   Locked stops keep their .order (sorted by it).
        #   Movable stops fill the remaining slots in submitted order.
        # We renumber 1..N using order of the FINAL list.
        # Strategy: sort locked stops by their current .order, then weave movables
        # into the positions they'd occupy.  Simpler: just renumber everything
        # with locked keeping relative position, movables sequenced.

        # Easiest correct implementation:
        # Interleave — iterate through positions 1..N. Walk through the OLD stop
        # sequence (by original order). At each locked slot, output that locked
        # stop. At each movable slot, output the next movable_id from submitted.
        old_sorted = sorted(stops, key=lambda s: s.order)
        movable_iter = iter(submitted_movable)

        # Assign new .order values
        with transaction.atomic():
            new_order = 1
            for s in old_sorted:
                if s.id in movable_ids:
                    next_movable_id = next(movable_iter)
                    mv = by_id[next_movable_id]
                    mv.order = new_order
                    mv.save(update_fields=['order'])
                else:
                    s.order = new_order
                    s.save(update_fields=['order'])
                new_order += 1

        publish_event('schedules_changed', by_user_id=getattr(request.driver, 'id', None))

        # Return refreshed schedule
        schedule.refresh_from_db()
        return Response(DailyScheduleSerializer(schedule).data)


# ──────────────────────────────────────────────
# ATTENDANCE (CLOCK IN / OUT)
# ──────────────────────────────────────────────

class AttendanceListView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        qs = Attendance.objects.all()
        # If it's a driver, scope to their own attendance automatically
        if hasattr(request, 'driver') and request.driver is not None:
            qs = qs.filter(driver=request.driver)
        else:
            # Manager — can filter by any driver
            driver_id = request.query_params.get('driver')
            if driver_id:
                qs = qs.filter(driver_id=driver_id)
        date_from = request.query_params.get('from')
        date_to   = request.query_params.get('to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        qs = qs.order_by('-date')
        return Response(AttendanceSerializer(qs, many=True).data)


class ClockInView(APIView):
    permission_classes = [IsDriver]

    def post(self, request):
        # Validate input FIRST before touching the DB
        ser = ClockInSerializer(data=request.data)
        if not ser.is_valid():
            print(f"[CLOCK-IN] Serializer errors: {ser.errors}", flush=True)
            return Response(ser.errors, status=400)

        # Auto-close any stale shift (>14h) belonging to this driver before
        # checking for "already clocked in". Without this, a forgotten
        # clock-out from days ago would lock the driver out forever.
        try:
            from .attendance_auto_close import close_stale_for_driver
            close_stale_for_driver(request.driver)
        except Exception as e:
            print(f"[CLOCK-IN] auto-close check failed: {e}", flush=True)

        # Check if driver has ANY open shift (no midnight barrier)
        open_att = Attendance.objects.filter(
            driver=request.driver,
            clock_in__isnull=False,
            clock_out__isnull=True,
        ).first()

        if open_att:
            return Response({
                'error': 'Already clocked in',
                'clock_in': open_att.clock_in.isoformat(),
                'date': str(open_att.date),
            }, status=400)

        # Create new record dated today (clock-in date)
        today = localdate()
        att = Attendance.objects.create(
            driver=request.driver,
            date=today,
            clock_in=timezone.now(),
            clock_in_lat=round(float(ser.validated_data['latitude']), 6) if ser.validated_data.get('latitude') else None,
            clock_in_lng=round(float(ser.validated_data['longitude']), 6) if ser.validated_data.get('longitude') else None,
        )
        print(f"[CLOCK-IN] driver={request.driver.id} date={today} time={att.clock_in}", flush=True)

        try:
            schedule = DailySchedule.objects.get(driver=request.driver, date=today)
            if schedule.status == 'pending':
                schedule.status = 'in_progress'
                schedule.save()
        except DailySchedule.DoesNotExist:
            pass

        publish_event('attendance_changed', by_user_id=getattr(request.driver, 'id', None))
        return Response(AttendanceSerializer(att).data)


class ClockOutView(APIView):
    permission_classes = [IsDriver]

    def post(self, request):
        # Find ANY open shift — no date filter (supports night shifts)
        att = Attendance.objects.filter(
            driver=request.driver,
            clock_in__isnull=False,
            clock_out__isnull=True,
        ).order_by('clock_in').last()

        if not att:
            return Response({'error': 'Not clocked in'}, status=400)

        ser = ClockOutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        att.clock_out     = timezone.now()
        att.clock_out_lat = round(float(ser.validated_data['latitude']), 6) if ser.validated_data.get('latitude') else None
        att.clock_out_lng = round(float(ser.validated_data['longitude']), 6) if ser.validated_data.get('longitude') else None
        att.notes         = ser.validated_data.get('notes', '')

        try:
            from .models import CompanySettings
            company = CompanySettings.objects.first()
            ot_threshold = float(company.overtime_threshold) if company else 8.0
            att.calculate_hours(overtime_threshold=ot_threshold, ot_125_limit=2.0)
        except Exception:
            pass

        att.save()
        print(f"[CLOCK-OUT] driver={request.driver.id} date={att.date} hours={att.total_hours}", flush=True)
        publish_event('attendance_changed', by_user_id=getattr(request.driver, 'id', None))
        return Response(AttendanceSerializer(att).data)

class AttendanceDetailView(APIView):
    """Manager can edit attendance records."""
    permission_classes = [IsManager]

    def get(self, request, pk):
        try:
            att = Attendance.objects.get(pk=pk)
        except Attendance.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        return Response(AttendanceSerializer(att).data)

    def put(self, request, pk):
        try:
            att = Attendance.objects.get(pk=pk)
        except Attendance.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        ser = AttendanceSerializer(att, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        obj.edited_by = request.manager
        obj.save(update_fields=['edited_by'])
        publish_event('attendance_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(AttendanceSerializer(obj).data)


# ──────────────────────────────────────────────
# CRANE SESSION
# ──────────────────────────────────────────────

class CraneStartView(APIView):
    permission_classes = [IsDriver]

    def post(self, request):
        ser = CraneStartSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        settings = CompanySettings.objects.first()
        price    = settings.crane_price_per_hour if settings else 0

        truck    = None
        schedule = None
        stop     = None
        if ser.validated_data.get('truck'):
            try:
                truck = Truck.objects.get(pk=ser.validated_data['truck'])
            except Truck.DoesNotExist:
                pass
        if ser.validated_data.get('schedule'):
            try:
                schedule = DailySchedule.objects.get(pk=ser.validated_data['schedule'])
            except DailySchedule.DoesNotExist:
                pass
        if ser.validated_data.get('stop'):
            try:
                stop = Stop.objects.get(pk=ser.validated_data['stop'])
            except Stop.DoesNotExist:
                pass

        session = CraneSession.objects.create(
            driver         = request.driver,
            truck          = truck,
            schedule       = schedule,
            stop           = stop,
            date           = timezone.now().date(),
            arrival_time   = timezone.now(),
            work_start     = timezone.now(),
            price_per_hour = price,
            notes          = ser.validated_data.get('notes', ''),
        )
        return Response(CraneSessionSerializer(session).data, status=201)


class CraneEndView(APIView):
    permission_classes = [IsDriver]

    def post(self, request, pk):
        try:
            session = CraneSession.objects.get(pk=pk, driver=request.driver)
        except CraneSession.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if session.work_end:
            return Response({'error': 'Already ended'}, status=400)

        ser = CraneEndSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        session.work_end = timezone.now()
        if ser.validated_data.get('notes'):
            session.notes = ser.validated_data['notes']

        settings       = CompanySettings.objects.first()
        rounding_rule  = settings.crane_rounding_rule if settings else 'half'
        session.save_with_billing(rounding_rule)

        return Response(CraneSessionSerializer(session).data)


class CraneSessionListView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        qs = CraneSession.objects.all()
        if hasattr(request, 'driver') and request.driver is not None:
            qs = qs.filter(driver=request.driver)
        else:
            driver_id = request.query_params.get('driver')
            if driver_id:
                qs = qs.filter(driver_id=driver_id)
        date_from = request.query_params.get('from')
        date_to   = request.query_params.get('to')
        if date_from:
            qs = qs.filter(date__gte=date_from)
        if date_to:
            qs = qs.filter(date__lte=date_to)
        return Response(CraneSessionSerializer(qs, many=True).data)


# ──────────────────────────────────────────────
# PAYROLL
# ──────────────────────────────────────────────

class PayrollGenerateView(APIView):
    permission_classes = [IsManager]

    def post(self, request):
        driver_id = request.data.get('driver_id')
        month     = int(request.data.get('month'))
        year      = int(request.data.get('year'))

        try:
            driver = Driver.objects.get(pk=driver_id)
        except Driver.DoesNotExist:
            return Response({'error': 'Driver not found'}, status=404)

        # Gather data
        attendance_qs = Attendance.objects.filter(
            driver=driver,
            date__month=month,
            date__year=year,
        )
        crane_qs = CraneSession.objects.filter(
            driver=driver,
            date__month=month,
            date__year=year,
        )

        data = generate_payroll(driver, month, year, attendance_qs, crane_qs)

        payroll, created = Payroll.objects.update_or_create(
            driver=driver, month=month, year=year,
            defaults={**data, 'generated_by': request.manager}
        )
        return Response(PayrollSerializer(payroll).data, status=201 if created else 200)


class PayrollListView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        qs = Payroll.objects.all()
        if hasattr(request, 'driver') and request.driver is not None:
            qs = qs.filter(driver=request.driver)
        else:
            driver_id = request.query_params.get('driver')
            if driver_id:
                qs = qs.filter(driver_id=driver_id)
        return Response(PayrollSummarySerializer(qs, many=True).data)


class PayrollDetailView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request, pk):
        try:
            payroll = Payroll.objects.get(pk=pk)
        except Payroll.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None \
                and payroll.driver_id != request.driver.id:
            return Response({'error': 'Forbidden'}, status=403)
        return Response(PayrollSerializer(payroll).data)

    def put(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        try:
            payroll = Payroll.objects.get(pk=pk)
        except Payroll.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        new_status = request.data.get('status')
        if new_status:
            payroll.status = new_status
            if new_status == 'paid':
                payroll.paid_at = timezone.now()
            payroll.save()
            if new_status == 'approved':
                notify_driver_payslip_ready(payroll.driver, payroll)
        return Response(PayrollSerializer(payroll).data)


# ──────────────────────────────────────────────
# NOTIFICATIONS
# ──────────────────────────────────────────────

class NotificationListView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        if hasattr(request, 'manager'):
            qs = NotificationLog.objects.filter(recipient_manager=request.manager)
        else:
            qs = NotificationLog.objects.filter(recipient_driver=request.driver)
        return Response(NotificationLogSerializer(qs[:50], many=True).data)

    def patch(self, request):
        """Mark all as read."""
        if hasattr(request, 'manager'):
            NotificationLog.objects.filter(
                recipient_manager=request.manager, read=False
            ).update(read=True)
        else:
            NotificationLog.objects.filter(
                recipient_driver=request.driver, read=False
            ).update(read=True)
        return Response({'detail': 'Marked as read'})


# ──────────────────────────────────────────────
# DOCUMENTS
# ──────────────────────────────────────────────

class DocumentListCreateView(APIView):
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        qs = Document.objects.all()
        if hasattr(request, 'driver') and request.driver is not None:
            qs = qs.filter(driver=request.driver)
        else:
            driver_id = request.query_params.get('driver')
            truck_id  = request.query_params.get('truck')
            if driver_id:
                qs = qs.filter(driver_id=driver_id)
            if truck_id:
                qs = qs.filter(truck_id=truck_id)
        return Response(DocumentSerializer(qs, many=True).data)

    def post(self, request):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        # `file` is a SerializerMethodField (read-only) on the serializer,
        # so DRF DROPS the uploaded file on save. Pull it from FILES and
        # hand it to the model explicitly.
        upload = request.FILES.get('file')
        if upload is None:
            return Response({'error': 'file is required'}, status=400)
        ser = DocumentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        # Upload explicitly and store Cloudinary's own secure_url —
        # correct by construction, immune to storage-class URL quirks.
        # Same proven pattern as invoices and phone scans.
        try:
            import cloudinary.uploader
            res = cloudinary.uploader.upload(
                upload, resource_type='auto', folder='documents',
                use_filename=True, unique_filename=True)
        except Exception as e:
            return Response({'error': f'upload failed: {e}'}, status=502)
        doc = ser.save(uploaded_by=request.manager, file=res['secure_url'])
        return Response(DocumentSerializer(doc).data, status=201)


class DocumentDetailView(APIView):
    permission_classes = [IsManager]

    def delete(self, request, pk):
        try:
            doc = Document.objects.get(pk=pk)
        except Document.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        doc.delete()
        return Response(status=204)


# ──────────────────────────────────────────────
# DASHBOARD STATS (manager desktop)
# ──────────────────────────────────────────────

class DashboardStatsView(APIView):
    permission_classes = [IsManager]

    def get(self, request):
        today = localdate()
        return Response({
            'total_drivers':      Driver.objects.filter(is_active=True).count(),
            'total_trucks':       Truck.objects.filter(status='active').count(),
            'clocked_in_today':   Attendance.objects.filter(date=today, clock_out__isnull=True).exclude(clock_in__isnull=True).count(),
            'schedules_today':    DailySchedule.objects.filter(date=today).count(),
            'missed_stops_today': Stop.objects.filter(schedule__date=today, status='skipped').count(),
            'active_cranes':      CraneSession.objects.filter(date=today, work_end__isnull=True).count(),
        })


class DriverLocationUpdateView(APIView):
    """Driver phone posts current GPS location while clocked in.

    DB hygiene: rows are only written when the driver has moved more
    than MIN_DISTANCE_METERS OR more than HEARTBEAT_SECONDS has passed
    since the last record. A parked truck no longer creates 120 rows/hour.
    """
    permission_classes = [IsDriver]

    # Tuning — kept here (not in CompanySettings) since they're network/DB
    # hygiene concerns, not business rules. Tweak if needed.
    MIN_DISTANCE_METERS = 20    # Below this, treat as "didn't move"
    HEARTBEAT_SECONDS   = 300   # Always record at least once every 5 minutes
    MAX_ACCURACY_METERS = 100   # Reject GPS fixes worse than this (defense-in-depth;
                                # app should already filter, but old clients exist)

    # Anti-jamming filter. Some areas have GPS jamming/spoofing — phone briefly
    # reports a position 100km away then snaps back. If two consecutive fixes
    # imply an implausibly high speed, the new one is fake. Only applied when
    # the previous fix is recent; longer gaps could legitimately span large
    # distances after a parked-then-driving sequence.
    MAX_REALISTIC_KMH        = 200    # truck physics ceiling
    JAMMING_WINDOW_SECONDS   = 600    # 10 minutes — filter only within this

    def post(self, request):
        driver = request.driver

        # Auto-close any stale shift (>17h) so we don't keep recording
        # locations against a forgotten shift. After this, the find-open-
        # shift query below correctly returns nothing for stale shifts.
        try:
            from .attendance_auto_close import close_stale_for_driver
            close_stale_for_driver(driver)
        except Exception as e:
            print(f"[LOCATION] auto-close check failed: {e}", flush=True)

        # Find any open shift — no date filter (supports night shifts)
        attendance = Attendance.objects.filter(
            driver=driver,
            clock_in__isnull=False,
            clock_out__isnull=True,
        ).first()

        if not attendance:
            return Response(
                {'error': 'Not clocked in'},
                status=status.HTTP_400_BAD_REQUEST
            )

        lat = request.data.get('latitude')
        lng = request.data.get('longitude')
        accuracy = request.data.get('accuracy')

        # ── Optional client-supplied timestamp ──
        # Live pings omit this and we use server now(). Replayed pings from
        # the app's offline buffer include the original recording time, so
        # the trail shows the truck where it actually was at that moment —
        # not where the queue happened to be flushed from.
        from django.utils.dateparse import parse_datetime
        raw_ts = request.data.get('recorded_at')
        client_ts = None
        if raw_ts:
            try:
                parsed = parse_datetime(raw_ts)
                if parsed is not None:
                    # Sanity: refuse future timestamps and anything > 48h old
                    # (the app caps at 24h; 48h gives us headroom for clock skew).
                    now = timezone.now()
                    if parsed <= now and (now - parsed).total_seconds() < 48 * 3600:
                        client_ts = parsed
            except (TypeError, ValueError):
                pass
        effective_ts = client_ts or timezone.now()

        # ── Reject GPS junk before doing any work ──
        # 200 OK (not 400) so the app doesn't treat it as a retryable failure
        # and queue it for resend. wrote_row=False makes the no-op explicit.
        if accuracy is not None:
            try:
                acc_val = float(accuracy)
                if acc_val <= 0 or acc_val > self.MAX_ACCURACY_METERS:
                    print(
                        f"[LOCATION] driver={driver.id} "
                        f"lat={lat} lng={lng} "
                        f"wrote=False reason=bad_accuracy({acc_val:.1f}m)",
                        flush=True,
                    )
                    return Response({
                        'ok': True,
                        'id': None,
                        'wrote_row': False,
                        'reason': 'low_accuracy',
                        'newly_arrived_stops': [],
                    }, status=status.HTTP_200_OK)
            except (TypeError, ValueError):
                pass  # missing/garbage accuracy — fall through, treat as unknown

        # ── Dedupe: skip DB write if driver hasn't moved meaningfully ──
        # We still run arrival-detection below so geofence transitions
        # within the 20m threshold still register.
        # For replayed (cached) pings we use the client's recorded_at as
        # the basis for the age calculation, so a backlog flush doesn't
        # collapse into a single row.
        from .geo_utils import haversine_meters
        from datetime import timedelta

        last = (DriverLocation.objects
                .filter(driver=driver)
                .order_by('-timestamp')
                .first())
        wrote_row = False
        loc = last

        # ── Anti-jamming filter ──
        # If the implied speed between the last DB row and the new fix
        # exceeds MAX_REALISTIC_KMH within JAMMING_WINDOW_SECONDS, the new
        # fix is almost certainly spoofed/jammed. Drop silently (200 OK +
        # wrote_row=False), don't write, don't run arrival detection.
        if last is not None and lat is not None and lng is not None:
            try:
                age_seconds = (effective_ts - last.timestamp).total_seconds()
                if 0 < age_seconds < self.JAMMING_WINDOW_SECONDS:
                    dist_meters = haversine_meters(
                        last.latitude, last.longitude, lat, lng)
                    implied_kmh = (dist_meters / age_seconds) * 3.6
                    if implied_kmh > self.MAX_REALISTIC_KMH:
                        print(
                            f"[LOCATION] driver={driver.id} "
                            f"lat={lat} lng={lng} "
                            f"wrote=False reason=jamming("
                            f"{dist_meters:.0f}m in {age_seconds:.0f}s "
                            f"= {implied_kmh:.0f} km/h)",
                            flush=True,
                        )
                        return Response({
                            'ok': True,
                            'id': None,
                            'wrote_row': False,
                            'reason': 'implausible_speed',
                            'newly_arrived_stops': [],
                        }, status=status.HTTP_200_OK)
            except (TypeError, ValueError):
                pass  # malformed numbers — fall through and let DB write fail loudly

        should_write = True
        if last is not None and lat is not None and lng is not None:
            distance = haversine_meters(last.latitude, last.longitude, lat, lng)
            age      = (effective_ts - last.timestamp).total_seconds()
            # Negative age means the replayed point pre-dates the last DB row
            # (out-of-order delivery). Always write in that case so we don't
            # lose historical points.
            if 0 <= age < self.HEARTBEAT_SECONDS and distance < self.MIN_DISTANCE_METERS:
                should_write = False

        if should_write:
            loc = DriverLocation.objects.create(
                driver=driver,
                latitude=lat,
                longitude=lng,
                speed=request.data.get('speed'),
                heading=request.data.get('heading'),
                accuracy=accuracy,
            )
            # Override auto_now_add with the client's timestamp if provided.
            # Update instead of save() to avoid a second hit on auto_now_add.
            if client_ts is not None:
                DriverLocation.objects.filter(pk=loc.pk).update(timestamp=client_ts)
                loc.timestamp = client_ts  # keep the local instance in sync
            wrote_row = True

        # Visible-in-log heartbeat — makes it possible to verify GPS is
        # flowing without querying the DB. One line per POST regardless
        # of whether we actually wrote a row.
        try:
            print(
                f"[LOCATION] driver={driver.id} "
                f"lat={lat} lng={lng} "
                f"wrote={wrote_row}",
                flush=True,
            )
        except Exception:
            pass

        # ── Auto-detect arrival at planned stops ──
        # Run on every POST, even when we skipped the DB insert. A small
        # movement within the 20m threshold can still cross a geofence.
        newly_arrived_ids = []
        if lat and lng:
            try:
                from .arrival_detection import check_arrivals_for_driver
                newly_arrived_ids = check_arrivals_for_driver(driver, lat, lng)
            except Exception as e:
                print(f"[ARRIVAL] detection error: {e}", flush=True)

        return Response({
            'ok': True,
            'id': loc.id if loc else None,
            'wrote_row': wrote_row,
            'newly_arrived_stops': newly_arrived_ids,
        }, status=status.HTTP_201_CREATED)


# ── Location history retention ──────────────────────────────────────────
# GPS pings power the live map and the trail replay, but grow forever.
# Keep a rolling window instead of deleting everything weekly/monthly:
# replay keeps working for the whole window, storage stays bounded.
# Runs lazily (no cron, matching the rest of the codebase) at most once a
# day, piggybacking on the manager opening the live map.
LOCATION_RETENTION_DAYS = 60


def _purge_old_locations():
    """Daily location cleanup. Runs in a background thread and deletes in
    small chunks with breathing room between them — a single giant DELETE
    can lock the locations table long enough to stall every worker (which
    froze the whole API once; never again)."""
    from django.core.cache import cache
    if cache.get('locations_purge_done'):
        return
    cache.set('locations_purge_done', 1, 60 * 60 * 24)  # once per day

    def _run():
        import time
        from datetime import timedelta
        try:
            cutoff = timezone.now() - timedelta(days=LOCATION_RETENTION_DAYS)
            total = 0
            while True:
                ids = list(
                    DriverLocation.objects
                    .filter(timestamp__lt=cutoff)
                    .values_list('id', flat=True)[:5000]
                )
                if not ids:
                    break
                deleted, _ = DriverLocation.objects.filter(id__in=ids).delete()
                total += deleted
                time.sleep(0.2)  # let other queries through between chunks
            if total:
                print(f'[LOCATIONS] purged {total} pings older than '
                      f'{LOCATION_RETENTION_DAYS} days', flush=True)
        except Exception as e:
            print(f'[LOCATIONS] purge failed: {e}', flush=True)

    import threading
    threading.Thread(target=_run, daemon=True).start()


class ActiveDriversLocationsView(APIView):
    """Manager desktop fetches all currently-clocked-in drivers with latest location + trail."""
    permission_classes = [IsManager]

    def get(self, request):
        _purge_old_locations()
        today = localdate()

        # Find all open shifts — no date filter (supports night shifts)
        active_attendances = Attendance.objects.filter(
            clock_in__isnull=False,
            clock_out__isnull=True,
        ).select_related('driver')

        result = []
        for att in active_attendances:
            driver = att.driver

            since = att.clock_in
            if since is None:
                continue

            trail_qs = DriverLocation.objects.filter(
                driver=driver, timestamp__gte=since
            ).order_by('timestamp')

            trail = [
                {'lat': float(l.latitude), 'lng': float(l.longitude)}
                for l in trail_qs
            ]

            latest = trail_qs.last()

            # Stops for the shift date (use att.date not today)
            stops_today = Stop.objects.filter(
                schedule__driver=driver,
                schedule__date=att.date,
            ).order_by('order')

            stops_data = [{
                'id': s.id,
                'order': s.order,
                'site_name': s.site_name,
                'address': s.address,
                'latitude': float(s.latitude) if s.latitude else None,
                'longitude': float(s.longitude) if s.longitude else None,
                'status': s.status,
            } for s in stops_today]

            # Truck info from shift date schedule
            try:
                daily_schedule = DailySchedule.objects.get(driver=driver, date=att.date)
                truck_plate = daily_schedule.truck.plate_number if daily_schedule.truck else None
                truck_model = f"{daily_schedule.truck.brand} {daily_schedule.truck.model}" if daily_schedule.truck else None
            except DailySchedule.DoesNotExist:
                truck_plate = None
                truck_model = None

            result.append({
                'driver_id':    driver.id,
                'driver_name':  driver.full_name,
                'phone':        driver.phone,
                'license_type': driver.license_type,
                'photo_url':    request.build_absolute_uri(driver.photo.url) if driver.photo else None,
                'truck_plate':  truck_plate,
                'truck_model':  truck_model,
                'clock_in':     att.clock_in.isoformat() if att.clock_in else None,
                'current_location': {
                    'lat':       float(latest.latitude) if latest else None,
                    'lng':       float(latest.longitude) if latest else None,
                    'speed':     latest.speed if latest else None,
                    'heading':   latest.heading if latest else None,
                    'timestamp': latest.timestamp.isoformat() if latest else None,
                } if latest else None,
                'trail': trail,
                'stops': stops_data,
            })

        return Response(result)


class NearestDriverView(APIView):
    """Manager pastes a destination coord; we return all clocked-in drivers
    ranked by straight-line distance to the destination.

    POST /api/locations/nearest-driver/
    Body: { "latitude": 32.7, "longitude": 35.3 }
    """
    permission_classes = [IsManager]

    def post(self, request):
        from .geo_utils import haversine_meters

        try:
            dest_lat = float(request.data.get('latitude'))
            dest_lng = float(request.data.get('longitude'))
        except (TypeError, ValueError):
            return Response(
                {'error': 'latitude and longitude required (numeric)'},
                status=400,
            )

        if not (-90 <= dest_lat <= 90 and -180 <= dest_lng <= 180):
            return Response({'error': 'coordinates out of range'}, status=400)

        # Find all clocked-in drivers
        active_attendances = Attendance.objects.filter(
            clock_in__isnull=False,
            clock_out__isnull=True,
        ).select_related('driver')

        candidates = []
        for att in active_attendances:
            driver = att.driver
            latest = (DriverLocation.objects
                      .filter(driver=driver)
                      .order_by('-timestamp')
                      .first())
            if not latest:
                # Clocked in but never sent a location — skip
                continue

            dist_m = haversine_meters(
                latest.latitude, latest.longitude,
                dest_lat, dest_lng,
            )
            candidates.append({
                'driver_id':   driver.id,
                'driver_name': driver.full_name,
                'lat':         float(latest.latitude),
                'lng':         float(latest.longitude),
                'last_seen':   latest.timestamp.isoformat(),
                'distance_m':  round(dist_m, 1),
                'distance_km': round(dist_m / 1000, 3),
            })

        # Sort closest first
        candidates.sort(key=lambda c: c['distance_m'])

        return Response({
            'destination': {'lat': dest_lat, 'lng': dest_lng},
            'count':       len(candidates),
            'drivers':     candidates,
        })


class AccountantListCreateView(APIView):
    """GET list of accountants, POST to create."""
    permission_classes = [IsManager]

    def get(self, request):
        accountants = Accountant.objects.all()
        serializer = AccountantSerializer(accountants, many=True)
        return Response(serializer.data)

    def post(self, request):
        # If marking this one as primary, un-primary all others
        if request.data.get('is_primary'):
            Accountant.objects.filter(is_primary=True).update(is_primary=False)
        serializer = AccountantSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AccountantDetailView(APIView):
    """GET/PUT/DELETE a single accountant."""
    permission_classes = [IsManager]

    def get(self, request, pk):
        acc = get_object_or_404(Accountant, pk=pk)
        return Response(AccountantSerializer(acc).data)

    def put(self, request, pk):
        acc = get_object_or_404(Accountant, pk=pk)
        if request.data.get('is_primary'):
            Accountant.objects.exclude(pk=pk).filter(is_primary=True).update(is_primary=False)
        serializer = AccountantSerializer(acc, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        acc = get_object_or_404(Accountant, pk=pk)
        acc.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PayrollSendLogListView(APIView):
    """GET send-log entries with optional filters."""
    permission_classes = [IsManager]

    def get(self, request):
        qs = PayrollSendLog.objects.all()
        # Filters
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if year:  qs = qs.filter(year=year)
        if month: qs = qs.filter(month=month)
        qs = qs[:200]  # cap
        return Response(PayrollSendLogSerializer(qs, many=True).data)


class PayrollConfigView(APIView):
    """GET/PUT the singleton PayrollConfig (tax brackets & rates)."""
    permission_classes = [IsManager]

    def get(self, request):
        cfg = PayrollConfig.get_config()
        return Response(PayrollConfigSerializer(cfg).data)

    def put(self, request):
        cfg = PayrollConfig.get_config()
        serializer = PayrollConfigSerializer(cfg, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save(updated_by=request.manager.username if hasattr(request, 'manager') else '')
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# ── Driver children (linked to a driver) ────────────────
class DriverChildrenView(APIView):
    """GET/POST children for a specific driver."""
    permission_classes = [IsManager]

    def get(self, request, driver_id):
        driver = get_object_or_404(Driver, pk=driver_id)
        return Response(ChildOfDriverSerializer(driver.children.all(), many=True).data)

    def post(self, request, driver_id):
        driver = get_object_or_404(Driver, pk=driver_id)
        data = request.data.copy()
        data['driver'] = driver.id
        serializer = ChildOfDriverSerializer(data=data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChildDetailView(APIView):
    """GET/PUT/DELETE a single child."""
    permission_classes = [IsManager]

    def put(self, request, pk):
        child = get_object_or_404(ChildOfDriver, pk=pk)
        serializer = ChildOfDriverSerializer(child, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        child = get_object_or_404(ChildOfDriver, pk=pk)
        child.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Payslip — list / generate / detail ──────────────────
class PayslipListView(APIView):
    """GET list of payslips, optionally filtered by year/month/driver."""
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        qs = Payslip.objects.select_related('driver').all()
        # Driver sees only their own payslips
        if hasattr(request, 'driver') and request.driver is not None:
            qs = qs.filter(driver=request.driver)
        else:
            year  = request.query_params.get('year')
            month = request.query_params.get('month')
            drv   = request.query_params.get('driver')
            if year:  qs = qs.filter(year=year)
            if month: qs = qs.filter(month=month)
            if drv:   qs = qs.filter(driver_id=drv)
        return Response(PayslipSummarySerializer(qs, many=True).data)


class PayslipDetailView(APIView):
    """GET/PUT/DELETE a single payslip."""
    permission_classes = [IsManager]

    def get(self, request, pk):
        ps = get_object_or_404(Payslip, pk=pk)
        return Response(PayslipSerializer(ps).data)

    def put(self, request, pk):
        ps = get_object_or_404(Payslip, pk=pk)
        # Apply override values from client
        ALLOWED_OVERRIDES = {
            'working_days', 'regular_hours', 'overtime_125_h', 'overtime_150_h',
            'crane_hours', 'travel_allowance', 'bonus', 'status', 'notes',
        }
        for field, value in request.data.items():
            if field in ALLOWED_OVERRIDES:
                setattr(ps, field, value)

        # Recalculate financials based on new work data
        from .payroll_calc import (
            calculate_income_tax, calculate_national_insurance,
            calculate_health_insurance, calculate_tax_points,
        )
        from .models import PayrollConfig
        cfg = PayrollConfig.get_config()
        driver = ps.driver

        base_rate = float(driver.base_rate or 0)
        ot_rate = float(driver.overtime_rate) if driver.overtime_rate else base_rate
        crane_rate = float(driver.crane_hourly_rate or 0)
        stype = driver.salary_type or 'monthly'

        reg_h = float(ps.regular_hours or 0)
        ot125_h = float(ps.overtime_125_h or 0)
        ot150_h = float(ps.overtime_150_h or 0)
        crane_h = float(ps.crane_hours or 0)
        days = int(ps.working_days or 0)

        if stype == 'monthly':
            base_pay = base_rate
            hourly_ot = ot_rate if driver.overtime_rate else (base_rate / 186 if base_rate else 0)
            ot125_pay = ot125_h * hourly_ot * float(cfg.overtime_125_rate)
            ot150_pay = ot150_h * hourly_ot * float(cfg.overtime_150_rate)
        elif stype == 'daily':
            base_pay = days * base_rate
            hourly_ot = ot_rate if driver.overtime_rate else (base_rate / 8 if base_rate else 0)
            ot125_pay = ot125_h * hourly_ot * float(cfg.overtime_125_rate)
            ot150_pay = ot150_h * hourly_ot * float(cfg.overtime_150_rate)
        else:  # hourly
            base_pay = reg_h * base_rate
            hourly_ot = ot_rate if driver.overtime_rate else base_rate
            ot125_pay = ot125_h * hourly_ot * float(cfg.overtime_125_rate)
            ot150_pay = ot150_h * hourly_ot * float(cfg.overtime_150_rate)

        crane_pay = crane_h * crane_rate
        travel = float(ps.travel_allowance or 0)
        bonus = float(ps.bonus or 0)
        gross = round(base_pay + ot125_pay + ot150_pay + crane_pay + travel + bonus, 2)

        tax_points = calculate_tax_points(driver, cfg=cfg)
        income_tax = calculate_income_tax(gross, tax_points, cfg=cfg)
        ni = calculate_national_insurance(gross, cfg=cfg)
        health = calculate_health_insurance(gross, cfg=cfg)
        pension = round(gross * float(driver.pension_percent or 0) / 100, 2) if getattr(driver, 'has_pension',
                                                                                        False) else 0.0
        study = round(gross * float(driver.study_fund_percent or 0) / 100, 2) if getattr(driver, 'has_study_fund',
                                                                                         False) else 0.0

        total_ded = round(income_tax + ni + health + pension + study, 2)

        ps.base_pay = round(base_pay, 2)
        ps.overtime_125_pay = round(ot125_pay, 2)
        ps.overtime_150_pay = round(ot150_pay, 2)
        ps.crane_pay = round(crane_pay, 2)
        ps.total_hours = round(reg_h + ot125_h + ot150_h, 2)
        ps.gross_pay = gross
        ps.tax_points_used = tax_points
        ps.income_tax = income_tax
        ps.national_ins = ni
        ps.health_ins = health
        ps.pension_emp = pension
        ps.study_fund_emp = study
        ps.total_deductions = total_ded
        ps.pension_employer = round(gross * 0.065, 2) if getattr(driver, 'has_pension', False) else 0.0
        ps.study_fund_employer = round(gross * 0.075, 2) if getattr(driver, 'has_study_fund', False) else 0.0
        ps.severance_employer = round(gross * 0.0833, 2) if getattr(driver, 'has_pension', False) else 0.0
        ps.net_pay = round(gross - total_ded, 2)
        ps.save()

        publish_event('payslips_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(PayslipSerializer(ps).data)

    def delete(self, request, pk):
        ps = get_object_or_404(Payslip, pk=pk)
        ps.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class PayslipUploadPDFView(APIView):
    """Upload a generated payslip PDF and attach it to the Payslip record.

    The desktop app generates the PDF locally (Hebrew RTL Israeli format)
    then POSTs the file here so it lives in Cloudinary alongside other
    documents. The model field uses RawMediaCloudinaryStorage so the file
    goes to the 'payslips/' folder in our Cloudinary account.

    POST /api/payslips/<pk>/upload-pdf/
    multipart/form-data:
        pdf_file: <binary>

    Response: { "id": pk, "pdf_url": "<cloudinary url>" }
    """
    permission_classes = [IsManager]

    def post(self, request, pk):
        ps = get_object_or_404(Payslip, pk=pk)
        pdf = request.FILES.get('pdf_file')
        if not pdf:
            return Response(
                {'error': 'pdf_file is required (multipart/form-data)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        # Optional: sanity-check extension. Cloudinary doesn't enforce mime.
        if not pdf.name.lower().endswith('.pdf'):
            return Response(
                {'error': 'file must be a .pdf'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Predictable storage filename so re-uploads overwrite cleanly.
        # Cloudinary appends its own random suffix anyway, but a stable
        # prefix makes the file easier to find in the dashboard.
        stable_name = f"payslip_{ps.driver_id}_{ps.year:04d}-{ps.month:02d}.pdf"
        ps.pdf_file.save(stable_name, pdf, save=True)
        print(f"[PAYSLIP-PDF] uploaded payslip={ps.id} → {ps.pdf_file.url}",
              flush=True)
        return Response({
            'id': ps.id,
            'pdf_url': ps.pdf_file.url if ps.pdf_file else None,
        })


class PayslipGenerateView(APIView):
    """
    POST to generate payslips.
    Body: { "year": 2026, "month": 4, "driver_id": <optional> }
    If driver_id omitted → generate for ALL active drivers.
    """
    permission_classes = [IsManager]

    def post(self, request):
        year = int(request.data.get('year') or 0)
        month = int(request.data.get('month') or 0)
        if not (year and 1 <= month <= 12):
            return Response(
                {'error': 'year and month (1-12) required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        driver_id = request.data.get('driver_id')
        try:
            if driver_id:
                driver = get_object_or_404(Driver, pk=driver_id)
                ps = generate_payslip(driver, year, month, save=True)
                publish_event('payslips_changed', by_user_id=getattr(request.manager, 'id', None))
                return Response(PayslipSerializer(ps).data)
            else:
                payslips = generate_all_payslips(year, month)
                publish_event('payslips_changed', by_user_id=getattr(request.manager, 'id', None))
                return Response({
                    'count': len(payslips),
                    'payslips': PayslipSummarySerializer(payslips, many=True).data,
                })
        except Exception as e:
            import traceback
            traceback.print_exc()
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ──────────────────────────────────────────────
# STOP PHOTOS (unlimited proof-of-delivery photos per stop)
# ──────────────────────────────────────────────

class StopDocumentListCreateView(APIView):
    """GET the documents on a stop; POST to add one (optionally with a file).
    Manager or driver; driver limited to their own schedule."""
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def _allowed(self, request, stop):
        if hasattr(request, 'driver') and request.driver is not None:
            return stop.schedule.driver_id == request.driver.id
        return True

    def get(self, request, pk):
        try:
            stop = Stop.objects.get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if not self._allowed(request, stop):
            return Response({'error': 'Forbidden'}, status=403)
        return Response(StopDocumentSerializer(
            stop.documents.all(), many=True, context={'request': request}).data)

    def post(self, request, pk):
        try:
            stop = Stop.objects.get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if not self._allowed(request, stop):
            return Response({'error': 'Forbidden'}, status=403)

        title = (request.data.get('title') or '').strip()
        if not title:
            return Response({'error': 'title required'}, status=400)

        doc = StopDocument(
            stop=stop,
            title=title,
            signer_name=(request.data.get('signer_name') or '').strip(),
            order=int(request.data.get('order') or 0),
        )

        upload = request.FILES.get('file')
        if upload:
            try:
                import cloudinary.uploader
                result = cloudinary.uploader.upload(
                    upload, resource_type='auto', folder='stop_documents',
                    use_filename=True, unique_filename=True,
                )
            except Exception as e:
                import traceback
                print(f"[STOP-DOC] ERROR upload:\n{traceback.format_exc()}", flush=True)
                return Response({'error': f'Upload failed: {e}'}, status=500)
            secure_url = result.get('secure_url') or result.get('url')
            if secure_url:
                doc.file = secure_url

        try:
            doc.save()
        except Exception as e:
            import traceback
            print(f"[STOP-DOC] ERROR save:\n{traceback.format_exc()}", flush=True)
            return Response({'error': f'Save failed: {e}'}, status=500)

        return Response(StopDocumentSerializer(
            doc, context={'request': request}).data, status=201)


class StopDocumentSignView(APIView):
    """POST a signature for one document and stamp it onto the document file.

    Accepts the signature PNG plus a normalized box position
    (sig_page, sig_x, sig_y, sig_w, sig_h). The signature is burned onto the
    actual file — PDF via the delivery-note stamper, image via PIL — so the
    signed paper itself carries the signature (mirrors the delivery-note flow).
    If position is omitted (older app build) a sensible default spot is used.
    If stamping fails for any reason we still record the detached signature, so
    signing never hard-fails."""
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request, pk):
        import io, traceback
        try:
            doc = StopDocument.objects.select_related(
                'stop', 'stop__schedule').get(pk=pk)
        except StopDocument.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None:
            if doc.stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)

        sig = request.FILES.get('signature') or request.FILES.get('signature_image')
        if not sig:
            return Response({'error': 'signature image required'}, status=400)
        try:
            sig_bytes = sig.read()
        except Exception:
            sig_bytes = None
        if not sig_bytes:
            return Response({'error': 'signature image required'}, status=400)

        import cloudinary.uploader
        # Keep the raw signature PNG as a record (same as before).
        try:
            sig_res = cloudinary.uploader.upload(
                io.BytesIO(sig_bytes), resource_type='image',
                folder='stop_doc_signatures',
                use_filename=True, unique_filename=True,
            )
            sig_url = sig_res.get('secure_url') or sig_res.get('url')
        except Exception:
            print(f"[STOP-DOC] ERROR signature upload:\n{traceback.format_exc()}", flush=True)
            return Response({'error': 'Signature upload failed'}, status=500)
        if not sig_url:
            return Response({'error': 'Upload returned no URL'}, status=500)

        # Signature box position on the document (normalized, top-left origin).
        def _f(key, default):
            try:
                return float(request.data.get(key, default))
            except (TypeError, ValueError):
                return default
        page = int(_f('sig_page', 0))
        nx, ny = _f('sig_x', 0.55), _f('sig_y', 0.82)
        nw, nh = _f('sig_w', 0.35), _f('sig_h', 0.10)

        # Stamp the signature onto the actual document file.
        signed_url = None
        try:
            import requests as _rq
            src = str(doc.file) if doc.file else ''
            if src.startswith('http'):
                r = _rq.get(src, timeout=20)
                if r.status_code == 200 and r.content:
                    raw = r.content
                    if raw[:5] == b'%PDF-':
                        from delivery_stamp import stamp_signature_on_note
                        signed = stamp_signature_on_note(
                            raw, sig_bytes, page, nx, ny, nw, nh)
                        up = cloudinary.uploader.upload(
                            io.BytesIO(signed), resource_type='raw',
                            folder='stop_documents_signed', format='pdf',
                            use_filename=True, unique_filename=True,
                        )
                    else:
                        from PIL import Image
                        base = Image.open(io.BytesIO(raw)).convert('RGBA')
                        W, H = base.size
                        sgn = Image.open(io.BytesIO(sig_bytes)).convert('RGBA')
                        bx = max(0, min(int(nx * W), W - 1))
                        by = max(0, min(int(ny * H), H - 1))
                        bw = max(1, min(int(nw * W), W - bx))
                        bh = max(1, min(int(nh * H), H - by))
                        sgn.thumbnail((bw, bh))
                        base.alpha_composite(sgn, (bx, by))
                        out = io.BytesIO()
                        base.convert('RGB').save(out, format='JPEG', quality=90)
                        up = cloudinary.uploader.upload(
                            io.BytesIO(out.getvalue()), resource_type='image',
                            folder='stop_documents_signed',
                            use_filename=True, unique_filename=True,
                        )
                    signed_url = up.get('secure_url') or up.get('url')
        except Exception:
            print(f"[STOP-DOC] WARN stamp failed (keeping original):\n{traceback.format_exc()}", flush=True)

        signer = (request.data.get('signer_name') or '').strip()
        if signer:
            doc.signer_name = signer
        doc.signature_image = sig_url
        if signed_url:
            # The signed document supersedes the blank original.
            doc.file = signed_url
        doc.signed_at = timezone.now()
        try:
            doc.save()
        except Exception as e:
            print(f"[STOP-DOC] ERROR save:\n{traceback.format_exc()}", flush=True)
            return Response({'error': f'Save failed: {e}'}, status=500)

        data = StopDocumentSerializer(doc, context={'request': request}).data
        data['pdf_url'] = signed_url or data.get('file_url')
        return Response(data)


class StopDocumentDeleteView(APIView):
    """DELETE a stop document. Driver limited to their own schedule."""
    permission_classes = [IsManagerOrDriver]

    def delete(self, request, pk):
        try:
            doc = StopDocument.objects.select_related(
                'stop', 'stop__schedule').get(pk=pk)
        except StopDocument.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None:
            if doc.stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)
        doc.delete()
        return Response(status=204)


class StopPhotoListCreateView(APIView):
    """GET photos for a stop, POST to upload a new photo. Both manager and driver allowed."""
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def get(self, request, stop_id):
        try:
            stop = Stop.objects.get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        # Driver can only view their own schedule's photos
        if hasattr(request, 'driver') and request.driver is not None:
            if stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)
        photos = stop.photos.all()
        return Response(StopPhotoSerializer(photos, many=True).data)

    def post(self, request, stop_id):
        try:
            stop = Stop.objects.get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None:
            if stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)

        image = request.FILES.get('image')
        if not image:
            return Response({'error': 'image file required'}, status=400)

        # Upload via Cloudinary SDK directly to avoid Django storage's
        # double-URL bug. We store the clean `secure_url` on the field.
        try:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                image,
                resource_type='image',
                folder='delivery_photos',
                use_filename=True,
                unique_filename=True,
            )
        except Exception as e:
            import traceback
            print(f"[STOP-PHOTO] ERROR cloudinary upload:\n{traceback.format_exc()}", flush=True)
            return Response({'error': f'Upload failed: {e}'}, status=500)

        secure_url = result.get('secure_url') or result.get('url')
        if not secure_url:
            return Response({'error': 'Upload returned no URL'}, status=500)

        # Save the DB row. Wrapped with a full traceback so that if THIS is
        # where the 500 was coming from (the file reaches Cloudinary but the
        # row never saves), the real reason is logged clearly instead of a
        # blank 500.
        try:
            photo = StopPhoto(stop=stop)
            photo.image = secure_url
            photo.save()
        except Exception as e:
            import traceback
            print(f"[STOP-PHOTO] ERROR save:\n{traceback.format_exc()}", flush=True)
            return Response({'error': f'Save failed: {e}'}, status=500)

        # Build the response directly from the clean Cloudinary URL. We do NOT
        # route the freshly-saved ImageField back through the serializer/_abs_url
        # here — secure_url is already the final, correct URL, and avoiding the
        # round-trip removes the other place a 500 could originate.
        return Response({
            'id':          photo.id,
            'stop':        stop.id,
            'image':       secure_url,
            'uploaded_at': photo.uploaded_at.isoformat() if photo.uploaded_at else None,
        }, status=201)


class StopPhotoDeleteView(APIView):
    """DELETE a stop photo. Manager: any photo. Driver: only their own schedule's photos."""
    permission_classes = [IsManagerOrDriver]

    def delete(self, request, pk):
        try:
            photo = StopPhoto.objects.get(pk=pk)
        except StopPhoto.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        # Driver check
        if hasattr(request, 'driver') and request.driver is not None:
            if photo.stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)
        photo.image.delete(save=False)
        photo.delete()
        return Response(status=204)


class StopDeliveryNoteView(APIView):
    """POST /api/stops/<pk>/delivery-note/    upload (multipart 'file')
       DELETE /api/stops/<pk>/delivery-note/  remove

    A "delivery note" is the document the client signs at handover. The
    manager attaches it from the assignments page when creating or
    editing a stop; the driver can view it from the app to confirm
    they're delivering the right paperwork. Later we'll layer signature
    capture on top and produce a flattened PDF in DeliveryConfirmation.

    Both manager and the assigned driver can upload — useful when a
    driver receives a paper delivery note and photographs it.
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def _get_stop(self, request, pk):
        try:
            stop = Stop.objects.select_related('schedule').get(pk=pk)
        except Stop.DoesNotExist:
            return None, Response({'error': 'Stop not found'}, status=404)
        # Driver may only touch their own schedule's stops.
        if hasattr(request, 'driver') and request.driver is not None:
            if stop.schedule.driver_id != request.driver.id:
                return None, Response({'error': 'Forbidden'}, status=403)
        return stop, None

    def post(self, request, pk):
        stop, err = self._get_stop(request, pk)
        if err:
            return err
        f = request.FILES.get('file') or request.FILES.get('pdf')
        if not f:
            return Response({'error': "'file' field required"}, status=400)
        # Light content-type sanity: we want PDFs in practice, but allow
        # client tools that send octet-stream by accepting anything
        # that *looks* like a PDF by its extension. Stronger validation
        # belongs in a virus scanner, not here.
        name = (f.name or '').lower()
        if not name.endswith('.pdf'):
            return Response({'error': 'PDF file required'}, status=400)

        # Why we bypass Django storage and use the Cloudinary SDK directly:
        # the Django storage layer has historically produced doubled URLs
        # in this project (e.g. `…/raw/upload/https://…/raw/upload/file.pdf`),
        # because some serializer-side helper concatenated an already-
        # absolute Cloudinary URL onto the storage prefix. Going straight
        # to the SDK gives us a single canonical URL we control, and we
        # store only the *secure_url* string on the field. The model's
        # FileField still works for reads — `.url` just echoes the string
        # back unchanged when it's already a full URL.
        try:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                f,
                resource_type='raw',
                folder='delivery_notes',
                use_filename=True,
                unique_filename=True,
                overwrite=False,
            )
        except Exception as e:
            print(f"[DELIVERY-NOTE] upload failed: {e}", flush=True)
            return Response({'error': f'Upload failed: {e}'}, status=500)

        secure_url = result.get('secure_url') or result.get('url')
        if not secure_url:
            return Response({'error': 'Upload returned no URL'}, status=500)

        # Replace any existing file so the row stays unique. We do this
        # *after* the new upload succeeds so we never end up with a stop
        # that has neither the old nor the new file.
        if stop.delivery_note_pdf:
            try:
                stop.delivery_note_pdf.delete(save=False)
            except Exception:
                # The previous URL may have been the broken double-prefixed
                # one — delete() can raise on that. Ignore and move on;
                # we're about to overwrite the column anyway.
                pass

        # Store the secure URL directly as the file's `name`. Because the
        # value already starts with `https://`, our `_abs_url` serializer
        # helper will short-circuit and return it verbatim — no
        # double-prefixing possible.
        stop.delivery_note_pdf = secure_url
        stop.save(update_fields=['delivery_note_pdf'])
        return Response({
            'ok':                True,
            'delivery_note_url': secure_url,
        }, status=201)

    def delete(self, request, pk):
        stop, err = self._get_stop(request, pk)
        if err:
            return err
        if stop.delivery_note_pdf:
            try:
                stop.delivery_note_pdf.delete(save=False)
            except Exception:
                pass
            stop.delivery_note_pdf = None
            stop.save(update_fields=['delivery_note_pdf'])
        return Response(status=204)


# ──────────────────────────────────────────────
# ATTENDANCE FIX REQUESTS
# ──────────────────────────────────────────────

class ZeroShiftListView(APIView):
    """List attendance records that were auto-closed to zero hours.

    A "zero shift" is an attendance row where the system filled in a fake
    clock_out (== clock_in) because the driver forgot to clock out at the
    end of their shift. The driver got credited for the day but the hours
    are 0, which makes payslips and reports inaccurate.

    The manager uses this list to manually correct each row by entering
    the real end time, after which the existing payslip should be
    regenerated to pick up the new hours.

    GET /api/attendance/zero-shifts/
        ?year=2026          (optional)
        &month=5            (optional, requires year)
        &driver_id=2        (optional)
        &resolved=false     (default false — show only unresolved)
    """
    permission_classes = [IsManager]

    def get(self, request):
        qs = Attendance.objects.filter(auto_closed=True).select_related('driver')
        # "zero shift" = clock_out is non-null but equal to clock_in (0h shift).
        # We also include rows where clock_out is null entirely, but the
        # auto-close path always sets clock_out so that's a sanity check.
        qs = qs.exclude(clock_in__isnull=True)
        qs = qs.filter(Q(clock_out=F('clock_in')) | Q(clock_out__isnull=True))

        # Optional filters
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        driver_id = request.query_params.get('driver_id')
        resolved = request.query_params.get('resolved', 'false').lower() == 'true'

        if year:
            try:
                qs = qs.filter(date__year=int(year))
            except (TypeError, ValueError):
                pass
        if month and year:
            try:
                qs = qs.filter(date__month=int(month))
            except (TypeError, ValueError):
                pass
        if driver_id:
            qs = qs.filter(driver_id=driver_id)
        # Resolved = the manager has already manually fixed it (auto_closed
        # flipped back to False after manual close). By default we hide
        # those; pass ?resolved=true to see history.
        # Note: our model has only one flag. We treat "no longer zero" as
        # resolved — handled implicitly by the F() filter above. So this
        # endpoint can only return unresolved rows currently. The flag is
        # kept for future extension.

        qs = qs.order_by('-date', 'driver__full_name')

        # Fetch the most-recent pending fix request per attendance to show
        # the driver's note (if any) alongside the row.
        results = []
        for att in qs:
            fix = (AttendanceFixRequest.objects
                   .filter(driver=att.driver, date=att.date, status='pending')
                   .order_by('-created_at')
                   .first())
            results.append({
                'id':                 att.id,
                'driver_id':          att.driver_id,
                'driver_name':        att.driver.full_name,
                'date':               att.date.isoformat(),
                'clock_in':           att.clock_in.isoformat() if att.clock_in else None,
                'clock_out':          att.clock_out.isoformat() if att.clock_out else None,
                'auto_closed':        att.auto_closed,
                'fix_request_id':     fix.id if fix else None,
                'fix_request_reason': fix.reason if fix else '',
            })
        return Response({'count': len(results), 'results': results})


class AttendanceManualCloseView(APIView):
    """Manager sets the real clock_out time for an attendance row.

    Used to fix shifts that were auto-closed to zero by the nightly
    auto-close job. After saving, the row's hours are recomputed and any
    pending fix-request for that day is auto-resolved. The Payslip for
    that month is NOT auto-regenerated — the manager regenerates it
    explicitly from the Salaries page.

    POST /api/attendance/<pk>/manual-close/
    Body:
        {
            "clock_out": "2026-05-15T17:30:00",   # required, ISO datetime
            "notes":     "Confirmed via phone"     # optional
        }
    """
    permission_classes = [IsManager]

    def post(self, request, pk):
        att = get_object_or_404(Attendance, pk=pk)
        clock_out_raw = request.data.get('clock_out')
        notes = request.data.get('notes', '') or ''

        if not clock_out_raw:
            return Response({'error': 'clock_out is required'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Parse ISO datetime. Accept "2026-05-15T17:30:00" or "...Z".
        try:
            from django.utils.dateparse import parse_datetime
            clock_out = parse_datetime(str(clock_out_raw))
            if clock_out is None:
                raise ValueError('unparseable')
        except (TypeError, ValueError):
            return Response({'error': f'invalid clock_out format: {clock_out_raw}'},
                            status=status.HTTP_400_BAD_REQUEST)
        # Make tz-aware if naive (assume local tz from settings)
        if timezone.is_naive(clock_out):
            clock_out = timezone.make_aware(clock_out)

        if not att.clock_in:
            return Response({'error': 'attendance has no clock_in to compare against'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Validate: clock_out after clock_in, and not absurdly far in the
        # future (more than 24h after clock_in is almost certainly a typo).
        if clock_out <= att.clock_in:
            return Response({'error': 'clock_out must be after clock_in'},
                            status=status.HTTP_400_BAD_REQUEST)
        max_shift_hours = 24
        delta_h = (clock_out - att.clock_in).total_seconds() / 3600
        if delta_h > max_shift_hours:
            return Response({
                'error': f'clock_out is {delta_h:.1f}h after clock_in '
                         f'(more than {max_shift_hours}h max). Re-check the time.'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Save the corrected times. Clear auto_closed so this row no longer
        # appears in the zero-shifts list. Recompute hour buckets.
        att.clock_out = clock_out
        att.auto_closed = False
        if notes:
            note_prefix = f"[Manual close by manager on {timezone.now().strftime('%Y-%m-%d %H:%M')}] "
            att.notes = (note_prefix + notes + ("\n\n" + att.notes if att.notes else ''))
        att.edited_by = getattr(request, 'manager', None)
        att.calculate_hours()
        att.save(update_fields=[
            'clock_out', 'auto_closed', 'notes', 'edited_by',
            'regular_hours', 'overtime_125_h', 'overtime_150_h',
        ])

        # Auto-resolve any pending fix request for this attendance day so
        # it doesn't keep nagging the manager. We don't touch fix requests
        # that are already approved/rejected.
        AttendanceFixRequest.objects.filter(
            driver=att.driver, date=att.date, status='pending'
        ).update(
            status='approved',
            decided_at=timezone.now(),
            decided_by=getattr(request, 'manager', None),
            manager_note='Auto-resolved by manual clock-out correction.',
        )

        print(f"[ATTENDANCE-MANUAL-CLOSE] driver={att.driver_id} date={att.date} "
              f"clock_out={att.clock_out.isoformat()} "
              f"hours={delta_h:.2f}", flush=True)

        return Response({
            'id':            att.id,
            'driver_id':     att.driver_id,
            'date':          att.date.isoformat(),
            'clock_in':      att.clock_in.isoformat(),
            'clock_out':     att.clock_out.isoformat(),
            'regular_hours': float(att.regular_hours),
            'overtime_125_h': float(att.overtime_125_h),
            'overtime_150_h': float(att.overtime_150_h),
            'auto_closed':   att.auto_closed,
        })


class AttendanceFixRequestListCreateView(APIView):
    """
    GET:   Manager sees all, driver sees only their own.
    POST:  Driver creates a fix request for a specific date.
    """
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        qs = AttendanceFixRequest.objects.all()
        if hasattr(request, 'driver') and request.driver is not None:
            qs = qs.filter(driver=request.driver)
        else:
            # Manager — can filter by status / driver
            status_f = request.query_params.get('status')
            driver_id = request.query_params.get('driver')
            if status_f:
                qs = qs.filter(status=status_f)
            if driver_id:
                qs = qs.filter(driver_id=driver_id)
        return Response(AttendanceFixRequestSerializer(qs, many=True).data)

    def post(self, request):
        # Only drivers create requests
        if not (hasattr(request, 'driver') and request.driver is not None):
            return Response({'error': 'Only drivers can submit fix requests'}, status=403)
        data = dict(request.data)
        data['driver'] = request.driver.id
        ser = AttendanceFixRequestSerializer(data=data)
        ser.is_valid(raise_exception=True)
        # Force status to pending and explicitly attach driver
        obj = AttendanceFixRequest.objects.create(
            driver=request.driver,
            date=ser.validated_data['date'],
            requested_clock_in=ser.validated_data.get('requested_clock_in'),
            requested_clock_out=ser.validated_data.get('requested_clock_out'),
            reason=ser.validated_data.get('reason', ''),
            status='pending',
        )
        publish_event('attendance_changed', by_user_id=getattr(request.driver, 'id', None))
        return Response(AttendanceFixRequestSerializer(obj).data, status=201)


class AttendanceFixRequestDecideView(APIView):
    """Manager approves or rejects a fix request."""
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            fr = AttendanceFixRequest.objects.get(pk=pk)
        except AttendanceFixRequest.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        if fr.status != 'pending':
            return Response({'error': 'Already decided'}, status=400)

        action = request.data.get('action')  # 'approve' or 'reject'
        note   = request.data.get('manager_note', '')

        if action == 'approve':
            # The manager may override the driver's requested times before they
            # are written to attendance. Fall back to the requested values when
            # a time isn't sent (keeps older clients working unchanged).
            from django.utils.dateparse import parse_datetime
            def _parse(val):
                if not val:
                    return None
                dt = parse_datetime(val)
                if dt and timezone.is_naive(dt):
                    dt = timezone.make_aware(dt, timezone.get_current_timezone())
                return dt
            new_in  = _parse(request.data.get('clock_in'))  or fr.requested_clock_in
            new_out = _parse(request.data.get('clock_out')) or fr.requested_clock_out

            # Apply the changes to the Attendance row
            att, _ = Attendance.objects.get_or_create(driver=fr.driver, date=fr.date)
            if new_in is not None:
                att.clock_in = new_in
            if new_out is not None:
                att.clock_out = new_out
            if att.clock_in and att.clock_out:
                att.calculate_hours()
            att.edited_by = request.manager
            att.save()

            fr.status = 'approved'
        elif action == 'reject':
            fr.status = 'rejected'
        else:
            return Response({'error': 'action must be approve or reject'}, status=400)

        fr.manager_note = note
        fr.decided_by   = request.manager
        fr.decided_at   = timezone.now()
        fr.save()

        publish_event('attendance_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(AttendanceFixRequestSerializer(fr).data)


# ──────────────────────────────────────────────
# DRIVER PASSWORD CHANGE
# ──────────────────────────────────────────────

class DriverChangePasswordView(APIView):
    permission_classes = [IsDriver]

    def post(self, request):
        old_pw = request.data.get('old_password', '')
        new_pw = request.data.get('new_password', '')
        if not new_pw or len(new_pw) < 4:
            return Response({'error': 'New password must be at least 4 characters'}, status=400)

        driver = request.driver
        if not driver.check_password(old_pw):
            return Response({'error': 'Current password is incorrect'}, status=400)

        driver.set_password(new_pw)
        driver.save()
        return Response({'ok': True})


# ─────────────────────────────────────────────
# DELIVERY CONFIRMATION — Signature + PDF + WhatsApp/Email
# ─────────────────────────────────────────────

def _send_confirmation_whatsapp(confirmation):
    """Send delivery confirmation via UltraMSG WhatsApp."""
    from django.conf import settings
    import requests

    phone = (confirmation.signed_by_phone or '').strip().replace('-','').replace(' ','')
    if not phone:
        return False

    token    = getattr(settings, 'ULTRAMSG_TOKEN', '')
    instance = getattr(settings, 'ULTRAMSG_INSTANCE', '')
    if not token or not instance:
        print('[WHATSAPP] ULTRAMSG_TOKEN or ULTRAMSG_INSTANCE not configured', flush=True)
        return False

    if phone.startswith('0'):
        phone = '972' + phone[1:]
    elif not phone.startswith('972'):
        phone = '972' + phone

    site_url  = getattr(settings, 'SITE_URL', '')
    media_url = getattr(settings, 'MEDIA_URL', '/media/')

    # Try to send PDF file via URL
    if site_url and confirmation.pdf_file:
        doc_url = site_url.rstrip('/') + media_url + str(confirmation.pdf_file)
        caption = (
            'Delivery Confirmation / ' + chr(0x05D0) + chr(0x05D9) + chr(0x05E9) + chr(0x05D5) + chr(0x05E8) + '\n'
            + 'Site: ' + confirmation.stop.site_name + '\n'
            + 'Signed by: ' + confirmation.signed_by_name
        )
        try:
            resp = requests.post(
                f'https://api.ultramsg.com/{instance}/messages/document',
                data={
                    'token': token,
                    'to': phone,
                    'document': doc_url,
                    'caption': caption,
                    'filename': f'confirmation_{confirmation.stop_id}.pdf',
                },
                timeout=15,
            )
            print(f'[WHATSAPP] sent to {phone}: {resp.status_code}', flush=True)
            return resp.status_code == 200
        except Exception as e:
            print(f'[WHATSAPP] error: {e}', flush=True)

    # Fallback: text message
    text = (
        'Delivery Confirmation\n'
        'Site: ' + confirmation.stop.site_name + '\n'
        'Address: ' + (confirmation.stop.address or '') + '\n'
        'Received by: ' + confirmation.signed_by_name + '\n'
        'Time: ' + (confirmation.created_at.strftime('%d/%m/%Y %H:%M') if confirmation.created_at else '')
    )
    try:
        resp = requests.post(
            f'https://api.ultramsg.com/{instance}/messages/chat',
            data={'token': token, 'to': phone, 'body': text},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f'[WHATSAPP] fallback error: {e}', flush=True)
        return False


def _send_confirmation_email(confirmation):
    """Send delivery confirmation PDF via email."""
    from django.core.mail import EmailMessage
    from django.conf import settings

    email = (confirmation.signed_by_email or '').strip()
    if not email:
        return False
    try:
        subject = 'Delivery Confirmation — ' + confirmation.stop.site_name
        body = (
            'Dear ' + confirmation.signed_by_name + ',\n\n'
            'Please find attached the delivery confirmation.\n'
            'Site: ' + confirmation.stop.site_name + '\n'
            'Address: ' + (confirmation.stop.address or '') + '\n'
            'Date: ' + str(confirmation.stop.schedule.date) + '\n\nThank you.'
        )
        msg = EmailMessage(
            subject=subject,
            body=body,
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@truckforce.app'),
            to=[email],
        )
        if confirmation.pdf_file:
            with open(confirmation.pdf_file.path, 'rb') as f:
                msg.attach(
                    f'delivery_confirmation_{confirmation.stop.id}.pdf',
                    f.read(),
                    'application/pdf',
                )
        msg.send()
        return True
    except Exception as e:
        print(f'[EMAIL] error: {e}', flush=True)
        return False


class DeliverySheetView(APIView):
    """The assignment-level delivery sheet — ONE shared PDF per schedule that
    every client signs. The manager OR the schedule's own driver may upload it.

    GET    -> current sheet (original + running signed copy + signature_count)
    POST   -> upload/replace the blank ORIGINAL pdf (resets the signing run)
    DELETE -> remove the sheet entirely
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def _get_schedule(self, request, pk):
        try:
            sched = DailySchedule.objects.get(pk=pk)
        except DailySchedule.DoesNotExist:
            return None, Response({'error': 'Schedule not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None:
            if sched.driver_id != request.driver.id:
                return None, Response({'error': 'Forbidden'}, status=403)
        return sched, None

    def get(self, request, pk):
        sched, err = self._get_schedule(request, pk)
        if err:
            return err
        sheet = getattr(sched, 'delivery_sheet', None)
        if not sheet:
            return Response({'error': 'No delivery sheet'}, status=404)
        return Response(DeliverySheetSerializer(
            sheet, context={'request': request}).data)

    def post(self, request, pk):
        sched, err = self._get_schedule(request, pk)
        if err:
            return err
        f = request.FILES.get('file')
        if not f:
            return Response({'error': 'file required'}, status=400)
        try:
            import cloudinary.uploader
            # Deterministic public_id => exactly one original asset per schedule.
            result = cloudinary.uploader.upload(
                f, resource_type='raw', folder='delivery_sheets',
                public_id=f'delivery_sheet_orig_{sched.id}',
                use_filename=False, unique_filename=False, overwrite=True,
            )
        except Exception as e:
            print(f"[DELIVERY-SHEET] upload failed: {e}", flush=True)
            return Response({'error': f'Upload failed: {e}'}, status=500)
        secure_url = result.get('secure_url') or result.get('url')
        if not secure_url:
            return Response({'error': 'Upload returned no URL'}, status=500)

        sheet, _created = DeliverySheet.objects.get_or_create(schedule=sched)
        # A fresh blank original starts a fresh signing run.
        sheet.original_pdf    = secure_url
        sheet.signed_pdf      = None
        sheet.signature_count = 0
        sheet.save()
        try:
            publish_event('schedules_changed',
                          by_user_id=getattr(getattr(request, 'driver', None), 'id', None))
        except Exception:
            pass
        return Response(DeliverySheetSerializer(
            sheet, context={'request': request}).data, status=201)

    def delete(self, request, pk):
        sched, err = self._get_schedule(request, pk)
        if err:
            return err
        sheet = getattr(sched, 'delivery_sheet', None)
        if sheet:
            for fld in ('original_pdf', 'signed_pdf'):
                old = getattr(sheet, fld)
                if old:
                    try:
                        old.delete(save=False)
                    except Exception:
                        pass
            sheet.delete()
        return Response(status=204)


class DeliverySheetSignView(APIView):
    """A client signs the shared assignment PDF.

    Stamp the signature onto the LATEST version (running signed copy if present,
    else the blank original), upload over the same Cloudinary asset, and REPLACE
    signed_pdf — bumping signature_count. blank -> client 1 signs -> that is the
    live copy -> client 2 signs THAT -> replaces again, and so on. Reuses the
    same stamper as the per-stop delivery-note signing.
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request, pk):
        try:
            sched = DailySchedule.objects.get(pk=pk)
        except DailySchedule.DoesNotExist:
            return Response({'error': 'Schedule not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None:
            if sched.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)

        sheet = getattr(sched, 'delivery_sheet', None)
        if not sheet or not sheet.original_pdf:
            return Response({'error': 'No delivery sheet to sign'}, status=400)

        signature_file = request.FILES.get('signature')
        if not signature_file:
            return Response({'error': 'signature image required'}, status=400)
        try:
            signature_file.seek(0)
            sig_bytes = signature_file.read()
        except Exception:
            sig_bytes = b''
        if not sig_bytes:
            return Response({'error': 'empty signature'}, status=400)

        def _f(key, default):
            try:
                return float(request.data.get(key, default))
            except (TypeError, ValueError):
                return default
        try:
            sig_page = int(float(request.data.get('sig_page', 0)))
        except (TypeError, ValueError):
            sig_page = 0
        sig_x = _f('sig_x', 0.55)
        sig_y = _f('sig_y', 0.82)
        sig_w = _f('sig_w', 0.30)
        sig_h = _f('sig_h', 0.10)

        def _url_of(field):
            try:
                if not field:
                    return ''
                nm = str(getattr(field, 'name', '') or '')
                return nm if nm.startswith('http') else field.url
            except Exception:
                return ''
        latest_url = _url_of(sheet.signed_pdf) or _url_of(sheet.original_pdf)
        if not latest_url:
            return Response({'error': 'sheet file missing'}, status=400)

        try:
            import requests as _rq
            from .delivery_stamp import stamp_signature_on_note
            resp = _rq.get(latest_url, timeout=30)
            if resp.status_code != 200 or not resp.content:
                return Response({'error': 'could not fetch current sheet'},
                                status=502)
            stamped = stamp_signature_on_note(
                resp.content, sig_bytes,
                page=sig_page, nx=sig_x, ny=sig_y, nw=sig_w, nh=sig_h,
            )
        except Exception as e:
            print(f"[SHEET-SIGN] stamp error: {e}", flush=True)
            return Response({'error': f'stamp failed: {e}'}, status=500)
        if not stamped:
            return Response({'error': 'stamp produced no output'}, status=500)

        def _upload_pdf_bytes(data, public_id):
            import os as _os, tempfile as _tempfile
            try:
                import cloudinary.uploader
                tmp = _tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
                try:
                    tmp.write(data)
                    tmp.close()
                    res = cloudinary.uploader.upload(
                        tmp.name, resource_type='raw', folder='delivery_sheets',
                        public_id=public_id, use_filename=False,
                        unique_filename=False, overwrite=True,
                    )
                finally:
                    try:
                        _os.unlink(tmp.name)
                    except OSError:
                        pass
                return res.get('secure_url') or res.get('url')
            except Exception as e:
                print(f"[SHEET-SIGN] upload failed: {e}", flush=True)
                return None

        secure_url = _upload_pdf_bytes(stamped, f'delivery_sheet_signed_{sched.id}')
        if not secure_url:
            return Response({'error': 'upload failed'}, status=500)

        sheet.signed_pdf      = secure_url
        sheet.signature_count = (sheet.signature_count or 0) + 1
        sheet.save(update_fields=['signed_pdf', 'signature_count', 'updated_at'])
        try:
            publish_event('schedules_changed',
                          by_user_id=getattr(getattr(request, 'driver', None), 'id', None))
        except Exception:
            pass

        return Response(DeliverySheetSerializer(
            sheet, context={'request': request}).data)


class StopSignatureView(APIView):
    """
    POST: Driver submits the signature PNG plus its position, and we stamp it
          straight onto the stop's delivery-note PDF (the document the manager
          attached). The signed PDF is saved to Cloudinary and the desktop
          office app is notified via the realtime event. WhatsApp delivery to
          the client now happens from the driver's own phone (free), so this
          endpoint no longer calls any paid WhatsApp API — it just returns the
          signed-PDF URL for the app to share.

          If the stop has no delivery note attached (or stamping fails), we
          fall back to the old behaviour: generate a standalone confirmation
          PDF with ReportLab.

    GET:  Returns the existing confirmation for this stop.
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def get(self, request, stop_id):
        try:
            stop = Stop.objects.get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        if hasattr(request, 'driver') and request.driver is not None:
            if stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)
        if not hasattr(stop, 'confirmation'):
            return Response({'error': 'No confirmation yet'}, status=404)
        return Response(DeliveryConfirmationSerializer(
            stop.confirmation, context={'request': request}).data)

    def post(self, request, stop_id):
        try:
            stop = Stop.objects.select_related('schedule').get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)

        if hasattr(request, 'driver') and request.driver is not None:
            if stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)

        if hasattr(stop, 'confirmation'):
            return Response({
                'error': 'Already signed',
                'confirmation': DeliveryConfirmationSerializer(
                    stop.confirmation, context={'request': request}).data,
            }, status=400)

        signature_file  = request.FILES.get('signature')
        signed_by_name  = request.data.get('signed_by_name', '').strip()
        signed_by_phone = request.data.get('signed_by_phone', '').strip()
        signed_by_email = request.data.get('signed_by_email', '').strip()

        if not signature_file:
            return Response({'error': 'signature image required'}, status=400)
        if not signed_by_name:
            return Response({'error': 'signed_by_name required'}, status=400)

        # ── Signature placement on the note (normalized, top-left origin) ──
        # The app sends fractions of the rendered page; defaults drop the
        # signature into the lower-right area if a client sends nothing.
        def _f(key, default):
            try:
                return float(request.data.get(key, default))
            except (TypeError, ValueError):
                return default
        try:
            sig_page = int(float(request.data.get('sig_page', 0)))
        except (TypeError, ValueError):
            sig_page = 0
        sig_x = _f('sig_x', 0.55)
        sig_y = _f('sig_y', 0.82)
        sig_w = _f('sig_w', 0.35)
        sig_h = _f('sig_h', 0.10)

        # Read the signature bytes once for stamping; we still keep the raw
        # PNG on the confirmation row for the record.
        try:
            signature_file.seek(0)
            sig_bytes = signature_file.read()
            signature_file.seek(0)
        except Exception:
            sig_bytes = b''

        conf = DeliveryConfirmation.objects.create(
            stop=stop,
            signed_by_name=signed_by_name,
            signed_by_phone=signed_by_phone,
            signed_by_email=signed_by_email,
            signature_image=signature_file,
        )

        # ── Build the documents ────────────────────────────────────────────
        # Every signed stop gets TWO PDFs:
        #   1. stamped — the manager's ORIGINAL delivery note with the
        #      client's signature stamped on it (when a note is attached)
        #   2. summary — the generated confirmation page (date, time,
        #      address, signature) — ALWAYS created
        stamped_bytes = None
        summary_bytes = None

        # CRITICAL: read .name BEFORE touching .url — the note field stores a
        # complete Cloudinary URL as its name (direct SDK upload), and calling
        # .url on it makes the storage layer nest it into a broken URL.
        note_url = ''
        try:
            note_field = stop.delivery_note_pdf
            if note_field:
                _name = str(getattr(note_field, 'name', '') or '')
                if _name.startswith('http'):
                    note_url = _name
                else:
                    note_url = note_field.url
        except Exception:
            note_url = ''

        if note_url and sig_bytes:
            try:
                import requests as _rq
                from .delivery_stamp import stamp_signature_on_note
                note_resp = _rq.get(note_url, timeout=30)
                if note_resp.status_code == 200 and note_resp.content:
                    stamped_bytes = stamp_signature_on_note(
                        note_resp.content, sig_bytes,
                        page=sig_page, nx=sig_x, ny=sig_y, nw=sig_w, nh=sig_h,
                    )
                else:
                    print(f'[STAMP] note download failed: '
                          f'HTTP {note_resp.status_code} {note_url[:140]}',
                          flush=True)
            except Exception as e:
                print(f'[STAMP] error: {e}', flush=True)
        elif not note_url:
            print(f'[STAMP] stop {stop.id} has no delivery note — '
                  f'confirmation page only', flush=True)

        # The confirmation page is always generated.
        try:
            from .delivery_pdf import generate_delivery_pdf
            # Pass the signature bytes we already hold — saves the PDF
            # generator a round-trip download of the asset we just uploaded
            # (which can stall on CDN propagation right after upload).
            summary_bytes = generate_delivery_pdf(conf, signature_bytes=sig_bytes)
        except Exception as e:
            print(f'[PDF] summary generation error: {e}', flush=True)

        # One final document: signed note + confirmation page appended.
        # No note → the confirmation page alone (old fallback behaviour).
        if stamped_bytes and summary_bytes:
            from .delivery_stamp import append_pdf
            final_bytes = append_pdf(stamped_bytes, summary_bytes)
        else:
            final_bytes = stamped_bytes or summary_bytes

        # ── Upload to Cloudinary ────────────────────────────────────────────
        def _upload_pdf(data, public_id):
            """Direct raw SDK upload — the same proven path as the
            delivery-note upload. The Django storage layer uploads PDFs as
            *image* assets, which Cloudinary refuses to deliver (and it
            mangles the public_id into a nested URL — the old doubled-URL
            bug all over again)."""
            import os as _os
            import tempfile as _tempfile
            try:
                import cloudinary.uploader
                tmp = _tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
                try:
                    tmp.write(data)
                    tmp.close()
                    result = cloudinary.uploader.upload(
                        tmp.name,
                        resource_type='raw',
                        folder='confirmation_pdfs',
                        public_id=public_id,
                        use_filename=False,
                        unique_filename=False,
                        overwrite=True,
                    )
                finally:
                    try:
                        _os.unlink(tmp.name)
                    except OSError:
                        pass
                url = result.get('secure_url') or result.get('url')
                if not url:
                    print(f'[CONFIRMATION] upload returned no URL '
                          f'({public_id})', flush=True)
                return url
            except Exception as e:
                print(f'[CONFIRMATION] PDF upload failed ({public_id}): {e}',
                      flush=True)
                return None

        # Upload the single merged document. Store the absolute URL
        # directly; _abs_url passes it through untouched on the read side.
        if final_bytes:
            url = _upload_pdf(final_bytes,
                              f'confirmation_{stop.id}_{conf.pk}.pdf')
            if url:
                conf.pdf_file = url
                conf.save(update_fields=['pdf_file'])

        # ── Email only (free SMTP) in the background. No paid WhatsApp API:
        #    the WhatsApp confirmation is sent from the driver's phone. ──────
        import threading
        _conf_pk = conf.pk
        def _send():
            from core.models import DeliveryConfirmation as DC
            c = DC.objects.get(pk=_conf_pk)
            email_ok = _send_confirmation_email(c)
            DC.objects.filter(pk=_conf_pk).update(email_sent=email_ok)
            print(f'[CONFIRMATION] Email={email_ok}', flush=True)
        threading.Thread(target=_send, daemon=True).start()

        publish_event('schedules_changed',
                      by_user_id=getattr(getattr(request, 'driver', None), 'id', None))
        return Response(
            DeliveryConfirmationSerializer(conf, context={'request': request}).data,
            status=201,
        )


# ─────────────────────────────────────────────
# DRIVER PROFILE PHOTO UPLOAD
# ─────────────────────────────────────────────

class DriverPhotoUploadView(APIView):
    """
    POST: Driver uploads their profile photo.
    Saves as base64 in photo_b64 (desktop will download + clear it).
    Also saves to media/profile/ so Flutter can load it immediately via URL.
    Only 1 photo per driver — always overwrites.
    """
    permission_classes = [IsDriver]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        # We upload via the Cloudinary SDK directly (NOT through Django's
        # FileField.save), because RawMediaCloudinaryStorage was building
        # a doubled URL — wrapping its own already-absolute URL inside
        # another `…/image/upload/…` prefix. Going SDK-direct gives us one
        # canonical `secure_url`, which we store as-is on the field.
        import base64

        image_file = request.FILES.get('photo')
        if not image_file:
            return Response({'error': 'photo file required'}, status=400)

        driver = request.driver

        # 1) Upload directly via Cloudinary SDK
        try:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                image_file,
                resource_type='image',
                folder='driver_photos',
                public_id=str(driver.id),  # stable per-driver id
                overwrite=True,            # replace previous photo
                invalidate=True,           # bust Cloudinary CDN cache
            )
        except Exception as e:
            print(f"[DRIVER-PHOTO] upload failed: {e}", flush=True)
            return Response({'error': f'Upload failed: {e}'}, status=500)

        secure_url = result.get('secure_url') or result.get('url')
        if not secure_url:
            return Response({'error': 'Upload returned no URL'}, status=500)

        # 2) Best-effort cleanup of any prior file. We do this AFTER the
        # new upload succeeds, so the row never has neither old nor new.
        if driver.photo:
            try:
                driver.photo.delete(save=False)
            except Exception:
                # Old URL might be a broken double-prefixed one — delete()
                # can raise on those. We're about to overwrite the field
                # value anyway, so ignore.
                pass

        # 3) Store the full secure URL as the field value. Because it
        # starts with `https://`, _abs_url in serializers will return it
        # verbatim — no double-prefixing possible.
        driver.photo = secure_url

        # 4) Also store base64 for desktop to pick up (legacy fast path).
        image_file.seek(0)
        raw  = image_file.read()
        b64  = base64.b64encode(raw).decode('utf-8')
        mime = image_file.content_type or 'image/jpeg'
        driver.photo_b64 = f'data:{mime};base64,{b64}'
        driver.save(update_fields=['photo', 'photo_b64'])

        # Fire Firebase event so desktop knows to download
        publish_event('drivers_changed', by_user_id=driver.id)

        # Return the raw stored value (already a clean https URL) — do NOT
        # use driver.photo.url because that goes through storage and can
        # re-wrap into a doubled URL on some Django/Cloudinary versions.
        return Response({
            'ok': True,
            'photo_url': str(driver.photo) if driver.photo else None,
        })

    def delete(self, request):
        """Driver removes their profile photo."""
        driver = request.driver
        if driver.photo:
            driver.photo.delete(save=False)
        driver.photo_b64 = ''
        driver.save(update_fields=['photo', 'photo_b64'])
        publish_event('drivers_changed', by_user_id=driver.id)
        return Response({'ok': True})


class DriverPhotoClearB64View(APIView):
    """
    Desktop calls this after successfully downloading the photo locally.
    Clears photo_b64 to save DB space.
    """
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            driver = Driver.objects.get(pk=pk)
        except Driver.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        driver.photo_b64 = ''
        driver.save(update_fields=['photo_b64'])
        return Response({'ok': True})




class DriverPingView(APIView):
    """Manager pings a driver → Firebase event → driver sends location immediately."""
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            driver = Driver.objects.get(pk=pk)
        except Driver.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)
        # Fire Firebase event targeting this specific driver
        publish_event('ping_driver', by_user_id=getattr(request.manager, 'id', None),
                      extra={'driver_id': pk})
        return Response({'ok': True, 'driver_id': pk})




@require_GET
def app_version(request):
    """
    GET /api/version/
    No auth — desktop checks this on startup.
    Latest version + download link come from env vars (set on Railway), so they
    survive redeploys (the old version.json in RELEASES_DIR did not).
    The .exe itself is hosted on our website; download_url points there.
    """
    from django.conf import settings
    return JsonResponse({
        'version':      getattr(settings, 'DESKTOP_VERSION', '1.0.0'),
        'download_url': getattr(settings, 'DESKTOP_DOWNLOAD_URL', ''),
        'exe_url':      getattr(settings, 'DESKTOP_DOWNLOAD_URL', ''),  # alias for older clients
        'notes':        getattr(settings, 'DESKTOP_UPDATE_NOTES', ''),
        'force_update': getattr(settings, 'DESKTOP_FORCE_UPDATE', False),
    })


def download_release(request, filename):
    """
    GET /api/downloads/<filename>
    No auth — driver/manager downloads directly.
    """
    safe = os.path.basename(filename)
    path = os.path.join(RELEASES_DIR, safe)
    if not os.path.exists(path):
        raise Http404(f'{safe} not found')
    return FileResponse(open(path, 'rb'), as_attachment=True, filename=safe)


class UploadReleaseView(APIView):
    """
    POST /api/upload-release/
    Called by GitHub Actions after building EXE or APK.
    Requires manager token.
    """
    permission_classes = [IsManager]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        file_obj  = request.FILES.get('file')
        file_type = request.data.get('type', '')
        version   = request.data.get('version', '').strip()

        if not file_obj or not version:
            return Response({'error': 'file and version required'}, status=400)

        os.makedirs(RELEASES_DIR, exist_ok=True)

        ext      = 'exe' if file_type == 'exe' else 'apk'
        filename = f'TruckForce-v{version}.{ext}'
        dest     = os.path.join(RELEASES_DIR, filename)

        with open(dest, 'wb') as f:
            for chunk in file_obj.chunks():
                f.write(chunk)

        size_mb = os.path.getsize(dest) / 1024 / 1024
        print(f"[RELEASE] Saved {filename} ({size_mb:.1f}MB)", flush=True)

        # Update version.json
        from django.conf import settings
        base_url = getattr(settings, 'SITE_URL', '').rstrip('/')
        ver_file = os.path.join(RELEASES_DIR, 'version.json')

        try:
            with open(ver_file) as f:
                data = json.load(f)
        except Exception:
            data = {}

        data['version'] = version
        if file_type == 'exe':
            data['exe_url'] = f"{base_url}/api/downloads/{filename}"
        elif file_type == 'apk':
            data['apk_url'] = f"{base_url}/api/downloads/{filename}"

        with open(ver_file, 'w') as f:
            json.dump(data, f, indent=2)

        print(f"[RELEASE] version.json → {version}", flush=True)
        return Response({'ok': True, 'version': version, 'file': filename})



"""
Tracking views — add to core/views.py

Also add to core/urls.py:
    path('track/<str:token>/',        views.tracking_page),
    path('api/track/<str:token>/data/', views.tracking_data),
    path('api/tracking-links/',         views.TrackingLinkListCreateView.as_view()),
    path('api/tracking-links/<int:pk>/revoke/', views.TrackingLinkRevokeView.as_view()),
"""

from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_GET


"""
Updated tracking_page view — replace tracking_page() in tracking_views.py
Now includes:
- ETA calculation
- Client notes upload
- Stop-specific tracking
"""


@require_GET
def tracking_page(request, token):
    """Public-facing client tracking page.

    Renders a single Hebrew-RTL HTML page that, once the client opens the
    shared link, polls our /api/track/<token>/data/ endpoint every 30s
    and updates a live map with the truck position + the client's stop +
    anonymized dots for the route's other stops.
    """
    try:
        link = TrackingLink.objects.select_related(
            'driver', 'target_stop'
        ).get(token=token)
    except TrackingLink.DoesNotExist:
        return HttpResponse(
            '<h2 style="color:#fff;font-family:sans-serif;text-align:center;'
            'margin-top:40px;">קישור לא נמצא</h2>',
            status=404,
        )

    if not link.is_valid():
        return HttpResponse(
            '<html dir="rtl" style="background:#0a0a0a;color:#fff;'
            'font-family:sans-serif;display:flex;align-items:center;'
            'justify-content:center;height:100vh;margin:0;">'
            '<div style="text-align:center;">'
            '<div style="font-size:48px;">⏱</div>'
            '<h2 style="color:#F5A623;">הקישור פג תוקף</h2>'
            '<p style="color:#888;">פנה לחברה לקבלת קישור חדש</p>'
            '</div></html>',
            status=410,
        )

    from django.conf import settings
    driver       = link.driver
    site_url     = getattr(settings, 'SITE_URL', '').rstrip('/')
    mapbox_token = getattr(settings, 'MAPBOX_TOKEN', '')
    target_stop  = getattr(link, 'target_stop', None)
    stop_id      = target_stop.id if target_stop else ''
    stop_name    = target_stop.site_name if target_stop else ''

    # All HTML/CSS/JS lives in this f-string for now — single-page deploys
    # are easier to ship and there's no build step to babysit.
    html = f'''<!DOCTYPE html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#0A0A0A">
<title>מעקב משלוח — {stop_name or driver.full_name}</title>
<link href="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.css" rel="stylesheet">
<script src="https://api.mapbox.com/mapbox-gl-js/v3.3.0/mapbox-gl.js"></script>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:#0A0A0A;color:#fff;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;min-height:100vh;display:flex;flex-direction:column;}}
#header{{padding:14px 16px;background:#111;border-bottom:1px solid #1E1E1E;display:flex;align-items:center;gap:12px;}}
#logo{{font-size:28px;}}
#info h1{{font-size:16px;font-weight:700;line-height:1.2;}}
#info p{{font-size:12px;color:#888;margin-top:2px;}}
.dot-live{{width:8px;height:8px;border-radius:50%;background:#22C55E;display:inline-block;margin-left:6px;animation:pulse 2s infinite;}}
@keyframes pulse{{0%,100%{{opacity:1;}}50%{{opacity:0.3;}}}}
#status-strip{{display:flex;align-items:center;justify-content:space-between;padding:10px 16px;background:#0F0F0F;border-bottom:1px solid #1A1A1A;}}
#position{{font-size:13px;color:#ccc;}}
#position strong{{color:#F5A623;font-size:15px;}}
#call-btn{{background:#22C55E;color:#000;border:none;padding:8px 14px;border-radius:8px;font-weight:700;font-size:13px;cursor:pointer;display:none;text-decoration:none;}}
#call-btn:active{{transform:scale(0.97);}}
#map{{height:50vh;min-height:340px;}}
#eta-card{{background:#111;border-top:1px solid #1E1E1E;padding:14px 16px;}}
#eta-row{{display:flex;align-items:baseline;gap:10px;}}
#eta-time{{font-size:26px;font-weight:800;color:#F5A623;font-family:monospace;}}
#eta-label{{font-size:13px;color:#888;}}
#eta-msg{{font-size:13px;color:#ccc;margin-top:6px;}}
#status-badge{{display:inline-block;padding:3px 10px;border-radius:99px;font-size:11px;font-weight:700;margin-inline-start:8px;}}
.s-pending{{background:rgba(245,166,35,0.15);color:#F5A623;}}
.s-done{{background:rgba(34,197,94,0.15);color:#22C55E;}}
.s-skipped{{background:rgba(239,68,68,0.15);color:#EF4444;}}
#proof-section{{background:#0D0D0D;padding:14px 16px;border-top:1px solid #1A1A1A;}}
#proof-title{{font-size:13px;font-weight:700;color:#F5A623;margin-bottom:10px;}}
#proof-empty{{font-size:12px;color:#666;text-align:center;padding:18px 0;}}
#proof-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:8px;}}
.proof-img{{aspect-ratio:1/1;background:#1A1A1A;border:1px solid #2A2A2A;border-radius:8px;overflow:hidden;cursor:pointer;}}
.proof-img img{{width:100%;height:100%;object-fit:cover;}}
#notes-section{{background:#0D0D0D;padding:16px;flex:1;border-top:1px solid #1A1A1A;}}
#notes-title{{font-size:14px;font-weight:700;color:#F5A623;margin-bottom:12px;}}
.note-input{{width:100%;background:#1A1A1A;border:1px solid #2A2A2A;border-radius:10px;padding:12px;color:#fff;font-size:14px;font-family:inherit;resize:none;}}
.note-input:focus{{outline:none;border-color:#F5A623;}}
.phone-input{{width:100%;background:#1A1A1A;border:1px solid #2A2A2A;border-radius:10px;padding:12px;color:#fff;font-size:14px;font-family:inherit;margin-top:8px;}}
.photo-label{{display:flex;align-items:center;gap:8px;background:#1A1A1A;border:1px dashed #333;border-radius:10px;padding:14px;margin-top:8px;cursor:pointer;color:#888;font-size:14px;}}
.photo-label:hover{{border-color:#F5A623;color:#F5A623;}}
#photo-preview{{width:100%;border-radius:10px;margin-top:8px;display:none;}}
.send-btn{{width:100%;background:#F5A623;color:#000;border:none;border-radius:10px;padding:14px;font-size:16px;font-weight:800;margin-top:12px;cursor:pointer;}}
.send-btn:active{{transform:scale(0.99);}}
.send-btn:disabled{{opacity:0.5;cursor:not-allowed;}}
#success{{display:none;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:14px;text-align:center;color:#22C55E;font-weight:700;margin-top:12px;}}
#powered{{text-align:center;font-size:11px;color:#333;padding:14px;}}
/* Lightbox for tapping a proof photo. */
#lightbox{{display:none;position:fixed;inset:0;background:rgba(0,0,0,0.95);z-index:1000;align-items:center;justify-content:center;cursor:pointer;}}
#lightbox img{{max-width:95vw;max-height:95vh;border-radius:8px;}}
</style>
</head>
<body>

<div id="header">
  <div id="logo">🚛</div>
  <div id="info">
    <h1 id="stop-title">{stop_name or 'מעקב משלוח'}</h1>
    <p id="truck-sub">מחשב זמן הגעה...</p>
  </div>
  <span class="dot-live" style="margin-inline-start:auto;"></span>
</div>

<div id="status-strip">
  <div id="position">—</div>
  <a id="call-btn" href="tel:">📞 התקשר לנהג</a>
</div>

<div id="map"></div>

<div id="eta-card">
  <div id="eta-label">זמן הגעה משוער <span id="status-badge"></span></div>
  <div id="eta-row">
    <div id="eta-time">--:-- – --:--</div>
  </div>
  <div id="eta-msg">מחשב...</div>
</div>

<div id="proof-section">
  <div id="proof-title">📸 הוכחות מסירה מהנהג</div>
  <div id="proof-empty">עדיין לא הועלו צילומים</div>
  <div id="proof-grid"></div>
</div>

<div id="loc-section" style="background:#0D0D0D;padding:16px;border-top:1px solid #1A1A1A;">
  <div style="font-size:14px;font-weight:700;color:#F5A623;margin-bottom:6px;">📍 שתף את המיקום המדויק שלך</div>
  <p style="font-size:12px;color:#888;margin-bottom:12px;">הנהג בדרך אליך — שתף את מיקומך כדי שיגיע בדיוק לנקודה.</p>
  <button class="send-btn" id="loc-btn" style="margin-top:0;" onclick="shareLocation()">📍 שלח את המיקום שלי</button>
  <div style="text-align:center;color:#444;font-size:12px;margin:10px 0;">— או —</div>
  <input class="phone-input" id="loc-link" type="text" style="margin-top:0;" placeholder="הדבק קישור Waze / Google Maps">
  <button class="send-btn" id="loc-link-btn" style="background:#1A1A1A;color:#F5A623;border:1px solid #2A2A2A;" onclick="shareLocationLink()">שלח קישור</button>
  <div id="loc-success" style="display:none;background:rgba(34,197,94,0.1);border:1px solid rgba(34,197,94,0.3);border-radius:10px;padding:14px;text-align:center;color:#22C55E;font-weight:700;margin-top:12px;">✓ המיקום נשלח לנהג!</div>
</div>

<div id="notes-section">
  <div id="notes-title">📝 השאר הערות לנהג</div>
  <textarea class="note-input" id="note-text" rows="3" placeholder="לדוגמה: אזור פריקה בכניסה הצדדית, קוד שער 1234..."></textarea>
  <input class="phone-input" id="phone-input" type="tel" placeholder="מספר טלפון ליצירת קשר (אופציונלי)">
  <label class="photo-label" for="photo-file">📸 הוסף תמונה (אזור פריקה, מיקום הרכב...)</label>
  <input type="file" id="photo-file" accept="image/*" style="display:none" onchange="previewPhoto(this)">
  <img id="photo-preview" alt="תצוגה מקדימה">
  <button class="send-btn" id="send-btn" onclick="sendNote()">שלח לנהג ✓</button>
  <div id="success">✓ ההערה נשלחה לנהג!</div>
</div>

<div id="powered">Powered by TruckForce</div>

<div id="lightbox" onclick="this.style.display='none'"><img id="lightbox-img"></div>

<script>
const TOKEN    = '{token}';
const SITE     = '{site_url}';
const STOP_ID  = '{stop_id}';
const MAPBOX   = '{mapbox_token}';

mapboxgl.accessToken = MAPBOX;
const map = new mapboxgl.Map({{
  container: 'map',
  style: 'mapbox://styles/mapbox/dark-v11',
  center: [34.85, 31.85],
  zoom: 11,
  attributionControl: false,
}});

// Truck marker. Lat/Lng updated each poll; one persistent marker.
const truckEl = document.createElement('div');
truckEl.innerHTML = '🚛';
truckEl.style.cssText = 'font-size:30px;filter:drop-shadow(0 0 10px rgba(245,166,35,0.7))';
const truckMarker = new mapboxgl.Marker(truckEl).setLngLat([34.85,31.85]).addTo(map);

// We re-create stop markers on each fetch (cheap, route doesn't change often).
let stopMarkers = [];
let firstLoad   = true;

function renderStops(stops, target) {{
  // Clear any markers from the previous poll.
  stopMarkers.forEach(m => m.remove());
  stopMarkers = [];

  stops.forEach(s => {{
    if (s.lat == null || s.lng == null) return;
    const el = document.createElement('div');
    if (s.is_target) {{
      // Highlighted target stop: large amber pin with a pulse so the
      // client can spot themselves immediately on the map.
      el.innerHTML = '📍';
      el.style.cssText = 'font-size:32px;filter:drop-shadow(0 0 12px rgba(245,166,35,0.9));animation:pulse 2s infinite;';
    }} else if (s.status === 'done') {{
      el.innerHTML = '✓';
      el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#22C55E;display:flex;align-items:center;justify-content:center;color:#000;font-size:11px;font-weight:800;border:2px solid #0A0A0A;';
    }} else if (s.status === 'skipped') {{
      el.innerHTML = '×';
      el.style.cssText = 'width:16px;height:16px;border-radius:50%;background:#EF4444;display:flex;align-items:center;justify-content:center;color:#fff;font-size:12px;font-weight:800;border:2px solid #0A0A0A;';
    }} else {{
      // Pending — anonymized neutral dot, no name shown.
      el.style.cssText = 'width:14px;height:14px;border-radius:50%;background:#666;border:2px solid #0A0A0A;';
    }}
    stopMarkers.push(
      new mapboxgl.Marker(el).setLngLat([s.lng, s.lat]).addTo(map)
    );
  }});
}}

function renderProofPhotos(photos) {{
  const grid  = document.getElementById('proof-grid');
  const empty = document.getElementById('proof-empty');
  grid.innerHTML = '';
  if (!photos || photos.length === 0) {{
    empty.style.display = '';
    return;
  }}
  empty.style.display = 'none';
  photos.forEach(p => {{
    const div = document.createElement('div');
    div.className = 'proof-img';
    div.innerHTML = `<img src="${{p.url}}" alt="">`;
    div.onclick = () => {{
      document.getElementById('lightbox-img').src = p.url;
      document.getElementById('lightbox').style.display = 'flex';
    }};
    grid.appendChild(div);
  }});
}}

function renderStatusBadge(status) {{
  const badge = document.getElementById('status-badge');
  badge.className = '';
  if (status === 'done') {{
    badge.textContent = '✓ נמסר';
    badge.classList.add('s-done');
  }} else if (status === 'skipped') {{
    badge.textContent = '× דולג';
    badge.classList.add('s-skipped');
  }} else {{
    badge.textContent = '⏱ בדרך';
    badge.classList.add('s-pending');
  }}
}}

async function updateData() {{
  try {{
    const res  = await fetch(`${{SITE}}/api/track/${{TOKEN}}/data/`);
    if (!res.ok) return;
    const data = await res.json();

    // Truck position
    if (data.location) {{
      const {{lat, lng}} = data.location;
      truckMarker.setLngLat([lng, lat]);
    }}

    // Other stops on the route (anonymized for everyone but target)
    if (data.route_stops) {{
      renderStops(data.route_stops, data.target_stop);

      // First-time framing: fit the map to the truck + all stops + the
      // target. We only do this once so subsequent polls don't yank the
      // user's pan/zoom around.
      if (firstLoad) {{
        const bounds = new mapboxgl.LngLatBounds();
        if (data.location)  bounds.extend([data.location.lng, data.location.lat]);
        data.route_stops.forEach(s => {{
          if (s.lat != null && s.lng != null) bounds.extend([s.lng, s.lat]);
        }});
        if (!bounds.isEmpty()) {{
          map.fitBounds(bounds, {{padding: 60, maxZoom: 14, duration: 1200}});
        }}
        firstLoad = false;
      }}
    }}

    // Target stop details (header, position, status, driver photos)
    if (data.target_stop) {{
      const t = data.target_stop;
      if (t.site_name) document.getElementById('stop-title').textContent = t.site_name;
      if (t.position && t.total_stops) {{
        document.getElementById('position').innerHTML =
          `המשלוח שלך: <strong>${{t.position}} / ${{t.total_stops}}</strong>`;
      }}
      renderStatusBadge(t.status);
      renderProofPhotos(t.driver_photos);
      // Hide the notes/photo upload section once delivered — feedback is
      // moot after the fact, and showing photos-of-the-delivery is what
      // the client wants at that point.
      if (t.status === 'done' || t.status === 'skipped') {{
        document.getElementById('notes-section').style.display = 'none';
      }}
    }}

    // Driver name + truck subtitle
    if (data.truck) document.getElementById('truck-sub').textContent = data.truck;

    // Show "Call driver" button when we have a phone number
    if (data.driver_phone) {{
      const btn = document.getElementById('call-btn');
      btn.href = `tel:${{data.driver_phone}}`;
      btn.style.display = 'inline-block';
    }}
  }} catch(e) {{ console.error(e); }}
}}

async function updateETA() {{
  if (!STOP_ID) return;
  try {{
    const res  = await fetch(`${{SITE}}/api/track/${{TOKEN}}/eta/?stop_id=${{STOP_ID}}`);
    const data = await res.json();
    if (data.eta_min_time && data.eta_max_time) {{
      document.getElementById('eta-time').textContent =
        `${{data.eta_min_time}} – ${{data.eta_max_time}}`;
      document.getElementById('eta-msg').textContent = data.message || '';
    }} else {{
      document.getElementById('eta-msg').textContent = 'ממתין למיקום הנהג...';
    }}
  }} catch(e) {{ console.error(e); }}
}}

function previewPhoto(input) {{
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => {{
    const img = document.getElementById('photo-preview');
    img.src   = e.target.result;
    img.style.display = 'block';
  }};
  reader.readAsDataURL(file);
}}

function shareLocation() {{
  var btn = document.getElementById('loc-btn');
  if (!navigator.geolocation) {{ alert('המכשיר לא תומך בשיתוף מיקום'); return; }}
  btn.disabled = true; btn.textContent = '...מאתר מיקום';
  navigator.geolocation.getCurrentPosition(function(pos) {{
    postLocation({{ lat: pos.coords.latitude, lng: pos.coords.longitude }});
  }}, function(err) {{
    btn.disabled = false; btn.textContent = '📍 שלח את המיקום שלי';
    alert('לא ניתן לקרוא מיקום. אפשר להדביק קישור במקום.');
  }}, {{ enableHighAccuracy: true, timeout: 10000 }});
}}
function shareLocationLink() {{
  var link = document.getElementById('loc-link').value.trim();
  if (!link) return;
  postLocation({{ link: link }});
}}
function postLocation(payload) {{
  payload.stop_id = STOP_ID;
  fetch(SITE + '/api/track/' + TOKEN + '/share-location/', {{
    method: 'POST',
    headers: {{ 'Content-Type': 'application/json' }},
    body: JSON.stringify(payload)
  }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
    var b = document.getElementById('loc-btn');
    if (data && data.ok) {{
      document.getElementById('loc-success').style.display = 'block';
      b.disabled = true; b.textContent = '✓ נשלח';
    }} else {{
      b.disabled = false; b.textContent = '📍 שלח את המיקום שלי';
      alert((data && data.error) ? data.error : 'שגיאה, נסה שוב');
    }}
  }}).catch(function() {{
    var b = document.getElementById('loc-btn');
    b.disabled = false; b.textContent = '📍 שלח את המיקום שלי';
    alert('שגיאת רשת');
  }});
}}
async function sendNote() {{
  const note  = document.getElementById('note-text').value.trim();
  const phone = document.getElementById('phone-input').value.trim();
  const photo = document.getElementById('photo-file').files[0];
  if (!note && !photo) return alert('אנא הוסף הערה או תמונה');

  const btn = document.getElementById('send-btn');
  btn.disabled    = true;
  btn.textContent = 'שולח...';

  const fd = new FormData();
  fd.append('stop_id', STOP_ID);
  if (note)  fd.append('note',  note);
  if (phone) fd.append('phone', phone);
  if (photo) fd.append('photo', photo);

  try {{
    const res = await fetch(`${{SITE}}/api/track/${{TOKEN}}/client-note/`, {{
      method: 'POST', body: fd
    }});
    if (res.ok) {{
      document.getElementById('success').style.display = 'block';
      document.getElementById('note-text').value  = '';
      document.getElementById('phone-input').value = '';
      document.getElementById('photo-file').value  = '';
      document.getElementById('photo-preview').style.display = 'none';
      btn.textContent = '✓ נשלח';
    }} else {{
      btn.disabled    = false;
      btn.textContent = 'שלח לנהג ✓';
      alert('שגיאה בשליחה, נסה שוב');
    }}
  }} catch(e) {{
    btn.disabled    = false;
    btn.textContent = 'שלח לנהג ✓';
  }}
}}

// Initial fetch + polling.
updateData();
updateETA();
setInterval(updateData, 30000);  // truck position + photos every 30s
setInterval(updateETA,  60000);  // ETA recompute every 60s
</script>
</body>
</html>'''
    return HttpResponse(html, content_type='text/html; charset=utf-8')


@require_GET
def tracking_data(request, token):
    """
    Returns JSON with driver's current location + route context.

    The returned shape is designed for a client-facing tracking page:

    - `location` is the live truck position (polled every 30s).
    - `route_stops` is an ordered, ANONYMIZED list of every stop on the
      route so the page can draw "I'm 3rd of 5" dots on the map without
      leaking other clients' names/addresses. Only the targeted stop
      (link.target_stop) carries its real site_name/address.
    - `target_stop` describes the link's own stop in detail, including
      its position (1-of-N), status, and any delivery photos the driver
      has already uploaded so the client can verify their goods.

    No auth required — public endpoint.
    """
    try:
        link = TrackingLink.objects.select_related('driver', 'target_stop').get(token=token)
    except TrackingLink.DoesNotExist:
        return JsonResponse({'error': 'Not found'}, status=404)

    if not link.is_valid():
        return JsonResponse({'error': 'Expired'}, status=410)

    driver = link.driver
    today  = localdate()

    # Latest GPS — drives the truck marker on the map.
    latest = DriverLocation.objects.filter(driver=driver).first()

    truck = None
    route_stops = []
    target_data = None

    try:
        schedule = DailySchedule.objects.prefetch_related('stops').get(
            driver=driver, date=today,
        )
    except DailySchedule.DoesNotExist:
        schedule = None

    if schedule:
        if schedule.truck:
            truck = (f"{schedule.truck.brand} {schedule.truck.model} · "
                     f"{schedule.truck.plate_number}")

        # All stops on the route, ordered. We expose coords + order + status
        # for every stop (so the page can draw dots and compute "3 of 5"),
        # but withhold site_name and address for stops other than the
        # link's target — that preserves privacy for the other clients
        # sharing this driver's route.
        target_id = link.target_stop_id
        for s in schedule.stops.all().order_by('order'):
            is_target = (s.id == target_id) if target_id else False
            route_stops.append({
                'id':        s.id,
                'order':     s.order,
                'status':    s.status,
                'lat':       float(s.latitude)  if s.latitude  else None,
                'lng':       float(s.longitude) if s.longitude else None,
                'is_target': is_target,
                # Only target stop reveals its name & address; others stay anonymous.
                'site_name': s.site_name if is_target else None,
                'address':   s.address   if is_target else None,
            })

        # Detailed payload for the targeted stop — drives the header text,
        # status banner, and "proof photos so far" gallery on the page.
        target_stop = link.target_stop
        if target_stop:
            # Position in the route (1-indexed for friendly display).
            position = next((i + 1 for i, s in enumerate(route_stops)
                             if s['id'] == target_stop.id), None)
            # Driver-uploaded delivery photos for THIS stop (proof that the
            # client's goods are loaded / dropped off). _abs_url-safe because
            # StopPhoto.image goes through Cloudinary storage which returns
            # absolute URLs already.
            driver_photos = []
            for p in target_stop.photos.all().order_by('uploaded_at'):
                try:
                    url = p.image.url if p.image else None
                except (ValueError, AttributeError):
                    url = None
                if url:
                    driver_photos.append({
                        'id':          p.id,
                        'url':         url,
                        'uploaded_at': p.uploaded_at.isoformat(),
                    })
            target_data = {
                'id':            target_stop.id,
                'site_name':     target_stop.site_name,
                'address':       target_stop.address,
                'order':         target_stop.order,
                'position':      position,
                'total_stops':   len(route_stops),
                'status':        target_stop.status,
                'driver_photos': driver_photos,
            }
        else:
            # No specific target — fall back to next pending stop for context.
            cur = schedule.stops.filter(status='pending').order_by('order').first()
            if cur:
                target_data = {
                    'id':            cur.id,
                    'site_name':     cur.site_name,
                    'address':       cur.address,
                    'order':         cur.order,
                    'position':      cur.order,
                    'total_stops':   len(route_stops),
                    'status':        cur.status,
                    'driver_photos': [],
                }

    return JsonResponse({
        'driver': driver.full_name,
        'driver_phone': driver.phone,
        'truck':  truck,
        'location': {
            'lat':       float(latest.latitude),
            'lng':       float(latest.longitude),
            'timestamp': latest.timestamp.isoformat(),
        } if latest else None,
        'route_stops': route_stops,
        'target_stop': target_data,
        'label': link.label,
    })


def _build_tracking_url(request, token: str) -> str:
    """
    Build the public URL a manager can share with a client.

    Prefers settings.SITE_URL — that's what gets set on Railway/production
    and matches the host that's actually reachable from the public internet.
    Falls back to request.build_absolute_uri so dev/runserver still works
    when SITE_URL isn't set in .env.

    Returns e.g. "https://truckforce-production.up.railway.app/track/abc.../"
    """
    from django.conf import settings
    base = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    if not base:
        base = request.build_absolute_uri('/')[:-1]
    return f"{base}/track/{token}/"


class DriverShareTrackingView(APIView):
    """Driver shares HIS OWN live location with a client — same public
    tracking page the office uses, created from the phone. Optionally
    pinned to one of the driver's own stops (ETA + header). Expires at
    end of day."""
    permission_classes = [IsManagerOrDriver]

    def post(self, request):
        driver = getattr(request, 'driver', None)
        if driver is None:
            return Response({'error': 'Drivers only'}, status=403)
        target_stop = None
        stop_id = request.data.get('target_stop_id')
        if stop_id:
            try:
                target_stop = Stop.objects.select_related('schedule').get(
                    pk=stop_id, schedule__driver=driver)  # own stops only
            except Stop.DoesNotExist:
                return Response({'error': 'Stop not found'}, status=404)
        from django.utils import timezone as tz
        from django.conf import settings as django_settings
        end_of_day = tz.localtime().replace(
            hour=23, minute=59, second=59, microsecond=0)
        link = TrackingLink.objects.create(
            driver=driver,
            created_by=None,
            created_by_driver=True,
            target_stop=target_stop,
            label=request.data.get('label', ''),
            expires_at=end_of_day,
        )
        base = getattr(django_settings, 'SITE_URL', '') or \
            request.build_absolute_uri('/').rstrip('/')
        return Response({
            'token': link.token,
            'url': f'{base}/track/{link.token}/',
        }, status=201)


class TrackingLinkListCreateView(APIView):
    """Manager creates and views tracking links."""
    permission_classes = [IsManager]

    def get(self, request):
        links = TrackingLink.objects.filter(
            created_by=request.manager
        ).select_related('driver')
        data = [{
            'id':         l.id,
            'token':      l.token,
            'driver':     l.driver.full_name,
            'driver_id':  l.driver.id,
            'label':      l.label,
            'is_active':  l.is_active,
            'is_valid':   l.is_valid(),
            'expires_at': l.expires_at.isoformat() if l.expires_at else None,
            'created_at': l.created_at.isoformat(),
            'url':        _build_tracking_url(request, l.token),
        } for l in links]
        return Response(data)

    def post(self, request):
        """Create a public tracking link.

        Body:
            driver_id       (required) — whose route to expose
            target_stop_id  (optional) — pin to a specific stop, used for
                                          the page header + ETA + privacy
            label           (optional) — friendly note for manager UI
            hours           (optional) — explicit lifetime in hours; if
                                          omitted, defaults to "end of the
                                          delivery day" — typically the
                                          target stop's date or today.
        """
        driver_id      = request.data.get('driver_id')
        target_stop_id = request.data.get('target_stop_id')
        label          = request.data.get('label', '')
        hours_raw      = request.data.get('hours')

        try:
            driver = Driver.objects.get(pk=driver_id)
        except Driver.DoesNotExist:
            return Response({'error': 'Driver not found'}, status=404)

        target_stop = None
        if target_stop_id:
            try:
                target_stop = Stop.objects.select_related('schedule').get(
                    pk=target_stop_id,
                )
            except Stop.DoesNotExist:
                return Response({'error': 'Stop not found'}, status=404)

        # Compute expiry. If caller passes `hours`, honour it (back-compat
        # with the old contract). Otherwise default to midnight at the END
        # of the delivery day — so a link generated Mon morning for a Mon
        # delivery dies at Mon 23:59:59, not Tue afternoon.
        from datetime import datetime, time, timedelta
        if hours_raw is not None:
            try:
                hours = int(hours_raw)
                expires_at = tz.now() + timedelta(hours=hours)
            except (TypeError, ValueError):
                return Response({'error': 'hours must be an integer'}, status=400)
        else:
            day = target_stop.schedule.date if target_stop else tz.localdate()
            # End-of-day in the project's local timezone, then make aware.
            naive_eod = datetime.combine(day, time(23, 59, 59))
            expires_at = tz.make_aware(naive_eod) if tz.is_naive(naive_eod) else naive_eod

        link = TrackingLink.objects.create(
            driver      = driver,
            created_by  = request.manager,
            target_stop = target_stop,
            label       = label,
            expires_at  = expires_at,
        )
        url = _build_tracking_url(request, link.token)
        return Response({
            'ok':         True,
            'token':      link.token,
            'url':        url,
            'expires_at': link.expires_at.isoformat(),
        }, status=201)


class TrackingLinkRevokeView(APIView):
    """Manager revokes a tracking link."""
    permission_classes = [IsManager]

    def post(self, request, pk):
        try:
            link = TrackingLink.objects.get(pk=pk, created_by=request.manager)
            link.is_active = False
            link.save()
            return Response({'ok': True})
        except TrackingLink.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)





from django.utils import timezone as tz


def _compute_violations(ordered_stop_ids, leg_durations_min, all_stops,
                        schedule_date, dwell_min=10):
    """Walk a chosen visit order and flag any windowed stops we'd arrive
    at AFTER their `expected_arrival`. Returns a list of dicts the UI can
    show as "this stop will be late by N minutes" warnings.

    Args:
        ordered_stop_ids:   chosen stop IDs in visit order
        leg_durations_min:  travel time per leg, in minutes (parallel list)
        all_stops:          queryset/list of all candidate Stop objects
                            (so we can look up expected_arrival, site_name)
        schedule_date:      the schedule's date — combined with the
                            TimeField to get a real datetime
        dwell_min:          minutes spent at each stop (matches the
                            optimizer's assumption)
    """
    from datetime import datetime as _dt, timedelta as _td
    lookup = {s.id: s for s in all_stops}
    violations = []
    cur = tz.now()
    for i, sid in enumerate(ordered_stop_ids):
        stop = lookup.get(sid)
        if stop is None:
            continue
        leg_min = leg_durations_min[i] if i < len(leg_durations_min) else 0
        cur = cur + _td(minutes=leg_min)
        if stop.expected_arrival:
            naive = _dt.combine(schedule_date, stop.expected_arrival)
            expected_dt = tz.make_aware(naive) if tz.is_naive(naive) else naive
            if cur > expected_dt:
                violations.append({
                    'stop_id':       sid,
                    'site_name':     stop.site_name,
                    'expected':      stop.expected_arrival.strftime('%H:%M'),
                    'predicted':     cur.strftime('%H:%M'),
                    'delay_minutes': round((cur - expected_dt).total_seconds() / 60),
                })
        cur = cur + _td(minutes=dwell_min)
    return violations


class OptimizeRouteView(APIView):
    """
    POST /api/schedules/<pk>/optimize/

    Calculate the best stop order for a schedule. Available to both manager
    and the driver who owns the schedule.

    Body (optional):
        {
            "mode": "fast" | "deadlines"   # default "fast"
        }

    "fast"        — pure shortest driving time (Mapbox Optimization API).
    "deadlines"   — same Mapbox call, but if any stop with `expected_arrival`
                    would be violated by the suggested order, we fall back
                    to a deadline-aware greedy: sort by `expected_arrival`
                    ascending, then within the same window do nearest-neighbor.
                    This isn't a globally-optimal TSP-with-windows, but it
                    behaves correctly for small Israeli fleet routes (≤15
                    stops, mostly 1-2 stops with hard windows).

    Saves the suggestion to schedule.route_suggestion and notifies the driver
    (when manager initiates) or the manager (when driver initiates).
    """
    permission_classes = [IsManagerOrDriver]

    def post(self, request, pk):
        try:
            return self._do_optimize(request, pk)
        except Exception as e:
            # Print the full traceback into Railway logs and return a JSON
            # error to the client. Without this, an uncaught exception
            # produces an HTML 500 page which the Flutter app can't parse
            # and surfaces as FormatException.
            import traceback
            tb = traceback.format_exc()
            print(f"[ROUTE-OPTIMIZE] crashed: {e}\n{tb}", flush=True)
            return Response(
                {'error': f'Server error: {type(e).__name__}: {e}'},
                status=500,
            )

    def _do_optimize(self, request, pk):
        try:
            schedule = DailySchedule.objects.prefetch_related('stops').get(pk=pk)
        except DailySchedule.DoesNotExist:
            return Response({'error': 'Schedule not found'}, status=404)

        # Ownership check: driver can only optimize their OWN schedule.
        # Manager can optimize anyone's.
        if hasattr(request, 'driver') and request.driver is not None:
            if schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden — not your schedule'},
                                status=403)

        stops = list(schedule.stops.filter(status='pending').order_by('order'))
        if not stops:
            return Response({'error': 'No pending stops to optimize'}, status=400)

        mode = (request.data.get('mode') or 'fast').lower()
        if mode not in ('fast', 'deadlines'):
            mode = 'fast'

        # Get driver's current location. Prefer the live GPS feed, fall back
        # to the first stop's coordinates so we always have *something*.
        driver  = schedule.driver
        latest  = DriverLocation.objects.filter(driver=driver).first()
        if latest:
            driver_lat = float(latest.latitude)
            driver_lng = float(latest.longitude)
        else:
            first = stops[0]
            driver_lat = float(first.latitude) if first.latitude else 31.85
            driver_lng = float(first.longitude) if first.longitude else 34.85

        # Run the new nearest-neighbor optimizer. It takes a single call
        # to Mapbox Matrix and walks stop-by-stop, picking the closest
        # unvisited each step — deterministic and intuitive. When the
        # manager picked 'deadlines' mode we also let windowed stops cut
        # the line if their slack is < 30 min.
        from .route_optimizer import optimize_route
        result = optimize_route(
            driver_lat, driver_lng, stops,
            deadline_aware=(mode == 'deadlines'),
            schedule_date=schedule.date,
        )

        if result.get('error') and not result.get('ordered_stop_ids'):
            return Response({'error': result['error']}, status=500)

        # The new optimizer computes deadline-related decisions internally,
        # but we still want to surface any violations to the driver so the
        # preview dialog can warn "stop X will be late". Compute them here
        # by walking the chosen order.
        deadline_violations = _compute_violations(
            result.get('ordered_stop_ids', []),
            result.get('durations', []),
            stops, schedule.date,
        )

        # Save suggestion to schedule. Skip the geometry — it's not needed
        # for re-display and bloats the JSON column. If we ever want to draw
        # the polyline on the office map, store it separately or re-call
        # Mapbox when needed.
        schedule.route_suggestion = {
            'ordered_stop_ids':       result.get('ordered_stop_ids', []),
            'durations':              result.get('durations', []),
            'total_duration_minutes': result.get('total_duration_minutes', 0),
            'driver_lat':             driver_lat,
            'driver_lng':             driver_lng,
            'mode':                   mode,
            'deadline_violations':    deadline_violations,
            'created_at':             tz.now().isoformat(),
        }
        schedule.route_optimized    = True
        schedule.route_optimized_at = tz.now()
        schedule.save()

        # Audit + Firebase ping. The "by_user_id" is the *initiator*, not the
        # owner — useful for showing "manager optimized your route" vs the
        # driver doing it themselves.
        by_user_id = (
            getattr(request, 'manager', None).id
            if getattr(request, 'manager', None) is not None
            else getattr(request, 'driver', None).id
            if getattr(request, 'driver', None) is not None
            else None
        )
        publish_event('route_suggestion', by_user_id=by_user_id, payload={
            'schedule_id':            pk,
            'driver_id':              driver.id,
            'total_duration_minutes': result.get('total_duration_minutes', 0),
            'stop_count':             len(stops),
            'mode':                   mode,
        })

        print(f"[ROUTE] Optimized schedule {pk} — {len(stops)} stops, "
              f"{result.get('total_duration_minutes', 0)} min, mode={mode}, "
              f"violations={len(deadline_violations)}", flush=True)

        # Note: `geometry` (full Mapbox route polyline) is intentionally NOT
        # returned to the client. It can be tens of KB and Flutter doesn't
        # draw it in the preview dialog. The geometry is still saved on the
        # schedule for the office map view, if needed.
        return Response({
            'ok':                     True,
            'ordered_stop_ids':       result.get('ordered_stop_ids', []),
            'durations':              result.get('durations', []),
            'total_duration_minutes': result.get('total_duration_minutes', 0),
            'method':                 result.get('method', 'mapbox'),
            'mode':                   mode,
            'deadline_violations':    deadline_violations,
        })


class ApplyRouteSuggestionView(APIView):
    """
    POST /api/schedules/<pk>/apply-suggestion/

    Apply the most recent route suggestion (reorder stops in DB).
    Available to manager (any schedule) and the driver who owns the schedule.
    """
    permission_classes = [IsManagerOrDriver]

    def post(self, request, pk):
        try:
            return self._do_apply(request, pk)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[ROUTE-APPLY] crashed: {e}\n{tb}", flush=True)
            return Response(
                {'error': f'Server error: {type(e).__name__}: {e}'},
                status=500,
            )

    def _do_apply(self, request, pk):
        try:
            schedule = DailySchedule.objects.get(pk=pk)
        except DailySchedule.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        # Ownership check for driver — same pattern as OptimizeRouteView.
        if hasattr(request, 'driver') and request.driver is not None:
            if schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden — not your schedule'},
                                status=403)

        suggestion = schedule.route_suggestion
        if not suggestion:
            return Response({'error': 'No suggestion available'}, status=400)

        ordered_ids = suggestion.get('ordered_stop_ids', [])
        if not ordered_ids:
            return Response({'error': 'Empty suggestion'}, status=400)

        # Reorder stops. We do it in a temporary unique range first to avoid
        # unique_together collisions on (schedule, order), then we settle the
        # final 1..N ordering. unique_together isn't declared on Stop today,
        # so this is defensive in case it gets added later.
        from django.db import transaction
        with transaction.atomic():
            for new_order, stop_id in enumerate(ordered_ids, start=1):
                Stop.objects.filter(pk=stop_id, schedule=schedule).update(
                    order=new_order
                )

        # Audit ping. Same pattern as optimize: tell the other party.
        by_user_id = (
            getattr(request, 'manager', None).id
            if getattr(request, 'manager', None) is not None
            else getattr(request, 'driver', None).id
            if getattr(request, 'driver', None) is not None
            else None
        )
        publish_event('route_confirmed', by_user_id=by_user_id, payload={
            'schedule_id': pk,
            'driver_id':   schedule.driver.id,
        })

        return Response({'ok': True, 'message': 'Route applied'})


class StopTaskListCreateView(APIView):
    """
    GET/POST /api/stops/<pk>/tasks/
    Manager adds notes/tasks for a stop — driver sees them on phone.
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes     = [MultiPartParser, FormParser, JSONParser]

    def get(self, request, pk):
        tasks = StopTask.objects.filter(stop_id=pk)
        data  = [{
            'id':         t.id,
            'source':     t.source,
            'note':       t.note,
            'phone':      t.phone,
            'photo':      request.build_absolute_uri(t.photo.url) if t.photo else None,
            'created_at': t.created_at.isoformat(),
        } for t in tasks]
        return Response(data)

    def post(self, request, pk):
        try:
            stop = Stop.objects.get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)

        source = 'manager' if hasattr(request, 'manager') and request.manager else 'driver'

        task = StopTask.objects.create(
            stop   = stop,
            source = source,
            note   = request.data.get('note', ''),
            phone  = request.data.get('phone', ''),
            photo  = request.FILES.get('photo'),
        )

        return Response({
            'id':         task.id,
            'source':     task.source,
            'note':       task.note,
            'phone':      task.phone,
            'photo':      request.build_absolute_uri(task.photo.url) if task.photo else None,
            'created_at': task.created_at.isoformat(),
        }, status=201)


class StopTaskDeleteView(APIView):
    """DELETE /api/stop-tasks/<pk>/"""
    permission_classes = [IsManager]

    def delete(self, request, pk):
        StopTask.objects.filter(pk=pk).delete()
        return Response({'ok': True})


class ShareLocationView(APIView):
    """
    POST /api/track/<token>/share-location/
    Public — client shares exact GPS (browser) or pastes a Waze/Google
    link via the tracking page. Updates the target stop's coordinates
    and pings the driver to recalculate. No auth required.
    """
    permission_classes = []

    def post(self, request, token):
        try:
            link = TrackingLink.objects.select_related(
                'driver', 'target_stop').get(token=token)
        except TrackingLink.DoesNotExist:
            return Response({'ok': False, 'error': 'Link not found'}, status=404)
        if not link.is_valid():
            return Response({'ok': False, 'error': 'Link expired'}, status=410)

        stop = link.target_stop
        if stop is None:
            sid = request.data.get('stop_id')
            stop = Stop.objects.filter(pk=sid).first() if sid else None
        if stop is None:
            return Response({'ok': False, 'error': 'No stop to update'}, status=400)

        lat, lng = request.data.get('lat'), request.data.get('lng')
        if lat is None or lng is None:
            raw = (request.data.get('link') or '').strip()
            if raw:
                try:
                    parsed = location_url_parser.parse(raw)
                except Exception:
                    parsed = None
                if parsed:
                    lat, lng = parsed
        try:
            lat, lng = float(lat), float(lng)
        except (TypeError, ValueError):
            return Response({'ok': False, 'error': 'No valid location'}, status=400)
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            return Response({'ok': False, 'error': 'Location out of range'}, status=400)

        stop.latitude, stop.longitude = lat, lng
        stop.save(update_fields=['latitude', 'longitude'])

        # Tell the driver the destination moved (same feed as client notes).
        try:
            StopTask.objects.create(
                stop=stop, source='client',
                note='📍 הלקוח עדכן את מיקום היעד — מומלץ לחשב מסלול מחדש')
        except Exception:
            pass
        try:
            from . import firebase
            sched = getattr(stop, 'schedule', None)
            if sched is not None:
                firebase.notify_driver_schedule_updated(
                    link.driver, sched, 'מיקום יעד עודכן על ידי הלקוח')
        except Exception:
            pass

        return Response({'ok': True})


class ClientNoteView(APIView):
    """
    POST /api/track/<token>/client-note/
    Client uploads note/photo via tracking link — driver sees it.
    No auth required.
    """
    permission_classes = []
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request, token):
        try:
            link = TrackingLink.objects.get(token=token)
        except TrackingLink.DoesNotExist:
            return Response({'error': 'Link not found'}, status=404)

        if not link.is_valid():
            return Response({'error': 'Link expired'}, status=410)

        stop_id = request.data.get('stop_id')
        if not stop_id:
            return Response({'error': 'stop_id required'}, status=400)

        try:
            stop = Stop.objects.get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)

        task = StopTask.objects.create(
            stop   = stop,
            source = 'client',
            note   = request.data.get('note', ''),
            phone  = request.data.get('phone', ''),
            photo  = request.FILES.get('photo'),
        )

        # Notify driver via Firebase
        publish_event('client_note_added', payload={
            'stop_id':   stop.id,
            'stop_name': stop.site_name,
            'driver_id': stop.schedule.driver.id,
        })

        return Response({'ok': True, 'task_id': task.id}, status=201)


class RouteETAView(APIView):
    """
    GET /api/track/<token>/eta/?stop_id=<id>
    Returns ETA for a specific stop via tracking link.
    No auth required.
    """
    permission_classes = []

    def get(self, request, token):
        try:
            link = TrackingLink.objects.get(token=token)
        except TrackingLink.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        if not link.is_valid():
            return Response({'error': 'Expired'}, status=410)

        stop_id = request.query_params.get('stop_id')
        driver  = link.driver
        today   = localdate()

        # Get driver location
        latest = DriverLocation.objects.filter(driver=driver).first()
        if not latest:
            return Response({'eta_min': None, 'eta_max': None, 'no_location': True})

        # Get pending stops
        try:
            schedule = DailySchedule.objects.get(driver=driver, date=today)
            stops_ahead = list(schedule.stops.filter(status='pending').order_by('order'))
        except DailySchedule.DoesNotExist:
            return Response({'eta_min': None, 'eta_max': None})

        from .route_optimizer import calculate_eta
        min_eta, max_eta = calculate_eta(
            float(latest.latitude), float(latest.longitude),
            stops_ahead, int(stop_id)
        )

        now = tz.now()
        from datetime import timedelta
        eta_min_time = (now + timedelta(minutes=min_eta)).strftime('%H:%M')
        eta_max_time = (now + timedelta(minutes=max_eta)).strftime('%H:%M')

        return Response({
            'eta_min_minutes': min_eta,
            'eta_max_minutes': max_eta,
            'eta_min_time':    eta_min_time,
            'eta_max_time':    eta_max_time,
            'message':         f'הנהג יגיע בין {eta_min_time} ל-{eta_max_time}',
        })


class StopETADistanceView(APIView):
    """GET /api/stops/<pk>/eta-distance/

    Manager-facing endpoint used by the Live Map popup. Given a stop ID,
    returns the ETA window (min/max minutes & HH:MM) plus straight-line
    distance from the assigned driver's current location.

    Distinct from RouteETAView (which is keyed off a tracking link token
    for public use) — this one requires manager auth and accepts any
    stop on any active schedule.
    """
    permission_classes = [IsManager]

    def get(self, request, pk):
        try:
            stop = Stop.objects.select_related('schedule', 'schedule__driver').get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)

        driver = stop.schedule.driver
        # Latest GPS row (model orders by -timestamp so .first() == newest).
        latest = DriverLocation.objects.filter(driver=driver).first()
        if not latest:
            return Response({
                'no_location':    True,
                'driver_id':      driver.id,
                'driver_name':    driver.full_name,
                'driver_phone':   driver.phone,
                'contact_phone':  stop.contact_phone or '',
                'contact_name':   stop.contact_name or '',
                'site_name':      stop.site_name,
                'address':        stop.address or '',
                'status':         stop.status,
            })

        # Distance: straight-line km. Driving distance would need another
        # Mapbox call; this is the popup teaser so haversine is fine.
        from .route_optimizer import _haversine_km
        distance_km = round(_haversine_km(
            float(latest.latitude), float(latest.longitude),
            float(stop.latitude),   float(stop.longitude),
        ), 1) if (stop.latitude and stop.longitude) else None

        # ETA: walk pending stops up to this one. If `stop` isn't actually
        # pending (already done/skipped) we still compute a best-effort
        # estimate via direct drive from driver → stop.
        stops_ahead = list(
            stop.schedule.stops.filter(status='pending').order_by('order')
        )
        from .route_optimizer import calculate_eta
        min_eta, max_eta = calculate_eta(
            float(latest.latitude), float(latest.longitude),
            stops_ahead, stop.id,
        )

        from datetime import timedelta
        now = tz.now()
        eta_min_time = (now + timedelta(minutes=min_eta)).strftime('%H:%M')
        eta_max_time = (now + timedelta(minutes=max_eta)).strftime('%H:%M')

        return Response({
            'driver_id':        driver.id,
            'driver_name':      driver.full_name,
            'driver_phone':     driver.phone,
            'contact_name':     stop.contact_name or '',
            'contact_phone':    stop.contact_phone or '',
            'site_name':        stop.site_name,
            'address':          stop.address or '',
            'status':           stop.status,
            'expected_arrival': stop.expected_arrival.strftime('%H:%M') if stop.expected_arrival else None,
            'order':            stop.order,
            'eta_min_minutes':  min_eta,
            'eta_max_minutes':  max_eta,
            'eta_min_time':     eta_min_time,
            'eta_max_time':     eta_max_time,
            'distance_km':      distance_km,
            'location_age_sec': int((tz.now() - latest.timestamp).total_seconds()),
        })


class StopCompleteView(APIView):
    """
    POST /api/stops/<pk>/complete/
    Driver marks stop as done with optional note and photo.
    More detailed than the simple update — handles pickup/delivery logic.
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, pk):
        try:
            stop = Stop.objects.select_related('schedule__driver').get(pk=pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)

        action = request.data.get('action', 'done')  # 'done' or 'skipped'
        driver_note = request.data.get('driver_note', '')
        photo = request.FILES.get('photo')
        skip_reason = request.data.get('skip_reason', '')

        stop.status = action
        stop.driver_note = driver_note
        stop.completed_at = timezone.now()

        if skip_reason:
            stop.skip_reason = skip_reason

        stop.save()

        # Photo (if any) goes via the StopPhoto FK — Stop has no direct photo field.
        if photo:
            try:
                import cloudinary.uploader
                result = cloudinary.uploader.upload(
                    photo,
                    resource_type='image',
                    folder='delivery_photos',
                    use_filename=True,
                    unique_filename=True,
                )
                secure_url = result.get('secure_url') or result.get('url')
                if secure_url:
                    sp = StopPhoto(stop=stop)
                    sp.image = secure_url
                    sp.save()
                else:
                    print(f"[STOP-PHOTO] upload returned no URL", flush=True)
            except Exception as e:
                print(f"[STOP-PHOTO] upload failed: {e}", flush=True)
                # Don't fail the whole stop completion just because photo failed

        # Log based on stop type
        type_label = dict(Stop.STOP_TYPE_CHOICES).get(stop.stop_type, stop.stop_type)
        print(f"[STOP] #{stop.order} {type_label} '{stop.site_name}' → {action} "
              f"(driver: {stop.schedule.driver.full_name})", flush=True)

        # Notify office via Firebase
        publish_event('stop_updated', payload={
            'stop_id': stop.id,
            'stop_name': stop.site_name,
            'stop_type': stop.stop_type,
            'status': action,
            'driver_id': stop.schedule.driver.id,
            'order': stop.order,
        })

        # Manager toast (desktop) + FCM for done & skipped stops.
        if action in ('done', 'skipped') and stop.schedule.driver:
            _notify_stop_completion(stop.schedule.driver, stop, action)

        return Response({
            'ok': True,
            'stop_id': stop.id,
            'status': stop.status,
            'stop_type': stop.stop_type,
            'completed_at': stop.completed_at.isoformat(),
        })


class ScheduleSummaryView(APIView):
    """
    GET /api/schedules/<pk>/summary/
    Returns a full summary of the schedule — all stops with their
    pickup/delivery relationships. Used by office and driver.
    """
    permission_classes = [IsManagerOrDriver]

    def get(self, request, pk):
        try:
            schedule = DailySchedule.objects.prefetch_related(
                'stops', 'stops__tasks', 'stops__delivery_stops'
            ).select_related('driver', 'truck').get(pk=pk)
        except DailySchedule.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        stops_data = []
        for stop in schedule.stops.order_by('order'):
            # Get linked deliveries if this is a pickup
            linked_deliveries = []
            if stop.stop_type in ('pickup', 'both'):
                for d in stop.delivery_stops.all():
                    linked_deliveries.append({
                        'id': d.id,
                        'order': d.order,
                        'site_name': d.site_name,
                        'status': d.status,
                        'items': d.items,
                    })

            # Tasks for this stop
            tasks = [{
                'id': t.id,
                'source': t.source,
                'note': t.note,
                'phone': t.phone,
                'photo': request.build_absolute_uri(t.photo.url) if t.photo else None,
            } for t in stop.tasks.all()]

            stops_data.append({
                'id': stop.id,
                'order': stop.order,
                'stop_type': stop.stop_type,
                'stop_type_label': dict([
                    ('delivery', 'מסירה'),
                    ('pickup', 'איסוף'),
                    ('service', 'שירות'),
                    ('both', 'איסוף + מסירה'),
                ]).get(stop.stop_type, stop.stop_type),
                'site_name': stop.site_name,
                'address': stop.address,
                'latitude': float(stop.latitude) if stop.latitude else None,
                'longitude': float(stop.longitude) if stop.longitude else None,
                'notes': stop.notes,
                'items': stop.items,
                'contact_name': stop.contact_name,
                'contact_phone': stop.contact_phone,
                'status': stop.status,
                'driver_note': stop.driver_note,
                'skip_reason': stop.skip_reason,
                'completed_at': stop.completed_at.isoformat() if stop.completed_at else None,
                'pickup_stop_id': stop.pickup_stop_id,
                'linked_deliveries': linked_deliveries,
                'tasks': tasks,
                'expected_arrival': str(stop.expected_arrival) if stop.expected_arrival else None,
            })

        # Stats
        total = len(stops_data)
        done = sum(1 for s in stops_data if s['status'] == 'done')
        pickups = sum(1 for s in stops_data if s['stop_type'] in ('pickup', 'both'))
        deliveries = sum(1 for s in stops_data if s['stop_type'] in ('delivery', 'both'))

        return Response({
            'id': schedule.id,
            'date': str(schedule.date),
            'driver': schedule.driver.full_name,
            'truck': f"{schedule.truck.brand} {schedule.truck.plate_number}" if schedule.truck else None,
            'status': schedule.status,
            'stats': {
                'total': total,
                'done': done,
                'pickups': pickups,
                'deliveries': deliveries,
                'completion': f"{round(done / total * 100)}%" if total else '0%',
            },
            'stops': stops_data,
            'manager_notes': schedule.manager_notes if hasattr(schedule, 'manager_notes') else '',
            'route_optimized': getattr(schedule, 'route_optimized', False),
        })


class DriverLocationsHistoryView(APIView):
    """Replay support for the office live map: returns the GPS trail each
    driver actually drove on a given past date. One row per driver:
        [{driver_id, trail: [{lat, lng}, ...]}, ...]
    Optional ?driver=<id> narrows to one driver. Points come back in
    chronological order so the client can draw them as a line directly.
    """
    permission_classes = [IsManager]

    def get(self, request):
        from datetime import datetime as _dt
        date_str  = request.query_params.get('date')
        driver_id = request.query_params.get('driver')
        try:
            day = _dt.strptime(date_str, '%Y-%m-%d').date()
        except (TypeError, ValueError):
            return Response({'error': 'date=YYYY-MM-DD required'}, status=400)

        qs = DriverLocation.objects.filter(timestamp__date=day)
        if driver_id:
            qs = qs.filter(driver_id=driver_id)
        qs = qs.order_by('driver_id', 'timestamp')

        grouped = {}
        for l in qs:
            grouped.setdefault(l.driver_id, []).append(
                {'lat': float(l.latitude), 'lng': float(l.longitude)})
        return Response(
            [{'driver_id': k, 'trail': v} for k, v in grouped.items()])


# ──────────────────────────────────────────────
# INVOICING MODULE (paid add-on — gated by CompanySettings.invoicing_enabled)
# ──────────────────────────────────────────────
from decimal import Decimal, InvalidOperation
from django.db.models import Max
from django.utils.dateparse import parse_date
from .models import Client, Invoice, InvoiceLine, FinanceDocument
from .serializers import (ClientSerializer, InvoiceSerializer,
                          FinanceDocumentSerializer)


def _invoicing_guard():
    """Returns a 403 Response when the billing module is off, else None.
    RESERVED for the Green Invoice integration (legal tax documents) —
    manual proformas, clients, and the archive are all base-package and
    unguarded. Flip CompanySettings.invoicing_enabled when a client pays
    for the Green Invoice add-on; no redeploy needed."""
    co = CompanySettings.objects.first()
    if co is None or not co.invoicing_enabled:
        return Response(
            {'error': 'Invoicing module is not enabled for this account'},
            status=403)
    return None


def _dec(value, default='0'):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _apply_lines(invoice, lines_data):
    """Replace an invoice's lines from request data. The manager types
    description / quantity / price — no automatic pricing."""
    invoice.lines.all().delete()
    for i, ld in enumerate(lines_data or []):
        desc = str(ld.get('description', '')).strip()
        if not desc:
            continue
        InvoiceLine.objects.create(
            invoice=invoice,
            stop_id=ld.get('stop') or None,
            description=desc[:300],
            quantity=_dec(ld.get('quantity', 1), '1'),
            unit_price=_dec(ld.get('unit_price', 0)),
            order=i,
        )


def _upload_invoice_pdf(pdf_bytes, public_id, folder='invoices'):
    """Direct raw Cloudinary upload — same proven path as delivery notes
    and confirmations (the storage layer mangles PDFs)."""
    import os as _os
    import tempfile as _tempfile
    try:
        import cloudinary.uploader
        tmp = _tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
        try:
            tmp.write(pdf_bytes)
            tmp.close()
            result = cloudinary.uploader.upload(
                tmp.name, resource_type='raw', folder=folder,
                public_id=public_id, use_filename=False,
                unique_filename=False, overwrite=True)
        finally:
            try:
                _os.unlink(tmp.name)
            except OSError:
                pass
        return result.get('secure_url') or result.get('url')
    except Exception as e:
        print(f'[INVOICE] PDF upload failed ({public_id}): {e}', flush=True)
        return None


class ClientListCreateView(APIView):
    """The hauler's business customers (who gets billed)."""
    permission_classes = [IsManager]

    def get(self, request):
        qs = Client.objects.all()
        if request.query_params.get('active') == '1':
            qs = qs.filter(is_active=True)
        return Response(ClientSerializer(qs, many=True).data)

    def post(self, request):
        ser = ClientSerializer(data=request.data)
        if ser.is_valid():
            ser.save()
            return Response(ser.data, status=201)
        return Response(ser.errors, status=400)


class ClientDetailView(APIView):
    permission_classes = [IsManager]

    def get(self, request, pk):
        client = get_object_or_404(Client, pk=pk)
        return Response(ClientSerializer(client).data)

    def patch(self, request, pk):
        client = get_object_or_404(Client, pk=pk)
        ser = ClientSerializer(client, data=request.data, partial=True)
        if ser.is_valid():
            ser.save()
            return Response(ser.data)
        return Response(ser.errors, status=400)

    def delete(self, request, pk):
        client = get_object_or_404(Client, pk=pk)
        try:
            client.delete()
        except Exception:
            # Has invoices (PROTECT) — deactivate instead of deleting,
            # because issued documents must keep their client reference.
            client.is_active = False
            client.save(update_fields=['is_active'])
            return Response({'deactivated': True})
        return Response(status=204)


class InvoiceListCreateView(APIView):
    """GET ?year=&month=&client=&status=   POST creates a DRAFT with lines:
    {client, vat_exempt?, notes?, lines: [{description, quantity,
    unit_price, stop?}]}"""
    permission_classes = [IsManager]

    def get(self, request):
        qs = Invoice.objects.select_related('client').prefetch_related('lines')
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if year:
            qs = qs.filter(created_at__year=year)
        if month:
            qs = qs.filter(created_at__month=month)
        if request.query_params.get('client'):
            qs = qs.filter(client_id=request.query_params['client'])
        if request.query_params.get('status'):
            qs = qs.filter(status=request.query_params['status'])
        return Response(
            InvoiceSerializer(qs, many=True,
                              context={'request': request}).data)

    def post(self, request):
        client = get_object_or_404(Client, pk=request.data.get('client'))
        inv = Invoice.objects.create(
            client=client,
            vat_exempt=bool(request.data.get('vat_exempt')),
            notes=str(request.data.get('notes', ''))[:2000],
        )
        _apply_lines(inv, request.data.get('lines'))
        inv.recalc_totals()
        inv.save()
        return Response(
            InvoiceSerializer(inv, context={'request': request}).data,
            status=201)


class InvoiceDetailView(APIView):
    permission_classes = [IsManager]

    def get(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        return Response(
            InvoiceSerializer(inv, context={'request': request}).data)

    def patch(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)

        # Payment status can change at any stage.
        touched = False
        for field in ('status', 'payment_date', 'payment_method'):
            if field in request.data:
                setattr(inv, field, request.data[field])
                touched = True

        # Content is editable only while the document is a draft —
        # an issued document is immutable.
        if inv.status == 'draft':
            if 'notes' in request.data:
                inv.notes = str(request.data['notes'])[:2000]
                touched = True
            if 'vat_exempt' in request.data:
                inv.vat_exempt = bool(request.data['vat_exempt'])
                touched = True
            if 'lines' in request.data:
                _apply_lines(inv, request.data['lines'])
                touched = True
            inv.recalc_totals()
        elif any(k in request.data for k in ('lines', 'vat_exempt')):
            return Response(
                {'error': 'Issued documents cannot be edited'}, status=400)

        if touched:
            inv.save()
        return Response(
            InvoiceSerializer(inv, context={'request': request}).data)

    def delete(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        if inv.status != 'draft':
            return Response(
                {'error': 'Only drafts can be deleted; cancel issued '
                          'documents instead'}, status=400)
        inv.delete()
        return Response(status=204)


class InvoiceIssueView(APIView):
    """POST /billing/invoices/<pk>/issue/ — turn a draft into an issued
    חשבון עסקה: assign the next number, snapshot the client, render the
    branded PDF, upload it, lock the document."""
    permission_classes = [IsManager]

    def post(self, request, pk):
        inv = get_object_or_404(Invoice, pk=pk)
        if inv.status != 'draft':
            return Response({'error': 'Already issued'}, status=400)
        if not inv.lines.exists():
            return Response({'error': 'Invoice has no lines'}, status=400)

        # Next number in this document type's sequence (starts at 1001).
        last = (Invoice.objects
                .filter(invoice_type=inv.invoice_type)
                .aggregate(Max('number'))['number__max'])
        inv.number = (last or 1000) + 1

        # Snapshot the client — issued documents never change retroactively.
        inv.client_name    = inv.client.name
        inv.client_tax_id  = inv.client.tax_id
        inv.client_address = inv.client.address
        inv.issue_date     = localdate()
        inv.recalc_totals()

        try:
            from .invoice_pdf import generate_invoice_pdf
            pdf_bytes = generate_invoice_pdf(inv)
        except Exception as e:
            print(f'[INVOICE] PDF generation error: {e}', flush=True)
            pdf_bytes = None

        if pdf_bytes:
            url = _upload_invoice_pdf(
                pdf_bytes, f'invoice_{inv.pk}_{inv.number}.pdf')
            if url:
                inv.pdf_file = url

        inv.status = 'issued'
        inv.save()

        # File the issued document into the month archive as income, so
        # the archive shows the complete financial picture of the month
        # (same PDF, same amount — one source of truth).
        try:
            pdf_name = str(getattr(inv.pdf_file, 'name', '') or '')
            if pdf_name:
                FinanceDocument.objects.create(
                    kind='income',
                    doc_date=inv.issue_date,
                    client=inv.client,
                    vendor_name=inv.client_name,
                    vendor_tax_id=inv.client_tax_id,
                    amount=inv.total,
                    file=pdf_name,
                    original_filename=f'חשבון_עסקה_{inv.number}.pdf',
                    notes=f'חשבון עסקה {inv.number}',
                )
        except Exception as e:
            print(f'[INVOICE] archive filing failed: {e}', flush=True)

        return Response(
            InvoiceSerializer(inv, context={'request': request}).data)


class FinanceDocumentListCreateView(APIView):
    """The year/month document archive. GET ?year=&month=&kind=
    POST multipart: file + kind + doc_date (+ vendor_name, description,
    amount, notes, client)."""
    permission_classes = [IsManager]

    def get(self, request):
        qs = FinanceDocument.objects.all()
        if request.query_params.get('year'):
            qs = qs.filter(doc_date__year=request.query_params['year'])
        if request.query_params.get('month'):
            qs = qs.filter(doc_date__month=request.query_params['month'])
        if request.query_params.get('kind'):
            qs = qs.filter(kind=request.query_params['kind'])
        return Response(
            FinanceDocumentSerializer(qs, many=True,
                                      context={'request': request}).data)

    def post(self, request):
        f = request.FILES.get('file')
        if f is None:
            return Response({'error': 'file is required'}, status=400)
        kind = request.data.get('kind')
        if kind not in ('income', 'expense'):
            return Response({'error': "kind must be 'income' or 'expense'"},
                            status=400)
        doc_date = parse_date(str(request.data.get('doc_date', '')))
        if doc_date is None:
            return Response({'error': 'doc_date=YYYY-MM-DD required'},
                            status=400)

        # Direct Cloudinary upload (resource_type auto: PDFs and photos of
        # receipts both work), filed into the year/month folder structure.
        try:
            import cloudinary.uploader
            result = cloudinary.uploader.upload(
                f,
                resource_type='auto',
                folder=f'finance_docs/{doc_date.year}/{doc_date.month:02d}',
                use_filename=True,
                unique_filename=True,
            )
            file_url = result.get('secure_url') or result.get('url')
        except Exception as e:
            print(f'[FINANCE-DOC] upload failed: {e}', flush=True)
            return Response({'error': 'upload failed'}, status=502)

        doc = FinanceDocument.objects.create(
            kind=kind,
            doc_date=doc_date,
            client_id=request.data.get('client') or None,
            vendor_name=str(request.data.get('vendor_name', ''))[:200],
            vendor_tax_id=str(request.data.get('vendor_tax_id', ''))[:20],
            description=str(request.data.get('description', ''))[:300],
            amount=_dec(request.data['amount']) if request.data.get('amount') else None,
            file=file_url,
            original_filename=getattr(f, 'name', '')[:255],
            notes=str(request.data.get('notes', ''))[:2000],
        )
        return Response(
            FinanceDocumentSerializer(doc, context={'request': request}).data,
            status=201)


class FinanceDocumentDeleteView(APIView):
    permission_classes = [IsManager]

    def delete(self, request, pk):
        doc = get_object_or_404(FinanceDocument, pk=pk)
        doc.delete()
        return Response(status=204)


# ──────────────────────────────────────────────
# MOBILE SCAN PAGE + ARCHIVE EXPORT
# ──────────────────────────────────────────────

def _get_or_create_scan_token():
    import secrets
    co = CompanySettings.objects.first()
    if co is None:
        return None
    if not co.scan_token:
        co.scan_token = secrets.token_urlsafe(24)
        co.save(update_fields=['scan_token'])
    return co.scan_token


def _scan_token_valid(token: str) -> bool:
    # The scanner/archive is part of the BASE package — only invoice
    # ISSUING (clients, invoices, Green Invoice) sits behind the paid flag.
    co = CompanySettings.objects.first()
    return bool(co and co.scan_token and token == co.scan_token)


def scan_page_view(request, token):
    """Serves the mobile scanner page. The token in the URL is the auth —
    upload-only, regenerable, no read access."""
    from django.http import HttpResponse
    if not _scan_token_valid(token):
        return HttpResponse('<h2 style="font-family:sans-serif">Link expired '
                            'or invalid</h2>', status=403)
    from .scan_page import render_scan_page
    return HttpResponse(render_scan_page(token))


class ScanUploadView(APIView):
    """POST /scan/<token>/upload/ — photos in, archived PDF out.
    multipart: images (1..N) + kind + doc_date (+ vendor_name, amount).
    The photos are merged into one PDF via PyMuPDF, uploaded to
    finance_docs/<year>/<month>/, and recorded as a FinanceDocument."""
    permission_classes = []          # the token IS the auth
    authentication_classes = []

    def post(self, request, token):
        if not _scan_token_valid(token):
            return Response({'error': 'invalid token'}, status=403)

        images = request.FILES.getlist('images')
        if not images:
            return Response({'error': 'no images'}, status=400)
        kind = request.data.get('kind')
        if kind not in ('income', 'expense'):
            return Response({'error': 'bad kind'}, status=400)
        doc_date = parse_date(str(request.data.get('doc_date', '')))
        if doc_date is None:
            doc_date = localdate()

        # Photos → one PDF (each photo becomes a page at its own size).
        try:
            import fitz  # PyMuPDF
            pdf = fitz.open()
            for f in images[:12]:                     # sanity cap
                file_bytes = f.read()
                if file_bytes[:5] == b'%PDF-':
                    # An existing PDF (e.g. arrived by WhatsApp/email and
                    # picked from the phone) — append its pages as-is.
                    part = fitz.open(stream=file_bytes, filetype='pdf')
                    pdf.insert_pdf(part)
                    part.close()
                    continue
                img = fitz.open(stream=file_bytes, filetype='image')
                rect = img[0].rect
                page_pdf = fitz.open('pdf', img.convert_to_pdf())
                page = pdf.new_page(width=rect.width, height=rect.height)
                page.show_pdf_page(rect, page_pdf, 0)
                img.close()
                page_pdf.close()
            pdf_bytes = pdf.tobytes(deflate=True)
            pdf.close()
        except Exception as e:
            print(f'[SCAN] photo->pdf failed: {e}', flush=True)
            return Response({'error': 'pdf conversion failed'}, status=500)

        url = _upload_invoice_pdf(
            pdf_bytes,
            f'scan_{kind}_{doc_date.isoformat()}_'
            f'{timezone.now().strftime("%H%M%S")}.pdf',
            folder=f'finance_docs/{doc_date.year}/{doc_date.month:02d}')
        if url is None:
            return Response({'error': 'upload failed'}, status=502)

        doc = FinanceDocument.objects.create(
            kind=kind,
            doc_date=doc_date,
            vendor_name=str(request.data.get('vendor_name', ''))[:200],
            vendor_tax_id=str(request.data.get('vendor_tax_id', ''))[:20],
            amount=_dec(request.data['amount']) if request.data.get('amount') else None,
            file=url,
            original_filename=f'scan_{len(images)}pages.pdf',
            notes='נסרק מהנייד',
        )
        return Response({'ok': True, 'id': doc.id}, status=201)


class ScanQRView(APIView):
    """GET /billing/scan-qr/ — returns the scan URL and a QR PNG (base64)
    for the desktop to display/print. POST regenerates the token (kills
    the old QR instantly)."""
    permission_classes = [IsManager]

    def get(self, request):
        token = _get_or_create_scan_token()
        if not token:
            return Response({'error': 'no company settings'}, status=400)
        # Derive the mount prefix from this view's own path so the scan
        # URL is correct no matter where the API is mounted.
        prefix = request.path[:-len('billing/scan-qr/')]
        scan_url = request.build_absolute_uri(f'{prefix}scan/{token}/')

        qr_b64 = None
        try:
            import qrcode
            import io as _io
            import base64 as _b64
            img = qrcode.make(scan_url, box_size=10, border=2)
            buf = _io.BytesIO()
            img.save(buf, format='PNG')
            qr_b64 = _b64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            print(f'[SCAN-QR] generation failed: {e}', flush=True)

        return Response({'url': scan_url, 'qr_png_base64': qr_b64})

    def post(self, request):
        import secrets
        co = CompanySettings.objects.first()
        co.scan_token = secrets.token_urlsafe(24)
        co.save(update_fields=['scan_token'])
        return self.get(request)


class FinanceExportPDFView(APIView):
    """GET /billing/finance-docs/export-pdf/?year=&month=[&kind=] —
    merges the whole month's archive into ONE PDF for the accountant.
    PDFs are appended as-is; image documents become pages."""
    permission_classes = [IsManager]

    def get(self, request):
        year = request.query_params.get('year')
        month = request.query_params.get('month')
        if not year or not month:
            return Response({'error': 'year and month required'}, status=400)
        qs = FinanceDocument.objects.filter(
            doc_date__year=year, doc_date__month=month)
        if request.query_params.get('kind'):
            qs = qs.filter(kind=request.query_params['kind'])
        qs = qs.order_by('doc_date', 'id')
        if not qs.exists():
            return Response({'error': 'no documents for this month'},
                            status=404)

        import requests as _rq
        import fitz
        merged = fitz.open()
        added = 0
        for doc in qs:
            try:
                name = str(getattr(doc.file, 'name', '') or '')
                url = name if name.startswith('http') else doc.file.url
                resp = _rq.get(url, timeout=30)
                if resp.status_code != 200 or not resp.content:
                    continue
                data = resp.content
                if data[:5] == b'%PDF-':
                    part = fitz.open(stream=data, filetype='pdf')
                    merged.insert_pdf(part)
                    part.close()
                else:
                    img = fitz.open(stream=data, filetype='image')
                    rect = img[0].rect
                    page_pdf = fitz.open('pdf', img.convert_to_pdf())
                    page = merged.new_page(width=rect.width,
                                           height=rect.height)
                    page.show_pdf_page(rect, page_pdf, 0)
                    img.close()
                    page_pdf.close()
                added += 1
            except Exception as e:
                print(f'[EXPORT] doc {doc.id} skipped: {e}', flush=True)

        if added == 0:
            merged.close()
            return Response({'error': 'no documents could be merged'},
                            status=502)

        out = merged.tobytes(deflate=True)
        merged.close()
        from django.http import HttpResponse
        resp = HttpResponse(out, content_type='application/pdf')
        resp['Content-Disposition'] = (
            f'attachment; filename="TruckForce_{year}_{int(month):02d}.pdf"')
        return resp


# ══════════════════════════════════════════════════════════════════
# PACKAGES (package_delivery stops) — load/deliver flow + leftover log
# ══════════════════════════════════════════════════════════════════
class StopPackagesView(APIView):
    """GET packages for a stop; POST creates one (manager or the
    assigned driver, e.g. AI-confirmed list)."""
    permission_classes = [IsManagerOrDriver]

    def get(self, request, stop_id):
        pkgs = Package.objects.filter(stop_id=stop_id)
        return Response(PackageSerializer(pkgs, many=True,
                        context={'request': request}).data)

    def post(self, request, stop_id):
        try:
            stop = Stop.objects.get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)
        data = request.data.copy()
        data['stop'] = stop.id
        ser = PackageSerializer(data=data, context={'request': request})
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(ser.data, status=201)


class SchedulePackagesView(APIView):
    """All packages for a whole day's schedule, grouped by stop — the
    phone's day list (stop → its packages with load/deliver checkboxes)."""
    permission_classes = [IsManagerOrDriver]

    def get(self, request, schedule_id):
        stops = (Stop.objects.filter(schedule_id=schedule_id)
                 .order_by('order').prefetch_related('packages'))
        out = []
        for st in stops:
            pkgs = PackageSerializer(st.packages.all(), many=True,
                                     context={'request': request}).data
            out.append({
                'stop_id':   st.id,
                'order':     st.order,
                'site_name': st.site_name,
                'address':   st.address,
                'status':    st.status,
                'packages':  pkgs,
            })
        return Response(out)


class PackageDetailView(APIView):
    """PATCH a single package: toggle is_loaded / is_delivered / status.
    Sets the matching timestamp automatically."""
    permission_classes = [IsManagerOrDriver]

    def patch(self, request, pk):
        try:
            pkg = Package.objects.get(pk=pk)
        except Package.DoesNotExist:
            return Response({'error': 'Package not found'}, status=404)
        ser = PackageSerializer(pkg, data=request.data, partial=True,
                                context={'request': request})
        ser.is_valid(raise_exception=True)
        obj = ser.save()
        # keep timestamps + status coherent with the flags
        changed = False
        if obj.is_loaded and obj.loaded_at is None:
            obj.loaded_at = timezone.now()
            if obj.status == 'pending':
                obj.status = 'loaded'
            changed = True
        if obj.is_delivered and obj.delivered_at is None:
            obj.delivered_at = timezone.now()
            obj.status = 'delivered'
            changed = True
        if changed:
            obj.save()
        return Response(PackageSerializer(obj, context={'request': request}).data)

    def delete(self, request, pk):
        Package.objects.filter(pk=pk).delete()
        return Response(status=204)


class LeftoverPackagesView(APIView):
    """The leftover log: packages not yet loaded/delivered for a driver —
    what didn't fit and rolls to a later trip, by code."""
    permission_classes = [IsManagerOrDriver]

    def get(self, request):
        driver = getattr(request, 'driver', None)
        qs = Package.objects.filter(
            is_delivered=False,
        ).exclude(status='delivered').select_related('stop', 'stop__schedule')
        if driver is not None:
            qs = qs.filter(stop__schedule__driver=driver)
        return Response(PackageSerializer(qs, many=True,
                        context={'request': request}).data)


# ══════════════════════════════════════════════════════════════════
# Location-link → coordinates (Waze / Google / OSM / raw coords)
# Driver pastes a link when self-creating a stop; the server parser
# resolves it (following short-link redirects where needed).
# ══════════════════════════════════════════════════════════════════
class ParseLocationLinkView(APIView):
    permission_classes = [IsManagerOrDriver]

    def post(self, request):
        text = (request.data.get('link') or request.data.get('text') or '').strip()
        if not text:
            return Response({'error': 'No link provided'}, status=400)

        from django.conf import settings as _dj
        _gkey = getattr(_dj, 'GOOGLE_GEOCODING_KEY', '') or ''
        _mtok = getattr(_dj, 'MAPBOX_TOKEN', '') or ''

        def _geocode(name):
            """Place name -> (lat, lng). Google first (best match for a name
            that CAME from Google Maps), Mapbox as fallback. None if neither."""
            if not name:
                return None
            import requests as _rq
            if _gkey:
                try:
                    r = _rq.get('https://maps.googleapis.com/maps/api/geocode/json',
                                params={'address': name, 'key': _gkey, 'region': 'il'},
                                timeout=10)
                    if r.status_code == 200:
                        res = r.json().get('results') or []
                        if res:
                            loc = res[0]['geometry']['location']
                            return (float(loc['lat']), float(loc['lng']))
                except Exception:
                    pass
            if _mtok:
                try:
                    import urllib.parse as _up
                    q = _up.quote(name)
                    r = _rq.get(
                        f'https://api.mapbox.com/geocoding/v5/mapbox.places/{q}.json',
                        params={'access_token': _mtok, 'country': 'il', 'limit': 1},
                        timeout=10)
                    if r.status_code == 200:
                        feats = r.json().get('features') or []
                        if feats:
                            c = feats[0]['center']  # [lng, lat]
                            return (float(c[1]), float(c[0]))
                except Exception:
                    pass
            return None

        try:
            result = location_url_parser.parse(text, geocode=_geocode)
        except Exception:
            result = None
        if not result:
            return Response({'found': False}, status=200)
        lat, lng = result
        return Response({'found': True, 'latitude': lat, 'longitude': lng})

# ══════════════════════════════════════════════════════════════════
# Google Places — Autocomplete + Details (server-proxied)
# The API key never leaves the backend. Used by the desktop assignments
# page and the driver app to search an address/POI by typing and pick
# from suggestions. Place IDs from autocomplete are resolved to coords
# by the details endpoint.
# ══════════════════════════════════════════════════════════════════
def _places_key():
    """Prefer a dedicated Places key; fall back to the geocoding key."""
    from django.conf import settings as _dj
    return (getattr(_dj, 'GOOGLE_PLACES_KEY', '') or
            getattr(_dj, 'GOOGLE_GEOCODING_KEY', '') or '')


class PlacesAutocompleteView(APIView):
    """POST {query, sessiontoken?, language?} ->
            {predictions: [{description, place_id}]}
    Type-ahead suggestions via Google Places Autocomplete, biased to Israel."""
    permission_classes = [IsManagerOrDriver]

    def post(self, request):
        query = (request.data.get('query') or request.data.get('input') or '').strip()
        if len(query) < 2:
            return Response({'predictions': []})
        key = _places_key()
        if not key:
            return Response({'predictions': [], 'error': 'no_places_key'}, status=200)
        token = request.data.get('sessiontoken') or ''
        lang = request.data.get('language') or 'iw'   # Google uses 'iw' for Hebrew
        import requests as _rq
        try:
            r = _rq.get(
                'https://maps.googleapis.com/maps/api/place/autocomplete/json',
                params={
                    'input':        query,
                    'key':          key,
                    'language':     lang,
                    'components':   'country:il',
                    'sessiontoken': token,
                },
                timeout=10)
            j = r.json()
            status = j.get('status')
            if status == 'REQUEST_DENIED':
                # Almost always: Places API not enabled on the key, or the
                # key is API-restricted to Geocoding only.
                return Response({'predictions': [],
                                 'error': 'request_denied',
                                 'detail': j.get('error_message', '')}, status=200)
            preds = [{'description': p.get('description', ''),
                      'place_id':    p.get('place_id', '')}
                     for p in (j.get('predictions') or [])]
            return Response({'predictions': preds[:6]})
        except Exception as e:
            print(f"[PLACES] autocomplete failed: {e}", flush=True)
            return Response({'predictions': [], 'error': 'exception'}, status=200)


class PlaceDetailsView(APIView):
    """POST {place_id, sessiontoken?} ->
            {found, latitude, longitude, address}
    Resolves an autocomplete prediction to coordinates."""
    permission_classes = [IsManagerOrDriver]

    def post(self, request):
        place_id = (request.data.get('place_id') or '').strip()
        if not place_id:
            return Response({'found': False}, status=200)
        key = _places_key()
        if not key:
            return Response({'found': False, 'error': 'no_places_key'}, status=200)
        token = request.data.get('sessiontoken') or ''
        import requests as _rq
        try:
            r = _rq.get(
                'https://maps.googleapis.com/maps/api/place/details/json',
                params={
                    'place_id':     place_id,
                    'key':          key,
                    'fields':       'geometry,formatted_address,name',
                    'sessiontoken': token,
                },
                timeout=10)
            j = r.json()
            if j.get('status') != 'OK':
                return Response({'found': False,
                                 'error':  j.get('status', 'error'),
                                 'detail': j.get('error_message', '')}, status=200)
            res = j.get('result') or {}
            loc = (res.get('geometry') or {}).get('location') or {}
            if 'lat' not in loc or 'lng' not in loc:
                return Response({'found': False}, status=200)
            return Response({
                'found':     True,
                'latitude':  float(loc['lat']),
                'longitude': float(loc['lng']),
                'address':   res.get('formatted_address') or res.get('name') or '',
            })
        except Exception as e:
            print(f"[PLACES] details failed: {e}", flush=True)
            return Response({'found': False, 'error': 'exception'}, status=200)


class PlaceResolveView(APIView):
    """POST {query, language?} ->
            {found, latitude, longitude, name, address, place_id, maps_link}
    Resolves free text to the single best-matching place via Google Places
    'Find Place From Text', and returns a ready-to-open Google Maps URL whose
    query carries the coordinates (so the same link is also parseable by the
    stop's location-link field)."""
    permission_classes = [IsManagerOrDriver]

    def post(self, request):
        query = (request.data.get('query') or request.data.get('text') or '').strip()
        if len(query) < 2:
            return Response({'found': False}, status=200)
        key = _places_key()
        if not key:
            return Response({'found': False, 'error': 'no_places_key'}, status=200)
        lang = request.data.get('language') or 'iw'
        import requests as _rq
        try:
            r = _rq.get(
                'https://maps.googleapis.com/maps/api/place/findplacefromtext/json',
                params={
                    'input':     query,
                    'inputtype': 'textquery',
                    'fields':    'geometry,name,formatted_address,place_id',
                    'language':  lang,
                    'key':       key,
                },
                timeout=10)
            j = r.json()
            if j.get('status') == 'REQUEST_DENIED':
                return Response({'found': False, 'error': 'request_denied',
                                 'detail': j.get('error_message', '')}, status=200)
            cands = j.get('candidates') or []
            if not cands:
                return Response({'found': False}, status=200)
            c = cands[0]
            loc = (c.get('geometry') or {}).get('location') or {}
            if 'lat' not in loc or 'lng' not in loc:
                return Response({'found': False}, status=200)
            lat = float(loc['lat'])
            lng = float(loc['lng'])
            place_id = c.get('place_id', '')
            # query carries the coords (parseable by the stop link field);
            # query_place_id makes Google open the exact place card.
            import urllib.parse as _up
            link = ('https://www.google.com/maps/search/?api=1'
                    f'&query={lat},{lng}')
            if place_id:
                link += '&query_place_id=' + _up.quote(place_id)
            return Response({
                'found':     True,
                'latitude':  lat,
                'longitude': lng,
                'name':      c.get('name', ''),
                'address':   c.get('formatted_address', ''),
                'place_id':  place_id,
                'maps_link': link,
            })
        except Exception as e:
            print(f"[PLACES] resolve failed: {e}", flush=True)
            return Response({'found': False, 'error': 'exception'}, status=200)


# ─── AI delivery-sheet reader ───────────────────────────────────────────────
class ParseDeliverySheetView(APIView):
    """Upload a delivery sheet (PDF, one photo, or several photos) and get back
    structured {date, drivers:[{driver, matched_driver, stops}]}.

    Used by the desktop "Review & Import" popup (manager) and by the driver app
    "Scan sheet" flow (driver scans his own papers). Graceful if OpenAI is off.
    """
    permission_classes = [IsManagerOrDriver]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        # Accept many photos ('files') OR a single file ('file'/'sheet').
        uploads = request.FILES.getlist('files')
        if not uploads:
            single = request.FILES.get('file') or request.FILES.get('sheet')
            uploads = [single] if single else []
        if not uploads:
            return Response({'error': 'no_file'}, status=400)

        files = []
        for f in uploads:
            try:
                fb = f.read()
            except Exception:
                continue
            if fb:
                files.append((fb, getattr(f, 'content_type', '') or '',
                              getattr(f, 'name', '') or ''))
        if not files:
            return Response({'error': 'empty_file'}, status=400)

        from .ai_delivery_sheet import parse_delivery_files, match_driver
        result = parse_delivery_files(files)
        if result.get('error') == 'no_openai_key':
            return Response(
                {'error': 'OpenAI is not configured on the server.'},
                status=503)
        for entry in result.get('drivers', []):
            entry['matched_driver'] = match_driver(entry.get('driver'))
        return Response(result, status=200)


class SendDeliveryNoteView(APIView):
    """Send/re-send a stop's signed delivery note by email or WhatsApp."""
    permission_classes = [IsManagerOrDriver]

    def post(self, request, stop_id):
        from .delivery_send import send_note
        stop = get_object_or_404(Stop, pk=stop_id)
        status_code, payload = send_note(
            stop,
            request.data.get('channel'),
            request.data.get('email'),
            request.data.get('phone'),
            request,
        )
        return Response(payload, status=status_code)



class DriverStopEditView(APIView):
    """Driver edits or deletes a stop on their OWN schedule.

    Only stops that are still pending may be changed — a completed (done or
    skipped) delivery is locked. Lets a driver fix details they mistyped
    (address, items, contact, etc.) or remove a stop they added by mistake.
    """
    permission_classes = [IsDriver]

    # Detail fields a driver may change. Mirrors the self-create flow.
    _EDITABLE = (
        'site_name', 'address', 'latitude', 'longitude',
        'notes', 'contact_name', 'contact_phone', 'contact_email',
        'items', 'stop_type',
    )

    def _get_stop(self, request, pk):
        return Stop.objects.get(pk=pk, schedule__driver=request.driver)

    def patch(self, request, pk):
        try:
            stop = self._get_stop(request, pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        if stop.status in ('done', 'skipped') or stop.completed_at:
            return Response(
                {'error': 'This stop is already completed and can no longer be edited'},
                status=400)

        for field in self._EDITABLE:
            if field in request.data:
                val = request.data.get(field)
                # Empty coordinate → NULL, so a blank doesn't break the Decimal field.
                if field in ('latitude', 'longitude') and (val == '' or val is None):
                    val = None
                setattr(stop, field, val)

        if not (stop.site_name or '').strip():
            return Response({'error': 'site_name is required'}, status=400)

        stop.save()
        try:
            publish_event('schedules_changed',
                          by_user_id=getattr(request.driver, 'id', None))
        except Exception:
            pass
        return Response(StopSerializer(stop).data)

    def delete(self, request, pk):
        try:
            stop = self._get_stop(request, pk)
        except Stop.DoesNotExist:
            return Response({'error': 'Not found'}, status=404)

        if stop.status in ('done', 'skipped') or stop.completed_at:
            return Response(
                {'error': 'This stop is already completed and cannot be deleted'},
                status=400)

        stop.delete()
        try:
            publish_event('schedules_changed',
                          by_user_id=getattr(request.driver, 'id', None))
        except Exception:
            pass
        return Response({'ok': True}, status=200)

