#!/usr/bin/env python3
"""
register_service.py  --  "Recent sightings register" variant of the Ship Wall.

Instead of a live radar of what's in the American Narrows right now (see
shipwall_service.py for that), this keeps a rolling register of every vessel
seen anywhere in the outer box over the last N hours, deduped to the most
recent sighting per ship, each stamped with how long ago it was seen. This
suits spotty AIS coverage: the live view is empty most of the time, but a
12-24h register is almost always populated -- you glance out the window, see a
laker, then check the wall to learn who it was and where it was bound.

The display (register_panel.py / register_esp32.ino) shows two modes:
  - BOARD: a departure-board list, one line per ship (chip + code + dir +
    name + "Xh ago"), scrolling if there are more than fit.
  - DETAIL: cycles through ships one at a time, each shown large with the full
    set of AIS fields (dimensions, flag, nav status, destination, ETA, ...).

Shares the funnel sprites, operator identification, and brightness schedule
with the live version; only the retention model and frame shape differ.
"""

import asyncio
import csv as _csv
import datetime as _dt
import json
import os
import sys
import time

import aiohttp
import websockets

from operators import operator_for, operator_code, log_unknown
from schedule import target_brightness, seaway_closed

# --- .env loader (same convenience as the live service) ----------------------
def _load_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                os.environ.setdefault(k.strip(), val.strip())
_load_env()

API_KEY = os.environ.get("AISSTREAM_KEY", "")
if not API_KEY:
    print("ERROR: set AISSTREAM_KEY (in .env or environment).")
    sys.exit(1)

# Transport to the renderer. Set ESP32_SERIAL=/dev/ttyACM0 (or COM3 on Windows)
# to push frames over USB serial to the MatrixPortal; otherwise frames are
# HTTP-POSTed to ESP32_HOST (used by the browser mock). Serial takes priority.
ESP32_SERIAL = os.environ.get("ESP32_SERIAL", "").strip()
SERIAL_BAUD = int(os.environ.get("ESP32_BAUD", "115200"))
ESP32_HOST = os.environ.get("ESP32_HOST", "localhost:8080")
ESP32_URL = f"http://{ESP32_HOST}/frame"

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

WS_URL = "wss://stream.aisstream.io/v0/stream"

# Outer box: Cape Vincent -> Eisenhower Lock (same reach as the live version).
BOUNDING_BOX = [[[44.10, -76.40], [45.3237, -73.9132]]]

PUSH_INTERVAL  = 10                                   # seconds between frames
RETAIN_HOURS   = float(os.environ.get("REGISTER_HOURS", "18"))
RETAIN_SECS    = RETAIN_HOURS * 3600
MIN_SPEED_KTS  = 0.3
DEBUG = os.environ.get("SHIPWALL_DEBUG", "") not in ("", "0", "false", "False")

# Set REGISTER_LOG=path.csv to record every vessel the feed delivers -- logged
# once when first seen and again when its name/operator resolves. Good seed
# data for building the MMSI->operator table, and a coverage record. Appends
# across restarts.
VESSEL_LOG = os.environ.get("REGISTER_LOG", "").strip()

# Set PASSAGE_LOG=path.csv to log vessels that pass Danger Island. We can't
# detect ships at Danger Island directly (no coverage there), so we infer a
# passage when a ship's river-progress crosses the home point between two
# sightings -- seen above, later seen below (or vice versa) means it transited
# past us. Direction comes from which way it crossed. Appends across restarts.
PASSAGE_LOG = os.environ.get("PASSAGE_LOG", "").strip()

# Set MMSI_DB=path.json to maintain a persistent MMSI -> identity database.
# Every time a static AIS message resolves a vessel, its name/operator/type/
# dimensions/flag are upserted (freshest wins). On startup the DB is loaded and
# used to pre-fill known MMSIs, so a vessel we've seen before is identified the
# moment its position arrives -- no waiting for this session's static message,
# and far fewer "ghost" entries over time.
MMSI_DB_PATH = os.environ.get("MMSI_DB", "").strip()

# --- Ship type + nav status + country (mirrors the live service) -------------
SHIP_TYPES = {
    range(30, 40): "FISH", range(40, 50): "HSC", range(50, 51): "PILOT",
    range(52, 53): "TUG", range(60, 70): "PAX", range(70, 80): "CARGO",
    range(80, 90): "TANKER",
}
def ship_type_label(code):
    if code is None:
        return "VSL"
    for r, label in SHIP_TYPES.items():
        if code in r:
            return label
    return "VSL"

