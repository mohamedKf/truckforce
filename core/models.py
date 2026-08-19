from django.db import models
from django.contrib.auth.hashers import make_password, check_password
import math

# Cloudinary storage for non-image files (PDFs, generic documents).
# ImageField uses the default DEFAULT_FILE_STORAGE (Cloudinary's image storage);
# FileField for non-images needs RawMediaCloudinaryStorage so Cloudinary stores
# them as raw binaries instead of trying to treat them as images.
from cloudinary_storage.storage import RawMediaCloudinaryStorage


# ──────────────────────────────────────────────
# COMPANY SETTINGS
# ──────────────────────────────────────────────

class CompanySettings(models.Model):
    CRANE_ROUNDING_CHOICES = [
        ('full', 'Full Hour'),
        ('half', 'Half Hour (30 min)'),
        ('quarter', 'Quarter Hour (15 min)'),
        ('exact', 'Exact Time'),
    ]
    LANGUAGE_CHOICES = [
        ('ar', 'Arabic'),
        ('he', 'Hebrew'),
        ('ru', 'Russian'),
        ('en', 'English'),
    ]

    company_name         = models.CharField(max_length=200)
    company_logo         = models.ImageField(upload_to='company_logos/', blank=True, null=True)
    phone                = models.CharField(max_length=20, blank=True)
    email                = models.EmailField(blank=True)
    address              = models.TextField(blank=True)
    default_language     = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default='he')

    # ── Invoicing module (paid add-on) ────────────────────────────────
    invoicing_enabled        = models.BooleanField(default=False,
                                   help_text='Billing module on/off per client')
    company_tax_id           = models.CharField(max_length=20, blank=True,
                                   help_text='ח.פ / עוסק מורשה of the company — printed on invoices')
    scan_token               = models.CharField(max_length=64, blank=True,
                                   help_text='Upload-only token for the mobile scan page (QR)')
    # NOTE: Green Invoice API credentials are intentionally NOT model
    # fields — the settings serializer exposes __all__, and secrets don't
    # belong in DB rows or backups. They live in Railway environment
    # variables: GREEN_INVOICE_API_KEY / GREEN_INVOICE_API_SECRET, read
    # via decouple in config/settings.py when the provider is built.
    crane_rounding_rule  = models.CharField(max_length=10, choices=CRANE_ROUNDING_CHOICES, default='half')
    crane_price_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    work_start_hour      = models.TimeField(default='07:00')   # default shift start
    overtime_threshold   = models.DecimalField(max_digits=4, decimal_places=1, default=8.0)  # hours/day before overtime
    firebase_server_key  = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Company Settings'

    def __str__(self):
        return self.company_name


# ──────────────────────────────────────────────
# MANAGER
# ──────────────────────────────────────────────

class Manager(models.Model):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
    ]

    full_name    = models.CharField(max_length=200)
    username     = models.CharField(max_length=100, unique=True)
    password     = models.CharField(max_length=255)   # stored hashed
    email        = models.EmailField(blank=True)
    phone        = models.CharField(max_length=20, blank=True)
    role         = models.CharField(max_length=10, choices=ROLE_CHOICES, default='manager')
    is_active    = models.BooleanField(default=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    fcm_token    = models.TextField(blank=True)        # Firebase push token

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def __str__(self):
        return f"{self.full_name} ({self.role})"


# ──────────────────────────────────────────────
# DRIVER
# ──────────────────────────────────────────────

class Driver(models.Model):
    LICENSE_CHOICES = [
        ('B', 'B – up to 3.5t'),
        ('C', 'C – up to 12t'),
        ('C1', 'C1 – up to 7.5t'),
        ('CE', 'C+E – articulated / full trailer'),
        ('D', 'D – bus'),
    ]
    SALARY_TYPE_CHOICES = [
        ('daily', 'Daily'),
        ('monthly', 'Monthly'),
        ('hourly', 'Hourly'),
    ]
    GENDER_CHOICES = [
        ('male',   'זכר'),
        ('female', 'נקבה'),
    ]

    gender             = models.CharField(max_length=10, choices=GENDER_CHOICES, default='male')
    is_immigrant       = models.BooleanField(default=False, help_text='עולה חדש')
    immigrant_since    = models.DateField(null=True, blank=True, help_text='תאריך עלייה')
    extra_tax_points   = models.DecimalField(max_digits=4, decimal_places=2, default=0,
                                             help_text='Additional manual tax points')

    # ── Auth ──
    username     = models.CharField(max_length=100, unique=True)
    password     = models.CharField(max_length=255)

    # ── Personal info ──
    full_name    = models.CharField(max_length=200)
    # Solo-driver mode: this driver may create and edit their OWN
    # schedules and stops from the phone (one-man businesses).
    can_self_manage = models.BooleanField(default=False)
    id_number    = models.CharField(max_length=20, unique=True)   # Israeli ID
    email        = models.EmailField(blank=True)
    phone        = models.CharField(max_length=20, blank=True)
    address      = models.TextField(blank=True)
    birth_date   = models.DateField(null=True, blank=True)
    hire_date    = models.DateField(null=True, blank=True)
    photo        = models.ImageField(upload_to='driver_photos/', blank=True, null=True,
                   max_length=500,  # Cloudinary URLs exceed the default 100 chars
                   help_text='Permanent profile photo served via media URL')
    photo_b64    = models.TextField(blank=True, default='',
                   help_text='Temporary base64 upload — desktop downloads and clears this')
    is_active    = models.BooleanField(default=True)
    fcm_token    = models.TextField(blank=True)

    # ── License info ──
    license_type        = models.CharField(max_length=5, choices=LICENSE_CHOICES)
    license_number      = models.CharField(max_length=50, blank=True)
    license_expiry      = models.DateField(null=True, blank=True)
    max_tonnage         = models.DecimalField(max_digits=6, decimal_places=1, default=0)
    crane_certified     = models.BooleanField(default=False)
    crane_license_expiry = models.DateField(null=True, blank=True)

    # ── Salary info (for payroll engine) ──
    salary_type         = models.CharField(max_length=10, choices=SALARY_TYPE_CHOICES, default='daily')
    base_rate           = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overtime_rate       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    crane_hourly_rate   = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    travel_allowance    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    tax_credit_points   = models.DecimalField(max_digits=4, decimal_places=1, default=2.25)  # Israeli standard
    pension_percent     = models.DecimalField(max_digits=4, decimal_places=2, default=6.0)
    study_fund_percent  = models.DecimalField(max_digits=4, decimal_places=2, default=2.5)
    has_pension    = models.BooleanField(default=False,
                                         help_text='זכאי לפנסיה — אם False, אין ניכוי גם אם האחוז מוגדר')
    has_study_fund = models.BooleanField(default=False,
                                         help_text='זכאי לקרן השתלמות — אם False, אין ניכוי גם אם האחוז מוגדר')

    created_at = models.DateTimeField(auto_now_add=True)

    def set_password(self, raw_password):
        self.password = make_password(raw_password)

    def check_password(self, raw_password):
        return check_password(raw_password, self.password)

    def __str__(self):
        return f"{self.full_name} – {self.license_type}"


# ──────────────────────────────────────────────
# TRUCK
# ──────────────────────────────────────────────

class Truck(models.Model):
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('service', 'In Service'),
        ('retired', 'Retired'),
    ]

    brand              = models.CharField(max_length=100)
    model              = models.CharField(max_length=100)
    year               = models.IntegerField(null=True, blank=True)
    plate_number       = models.CharField(max_length=20, unique=True)
    # Physical dimensions for truck-aware navigation (meters). Sent to
    # the routing API as max_height/max_width so routes avoid low
    # bridges/tunnels and too-narrow roads. Null = route like a car.
    height_m           = models.DecimalField(max_digits=4, decimal_places=2,
                                             null=True, blank=True)
    width_m            = models.DecimalField(max_digits=4, decimal_places=2,
                                             null=True, blank=True)
    capacity_tons      = models.DecimalField(max_digits=6, decimal_places=1)
    has_crane          = models.BooleanField(default=False)
    status             = models.CharField(max_length=10, choices=STATUS_CHOICES, default='active')

    # ── Maintenance ──
    last_service_date  = models.DateField(null=True, blank=True)
    next_service_date  = models.DateField(null=True, blank=True)
    last_tire_change   = models.DateField(null=True, blank=True)
    next_tire_change   = models.DateField(null=True, blank=True)
    last_inspection    = models.DateField(null=True, blank=True)
    next_inspection    = models.DateField(null=True, blank=True)
    odometer_km        = models.IntegerField(default=0)
    notes              = models.TextField(blank=True)

    # ── Assignment ──
    assigned_driver    = models.ForeignKey(
        Driver, on_delete=models.SET_NULL,
        null=True, blank=True, related_name='trucks'
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.brand} {self.model} – {self.plate_number}"


