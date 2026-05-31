"""Demo-data seeder for screenshot / pitch-deck purposes.

STANDALONE VERSION — run directly without manage.py:

    python core\\seed_demo.py

(or from any directory: python C:\\path\\to\\core\\seed_demo.py)
"""
from __future__ import annotations

# ──────────────────────────────────────────────────────────
# DJANGO BOOT — must happen BEFORE any model imports
# ──────────────────────────────────────────────────────────
# When you run `python manage.py X`, Django sets all this up for you.
# Because we're running this file directly, we have to do the
# equivalent ourselves: tell Python where the project root is, point
# DJANGO_SETTINGS_MODULE at the right settings file, then call
# django.setup() so Apps and models are wired up properly.
import os
import sys
from pathlib import Path

# This file lives at <project_root>/core/seed_demo.py, so the project
# root is one directory up. We prepend it to sys.path so the
# `core` and `config` packages are importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# The project's settings module — adjust if yours lives somewhere else.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()


# ──────────────────────────────────────────────────────────
# Now safe to import everything else
# ──────────────────────────────────────────────────────────
import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.utils import timezone as tz


# ──────────────────────────────────────────────────────────
# DATA POOLS
# ──────────────────────────────────────────────────────────

# 10 Israeli driver names — mix of secular, religious, and Arab-Israeli
# so the screenshots feel like a real Israeli fleet (which is exactly
# the audience we're targeting first).
DRIVER_POOL = [
    ('יוסי כהן',          'יוסי',  '052-1234567', 'C'),
    ('דוד לוי',           'דוד',   '050-2345678', 'CE'),
    ('משה אברהם',         'משה',   '054-3456789', 'C'),
    ('אבי מזרחי',         'אבי',   '052-4567890', 'C1'),
    ('יעקב פרידמן',       'יעקב',  '053-5678901', 'CE'),
    ('שמואל בן-דוד',      'שמואל', '050-6789012', 'C'),
    ('אחמד אבו-חוסיין',   'אחמד',  '054-7890123', 'CE'),
    ('עומר נסר',          'עומר',  '052-8901234', 'C'),
    ('איתי גולן',         'איתי',  '050-9012345', 'C1'),
    ('רונן שמש',          'רונן',  '053-0123456', 'CE'),
]

# 5 trucks — mix of brands you'd actually see on Israeli roads.
TRUCK_POOL = [
    ('Volvo',         'FH 460',   2022, '12-345-67', Decimal('18.0'), False),
    ('Mercedes-Benz', 'Actros',   2021, '23-456-78', Decimal('25.0'), True),
    ('MAN',           'TGS 26',   2023, '34-567-89', Decimal('15.5'), False),
    ('Scania',        'R 500',    2020, '45-678-90', Decimal('22.0'), True),
    ('Isuzu',         'NQR 75',   2022, '56-789-01', Decimal('7.5'),  False),
]

# Real Israeli locations with real GPS coordinates so the route lines
# on the map look credible (clients near roads, not in fields).
ISRAELI_STOPS = [
    # site_name, city, address, lat, lng
    ('חברת הדים בע״מ',       'תל אביב',   'רחוב הרצל 142, תל אביב',         32.0648, 34.7711),
    ('מרכז שיווק רותם',      'רמת גן',    'רחוב ביאליק 18, רמת גן',         32.0823, 34.8131),
    ('סופר דיל בני ברק',      'בני ברק',   'רחוב רבי עקיבא 56, בני ברק',     32.0832, 34.8338),
    ('מחסן רהיטי השרון',      'הרצליה',    'רחוב הצלע 7, הרצליה',           32.1624, 34.8447),
    ('בית מלון פרדייז',       'נתניה',     'טיילת נתניה 12, נתניה',          32.3215, 34.8532),
    ('חנות בולענים',          'חדרה',      'רחוב הרברט סמואל 5, חדרה',       32.4365, 34.9196),
    ('מרכז לוגיסטי קיסריה',   'קיסריה',    'אזור תעשייה קיסריה',             32.5006, 34.9018),
    ('חברת אדמית',           'חיפה',      'רחוב הנמל 23, חיפה',             32.8245, 34.9943),
    ('פטיוס ושות׳',           'חיפה',      'רחוב יפו 75, חיפה',              32.8156, 34.9892),
    ('מרכז דניה',            'חיפה',      'רחוב פיכמן 14, חיפה',            32.7901, 34.9803),
    ('אם המושבות',           'פתח תקווה', 'רחוב ז׳בוטינסקי 22, פתח תקווה',   32.0867, 34.8862),
    ('פרי גליל',             'יקנעם',     'אזור תעשייה יקנעם',              32.6573, 35.1107),
    ('בנימינה ייצור',         'בנימינה',   'רחוב המייסדים 8, בנימינה',        32.5189, 34.9489),
    ('הוט מובייל מרכז שירות', 'אשדוד',     'רחוב הצלע 14, אשדוד',            31.8044, 34.6553),
    ('בית עסק רעננה',        'רעננה',     'רחוב אחוזה 110, רעננה',          32.1840, 34.8708),
    ('שיק לוגיסטיקה',         'רחובות',    'רחוב הפלמ״ח 56, רחובות',         31.8943, 34.8094),
    ('המכל הירוק',           'ראשון לציון','רחוב רוטשילד 45, ראשון לציון',   31.9590, 34.7991),
    ('מינימרקט בלעז',         'בת ים',     'רחוב בלפור 7, בת ים',            32.0167, 34.7456),
    ('פאלפא',                'חולון',     'רחוב סוקולוב 88, חולון',         32.0167, 34.7793),
    ('סופר זול',             'לוד',       'רחוב הרצל 65, לוד',              31.9468, 34.8896),
]

