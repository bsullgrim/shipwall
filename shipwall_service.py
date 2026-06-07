#!/usr/bin/env python3
"""
shipwall_service.py  --  St. Lawrence Ship Wall (Chippewa Bay -> Oak Point)

Subscribes to AISStream.io, filters to a bounding box over the American
Narrows, merges position + static data per vessel, and pushes a compact
JSON frame to the ESP32 display over HTTP POST.

Run on a Raspberry Pi / always-on Linux box:
    pip install websockets aiohttp
    AISSTREAM_KEY=xxxx ESP32_HOST=192.168.1.50 python3 shipwall_service.py
"""

import asyncio
import json
import math
import os
import sys
import time

import aiohttp
import websockets

# On Windows, asyncio defaults to the Proactor event loop, which aiodns
# (pulled in by aiohttp for DNS) cannot use. Switch to the Selector loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from operators import operator_for, log_unknown
from schedule import target_brightness, seaway_closed


def _load_dotenv(path=".env"):
    """Minimal .env loader (no dependency). Lines like KEY=value; '#' comments.
    Existing environment variables always take precedence over the file."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


_load_dotenv()

# --- Configuration -----------------------------------------------------------

try:
    API_KEY = os.environ["AISSTREAM_KEY"]
except KeyError:
    raise SystemExit(
        "AISSTREAM_KEY is not set.\n"
        "  Copy .env.example to .env and add your key, or export it:\n"
        "    export AISSTREAM_KEY=your_key_here\n"
        "  Get a free key at https://aisstream.io"
    )
ESP32_SERIAL = os.environ.get("ESP32_SERIAL", "").strip()
SERIAL_BAUD = int(os.environ.get("ESP32_BAUD", "115200"))
ESP32_HOST = os.environ.get("ESP32_HOST", "192.168.1.50")
ESP32_URL = f"http://{ESP32_HOST}/frame"

# Set SHIPWALL_DEBUG=1 to log, every push, why each tracked vessel is or isn't
# placed in a zone. Invaluable when the panel looks empty but ships are tracked.
DEBUG = os.environ.get("SHIPWALL_DEBUG", "") not in ("", "0", "false", "False")

# Set SHIPWALL_LOG=path.csv to record every vessel AISStream delivers, to a CSV.
# Logs each vessel the first time it's seen and whenever its name/type/zone
# changes -- not every push -- so you can leave it running for hours and review
# what your feed actually covers. Empty = disabled.
VESSEL_LOG = os.environ.get("SHIPWALL_LOG", "").strip()

# Two zones:
#   OUTER box -- Cape Vincent (where the river leaves Lake Ontario) downbound
#     to the Eisenhower Lock near Massena, NY (~45.00, -74.80). Feeds the
#     right-side roster: every big ship in this stretch of the upper Seaway.
#   INNER box -- Chippewa Bay -> Oak Point (American Narrows). Feeds the
#     detailed left-side cards, exactly as before.
# AISStream bounding box format: [[[lat_sw, lon_sw], [lat_ne, lon_ne]]]
#   SW corner: Cape Vincent area (44.10, -76.40)
#   NE corner: just past the Eisenhower Lock (45.02, -74.78)
OUTER_BOX = [[[44.10, -76.40], [45.3237, -73.9132]]]
INNER_BOX = (44.42, -75.82, 44.58, -75.55)   # (min_lat, min_lon, max_lat, max_lon)
BOUNDING_BOX = OUTER_BOX                       # what we subscribe to

PUSH_INTERVAL   = 10      # seconds between frames sent to the display
VESSEL_TIMEOUT  = 600     # drop vessels not heard from in 10 minutes
MAX_VESSELS     = 4       # detailed cards on the left (inner box)
MAX_ROSTER      = 7       # rows in the right-side roster (outer box)
MIN_SPEED_KTS   = 0.3     # below this, treat as moored/anchored

# River axis through the Narrows runs ~SW<->NE. Downbound = toward the sea
# (NE-ish course), upbound = toward the lakes (SW-ish course).
# Downbound if COG falls in [315..360]U[0..135]; else upbound. Moored handled
# separately by speed.
def bound_dir(cog, sog):
    if sog < MIN_SPEED_KTS:
        return "M"          # moored / anchored
    if cog is None:
        return "?"
    c = cog % 360
    return "D" if (c >= 315 or c < 135) else "U"

# Big commercial traffic only -- drop pleasure/sailing/fishing/etc. from roster.
# AIS type codes: 70-79 cargo, 80-89 tanker, 52 tug, 60-69 passenger, 50 pilot.
def is_big_ship(type_code):
    if type_code is None:
        return False
    return (70 <= type_code <= 89) or type_code in (50, 52) or (60 <= type_code <= 69)


# Roster eligibility is more permissive: keep a vessel UNLESS we positively know
# it's small. A freshly-seen ship has no type code yet (static data lags its
# position by up to ~6 min); excluding it would leave the roster blank for
# minutes. Known-small types (30 fishing, 36-37 sailing/pleasure) are dropped.
SMALL_TYPES = set(range(30, 32)) | set(range(36, 38))  # fishing, sailing, pleasure
def _roster_eligible(type_code):
    if type_code is None:
        return True                 # unknown yet -> show provisionally
    if type_code in SMALL_TYPES:
        return False
    # Other known small/irrelevant (e.g. 0 = not available) still allowed in;
    # most upper-Seaway AIS traffic that isn't pleasure craft is worth showing.
    return True

WS_URL = "wss://stream.aisstream.io/v0/stream"

# Ship-type code -> short label for the matrix (AIS "type of ship and cargo").
SHIP_TYPES = {
    range(30, 40):  "FISH",
    range(40, 50):  "HSC",
    range(50, 51):  "PILOT",
    range(52, 53):  "TUG",
    range(60, 70):  "PAX",
    range(70, 80):  "CARGO",
    range(80, 90):  "TANKER",
}


def ship_type_label(code):
    if code is None:
        return "VSL"
    for r, label in SHIP_TYPES.items():
        if code in r:
            return label
    return "VSL"


# AIS navigational status codes -> short labels. 15 = default/undefined.
NAV_STATUS = {
    0: "UNDERWAY", 1: "ANCHORED", 2: "NOT UNDER CMD", 3: "RESTRICTED",
    4: "DRAFT LIMITED", 5: "MOORED", 6: "AGROUND", 7: "FISHING",
    8: "SAILING", 11: "TOWING", 12: "PUSHING",
}


def nav_status_label(code):
    if code is None or code == 15:
        return None
    return NAV_STATUS.get(code)


# Maritime Identification Digits (first 3 of MMSI) -> 2-letter country code.
# Only the flags actually seen on the upper St. Lawrence + common salties.
MID_COUNTRY = {
    "316": "CA", "338": "US", "366": "US", "367": "US", "368": "US",
    "369": "US", "303": "US", "338": "US",
    "319": "KY",  # Cayman
    "229": "MT", "248": "MT", "256": "MT",  # Malta
    "538": "MH",  # Marshall Is.
    "636": "LR", "637": "LR",  # Liberia
    "311": "BS", "309": "BS", "308": "BS",  # Bahamas
    "373": "PA", "374": "PA", "351": "PA", "352": "PA", "353": "PA",
    "354": "PA", "355": "PA", "356": "PA", "357": "PA", "370": "PA",
    "371": "PA", "372": "PA",  # Panama
    "244": "NL", "245": "NL", "246": "NL",  # Netherlands
    "232": "GB", "233": "GB", "234": "GB", "235": "GB",  # UK
    "563": "SG", "564": "SG", "565": "SG", "566": "SG",  # Singapore
    "477": "HK",  # Hong Kong
    "209": "CY", "210": "CY", "212": "CY",  # Cyprus
    "215": "MT", "256": "MT",
}


def country_for(mmsi):
    """2-letter flag-state code from the MMSI's MID, or None."""
    s = str(mmsi)
    return MID_COUNTRY.get(s[:3]) if len(s) >= 3 else None