# ──────────────────────────────────────────────
# DAILY SCHEDULE & STOPS
# ──────────────────────────────────────────────

class DailySchedule(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('completed', 'Completed'),
        ('partial', 'Partial – missed stops'),
    ]

    driver      = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='schedules')
    truck       = models.ForeignKey(Truck, on_delete=models.SET_NULL, null=True, blank=True)
    date        = models.DateField()
    status      = models.CharField(max_length=15, choices=STATUS_CHOICES, default='pending')
    manager_notes = models.TextField(blank=True)
    created_by  = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    # ── Route optimization ─────────────────────────────────
    route_optimized    = models.BooleanField(default=False)
    route_optimized_at = models.DateTimeField(null=True, blank=True)
    route_suggestion   = models.JSONField(null=True, blank=True,
                            help_text='Mapbox suggested stop order + durations + geometry')
    driver_notified    = models.BooleanField(default=False,
                            help_text='True once driver has been pushed the optimized route')

    class Meta:
        unique_together = ('driver', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.driver.full_name} – {self.date}"

    @property
    def missed_stops_count(self):
        return self.stops.filter(status='skipped').count()

    @property
    def completion_percent(self):
        total = self.stops.count()
        if total == 0:
            return 0
        done = self.stops.filter(status='done').count()
        return round((done / total) * 100)


class Stop(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('done', 'Done'),
        ('skipped', 'Skipped'),
    ]

    STOP_TYPE_CHOICES = [
        ('delivery', 'מסירה'),  # Default — deliver something
        ('pickup', 'איסוף'),  # Pick something up
        ('service', 'שירות'),  # Do work here (crane, repair, tow dropoff)
        ('both', 'איסוף + מסירה'),  # Pick up AND deliver at same location
        ('package_delivery', 'מסירת חבילות'),  # Multi-package delivery with load/check flow
    ]

    schedule        = models.ForeignKey(DailySchedule, on_delete=models.CASCADE, related_name='stops')
    order           = models.PositiveIntegerField()
    site_name       = models.CharField(max_length=200)
    address         = models.TextField()

    # Where this stop came from, when it was built by picking a saved client
    # rather than typed by hand. SET_NULL because deleting a customer from the
    # directory must not delete the history of what was delivered to them, and
    # the stop already carries its own copy of the name and address.
    #
    # The copy is deliberate: a stop is a record of where the truck went that
    # day. Reading the address through the client would silently rewrite last
    # month's routes the first time the office corrects a typo.
    client          = models.ForeignKey('Client', on_delete=models.SET_NULL,
                        null=True, blank=True, related_name='stops',
                        help_text='Saved client this stop was created from')
    latitude        = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude       = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes           = models.TextField(blank=True)
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    completed_at    = models.DateTimeField(null=True, blank=True)
    skip_reason     = models.TextField(blank=True)

    expected_arrival   = models.TimeField(null=True, blank=True,
                                          help_text='זמן הגעה מתוכנן')
    actual_arrival     = models.DateTimeField(null=True, blank=True,
                                              help_text='זמן הגעה בפועל (נרשם אוטומטית)')

    allow_driver_reorder = models.BooleanField(default=True,
        help_text='If True, driver can reorder this stop. Set False for time-locked stops.')

    stop_type = models.CharField(
        max_length=20,
        choices=STOP_TYPE_CHOICES,
        default='delivery',
        help_text='סוג העצירה'
    )

    # Item reference — what is being picked up or delivered
    items = models.TextField(
        blank=True,
        help_text='פריטים — חבילה #123, רכב 12-345-67, חומרים לאתר...'
    )

    # Contact at this stop
    contact_name = models.CharField(max_length=100, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True,
        help_text="Receiver email — delivery note / invoice can be emailed here")
    # Invoice for THIS stop (one per stop). The file may be a generated
    # PDF or a photo of a paper invoice turned into PDF.
    invoice_number = models.CharField(max_length=50, blank=True)
    invoice_file   = models.FileField(upload_to='invoices/', max_length=500,
        storage=RawMediaCloudinaryStorage(), null=True, blank=True)
    invoice_signed = models.BooleanField(default=False)

    # Pre-uploaded delivery-note PDF — the document the client will sign
    # later. Manager attaches it when creating/editing the stop in the
    # assignments page. Driver can view it from the app, and once we
    # build the signature-stamping flow it'll be flattened with the
    # signature and re-uploaded as a confirmation PDF (DeliveryConfirmation).
    delivery_note_pdf = models.FileField(
        upload_to='delivery_notes/',
        storage=RawMediaCloudinaryStorage(),
        blank=True, null=True,
        max_length=500,  # Cloudinary URLs exceed the default 100 chars
        help_text='פתק משלוח לחתימה ע״י הלקוח'
    )

    # Link delivery stops back to their pickup stop
    pickup_stop = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='delivery_stops',
        help_text='עצירת האיסוף שממנה נלקחו הפריטים'
    )

    # Driver confirmation note
    driver_note = models.TextField(
        blank=True,
        help_text='הערת נהג בסיום'
    )

    class Meta:
        ordering = ['order']

    def __str__(self):
        return f"#{self.order} {self.site_name} – {self.status}"

    @property
    def waze_link(self):
        if self.latitude and self.longitude:
            return f"https://waze.com/ul?ll={self.latitude},{self.longitude}&navigate=yes"
        return ""

    @property
    def google_maps_link(self):
        if self.latitude and self.longitude:
            return f"https://www.google.com/maps/dir/?api=1&destination={self.latitude},{self.longitude}"
        return ""

    @property
    def apple_maps_link(self):
        if self.latitude and self.longitude:
            return f"https://maps.apple.com/?daddr={self.latitude},{self.longitude}"
        return ""

    @property
    def is_late(self):
        if not (self.actual_arrival and self.expected_arrival):
            return False
        return self.actual_arrival.time() > self.expected_arrival


