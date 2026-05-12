"""
geo_utils.py — Small geometry helpers used across the app.

Kept minimal on purpose. If we ever need real spatial queries we'll
switch DriverLocation to use a PointField via PostGIS or similar, and
this module becomes redundant.
"""

import math


def haversine_meters(lat1, lng1, lat2, lng2) -> float:
    """
    Great-circle distance between two GPS points, in meters.
    Accepts Decimal or float for any input.

    Returns 0.0 if any coordinate is None (treats unknown as "didn't move").
    """
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return 0.0

    # Coerce Decimal → float without precision issues at GPS scale
    lat1 = float(lat1); lng1 = float(lng1)
    lat2 = float(lat2); lng2 = float(lng2)

    R = 6371000.0  # Earth's mean radius, meters
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)

    a = (math.sin(dphi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2)
    return 2 * R * math.asin(math.sqrt(a))