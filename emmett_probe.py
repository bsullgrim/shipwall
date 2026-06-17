#!/usr/bin/env python3
"""Standalone probe: connect to AISStream, follow only the Lake Guardian, and
print whatever comes back. Confirms the MMSI subscription works independent of
register_service. Run:  AISSTREAM_KEY=... python3 emmett_probe.py"""
import os, json, asyncio, websockets

def _load_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                os.environ.setdefault(k.strip(), val.strip())
_load_env()

KEY = os.environ.get("AISSTREAM_KEY") or os.environ.get("AISSTREAM_API_KEY")
MMSI = "338021074"

async def main():
    if not KEY:
        print("set AISSTREAM_KEY"); return
    sub = {
        "APIKey": KEY,
        "BoundingBoxes": [[[41.0, -93.0], [49.5, -73.0]]],   # Great Lakes, not world
        "FiltersShipMMSI": [MMSI],
        "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
    }
    print("connecting...")
    async with websockets.connect("wss://stream.aisstream.io/v0/stream", ping_interval=20) as ws:
        await ws.send(json.dumps(sub))
        print("subscribed; waiting for messages (Ctrl-C to stop)...")
        async for raw in ws:
            msg = json.loads(raw)
            mt = msg.get("MessageType")
            # AISStream sends an 'Error' message if the subscription is bad
            if "error" in msg or mt is None:
                print("RAW:", raw[:300]); continue
            meta = msg.get("MetaData", {})
            if mt == "PositionReport":
                pr = msg.get("Message", {}).get("PositionReport", {})
                print(f"POS  lat={pr.get('Latitude')} lon={pr.get('Longitude')} "
                      f"sog={pr.get('Sog')} mmsi={meta.get('MMSI')}")
            else:
                print(f"{mt}  mmsi={meta.get('MMSI')}")

asyncio.run(main())