class DeliverySheet(models.Model):
    """The master delivery manifest for a day's assignment — the paper
    the driver photographs. ONE per schedule. Starts as the blank
    original; as each stop's receiver signs, the newest signed PDF
    REPLACES the previous signed copy, so we keep exactly two artifacts:
    the empty original and the running all-signatures copy."""
    schedule       = models.OneToOneField('DailySchedule',
                        on_delete=models.CASCADE, related_name='delivery_sheet')
    original_pdf   = models.FileField(upload_to='delivery_sheets/', max_length=500,
                        storage=RawMediaCloudinaryStorage(), null=True, blank=True,
                        help_text="Blank manifest — photo→PDF or office upload")
    signed_pdf     = models.FileField(upload_to='delivery_sheets/', max_length=500,
                        storage=RawMediaCloudinaryStorage(), null=True, blank=True,
                        help_text="Running copy — replaced after each signature")
    signature_count = models.PositiveIntegerField(default=0)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"DeliverySheet for schedule {self.schedule_id}"


class Package(models.Model):
    """A single item/line delivered to a stop. Many packages per stop;
    the truck loads them across stops and can fill before all fit —
    unloaded packages stay (is_loaded=False) and roll to a later trip."""
    STATUS_CHOICES = [
        ('pending',   'ממתין'),       # not yet loaded
        ('loaded',    'נטען'),         # on the truck
        ('delivered', 'נמסר'),         # handed over + checked at stop
        ('returned',  'הוחזר'),        # came back / refused
        ('left',      'נשאר במחסן'),   # didn't fit this trip
    ]

    stop            = models.ForeignKey('Stop', on_delete=models.CASCADE,
                        related_name='packages')
    # Identity (from the manifest)
    product_name    = models.CharField(max_length=200, blank=True)
    product_code    = models.CharField(max_length=60, blank=True)   # 71:… / 91:…
    tmsh_number     = models.CharField(max_length=60, blank=True)   # מס' תמ"ש
    barcode         = models.CharField(max_length=80, blank=True)
    # Quantities
    quantity_pallets = models.PositiveIntegerField(default=0)       # כמות משטחים
    quantity_units   = models.PositiveIntegerField(default=0)
    weight_kg        = models.DecimalField(max_digits=8, decimal_places=2,
                        null=True, blank=True)
    # Paperwork
    invoice_number  = models.CharField(max_length=50, blank=True)
    checker_name    = models.CharField(max_length=100, blank=True)  # בודק
    returns         = models.CharField(max_length=200, blank=True)  # החזרות
    # State — load and deliver are independent flags
    is_loaded       = models.BooleanField(default=False)
    is_delivered    = models.BooleanField(default=False)
    status          = models.CharField(max_length=12, choices=STATUS_CHOICES,
                        default='pending')
    loaded_at       = models.DateTimeField(null=True, blank=True)
    delivered_at    = models.DateTimeField(null=True, blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['stop__order', 'id']

    def __str__(self):
        return f"{self.product_name or self.product_code} → stop {self.stop_id}"


class StopPhoto(models.Model):
    """Multiple delivery/proof photos per stop (unlimited)."""
    stop        = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='photos')
    image       = models.ImageField(upload_to='delivery_photos/', max_length=500)  # long Cloudinary URLs
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['uploaded_at']

    def __str__(self):
        return f"Photo for stop #{self.stop_id} @ {self.uploaded_at:%Y-%m-%d %H:%M}"




# ──────────────────────────────────────────────
# DELIVERY CONFIRMATION (signature + PDF)
# ──────────────────────────────────────────────

class DeliveryConfirmation(models.Model):
    """Signed delivery confirmation per stop."""
    stop            = models.OneToOneField(Stop, on_delete=models.CASCADE,
                                           related_name='confirmation')
    signed_by_name  = models.CharField(max_length=200, help_text='Name of person who signed')
    signed_by_phone = models.CharField(max_length=30, blank=True,
                                        help_text='Phone to send WhatsApp confirmation to')
    signed_by_email = models.EmailField(blank=True,
                                         help_text='Email to send confirmation to')
    signature_image = models.ImageField(upload_to='signatures/',
                                         max_length=500,  # long Cloudinary URLs
                                         help_text='PNG of the hand-drawn signature')
    pdf_file        = models.FileField(upload_to='confirmation_pdfs/',
                                        storage=RawMediaCloudinaryStorage(),
                                        blank=True,
                                        max_length=500,  # long Cloudinary URLs
                                        help_text='Generated PDF confirmation')
    whatsapp_sent   = models.BooleanField(default=False)
    email_sent      = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Confirmation for stop #{self.stop_id} – {self.signed_by_name}"

# ──────────────────────────────
# STOP DOCUMENTS (multiple signable docs per stop)
# ──────────────────────────────

class StopDocument(models.Model):
    """One signable document/paper attached to a stop.

    A stop can carry several of these — a delivery note, a return slip,
    an invoice acknowledgment — each with its own file and its own
    signature. Generalizes the single per-stop DeliveryConfirmation; that
    older flow is kept alive for current app builds during rollout.
    """
    stop            = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='documents')
    title           = models.CharField(max_length=200, help_text='What this document is, e.g. "תעודת משלוח"')
    signer_name     = models.CharField(max_length=200, blank=True, help_text='Name of the person who signs')
    file            = models.FileField(upload_to='stop_documents/',
                                       storage=RawMediaCloudinaryStorage(),
                                       blank=True, null=True,
                                       max_length=500,  # long Cloudinary URLs
                                       help_text='The document file (PDF or image of the paper)')
    signature_image = models.ImageField(upload_to='stop_doc_signatures/',
                                        blank=True, null=True,
                                        max_length=500,  # long Cloudinary URLs
                                        help_text='PNG of the hand-drawn signature, once signed')
    signed_at       = models.DateTimeField(null=True, blank=True,
                                           help_text='Set when signed; empty = unsigned')
    order           = models.PositiveIntegerField(default=0)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['order', 'created_at']

    @property
    def signed(self):
        return self.signed_at is not None

    def __str__(self):
        state = 'signed' if self.signed else 'unsigned'
        return f"Doc '{self.title}' for stop #{self.stop_id} ({state})"


# ──────────────────────────────────────────────
# ATTENDANCE (CLOCK IN / OUT)
# ──────────────────────────────────────────────

