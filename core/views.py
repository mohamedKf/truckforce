from django.utils import timezone
from django.db import transaction
from django.db import models
from django.db.models import Q
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.parsers import MultiPartParser, FormParser

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
    Payroll, NotificationLog, Document
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
    notify_manager_day_summary,
    notify_driver_payslip_ready,
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
    """Driver updates their own FCM token after app launch."""
    permission_classes = [IsDriver]

    def post(self, request):
        token = request.data.get('fcm_token', '')
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
        ser = DailyScheduleSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(ser.data)

    def delete(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        obj.delete()
        return Response(status=204)

    def patch(self, request, pk):
        if not hasattr(request, 'manager'):
            return Response({'error': 'Managers only'}, status=403)
        obj = self.get_object(pk)
        if not obj:
            return Response({'error': 'Not found'}, status=404)
        ser = DailyScheduleSerializer(obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(ser.data)


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
        }
        clean = {k: v for k, v in data.items() if k in EDITABLE}
        if not clean.get('site_name'):
            return Response({'error': 'site_name is required'}, status=400)

        stop = Stop.objects.create(schedule=schedule, **clean)
        publish_event('schedules_changed', by_user_id=getattr(request.manager, 'id', None))
        return Response(StopSerializer(stop).data, status=201)


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
            # Notify all managers immediately
            managers = Manager.objects.filter(is_active=True)
            for manager in managers:
                notify_manager_stop_skipped(manager, request.driver, stop)
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
    """Driver phone posts current GPS location while clocked in."""
    permission_classes = [IsDriver]

    def post(self, request):
        driver = request.driver

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
        loc = DriverLocation.objects.create(
            driver=driver,
            latitude=lat,
            longitude=lng,
            speed=request.data.get('speed'),
            heading=request.data.get('heading'),
            accuracy=request.data.get('accuracy'),
        )

        # ── Auto-detect arrival at planned stops ──
        newly_arrived_ids = []
        if lat and lng:
            try:
                from .arrival_detection import check_arrivals_for_driver
                newly_arrived_ids = check_arrivals_for_driver(driver, lat, lng)
            except Exception as e:
                print(f"[ARRIVAL] detection error: {e}", flush=True)

        return Response({
            'ok': True,
            'id': loc.id,
            'newly_arrived_stops': newly_arrived_ids,
        }, status=status.HTTP_201_CREATED)


class ActiveDriversLocationsView(APIView):
    """Manager desktop fetches all currently-clocked-in drivers with latest location + trail."""
    permission_classes = [IsManager]

    def get(self, request):
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

        photo = StopPhoto.objects.create(stop=stop, image=image)
        return Response(StopPhotoSerializer(photo).data, status=201)


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

# ──────────────────────────────────────────────
# ATTENDANCE FIX REQUESTS
# ──────────────────────────────────────────────

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
            # Apply the changes to the Attendance row
            att, _ = Attendance.objects.get_or_create(driver=fr.driver, date=fr.date)
            if fr.requested_clock_in is not None:
                att.clock_in = fr.requested_clock_in
            if fr.requested_clock_out is not None:
                att.clock_out = fr.requested_clock_out
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
    POST: Driver submits signature (as PNG) to create a delivery confirmation.
    Generates PDF, sends via WhatsApp + email, stores everything.
    GET:  Returns existing confirmation for this stop.
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
        return Response(DeliveryConfirmationSerializer(stop.confirmation).data)

    def post(self, request, stop_id):
        try:
            stop = Stop.objects.get(pk=stop_id)
        except Stop.DoesNotExist:
            return Response({'error': 'Stop not found'}, status=404)

        if hasattr(request, 'driver') and request.driver is not None:
            if stop.schedule.driver_id != request.driver.id:
                return Response({'error': 'Forbidden'}, status=403)

        if hasattr(stop, 'confirmation'):
            return Response({
                'error': 'Already signed',
                'confirmation': DeliveryConfirmationSerializer(stop.confirmation).data,
            }, status=400)

        signature_file  = request.FILES.get('signature')
        signed_by_name  = request.data.get('signed_by_name', '').strip()
        signed_by_phone = request.data.get('signed_by_phone', '').strip()
        signed_by_email = request.data.get('signed_by_email', '').strip()

        if not signature_file:
            return Response({'error': 'signature image required'}, status=400)
        if not signed_by_name:
            return Response({'error': 'signed_by_name required'}, status=400)

        conf = DeliveryConfirmation.objects.create(
            stop=stop,
            signed_by_name=signed_by_name,
            signed_by_phone=signed_by_phone,
            signed_by_email=signed_by_email,
            signature_image=signature_file,
        )

        # Generate PDF
        try:
            from .delivery_pdf import generate_delivery_pdf
            from django.core.files.base import ContentFile
            pdf_bytes = generate_delivery_pdf(conf)
            conf.pdf_file.save(
                f'confirmation_{stop.id}.pdf',
                ContentFile(pdf_bytes),
                save=True,
            )
        except Exception as e:
            print(f'[PDF] generation error: {e}', flush=True)

        # Send notifications in background
        import threading
        _conf_pk = conf.pk
        def _send():
            from core.models import DeliveryConfirmation as DC
            c = DC.objects.get(pk=_conf_pk)
            wa_ok    = _send_confirmation_whatsapp(c)
            email_ok = _send_confirmation_email(c)
            DC.objects.filter(pk=_conf_pk).update(
                whatsapp_sent=wa_ok,
                email_sent=email_ok,
            )
            print(f'[CONFIRMATION] WA={wa_ok} Email={email_ok}', flush=True)
        threading.Thread(target=_send, daemon=True).start()

        publish_event('schedules_changed',
                      by_user_id=getattr(getattr(request, 'driver', None), 'id', None))
        return Response(DeliveryConfirmationSerializer(conf).data, status=201)


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
        import base64, os
        from django.core.files.base import ContentFile

        image_file = request.FILES.get('photo')
        if not image_file:
            return Response({'error': 'photo file required'}, status=400)

        driver = request.driver

        # Save to media/profile/{driver_id}.jpg (permanent URL)
        ext  = os.path.splitext(image_file.name)[1] or '.jpg'
        name = f'{driver.id}{ext}'
        # Delete old file if exists
        if driver.photo:
            try:
                driver.photo.delete(save=False)
            except Exception:
                pass
        driver.photo.save(name, image_file, save=False)

        # Also store as base64 for desktop to pick up
        image_file.seek(0)
        raw   = image_file.read()
        b64   = base64.b64encode(raw).decode('utf-8')
        mime  = image_file.content_type or 'image/jpeg'
        driver.photo_b64 = f'data:{mime};base64,{b64}'
        driver.save(update_fields=['photo', 'photo_b64'])

        # Fire Firebase event so desktop knows to download
        publish_event('drivers_changed', by_user_id=driver.id)

        return Response({
            'ok': True,
            'photo_url': request.build_absolute_uri(driver.photo.url) if driver.photo else None,
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