from rest_framework import serializers

from .models import (
    CompanySettings, Manager, Driver, Truck,
    DailySchedule, Stop, Attendance, CraneSession,
    Payroll, NotificationLog, Document,
    Accountant, PayrollSendLog,
    ChildOfDriver, PayrollConfig, Payslip,
    StopPhoto, AttendanceFixRequest, DeliveryConfirmation,
    Package, DeliverySheet, StopDocument,
)


# ──────────────────────────────────────────────
# URL HELPERS — Cloudinary returns absolute URLs already
# ──────────────────────────────────────────────

def _abs_url(field_file, request=None):
    """Return an absolute URL for a FileField/ImageField, defensively.

    We store Cloudinary's `secure_url` straight onto the field. The trap:
    the model uses a plain FileField whose default storage is Cloudinary's
    *image* storage, so calling `field_file.url` re-wraps the stored value
    into a doubled URL:

        .../image/upload/https://res.cloudinary.com/<cloud>/raw/upload/<file>

    Fix: inspect the RAW stored value (`.name`) FIRST. If it's already a
    full URL, return it verbatim and never call `.url` (the thing that
    corrupts it). `_undouble_cloudinary` also repairs any rows whose stored
    value was corrupted historically.
    """
    if not field_file:
        return None

    # Raw stored value — read it BEFORE touching .url.
    raw = getattr(field_file, 'name', None) or str(field_file)
    if raw and raw.startswith(("http://", "https://")):
        return _undouble_cloudinary(raw)

    # Legacy / local-media path needs the storage URL.
    try:
        url = field_file.url
    except (ValueError, AttributeError):
        return None
    if not url:
        return None
    url = _undouble_cloudinary(url)
    if url.startswith(("http://", "https://")):
        return url
    if request is not None:
        return request.build_absolute_uri(url)
    return url


def _undouble_cloudinary(url):
    """Repair a doubled Cloudinary URL, e.g.
        .../image/upload/https://res.cloudinary.com/.../raw/upload/file.pdf
    Returns the inner absolute URL. Handles the slash-collapsed `https:/`
    form Cloudinary sometimes emits. No-op for clean URLs.
    """
    if not url:
        return url
    idx = url.find('/upload/')
    if idx == -1:
        return url
    tail = url[idx + len('/upload/'):]
    for proto in ('https://', 'http://', 'https:/', 'http:/'):
        if tail.startswith(proto):
            if tail.startswith('https:/') and not tail.startswith('https://'):
                tail = 'https://' + tail[len('https:/'):]
            elif tail.startswith('http:/') and not tail.startswith('http://'):
                tail = 'http://' + tail[len('http:/'):]
            return tail
    return url


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
    # Cloudinary URL safety — see _abs_url at top of file.
    company_logo = serializers.SerializerMethodField()

    class Meta:
        model  = CompanySettings
        fields = '__all__'

    def get_company_logo(self, obj):
        return _abs_url(obj.company_logo, self.context.get('request'))


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
    # Both `photo` and `photo_url` return the same safe absolute URL. We keep
    # both names so existing clients (Flutter app reads `photo`, desktop reads
    # `photo_url`) don't have to change in lockstep.
    photo     = serializers.SerializerMethodField()
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

    def get_photo(self, obj):
        # Same safe URL — kept for clients that read `photo` directly.
        return _abs_url(obj.photo, self.context.get('request'))

    def get_photo_url(self, obj):
        # Cloudinary returns absolute URLs from obj.photo.url already, so we
        # MUST NOT re-wrap with request.build_absolute_uri — that produces
        # a doubled prefix on some Django versions. _abs_url handles this.
        return _abs_url(obj.photo, self.context.get('request'))

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
                  'crane_certified', 'is_active', 'phone',
                  # expiry dates needed by the notification bell
                  'license_expiry', 'crane_license_expiry']


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
                  'capacity_tons', 'has_crane', 'status',
                  # dates needed by the trucks page cells + notification bell
                  'last_service_date', 'next_service_date',
                  'last_tire_change', 'next_tire_change',
                  'last_inspection', 'next_inspection']


# ──────────────────────────────────────────────
# STOPS
# ──────────────────────────────────────────────

class StopPhotoSerializer(serializers.ModelSerializer):
    # Override default ImageField URL serialization. DRF would call
    # request.build_absolute_uri on top of Cloudinary's already-absolute
    # URL, which can double-prefix. _abs_url handles it safely.
    image = serializers.SerializerMethodField()

    class Meta:
        model  = StopPhoto
        fields = ['id', 'stop', 'image', 'uploaded_at']
        read_only_fields = ['uploaded_at']

    def get_image(self, obj):
        return _abs_url(obj.image, self.context.get('request'))