class Attendance(models.Model):
    driver       = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='attendance')
    date         = models.DateField()
    clock_in     = models.DateTimeField(null=True, blank=True)
    clock_out    = models.DateTimeField(null=True, blank=True)
    clock_in_lat  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    clock_in_lng  = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    clock_out_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    clock_out_lng = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    notes        = models.TextField(blank=True)
    edited_by    = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True, blank=True)

    # Auto-close support: True if this shift was closed by the system because
    # the driver forgot to clock out (>14h). clock_out will equal clock_in
    # (zero-hour shift) and a pending AttendanceFixRequest will exist.
    auto_closed  = models.BooleanField(default=False, db_index=True,
                       help_text='True if system auto-closed this shift (driver forgot to clock out)')

    regular_hours  = models.DecimalField(max_digits=6, decimal_places=2, default=0,
                                          help_text='Hours at normal rate')
    overtime_125_h = models.DecimalField(max_digits=6, decimal_places=2, default=0,
                                          help_text='Hours at 125% (first 2 OT hours)')
    overtime_150_h = models.DecimalField(max_digits=6, decimal_places=2, default=0,
                                          help_text='Hours at 150% (beyond 10h/day)')

    class Meta:
        unique_together = ('driver', 'date')
        ordering = ['-date']

    def __str__(self):
        return f"{self.driver.full_name} – {self.date}"

    @property
    def total_hours(self):
        if self.clock_in and self.clock_out:
            delta = self.clock_out - self.clock_in
            return round(delta.total_seconds() / 3600, 2)
        return 0

    def calculate_hours(self, overtime_threshold: float = 8.0, ot_125_limit: float = 2.0):
        """
        Calculate and store regular, OT-125%, OT-150% hours.
        Call this after clock_out is set.

        Israeli law:
          First 8h → regular
          Next 2h (hours 9-10) → 125%
          Beyond 10h → 150%

        overtime_threshold: hours per day before OT kicks in (default 8)
        ot_125_limit: how many OT hours at 125% before switching to 150% (default 2)
        """
        total = self.total_hours
        if total <= 0:
            self.regular_hours = 0
            self.overtime_125_h = 0
            self.overtime_150_h = 0
            return

        if total <= overtime_threshold:
            self.regular_hours = total
            self.overtime_125_h = 0
            self.overtime_150_h = 0
        else:
            self.regular_hours = overtime_threshold
            overtime = total - overtime_threshold
            ot_125 = min(overtime, ot_125_limit)
            ot_150 = max(0, overtime - ot_125_limit)
            self.overtime_125_h = round(ot_125, 2)
            self.overtime_150_h = round(ot_150, 2)

        self.regular_hours = round(self.regular_hours, 2)




# ──────────────────────────────────────────────
# ATTENDANCE FIX REQUEST (driver asks manager to correct a day)
# ──────────────────────────────────────────────

class AttendanceFixRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    driver       = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='fix_requests')
    date         = models.DateField(help_text='Date driver wants corrected')
    # What driver requests (nullable — only fill the fields they want changed)
    requested_clock_in  = models.DateTimeField(null=True, blank=True)
    requested_clock_out = models.DateTimeField(null=True, blank=True)
    reason       = models.TextField(help_text='Why the correction is needed')
    # Decision fields
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    manager_note = models.TextField(blank=True, help_text='Manager reply/note')
    decided_by   = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True, blank=True, related_name='decided_fix_requests')
    decided_at   = models.DateTimeField(null=True, blank=True)
    # Timestamps
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.driver.full_name} – {self.date} ({self.status})"


# ──────────────────────────────────────────────
# CRANE SESSION
# ──────────────────────────────────────────────

class CraneSession(models.Model):
    driver        = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='crane_sessions')
    truck         = models.ForeignKey(Truck, on_delete=models.SET_NULL, null=True, blank=True)
    schedule      = models.ForeignKey(DailySchedule, on_delete=models.SET_NULL, null=True, blank=True)
    stop          = models.ForeignKey(Stop, on_delete=models.SET_NULL, null=True, blank=True)
    date          = models.DateField()
    arrival_time  = models.DateTimeField(null=True, blank=True)
    work_start    = models.DateTimeField(null=True, blank=True)
    work_end      = models.DateTimeField(null=True, blank=True)
    raw_minutes   = models.IntegerField(default=0)       # exact minutes recorded
    billed_hours  = models.DecimalField(max_digits=6, decimal_places=2, default=0)  # after rounding
    price_per_hour = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_charge  = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes         = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-work_start']

    def calculate_billed_hours(self, rounding_rule='half'):
        """Apply rounding rule to raw_minutes and return billed hours."""
        if self.raw_minutes == 0:
            return 0
        total_minutes = self.raw_minutes
        if rounding_rule == 'full':
            hours = math.ceil(total_minutes / 60)
        elif rounding_rule == 'half':
            half_intervals = math.ceil(total_minutes / 30)
            hours = half_intervals * 0.5
        elif rounding_rule == 'quarter':
            quarter_intervals = math.ceil(total_minutes / 15)
            hours = quarter_intervals * 0.25
        else:  # exact
            hours = round(total_minutes / 60, 4)
        return hours

    def save_with_billing(self, rounding_rule='half'):
        if self.work_start and self.work_end:
            delta = self.work_end - self.work_start
            self.raw_minutes = int(delta.total_seconds() / 60)
        self.billed_hours = self.calculate_billed_hours(rounding_rule)
        self.total_charge = float(self.billed_hours) * float(self.price_per_hour)
        self.save()

    def __str__(self):
        return f"Crane – {self.driver.full_name} – {self.date} – {self.billed_hours}h"


# ──────────────────────────────────────────────
# PAYROLL
# ──────────────────────────────────────────────

class Payroll(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('approved', 'Approved'),
        ('paid', 'Paid'),
    ]

    driver           = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='payrolls')
    month            = models.IntegerField()   # 1–12
    year             = models.IntegerField()
    status           = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')

    # ── Work data ──
    working_days     = models.IntegerField(default=0)
    total_hours      = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overtime_hours   = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    crane_hours      = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # ── Earnings ──
    base_pay         = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overtime_pay     = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    crane_pay        = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    travel_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gross_pay        = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # ── Israeli deductions ──
    income_tax       = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bituach_leumi    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    health_insurance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pension_employee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    study_fund_employee = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # ── Employer contributions ──
    pension_employer    = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    study_fund_employer = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    severance_fund      = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    net_pay          = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    notes            = models.TextField(blank=True)
    generated_by     = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True, blank=True)
    generated_at     = models.DateTimeField(auto_now_add=True)
    paid_at          = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('driver', 'month', 'year')
        ordering = ['-year', '-month']

    def __str__(self):
        return f"Payroll – {self.driver.full_name} – {self.month}/{self.year}"


# ──────────────────────────────────────────────
# NOTIFICATION LOG
# ──────────────────────────────────────────────

class NotificationLog(models.Model):
    TYPE_CHOICES = [
        ('stop_skipped', 'Stop Skipped'),
        ('day_summary', 'End of Day Summary'),
        ('clock_reminder', 'Clock Out Reminder'),
        ('payslip_ready', 'Payslip Ready'),
        ('license_expiry', 'License Expiry Warning'),
        ('truck_service', 'Truck Service Due'),
        ('schedule_changed', 'Schedule Changed'),
    ]

    recipient_manager = models.ForeignKey(Manager, on_delete=models.CASCADE, null=True, blank=True)
    recipient_driver  = models.ForeignKey(Driver, on_delete=models.CASCADE, null=True, blank=True)
    notification_type = models.CharField(max_length=30, choices=TYPE_CHOICES)
    title             = models.CharField(max_length=200)
    body              = models.TextField()
    data              = models.JSONField(default=dict, blank=True)  # extra payload
    sent              = models.BooleanField(default=False)
    sent_at           = models.DateTimeField(null=True, blank=True)
    read              = models.BooleanField(default=False)
    created_at        = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.notification_type} – {self.created_at.strftime('%Y-%m-%d %H:%M')}"