NAV_STATUS = {
    0: "UNDERWAY", 1: "ANCHORED", 2: "NOT UNDER CMD", 3: "RESTRICTED",
    4: "DRAFT LIMITED", 5: "MOORED", 6: "AGROUND", 7: "FISHING",
    8: "SAILING", 11: "TOWING", 12: "PUSHING",
}
def nav_status_label(code):
    if code is None or code == 15:
        return None
    return NAV_STATUS.get(code)

MID_COUNTRY = {
    "316": "CA", "338": "US", "366": "US", "367": "US", "368": "US",
    "369": "US", "303": "US", "319": "KY", "229": "MT", "248": "MT",
    "256": "MT", "215": "MT", "538": "MH", "636": "LR", "637": "LR",
    "311": "BS", "309": "BS", "308": "BS", "373": "PA", "374": "PA",
    "351": "PA", "352": "PA", "353": "PA", "354": "PA", "355": "PA",
    "356": "PA", "357": "PA", "370": "PA", "371": "PA", "372": "PA",
    "244": "NL", "245": "NL", "246": "NL", "232": "GB", "233": "GB",
    "234": "GB", "235": "GB", "563": "SG", "564": "SG", "565": "SG",
    "566": "SG", "477": "HK", "209": "CY", "210": "CY", "212": "CY",
}
def country_for(mmsi):
    s = str(mmsi)
    return MID_COUNTRY.get(s[:3]) if len(s) >= 3 else None


# --- Position along the river (for the detail card's progress line) ----------
# The reach is ~linear; project a lat/lon onto the axis from Lake Ontario
# (Cape Vincent) to the downriver box edge (Snell/Beauharnois lock) and report
# progress 0.0 (lake) .. 1.0 (lock). Avoids pulling in Montreal harbor traffic.
RIVER_LO  = (44.13, -76.37)            # Lake Ontario end (Cape Vincent)
RIVER_MTL = (45.3237, -73.9132)        # downriver end (lock, box NE corner)
# Your reference point on the river -- Danger Island, 233 Riverledge Road.
# 44°30'00.0"N 75°36'00.0"W
HOME_POINT = (44.5000, -75.6000)


def river_progress(lat, lon):
    """Fraction 0..1 of the way from the Lake Ontario end to Montreal,
    by projecting (lat,lon) onto the LO->MTL axis. None if no position."""
    if lat is None or lon is None:
        return None
    ax, ay = RIVER_LO[1], RIVER_LO[0]          # x=lon, y=lat
    bx, by = RIVER_MTL[1], RIVER_MTL[0]
    px, py = lon, lat
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom == 0:
        return 0.0
    t = ((px - ax) * dx + (py - ay) * dy) / denom
    return max(0.0, min(1.0, t))


HOME_PROGRESS = river_progress(HOME_POINT[0], HOME_POINT[1])


# Destination ports along the river, as approximate progress 0 (lake) .. 1
# (lock/sea). Used to infer direction for a sighting that lacks COG -- e.g. an
# older logged row -- by comparing where the ship is to where it's headed.
_DEST_PROGRESS = {
    # toward the lakes / upbound (low progress)
    "THUNDER BAY": 0.0, "SAULT STE MARIE": 0.0, "USSAW": 0.0, "DETROIT": 0.0,
    "USDET": 0.0, "SARNIA": 0.0, "CLEVELAND": 0.0, "USCLE": 0.0, "NANTICOKE": 0.0,
    "NANICOKE": 0.0, "HAMILTON": 0.05, "CAHAM": 0.05, "OSWEGO": 0.05,
    "TORONTO": 0.06, "PORT WELLER": 0.05, "KINGSTON": 0.08, "CATCY": 0.0,
    # toward the sea / downbound (high progress)
    "MONTREAL": 1.0, "CA MTR": 1.0, "CAMTR": 1.0, "VALLEYFIELD": 0.95,
    "SOREL": 1.0, "SORREL": 1.0, "QUEBEC": 1.0, "QUEBEC CITY": 1.0,
    "PORT CARTIER": 1.0, "SEPT-ILES": 1.0, "HALIFAX": 1.0, "USASF": 1.0,
    "CATHU": 1.0, "US LDM": 1.0, "USLDM": 1.0, "VALLEYFIELD QC": 0.95,
}


