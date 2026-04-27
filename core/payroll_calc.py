"""
core/utils/payroll_calc.py — Payroll calculation engine.
Reads tax config from PayrollConfig (DB-editable, 2026 defaults).
"""

from decimal import Decimal
from datetime import date
from django.db.models import Sum, Q

from core.models import (
    PayrollConfig, Driver, Attendance, CraneSession, Payslip
)


# ═════════════════════════════════════════════════════════════════
# Tax & deduction functions
# ═════════════════════════════════════════════════════════════════

def calculate_income_tax(gross_monthly: float, tax_points: float, cfg: PayrollConfig = None) -> float:
    """Israeli progressive income tax, minus tax credit points."""
    if gross_monthly <= 0:
        return 0.0
    cfg = cfg or PayrollConfig.get_config()

    tax = 0.0
    remaining = gross_monthly
    prev_limit = 0

    for limit, rate in cfg.tax_brackets:
        if remaining <= 0:
            break
        bracket_size = limit - prev_limit
        taxable_in_bracket = min(remaining, bracket_size)
        tax += taxable_in_bracket * rate
        remaining -= taxable_in_bracket
        prev_limit = limit

    credit = tax_points * float(cfg.tax_point_value)
    tax = max(0, tax - credit)
    return round(tax, 2)


def calculate_national_insurance(gross_monthly: float, cfg: PayrollConfig = None) -> float:
    """ביטוח לאומי — National Insurance (employee)."""
    if gross_monthly <= 0:
        return 0.0
    cfg = cfg or PayrollConfig.get_config()
    capped = min(gross_monthly, float(cfg.insurance_high_limit))
    low  = min(capped, float(cfg.insurance_low_limit))
    high = max(0, capped - float(cfg.insurance_low_limit))
    total = (low * float(cfg.national_insurance_low)
           + high * float(cfg.national_insurance_high))
    return round(total, 2)


def calculate_health_insurance(gross_monthly: float, cfg: PayrollConfig = None) -> float:
    """מס בריאות — Health Insurance (employee)."""
    if gross_monthly <= 0:
        return 0.0
    cfg = cfg or PayrollConfig.get_config()
    capped = min(gross_monthly, float(cfg.insurance_high_limit))
    low  = min(capped, float(cfg.insurance_low_limit))
    high = max(0, capped - float(cfg.insurance_low_limit))
    total = (low * float(cfg.health_insurance_low)
           + high * float(cfg.health_insurance_high))
    return round(total, 2)


def calculate_tax_points(driver: Driver, cfg: PayrollConfig = None) -> float:
    """Calculate total tax points based on driver's profile + children."""
    cfg = cfg or PayrollConfig.get_config()
    points = float(cfg.base_tax_points)  # default 2.25

    # Female +0.5
    if driver.gender == 'female':
        points += 0.5

    # Children
    today = date.today()
    for child in driver.children.all():
        age = today.year - child.birth_date.year
        if (today.month, today.day) < (child.birth_date.month, child.birth_date.day):
            age -= 1

        if age < 1:
            child_points = 1.5
        elif age < 5:
            child_points = 2.5
        elif age < 18:
            child_points = 1.0
        else:
            child_points = 0

        if child.has_disability:
            child_points += 2.0
        if child.receives_allowance:
            child_points = max(0, child_points - 1.0)

        points += child_points

    # Immigrant (first 18 months = 3 pts, next 12 = 2, next 12 = 1)
    if driver.is_immigrant and driver.immigrant_since:
        months = (today.year - driver.immigrant_since.year) * 12 + (today.month - driver.immigrant_since.month)
        if months <= 18:
            points += 3.0
        elif months <= 30:
            points += 2.0
        elif months <= 42:
            points += 1.0

    # Manual override / extras
    points += float(driver.extra_tax_points or 0)
    return round(points, 2)


# ═════════════════════════════════════════════════════════════════
# Work hours aggregation
# ═════════════════════════════════════════════════════════════════