# ──────────────────────────────────────────────
# DOCUMENT STORAGE
# ──────────────────────────────────────────────

class Document(models.Model):
    DOC_TYPE_CHOICES = [
        ('payslip', 'Payslip'),
        ('contract', 'Contract'),
        ('license_copy', 'License Copy'),
        ('medical', 'Medical Certificate'),
        ('truck_doc', 'Truck Document'),
        ('other', 'Other'),
    ]

    driver      = models.ForeignKey(Driver, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    truck       = models.ForeignKey(Truck, on_delete=models.CASCADE, null=True, blank=True, related_name='documents')
    doc_type    = models.CharField(max_length=20, choices=DOC_TYPE_CHOICES)
    title       = models.CharField(max_length=200)
    file        = models.FileField(upload_to='documents/', max_length=500,
                                    storage=RawMediaCloudinaryStorage())
    notes       = models.TextField(blank=True)
    uploaded_by = models.ForeignKey(Manager, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.doc_type} – {self.title}"


class DriverLocation(models.Model):
    """Tracks driver GPS location while clocked in."""
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='locations')
    latitude = models.DecimalField(max_digits=10, decimal_places=7)
    longitude = models.DecimalField(max_digits=10, decimal_places=7)
    speed = models.FloatField(null=True, blank=True)  # km/h optional
    heading = models.FloatField(null=True, blank=True)  # direction 0-360 optional
    accuracy = models.FloatField(null=True, blank=True)  # meters optional
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['driver', '-timestamp']),
            models.Index(fields=['-timestamp']),
        ]

    def __str__(self):
        return f"{self.driver.full_name} @ {self.timestamp:%Y-%m-%d %H:%M}"


class Accountant(models.Model):
    """רואה חשבון — company's accountant contact info."""
    name = models.CharField(max_length=200, help_text='שם רואה החשבון')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    notes = models.TextField(blank=True)
    is_primary = models.BooleanField(default=True,
                                     help_text='Primary accountant (used for sending reports)')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-is_primary', 'name']
        verbose_name = 'Accountant'

    def __str__(self):
        return self.name


# ── NEW MODEL — PayrollSendLog ───────────────────────────────────

class PayrollSendLog(models.Model):
    """Tracks payslip/salary report delivery to drivers and accountant."""
    CHANNEL_CHOICES = [
        ('whatsapp', 'WhatsApp'),
        ('sms', 'SMS'),
        ('email', 'Email'),
    ]
    RECIPIENT_TYPE_CHOICES = [
        ('driver', 'Driver'),
        ('accountant', 'Accountant'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('sent', 'Sent'),
        ('failed', 'Failed'),
    ]

    recipient_type = models.CharField(max_length=15, choices=RECIPIENT_TYPE_CHOICES)
    recipient_name = models.CharField(max_length=200)
    recipient_phone = models.CharField(max_length=20, blank=True)
    recipient_email = models.EmailField(blank=True)

    payroll = models.ForeignKey('Payroll', on_delete=models.SET_NULL, null=True, blank=True)
    year = models.IntegerField(null=True, blank=True)
    month = models.IntegerField(null=True, blank=True)

    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    error_message = models.TextField(blank=True)

    subject = models.CharField(max_length=300, blank=True)
    body_preview = models.TextField(blank=True)
    attachment_name = models.CharField(max_length=300, blank=True)

    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']
        indexes = [
            models.Index(fields=['-sent_at']),
            models.Index(fields=['recipient_type', '-sent_at']),
        ]

    def __str__(self):
        return f"{self.recipient_type}:{self.recipient_name} via {self.channel} → {self.status}"


class Payslip(models.Model):
    """Monthly payslip for a driver with full breakdown."""
    STATUS_CHOICES = [
        ('draft', 'טיוטה'),
        ('approved', 'מאושר'),
        ('paid', 'שולם'),
    ]

    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='payslips')
    year = models.IntegerField()
    month = models.IntegerField()  # 1-12
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='draft')

    # Work summary
    working_days = models.IntegerField(default=0)
    total_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    regular_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overtime_125_h = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    overtime_150_h = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    crane_hours = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # Earnings
    base_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overtime_125_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    overtime_150_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    crane_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    travel_allowance = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    bonus = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    gross_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Deductions (employee side)
    tax_points_used = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    income_tax = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    national_ins = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    health_ins = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    pension_emp = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    study_fund_emp = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    other_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Employer contributions
    pension_employer = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    study_fund_employer = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    severance_employer = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Final
    net_pay = models.DecimalField(max_digits=10, decimal_places=2, default=0)

    # Metadata
    notes = models.TextField(blank=True)
    generated_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    pdf_file = models.FileField(upload_to='payslips/',
                                 storage=RawMediaCloudinaryStorage(),
                                 blank=True, null=True)

    class Meta:
        ordering = ['-year', '-month']
        unique_together = [('driver', 'year', 'month')]
        indexes = [
            models.Index(fields=['-year', '-month']),
            models.Index(fields=['driver', '-year', '-month']),
        ]

    def __str__(self):
        return f"{self.driver.full_name} — {self.month:02d}/{self.year}"


