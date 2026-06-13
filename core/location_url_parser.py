"""
location_url_parser.py — Convert various location URL formats and coordinate
strings into (latitude, longitude) tuples.

Supported input formats (case-insensitive, whitespace-tolerant):

  Plain coords:        "32.7170, 35.3022"
                       "32.7170,35.3022"
                       "lat:32.7170 lng:35.3022"
  geo: URI:            "geo:32.7170,35.3022"
                       "geo:32.7170,35.3022?z=15"
  Google Maps long:    "https://www.google.com/maps/place/.../@32.7170,35.3022,17z/..."
                       "https://www.google.com/maps/@32.7170,35.3022,15z"
                       "https://www.google.com/maps?q=32.7170,35.3022"
                       "https://www.google.com/maps/dir/.../32.7170,35.3022"
  Google Maps short:   "https://maps.app.goo.gl/abc123"        (needs network)
                       "https://goo.gl/maps/abc123"            (needs network)
                       "https://g.co/kgs/abc123"               (needs network)
  Waze:                "https://waze.com/ul?ll=32.7170%2C35.3022&navigate=yes"
                       "https://ul.waze.com/ul?ll=..."
                       "waze://?ll=32.7170,35.3022"
  Apple Maps:          "https://maps.apple.com/?ll=32.7170,35.3022"
                       "https://maps.apple.com/?q=32.7170,35.3022"
                       "https://maps.apple.com/?daddr=32.7170,35.3022"
  OpenStreetMap:       "https://www.openstreetmap.org/?mlat=32.7170&mlon=35.3022"
                       "https://www.openstreetmap.org/#map=15/32.7170/35.3022"
  Bing Maps:           "https://www.bing.com/maps?cp=32.7170~35.3022"

API:
  parse(text)           -> (lat, lng) or None
  parse(text, expand_short=True)   -> follows short-URL redirects (HTTP HEAD)

Returns None for anything we can't recognize. Never raises.
"""

import re
from typing import Optional, Tuple

try:
    import requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False


# ──────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────

# Domains we recognize as Google Maps short-URL hosts.
SHORT_URL_HOSTS = (
    'maps.app.goo.gl',
    'goo.gl',
    'g.co',
    'app.goo.gl',
)

# HTTP timeout for short-URL expansion (seconds)
EXPAND_TIMEOUT = 6

# Reasonable lat/lng bounds — anything outside is parser noise, not a real coord
LAT_MIN, LAT_MAX = -90.0, 90.0
LNG_MIN, LNG_MAX = -180.0, 180.0


# ──────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────

def parse(text: str, expand_short: bool = True, geocode=None) -> Optional[Tuple[float, float]]:
    """
    Parse `text` (URL or coords) into a (lat, lng) tuple.

    If `expand_short` is True and the text is a known short-URL host,
    follows the redirect (HTTP HEAD) to get the long URL, then parses that.
    Requires `requests`; if not available, short URLs return None.

    Returns None for unrecognized input. Never raises.
    """
    if not text:
        return None
    s = text.strip()

    # Try every strategy in order. First hit wins.
    for strategy in (
        _try_plain_coords,
        _try_geo_uri,
        _try_at_pattern,            # Google's "/@lat,lng,zoom"
        _try_ll_param,              # Waze, Apple, others
        _try_query_param,           # ?q=lat,lng
        _try_osm_hash,
        _try_osm_mlat_mlon,
        _try_bing_cp,
        _try_daddr_param,           # Apple "daddr=lat,lng"
    ):
        result = strategy(s)
        if result is not None:
            return result

    # Place-share links (Google "Share" button) carry only a NAME + place-id,
    # never coordinates. If a geocoder was supplied, resolve the name to coords.
    if geocode is not None:
        name = extract_place_name(s)
        if name:
            try:
                gr = geocode(name)
            except Exception:
                gr = None
            if gr is not None:
                return gr

    # Last resort: short URLs need a network round-trip to expand.
    if expand_short and _is_short_url(s):
        expanded = _expand_short_url(s)
        if expanded and expanded != s:
            # Recurse once with expansion off so a chain of shorts can't loop
            return parse(expanded, expand_short=False, geocode=geocode)

    return None


# ──────────────────────────────────────────────────────────────────────────
# Strategies
# ──────────────────────────────────────────────────────────────────────────

# Tolerant coordinate pattern: optional sign, digits, decimal, more digits.
# Used as a building block in many strategies.
_COORD = r'(-?\d+(?:\.\d+)?)'

