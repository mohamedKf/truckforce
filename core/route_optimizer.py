"""
route_optimizer.py — Mapbox-powered route optimization
Place in truckforce_backend/core/route_optimizer.py
"""

import requests
from django.conf import settings


MAPBOX_TOKEN = getattr(settings, 'MAPBOX_TOKEN', '')


def optimize_route(driver_lat, driver_lng, stops, deadline_aware=False, schedule_date=None):
    """Pick the next-closest stop, repeatedly.

    This is a deterministic nearest-neighbor algorithm. Starting from the
    driver's GPS location, we ask Mapbox for the full travel-time matrix
    between every point (driver + all stops). Then we greedily pick the
    closest unvisited stop, jump to it, and repeat until done.

    Why nearest-neighbor and not Mapbox's own /optimized-trips endpoint?
    Two reasons:
      1. Drivers think about routes this way: "what's closest from here?"
         The result is intuitive and predictable. Mapbox's TSP solver can
         shuffle stops in surprising orders.
      2. Same input always produces the same output. Mapbox's optimization
         occasionally returns different orders for the same input — bad
         for a manager comparing routes side by side.

    Args:
        driver_lat, driver_lng: starting GPS position
        stops: list of Stop ORM objects (need .id, .latitude, .longitude;
               optionally .expected_arrival for deadline-aware mode)
        deadline_aware: when True, a stop with `expected_arrival` will
               jump the queue if its time window is at risk (< 30 min
               buffer) — see _pick_next() for details
        schedule_date: required when deadline_aware=True so we can
               combine TimeField with date into real datetimes

    Returns:
        dict with:
            - ordered_stop_ids: list of stop IDs in chosen visit order
            - durations: per-leg minutes (parallel to ordered_stop_ids)
            - total_duration_minutes: sum of all legs + per-stop dwell
            - method: 'matrix_nearest_neighbor' on success, fallback name
                     otherwise
            - error: None on success, message on failure
    """
    # Filter stops that have coordinates — anything without lat/lng can't
    # be on the map, much less optimized.
    valid_stops = [s for s in stops if s.latitude and s.longitude]
    if not valid_stops:
        return {
            'error': 'No stops have GPS coordinates',
            'ordered_stop_ids': [s.id for s in stops],
            'durations': [],
            'total_duration_minutes': 0,
            'method': 'no_coords',
        }

    if not MAPBOX_TOKEN:
        # No token — fall back to straight-line nearest-neighbor so the
        # feature still works offline / in tests.
        print("[ROUTE] No Mapbox token, using haversine nearest-neighbor", flush=True)
        return _nearest_neighbor(driver_lat, driver_lng, valid_stops)

    # Mapbox Matrix API caps at 25 coordinates per call. With driver + N
    # stops, that's 24 stops max per route. Realistic for daily delivery
    # routes; if someone has 30 stops we cleanly fall back to haversine.
    if len(valid_stops) + 1 > 25:
        print(f"[ROUTE] {len(valid_stops)} stops exceeds Matrix limit (25), "
              f"falling back to haversine", flush=True)
        return _nearest_neighbor(driver_lat, driver_lng, valid_stops)

    # Build the coordinate list. Index 0 = driver start, 1..N = stops in
    # the order they appear in valid_stops (we'll permute later).
    coords = [(driver_lng, driver_lat)]
    for s in valid_stops:
        coords.append((float(s.longitude), float(s.latitude)))

    matrix = _fetch_driving_matrix(coords)
    if matrix is None:
        print("[ROUTE] Matrix API failed, falling back to haversine", flush=True)
        return _nearest_neighbor(driver_lat, driver_lng, valid_stops)

    # Now run nearest-neighbor against the matrix. Optionally tilt picks
    # toward windowed stops with tight deadlines.
    ordered_indices, leg_durations_sec = _nearest_neighbor_on_matrix(
        matrix,
        valid_stops,
        deadline_aware=deadline_aware,
        schedule_date=schedule_date,
    )

    # Translate matrix indices (1..N) back to Stop ORM objects.
    ordered_stop_ids = [valid_stops[i - 1].id for i in ordered_indices]
    leg_durations    = [round(s / 60) for s in leg_durations_sec]
    # 10 minutes dwell at each stop — matches our ETA assumption elsewhere.
    DWELL_PER_STOP_MIN = 10
    total_min = sum(leg_durations) + DWELL_PER_STOP_MIN * len(ordered_stop_ids)

    print(f"[ROUTE] NN-on-matrix: {len(valid_stops)} stops, "
          f"{total_min} min total (deadline_aware={deadline_aware})",
          flush=True)

    return {
        'ordered_stop_ids':       ordered_stop_ids,
        'durations':              leg_durations,
        'total_duration_minutes': total_min,
        'geometry':               None,  # Matrix API doesn't return polylines
        'method':                 'matrix_nearest_neighbor',
        'error':                  None,
    }


