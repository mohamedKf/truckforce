"""
core/arrival_detection.py — Auto-detect when a driver arrives at a stop.

Called from DriverLocationUpdateView on every location ping.
Strategy:
  1. Find today's schedule stops that are pending and have lat/lng
  2. For each, check distance to driver's current location
  3. If within ARRIVAL_RADIUS_METERS, record a "within-radius" ping
  4. If driver has 2+ within-radius pings spanning >= CONFIRM_SECONDS, mark arrived
"""

import math
from datetime import timedelta
from django.utils import timezone

ARRIVAL_RADIUS_METERS = 50   # within this distance = candidate arrival
CONFIRM_SECONDS       = 30   # must stay within radius at least this long


def haversine_distance_m(lat1, lng1, lat2, lng2) -> float:
    """Great-circle distance between two GPS points in meters."""
    try:
        lat1, lng1, lat2, lng2 = float(lat1), float(lng1), float(lat2), float(lng2)
    except (TypeError, ValueError):
        return float('inf')

    R = 6371000  # Earth radius in meters
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)

    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c


def check_arrivals_for_driver(driver, current_lat, current_lng):
    """
    Check if driver is at any pending stop.
    If within radius for CONFIRM_SECONDS+, mark arrival + notify manager.
    Returns list of Stop IDs where arrival was just confirmed.
    """
    from .models import DailySchedule, Stop, DriverLocation, Manager

    today = timezone.now().date()
    try:
        schedule = DailySchedule.objects.get(driver=driver, date=today)
    except DailySchedule.DoesNotExist:
        return []

    # Candidate stops: pending, have coordinates, not already arrived at
    pending_stops = schedule.stops.filter(
        status='pending',
        latitude__isnull=False,
        longitude__isnull=False,
        actual_arrival__isnull=True,
    )
    if not pending_stops.exists():
        return []

    now = timezone.now()
    newly_arrived = []

    for stop in pending_stops:
        distance = haversine_distance_m(
            current_lat, current_lng, stop.latitude, stop.longitude
        )

        if distance > ARRIVAL_RADIUS_METERS:
            continue

        # Driver is within radius RIGHT NOW. Check if they've been nearby for CONFIRM_SECONDS
        cutoff = now - timedelta(seconds=CONFIRM_SECONDS)
        recent_pings = DriverLocation.objects.filter(
            driver=driver,
            recorded_at__gte=cutoff,
        ).order_by('recorded_at')

        if recent_pings.count() < 2:
            continue  # Need at least 2 pings to confirm

        # Check if at least the earliest ping in the window was also within radius
        earliest = recent_pings.first()
        earliest_dist = haversine_distance_m(
            earliest.latitude, earliest.longitude,
            stop.latitude, stop.longitude
        )

        if earliest_dist > ARRIVAL_RADIUS_METERS:
            continue  # Driver just arrived, not yet confirmed

        # ── CONFIRMED ARRIVAL ──
        stop.actual_arrival = now
        stop.save(update_fields=['actual_arrival'])
        newly_arrived.append(stop.id)
        print(f"[ARRIVAL] driver={driver.full_name} arrived at '{stop.site_name}' "
              f"(dist={distance:.0f}m)", flush=True)

        # Notify managers
        try:
            _notify_managers_arrival(driver, stop)
        except Exception as e:
            print(f"[ARRIVAL] notification failed: {e}", flush=True)

    return newly_arrived


def _notify_managers_arrival(driver, stop):
    """Send WhatsApp/SMS/push notification to active managers."""
    from .models import Manager, NotificationLog

    # Build message
    msg = (f"🚚 {driver.full_name} הגיע ל-{stop.site_name}\n"
           f"שעה: {timezone.now().strftime('%H:%M')}")

    is_late = False
    if stop.expected_arrival:
        now_t = timezone.now().time()
        is_late = now_t > stop.expected_arrival
        if is_late:
            msg += f"\n⚠ באיחור (צפוי: {stop.expected_arrival.strftime('%H:%M')})"

    # Log notification to DB for audit
    for manager in Manager.objects.filter(is_active=True):
        NotificationLog.objects.create(
            manager=manager,
            driver=driver,
            kind='arrival',
            message=msg,
        )

    # TODO: Actually send via Twilio/UltraMsg — desktop-side messaging provider settings
    # For now just DB-log. Messaging dispatch will be wired via the existing notification system.