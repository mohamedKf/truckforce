from django.db import models
from django.contrib.auth.hashers import make_password, check_password
import math


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
    company_logo         = models.ImageField(upload_to='company/', blank=True, null=True)
    phone                = models.CharField(max_length=20, blank=True)
    email                = models.EmailField(blank=True)
    address              = models.TextField(blank=True)
    default_language     = models.CharField(max_length=2, choices=LANGUAGE_CHOICES, default='he')
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
    id_number    = models.CharField(max_length=20, unique=True)   # Israeli ID
    email        = models.EmailField(blank=True)
    phone        = models.CharField(max_length=20, blank=True)
    address      = models.TextField(blank=True)
    birth_date   = models.DateField(null=True, blank=True)
    hire_date    = models.DateField(null=True, blank=True)
    photo        = models.ImageField(upload_to='profile/', blank=True, null=True,
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

    schedule        = models.ForeignKey(DailySchedule, on_delete=models.CASCADE, related_name='stops')
    order           = models.PositiveIntegerField()
    site_name       = models.CharField(max_length=200)
    address         = models.TextField()
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


class StopPhoto(models.Model):
    """Multiple delivery/proof photos per stop (unlimited)."""
    stop        = models.ForeignKey(Stop, on_delete=models.CASCADE, related_name='photos')
    image       = models.ImageField(upload_to='delivery_photos/%Y/%m/')
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
    signature_image = models.ImageField(upload_to='signatures/%Y/%m/',
                                         help_text='PNG of the hand-drawn signature')
    pdf_file        = models.FileField(upload_to='confirmations/%Y/%m/', blank=True,
                                        help_text='Generated PDF confirmation')
    whatsapp_sent   = models.BooleanField(default=False)
    email_sent      = models.BooleanField(default=False)
    created_at      = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Confirmation for stop #{self.stop_id} – {self.signed_by_name}"

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
    ]

    recipient_manager = models.ForeignKey(Manager, on_delete=models.CASCADE, null=True, blank=True)
    recipient_driver  = models.ForeignKey(Driver, on_delete=models.CASCADE, null=True, blank=True)
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
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
    file        = models.FileField(upload_to='documents/')
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
    pdf_file = models.FileField(upload_to='payslips/', blank=True, null=True)

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