def dest_progress(dest):
    if not dest:
        return None
    key = dest.strip().upper()
    if key in _DEST_PROGRESS:
        return _DEST_PROGRESS[key]
    for k, v in _DEST_PROGRESS.items():
        if key.startswith(k) or k in key:
            return v
    return None


def dir_from_dest(lat, lon, dest):
    """Infer 'D'/'U' from where the ship is vs. where it's headed. None if the
    destination is unknown or too close to the ship's current progress."""
    here = river_progress(lat, lon)
    there = dest_progress(dest)
    if here is None or there is None or abs(there - here) < 0.03:
        return None
    return "D" if there > here else "U"


# --- Passage detection (crossings of Danger Island) --------------------------
# Track each MMSI's last river-progress + time; when a new sighting lands on the
# opposite side of HOME_PROGRESS, the ship transited past us.
_last_progress = {}            # mmsi -> (progress, observed_time)
_passage_header_written = False


def _log_passage(mmsi, name, op, direction, t_before, p_before, t_after, p_after):
    global _passage_header_written
    # Estimate the pass time by linear interpolation to the home crossing.
    span = (p_after - p_before)
    if span != 0:
        frac = (HOME_PROGRESS - p_before) / span
        t_pass = t_before + frac * (t_after - t_before)
    else:
        t_pass = (t_before + t_after) / 2
    row = {
        "pass_time": _dt.datetime.fromtimestamp(t_pass).isoformat(timespec="seconds"),
        "mmsi": mmsi,
        "name": name or f"MMSI {mmsi}",
        "operator": op,
        "direction": direction,          # "downbound" / "upbound"
        "seen_before": _dt.datetime.fromtimestamp(t_before).isoformat(timespec="seconds"),
        "seen_after": _dt.datetime.fromtimestamp(t_after).isoformat(timespec="seconds"),
        "gap_min": round((t_after - t_before) / 60.0, 1),
    }
    try:
        write_header = not _passage_header_written and not os.path.exists(PASSAGE_LOG)
        with open(PASSAGE_LOG, "a", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(row.keys()))
            if write_header:
                w.writeheader()
            w.writerow(row)
        _passage_header_written = True
        print(f"[passage] {row['name']} {direction} "
              f"(gap {row['gap_min']}m)")
    except Exception as e:
        print(f"[passage] could not write {PASSAGE_LOG}: {e}")


def check_passage(mmsi, lat, lon, observed, name, op):
    """Detect a crossing of HOME_PROGRESS between consecutive sightings."""
    if not PASSAGE_LOG or HOME_PROGRESS is None:
        return
    p = river_progress(lat, lon)
    if p is None:
        return
    prev = _last_progress.get(mmsi)
    _last_progress[mmsi] = (p, observed)
    if prev is None:
        return
    p_prev, t_prev = prev
    # Crossed the home point if the two sightings straddle HOME_PROGRESS.
    if (p_prev < HOME_PROGRESS <= p) :
        _log_passage(mmsi, name, op, "downbound", t_prev, p_prev, observed, p)
    elif (p_prev > HOME_PROGRESS >= p):
        _log_passage(mmsi, name, op, "upbound", t_prev, p_prev, observed, p)


def bound_dir(cog, sog):
    """NE course = downbound (D, toward sea), SW = upbound (U), slow = moored
    (M). Returns '?' when we have no velocity data at all -- e.g. a vessel
    warm-started from the register, which stores identity but not course/speed.
    Such a ship was underway when logged; we just don't know its direction
    until a live position arrives, so don't mislabel it as moored."""
    if cog is None and sog is None:
        return "?"                       # seeded / no live position yet
    if (sog or 0) < MIN_SPEED_KTS:
        return "M"                       # observed near-zero speed = moored
    if cog is None:
        return "?"
    c = cog % 360
    return "D" if (c >= 315 or c < 135) else "U"


def _dir_for(v):
    """Best available direction for a vessel: real course if we have it,
    else inferred from destination + position (for seeded/old rows), else '?'."""
    d = bound_dir(v.get("cog"), v.get("sog"))
    if d in ("D", "U", "M"):
        return d
    inferred = dir_from_dest(v.get("lat"), v.get("lon"), v.get("dest"))
    return inferred or "?"