class PayrollConfig(models.Model):
    """
    Editable tax configuration. Only ONE row should exist (singleton).
    Values default to 2026 rates — can be updated as laws change.
    """
    # ── Tax brackets (monthly, ILS) ──
    bracket_1_limit = models.DecimalField(max_digits=10, decimal_places=2, default=7010,
                                          help_text='Bracket 1 upper limit (monthly)')
    bracket_1_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.10)
    bracket_2_limit = models.DecimalField(max_digits=10, decimal_places=2, default=10060)
    bracket_2_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.14)
    bracket_3_limit = models.DecimalField(max_digits=10, decimal_places=2, default=19000)
    bracket_3_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.20)
    bracket_4_limit = models.DecimalField(max_digits=10, decimal_places=2, default=25100)
    bracket_4_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.31)
    bracket_5_limit = models.DecimalField(max_digits=10, decimal_places=2, default=46690)
    bracket_5_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.35)
    bracket_6_limit = models.DecimalField(max_digits=10, decimal_places=2, default=60130)
    bracket_6_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.47)
    bracket_7_rate = models.DecimalField(max_digits=5, decimal_places=4, default=0.50,
                                         help_text='Above bracket 6 — top rate')

    # ── National Insurance + Health ──
    insurance_low_limit = models.DecimalField(max_digits=10, decimal_places=2, default=7703)
    insurance_high_limit = models.DecimalField(max_digits=10, decimal_places=2, default=51910)
    national_insurance_low = models.DecimalField(max_digits=6, decimal_places=4, default=0.0104)
    national_insurance_high = models.DecimalField(max_digits=6, decimal_places=4, default=0.07)
    health_insurance_low = models.DecimalField(max_digits=6, decimal_places=4, default=0.0323)
    health_insurance_high = models.DecimalField(max_digits=6, decimal_places=4, default=0.0517)

    # ── Tax points ──
    tax_point_value = models.DecimalField(max_digits=8, decimal_places=2, default=242,
                                          help_text='Monthly ILS value per tax point (2026)')
    base_tax_points = models.DecimalField(max_digits=4, decimal_places=2, default=2.25,
                                          help_text='Default points for Israeli resident')

    # ── Minimum wage ──
    minimum_wage_monthly = models.DecimalField(max_digits=10, decimal_places=2, default=5880.02)
    minimum_wage_hourly = models.DecimalField(max_digits=6, decimal_places=2, default=32.30)

    # ── Overtime multipliers ──
    overtime_125_limit = models.DecimalField(max_digits=4, decimal_places=1, default=2,
                                             help_text='First N OT hours at 125%')
    overtime_125_rate = models.DecimalField(max_digits=4, decimal_places=2, default=1.25)
    overtime_150_rate = models.DecimalField(max_digits=4, decimal_places=2, default=1.50)

    # ── Metadata ──
    effective_year = models.IntegerField(default=2026)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = 'Payroll Configuration'

    def __str__(self):
        return f"PayrollConfig ({self.effective_year})"

    @classmethod
    def get_config(cls):
        """Get or create the singleton config row."""
        cfg, _ = cls.objects.get_or_create(pk=1)
        return cfg

    @property
    def tax_brackets(self):
        """Return list of (limit, rate) tuples for tax calculation."""
        return [
            (float(self.bracket_1_limit), float(self.bracket_1_rate)),
            (float(self.bracket_2_limit), float(self.bracket_2_rate)),
            (float(self.bracket_3_limit), float(self.bracket_3_rate)),
            (float(self.bracket_4_limit), float(self.bracket_4_rate)),
            (float(self.bracket_5_limit), float(self.bracket_5_rate)),
            (float(self.bracket_6_limit), float(self.bracket_6_rate)),
            (float('inf'), float(self.bracket_7_rate)),
        ]


class ChildOfDriver(models.Model):
    """Children of a driver — used for tax point calculation."""
    driver = models.ForeignKey(Driver, on_delete=models.CASCADE, related_name='children')
    full_name = models.CharField(max_length=200, blank=True)
    birth_date = models.DateField()
    has_disability = models.BooleanField(default=False, help_text='ילד בעל מוגבלות')
    receives_allowance = models.BooleanField(default=False,
                                             help_text='מקבל קצבה מהורה גרוש (מפחית נקודת זיכוי)')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-birth_date']

    def __str__(self):
        return f"{self.full_name or 'child'} of {self.driver.full_name}"


# ── Public tracking links (client-facing live truck location) ────

import secrets

def _new_tracking_token():
    """Generates a fresh token per row — must be a callable, not a value."""
    return secrets.token_urlsafe(24)


