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