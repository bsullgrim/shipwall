#!/usr/bin/env python3
"""
coverage_probe.py  --  Gauge AIS coverage of the Ship Wall's reach.

AIS base stations (message type 4, "BaseStationReport") broadcast their own
position. Hearing them tells you which shore receivers actually cover the upper
St. Lawrence on AISStream's network -- the real question behind the spotty
coverage. This tool subscribes to BaseStationReport (and, with --vessels, also
PositionReport) inside the bounding box and prints what it hears, with
distances from a reference point (Clayton, NY by default -- near the Narrows).

    AISSTREAM_KEY=... python3 coverage_probe.py            # base stations only
    AISSTREAM_KEY=... python3 coverage_probe.py --vessels  # + unique vessels
    AISSTREAM_KEY=... python3 coverage_probe.py --minutes 30

It does NOT touch the display or the services -- purely a diagnostic. Run it for
a while (base stations report every ~10s when heard) and watch what shows up.
A station list that's empty or far away explains thin vessel coverage.
"""

import argparse
import asyncio
import csv
import json
import math
import os
import sys
import time

import websockets

# Reuse .env loading convenience.
def _load_env():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, val = line.split("=", 1)
                os.environ.setdefault(k.strip(), val.strip().strip('"').strip("'"))
_load_env()

API_KEY = os.environ.get("AISSTREAM_KEY", "")
if not API_KEY:
    print("ERROR: set AISSTREAM_KEY (in .env or environment).")
    sys.exit(1)

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

WS_URL = "wss://stream.aisstream.io/v0/stream"
# Same outer reach as the services: Cape Vincent -> Montreal.
BOUNDING_BOX = [[[44.10, -76.40], [45.3237, -73.9132]]]
# Reference point for distances -- Clayton, NY, near the American Narrows.
REF_LAT, REF_LON = 44.224, -76.086


def haversine_nm(lat1, lon1, lat2, lon2):
    """Great-circle distance in nautical miles."""
    R_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R_nm * 2 * math.asin(math.sqrt(a))


def fmt_pos(lat, lon):
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return f"{abs(lat):.4f}{ns} {abs(lon):.4f}{ew}"


async def run(minutes, want_vessels):
    types = ["BaseStationReport"]
    if want_vessels:
        types.append("PositionReport")
    sub = {
        "APIKey": API_KEY,
        "BoundingBoxes": BOUNDING_BOX,
        "FilterMessageTypes": types,
    }

    stations = {}   # mmsi -> {lat, lon, count, first, last}
    vessels = {}    # mmsi -> {name, count, last}
    t_end = time.time() + minutes * 60 if minutes else None

    print(f"Coverage probe -- listening for {', '.join(types)}")
    print(f"  Box: {BOUNDING_BOX[0]}")
    print(f"  Reference: Clayton NY ({fmt_pos(REF_LAT, REF_LON)})")
    print(f"  Duration: {('%g min' % minutes) if minutes else 'until Ctrl-C'}\n")

    try:
        async with websockets.connect(WS_URL, ping_interval=20) as ws:
            await ws.send(json.dumps(sub))
            print("[subscribed; waiting for reports...]\n")
            while True:
                if t_end and time.time() > t_end:
                    break
                timeout = (t_end - time.time()) if t_end else None
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    break
                msg = json.loads(raw)
                mtype = msg.get("MessageType")
                meta = msg.get("MetaData", {})
                mmsi = meta.get("MMSI")
                body = msg.get("Message", {})

                if mtype == "BaseStationReport":
                    rpt = body.get("BaseStationReport", {})
                    lat = rpt.get("Latitude", meta.get("latitude"))
                    lon = rpt.get("Longitude", meta.get("longitude"))
                    s = stations.setdefault(mmsi, {"count": 0, "first": time.time()})
                    s["lat"], s["lon"] = lat, lon
                    s["count"] += 1
                    s["last"] = time.time()
                    if s["count"] == 1:
                        d = (haversine_nm(REF_LAT, REF_LON, lat, lon)
                             if lat is not None and lon is not None else None)
                        dist = f"{d:.1f} nm from Clayton" if d is not None else "no position"
                        print(f"  STATION {mmsi}  {fmt_pos(lat, lon) if lat is not None else '?':22}  {dist}")

                elif mtype == "PositionReport":
                    v = vessels.setdefault(mmsi, {"count": 0})
                    v["count"] += 1
                    v["last"] = time.time()
                    nm = meta.get("ShipName")
                    if nm:
                        v["name"] = nm.strip()
                    if v["count"] == 1:
                        print(f"  vessel  {mmsi}  {v.get('name', '(no name yet)')}")
    except KeyboardInterrupt:
        pass

    # Summary
    print("\n" + "=" * 56)
    print(f"COVERAGE SUMMARY  ({time.strftime('%Y-%m-%d %H:%M')})")
    print("=" * 56)
    if not stations:
        print("\nNo base stations heard in the box.")
        print("That strongly suggests no terrestrial receiver covers this")
        print("reach on AISStream -- which explains thin vessel coverage.")
    else:
        print(f"\n{len(stations)} base station(s) heard:\n")
        for mmsi, s in sorted(stations.items(),
                              key=lambda kv: haversine_nm(REF_LAT, REF_LON, kv[1]["lat"], kv[1]["lon"])
                              if kv[1].get("lat") is not None else 9e9):
            if s.get("lat") is not None:
                d = haversine_nm(REF_LAT, REF_LON, s["lat"], s["lon"])
                print(f"  {mmsi}  {fmt_pos(s['lat'], s['lon']):22}  "
                      f"{d:5.1f} nm  ({s['count']} reports)")
            else:
                print(f"  {mmsi}  position unknown  ({s['count']} reports)")
        nearest = min((haversine_nm(REF_LAT, REF_LON, s["lat"], s["lon"])
                       for s in stations.values() if s.get("lat") is not None),
                      default=None)
        if nearest is not None:
            print(f"\nNearest station ~{nearest:.0f} nm away.")
            if nearest > 30:
                print("That's far -- terrestrial AIS range is ~20-40 nm line of")
                print("sight, so coverage of your reach is likely marginal.")
    if want_vessels:
        named = sum(1 for v in vessels.values() if v.get("name"))
        print(f"\nVessels heard: {len(vessels)} unique ({named} with names).")
    print()
    return stations


