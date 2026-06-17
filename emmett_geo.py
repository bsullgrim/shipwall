#!/usr/bin/env python3
"""
emmett_geo.py -- shared helpers for the Where's-Emmett frame.

Pure-stdlib (json + math). No geopandas at runtime: the per-pixel lake
assignment is baked into lakes_mask.json by build_lakes_mask.py, so naming the
lake is a cheap nearest-water lookup.
"""
import json, math, os

_HERE = os.path.dirname(os.path.abspath(__file__))
MASK_PATH = os.environ.get("LAKES_MASK", os.path.join(_HERE, "lakes_mask.json"))

# Home: Traverse City, MI (Emmett's home; matches the cherry on the map)
HOME_NAME = "TRAVERSE"
HOME_LAT, HOME_LON = 44.7631, -85.6206

_mask = None
def mask():
    global _mask
    if _mask is None:
        with open(MASK_PATH) as f:
            _mask = json.load(f)
        _mask["_water_set"] = set(tuple(p) for p in _mask["water"])
    return _mask


def lonlat_to_xy(lon, lat):
    """Project lon/lat to integer (x,y) in mask space (0..w-1, 0..h-1)."""
    m = mask(); p = m["proj"]; W, H = m["w"], m["h"]
    fx = (lon - p["lon_min"]) / (p["lon_max"] - p["lon_min"])
    fy = (p["lat_max"] - lat) / (p["lat_max"] - p["lat_min"])
    x = int(round(fx * (W - 1)))
    y = int(round(fy * (H - 1)))
    return x, y


def in_window(lon, lat):
    m = mask(); p = m["proj"]
    return (p["lon_min"] <= lon <= p["lon_max"] and
            p["lat_min"] <= lat <= p["lat_max"])


def lake_name(lon, lat, max_r=4):
    """Name the lake at a lon/lat by nearest baked water pixel.
    Returns e.g. 'MICHIGAN', or '?' if no water within max_r pixels."""
    m = mask()
    lake_of = m.get("lake_of", {})
    wx, wy = lonlat_to_xy(lon, lat)
    best = None; bestd = 1e9
    for r in range(0, max_r + 1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if max(abs(dx), abs(dy)) != r:
                    continue
                k = "%d,%d" % (wx + dx, wy + dy)
                nm = lake_of.get(k)
                if nm and nm != "?":
                    d = dx*dx + dy*dy
                    if d < bestd:
                        bestd = d; best = nm
        if best is not None:
            return best
    return "?"


def haversine_mi(lat1, lon1, lat2, lon2):
    R = 3958.7613  # earth radius miles
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(min(1.0, math.sqrt(a)))


def dist_home_mi(lat, lon):
    return haversine_mi(lat, lon, HOME_LAT, HOME_LON)


# AIS navigational status codes -> short label
_NAV = {
    0: "UNDERWAY", 1: "ANCHORED", 2: "NOT UNDER CMD", 3: "RESTRICTED",
    4: "DRAFT LIMITED", 5: "MOORED", 6: "AGROUND", 7: "FISHING",
    8: "SAILING", 9: "HSC", 11: "TOWING ASTERN", 12: "TOWING AHEAD",
    14: "AIS-SART", 15: "UNDEFINED",
}

def nav_label(nav_status, sog):
    """Prefer the AIS status code, but fall back to / sanity-check against SOG,
    because crews often leave the status field stale.
    - If SOG is clearly moving (>0.5 kn) but status says moored/anchored,
      trust the motion: 'UNDERWAY'.
    - If SOG ~ 0 and status is missing/underway, call it 'STOPPED'."""
    moving = (sog is not None and sog > 0.5)
    label = _NAV.get(nav_status) if nav_status is not None else None
    if moving and label in (None, "MOORED", "ANCHORED", "UNDEFINED"):
        return "UNDERWAY"
    if not moving and label in (None, "UNDERWAY", "UNDEFINED"):
        return "STOPPED"
    return label or ("UNDERWAY" if moving else "STOPPED")


def summarize(state):
    """Turn a raw tracker state dict into display-ready fields."""
    lat, lon = state.get("lat"), state.get("lon")
    sog = state.get("sog")
    out = {
        "have_fix": lat is not None and lon is not None,
        "lake": "?",
        "nav": nav_label(state.get("nav_status"), sog),
        "sog": sog,
        "cog": state.get("cog"),
        "dist_home": None,
        "in_window": False,
        "xy": None,
        "age_s": None,
        "name": state.get("name"),
        "dest": state.get("dest"),
    }
    if out["have_fix"]:
        out["lake"] = lake_name(lon, lat)
        out["dist_home"] = dist_home_mi(lat, lon)
        out["in_window"] = in_window(lon, lat)
        out["xy"] = lonlat_to_xy(lon, lat)
    ts = state.get("ts")
    if ts:
        import time as _t
        out["age_s"] = max(0, _t.time() - ts)
    return out


# --- ports: registry, nearest-by-position, destination string matching -------
# lat/lon for each port that has a landmark icon, plus the keywords we look for
# in the AIS Destination free-text field to match it.
PORTS = {
    'duluth':         {'ll': (46.78, -92.10), 'kw': ['DULUTH', 'SUPERIOR WI']},
    'marquette_dock': {'ll': (46.54, -87.40), 'kw': ['MARQUETTE']},
    'soo':            {'ll': (46.50, -84.35), 'kw': ['SAULT', 'SOO', 'STE MARIE']},
    'detroit':        {'ll': (42.33, -83.05), 'kw': ['DETROIT']},
    'cleveland':      {'ll': (41.50, -81.69), 'kw': ['CLEVELAND']},
    'rochester':      {'ll': (43.21, -77.62), 'kw': ['ROCHESTER']},
    'shovel':         {'ll': (43.04, -87.95), 'kw': ['MILWAUKEE', 'USMKE', 'MKE']},
}

def nearest_port(lat, lon):
    """Return (port_key, distance_mi) of the closest landmark port."""
    best = None; bestd = 1e18
    for k, info in PORTS.items():
        plat, plon = info['ll']
        d = haversine_mi(lat, lon, plat, plon)
        if d < bestd:
            bestd = d; best = k
    return best, bestd

def dest_port(dest):
    """Match an AIS destination free-text string to a port key, or None.
    AIS destinations are messy ('DETROIT MI', '>CLEVELAND', 'SAULT STE.MARIE')."""
    if not dest:
        return None
    up = dest.upper()
    for k, info in PORTS.items():
        for kw in info['kw']:
            if kw in up:
                return k
    return None

def active_port_icon(state, near_radius_mi=60):
    """Decide which single port landmark icon to show, per the rule:
    destination port if known, else nearest port within near_radius_mi.
    Returns a port key or None."""
    dest = dest_port(state.get('dest'))
    if dest:
        return dest
    lat, lon = state.get('lat'), state.get('lon')
    if lat is None or lon is None:
        return None
    k, d = nearest_port(lat, lon)
    return k if d <= near_radius_mi else None