# Plain "lat, lng" or "lat,lng" — also catches "lat:N lng:M"
_PLAIN_RE = re.compile(
    rf'(?:lat\s*[:=]?\s*)?'
    rf'{_COORD}'
    rf'\s*[,\s]\s*'
    rf'(?:lng\s*[:=]?\s*|lon\s*[:=]?\s*)?'
    rf'{_COORD}'
    rf'(?:\s|$|[,;&?#])',
    re.IGNORECASE,
)

# Google's "@lat,lng,zoom" pattern in the path
_AT_RE = re.compile(rf'@{_COORD}\s*,\s*{_COORD}')

# ?ll=lat,lng (Waze, Apple) — URL-encoded comma %2C is decoded by urllib
_LL_RE = re.compile(rf'[?&#]ll=\s*{_COORD}\s*[,\s]\s*{_COORD}', re.IGNORECASE)

# ?q=lat,lng — Google fallback, Apple, etc.
_Q_RE = re.compile(rf'[?&#]q=\s*{_COORD}\s*[,\s]\s*{_COORD}', re.IGNORECASE)

# Apple ?daddr=lat,lng (driving destination)
_DADDR_RE = re.compile(rf'[?&#]daddr=\s*{_COORD}\s*[,\s]\s*{_COORD}', re.IGNORECASE)

# OSM hash: #map=zoom/lat/lng
_OSM_HASH_RE = re.compile(rf'#map=\d+(?:\.\d+)?/{_COORD}/{_COORD}', re.IGNORECASE)

# OSM ?mlat=...&mlon=...
_OSM_MLAT_RE = re.compile(rf'[?&]mlat={_COORD}', re.IGNORECASE)
_OSM_MLON_RE = re.compile(rf'[?&]mlon={_COORD}', re.IGNORECASE)

# Bing ?cp=lat~lng
_BING_CP_RE = re.compile(rf'[?&]cp={_COORD}~{_COORD}', re.IGNORECASE)

# Google "/maps/place/<NAME>/..." — the app's Share-button links carry only a
# place NAME (+ an internal place-id), no coordinates. We pull the name out so
# a geocoder can turn it into lat/lng.
_PLACE_RE = re.compile(r'/maps/place/([^/@?]+)', re.IGNORECASE)


def extract_place_name(text):
    """Pull a human place name out of a Google '/maps/place/<NAME>/' URL.
    Returns the decoded name (e.g. 'The Greek Orthodox Church ...') or None."""
    if not text:
        return None
    m = _PLACE_RE.search(text)
    if not m:
        return None
    import urllib.parse
    name = urllib.parse.unquote_plus(m.group(1)).replace('+', ' ').strip()
    # Guard: don't treat a coordinate-looking segment as a place name.
    if re.match(r'^-?\d+\.\d+\s*,', name):
        return None
    return name or None


def _coerce(lat_str: str, lng_str: str) -> Optional[Tuple[float, float]]:
    """Convert match strings to floats, bounds-check, return None on failure."""
    try:
        lat = float(lat_str)
        lng = float(lng_str)
    except (TypeError, ValueError):
        return None
    if not (LAT_MIN <= lat <= LAT_MAX and LNG_MIN <= lng <= LNG_MAX):
        return None
    # Reject "0, 0" — almost always a parser false-positive on a bare URL,
    # not a real destination off the coast of Africa.
    if lat == 0.0 and lng == 0.0:
        return None
    return (lat, lng)


def _try_plain_coords(s: str) -> Optional[Tuple[float, float]]:
    # Only apply to inputs that don't look like URLs.
    if '://' in s or s.lower().startswith(('http', 'www.', 'geo:', 'waze:')):
        return None
    m = _PLAIN_RE.search(s + ' ')   # trailing space helps the boundary
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


def _try_geo_uri(s: str) -> Optional[Tuple[float, float]]:
    if not s.lower().startswith('geo:'):
        return None
    rest = s[4:].split('?', 1)[0]
    parts = rest.split(',')
    if len(parts) >= 2:
        return _coerce(parts[0], parts[1])
    return None


def _try_at_pattern(s: str) -> Optional[Tuple[float, float]]:
    m = _AT_RE.search(s)
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


def _try_ll_param(s: str) -> Optional[Tuple[float, float]]:
    # URL-decode %2C → ',' so the regex works on encoded params
    decoded = s.replace('%2C', ',').replace('%2c', ',')
    m = _LL_RE.search(decoded)
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


def _try_query_param(s: str) -> Optional[Tuple[float, float]]:
    decoded = s.replace('%2C', ',').replace('%2c', ',')
    m = _Q_RE.search(decoded)
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


def _try_daddr_param(s: str) -> Optional[Tuple[float, float]]:
    decoded = s.replace('%2C', ',').replace('%2c', ',')
    m = _DADDR_RE.search(decoded)
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


def _try_osm_hash(s: str) -> Optional[Tuple[float, float]]:
    m = _OSM_HASH_RE.search(s)
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