class TrackingLink(models.Model):
    """
    Public tracking link — share with client to show live truck location.
    No authentication required to view.
    """
    token       = models.CharField(max_length=32, unique=True, default=_new_tracking_token)
    driver      = models.ForeignKey('Driver', on_delete=models.CASCADE, related_name='tracking_links')
    created_by  = models.ForeignKey('Manager', on_delete=models.CASCADE,
                                    null=True, blank=True,
                                    related_name='tracking_links')
    # When the DRIVER shares his own location with the client (from the
    # phone), created_by stays empty and this flag marks the origin.
    created_by_driver = models.BooleanField(default=False)
    target_stop = models.ForeignKey(
        'Stop',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='tracking_links',
        help_text='Optional — pin tracking page to a specific stop (ETA, client notes target).'
    )
    label       = models.CharField(max_length=100, blank=True, help_text="e.g. 'משלוח לאתר נתניה'")
    is_active   = models.BooleanField(default=True)
    expires_at  = models.DateTimeField(null=True, blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Track:{self.token[:8]} → {self.driver.full_name}"

    def is_valid(self):
        from django.utils import timezone
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        return True

    @classmethod
    def generate(cls, driver, manager, label='', hours=24, target_stop=None):
        from django.utils import timezone
        from datetime import timedelta
        return cls.objects.create(
            token       = _new_tracking_token(),
            driver      = driver,
            created_by  = manager,
            target_stop = target_stop,
            label       = label,
            expires_at  = timezone.now() + timedelta(hours=hours),
        )


# ── Stop Tasks (notes / photos / phones attached to stops) ────


class StopTask(models.Model):
    """
    Notes and attachments for a stop — visible to driver on phone.
    Can come from manager OR from client via tracking link.
    """
    SOURCE_CHOICES = [
        ('manager', 'Manager'),
        ('client',  'Client'),
    ]

    stop       = models.ForeignKey('Stop', on_delete=models.CASCADE, related_name='tasks')
    source     = models.CharField(max_length=10, choices=SOURCE_CHOICES, default='manager')
    note       = models.TextField(blank=True)
    photo      = models.ImageField(upload_to='stop_task_photos/', null=True, blank=True,
                                   max_length=500)  # long Cloudinary URLs
    phone      = models.CharField(max_length=20, blank=True, help_text='Contact phone for driver')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"Task for Stop #{self.stop.order} ({self.source})"


# ──────────────────────────────────────────────────────────────────────
# INVOICING MODULE
# ──────────────────────────────────────────────────────────────────────

class Client(models.Model):
    """A business customer of the hauling company.

    Started life as the billing entity for the invoicing add-on, and is now
    also the dispatch directory: a saved customer a stop can be built from in
    one pick, instead of retyping the site name, address and contact every
    time. The billing fields stay where they were so invoicing is unaffected;
    everything under "Delivery identity" below is what makes a Client usable
    as a destination.

    `address` remains the BILLING address (what goes on an invoice).
    `site_address` is where the truck actually goes — often a yard or a
    building site at a different place entirely.
    """
    name            = models.CharField(max_length=200)
    tax_id          = models.CharField(max_length=20, blank=True,
                                       help_text='ח.פ / עוסק מורשה')
    address         = models.TextField(blank=True,
                                       help_text='Billing address — printed on invoices')
    contact_name    = models.CharField(max_length=120, blank=True)
    phone           = models.CharField(max_length=30, blank=True)
    email           = models.EmailField(blank=True)
    payment_terms   = models.CharField(max_length=50, blank=True,
                                       help_text='e.g. שוטף+30')
    notes           = models.TextField(blank=True)
    green_invoice_id = models.CharField(max_length=64, blank=True,
                                        help_text='Client id on Green Invoice')
    is_active       = models.BooleanField(default=True)
    created_at      = models.DateTimeField(auto_now_add=True)

    # ── Delivery identity ─────────────────────────────────
    # What a Stop is built from. site_name is the label the driver sees on the
    # route; it defaults to the company name when left blank.
    site_name       = models.CharField(max_length=200, blank=True,
                        help_text='Label shown on the route. Blank → the client name')
    site_address    = models.TextField(blank=True,
                        help_text='Where the truck goes, when that is not the billing address')
    latitude        = models.DecimalField(max_digits=9, decimal_places=6,
                        null=True, blank=True)
    longitude       = models.DecimalField(max_digits=9, decimal_places=6,
                        null=True, blank=True)
    # Either input the office pasted. Kept verbatim so a failed geocode can be
    # retried later without asking the office to find the link again.
    location_url    = models.TextField(blank=True,
                        help_text='Google Maps / Waze share link, parsed into coordinates')
    google_place_id = models.CharField(max_length=200, blank=True,
                        help_text='Google Places id, when picked from the search box')

    # Contact at the SITE, when it differs from the billing contact above.
    site_contact_name  = models.CharField(max_length=100, blank=True)
    site_contact_phone = models.CharField(max_length=30, blank=True)

    delivery_notes  = models.TextField(blank=True,
                        help_text='Standing instructions — gate code, which entrance, call ahead')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    # ── Helpers used when a Stop is built from this client ──

    @property
    def route_label(self) -> str:
        return self.site_name or self.name

    @property
    def route_address(self) -> str:
        return self.site_address or self.address

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class Invoice(models.Model):
    """A billing document. 'proforma' (חשבון עסקה) is generated and branded
    by TruckForce itself — it is not a tax document, so we own its design.
    'tax_invoice' (חשבונית מס) and 'receipt' (קבלה) are legal documents
    issued through Green Invoice; we mirror their number, allocation
    number, and PDF."""
    TYPE_CHOICES = [
        ('proforma',    'חשבון עסקה'),
        ('tax_invoice', 'חשבונית מס'),
        ('receipt',     'קבלה'),
    ]
    STATUS_CHOICES = [
        ('draft',     'Draft'),
        ('issued',    'Issued'),
        ('sent',      'Sent'),
        ('paid',      'Paid'),
        ('cancelled', 'Cancelled'),
    ]

    client          = models.ForeignKey(Client, on_delete=models.PROTECT,
                                        related_name='invoices')
    invoice_type    = models.CharField(max_length=12, choices=TYPE_CHOICES,
                                       default='proforma')
    status          = models.CharField(max_length=10, choices=STATUS_CHOICES,
                                       default='draft')
    number          = models.PositiveIntegerField(null=True, blank=True,
                          help_text='Internal sequence for proformas; '
                                    'mirrors the provider number for tax docs')
    issue_date      = models.DateField(null=True, blank=True)

    # Snapshot of the client at issue time — an issued document must never
    # change retroactively, even if the client record is edited later.
    client_name     = models.CharField(max_length=200, blank=True)
    client_tax_id   = models.CharField(max_length=20, blank=True)
    client_address  = models.TextField(blank=True)

    # Money — Decimal end to end. Line prices are BEFORE VAT (Israeli B2B
    # convention); totals are computed from the lines at issue time.
    subtotal        = models.DecimalField(max_digits=12, decimal_places=2,
                                          default=0)
    vat_rate        = models.DecimalField(max_digits=5, decimal_places=2,
                                          default=18)
    vat_exempt      = models.BooleanField(default=False)
    vat_amount      = models.DecimalField(max_digits=12, decimal_places=2,
                                          default=0)
    total           = models.DecimalField(max_digits=12, decimal_places=2,
                                          default=0)

    # Legal/provider layer (Green Invoice)
    provider        = models.CharField(max_length=20, default='truckforce',
                          help_text="'truckforce' for proformas, "
                                    "'greeninvoice' for legal docs")
    provider_doc_id = models.CharField(max_length=64, blank=True)
    allocation_number = models.CharField(max_length=30, blank=True,
                                         help_text='מספר הקצאה')

    pdf_file        = models.FileField(upload_to='invoices/',
                                       storage=RawMediaCloudinaryStorage(),
                                       max_length=500, blank=True)

    payment_date    = models.DateField(null=True, blank=True)
    payment_method  = models.CharField(max_length=50, blank=True)
    notes           = models.TextField(blank=True)
    created_at      = models.DateTimeField(auto_now_add=True)
    updated_at      = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.get_invoice_type_display()} #{self.number or '—'} — {self.client_name or self.client.name}"

    def recalc_totals(self):
        """Recompute subtotal / VAT / total from the lines. Call before
        issuing. Stays in Decimal the whole way."""
        from decimal import Decimal
        sub = sum((l.line_total for l in self.lines.all()), Decimal('0'))
        self.subtotal = sub
        rate = Decimal('0') if self.vat_exempt else (self.vat_rate or Decimal('0'))
        self.vat_amount = (sub * rate / Decimal('100')).quantize(Decimal('0.01'))
        self.total = sub + self.vat_amount


class InvoiceLine(models.Model):
    """One billed line. The manager types the description and the amount —
    no automatic pricing. Optionally linked to a delivered Stop, which puts
    the signed delivery note one click away from the invoice line."""
    invoice     = models.ForeignKey(Invoice, on_delete=models.CASCADE,
                                    related_name='lines')
    stop        = models.ForeignKey('Stop', on_delete=models.SET_NULL,
                                    null=True, blank=True,
                                    related_name='invoice_lines')
    description = models.CharField(max_length=300)
    quantity    = models.DecimalField(max_digits=10, decimal_places=2,
                                      default=1)
    unit_price  = models.DecimalField(max_digits=12, decimal_places=2,
                                      help_text='Before VAT')
    order       = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    @property
    def line_total(self):
        from decimal import Decimal
        return ((self.quantity or Decimal('0')) *
                (self.unit_price or Decimal('0'))).quantize(Decimal('0.01'))

    def __str__(self):
        return f"{self.description} — {self.line_total}"


