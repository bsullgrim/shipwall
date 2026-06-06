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
import time

import aiohttp
import websockets

from operators import operator_for, log_unknown
from schedule import target_brightness, seaway_closed

# --- Configuration -----------------------------------------------------------

API_KEY   = os.environ["AISSTREAM_KEY"]          # get a free key at aisstream.io
ESP32_URL = f"http://{os.environ.get('ESP32_HOST', '192.168.1.50')}/frame"

# Chippewa Bay -> Oak Point, upper St. Lawrence Seaway (American Narrows).
# AISStream bounding box format: [[[lat_sw, lon_sw], [lat_ne, lon_ne]]]
BOUNDING_BOX = [[[44.42, -75.82], [44.58, -75.55]]]

PUSH_INTERVAL   = 10      # seconds between frames sent to the display
VESSEL_TIMEOUT  = 600     # drop vessels not heard from in 10 minutes
MAX_VESSELS     = 5       # display shows up to 5 at once (FlightWall parity)
MIN_SPEED_KTS   = 0.3     # below this, treat as moored/anchored

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
    v["last"] = time.time()


def update_static(mmsi, msg):
    v = vessels.setdefault(mmsi, {"mmsi": mmsi})
    name = (msg.get("Name") or "").strip()
    if name:
        v["name"] = name
    v["type"] = ship_type_label(msg.get("Type"))
    dest = (msg.get("Destination") or "").strip()
    if dest:
        v["dest"] = dest
    v["draught"] = msg.get("MaximumStaticDraught")
    v["last"] = time.time()


def prune():
    now = time.time()
    stale = [m for m, v in vessels.items() if now - v.get("last", 0) > VESSEL_TIMEOUT]
    for m in stale:
        del vessels[m]


def build_frame():
    """Pick the vessels to show and shape them for the display."""
    prune()
    now = time.time()
    candidates = []
    for v in vessels.values():
        if v.get("lat") is None or v.get("lon") is None:
            continue
        sog = v.get("sog") or 0.0
        # Prefer moving vessels; moored ships are boring on a wall.
        moving = sog >= MIN_SPEED_KTS
        candidates.append((moving, sog, v))

    # Moving vessels first, then by speed descending.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

    frame = []
    for _, _, v in candidates[:MAX_VESSELS]:
        name = (v.get("name") or f"MMSI {v['mmsi']}")
        op = operator_for(v["mmsi"], v.get("name"))
        if op == "UNKNOWN" and v.get("name"):
            log_unknown(v["mmsi"], v.get("name"))
        frame.append({
            "name": name[:20],
            "op":   op,
            "type": v.get("type", "VSL"),
            "dest": (v.get("dest") or "")[:16],
            "sog":  round(v.get("sog") or 0.0, 1),
            "cog":  round(v.get("cog") or 0.0),
            "drft": v.get("draught"),
        })
    return {
        "ts": int(now),
        "bright": target_brightness(),     # 0-255, sun-based
        "closed": seaway_closed(),         # winter closure flag
        "vessels": frame,
    }


# --- Networking ---------------------------------------------------------------

async def pusher(session):
    """Periodically POST the current frame to the ESP32."""
    while True:
        await asyncio.sleep(PUSH_INTERVAL)
        frame = build_frame()
        try:
            async with session.post(ESP32_URL, json=frame, timeout=5) as resp:
                await resp.read()
            print(f"[push] {len(frame['vessels'])} vessels -> ESP32 "
                  f"(tracking {len(vessels)} total)")
        except Exception as e:
            print(f"[push] failed: {e}")


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
    print(f"  ESP32: {ESP32_URL}")
    asyncio.run(subscribe())