# --- Vessel register ----------------------------------------------------------
# mmsi -> merged dict; "last" is the most recent sighting time (AIS observation
# time when available, else local receive time).
#
# NOTE: we subscribe to PositionReport + ShipStaticData only -- these are the
# Class A messages broadcast by big commercial ships (lakers, salties), which
# is exactly the traffic this wall is about. Smaller craft (some tugs, pleasure
# boats) transmit Class B (Standard/ExtendedClassBPositionReport), which we
# deliberately do NOT request, to keep the register to big ships.
vessels = {}


# --- Persistent MMSI identity database ---------------------------------------
# mmsi (str) -> {name, operator, code, flag, type, type_code, length, beam,
#               updated}. Grows as static AIS messages resolve vessels; used to
# identify known MMSIs on sight in future sessions.
mmsi_db = {}


def load_mmsi_db():
    if not MMSI_DB_PATH or not os.path.exists(MMSI_DB_PATH):
        return
    try:
        with open(MMSI_DB_PATH) as f:
            data = json.load(f)
        if isinstance(data, dict):
            mmsi_db.update(data)
            print(f"[mmsidb] loaded {len(mmsi_db)} known vessels from {MMSI_DB_PATH}")
    except Exception as e:
        print(f"[mmsidb] could not load {MMSI_DB_PATH}: {e}")


_mmsi_db_dirty = False


def save_mmsi_db():
    if not MMSI_DB_PATH or not _mmsi_db_dirty:
        return
    try:
        tmp = MMSI_DB_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(mmsi_db, f, indent=2, sort_keys=True)
        os.replace(tmp, MMSI_DB_PATH)
    except Exception as e:
        print(f"[mmsidb] could not save {MMSI_DB_PATH}: {e}")


def db_upsert(mmsi, v, op, code, flag):
    """Record/refresh a vessel's identity in the persistent DB (freshest wins).
    Only stores stable identity fields -- not position or destination."""
    global _mmsi_db_dirty
    if not MMSI_DB_PATH:
        return
    name = (v.get("name") or "").strip()
    if not name:                       # only store once we actually know who it is
        return
    key = str(mmsi)
    rec = {
        "name": name,
        "operator": op,
        "code": code,
        "flag": flag or "",
        "type": v.get("type", ""),
        "type_code": v.get("type_code"),
        "length": v.get("length"),
        "beam": v.get("beam"),
        "updated": _dt.datetime.now().isoformat(timespec="seconds"),
    }
    if mmsi_db.get(key) != rec:
        # compare ignoring the timestamp so we don't churn on identical data
        prev = dict(mmsi_db.get(key, {})); prev.pop("updated", None)
        cmp = dict(rec); cmp.pop("updated", None)
        if prev != cmp:
            mmsi_db[key] = rec
            _mmsi_db_dirty = True


def db_prefill(mmsi):
    """If we already know this MMSI from a past session, seed its identity into
    the live vessel record so it shows up named immediately (no ghost)."""
    rec = mmsi_db.get(str(mmsi))
    if not rec:
        return
    v = vessels.setdefault(mmsi, {"mmsi": mmsi})
    v.setdefault("name", rec.get("name"))
    v.setdefault("type", rec.get("type") or "VSL")
    if rec.get("type_code") is not None:
        v.setdefault("type_code", rec.get("type_code"))
    if rec.get("length"):
        v.setdefault("length", rec["length"])
    if rec.get("beam"):
        v.setdefault("beam", rec["beam"])


def parse_time_utc(meta):
    """AIS observation time from message metadata ('time_utc'), as an epoch
    float; falls back to local receive time if absent/unparseable. The field
    looks like '2022-12-29 18:22:32.318353 +0000 UTC'."""
    s = (meta or {}).get("time_utc")
    if not s:
        return time.time()
    try:
        # Trim the trailing ' UTC' label and parse the offset-aware timestamp.
        s2 = s.replace(" UTC", "").strip()
        # Python wants 6-digit microseconds and +0000 (no colon) works with %z.
        return _dt.datetime.strptime(s2, "%Y-%m-%d %H:%M:%S.%f %z").timestamp()
    except Exception:
        return time.time()


def update_position(mmsi, msg, observed):
    v = vessels.setdefault(mmsi, {"mmsi": mmsi})
    v["lat"] = msg.get("Latitude")
    v["lon"] = msg.get("Longitude")
    v["sog"] = msg.get("Sog")
    v["cog"] = msg.get("Cog")
    v["hdg"] = msg.get("TrueHeading")
    v["navstat"] = msg.get("NavigationalStatus")
    v["last"] = observed