# --- Vessel state -------------------------------------------------------------

# mmsi -> dict of merged fields
vessels = {}


def update_position(mmsi, msg):
    v = vessels.setdefault(mmsi, {"mmsi": mmsi})
    v["lat"]  = msg.get("Latitude")
    v["lon"]  = msg.get("Longitude")
    v["sog"]  = msg.get("Sog")          # speed over ground, knots
    v["cog"]  = msg.get("Cog")          # course over ground, degrees
    v["hdg"]  = msg.get("TrueHeading")
    v["navstat"] = msg.get("NavigationalStatus")  # 0..15, reported status
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
    # Dimensions: antenna offsets A,B (fore/aft) and C,D (port/stbd).
    # length = A+B, beam = C+D. Only store if non-trivial.
    dim = msg.get("Dimension") or {}
    length = (dim.get("A") or 0) + (dim.get("B") or 0)
    beam   = (dim.get("C") or 0) + (dim.get("D") or 0)
    if length > 0:
        v["length"] = length
    if beam > 0:
        v["beam"] = beam
    cs = (msg.get("CallSign") or "").strip()
    if cs:
        v["callsign"] = cs
    if msg.get("ImoNumber"):
        v["imo"] = msg.get("ImoNumber")
    # ETA is {Month, Day, Hour, Minute}; keep as a compact "DD HH:MM" if valid.
    eta = msg.get("Eta") or {}
    mo, day = eta.get("Month") or 0, eta.get("Day") or 0
    hr, mn = eta.get("Hour"), eta.get("Minute")
    if day and hr is not None and hr < 24 and mn is not None and mn < 60:
        v["eta"] = f"{day:02d} {hr:02d}:{mn:02d}"
    v["last"] = time.time()