def aggregate_work_data(driver: Driver, year: int, month: int, cfg: PayrollConfig = None) -> dict:
    """
    Look at all attendance + crane sessions for this driver in the given month.
    Return a dict of: working_days, total_hours, regular_hours,
                      overtime_125_h, overtime_150_h, crane_hours.
    """
    cfg = cfg or PayrollConfig.get_config()

    # Daily overtime threshold comes from CompanySettings (already in your model)
    from core.models import CompanySettings
    company = CompanySettings.objects.first()
    ot_threshold = float(company.overtime_threshold) if company else 8.0

    ot_125_limit = float(cfg.overtime_125_limit)  # first N OT hrs at 125%

    attendances = Attendance.objects.filter(
        driver=driver, date__year=year, date__month=month,
        clock_out__isnull=False
    )

    working_days  = attendances.count()
    total_hours   = 0.0
    regular_hours = 0.0
    ot_125_hours  = 0.0
    ot_150_hours  = 0.0

    for att in attendances:
        if not att.clock_out:
            continue
        hours = (att.clock_out - att.clock_in).total_seconds() / 3600.0
        total_hours += hours

        if hours <= ot_threshold:
            regular_hours += hours
        else:
            regular_hours += ot_threshold
            overtime = hours - ot_threshold
            # First N overtime hours at 125%, rest at 150%
            ot_125 = min(overtime, ot_125_limit)
            ot_150 = max(0, overtime - ot_125_limit)
            ot_125_hours += ot_125
            ot_150_hours += ot_150

    # Crane hours (billed)
    crane_hours = CraneSession.objects.filter(
        driver=driver, date__year=year, date__month=month,
        work_end__isnull=False
    ).aggregate(total=Sum('billed_hours'))['total'] or 0

    return {
        'working_days':  working_days,
        'total_hours':   round(total_hours, 2),
        'regular_hours': round(regular_hours, 2),
        'overtime_125_h': round(ot_125_hours, 2),
        'overtime_150_h': round(ot_150_hours, 2),
        'crane_hours':   round(float(crane_hours), 2),
    }


# ═════════════════════════════════════════════════════════════════
# MAIN — Generate a payslip for one driver for one month
# ═════════════════════════════════════════════════════════════════

