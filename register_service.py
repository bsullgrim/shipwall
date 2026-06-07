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


def bound_dir(cog, sog):
    """NE course = downbound (D, toward sea), SW = upbound (U), slow = moored."""
    if (sog or 0) < MIN_SPEED_KTS:
        return "M"
    if cog is None:
        return "?"
    c = cog % 360
    return "D" if (c >= 315 or c < 135) else "U"


# --- Vessel register ----------------------------------------------------------
# mmsi -> merged dict; "last" is the most recent sighting time.
vessels = {}


def update_position(mmsi, msg):
    v = vessels.setdefault(mmsi, {"mmsi": mmsi})
    v["lat"] = msg.get("Latitude")
    v["lon"] = msg.get("Longitude")
    v["sog"] = msg.get("Sog")
    v["cog"] = msg.get("Cog")
    v["hdg"] = msg.get("TrueHeading")
    v["navstat"] = msg.get("NavigationalStatus")
    v["last"] = time.time()


def update_static(mmsi, msg):
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
    v["last"] = time.time()


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
import csv as _csv
import datetime as _dt

_logged = {}                  # mmsi -> last-logged (name, op) signature
_log_header_written = False


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
            "dir":  bound_dir(v.get("cog"), v.get("sog") or 0.0),
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
    body = msg.get("Message", {})
    if mtype == "PositionReport":
        update_position(mmsi, body.get("PositionReport", {}))
    elif mtype == "ShipStaticData":
        update_static(mmsi, body.get("ShipStaticData", {}))


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


if __name__ == "__main__":
    print("St. Lawrence Ship Wall -- REGISTER mode")
    print(f"  Retention: {RETAIN_HOURS:.0f} hours")
    if ESP32_SERIAL:
        print(f"  Display:   serial {ESP32_SERIAL} @ {SERIAL_BAUD}")
    else:
        print(f"  Display:   HTTP {ESP32_URL}")
    asyncio.run(subscribe())