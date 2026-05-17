"""
route_optimizer.py — Mapbox-powered route optimization
Place in truckforce_backend/core/route_optimizer.py
"""

import requests
from django.conf import settings


MAPBOX_TOKEN = getattr(settings, 'MAPBOX_TOKEN', '')


def optimize_route(driver_lat, driver_lng, stops):
    """
    Optimize stop order using Mapbox Optimization API.

    Args:
        driver_lat: current driver latitude
        driver_lng: current driver longitude
        stops: list of Stop objects with latitude/longitude

    Returns:
        dict with:
            - ordered_stop_ids: list of stop IDs in optimal order
            - durations: list of estimated minutes between stops
            - total_duration_minutes: total trip duration
            - waypoints: list of {lat, lng, stop_id}
            - error: error message if failed
    """
    if not MAPBOX_TOKEN:
        return {'error': 'Mapbox token not configured', 'ordered_stop_ids': [s.id for s in stops]}

    # Filter stops that have coordinates
    valid_stops = [s for s in stops if s.latitude and s.longitude]
    if not valid_stops:
        return {'error': 'No stops have GPS coordinates', 'ordered_stop_ids': [s.id for s in stops]}

    # Build coordinates string: driver location first, then all stops
    coords = [f"{driver_lng},{driver_lat}"]
    for s in valid_stops:
        coords.append(f"{float(s.longitude)},{float(s.latitude)}")

    coordinates = ";".join(coords)

    # Mapbox Optimization API
    url = f"https://api.mapbox.com/optimized-trips/v1/mapbox/driving/{coordinates}"
    params = {
        'access_token':      MAPBOX_TOKEN,
        'source':            'first',   # Start from driver location
        'destination':       'last',    # End at last stop
        'roundtrip':         'false',
        'geometries':        'geojson',
        'overview':          'simplified',
    }

    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()

        if data.get('code') != 'Ok':
            print(f"[ROUTE] Mapbox error: {data.get('code')} — {data.get('message')}", flush=True)
            # Fallback to nearest neighbor
            return _nearest_neighbor(driver_lat, driver_lng, valid_stops)

        # Extract waypoint order from response
        waypoints = data.get('waypoints', [])
        trips     = data.get('trips', [])

        if not trips:
            return _nearest_neighbor(driver_lat, driver_lng, valid_stops)

        trip     = trips[0]
        duration = trip.get('duration', 0) / 60  # seconds → minutes

        # waypoints[0] is driver start, waypoints[1:] are stops in optimal order
        # waypoint_index tells us original position, trips give us the ordered waypoints
        ordered_indices = [w.get('waypoint_index', i) for i, w in enumerate(waypoints)]

        # Map back to stop IDs (skip index 0 which is driver location)
        ordered_stop_ids = []
        leg_durations    = []
        legs = trip.get('legs', [])

        for i, leg in enumerate(legs):
            wp_idx = ordered_indices[i + 1] - 1  # -1 because driver is index 0
            if 0 <= wp_idx < len(valid_stops):
                ordered_stop_ids.append(valid_stops[wp_idx].id)
                leg_durations.append(round(leg.get('duration', 0) / 60))

        print(f"[ROUTE] Optimized {len(valid_stops)} stops in {round(duration)} min", flush=True)

        return {
            'ordered_stop_ids':      ordered_stop_ids,
            'durations':             leg_durations,
            'total_duration_minutes': round(duration),
            'geometry':              trip.get('geometry'),
            'error':                 None,
        }

    except requests.exceptions.Timeout:
        print("[ROUTE] Mapbox timeout — falling back to nearest neighbor", flush=True)
        return _nearest_neighbor(driver_lat, driver_lng, valid_stops)
    except Exception as e:
        print(f"[ROUTE] Error: {e} — falling back to nearest neighbor", flush=True)
        return _nearest_neighbor(driver_lat, driver_lng, valid_stops)