# Realistic Hebrew first-names for the contact_name on stops.
CONTACT_FIRSTNAMES = [
    'גלית', 'ענת', 'תמר', 'מאיה', 'דנה', 'שירה', 'נועה', 'הילה',
    'אורי', 'גיל', 'איתי', 'רן', 'שגיא', 'אופיר', 'בועז', 'אסף',
]

CONTACT_LASTNAMES = [
    'כהן', 'לוי', 'מזרחי', 'פרץ', 'אזולאי', 'ביטון', 'דהן', 'גולן',
    'הלל', 'אדרי', 'שטרית', 'אוחנה', 'אטיאס', 'בן-דוד',
]

# Common items appearing on delivery notes — Hebrew, gives screenshots life.
ITEMS_POOL = [
    'פלטות פלדה — 12 חבילות',
    'חלקי חילוף לרכבים — קרטונים מס׳ 145-152',
    'פריקת קונטיינר 20 רגל',
    'משלוח מקרי כלי עבודה — 8 ארגזים',
    'חבילת מסמכים + 2 צמיגי משאית',
    'מערכת מיזוג מרכזית — 3 יחידות',
    'אבני ריצוף — 24 משטחים',
    'מקררים תעשייתיים — 4 יחידות',
    'אלמנטים מתכת — 6 חבילות',
    'מטען רגיל — 18 ארגזים',
]


# ──────────────────────────────────────────────────────────
# THE COMMAND
# ──────────────────────────────────────────────────────────

# ──────────────────────────────────────────────────────────
# THE SEED FUNCTION
# ──────────────────────────────────────────────────────────

