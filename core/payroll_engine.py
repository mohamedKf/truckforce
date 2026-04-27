"""
Israeli payroll engine – 2025 brackets
Covers: income tax, bituach leumi, health insurance,
        pension, study fund, severance.
"""
from decimal import Decimal


# ── 2025 Income Tax Brackets (monthly) ──────────────────
# (up_to_ils, rate)  – last bracket has no upper limit
INCOME_TAX_BRACKETS = [
    (7010,   0.10),
    (10060,  0.14),
    (16150,  0.20),
    (21400,  0.31),
    (44550,  0.35),
    (57450,  0.47),
    (float('inf'), 0.50),
]

# ── 2025 Tax Credit Point Value ─────────────────────────
TAX_CREDIT_POINT_VALUE = Decimal('242')   # ₪ per month per point

# ── 2025 Bituach Leumi (employee) ───────────────────────
BL_REDUCED_RATE   = Decimal('0.004')   # up to BL_REDUCED_CEILING
BL_FULL_RATE      = Decimal('0.07')
BL_REDUCED_CEILING = Decimal('7522')   # monthly
BL_MAX_CEILING    = Decimal('49030')   # monthly cap

# ── 2025 Health Insurance (employee) ────────────────────
HI_REDUCED_RATE   = Decimal('0.031')
HI_FULL_RATE      = Decimal('0.05')
HI_REDUCED_CEILING = Decimal('7522')

# ── Employer contributions ───────────────────────────────
EMPLOYER_PENSION_RATE   = Decimal('0.075')   # 7.5%
EMPLOYER_SEVERANCE_RATE = Decimal('0.0833')  # 8.33%
EMPLOYER_STUDY_RATE     = Decimal('0.075')   # 7.5%


def calc_income_tax(monthly_gross: Decimal, credit_points: Decimal = Decimal('2.25')) -> Decimal:
    """Calculate monthly income tax after credit points."""
    gross = float(monthly_gross)
    tax   = 0.0
    prev  = 0
    for ceiling, rate in INCOME_TAX_BRACKETS:
        if gross <= prev:
            break
        taxable = min(gross, ceiling) - prev
        tax    += taxable * rate
        prev    = ceiling

    credit  = float(credit_points * TAX_CREDIT_POINT_VALUE)
    tax_net = max(0.0, tax - credit)
    return Decimal(str(round(tax_net, 2)))


def calc_bituach_leumi(monthly_gross: Decimal):
    """Returns (bituach_leumi, health_insurance) tuple."""
    gross = min(monthly_gross, BL_MAX_CEILING)

    # Bituach leumi
    if gross <= BL_REDUCED_CEILING:
        bl = gross * BL_REDUCED_RATE
    else:
        bl = BL_REDUCED_CEILING * BL_REDUCED_RATE + (gross - BL_REDUCED_CEILING) * BL_FULL_RATE

    # Health insurance
    if gross <= HI_REDUCED_CEILING:
        hi = gross * HI_REDUCED_RATE
    else:
        hi = HI_REDUCED_CEILING * HI_REDUCED_RATE + (gross - HI_REDUCED_CEILING) * HI_FULL_RATE

    return Decimal(str(round(bl, 2))), Decimal(str(round(hi, 2)))


def calc_employer_contributions(monthly_gross: Decimal, pension_pct: Decimal, study_pct: Decimal):
    pension   = monthly_gross * (pension_pct / 100)
    severance = monthly_gross * EMPLOYER_SEVERANCE_RATE
    study     = monthly_gross * (study_pct / 100)
    return (
        Decimal(str(round(pension, 2))),
        Decimal(str(round(severance, 2))),
        Decimal(str(round(study, 2))),
    )


def generate_payroll(driver, month: int, year: int, attendance_qs, crane_qs):
    """
    Full payroll calculation for a driver for a given month.
    Returns a dict that maps directly to Payroll model fields.
    """
    from .models import CompanySettings
    settings = CompanySettings.objects.first()
    overtime_threshold = float(settings.overtime_threshold) if settings else 8.0

    # ── Aggregate attendance ────────────────────────────
    working_days  = 0
    total_hours   = Decimal('0')
    overtime_hours = Decimal('0')

    for att in attendance_qs:
        if att.clock_in and att.clock_out:
            working_days += 1
            hours = Decimal(str(att.total_hours))
            total_hours += hours
            ot = max(Decimal('0'), hours - Decimal(str(overtime_threshold)))
            overtime_hours += ot

    regular_hours = total_hours - overtime_hours

    # ── Aggregate crane ────────────────────────────────
    crane_hours = sum(
        Decimal(str(c.billed_hours)) for c in crane_qs
    )

    # ── Base pay ───────────────────────────────────────
    if driver.salary_type == 'daily':
        base_pay = Decimal(str(driver.base_rate)) * Decimal(str(working_days))
    elif driver.salary_type == 'hourly':
        base_pay = Decimal(str(driver.base_rate)) * regular_hours
    else:  # monthly
        base_pay = Decimal(str(driver.base_rate))

    overtime_pay = Decimal(str(driver.overtime_rate)) * overtime_hours
    crane_pay    = Decimal(str(driver.crane_hourly_rate)) * crane_hours
    travel       = Decimal(str(driver.travel_allowance)) * Decimal(str(working_days))
    gross_pay    = base_pay + overtime_pay + crane_pay + travel

    # ── Deductions ─────────────────────────────────────
    income_tax          = calc_income_tax(gross_pay, Decimal(str(driver.tax_credit_points)))
    bituach_leumi, hi   = calc_bituach_leumi(gross_pay)
    pension_employee    = gross_pay * (Decimal(str(driver.pension_percent)) / 100)
    study_employee      = gross_pay * (Decimal(str(driver.study_fund_percent)) / 100)

    pension_employee    = Decimal(str(round(pension_employee, 2)))
    study_employee      = Decimal(str(round(study_employee, 2)))
    total_deductions    = income_tax + bituach_leumi + hi + pension_employee + study_employee

    net_pay = gross_pay - total_deductions

    # ── Employer contributions ─────────────────────────
    pension_employer, severance_fund, study_employer = calc_employer_contributions(
        gross_pay,
        Decimal(str(driver.pension_percent)),
        Decimal(str(driver.study_fund_percent)),
    )

    return {
        'driver':               driver,
        'month':                month,
        'year':                 year,
        'working_days':         working_days,
        'total_hours':          round(total_hours, 2),
        'overtime_hours':       round(overtime_hours, 2),
        'crane_hours':          round(crane_hours, 2),
        'base_pay':             round(base_pay, 2),
        'overtime_pay':         round(overtime_pay, 2),
        'crane_pay':            round(crane_pay, 2),
        'travel_allowance':     round(travel, 2),
        'gross_pay':            round(gross_pay, 2),
        'income_tax':           income_tax,
        'bituach_leumi':        bituach_leumi,
        'health_insurance':     hi,
        'pension_employee':     pension_employee,
        'study_fund_employee':  study_employee,
        'total_deductions':     round(total_deductions, 2),
        'pension_employer':     pension_employer,
        'study_fund_employer':  study_employer,
        'severance_fund':       severance_fund,
        'net_pay':              round(net_pay, 2),
    }