def prune():
    now = time.time()
    stale = [m for m, v in vessels.items() if now - v.get("last", 0) > VESSEL_TIMEOUT]
    for m in stale:
        del vessels[m]


# --- Long-run vessel logging (SHIPWALL_LOG=path.csv) --------------------------
import csv
import datetime

# Tracks the last-logged signature per MMSI so we only write on change.
_logged = {}
_log_header_written = False


def log_vessel(v):
    """Append a CSV row when a vessel is first seen or its key details change."""
    if not VESSEL_LOG:
        return
    global _log_header_written
    mmsi = v["mmsi"]
    name = v.get("name") or ""
    tc = v.get("type_code")
    # Signature of the fields we care about changing.
    sig = (name, tc)
    if _logged.get(mmsi) == sig:
        return                      # nothing new worth logging
    first_seen = mmsi not in _logged
    _logged[mmsi] = sig

    row = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "event": "first_seen" if first_seen else "updated",
        "mmsi": mmsi,
        "name": name,
        "type_code": tc if tc is not None else "",
        "type": v.get("type", ""),
        "lat": round(v.get("lat", 0.0), 4),
        "lon": round(v.get("lon", 0.0), 4),
        "sog": v.get("sog", ""),
        "cog": v.get("cog", ""),
        "dest": v.get("dest", ""),
        "in_inner": _in_inner_box(v) if v.get("lat") is not None else "",
        "roster_ok": _roster_eligible(tc),
    }
    try:
        write_header = not _log_header_written and not os.path.exists(VESSEL_LOG)
        with open(VESSEL_LOG, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            if write_header:
                w.writeheader()
            w.writerow(row)
        _log_header_written = True
    except Exception as e:
        print(f"[log] could not write {VESSEL_LOG}: {e}")


def _in_inner_box(v):
    mn_lat, mn_lon, mx_lat, mx_lon = INNER_BOX
    return (mn_lat <= v["lat"] <= mx_lat) and (mn_lon <= v["lon"] <= mx_lon)


def _debug_frame(positioned, frame, roster):
    """Explain, per tracked-and-positioned vessel, where it landed and why."""
    print(f"--- frame: {len(positioned)} positioned | "
          f"{len(frame)} detail | {len(roster)} roster ---")
    for v in positioned:
        tc = v.get("type_code")
        zones = []
        if _in_inner_box(v):
            zones.append("INNER")
        if _roster_eligible(tc):
            zones.append("ROSTER")
        why = ""
        if not zones:
            if not _roster_eligible(tc):
                why = f"(type {tc} = small, excluded)"
            else:
                why = "(outside inner box, not roster-eligible)"
        print(f"  {v.get('name') or v['mmsi']:<20} "
              f"lat={v['lat']:.3f} lon={v['lon']:.3f} "
              f"type={tc} sog={v.get('sog')} -> {','.join(zones) or 'NONE '+why}")


def build_frame():
    """Build both display zones from the tracked vessels.

    - 'vessels': detailed cards for ships inside the INNER box (Chippewa->Oak).
    - 'roster':  one-line entries for every big ship in the OUTER box.
    """
    prune()
    now = time.time()

    positioned = [v for v in vessels.values()
                  if v.get("lat") is not None and v.get("lon") is not None]

    # Long-run CSV log: record every vessel the feed delivers (dedupes itself).
    for v in positioned:
        log_vessel(v)

    # --- Inner-box detailed cards (left side) ---
    inner = [v for v in positioned if _in_inner_box(v)]
    inner.sort(key=lambda v: ((v.get("sog") or 0) >= MIN_SPEED_KTS,
                              v.get("sog") or 0), reverse=True)
    frame = []
    for v in inner[:MAX_VESSELS]:
        name = (v.get("name") or f"MMSI {v['mmsi']}")
        op = operator_for(v["mmsi"], v.get("name"))
        if op == "UNKNOWN" and v.get("name"):
            log_unknown(v["mmsi"], v.get("name"))
        frame.append({
            "name": name[:18],
            "op":   op,
            "type": v.get("type", "VSL"),
            "dest": (v.get("dest") or "")[:16],
            "sog":  round(v.get("sog") or 0.0, 1),
            "cog":  round(v.get("cog") or 0.0),
            "drft": v.get("draught"),
            "dir":  bound_dir(v.get("cog"), v.get("sog") or 0.0),
        })

    # --- Outer-box roster (right side): big commercial ships ---
    # Include vessels whose static data (and thus type) hasn't arrived yet:
    # a fresh position report precedes static data by up to ~6 min, and we'd
    # rather show an unknown big ship than a blank roster. Only EXCLUDE types
    # we positively know are small (pleasure/sailing/fishing).
    big = [v for v in positioned if _roster_eligible(v.get("type_code"))]
    # Order: moving first (by speed), moored last, so active traffic is on top.
    big.sort(key=lambda v: ((v.get("sog") or 0) >= MIN_SPEED_KTS,
                            v.get("sog") or 0), reverse=True)
    roster = []
    for v in big[:MAX_ROSTER]:
        name = (v.get("name") or f"{v['mmsi']}")
        roster.append({
            "name": name[:5],     # 5 chars fits the 40px roster zone past the glyph
            "op":   operator_for(v["mmsi"], v.get("name")),
            "dir":  bound_dir(v.get("cog"), v.get("sog") or 0.0),
        })

    if DEBUG:
        _debug_frame(positioned, frame, roster)

    return {
        "ts": int(now),
        "bright": target_brightness(),     # 0-255, sun-based
        "closed": seaway_closed(),         # winter closure flag
        "vessels": frame,                  # inner-box detail cards
        "roster": roster,                  # outer-box one-liners
    }


# --- Networking ---------------------------------------------------------------

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
    """Periodically push the current frame to the renderer (serial or HTTP)."""
    if ESP32_SERIAL:
        _open_serial()
    while True:
        await asyncio.sleep(PUSH_INTERVAL)
        frame = build_frame()
        tag = f"{len(frame['vessels'])} detail / {len(frame['roster'])} roster"
        if ESP32_SERIAL:
            try:
                line = (json.dumps(frame, separators=(",", ":")) + "\n").encode()
                _serial_port.write(line)
                _serial_port.flush()
                print(f"[push] {tag} -> serial {ESP32_SERIAL}")
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
                print(f"[push] {tag} -> {ESP32_HOST} "
                      f"(tracking {len(vessels)} total)")
            except Exception as e:
                print(f"[push] HTTP failed: {e}")


async def subscribe():
    """Maintain the AISStream websocket, reconnecting on drop."""
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
                    print("[ais] subscribed to AISStream")
                    async for raw in ws:
                        handle_message(json.loads(raw))
            except Exception as e:
                print(f"[ais] connection lost ({e}); reconnecting in 5s")
                await asyncio.sleep(5)


def handle_message(msg):
    mtype = msg.get("MessageType")
    meta  = msg.get("MetaData", {})
    mmsi  = meta.get("MMSI")
    if mmsi is None:
        return
    body = msg.get("Message", {})
    if mtype == "PositionReport":
        update_position(mmsi, body.get("PositionReport", {}))
    elif mtype == "ShipStaticData":
        update_static(mmsi, body.get("ShipStaticData", {}))


if __name__ == "__main__":
    print("St. Lawrence Ship Wall service starting...")
    print(f"  Box: {BOUNDING_BOX}")
    if ESP32_SERIAL:
        print(f"  Display: serial {ESP32_SERIAL} @ {SERIAL_BAUD}")
    else:
        print(f"  Display: HTTP {ESP32_URL}")
    asyncio.run(subscribe())