class StopDocumentSerializer(serializers.ModelSerializer):
    file_url      = serializers.SerializerMethodField()
    signature_url = serializers.SerializerMethodField()
    signed        = serializers.SerializerMethodField()

    class Meta:
        model  = StopDocument
        fields = ['id', 'stop', 'title', 'signer_name',
                  'file_url', 'signature_url',
                  'signed', 'signed_at', 'order', 'created_at']
        read_only_fields = ['signed_at', 'created_at']

    def get_file_url(self, obj):
        return _abs_url(obj.file, self.context.get('request'))

    def get_signature_url(self, obj):
        return _abs_url(obj.signature_image, self.context.get('request'))

    def get_signed(self, obj):
        return obj.signed_at is not None


class PackageSerializer(serializers.ModelSerializer):
    invoice_file_url = serializers.SerializerMethodField()

    def get_invoice_file_url(self, obj):
        return _abs_url(obj.invoice_file, self.context.get('request'))

    class Meta:
        model = Package
        fields = [
            'id', 'stop', 'product_name', 'product_code', 'tmsh_number',
            'barcode', 'quantity_pallets', 'quantity_units', 'weight_kg',
            'invoice_number', 'checker_name', 'returns',
            'is_loaded', 'is_delivered', 'status',
            'loaded_at', 'delivered_at', 'notes', 'invoice_file_url',
        ]
        read_only_fields = ['loaded_at', 'delivered_at']


class DeliverySheetSerializer(serializers.ModelSerializer):
    original_pdf_url = serializers.SerializerMethodField()
    signed_pdf_url   = serializers.SerializerMethodField()

    def get_original_pdf_url(self, obj):
        return _abs_url(obj.original_pdf, self.context.get('request'))

    def get_signed_pdf_url(self, obj):
        return _abs_url(obj.signed_pdf, self.context.get('request'))

    class Meta:
        model = DeliverySheet
        fields = ['id', 'schedule', 'original_pdf_url', 'signed_pdf_url',
                  'signature_count', 'created_at', 'updated_at']


class StopSerializer(serializers.ModelSerializer):
    waze_link        = serializers.CharField(read_only=True)
    google_maps_link = serializers.CharField(read_only=True)
    apple_maps_link  = serializers.CharField(read_only=True)
    is_late          = serializers.BooleanField(read_only=True)
    photos           = StopPhotoSerializer(many=True, read_only=True)

    # Pre-uploaded delivery-note PDF, served from Cloudinary. Uploaded
    # via the dedicated /stops/<pk>/delivery-note/ endpoint to keep
    # multipart logic out of regular stop CRUD.
    delivery_note_url = serializers.SerializerMethodField()

    def get_delivery_note_url(self, obj):
        return _abs_url(obj.delivery_note_pdf, self.context.get('request'))

    packages          = PackageSerializer(many=True, read_only=True)
    documents         = StopDocumentSerializer(many=True, read_only=True)
    invoice_file_url  = serializers.SerializerMethodField()

    def get_invoice_file_url(self, obj):
        return _abs_url(obj.invoice_file, self.context.get('request'))

    # Signed confirmation PDF (set once the driver collects a signature).
    confirmation_pdf_url = serializers.SerializerMethodField()

    def get_confirmation_pdf_url(self, obj):
        conf = getattr(obj, 'confirmation', None)
        if conf is None:
            return None
        return _abs_url(conf.pdf_file, self.context.get('request'))

    class Meta:
        model  = Stop
        fields = [
            'id', 'schedule', 'order', 'site_name', 'address',
            'latitude', 'longitude',
            'waze_link', 'google_maps_link', 'apple_maps_link',
            'expected_arrival', 'actual_arrival', 'allow_driver_reorder',
            'photos', 'is_late',
            'notes', 'status', 'completed_at', 'skip_reason',
            # ── Stop-type / pickup-delivery linkage ──
            'stop_type', 'items',
            'contact_name', 'contact_phone', 'contact_email',
            'invoice_number', 'invoice_file_url', 'invoice_signed',
            'packages',
            'documents',
            'pickup_stop',
            'driver_note',
            'delivery_note_url',
            'confirmation_pdf_url',
        ]
        read_only_fields = ['completed_at', 'actual_arrival']


class StopUpdateSerializer(serializers.ModelSerializer):
    """Driver uses this to mark stop done/skipped."""
    class Meta:
        model  = Stop
        fields = ['status', 'skip_reason', 'completed_at', 'actual_arrival', 'driver_note']


# ──────────────────────────────────────────────
# DAILY SCHEDULE
# ──────────────────────────────────────────────