# --- Map generation ----------------------------------------------------------
def read_register(path):
    """Read vessel detections (lat/lon) from the register CSV the service writes."""
    detections = []
    if not os.path.exists(path):
        return detections
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                try:
                    lat = float(row.get("lat", "") or "nan")
                    lon = float(row.get("lon", "") or "nan")
                except ValueError:
                    continue
                if math.isnan(lat) or math.isnan(lon):
                    continue
                detections.append({
                    "lat": lat, "lon": lon,
                    "name": (row.get("name") or "").strip() or f"MMSI {row.get('mmsi','')}",
                    "code": (row.get("code") or "").strip(),
                })
    except Exception as e:
        print(f"[map] could not read {path}: {e}")
    return detections


def infer_ranges(stations, detections):
    """Attribute each detection to its nearest station; a station's inferred
    range is the distance to its farthest attributed detection (nm)."""
    located = {m: s for m, s in stations.items() if s.get("lat") is not None}
    for s in located.values():
        s["range_nm"] = 0.0
        s["n_attributed"] = 0
    if not located:
        return
    for d in detections:
        nearest_m, nearest_dist = None, 9e9
        for m, s in located.items():
            dist = haversine_nm(s["lat"], s["lon"], d["lat"], d["lon"])
            if dist < nearest_dist:
                nearest_m, nearest_dist = m, dist
        if nearest_m is not None:
            st = located[nearest_m]
            st["n_attributed"] += 1
            if nearest_dist > st["range_nm"]:
                st["range_nm"] = nearest_dist