def update_static(mmsi, msg, observed):
    v = vessels.setdefault(mmsi, {"mmsi": mmsi})
    name = (msg.get("Name") or "").strip()
    if name:
        v["name"] = name
    v["type"] = ship_type_label(msg.get("Type"))
    v["type_code"] = msg.get("Type")
    dest = (msg.get("Destination") or "").strip()
    if dest:
        v["dest"] = dest
    v["draught"] = msg.get("MaximumStaticDraught")
    dim = msg.get("Dimension") or {}
    length = (dim.get("A") or 0) + (dim.get("B") or 0)
    beam = (dim.get("C") or 0) + (dim.get("D") or 0)
    if length > 0:
        v["length"] = length
    if beam > 0:
        v["beam"] = beam
    cs = (msg.get("CallSign") or "").strip()
    if cs:
        v["callsign"] = cs
    if msg.get("ImoNumber"):
        v["imo"] = msg.get("ImoNumber")
    eta = msg.get("Eta") or {}
    day = eta.get("Day") or 0
    hr, mn = eta.get("Hour"), eta.get("Minute")
    if day and hr is not None and hr < 24 and mn is not None and mn < 60:
        v["eta"] = f"{day:02d} {hr:02d}:{mn:02d}"
    v["last"] = observed


def prune():
    """Drop sightings older than the retention window."""
    now = time.time()
    stale = [m for m, v in vessels.items() if now - v.get("last", 0) > RETAIN_SECS]
    for m in stale:
        del vessels[m]


def _eligible(tc):
    """Big commercial traffic only; drop known-small craft, keep unknowns."""
    if tc is None:
        return True
    # exclude pleasure(37)/sailing(36)/fishing(30) and other small craft
    if tc in (30, 36, 37):
        return False
    if 1 <= tc < 30:
        return False
    return True