def generate_payslip(driver: Driver, year: int, month: int, save: bool = True) -> Payslip:
    """
    Compute and (optionally) save a Payslip for a driver for the given month.
    If a payslip already exists (draft), it will be updated.
    """
    # Force reload from DB to avoid stale data from cached instance
    driver.refresh_from_db()
    print(f"[PAYROLL] driver={driver.full_name} id={driver.id}", flush=True)
    print(f"[PAYROLL]   salary_type={driver.salary_type} base_rate={driver.base_rate}", flush=True)
    print(f"[PAYROLL]   pension%={driver.pension_percent} study_fund%={driver.study_fund_percent}", flush=True)
    print(f"[PAYROLL]   travel={driver.travel_allowance} crane_rate={driver.crane_hourly_rate}", flush=True)
    print(f"[PAYROLL]   gender={driver.gender} children_count={driver.children.count()}", flush=True)
    cfg = PayrollConfig.get_config()

    work = aggregate_work_data(driver, year, month, cfg=cfg)

    # ── Compute earnings based on salary_type ──
    base_rate = float(driver.base_rate or 0)
    ot_rate   = float(driver.overtime_rate or 0) if driver.overtime_rate else base_rate
    crane_rate = float(driver.crane_hourly_rate or 0)

    salary_type = driver.salary_type or 'monthly'

    base_pay         = 0.0
    overtime_125_pay = 0.0
    overtime_150_pay = 0.0

    # If driver has a custom overtime_rate, use it as the base for OT multipliers
    # Otherwise use the salary rate (hourly derived or base_rate)
    if salary_type == 'monthly':
        base_pay = base_rate  # flat monthly salary
        # Derive hourly for OT calculation
        hourly_for_ot = ot_rate if driver.overtime_rate else (base_rate / 186 if base_rate else 0)
        overtime_125_pay = work['overtime_125_h'] * hourly_for_ot * float(cfg.overtime_125_rate)
        overtime_150_pay = work['overtime_150_h'] * hourly_for_ot * float(cfg.overtime_150_rate)

    elif salary_type == 'daily':
        base_pay = work['working_days'] * base_rate
        # For daily workers, OT needs explicit overtime_rate or fallback to base/8
        hourly_for_ot = ot_rate if driver.overtime_rate else (base_rate / 8 if base_rate else 0)
        overtime_125_pay = work['overtime_125_h'] * hourly_for_ot * float(cfg.overtime_125_rate)
        overtime_150_pay = work['overtime_150_h'] * hourly_for_ot * float(cfg.overtime_150_rate)

    elif salary_type == 'hourly':
        base_pay = work['regular_hours'] * base_rate
        # Hourly worker: use overtime_rate if set, else base_rate as hourly
        hourly_for_ot = ot_rate if driver.overtime_rate else base_rate
        overtime_125_pay = work['overtime_125_h'] * hourly_for_ot * float(cfg.overtime_125_rate)
        overtime_150_pay = work['overtime_150_h'] * hourly_for_ot * float(cfg.overtime_150_rate)

    crane_pay        = work['crane_hours'] * crane_rate
    travel_allowance = float(driver.travel_allowance or 0)

    gross_pay = round(
        base_pay + overtime_125_pay + overtime_150_pay + crane_pay + travel_allowance,
        2
    )

    # ── Deductions ──
    tax_points = calculate_tax_points(driver, cfg=cfg)

    income_tax     = calculate_income_tax(gross_pay, tax_points, cfg=cfg)
    national_ins   = calculate_national_insurance(gross_pay, cfg=cfg)
    health_ins     = calculate_health_insurance(gross_pay, cfg=cfg)
    # Pension & study fund only if driver is entitled (booleans)
    pension_emp    = round(gross_pay * float(driver.pension_percent    or 0) / 100, 2) if getattr(driver, 'has_pension',    False) else 0.0
    study_fund_emp = round(gross_pay * float(driver.study_fund_percent or 0) / 100, 2) if getattr(driver, 'has_study_fund', False) else 0.0

    total_deductions = round(
        income_tax + national_ins + health_ins + pension_emp + study_fund_emp,
        2
    )

    # ── Employer contributions (standard Israeli rates) — only if driver has the benefits ──
    pension_employer    = round(gross_pay * 0.065, 2)  if getattr(driver, 'has_pension',    False) else 0.0  # 6.5%
    study_fund_employer = round(gross_pay * 0.075, 2)  if getattr(driver, 'has_study_fund', False) else 0.0  # 7.5%
    severance_employer  = round(gross_pay * 0.0833, 2) if getattr(driver, 'has_pension',    False) else 0.0  # 8.33% — part of pension package

    net_pay = round(gross_pay - total_deductions, 2)

    # ── Upsert payslip ──
    if save:
        payslip, _ = Payslip.objects.update_or_create(
            driver=driver, year=year, month=month,
            defaults={
                'working_days':     work['working_days'],
                'total_hours':      work['total_hours'],
                'regular_hours':    work['regular_hours'],
                'overtime_125_h':   work['overtime_125_h'],
                'overtime_150_h':   work['overtime_150_h'],
                'crane_hours':      work['crane_hours'],
                'base_pay':         base_pay,
                'overtime_125_pay': overtime_125_pay,
                'overtime_150_pay': overtime_150_pay,
                'crane_pay':        crane_pay,
                'travel_allowance': travel_allowance,
                'gross_pay':        gross_pay,
                'tax_points_used':  tax_points,
                'income_tax':       income_tax,
                'national_ins':     national_ins,
                'health_ins':       health_ins,
                'pension_emp':      pension_emp,
                'study_fund_emp':   study_fund_emp,
                'total_deductions': total_deductions,
                'pension_employer':    pension_employer,
                'study_fund_employer': study_fund_employer,
                'severance_employer':  severance_employer,
                'net_pay':          net_pay,
            }
        )
        return payslip

    # Unsaved instance (for preview)
    return Payslip(
        driver=driver, year=year, month=month,
        working_days=work['working_days'],
        total_hours=work['total_hours'],
        regular_hours=work['regular_hours'],
        overtime_125_h=work['overtime_125_h'],
        overtime_150_h=work['overtime_150_h'],
        crane_hours=work['crane_hours'],
        base_pay=base_pay,
        overtime_125_pay=overtime_125_pay,
        overtime_150_pay=overtime_150_pay,
        crane_pay=crane_pay,
        travel_allowance=travel_allowance,
        gross_pay=gross_pay,
        tax_points_used=tax_points,
        income_tax=income_tax,
        national_ins=national_ins,
        health_ins=health_ins,
        pension_emp=pension_emp,
        study_fund_emp=study_fund_emp,
        total_deductions=total_deductions,
        pension_employer=pension_employer,
        study_fund_employer=study_fund_employer,
        severance_employer=severance_employer,
        net_pay=net_pay,
    )


def generate_all_payslips(year: int, month: int) -> list:
    """Generate payslips for ALL active drivers for the given month."""
    drivers = Driver.objects.filter(is_active=True)
    return [generate_payslip(d, year, month, save=True) for d in drivers]