def _nearest_neighbor(driver_lat, driver_lng, stops):
    """
    Fallback: simple nearest neighbor algorithm.
    Free, always works, ~85% as good as optimal for typical routes.
    """
    import math

    def dist(lat1, lng1, lat2, lng2):
        # Haversine distance in km
        R = 6371
        dlat = math.radians(lat2 - lat1)
        dlng = math.radians(lng2 - lng1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
        return R * 2 * math.asin(math.sqrt(a))

    remaining = list(stops)
    ordered   = []
    cur_lat, cur_lng = driver_lat, driver_lng

    while remaining:
        nearest = min(remaining, key=lambda s: dist(cur_lat, cur_lng, float(s.latitude), float(s.longitude)))
        ordered.append(nearest)
        cur_lat  = float(nearest.latitude)
        cur_lng  = float(nearest.longitude)
        remaining.remove(nearest)

    # Estimate 30 min average per stop
    durations = [30] * len(ordered)

    return {
        'ordered_stop_ids':      [s.id for s in ordered],
        'durations':             durations,
        'total_duration_minutes': len(ordered) * 30,
        'geometry':              None,
        'error':                 None,
        'method':                'nearest_neighbor',
    }


def calculate_eta(driver_lat, driver_lng, stops_ahead, target_stop_id):
    """
    Calculate ETA for a specific stop given driver location and stops ahead.
    Used for client tracking page.

    Returns: (min_eta_minutes, max_eta_minutes)
    """
    result = optimize_route(driver_lat, driver_lng, stops_ahead)
    if result.get('error'):
        # Rough estimate: 20 min per stop
        position = next((i for i, s in enumerate(stops_ahead) if s.id == target_stop_id), len(stops_ahead))
        est = position * 20
        return est - 10, est + 15

    ordered_ids = result.get('ordered_stop_ids', [])
    durations   = result.get('durations', [])

    # Sum durations up to and including target stop
    total = 0
    for i, stop_id in enumerate(ordered_ids):
        if i < len(durations):
            total += durations[i]
        if stop_id == target_stop_id:
            break

    # Add ±15 min buffer for traffic/loading
    return max(0, total - 10), total + 15


# ─────────────────────────────────────────────────────────────
# DEADLINE-AWARE POST-PROCESSING
# ─────────────────────────────────────────────────────────────

def _stop_lookup(stops):
    """Build {id: Stop} dict for quick access."""
    return {s.id: s for s in stops}


def _parse_window_dt(schedule_date, time_field):
    """Combine schedule date + a TimeField into a tz-aware datetime.

    Stop.expected_arrival is a TimeField like "14:30:00". Combined with the
    schedule's date and the local timezone, we get a real datetime for
    deadline comparisons.
    """
    if not time_field:
        return None
    from django.utils import timezone as tz
    import datetime as _dt
    dt = _dt.datetime.combine(schedule_date, time_field)
    if tz.is_naive(dt):
        dt = tz.make_aware(dt)
    return dt


def _haversine_min(lat1, lng1, lat2, lng2, avg_speed_kmh=45):
    """Rough driving-time estimate in minutes, using haversine + an average
    speed. We use this as a fallback to score alternative orderings without
    burning Mapbox calls on every permutation."""
    import math
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
           * math.sin(dlng / 2) ** 2)
    km = R * 2 * math.asin(math.sqrt(a))
    # +5 min per stop overhead (parking, walking to door, etc.) baked into
    # the speed assumption — a real-world delivery averages slower than the
    # raw road speed.
    return (km / avg_speed_kmh) * 60


def _check_violations(ordered_ids, lookup, driver_lat, driver_lng,
                      durations_min, schedule_date, stop_dwell_min=10,
                      start_time=None):
    """Walk through an ordered list and flag any stops whose expected_arrival
    would be missed given driving times + a per-stop dwell allowance.

    Args:
        ordered_ids: list of Stop IDs in proposed order
        lookup: {id: Stop} dict
        driver_lat/driver_lng: starting point
        durations_min: parallel list of leg durations between stops (mins).
                       If shorter than ordered_ids, we estimate the gap.
        schedule_date: the schedule's date for combining with TimeField
        stop_dwell_min: minutes spent at each stop (parking, handoff)
        start_time: when the driver starts (defaults to now)

    Returns:
        list of {stop_id, expected, predicted, delay_min} dicts, only for
        stops that arrive late.
    """
    from django.utils import timezone as tz
    if start_time is None:
        start_time = tz.now()

    violations = []
    cur = start_time
    prev_lat, prev_lng = driver_lat, driver_lng

    for i, sid in enumerate(ordered_ids):
        stop = lookup.get(sid)
        if stop is None:
            continue

        # Driving time to this stop
        if i < len(durations_min):
            drive_min = durations_min[i]
        elif stop.latitude and stop.longitude:
            drive_min = _haversine_min(prev_lat, prev_lng,
                                       float(stop.latitude), float(stop.longitude))
        else:
            drive_min = 0

        from datetime import timedelta as _td
        cur = cur + _td(minutes=drive_min)

        expected_dt = _parse_window_dt(schedule_date, stop.expected_arrival)
        if expected_dt and cur > expected_dt:
            late_min = round((cur - expected_dt).total_seconds() / 60)
            violations.append({
                'stop_id':       sid,
                'site_name':     stop.site_name,
                'expected':      stop.expected_arrival.strftime('%H:%M'),
                'predicted':     cur.strftime('%H:%M'),
                'delay_minutes': late_min,
            })

        # Add dwell at the stop before moving on
        cur = cur + _td(minutes=stop_dwell_min)
        if stop.latitude and stop.longitude:
            prev_lat = float(stop.latitude)
            prev_lng = float(stop.longitude)

    return violations