def write_map(stations, detections, path="coverage_map.html"):
    box = BOUNDING_BOX[0]
    (lat0, lon0), (lat1, lon1) = box[0], box[1]
    center_lat, center_lon = (lat0 + lat1) / 2, (lon0 + lon1) / 2

    st_json = [{
        "mmsi": m, "lat": s["lat"], "lon": s["lon"],
        "range_nm": round(s.get("range_nm", 0.0), 1),
        "reports": s.get("count", 0),
        "attributed": s.get("n_attributed", 0),
    } for m, s in stations.items() if s.get("lat") is not None]
    det_json = [{"lat": d["lat"], "lon": d["lon"], "name": d["name"], "code": d["code"]}
                for d in detections]

    html = """<!doctype html><html><head><meta charset=utf-8>
<title>Ship Wall coverage map</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<link rel=stylesheet href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  body{margin:0;font-family:system-ui,sans-serif}
  #map{height:100vh}
  .legend{background:#fff;padding:8px 10px;border-radius:6px;font-size:12px;line-height:1.5;box-shadow:0 1px 4px rgba(0,0,0,.3)}
  .legend b{display:block;margin-bottom:4px}
  .sw{display:inline-block;width:10px;height:10px;margin-right:5px;border-radius:2px;vertical-align:middle}
</style></head><body>
<div id=map></div>
<script>
const BOX=%BOX%, CENTER=[%CLAT%,%CLON%], REF=[%RLAT%,%RLON%];
const STATIONS=%STATIONS%, DETECTIONS=%DETECTIONS%;
const NM=1852; // metres per nautical mile

const map=L.map('map').setView(CENTER,9);
// CARTO basemap (free, CORS/local-friendly, no Referer requirement like
// OSM's main tile server; dark theme suits the panel colors). Subdomains a-d.
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
  {maxZoom:19, subdomains:'abcd',
   attribution:'&copy; OpenStreetMap &copy; CARTO'}).addTo(map);

// Area-of-interest box
L.rectangle([[BOX[0][0],BOX[0][1]],[BOX[1][0],BOX[1][1]]],
  {color:'#3388ff',weight:2,fill:false,dashArray:'5,5'}).addTo(map)
  .bindPopup('Area of interest');

// Reference point (Clayton)
L.circleMarker(REF,{radius:5,color:'#222',fillColor:'#fff',fillOpacity:1,weight:2})
  .addTo(map).bindPopup('Clayton, NY (reference)');

// Received vessel detections
DETECTIONS.forEach(d=>{
  L.circleMarker([d.lat,d.lon],{radius:3,color:'#e6442e',fillColor:'#e6442e',
    fillOpacity:.7,weight:0}).addTo(map)
    .bindPopup(d.name+(d.code&&d.code!=='???'?' ('+d.code+')':''));
});

// Base stations + inferred range rings
STATIONS.forEach(s=>{
  if(s.range_nm>0){
    L.circle([s.lat,s.lon],{radius:s.range_nm*NM,color:'#2e9e5b',weight:1,
      fillColor:'#2e9e5b',fillOpacity:.08}).addTo(map);
  }
  L.circleMarker([s.lat,s.lon],{radius:7,color:'#1a6b3a',fillColor:'#3cdc78',
    fillOpacity:1,weight:2}).addTo(map)
    .bindPopup('Base station '+s.mmsi+'<br>inferred range ~'+s.range_nm+
      ' nm<br>'+s.attributed+' detections attributed<br>'+s.reports+' reports heard');
});

const legend=L.control({position:'bottomright'});
legend.onAdd=function(){
  const d=L.DomUtil.create('div','legend');
  d.innerHTML='<b>Ship Wall coverage</b>'+
    '<span class=sw style="background:#3cdc78"></span>base station<br>'+
    '<span class=sw style="background:#2e9e5b;opacity:.4"></span>inferred range (from detections)<br>'+
    '<span class=sw style="background:#e6442e"></span>received vessel<br>'+
    '<span class=sw style="background:#fff;border:1px solid #222"></span>Clayton (ref)<br>'+
    DETECTIONS.length+' detections, '+STATIONS.length+' stations';
  return d;
};
legend.addTo(map);

// Fit to everything
const pts=[[BOX[0][0],BOX[0][1]],[BOX[1][0],BOX[1][1]]]
  .concat(STATIONS.map(s=>[s.lat,s.lon]))
  .concat(DETECTIONS.map(d=>[d.lat,d.lon]));
if(pts.length) map.fitBounds(pts,{padding:[30,30]});
</script></body></html>"""

    html = (html.replace("%BOX%", json.dumps(box))
                .replace("%CLAT%", str(center_lat)).replace("%CLON%", str(center_lon))
                .replace("%RLAT%", str(REF_LAT)).replace("%RLON%", str(REF_LON))
                .replace("%STATIONS%", json.dumps(st_json))
                .replace("%DETECTIONS%", json.dumps(det_json)))
    with open(path, "w") as f:
        f.write(html)
    print(f"\n[map] wrote {path}: {len(st_json)} stations, {len(det_json)} detections")
    if not st_json:
        print("[map] (no base stations located -- rings/markers will be empty;")
        print("      the detection scatter still shows real coverage.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Probe AIS coverage of the reach.")
    ap.add_argument("--minutes", type=float, default=10,
                    help="how long to listen (0 = until Ctrl-C; default 10)")
    ap.add_argument("--vessels", action="store_true",
                    help="also count unique vessels heard")
    ap.add_argument("--no-map", dest="make_map", action="store_false",
                    help="skip writing coverage_map.html (map is on by default)")
    ap.add_argument("--register", default="register.csv",
                    help="register CSV to read vessel detections from (for the map)")
    args = ap.parse_args()
    try:
        stations = asyncio.run(run(args.minutes, args.vessels))
    except KeyboardInterrupt:
        stations = {}
        print("\nstopped.")
    if args.make_map:
        detections = read_register(args.register)
        if not detections:
            print(f"[map] no detections found in {args.register} "
                  f"(is the register service logging to it?)")
        infer_ranges(stations or {}, detections)
        write_map(stations or {}, detections)