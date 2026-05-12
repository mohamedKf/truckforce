"""
attendance_auto_close.py — Detects and closes shifts where a driver
forgot to clock out (>14h since clock-in), creating a fix request
so the manager and driver can correct the actual end time.

Two entry points:
  • close_one(att)              — close a specific stale Attendance row
  • close_all_stale(threshold)  — find & close every stale shift across all drivers

Used by:
  • ClockInView, ClockOutView, DriverLocationUpdateView (lazy/just-in-time)
  • management command `auto_close_shifts` (periodic cron)
"""

from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from .models import Attendance, AttendanceFixRequest
from .firebase_sync import publish_event


# How long after clock-in we consider a shift "forgotten".
# No human should be on shift longer than this without clocking out.
STALE_SHIFT_HOURS = 14


def _is_stale(att: Attendance, threshold_hours: int = STALE_SHIFT_HOURS) -> bool:
    """True if the shift is open and older than the threshold."""
    if not att.clock_in or att.clock_out:
        return False
    return (timezone.now() - att.clock_in) > timedelta(hours=threshold_hours)


@transaction.atomic
def close_one(att: Attendance, threshold_hours: int = STALE_SHIFT_HOURS,
              reason_override: str = None) -> bool:
    """
    Close a single stale Attendance row.

    Atomic: either everything succeeds or nothing changes. After commit,
    fires a Firebase event so the driver app and manager dashboard refresh.

    Returns True if the shift was closed by this call, False if it wasn't
    stale (or was already closed in a race).
    """
    # Re-read inside the transaction so two concurrent callers can't
    # both close the same row (the second will see clock_out already set).
    att = Attendance.objects.select_for_update().get(pk=att.pk)
    if not _is_stale(att, threshold_hours):
        return False

    # Zero-hour shift — forces the driver to file a correct end time
    # through the fix-request flow instead of silently getting paid for
    # hours we don't actually have evidence for.
    att.clock_out = att.clock_in
    att.auto_closed = True
    att.calculate_hours()
    att.save(update_fields=[
        'clock_out', 'auto_closed',
        'regular_hours', 'overtime_125_h', 'overtime_150_h',
    ])

    # Create a pending fix request so the manager sees it and the driver
    # can submit the real clock-out time. requested_clock_out is left
    # blank — driver fills it via the existing fix-request screen.
    reason = reason_override or (
        f"Auto-closed: shift exceeded {threshold_hours}h with no clock-out. "
        "Driver to confirm actual end time."
    )
    AttendanceFixRequest.objects.create(
        driver=att.driver,
        date=att.date,
        requested_clock_in=None,
        requested_clock_out=None,
        reason=reason,
        status='pending',
    )

    print(
        f"[AUTO-CLOSE] driver={att.driver_id} date={att.date} "
        f"clock_in={att.clock_in} stale_hours="
        f"{(timezone.now() - att.clock_in).total_seconds() / 3600:.1f}",
        flush=True,
    )

    # Fire events so the UI reflects the change immediately.
    # publish_event runs after the atomic block commits via on_commit.
    transaction.on_commit(lambda: _publish_events(att))
    return True


def _publish_events(att: Attendance):
    """Fire Firebase events for the auto-closed shift. Best-effort — never raises.

    Note: we send the event name only, not a payload. The mobile app's
    Firebase listener for 'attendance_auto_closed' should respond by
    re-fetching this driver's attendance state, which gives it the
    auto-closed row plus any associated fix request.
    """
    try:
        publish_event('attendance_changed', by_user_id=att.driver_id)
        publish_event('attendance_auto_closed', by_user_id=att.driver_id)
    except Exception as e:
        print(f"[AUTO-CLOSE] Firebase publish failed: {e}", flush=True)


def close_all_stale(threshold_hours: int = STALE_SHIFT_HOURS) -> int:
    """
    Find every open shift older than the threshold across all drivers
    and close each. Returns the number of shifts closed.

    Safe to call concurrently with itself or with the lazy-check
    callers — each row is closed under SELECT FOR UPDATE so duplicates
    are avoided.
    """
    cutoff = timezone.now() - timedelta(hours=threshold_hours)
    candidates = Attendance.objects.filter(
        clock_in__isnull=False,
        clock_in__lte=cutoff,
        clock_out__isnull=True,
    ).select_related('driver')

    closed = 0
    for att in candidates:
        try:
            if close_one(att, threshold_hours):
                closed += 1
        except Exception as e:
            print(f"[AUTO-CLOSE] Failed to close attendance {att.id}: {e}", flush=True)
    return closed


def close_stale_for_driver(driver, threshold_hours: int = STALE_SHIFT_HOURS) -> int:
    """
    Close stale shifts belonging to a specific driver.
    Used by lazy-check call sites (e.g. ClockInView) before they
    reject a clock-in for "already clocked in".

    Returns the number of shifts closed (usually 0 or 1).
    """
    cutoff = timezone.now() - timedelta(hours=threshold_hours)
    candidates = Attendance.objects.filter(
        driver=driver,
        clock_in__isnull=False,
        clock_in__lte=cutoff,
        clock_out__isnull=True,
    )

    closed = 0
    for att in candidates:
        try:
            if close_one(att, threshold_hours):
                closed += 1
        except Exception as e:
            print(f"[AUTO-CLOSE] Failed to close attendance {att.id}: {e}", flush=True)
    return closed