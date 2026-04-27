from rest_framework import serializers

from .models import (
    CompanySettings, Manager, Driver, Truck,
    DailySchedule, Stop, Attendance, CraneSession,
    Payroll, NotificationLog, Document,
    Accountant, PayrollSendLog,
    ChildOfDriver, PayrollConfig, Payslip,
    StopPhoto, AttendanceFixRequest, DeliveryConfirmation,
)


# ──────────────────────────────────────────────
# AUTH TOKEN (simple custom token)
# ──────────────────────────────────────────────

class ManagerLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class DriverLoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


# ──────────────────────────────────────────────
# COMPANY SETTINGS
# ──────────────────────────────────────────────

class CompanySettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model  = CompanySettings
        fields = '__all__'


# ──────────────────────────────────────────────
# MANAGER
# ──────────────────────────────────────────────

class ManagerSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False)

    class Meta:
        model  = Manager
        fields = [
            'id', 'full_name', 'username', 'password',
            'email', 'phone', 'role', 'is_active',
            'fcm_token', 'created_at',
        ]
        read_only_fields = ['created_at']

    def create(self, validated_data):
        raw_pw = validated_data.pop('password', None)
        manager = Manager(**validated_data)
        if raw_pw:
            manager.set_password(raw_pw)
        manager.save()
        return manager

    def update(self, instance, validated_data):
        raw_pw = validated_data.pop('password', None)
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        if raw_pw:
            instance.set_password(raw_pw)
        instance.save()
        return instance


# ──────────────────────────────────────────────
# DRIVER
# ──────────────────────────────────────────────

class DriverSerializer(serializers.ModelSerializer):
    password  = serializers.CharField(write_only=True, required=False)
    photo_url = serializers.SerializerMethodField()

    class Meta:
        model  = Driver
        fields = [
            'id', 'username', 'password',
            'full_name', 'id_number', 'email', 'phone',
            'address', 'birth_date', 'hire_date', 'photo', 'photo_url',
            'photo_b64', 'is_active',
            'license_type', 'license_number', 'license_expiry',
            'max_tonnage', 'crane_certified', 'crane_license_expiry',
            'salary_type', 'base_rate', 'overtime_rate', 'crane_hourly_rate',
            'travel_allowance', 'tax_credit_points',
            'pension_percent', 'study_fund_percent',
            'fcm_token', 'created_at',
        ]
        read_only_fields = ['created_at']

    def get_photo_url(self, obj):
        request = self.context.get('request')
        if obj.photo and request:
            return request.build_absolute_uri(obj.photo.url)
        if obj.photo:
            return obj.photo.url
        return None

    def create(self, validated_data):
        raw_pw = validated_data.pop('password', None)
        driver = Driver(**validated_data)
        if raw_pw:
            driver.set_password(raw_pw)
        driver.save()
        return driver

    def update(self, instance, validated_data):
        raw_pw = validated_data.pop('password', None)
        for attr, val in validated_data.items():
            setattr(instance, attr, val)
        if raw_pw:
            instance.set_password(raw_pw)
        instance.save()
        return instance


class DriverListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for lists/dropdowns."""
    class Meta:
        model  = Driver
        fields = ['id', 'full_name', 'username', 'license_type',
                  'crane_certified', 'is_active', 'phone']


# ──────────────────────────────────────────────
# TRUCK
# ──────────────────────────────────────────────

class TruckSerializer(serializers.ModelSerializer):
    assigned_driver_name = serializers.CharField(
        source='assigned_driver.full_name', read_only=True
    )

    class Meta:
        model  = Truck
        fields = [
            'id', 'brand', 'model', 'year', 'plate_number',
            'capacity_tons', 'has_crane', 'status',
            'last_service_date', 'next_service_date',
            'last_tire_change', 'next_tire_change',
            'last_inspection', 'next_inspection',
            'odometer_km', 'notes',
            'assigned_driver', 'assigned_driver_name',
            'created_at',
        ]
        read_only_fields = ['created_at']


class TruckListSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Truck
        fields = ['id', 'brand', 'model', 'plate_number',
                  'capacity_tons', 'has_crane', 'status']


# ──────────────────────────────────────────────
# STOPS
# ──────────────────────────────────────────────

class StopPhotoSerializer(serializers.ModelSerializer):
    class Meta:
        model  = StopPhoto
        fields = ['id', 'stop', 'image', 'uploaded_at']
        read_only_fields = ['uploaded_at']


class StopSerializer(serializers.ModelSerializer):
    waze_link        = serializers.CharField(read_only=True)
    google_maps_link = serializers.CharField(read_only=True)
    apple_maps_link  = serializers.CharField(read_only=True)
    is_late          = serializers.BooleanField(read_only=True)
    photos           = StopPhotoSerializer(many=True, read_only=True)

    class Meta:
        model  = Stop
        fields = [
            'id', 'schedule', 'order', 'site_name', 'address',
            'latitude', 'longitude',
            'waze_link', 'google_maps_link', 'apple_maps_link',
            'expected_arrival', 'actual_arrival', 'allow_driver_reorder',
            'photos', 'is_late',
            'notes', 'status', 'completed_at', 'skip_reason',
        ]
        read_only_fields = ['completed_at', 'actual_arrival']


class StopUpdateSerializer(serializers.ModelSerializer):
    """Driver uses this to mark stop done/skipped."""
    class Meta:
        model  = Stop
        fields = ['status', 'skip_reason', 'completed_at', 'actual_arrival']


# ──────────────────────────────────────────────
# DAILY SCHEDULE
# ──────────────────────────────────────────────

class DailyScheduleSerializer(serializers.ModelSerializer):
    stops               = StopSerializer(many=True, read_only=True)
    driver_name         = serializers.CharField(source='driver.full_name', read_only=True)
    truck_plate         = serializers.CharField(source='truck.plate_number', read_only=True)
    missed_stops_count  = serializers.IntegerField(read_only=True)
    completion_percent  = serializers.IntegerField(read_only=True)

    class Meta:
        model  = DailySchedule
        fields = [
            'id', 'driver', 'driver_name', 'truck', 'truck_plate',
            'date', 'status', 'manager_notes', 'created_by',
            'missed_stops_count', 'completion_percent',
            'stops', 'created_at',
        ]
        read_only_fields = ['created_at']


class StopCreateNestedSerializer(serializers.ModelSerializer):
    """Used only when creating stops nested inside a schedule — 'schedule' FK is set by parent."""
    class Meta:
        model  = Stop
        fields = [
            'order', 'site_name', 'address',
            'latitude', 'longitude',
            'expected_arrival', 'notes',
        ]
        extra_kwargs = {
            'order':     {'required': False},
            'address':   {'required': False, 'allow_blank': True},
            'notes':     {'required': False, 'allow_blank': True},
            'latitude':  {'required': False, 'allow_null': True},
            'longitude': {'required': False, 'allow_null': True},
            'expected_arrival': {'required': False, 'allow_null': True},
        }


class DailyScheduleCreateSerializer(serializers.ModelSerializer):
    stops = StopCreateNestedSerializer(many=True)

    class Meta:
        model  = DailySchedule
        fields = ['driver', 'truck', 'date', 'manager_notes', 'created_by', 'stops']

    def create(self, validated_data):
        stops_data = validated_data.pop('stops')
        schedule   = DailySchedule.objects.create(**validated_data)
        for i, stop_data in enumerate(stops_data):
            stop_data['schedule'] = schedule
            stop_data.setdefault('order', i + 1)
            Stop.objects.create(**stop_data)
        return schedule


# ──────────────────────────────────────────────
# ATTENDANCE
# ──────────────────────────────────────────────

class AttendanceSerializer(serializers.ModelSerializer):
    driver_name  = serializers.CharField(source='driver.full_name', read_only=True)
    total_hours  = serializers.FloatField(read_only=True)

    class Meta:
        model  = Attendance
        fields = [
            'id', 'driver', 'driver_name', 'date',
            'clock_in', 'clock_out',
            'clock_in_lat', 'clock_in_lng',
            'clock_out_lat', 'clock_out_lng',
            'notes', 'edited_by', 'total_hours',
        ]


class ClockInSerializer(serializers.Serializer):
    latitude  = serializers.DecimalField(max_digits=12, decimal_places=8, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8, required=False, allow_null=True)


class ClockOutSerializer(serializers.Serializer):
    latitude  = serializers.DecimalField(max_digits=12, decimal_places=8, required=False, allow_null=True)
    longitude = serializers.DecimalField(max_digits=12, decimal_places=8, required=False, allow_null=True)
    notes     = serializers.CharField(required=False, allow_blank=True)


# ──────────────────────────────────────────────
# CRANE SESSION
# ──────────────────────────────────────────────

class CraneSessionSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.full_name', read_only=True)
    truck_plate = serializers.CharField(source='truck.plate_number', read_only=True)

    class Meta:
        model  = CraneSession
        fields = [
            'id', 'driver', 'driver_name', 'truck', 'truck_plate',
            'schedule', 'stop', 'date',
            'arrival_time', 'work_start', 'work_end',
            'raw_minutes', 'billed_hours',
            'price_per_hour', 'total_charge',
            'notes', 'created_at',
        ]
        read_only_fields = ['raw_minutes', 'billed_hours', 'total_charge', 'created_at']


class CraneStartSerializer(serializers.Serializer):
    truck    = serializers.IntegerField(required=False)
    schedule = serializers.IntegerField(required=False)
    stop     = serializers.IntegerField(required=False)
    notes    = serializers.CharField(required=False, allow_blank=True)


class CraneEndSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True)


# ──────────────────────────────────────────────
# PAYROLL
# ──────────────────────────────────────────────

class PayrollSerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.full_name', read_only=True)

    class Meta:
        model  = Payroll
        fields = '__all__'
        read_only_fields = ['generated_at']


class PayrollSummarySerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.full_name', read_only=True)

    class Meta:
        model  = Payroll
        fields = [
            'id', 'driver', 'driver_name', 'month', 'year',
            'gross_pay', 'net_pay', 'status',
        ]


# ──────────────────────────────────────────────
# NOTIFICATION LOG
# ──────────────────────────────────────────────

class NotificationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model  = NotificationLog
        fields = '__all__'


# ──────────────────────────────────────────────
# DOCUMENT
# ──────────────────────────────────────────────

class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = Document
        fields = '__all__'
        read_only_fields = ['uploaded_at']


class AccountantSerializer(serializers.ModelSerializer):
    class Meta:
        model = Accountant
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at']


class PayrollSendLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayrollSendLog
        fields = '__all__'
        read_only_fields = ['sent_at']


# ═════════════════════════════════════════════════════════════════
# ADD TO core/serializers.py
# ═════════════════════════════════════════════════════════════════



# 2. Add these classes at bottom of serializers.py:


class ChildOfDriverSerializer(serializers.ModelSerializer):
    class Meta:
        model  = ChildOfDriver
        fields = '__all__'
        read_only_fields = ['created_at']


class PayrollConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PayrollConfig
        fields = '__all__'
        read_only_fields = ['updated_at']


class PayslipSerializer(serializers.ModelSerializer):
    driver_name   = serializers.CharField(source='driver.full_name', read_only=True)
    driver_id_num = serializers.CharField(source='driver.id_number', read_only=True)

    class Meta:
        model  = Payslip
        fields = '__all__'
        read_only_fields = ['generated_at', 'updated_at']


class PayslipSummarySerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.full_name', read_only=True)

    class Meta:
        model  = Payslip
        fields = ['id', 'driver', 'driver_name', 'year', 'month',
                  'working_days', 'total_hours', 'gross_pay', 'net_pay', 'status']


# ──────────────────────────────────────────────
# ATTENDANCE FIX REQUEST
# ──────────────────────────────────────────────

class AttendanceFixRequestSerializer(serializers.ModelSerializer):
    driver_name     = serializers.CharField(source='driver.full_name', read_only=True)
    decided_by_name = serializers.CharField(source='decided_by.full_name', read_only=True, allow_null=True)

    class Meta:
        model  = AttendanceFixRequest
        fields = [
            'id', 'driver', 'driver_name', 'date',
            'requested_clock_in', 'requested_clock_out', 'reason',
            'status', 'manager_note', 'decided_by', 'decided_by_name', 'decided_at',
            'created_at',
        ]
        read_only_fields = ['id', 'driver', 'driver_name', 'status',
                            'manager_note', 'decided_by', 'decided_by_name', 'decided_at',
                            'created_at']


# ─────────────────────────────────────────────
# DELIVERY CONFIRMATION
# ─────────────────────────────────────────────

class DeliveryConfirmationSerializer(serializers.ModelSerializer):
    signature_image_url = serializers.SerializerMethodField()
    pdf_url             = serializers.SerializerMethodField()

    class Meta:
        model  = DeliveryConfirmation
        fields = [
            'id', 'stop', 'signed_by_name', 'signed_by_phone', 'signed_by_email',
            'signature_image', 'signature_image_url',
            'pdf_file', 'pdf_url',
            'whatsapp_sent', 'email_sent', 'created_at',
        ]
        read_only_fields = ['id', 'whatsapp_sent', 'email_sent', 'created_at',
                            'signature_image_url', 'pdf_url']

    def get_signature_image_url(self, obj):
        request = self.context.get('request')
        if obj.signature_image and request:
            return request.build_absolute_uri(obj.signature_image.url)
        return str(obj.signature_image) if obj.signature_image else None

    def get_pdf_url(self, obj):
        request = self.context.get('request')
        if obj.pdf_file and request:
            return request.build_absolute_uri(obj.pdf_file.url)
        return str(obj.pdf_file) if obj.pdf_file else None