class DailyScheduleSerializer(serializers.ModelSerializer):
    stops               = StopSerializer(many=True, read_only=True)
    driver_name         = serializers.CharField(source='driver.full_name', read_only=True)
    truck_plate         = serializers.CharField(source='truck.plate_number', read_only=True)
    missed_stops_count  = serializers.IntegerField(read_only=True)
    completion_percent  = serializers.IntegerField(read_only=True)
    delivery_sheet      = serializers.SerializerMethodField()

    def get_delivery_sheet(self, obj):
        sheet = getattr(obj, 'delivery_sheet', None)
        if not sheet:
            return None
        return DeliverySheetSerializer(sheet, context=self.context).data

    class Meta:
        model  = DailySchedule
        fields = [
            'id', 'driver', 'driver_name', 'truck', 'truck_plate',
            'date', 'status', 'manager_notes', 'created_by',
            'missed_stops_count', 'completion_percent',
            'stops', 'created_at', 'delivery_sheet',
            # ── Route optimization ──
            'route_optimized', 'route_optimized_at', 'route_suggestion',
            'driver_notified',
        ]
        read_only_fields = ['created_at', 'route_optimized_at']


class StopCreateNestedSerializer(serializers.ModelSerializer):
    """Used only when creating stops nested inside a schedule — 'schedule' FK is set by parent."""
    class Meta:
        model  = Stop
        fields = [
            'order', 'site_name', 'address',
            'latitude', 'longitude',
            'expected_arrival', 'notes',
            'allow_driver_reorder',
            # ── Stop-type / pickup-delivery linkage ──
            'stop_type', 'items',
            'contact_name', 'contact_phone', 'contact_email',
            'invoice_number', 'invoice_signed',
            'pickup_stop',
            # Which saved client the stop was built from. Without this in the
            # list DRF drops it silently, and a stop created through the
            # schedule form would lose the link the picker just made.
            'client',
        ]
        extra_kwargs = {
            'order':                {'required': False},
            'address':              {'required': False, 'allow_blank': True},
            'notes':                {'required': False, 'allow_blank': True},
            'latitude':             {'required': False, 'allow_null': True},
            'longitude':            {'required': False, 'allow_null': True},
            'expected_arrival':     {'required': False, 'allow_null': True},
            'allow_driver_reorder': {'required': False},
            'stop_type':            {'required': False},
            'items':                {'required': False, 'allow_blank': True},
            'contact_name':         {'required': False, 'allow_blank': True},
            'contact_phone':        {'required': False, 'allow_blank': True},
            'pickup_stop':          {'required': False, 'allow_null': True},
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
            'auto_closed',
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
    # Same safe-URL handling as Driver.photo etc. — see _abs_url at top.
    file = serializers.SerializerMethodField()

    class Meta:
        model  = Document
        fields = '__all__'
        read_only_fields = ['uploaded_at']

    def get_file(self, obj):
        return _abs_url(obj.file, self.context.get('request'))


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
    # Cloudinary URL safety — see _abs_url at top of file.
    pdf_file      = serializers.SerializerMethodField()

    class Meta:
        model  = Payslip
        fields = '__all__'
        read_only_fields = ['generated_at', 'updated_at']

    def get_pdf_file(self, obj):
        return _abs_url(obj.pdf_file, self.context.get('request'))


class PayslipSummarySerializer(serializers.ModelSerializer):
    driver_name = serializers.CharField(source='driver.full_name', read_only=True)

    class Meta:
        model  = Payslip
        fields = ['id', 'driver', 'driver_name', 'year', 'month',
                  'working_days', 'total_hours', 'gross_pay', 'net_pay', 'status',
                  # breakdown — needed by the phone payslip detail (it reuses list data)
                  'regular_hours', 'overtime_125_h', 'overtime_150_h', 'crane_hours',
                  'base_pay', 'overtime_125_pay', 'overtime_150_pay', 'crane_pay',
                  'travel_allowance', 'bonus',
                  'income_tax', 'national_ins', 'health_ins',
                  'pension_emp', 'study_fund_emp', 'other_deductions', 'total_deductions',
                  'pension_employer', 'study_fund_employer', 'severance_employer']


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
    # Raw fields ALSO go through _abs_url because DRF's default file URL
    # serialization is just as prone to double-prefix as the legacy
    # build_absolute_uri pattern was. Both `signature_image` and
    # `signature_image_url` return the same safe URL — kept for back-compat.
    signature_image     = serializers.SerializerMethodField()
    signature_image_url = serializers.SerializerMethodField()
    pdf_file            = serializers.SerializerMethodField()
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

    def get_signature_image(self, obj):
        return _abs_url(obj.signature_image, self.context.get('request'))

    def get_signature_image_url(self, obj):
        return _abs_url(obj.signature_image, self.context.get('request'))

    def get_pdf_file(self, obj):
        return _abs_url(obj.pdf_file, self.context.get('request'))

    def get_pdf_url(self, obj):
        return _abs_url(obj.pdf_file, self.context.get('request'))


# ──────────────────────────────────────────────
# INVOICING MODULE
# ──────────────────────────────────────────────
from .models import (Client, Invoice, InvoiceLine, FinanceDocument,
                     CatalogPackage, PackageOrder)


class CatalogPackageSerializer(serializers.ModelSerializer):
    """A catalogue row. `unit_display` saves every client translating the code."""
    unit_display = serializers.CharField(source='get_unit_display', read_only=True)

    class Meta:
        model = CatalogPackage
        fields = '__all__' 


class PackageOrderSerializer(serializers.ModelSerializer):
    """One package a client is owed on one day.

    Flattened rather than nested: the packages page, the stop form and the
    driver all want the package name and the client name beside the quantity,
    and nesting both objects would send the same rows over and over.
    """
    package_name   = serializers.CharField(source='package.name', read_only=True)
    package_code   = serializers.CharField(source='package.code', read_only=True)
    package_number = serializers.CharField(source='package.package_number',
                                           read_only=True)
    unit           = serializers.CharField(source='package.unit', read_only=True)
    unit_display   = serializers.CharField(source='package.get_unit_display',
                                           read_only=True)
    weight_kg      = serializers.DecimalField(source='package.weight_kg',
                                              max_digits=8, decimal_places=2,
                                              read_only=True)
    client_name    = serializers.CharField(source='client.name', read_only=True)
    client_label   = serializers.CharField(source='client.route_label',
                                           read_only=True)
    status_display = serializers.CharField(source='get_status_display',
                                           read_only=True)
    effective_price = serializers.DecimalField(max_digits=10, decimal_places=2,
                                               read_only=True)
    was_rescheduled = serializers.BooleanField(read_only=True)
    is_open         = serializers.BooleanField(read_only=True)

    class Meta:
        model = PackageOrder
        fields = ['id', 'client', 'client_name', 'client_label',
                  'package', 'package_name', 'package_code', 'package_number',
                  'unit', 'unit_display', 'weight_kg',
                  'quantity', 'delivery_date', 'status', 'status_display',
                  'stop', 'delivered_at', 'failure_reason',
                  'original_date', 'reschedule_count', 'was_rescheduled',
                  'is_open', 'price_override', 'effective_price', 'notes',
                  'created_at', 'updated_at']
        # Owned by the lifecycle, not by whoever is editing the form.
        read_only_fields = ['stop', 'delivered_at', 'original_date',
                            'reschedule_count', 'created_at', 'updated_at']


class ClientSerializer(serializers.ModelSerializer):
    """The saved customer, as both the billing entity and a destination.

    `route_label` / `route_address` are what a Stop would actually be built
    with, so a client picking a site address different from its billing
    address does not have to be resolved again by every caller.
    """
    route_label     = serializers.CharField(read_only=True)
    route_address   = serializers.CharField(read_only=True)
    has_coordinates = serializers.BooleanField(read_only=True)
    open_order_count = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = '__all__'

    def get_open_order_count(self, obj):
        """Packages this client is still owed, on any date."""
        return obj.package_orders.filter(
            status__in=PackageOrder.OPEN_STATUSES).count()


class ClientDetailSerializer(ClientSerializer):
    """Client plus the packages it is still owed.

    Used by the pickers, which want to show what is outstanding — not by the
    client form, which has no package section at all now that packages belong
    to a date rather than to the customer.
    """
    open_orders = serializers.SerializerMethodField()

    def get_open_orders(self, obj):
        rows = obj.package_orders.filter(
            status__in=PackageOrder.OPEN_STATUSES).select_related('package')
        return PackageOrderSerializer(rows, many=True).data


class InvoiceLineSerializer(serializers.ModelSerializer):
    line_total = serializers.SerializerMethodField()
    stop_site  = serializers.SerializerMethodField()

    class Meta:
        model = InvoiceLine
        fields = ['id', 'stop', 'stop_site', 'description',
                  'quantity', 'unit_price', 'line_total', 'order']

    def get_line_total(self, obj):
        return str(obj.line_total)

    def get_stop_site(self, obj):
        return obj.stop.site_name if obj.stop_id else None


class InvoiceSerializer(serializers.ModelSerializer):
    lines          = InvoiceLineSerializer(many=True, read_only=True)
    client_display = serializers.SerializerMethodField()
    pdf_url        = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = '__all__'

    def get_client_display(self, obj):
        return obj.client_name or (obj.client.name if obj.client_id else '')

    def get_pdf_url(self, obj):
        return _abs_url(obj.pdf_file, self.context.get('request'))


class FinanceDocumentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    year     = serializers.ReadOnlyField()
    month    = serializers.ReadOnlyField()

    class Meta:
        model = FinanceDocument
        fields = '__all__'

    def get_file_url(self, obj):
        return _abs_url(obj.file, self.context.get('request'))