#!/usr/bin/env python3
"""
simulate.py  --  Drive the mock panel with synthetic Seaway traffic.

Lets you see the whole display behave -- adaptive layout, pair cycling,
brightness, and the idle/closed screens -- with NO AISStream key and NO
hardware. It posts frames to the mock panel just like the real service does.

    # terminal 1
    python3 mock_panel.py            # open http://localhost:8080

    # terminal 2
    python3 simulate.py              # watch the browser

Scenarios cycle automatically so you can see every display state:
  0 vessels -> 1 -> 2 -> 4 (pair cycling) -> winter closed -> back to 0.

Uses the real operators.py and schedule.py so operator matching and
sun-based brightness are exercised exactly as in production.
"""

import json
import time
import urllib.request

from operators import operator_for
from schedule import target_brightness

MOCK_URL = "http://localhost:8080/frame"

# A cast of real upper-Seaway vessels (names chosen to exercise the prefix
# matcher: CSL, Algoma, Fednav, Lower Lakes). Operator is derived, not hard-set.
FLEET = [
    (316001001, "CSL WELLAND",    "CARGO",  "MONTREAL",       8.2, 11.4, 62),
    (316002002, "ALGOMA SAULT",   "CARGO",  "THUNDER BAY",    9.0, 9.8, 245),
    (311003003, "FEDERAL HUNTER", "CARGO",  "TROIS-RIVIERES", 10.1, 12.0, 70),
    (367004004, "MISSISSAGI",     "CARGO",  "CLEVELAND",      7.5, 8.0, 250),
]


def vessel_dict(rec):
    mmsi, name, typ, dest, drft, sog, cog = rec
    return {
        "name": name,
        "op":   operator_for(mmsi, name),
        "type": typ,
        "dest": dest,
        "sog":  sog,
        "cog":  cog,
        "drft": drft,
    }


def post(frame):
    data = json.dumps(frame).encode()
    req = urllib.request.Request(MOCK_URL, data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception as e:
        print(f"  (could not reach mock panel: {e} -- is mock_panel.py running?)")


def send(vessels, closed=False, label=""):
    frame = {
        "ts": int(time.time()),
        "bright": target_brightness(),   # real sun-based value for right now
        "closed": closed,
        "vessels": vessels,
    }
    print(f"[{label}] {len(vessels)} vessel(s), bright {frame['bright']}, "
          f"{'CLOSED' if closed else 'open'}")
    post(frame)


SCENARIOS = [
    ("empty / no vessels",      lambda: ([], False)),
    ("one vessel (big card)",   lambda: ([vessel_dict(FLEET[0])], False)),
    ("two vessels (split)",     lambda: ([vessel_dict(FLEET[0]),
                                          vessel_dict(FLEET[1])], False)),
    ("four vessels (cycle)",    lambda: ([vessel_dict(f) for f in FLEET], False)),
    ("winter closure",          lambda: ([], True)),
]


if __name__ == "__main__":
    print("Simulating Seaway traffic -> mock panel. Ctrl-C to stop.\n")
    i = 0
    try:
        while True:
            label, gen = SCENARIOS[i % len(SCENARIOS)]
            vessels, closed = gen()
            send(vessels, closed=closed, label=label)
            # Hold each scenario long enough to watch pair cycling (12s).
            time.sleep(12)
            i += 1
    except KeyboardInterrupt:
        print("\nstopped.")