def main():
    """Wipe and reseed the local DB with demo data for screenshots."""
    # Parse the optional flag without bringing in argparse for one switch.
    opts = {'i_really_mean_it': '--i-really-mean-it' in sys.argv}
    # Safety guard — don't let someone accidentally nuke production.
    if not settings.DEBUG and not opts.get('i_really_mean_it'):
        print(
            "Refusing to seed: DEBUG=False. "
            "If you REALLY want to run this on a non-debug DB, "
            "rerun with --i-really-mean-it."
        )
        sys.exit(1)

    # We import inside handle() so this file is harmless to import-discover
    # (e.g. by Django's app loader) even when the project's apps aren't
    # fully registered yet.
    from core.models import (
        Manager, Driver, Truck, DailySchedule, Stop, Attendance,
        Payslip, DriverLocation, CompanySettings, StopPhoto, StopTask,
        TrackingLink, AttendanceFixRequest,
    )

    print('▶ Wiping existing demo tables…')
    with transaction.atomic():
        # Order matters: child rows first.
        StopPhoto.objects.all().delete()
        StopTask.objects.all().delete()
        Stop.objects.all().delete()
        DailySchedule.objects.all().delete()
        AttendanceFixRequest.objects.all().delete()
        Attendance.objects.all().delete()
        Payslip.objects.all().delete()
        DriverLocation.objects.all().delete()
        TrackingLink.objects.all().delete()
        Truck.objects.all().delete()
        Driver.objects.all().delete()
        Manager.objects.all().delete()
        CompanySettings.objects.all().delete()

    print('  ✓ wiped')

    # ── Company + Manager ──
    print('▶ Creating company + manager…')
    CompanySettings.objects.create(
        company_name='הובלות הירדן בע״מ',
        phone='03-5551234',
        email='office@hayarden.co.il',
        address='רחוב המסילה 17, פתח תקווה',
        default_language='he',
        crane_price_per_hour=Decimal('450'),
        work_start_hour=time(7, 0),
    )
    admin = Manager.objects.create(
        full_name='מאיר רוזנברג',
        username='admin',
        email='manager@hayarden.co.il',
        phone='050-1112233',
        role='admin',
    )
    admin.set_password('admin')
    admin.save()
    print('  ✓ admin / admin')

    # ── Drivers ──
    print('▶ Creating drivers…')
    drivers = []
    for i, (full, first, phone, lic) in enumerate(DRIVER_POOL):
        base = Decimal(random.choice([350, 380, 400, 420, 450]))
        d = Driver.objects.create(
            username      = f'driver{i + 1}',
            full_name     = full,
            id_number     = f'{random.randint(100_000_000, 399_999_999)}',
            email         = f'{first.replace("ם", "m")}@hayarden.co.il'.lower()[:50],
            phone         = phone,
            license_type  = lic,
            license_number= f'{random.randint(1000000, 9999999)}',
            license_expiry= date.today() + timedelta(days=random.randint(180, 720)),
            max_tonnage   = {'C': 12, 'C1': 7.5, 'CE': 40, 'B': 3.5, 'D': 18}[lic],
            crane_certified=(i % 4 == 0),
            salary_type   = 'daily',
            base_rate     = base,
            overtime_rate = base * Decimal('1.25'),
            crane_hourly_rate=Decimal('120') if i % 4 == 0 else Decimal('0'),
            travel_allowance=Decimal(random.choice([300, 400, 500])),
            tax_credit_points=Decimal('2.25'),
            has_pension   = True,
            has_study_fund= (i % 3 != 0),
            hire_date     = date.today() - timedelta(days=random.randint(120, 1500)),
            is_active     = True,
        )
        d.set_password('1234')
        d.save()
        drivers.append(d)
    print(f'  ✓ {len(drivers)} drivers (login: driverN / 1234)')

    # ── Trucks ──
    print('▶ Creating trucks…')
    trucks = []
    for brand, model, year, plate, capacity, has_crane in TRUCK_POOL:
        t = Truck.objects.create(
            brand        = brand,
            model        = model,
            year         = year,
            plate_number = plate,
            capacity_tons= capacity,
            has_crane    = has_crane,
            status       = 'active',
            last_service_date= date.today() - timedelta(days=random.randint(20, 90)),
            next_service_date= date.today() + timedelta(days=random.randint(30, 180)),
            next_inspection  = date.today() + timedelta(days=random.randint(60, 300)),
            odometer_km  = random.randint(45_000, 320_000),
        )
        trucks.append(t)
    print(f'  ✓ {len(trucks)} trucks')

    # ── Schedules + Stops ──
    # 4 schedules today, 4 tomorrow. Each picks a random driver + truck
    # and 5-8 random stops from the pool. We give today's first
    # schedule a couple of "done" stops so the progress bar shows real
    # state in screenshots, not all-pending boredom.
    print('▶ Creating schedules + stops…')
    today = date.today()
    tomorrow = today + timedelta(days=1)
    all_schedules = []
    random.shuffle(drivers)  # so first-N drivers in each day differ

    for day_index, day in enumerate([today, tomorrow]):
        day_drivers = drivers[day_index * 4:(day_index * 4) + 4]
        for sched_index, drv in enumerate(day_drivers):
            truck = trucks[(day_index * 4 + sched_index) % len(trucks)]
            sched = DailySchedule.objects.create(
                driver = drv,
                truck  = truck,
                date   = day,
                status = 'pending',
                manager_notes = '',
                created_by    = admin,
            )

            n_stops = random.randint(5, 8)
            picks = random.sample(ISRAELI_STOPS, n_stops)
            start_hour = random.choice([7, 8, 9])
            for order_idx, (site, city, addr, lat, lng) in enumerate(picks, start=1):
                arrive = time(
                    hour=min(start_hour + (order_idx - 1), 19),
                    minute=random.choice([0, 15, 30, 45]),
                )
                contact = (
                    f"{random.choice(CONTACT_FIRSTNAMES)} "
                    f"{random.choice(CONTACT_LASTNAMES)}"
                )
                contact_phone = (
                    f"05{random.choice([0, 2, 3, 4])}-"
                    f"{random.randint(1000000, 9999999)}"
                )
                Stop.objects.create(
                    schedule         = sched,
                    order            = order_idx,
                    site_name        = site,
                    address          = addr,
                    latitude         = Decimal(str(lat)),
                    longitude        = Decimal(str(lng)),
                    expected_arrival = arrive,
                    stop_type        = random.choice(
                        ['delivery', 'delivery', 'delivery', 'pickup', 'service']
                    ),
                    items            = random.choice(ITEMS_POOL),
                    contact_name     = contact,
                    contact_phone    = contact_phone,
                    status           = 'pending',
                )

            # On today's first 2 schedules, mark a couple of stops as done
            # and one as skipped — gives screenshots realistic progress
            # state instead of a wall of "pending".
            if day == today and sched_index < 2:
                stops_qs = sched.stops.order_by('order')
                for s in stops_qs[:2]:
                    s.status = 'done'
                    s.completed_at = tz.now() - timedelta(
                        hours=random.randint(1, 4)
                    )
                    s.actual_arrival = s.completed_at - timedelta(minutes=15)
                    s.save()
                if stops_qs.count() >= 4:
                    skip = stops_qs[2]
                    skip.status = 'skipped'
                    skip.skip_reason = random.choice([
                        'הלקוח לא ענה — תיאמנו ליום הבא',
                        'גישה חסומה ע״י עבודות בכביש',
                        'הסחורה לא הייתה מוכנה',
                    ])
                    skip.completed_at = tz.now() - timedelta(hours=2)
                    skip.save()
                sched.status = 'in_progress'
                sched.save()

            all_schedules.append(sched)
    print(f'  ✓ {len(all_schedules)} schedules')

    # ── Today's driver shifts (clocked-in) + GPS trail ──
    # The Live Map page ONLY shows drivers who have an open attendance
    # (clocked in but not out), so we have to create those rows too.
    # We also lay down a short trail of historical location points so
    # the map renders the path each driver has driven so far today —
    # otherwise it's just a static dot, which photographs poorly.
    print('▶ Seeding today shifts + GPS trails (Live Map)…')
    today_scheds = list(DailySchedule.objects.filter(date=today))
    now = tz.now()

    def _haversine_step(lat, lng, frac, dest_lat, dest_lng):
        """Linear interpolation between two points — close enough for
        faked telematics, and easier than re-implementing route
        geometry. Returns a point `frac` of the way from (lat,lng)
        toward (dest_lat,dest_lng)."""
        return (
            lat + (dest_lat - lat) * frac,
            lng + (dest_lng - lng) * frac,
        )

    for sched in today_scheds:
        # Build a list of (lat, lng) waypoints to interpolate between:
        # depot-ish start → each done stop in order → halfway to the
        # next pending stop. That way the trail traces real visits.
        stops_in_order = list(sched.stops.order_by('order'))
        if not stops_in_order:
            continue

        # Start the day at a slightly offset point from the first stop
        # so the trail doesn't begin exactly on it.
        first = stops_in_order[0]
        if not first.latitude:
            continue
        start_lat = float(first.latitude) + random.uniform(-0.06, -0.02)
        start_lng = float(first.longitude) + random.uniform(-0.06, -0.02)

        waypoints = [(start_lat, start_lng)]
        for s in stops_in_order:
            if s.latitude and s.longitude and s.status in ('done', 'skipped'):
                waypoints.append((float(s.latitude), float(s.longitude)))

        # Add a partial leg toward the next pending stop (so the
        # current marker sits between waypoints, looking "en route").
        next_pending = next(
            (s for s in stops_in_order if s.status == 'pending' and s.latitude),
            None,
        )
        if next_pending and len(waypoints) >= 1:
            prev_lat, prev_lng = waypoints[-1]
            mid = _haversine_step(
                prev_lat, prev_lng, random.uniform(0.35, 0.65),
                float(next_pending.latitude), float(next_pending.longitude),
            )
            waypoints.append(mid)

        # Open shift: clocked-in at 07:30 today, no clock_out yet.
        # Use update_or_create in case the historical-attendance loop
        # happened to land on today (it doesn't currently, but if the
        # 6-month range ever overlaps we don't want a UNIQUE crash).
        ci = tz.make_aware(datetime.combine(today, time(7, 30)))
        Attendance.objects.update_or_create(
            driver = sched.driver,
            date   = today,
            defaults={
                'clock_in':     ci,
                'clock_out':    None,
                'clock_in_lat': Decimal(str(round(waypoints[0][0], 6))),
                'clock_in_lng': Decimal(str(round(waypoints[0][1], 6))),
                'regular_hours':  Decimal('0'),
                'overtime_125_h': Decimal('0'),
                'overtime_150_h': Decimal('0'),
            },
        )

        # Generate ~20-40 trail points evenly between consecutive
        # waypoints, with timestamps spread between clock-in and now.
        total_points = random.randint(20, 40)
        n_segments = max(1, len(waypoints) - 1)
        per_segment = max(2, total_points // n_segments)

        timeline_start = ci + timedelta(minutes=5)
        timeline_end   = now - timedelta(seconds=30)
        total_span_sec = (timeline_end - timeline_start).total_seconds()
        point_idx = 0

        for seg_idx in range(n_segments):
            a_lat, a_lng = waypoints[seg_idx]
            b_lat, b_lng = waypoints[seg_idx + 1]
            for k in range(per_segment):
                frac = k / per_segment
                plat, plng = _haversine_step(a_lat, a_lng, frac, b_lat, b_lng)
                # tiny jitter so the trail isn't a perfect ruler-line
                plat += random.uniform(-0.0008, 0.0008)
                plng += random.uniform(-0.0008, 0.0008)

                # Distribute timestamps linearly across the day
                progress = point_idx / max(1, total_points - 1)
                ts_offset = progress * total_span_sec
                point_ts = timeline_start + timedelta(seconds=ts_offset)

                loc = DriverLocation(
                    driver    = sched.driver,
                    latitude  = Decimal(str(round(plat, 6))),
                    longitude = Decimal(str(round(plng, 6))),
                    speed     = float(random.choice([0, 38, 52, 65, 72, 85, 30])),
                    accuracy  = float(random.choice([5, 8, 10, 12])),
                )
                # auto_now_add makes timestamp ignore our value on save(),
                # so set it via update() after the row exists. For demo
                # data we just save() and live with the present-time
                # bunch — they still form a valid trail.
                loc.save()
                # Force timestamp to the historical value we wanted
                DriverLocation.objects.filter(pk=loc.pk).update(timestamp=point_ts)
                point_idx += 1

    print(f'  ✓ {len(today_scheds)} drivers clocked-in with trails')

    # ── Attendance + Payslips (6 months back) ──
    # For each driver, generate ~22 working-day attendance rows per
    # month going back 6 months, then a Payslip summarising each
    # month. Numbers are realistic but not necessarily perfectly
    # consistent with the payroll engine — good enough for screenshots.
    print('▶ Generating 6 months of attendance + payslips…')
    first_of_this_month = today.replace(day=1)
    total_attendance = 0
    total_payslips = 0

    for months_back in range(1, 7):  # last 6 closed months
        # Compute the month/year by stepping back from this month
        month_target = first_of_this_month
        for _ in range(months_back):
            # First day of previous month
            prev = month_target - timedelta(days=1)
            month_target = prev.replace(day=1)
        month_num = month_target.month
        year_num  = month_target.year

        # Approx number of working days that month (skip Fri-Sat)
        from calendar import monthrange
        last_day = monthrange(year_num, month_num)[1]
        working_days_in_month = [
            date(year_num, month_num, d) for d in range(1, last_day + 1)
            if date(year_num, month_num, d).weekday() < 5  # Mon-Fri
        ]

        for drv in drivers:
            # Each driver works ~85% of working days (sick/vacation gaps)
            worked = random.sample(
                working_days_in_month,
                k=max(15, int(len(working_days_in_month) * 0.85))
            )
            worked.sort()
            total_reg = Decimal('0')
            total_125 = Decimal('0')
            total_150 = Decimal('0')

            for wd in worked:
                start_h = random.choice([6, 7, 7, 8])
                start_m = random.choice([0, 15, 30, 45])
                shift_len_h = random.choice([
                    # heavily weight 8-9h shifts (typical Israeli day)
                    8, 8, 8, 8.5, 9, 9, 9.5, 10, 7.5, 11
                ])
                ci = datetime.combine(wd, time(start_h, start_m))
                co = ci + timedelta(hours=shift_len_h)
                att = Attendance.objects.create(
                    driver    = drv,
                    date      = wd,
                    clock_in  = tz.make_aware(ci),
                    clock_out = tz.make_aware(co),
                )
                # Compute Israeli OT split (8h reg / 2h@125 / rest@150)
                total = Decimal(str(shift_len_h))
                if total <= Decimal('8'):
                    att.regular_hours = total
                elif total <= Decimal('10'):
                    att.regular_hours = Decimal('8')
                    att.overtime_125_h = total - Decimal('8')
                else:
                    att.regular_hours = Decimal('8')
                    att.overtime_125_h = Decimal('2')
                    att.overtime_150_h = total - Decimal('10')
                att.save()
                total_reg += att.regular_hours
                total_125 += att.overtime_125_h
                total_150 += att.overtime_150_h
                total_attendance += 1

            # Payslip — daily driver pays per worked day
            base_pay = Decimal(len(worked)) * drv.base_rate
            ot125_pay = total_125 * drv.overtime_rate
            ot150_pay = total_150 * (drv.base_rate * Decimal('1.5'))
            travel = drv.travel_allowance
            gross = base_pay + ot125_pay + ot150_pay + travel

            # Approximate Israeli deductions — rough but plausible
            national_ins = (gross * Decimal('0.07')).quantize(Decimal('0.01'))
            health_ins   = (gross * Decimal('0.031')).quantize(Decimal('0.01'))
            pension_emp  = (gross * Decimal('0.06')).quantize(Decimal('0.01')) if drv.has_pension else Decimal('0')
            study_emp    = (gross * Decimal('0.025')).quantize(Decimal('0.01')) if drv.has_study_fund else Decimal('0')
            # Naive income tax — only kicks in above ~5000
            taxable = max(gross - Decimal('5000'), Decimal('0'))
            income_tax = (taxable * Decimal('0.1')).quantize(Decimal('0.01'))
            total_ded = national_ins + health_ins + pension_emp + study_emp + income_tax
            net = gross - total_ded

            Payslip.objects.create(
                driver        = drv,
                year          = year_num,
                month         = month_num,
                status        = 'approved',
                working_days  = len(worked),
                total_hours   = total_reg + total_125 + total_150,
                regular_hours = total_reg,
                overtime_125_h= total_125,
                overtime_150_h= total_150,
                base_pay      = base_pay.quantize(Decimal('0.01')),
                overtime_125_pay = ot125_pay.quantize(Decimal('0.01')),
                overtime_150_pay = ot150_pay.quantize(Decimal('0.01')),
                travel_allowance = travel,
                gross_pay        = gross.quantize(Decimal('0.01')),
                tax_points_used  = drv.tax_credit_points,
                income_tax       = income_tax,
                national_ins     = national_ins,
                health_ins       = health_ins,
                pension_emp      = pension_emp,
                study_fund_emp   = study_emp,
                total_deductions = total_ded.quantize(Decimal('0.01')),
                pension_employer    = (gross * Decimal('0.065')).quantize(Decimal('0.01')) if drv.has_pension else Decimal('0'),
                study_fund_employer = (gross * Decimal('0.075')).quantize(Decimal('0.01')) if drv.has_study_fund else Decimal('0'),
                severance_employer  = (gross * Decimal('0.0833')).quantize(Decimal('0.01')),
                net_pay          = net.quantize(Decimal('0.01')),
            )
            total_payslips += 1

    print(
        f'  ✓ {total_attendance} attendance rows · {total_payslips} payslips'
    )

    print('')
    print('✓ DONE — demo data seeded')
    print('')
    print('Logins:')
    print('  Manager:  admin / admin')
    print('  Drivers:  driver1, driver2, …, driver10  /  password: 1234')
    print('')


if __name__ == "__main__":
    main()