class FinanceDocument(models.Model):
    """Archive of financial documents — income invoices the business issued
    elsewhere, and expense documents it received (fuel, parts, suppliers).
    Not bookkeeping: a tidy, month-organized archive the accountant can be
    handed wholesale. Files live on Cloudinary and mirror to the office PC.
    """
    KIND_CHOICES = [
        ('income',  'הכנסה'),
        ('expense', 'הוצאה'),
    ]

    kind          = models.CharField(max_length=8, choices=KIND_CHOICES)
    doc_date      = models.DateField(help_text='Drives the year/month archive structure')
    client        = models.ForeignKey(Client, on_delete=models.SET_NULL,
                                      null=True, blank=True,
                                      related_name='finance_documents')
    vendor_name   = models.CharField(max_length=200, blank=True,
                                     help_text='Issuer (for expenses) or payer (for income)')
    vendor_tax_id = models.CharField(max_length=20, blank=True,
                                     help_text='ח.פ / ת.ז on the document')
    description   = models.CharField(max_length=300, blank=True)
    amount        = models.DecimalField(max_digits=12, decimal_places=2,
                                        null=True, blank=True,
                                        help_text='Optional — typed, not calculated')
    file          = models.FileField(upload_to='finance_docs/',
                                     storage=RawMediaCloudinaryStorage(),
                                     max_length=500)
    original_filename = models.CharField(max_length=255, blank=True)
    notes         = models.TextField(blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-doc_date', '-created_at']
        indexes = [models.Index(fields=['kind', 'doc_date'])]

    @property
    def year(self):
        return self.doc_date.year if self.doc_date else None

    @property
    def month(self):
        return self.doc_date.month if self.doc_date else None

    def __str__(self):
        return f"{self.get_kind_display()} {self.doc_date} — {self.vendor_name or self.description or self.pk}"

# ──────────────────────────────────────────────
# PACKAGE CATALOGUE
# ──────────────────────────────────────────────
# `Package` (further up) is a line on ONE stop — created when that delivery is
# planned and thrown away with it. This is the other thing: a standing list of
# what the company actually ships, so the office picks "3 × pallet of tiles"
# instead of retyping the code, the weight and the description every time.
#
# The two are deliberately separate. A catalogue row is a template; copying it
# onto a stop produces a Package that the driver then loads, delivers or
# returns. Editing the catalogue afterwards must never rewrite the history of
# what was already delivered, which is exactly what sharing one row would do.

class CatalogPackage(models.Model):
    """A product/package the company ships, kept once and reused."""

    UNIT_CHOICES = [
        ('pallet', 'משטח'),
        ('unit',   'יחידה'),
        ('box',    'קרטון'),
        ('bag',    'שק'),
        ('ton',    'טון'),
        ('m3',     'מ״ק'),
    ]

    package_number = models.CharField(max_length=60, blank=True, db_index=True,
                        help_text='מספר חבילה — the number the office refers to it by')
    code           = models.CharField(max_length=60, blank=True, db_index=True,
                        help_text='מק״ט / product code')
    name           = models.CharField(max_length=200)
    description    = models.TextField(blank=True)
    barcode        = models.CharField(max_length=80, blank=True)

    unit           = models.CharField(max_length=10, choices=UNIT_CHOICES,
                                      default='pallet')
    weight_kg      = models.DecimalField(max_digits=8, decimal_places=2,
                                         null=True, blank=True)
    length_cm      = models.DecimalField(max_digits=7, decimal_places=1,
                                         null=True, blank=True)
    width_cm       = models.DecimalField(max_digits=7, decimal_places=1,
                                         null=True, blank=True)
    height_cm      = models.DecimalField(max_digits=7, decimal_places=1,
                                         null=True, blank=True)

    # What a client is charged per unit, when the invoicing add-on is on.
    price_per_unit = models.DecimalField(max_digits=10, decimal_places=2,
                                         default=0)

    notes          = models.TextField(blank=True)
    is_active      = models.BooleanField(default=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['code', 'package_number'])]

    def __str__(self):
        label = self.code or self.package_number
        return f"{self.name} ({label})" if label else self.name

    def as_stop_package_kwargs(self, quantity: int = None) -> dict:
        """The fields to copy onto a per-stop Package.

        Only the identity travels; state (loaded, delivered) belongs to the
        stop and always starts fresh.
        """
        return {
            'product_name': self.name,
            'product_code': self.code,
            'barcode':      self.barcode,
            'quantity_pallets': quantity if self.unit == 'pallet' else 0,
            'quantity_units':   quantity if self.unit != 'pallet' else 0,
            'weight_kg':    self.weight_kg,
        }


class PackageOrder(models.Model):
    """A package a client wants on a particular day.

    This is deliberately NOT a standing list. What a customer takes changes
    from one delivery to the next, so "client X gets 4 pallets of tiles" is
    only ever true of a date. An order is created for a day, picked up by the
    stop built for that client on that day, and closed when the driver
    finishes — or flagged when they do not.

    The lifecycle is the point:

        pending      created; no stop for it yet
        scheduled    attached to a stop on its delivery_date
        delivered    that stop was completed
        undelivered  the stop was skipped or the day ended without it —
                     this is the flag the office works from
        cancelled    called off

    Rescheduling moves the row rather than making a new one: the date changes,
    the status goes back to pending and the stop link is cleared, while
    original_date and reschedule_count keep the history. One row per thing the
    customer is owed means the office can never double-deliver by losing track
    of which copy was the live one.
    """

    STATUS_CHOICES = [
        ('pending',     'ממתין'),
        ('scheduled',   'משובץ'),
        ('delivered',   'נמסר'),
        ('undelivered', 'לא נמסר'),
        ('cancelled',   'בוטל'),
    ]
    # Statuses that still owe the customer something.
    OPEN_STATUSES = ('pending', 'scheduled', 'undelivered')

    client        = models.ForeignKey(Client, on_delete=models.CASCADE,
                      related_name='package_orders')
    package       = models.ForeignKey(CatalogPackage, on_delete=models.PROTECT,
                      related_name='orders',
                      help_text='PROTECT: a catalogue row with history is '
                                'deactivated, never deleted out from under it')
    quantity      = models.PositiveIntegerField(default=1)

    delivery_date = models.DateField(db_index=True,
                      help_text='The day this is to be delivered')
    status        = models.CharField(max_length=12, choices=STATUS_CHOICES,
                      default='pending', db_index=True)

    # The stop that picked this up, once one exists for the client and day.
    stop          = models.ForeignKey('Stop', on_delete=models.SET_NULL,
                      null=True, blank=True, related_name='package_orders')

    delivered_at  = models.DateTimeField(null=True, blank=True)
    # Why it did not go out, when it did not.
    failure_reason = models.TextField(blank=True)

    # Rescheduling trail.
    original_date    = models.DateField(null=True, blank=True,
                         help_text='The first date asked for, kept across moves')
    reschedule_count = models.PositiveIntegerField(default=0)

    price_override = models.DecimalField(max_digits=10, decimal_places=2,
                                         null=True, blank=True)
    notes          = models.TextField(blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['delivery_date', 'client__name', 'package__name']
        indexes = [
            # The two questions actually asked: what does this client need on
            # this day, and what is still outstanding.
            models.Index(fields=['client', 'delivery_date']),
            models.Index(fields=['status', 'delivery_date']),
        ]

    def __str__(self):
        return (f"{self.client.name} — {self.package.name} ×{self.quantity} "
                f"on {self.delivery_date} ({self.status})")

    def save(self, *args, **kwargs):
        # Remember where it started, so a row moved three times still shows
        # the day it was first promised for.
        if self.original_date is None:
            self.original_date = self.delivery_date
        super().save(*args, **kwargs)

    @property
    def is_open(self) -> bool:
        return self.status in self.OPEN_STATUSES

    @property
    def was_rescheduled(self) -> bool:
        return bool(self.original_date and self.original_date != self.delivery_date)

    @property
    def effective_price(self):
        return self.price_override if self.price_override is not None \
            else self.package.price_per_unit

    def mark_delivered(self, when=None):
        from django.utils import timezone
        self.status = 'delivered'
        self.delivered_at = when or timezone.now()
        self.failure_reason = ''
        self.save(update_fields=['status', 'delivered_at', 'failure_reason',
                                 'updated_at'])

    def mark_undelivered(self, reason: str = ''):
        """The stop did not happen. Flag it; the office decides the new date."""
        self.status = 'undelivered'
        self.failure_reason = reason or ''
        self.save(update_fields=['status', 'failure_reason', 'updated_at'])

    def reschedule_to(self, new_date):
        """Move this order to another day and put it back in the queue."""
        self.delivery_date = new_date
        self.status = 'pending'
        self.stop = None
        self.failure_reason = ''
        self.reschedule_count += 1
        self.save(update_fields=['delivery_date', 'status', 'stop',
                                 'failure_reason', 'reschedule_count',
                                 'updated_at'])