def _deadline_greedy(stops, driver_lat, driver_lng, schedule_date,
                     stop_dwell_min=10):
    """Greedy deadline-first scheduler. Picks the next stop by combining:
       - earliest expected_arrival wins, but
       - if the deadline is far away or unset, prefer the nearest pending stop.

    Specifically: filter stops to those with a deadline; among those, pick
    the EARLIEST. If none have a deadline, pick the NEAREST. After picking,
    if we'd still arrive on time at the earliest-deadline stop by detouring
    to a closer no-deadline stop first, take the detour.

    Not globally optimal (TSPTW is NP-hard) but produces sensible routes for
    small Israeli fleet schedules with a handful of windowed stops.
    """
    from django.utils import timezone as tz
    remaining = list(stops)
    ordered = []
    cur_lat, cur_lng = driver_lat, driver_lng
    cur_time = tz.now()
    leg_durations = []

    from datetime import timedelta as _td

    while remaining:
        # Estimated drive time from current position to each candidate
        candidates = []
        for s in remaining:
            if s.latitude is None or s.longitude is None:
                continue
            drive_min = _haversine_min(cur_lat, cur_lng,
                                       float(s.latitude), float(s.longitude))
            arrival = cur_time + _td(minutes=drive_min)
            deadline = _parse_window_dt(schedule_date, s.expected_arrival)
            candidates.append({
                'stop':      s,
                'drive_min': drive_min,
                'arrival':   arrival,
                'deadline':  deadline,
            })

        if not candidates:
            # No remaining stops have coordinates — append in original order
            ordered.extend(remaining)
            break

        # Step 1: do any candidates have a deadline?
        windowed = [c for c in candidates if c['deadline'] is not None]
        if windowed:
            # Pick the one with the earliest deadline (most urgent).
            pick = min(windowed, key=lambda c: c['deadline'])
            # But: if there's a no-window stop closer to us that we could
            # detour through without missing the picked deadline, take that
            # detour first (better fuel efficiency, same on-time delivery).
            no_window = [c for c in candidates
                         if c['deadline'] is None and c['stop'] != pick['stop']]
            for nw in no_window:
                # Time after the detour
                after_detour = cur_time + _td(minutes=nw['drive_min'] + stop_dwell_min)
                # Drive from detour to the deadline stop
                drive2 = _haversine_min(
                    float(nw['stop'].latitude), float(nw['stop'].longitude),
                    float(pick['stop'].latitude), float(pick['stop'].longitude),
                )
                est_deadline_arrival = after_detour + _td(minutes=drive2)
                if est_deadline_arrival <= pick['deadline']:
                    pick = nw
                    break
        else:
            # No deadlines — nearest-neighbor.
            pick = min(candidates, key=lambda c: c['drive_min'])

        ordered.append(pick['stop'])
        leg_durations.append(round(pick['drive_min']))
        cur_lat = float(pick['stop'].latitude)
        cur_lng = float(pick['stop'].longitude)
        cur_time = cur_time + _td(minutes=pick['drive_min'] + stop_dwell_min)
        remaining.remove(pick['stop'])

    total = sum(leg_durations) + (len(ordered) * stop_dwell_min)
    return {
        'ordered_stop_ids':       [s.id for s in ordered],
        'durations':              leg_durations,
        'total_duration_minutes': total,
        'geometry':               None,
        'error':                  None,
        'method':                 'deadline_greedy',
    }


def apply_deadline_constraints(mapbox_result, stops, driver_lat, driver_lng,
                               schedule_date):
    """Check the Mapbox-suggested order for time-window violations. If any
    stop with a hard expected_arrival would be late, recompute using the
    deadline-aware greedy. Otherwise return Mapbox's result unchanged.

    Returns:
        (result_dict, violations_list)
        result_dict matches optimize_route()'s output.
        violations_list is the list of late stops in the FINAL chosen order
        (empty if all deadlines are met, or if no deadlines exist).
    """
    lookup = _stop_lookup(stops)
    ordered_ids = mapbox_result.get('ordered_stop_ids', [])
    durations = mapbox_result.get('durations', [])

    # If no stops have deadlines, there's nothing to enforce.
    any_deadline = any(
        s.expected_arrival is not None
        for s in stops
    )
    if not any_deadline:
        return mapbox_result, []

    # Check whether Mapbox's order would miss any deadlines.
    violations = _check_violations(
        ordered_ids, lookup, driver_lat, driver_lng,
        durations, schedule_date,
    )
    if not violations:
        # Mapbox happened to give us a deadline-safe order — use it.
        return mapbox_result, []

    # Recompute using deadline-aware greedy.
    print(f"[ROUTE] Mapbox order has {len(violations)} deadline violations — "
          f"falling back to deadline-aware greedy", flush=True)
    greedy = _deadline_greedy(stops, driver_lat, driver_lng, schedule_date)
    # Re-check violations on the greedy result (some might be unavoidable).
    greedy_violations = _check_violations(
        greedy['ordered_stop_ids'], lookup, driver_lat, driver_lng,
        greedy['durations'], schedule_date,
    )
    return greedy, greedy_violations