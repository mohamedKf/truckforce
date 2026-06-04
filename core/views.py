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
    TrackingLink, StopTask,
)
from .serializers import AttendanceFixRequestSerializer
from .serializers import (
    CompanySettingsSerializer,
    ManagerSerializer, ManagerLoginSerializer,
    DriverSerializer, DriverListSerializer, DriverLoginSerializer,
    TruckSerializer, TruckListSerializer,
    DailyScheduleSerializer, DailyScheduleCreateSerializer,
    StopSerializer, StopUpdateSerializer, StopPhotoSerializer,
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
        # Only admins see the current registration code — read from env, never DB
        if hasattr(request, 'manager') and request.manager.role == 'admin':
            data['registration_code']    = django_settings.REGISTRATION_CODE
            data['registration_enabled'] = django_settings.REGISTRATION_ENABLED
        return Response(data)

    def put(self, request):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        # Strip out env-only fields — they can't be saved to DB
        safe_data = {k: v for k, v in request.data.items()
                     if k not in ('registration_code', 'registration_enabled')}
        obj = CompanySettings.objects.first()
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
        if hasattr(request, 'driver') and obj.driver_id != request.driver.id:
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
        if hasattr(request, 'driver'):
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
        if hasattr(request, 'driver'):
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
        if hasattr(request, 'driver') and payroll.driver_id != request.driver.id:
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
        if hasattr(request, 'driver'):
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
        ser = DocumentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ser.save(uploaded_by=request.manager)
        return Response(ser.data, status=201)


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

        # Auto-close any stale shift (>14h) so we don't keep recording
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
    from datetime import timedelta
    from django.core.cache import cache
    if cache.get('locations_purge_done'):
        return
    cache.set('locations_purge_done', 1, 60 * 60 * 24)  # once per day
    try:
        cutoff = timezone.now() - timedelta(days=LOCATION_RETENTION_DAYS)
        deleted, _ = DriverLocation.objects.filter(timestamp__lt=cutoff).delete()
        if deleted:
            print(f'[LOCATIONS] purged {deleted} pings older than '
                  f'{LOCATION_RETENTION_DAYS} days', flush=True)
    except Exception as e:
        print(f'[LOCATIONS] purge failed: {e}', flush=True)


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

        # ── Build the signed PDF ───────────────────────────────────────────
        pdf_bytes = None

        # Preferred path: stamp onto the manager's actual delivery note.
        note_url = ''
        try:
            if stop.delivery_note_pdf:
                note_url = stop.delivery_note_pdf.url
        except Exception:
            note_url = ''

        if note_url and sig_bytes:
            try:
                import requests as _rq
                from .delivery_stamp import stamp_signature_on_note
                note_resp = _rq.get(note_url, timeout=30)
                if note_resp.status_code == 200 and note_resp.content:
                    pdf_bytes = stamp_signature_on_note(
                        note_resp.content, sig_bytes,
                        page=sig_page, nx=sig_x, ny=sig_y, nw=sig_w, nh=sig_h,
                    )
            except Exception as e:
                print(f'[STAMP] error: {e}', flush=True)

        # Fallback: no note attached (or stamping failed) → standalone PDF.
        if not pdf_bytes:
            try:
                from .delivery_pdf import generate_delivery_pdf
                pdf_bytes = generate_delivery_pdf(conf)
            except Exception as e:
                print(f'[PDF] generation error: {e}', flush=True)

        # ── Store the signed PDF (Cloudinary via the FileField storage) ────
        # _abs_url on the serializer keeps the read-side URL clean, so the
        # plain storage save is safe here and matches the prior flow.
        if pdf_bytes:
            try:
                from django.core.files.base import ContentFile
                conf.pdf_file.save(
                    f'confirmation_{stop.id}.pdf',
                    ContentFile(pdf_bytes),
                    save=True,
                )
            except Exception as e:
                print(f'[CONFIRMATION] PDF save failed: {e}', flush=True)

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
    No auth — desktop and phone check this on startup.
    """
    ver_file = os.path.join(RELEASES_DIR, 'version.json')
    try:
        with open(ver_file) as f:
            return JsonResponse(json.load(f))
    except FileNotFoundError:
        return JsonResponse({
            'version':      '1.0.0',
            'exe_url':      '',
            'apk_url':      '',
            'notes':        'Initial release',
            'force_update': False,
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