def _try_osm_mlat_mlon(s: str) -> Optional[Tuple[float, float]]:
    lat_m = _OSM_MLAT_RE.search(s)
    lng_m = _OSM_MLON_RE.search(s)
    if lat_m and lng_m:
        return _coerce(lat_m.group(1), lng_m.group(1))
    return None


def _try_bing_cp(s: str) -> Optional[Tuple[float, float]]:
    m = _BING_CP_RE.search(s)
    if m:
        return _coerce(m.group(1), m.group(2))
    return None


# ──────────────────────────────────────────────────────────────────────────
# Short-URL expansion
# ──────────────────────────────────────────────────────────────────────────

def _is_short_url(s: str) -> bool:
    if '://' not in s:
        return False
    try:
        from urllib.parse import urlparse
        host = (urlparse(s).hostname or '').lower()
        return any(host == h or host.endswith('.' + h) for h in SHORT_URL_HOSTS)
    except Exception:
        return False


def _expand_short_url(s: str) -> Optional[str]:
    """
    Resolve a short URL to its final long form by following redirects.
    Returns the resolved URL on success, None on failure (network down,
    timeout, blocked, etc).
    """
    if not _REQUESTS_AVAILABLE:
        return None
    try:
        # GET (not HEAD) because some shorteners only respond to GET.
        # stream=True + immediate close means we don't actually download
        # the body — we just want the final URL after redirects.
        resp = requests.get(
            s,
            allow_redirects=True,
            timeout=EXPAND_TIMEOUT,
            stream=True,
            headers={'User-Agent': 'Mozilla/5.0 TruckForce/1.0'},
        )
        try:
            return resp.url
        finally:
            resp.close()
    except Exception:
        return None


# ──────────────────────────────────────────────────────────────────────────
# Self-test (run `python location_url_parser.py` to verify regex behaviour)
# ──────────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Cases that should parse to (32.717, 35.3022) ± floating-point noise.
    # Short URLs aren't tested here — they need network and don't have a
    # deterministic answer.
    cases = [
        ('32.717,35.3022',                                                          (32.717, 35.3022)),
        ('32.717, 35.3022',                                                         (32.717, 35.3022)),
        ('lat:32.717 lng:35.3022',                                                  (32.717, 35.3022)),
        ('geo:32.717,35.3022',                                                      (32.717, 35.3022)),
        ('geo:32.717,35.3022?z=15',                                                 (32.717, 35.3022)),
        ('https://www.google.com/maps/place/x/@32.717,35.3022,17z/data=!3m1!1e3',   (32.717, 35.3022)),
        ('https://www.google.com/maps/@32.717,35.3022,15z',                         (32.717, 35.3022)),
        ('https://www.google.com/maps?q=32.717,35.3022',                            (32.717, 35.3022)),
        ('https://waze.com/ul?ll=32.717%2C35.3022&navigate=yes',                    (32.717, 35.3022)),
        ('https://ul.waze.com/ul?ll=32.717,35.3022',                                (32.717, 35.3022)),
        ('waze://?ll=32.717,35.3022',                                               (32.717, 35.3022)),
        ('https://maps.apple.com/?ll=32.717,35.3022',                               (32.717, 35.3022)),
        ('https://maps.apple.com/?q=32.717,35.3022',                                (32.717, 35.3022)),
        ('https://maps.apple.com/?daddr=32.717,35.3022',                            (32.717, 35.3022)),
        ('https://www.openstreetmap.org/?mlat=32.717&mlon=35.3022',                 (32.717, 35.3022)),
        ('https://www.openstreetmap.org/#map=15/32.717/35.3022',                    (32.717, 35.3022)),
        ('https://www.bing.com/maps?cp=32.717~35.3022',                             (32.717, 35.3022)),
    ]
    fails = []
    for text, expected in cases:
        got = parse(text, expand_short=False)
        if got is None or abs(got[0] - expected[0]) > 1e-4 or abs(got[1] - expected[1]) > 1e-4:
            fails.append((text, expected, got))
    if fails:
        for text, expected, got in fails:
            print(f"FAIL: {text!r}  expected={expected}  got={got}")
        print(f"\n{len(fails)} of {len(cases)} cases failed")
    else:
        print(f"All {len(cases)} cases passed ✓")

    # Negative cases — should return None
    bad = [
        '',
        'not a url',
        'https://example.com',
        'lat:0 lng:0',                # 0,0 explicitly rejected
        'geo:abc,def',
        'https://www.google.com/',
    ]
    for s in bad:
        got = parse(s, expand_short=False)
        if got is not None:
            print(f"FALSE POSITIVE: {s!r}  got={got}")