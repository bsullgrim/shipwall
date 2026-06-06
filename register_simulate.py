#!/usr/bin/env python3
"""
register_simulate.py  --  Drive register_panel.py with synthetic traffic.

Lets you see the register display (BOARD + DETAIL modes) behave with NO
AISStream key and NO hardware. Posts register frames to the panel just like
register_service.py does.

    # terminal 1
    python3 register_panel.py            # open http://localhost:8080

    # terminal 2
    python3 register_simulate.py         # watch the browser

It posts a fixed register of vessels with varied ages, directions, operators,
and field completeness (some fully resolved, some bare MMSI fragments) so you
can exercise: the scrolling board (set BIG=1 for an overflowing list), the
detail-card cycling, missing-field handling, and the up/down/moored glyphs.

Uses the real operators.py and schedule.py so codes and brightness match
production.
"""

import json
import os
import time
import urllib.request

from operators import operator_for, operator_code
from schedule import target_brightness, seaway_closed

PANEL_URL = "http://localhost:8080/frame"
RETAIN_HOURS = float(os.environ.get("REGISTER_HOURS", "18"))
BIG = os.environ.get("BIG", "") not in ("", "0", "false", "False")

# Country lookup (trimmed copy of the service's MID table for the sim).
MID_COUNTRY = {"316": "CA", "366": "US", "367": "US", "368": "US",
               "538": "MH", "636": "LR", "311": "BS", "244": "NL"}
def country_for(mmsi):
    return MID_COUNTRY.get(str(mmsi)[:3])

# (mmsi, name, type, dir, sog, cog, navstat, length, beam, draught, dest, eta, age_min)
# age_min = minutes since last seen, so we can show a spread of ages.
FLEET = [
    (316111001, "FEDERAL KIVALINA", "CARGO",  "D", 11.4,  62, "UNDERWAY", 225, 24, 11.3, "MONTREAL",     "08 14:00",   0),
    (316222002, "ALGOMA SAULT",     "CARGO",  "U",  9.8, 245, "UNDERWAY", 225, 24,  8.2, "THUNDER BAY",  None,        12),
    (316333003, "BAIE ST PAUL",     "CARGO",  "D", 10.2,  58, "UNDERWAY", 179, 24,  9.5, "QUEBEC",       None,        95),
    (367444004, "MESABI MINER",     "CARGO",  "M",  0.0,   0, "MOORED",   305, 32, None,  "",            None,       180),
    (316555005, None,               "VSL",    "D",  7.0,  70, None,       None, None, None, "",           None,       300),  # bare fragment
    (538666006, "FEDERAL HUNTER",   "CARGO",  "U", 11.0, 250, "UNDERWAY", 200, 24, 10.1, "DULUTH",       "09 06:00", 420),
    (367777007, "JOHN J CARRICK",   "TANKER", "U",  8.5, 240, "UNDERWAY", 123, 19,  6.0, "HAMILTON",     None,       510),
    (316888008, "CSL WELLAND",      "CARGO",  "D", 12.0,  60, "UNDERWAY", 225, 24, 10.8, "TROIS-RIVIERES", None,     640),
]


def ship_dict(rec):
    mmsi, name, typ, d, sog, cog, nav, length, beam, draught, dest, eta, age_min = rec
    op = operator_for(mmsi, name)
    if age_min < 1:
        age = "now"
    elif age_min < 60:
        age = f"{age_min}m"
    else:
        age = f"{round(age_min/60)}h"
    return {
        "mmsi": mmsi,
        "name": (name or f"MMSI {mmsi}")[:20],
        "op": op,
        "code": operator_code(op),
        "type": typ,
        "dir": d,
        "sog": sog,
        "cog": cog,
        "navstat": nav,
        "flag": country_for(mmsi),
        "length": length,
        "beam": beam,
        "draught": draught,
        "dest": dest,
        "eta": eta,
        "callsign": None,
        "imo": None,
        "age": age,
        "age_secs": age_min * 60,
    }


def post(frame):
    data = json.dumps(frame).encode()
    req = urllib.request.Request(PANEL_URL, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception as e:
        print(f"  (could not reach panel: {e} -- is register_panel.py running?)")


if __name__ == "__main__":
    # BIG mode duplicates the fleet to force the board to scroll.
    fleet = FLEET * 2 if BIG else FLEET
    ships = [ship_dict(r) for r in fleet]
    frame = {
        "ts": int(time.time()),
        "bright": target_brightness(),
        "closed": seaway_closed(),
        "hours": RETAIN_HOURS,
        "ships": ships,
    }
    print(f"Simulating a {RETAIN_HOURS:.0f}h register of {len(ships)} ships "
          f"-> register panel. Ctrl-C to stop.")
    print("  (set BIG=1 to overflow the board and see it scroll)")
    try:
        while True:
            # Refresh brightness/age each push; keep posting so the panel's
            # 'waiting for data' timeout never trips.
            frame["ts"] = int(time.time())
            frame["bright"] = target_brightness()
            post(frame)
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nstopped.")