def _fetch_driving_matrix(coords):
    """Call Mapbox Matrix API once for all-to-all driving durations.

    Args:
        coords: list of (lng, lat) tuples, max 25

    Returns:
        A 2D list `m[i][j] = seconds to drive from coords[i] to coords[j]`,
        or None on any error so the caller can fall back.
    """
    coord_str = ";".join(f"{lng},{lat}" for lng, lat in coords)
    url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coord_str}"
    params = {
        'access_token': MAPBOX_TOKEN,
        # 'annotations=duration' is the default but being explicit helps
        # avoid accidental quota usage if defaults change upstream.
        'annotations':  'duration',
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        data = resp.json()
        if data.get('code') != 'Ok':
            print(f"[ROUTE] Matrix API error: {data.get('code')} — "
                  f"{data.get('message')}", flush=True)
            return None
        durations = data.get('durations')
        if not durations or len(durations) != len(coords):
            return None
        # Sanity check — durations may contain None for unreachable pairs
        # (e.g. islands without ferry data). Replace with a large penalty
        # so nearest-neighbor still picks something rather than crashing.
        BIG = 10 ** 9
        cleaned = [
            [(BIG if v is None else float(v)) for v in row]
            for row in durations
        ]
        return cleaned
    except requests.exceptions.Timeout:
        print("[ROUTE] Matrix API timeout", flush=True)
        return None
    except Exception as e:
        print(f"[ROUTE] Matrix API exception: {e}", flush=True)
        return None


def _nearest_neighbor_on_matrix(matrix, valid_stops, deadline_aware=False,
                                schedule_date=None,
                                deadline_buffer_min=30):
    """Run nearest-neighbor against a pre-computed travel-time matrix.

    Indices: matrix[0] is driver start, matrix[1..N] are stops (matching
    valid_stops[0..N-1]). We start at index 0 and visit every other index
    exactly once, picking the lowest travel time each step.

    Deadline behaviour (only when `deadline_aware=True`):
        Before picking the regular nearest, we check whether any unvisited
        stop with an `expected_arrival` time is at risk of being missed —
        defined as "less than `deadline_buffer_min` minutes of slack
        remaining if we waited any longer". If yes, we jump straight to
        the most-urgent such stop instead of the nearest one.

    Returns:
        (ordered_indices, leg_durations_seconds)
            ordered_indices  — visit order using matrix indices (1..N)
            leg_durations_seconds — parallel list of travel time per leg
    """
    from django.utils import timezone as tz
    from datetime import datetime as _dt, timedelta as _td

    n_stops = len(valid_stops)
    unvisited = set(range(1, n_stops + 1))  # matrix indices 1..N
    ordered = []
    leg_durations = []

    current = 0  # start at driver
    cur_time = tz.now() if deadline_aware else None
    DWELL_MIN = 10

    while unvisited:
        nearest_idx = min(unvisited, key=lambda i: matrix[current][i])
        chosen = nearest_idx

        # Deadline override: if a windowed stop is at risk, take it now.
        if deadline_aware and schedule_date is not None:
            urgent = _find_urgent_stop(
                unvisited, matrix, current, cur_time,
                valid_stops, schedule_date, deadline_buffer_min,
            )
            if urgent is not None:
                chosen = urgent

        leg_sec = matrix[current][chosen]
        ordered.append(chosen)
        leg_durations.append(leg_sec)
        unvisited.discard(chosen)

        # Advance the simulated clock for the next deadline check.
        if deadline_aware and cur_time is not None:
            cur_time = cur_time + _td(seconds=leg_sec) + _td(minutes=DWELL_MIN)

        current = chosen

    return ordered, leg_durations


def _find_urgent_stop(unvisited, matrix, current, cur_time, valid_stops,
                      schedule_date, deadline_buffer_min):
    """Return the matrix-index of the most-urgent windowed stop, if any
    deadline is at risk of being missed. Otherwise return None.

    "At risk" means: if we picked this stop NOW, we'd arrive with less
    than `deadline_buffer_min` minutes to spare (or already be late).

    Among at-risk stops we pick the one with the EARLIEST deadline —
    skipping it would be unrecoverable; skipping a later-deadline stop
    leaves more flexibility.
    """
    from django.utils import timezone as tz
    from datetime import datetime as _dt, timedelta as _td

    best_idx = None
    best_deadline = None
    for idx in unvisited:
        stop = valid_stops[idx - 1]
        if not stop.expected_arrival:
            continue
        # Combine schedule date + TimeField into a tz-aware datetime
        naive = _dt.combine(schedule_date, stop.expected_arrival)
        deadline_dt = tz.make_aware(naive) if tz.is_naive(naive) else naive
        # Predicted arrival if we drove straight to this stop now
        arrive_dt = cur_time + _td(seconds=matrix[current][idx])
        slack_min = (deadline_dt - arrive_dt).total_seconds() / 60
        if slack_min < deadline_buffer_min:
            # Among at-risk stops, take the earliest deadline
            if best_deadline is None or deadline_dt < best_deadline:
                best_deadline = deadline_dt
                best_idx = idx
    return best_idx



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