def age_string(secs):
    """Compact age for the board: 'now', '12m', '2h', '14h'."""
    m = int(secs // 60)
    if m < 1:
        return "now"
    if m < 60:
        return f"{m}m"
    return f"{int(round(m/60.0))}h"


# --- Long-run CSV logging (REGISTER_LOG=path.csv) ----------------------------
_logged = {}                  # mmsi -> last-logged (name, op) signature
_log_header_written = False


def _seed_log_from_existing():
    """Pre-load _logged from an existing REGISTER_LOG so vessels already
    recorded in a previous run aren't re-logged as 'first_seen' after a
    restart. Uses the most recent (name, operator) seen per MMSI in the file."""
    global _log_header_written
    if not VESSEL_LOG or not os.path.exists(VESSEL_LOG):
        return
    try:
        with open(VESSEL_LOG, newline="") as f:
            n = 0
            for row in _csv.DictReader(f):
                m = row.get("mmsi")
                if not m:
                    continue
                try:
                    m = int(m)
                except ValueError:
                    continue
                _logged[m] = (row.get("name", "") or "", row.get("operator", "") or "")
                n += 1
        _log_header_written = True   # file exists, header already present
        if n:
            print(f"[log] seeded dedup from {VESSEL_LOG}: "
                  f"{len(_logged)} known vessels (won't re-log on restart)")
    except Exception as e:
        print(f"[log] could not seed from {VESSEL_LOG}: {e}")


def _parse_iso(ts):
    """Parse a logged ISO timestamp to epoch seconds; None on failure."""
    try:
        return _dt.datetime.fromisoformat(ts).timestamp()
    except Exception:
        return None


def _seed_vessels_from_register():
    """Warm-start the display: pre-load the `vessels` dict from REGISTER_LOG so
    the panel shows the recent register immediately after a restart instead of
    waiting for ships to re-report. Uses each vessel's most recent logged row;
    positions are last-known (possibly stale) but the age stamp tells the truth,
    and prune() drops anything already past the retention window. Live AIS then
    refreshes positions as ships re-report."""
    if not VESSEL_LOG or not os.path.exists(VESSEL_LOG):
        return
    latest = {}                   # mmsi -> (epoch, row) keeping the newest row
    try:
        with open(VESSEL_LOG, newline="") as f:
            for row in _csv.DictReader(f):
                m = row.get("mmsi")
                if not m or not m.isdigit():
                    continue
                m = int(m)
                t = _parse_iso(row.get("timestamp", "")) or 0
                if m not in latest or t >= latest[m][0]:
                    latest[m] = (t, row)
    except Exception as e:
        print(f"[seed] could not read {VESSEL_LOG}: {e}")
        return

    def num(s):
        try:
            return float(s)
        except (ValueError, TypeError):
            return None

    seeded = 0
    for m, (t, row) in latest.items():
        if not t:
            continue
        v = {"mmsi": m, "last": t}
        name = (row.get("name") or "").strip()
        if name:
            v["name"] = name
        if row.get("type"):
            v["type"] = row["type"]
        tc = row.get("type_code")
        if tc and tc.lstrip("-").isdigit():
            v["type_code"] = int(tc)
        for k_csv, k_v in (("length", "length"), ("beam", "beam")):
            val = num(row.get(k_csv))
            if val:
                v[k_v] = val
        lat, lon = num(row.get("lat")), num(row.get("lon"))
        if lat is not None and lon is not None:
            v["lat"], v["lon"] = lat, lon
        if row.get("dest"):
            v["dest"] = row["dest"]
        # Course/speed if the row has them (new-schema rows); else leave unset
        # and let build_frame fall back to destination inference.
        cog = num(row.get("cog"))
        sog = num(row.get("sog"))
        if cog is not None:
            v["cog"] = cog
        if sog is not None:
            v["sog"] = sog
        vessels[m] = v
        # Prime passage detection's memory with this last-known progress, so a
        # crossing that straddles a restart still logs (the ship's pre-restart
        # position is remembered, not wiped). Without this, the first sighting
        # after every restart has no prior to compare against and is missed.
        if PASSAGE_LOG and lat is not None and lon is not None:
            p = river_progress(lat, lon)
            if p is not None:
                _last_progress[m] = (p, t)
        seeded += 1
    prune()                       # drop any already older than the window
    if seeded:
        print(f"[seed] warm-started display with {len(vessels)} recent vessels "
              f"from {VESSEL_LOG} (positions may be stale until they re-report)")


def log_vessel(mmsi, v, op, code, flag):
    if not VESSEL_LOG:
        return
    global _log_header_written
    name = v.get("name") or ""
    sig = (name, op)
    if _logged.get(mmsi) == sig:
        return
    first_seen = mmsi not in _logged
    _logged[mmsi] = sig
    row = {
        "timestamp": _dt.datetime.now().isoformat(timespec="seconds"),
        "event": "first_seen" if first_seen else "updated",
        "mmsi": mmsi,
        "name": name,
        "operator": op,
        "code": code,
        "flag": flag or "",
        "type": v.get("type", ""),
        "type_code": v.get("type_code") if v.get("type_code") is not None else "",
        "length": v.get("length") or "",
        "beam": v.get("beam") or "",
        "lat": round(v["lat"], 4) if v.get("lat") is not None else "",
        "lon": round(v["lon"], 4) if v.get("lon") is not None else "",
        "dest": v.get("dest", "") or "",
        "cog": round(v["cog"], 1) if v.get("cog") is not None else "",
        "sog": round(v["sog"], 1) if v.get("sog") is not None else "",
    }
    try:
        write_header = not _log_header_written and not os.path.exists(VESSEL_LOG)
        with open(VESSEL_LOG, "a", newline="") as f:
            w = _csv.DictWriter(f, fieldnames=list(row.keys()))
            if write_header:
                w.writeheader()
            w.writerow(row)
        _log_header_written = True
    except Exception as e:
        print(f"[log] could not write {VESSEL_LOG}: {e}")


def build_frame():
    prune()
    now = time.time()
    eligible = [v for v in vessels.values()
                if _eligible(v.get("type_code"))
                and (v.get("lat") is not None or v.get("name"))]
    # Named ships first (most-recent within that group), then nameless
    # MMSI-only fragments at the bottom. has_name=0 sorts before has_name=1;
    # negative time sorts newest-first within each group.
    def sort_key(v):
        has_name = 0 if (v.get("name") or "").strip() else 1
        return (has_name, -v.get("last", 0))
    eligible.sort(key=sort_key)

    ships = []
    for v in eligible:
        mmsi = v["mmsi"]
        op = operator_for(mmsi, v.get("name"))
        if op == "UNKNOWN" and v.get("name"):
            log_unknown(mmsi, v.get("name"))
        code = operator_code(op)
        flag = country_for(mmsi)
        log_vessel(mmsi, v, op, code, flag)
        age = now - v.get("last", now)
        ships.append({
            "mmsi": mmsi,
            "name": (v.get("name") or f"MMSI {mmsi}")[:20],
            "op":   op,
            "code": code,
            "type": v.get("type", "VSL"),
            "dir":  _dir_for(v),
            "sog":  round(v.get("sog") or 0.0, 1),
            "cog":  round(v.get("cog") or 0.0),
            "navstat": nav_status_label(v.get("navstat")),
            "flag": flag,
            "length": v.get("length"),
            "beam": v.get("beam"),
            "draught": v.get("draught"),
            "dest": (v.get("dest") or "")[:18],
            "eta": v.get("eta"),
            "callsign": v.get("callsign"),
            "imo": v.get("imo"),
            "age": age_string(age),
            "age_secs": int(age),
            "progress": river_progress(v.get("lat"), v.get("lon")),
        })

    return {
        "ts": int(now),
        "bright": target_brightness(),
        "closed": seaway_closed(),
        "hours": RETAIN_HOURS,
        "home": HOME_PROGRESS,
        "ships": ships,
    }


# --- Transport: serial (hardware) or HTTP (mock) -----------------------------
_serial_port = None


def _open_serial():
    global _serial_port
    try:
        import serial  # pyserial
    except ImportError:
        print("[serial] pyserial not installed: pip install pyserial")
        sys.exit(1)
    try:
        _serial_port = serial.Serial(ESP32_SERIAL, SERIAL_BAUD, timeout=1)
        print(f"[serial] open {ESP32_SERIAL} @ {SERIAL_BAUD}")
    except Exception as e:
        print(f"[serial] could not open {ESP32_SERIAL}: {e}")
        sys.exit(1)


async def pusher(session):
    if ESP32_SERIAL:
        _open_serial()
    while True:
        await asyncio.sleep(PUSH_INTERVAL)
        frame = build_frame()
        n = len(frame["ships"])
        save_mmsi_db()             # persist any new identities (no-op if unchanged)
        if ESP32_SERIAL:
            # Newline-delimited JSON: one frame per line, '\n'-terminated.
            try:
                line = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
                _serial_port.write(line)
                _serial_port.flush()
                print(f"[push] {n} ships -> serial {ESP32_SERIAL}")
            except Exception as e:
                print(f"[push] serial write failed: {e}; reopening")
                try:
                    _serial_port.close()
                except Exception:
                    pass
                _open_serial()
        else:
            try:
                async with session.post(ESP32_URL, json=frame, timeout=5) as resp:
                    await resp.read()
                print(f"[push] {n} ships in register "
                      f"(last {RETAIN_HOURS:.0f}h) -> {ESP32_HOST}")
            except Exception as e:
                print(f"[push] HTTP failed: {e}")


def handle_message(msg):
    mtype = msg.get("MessageType")
    meta = msg.get("MetaData", {})
    mmsi = meta.get("MMSI")
    if mmsi is None:
        return
    observed = parse_time_utc(meta)        # AIS observation time, not receive time
    body = msg.get("Message", {})
    if mtype == "PositionReport":
        pr = body.get("PositionReport", {})
        # If we know this MMSI from a past session, seed its identity now so it
        # shows up named immediately instead of as a ghost.
        if MMSI_DB_PATH and str(mmsi) in mmsi_db and not vessels.get(mmsi, {}).get("name"):
            db_prefill(mmsi)
        update_position(mmsi, pr, observed)
        # Passage check on every position report (not just at frame build, so
        # we don't miss a crossing between the 10s frame pushes).
        if PASSAGE_LOG:
            v = vessels.get(mmsi, {})
            op = operator_for(mmsi, v.get("name"))
            check_passage(mmsi, pr.get("Latitude"), pr.get("Longitude"),
                          observed, v.get("name"), op)
    elif mtype == "ShipStaticData":
        update_static(mmsi, body.get("ShipStaticData", {}), observed)
        # Record the resolved identity in the persistent DB (freshest wins).
        if MMSI_DB_PATH:
            v = vessels.get(mmsi, {})
            op = operator_for(mmsi, v.get("name"))
            db_upsert(mmsi, v, op, operator_code(op), country_for(mmsi))


async def subscribe():
    sub = {
        "APIKey": API_KEY,
        "BoundingBoxes": BOUNDING_BOX,
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    async with aiohttp.ClientSession() as session:
        asyncio.create_task(pusher(session))
        while True:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    await ws.send(json.dumps(sub))
                    print(f"[ais] subscribed; building {RETAIN_HOURS:.0f}h register")
                    async for raw in ws:
                        handle_message(json.loads(raw))
            except Exception as e:
                print(f"[ais] connection lost ({e}); reconnecting in 5s")
                await asyncio.sleep(5)


def _backfill_passages_from_register():
    """On startup, scan the register for crossings of Danger Island that aren't
    already in PASSAGE_LOG and log them. This recovers passages missed while the
    service was down (or before PASSAGE_LOG was enabled): warm-start handles a
    crossing that straddles a restart, this handles crossings that happened
    entirely during downtime. Safe to run every boot -- it de-dupes against
    existing passages by (mmsi, day, direction)."""
    if not PASSAGE_LOG or not VESSEL_LOG or HOME_PROGRESS is None:
        return
    if not os.path.exists(VESSEL_LOG):
        return

    # already-logged crossings, to avoid double-logging
    have = set()
    if os.path.exists(PASSAGE_LOG):
        try:
            with open(PASSAGE_LOG, newline="") as f:
                for row in _csv.DictReader(f):
                    have.add((row.get("mmsi", ""), row.get("pass_time", "")[:10],
                              row.get("direction", "")))
        except Exception:
            pass

    # gather each vessel's time-sorted (epoch, progress, name, op) sightings
    tracks = {}
    try:
        with open(VESSEL_LOG, newline="") as f:
            for row in _csv.DictReader(f):
                m = (row.get("mmsi") or "").strip()
                if not m.isdigit():
                    continue
                try:
                    lat = float(row["lat"]); lon = float(row["lon"])
                except (ValueError, TypeError, KeyError):
                    continue
                t = _parse_iso(row.get("timestamp", ""))
                p = river_progress(lat, lon)
                if t is None or p is None:
                    continue
                tracks.setdefault(int(m), []).append(
                    (t, p, (row.get("name") or "").strip(),
                     (row.get("operator") or "").strip()))
    except Exception as e:
        print(f"[backfill] could not read {VESSEL_LOG}: {e}")
        return

    added = 0
    for mmsi, pts in tracks.items():
        pts.sort(key=lambda r: r[0])
        # carry best-known name/operator forward across the track
        name = ""; op = ""
        for (_, _, nm, o) in pts:
            if nm:
                name = nm
            if o and o != "UNKNOWN":
                op = o
        for (t0, p0, _, _), (t1, p1, _, _) in zip(pts, pts[1:]):
            direction = None
            if p0 < HOME_PROGRESS <= p1:
                direction = "downbound"
            elif p0 > HOME_PROGRESS >= p1:
                direction = "upbound"
            if not direction:
                continue
            t_pass_iso = _dt.datetime.fromtimestamp(
                t0 + ((HOME_PROGRESS - p0) / (p1 - p0) if p1 != p0 else 0.5)
                * (t1 - t0)).isoformat(timespec="seconds")
            if (str(mmsi), t_pass_iso[:10], direction) in have:
                continue
            _log_passage(mmsi, name, op or "UNKNOWN", direction, t0, p0, t1, p1)
            have.add((str(mmsi), t_pass_iso[:10], direction))
            added += 1
    if added:
        print(f"[backfill] recovered {added} passage(s) from the register "
              f"(missed during downtime)")


if __name__ == "__main__":
    print("St. Lawrence Ship Wall -- REGISTER mode")
    print(f"  Retention: {RETAIN_HOURS:.0f} hours")
    if ESP32_SERIAL:
        print(f"  Display:   serial {ESP32_SERIAL} @ {SERIAL_BAUD}")
    else:
        print(f"  Display:   HTTP {ESP32_URL}")
    print(f"  Register log: {VESSEL_LOG or 'OFF (set REGISTER_LOG)'}")
    print(f"  Passage log:  {PASSAGE_LOG or 'OFF (set PASSAGE_LOG)'}")
    print(f"  MMSI DB:      {MMSI_DB_PATH or 'OFF (set MMSI_DB)'}")
    _seed_log_from_existing()
    load_mmsi_db()
    _seed_vessels_from_register()
    _backfill_passages_from_register()    # recover any crossings missed while down
    asyncio.run(subscribe())