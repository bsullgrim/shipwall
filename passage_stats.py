#!/usr/bin/env python3
"""
passage_stats.py  --  Serve a ship-passage leaderboard on the local network.

Reads the passage log written by register_service.py (PASSAGE_LOG=passages.csv)
and serves a web page showing which ships have passed Danger Island, how many
times, and recent crossings. Open it from any phone or laptop on the same WiFi
as the Pi -- no need to pull the Pi or extract the CSV.

    PASSAGE_LOG=passages.csv python3 passage_stats.py      # http://<pi>:8090

Then browse to http://<pi-ip>:8090  (or http://shipwall.local:8090 if mDNS
resolves on your network). Runs independently of the display, so it works
whether the wall is driven over serial or the browser mock.
"""

import csv
import http.server
import json
import os
import socketserver
from collections import defaultdict

PORT = int(os.environ.get("STATS_PORT", "8090"))
PASSAGE_LOG = os.environ.get("PASSAGE_LOG", "passages.csv").strip()
DEMO = False                       # set by --demo; serves built-in sample data

# Built-in sample passages so the page can be previewed with no real data
# (python passage_stats.py --demo). Mirrors the passages.csv schema.
DEMO_ROWS = [
    {"pass_time": "2026-06-07T05:40:00", "mmsi": "316111001", "name": "FEDERAL KIVALINA",
     "operator": "FEDNAV", "direction": "upbound",
     "seen_before": "2026-06-07T05:10:00", "seen_after": "2026-06-07T06:10:00", "gap_min": "60.0"},
    {"pass_time": "2026-06-07T04:15:00", "mmsi": "316222002", "name": "ALGOMA SAULT",
     "operator": "ALGOMA", "direction": "downbound",
     "seen_before": "2026-06-07T03:30:00", "seen_after": "2026-06-07T05:00:00", "gap_min": "90.0"},
    {"pass_time": "2026-06-07T01:13:50", "mmsi": "316111001", "name": "FEDERAL KIVALINA",
     "operator": "FEDNAV", "direction": "downbound",
     "seen_before": "2026-06-07T01:00:00", "seen_after": "2026-06-07T02:00:00", "gap_min": "60.0"},
    {"pass_time": "2026-06-06T22:30:00", "mmsi": "367444004", "name": "MESABI MINER",
     "operator": "INTERLAKE", "direction": "downbound",
     "seen_before": "2026-06-06T21:30:00", "seen_after": "2026-06-06T23:30:00", "gap_min": "120.0"},
    {"pass_time": "2026-06-06T19:00:00", "mmsi": "316111001", "name": "FEDERAL KIVALINA",
     "operator": "FEDNAV", "direction": "upbound",
     "seen_before": "2026-06-06T18:30:00", "seen_after": "2026-06-06T19:30:00", "gap_min": "60.0"},
    {"pass_time": "2026-06-06T14:20:00", "mmsi": "316333003", "name": "BAIE ST PAUL",
     "operator": "CSL", "direction": "downbound",
     "seen_before": "2026-06-06T13:45:00", "seen_after": "2026-06-06T15:00:00", "gap_min": "75.0"},
    {"pass_time": "2026-06-06T09:10:00", "mmsi": "316222002", "name": "ALGOMA SAULT",
     "operator": "ALGOMA", "direction": "upbound",
     "seen_before": "2026-06-06T08:30:00", "seen_after": "2026-06-06T10:00:00", "gap_min": "90.0"},
    {"pass_time": "2026-06-05T20:45:00", "mmsi": "367777007", "name": "JOHN J CARRICK",
     "operator": "UNKNOWN", "direction": "downbound",
     "seen_before": "2026-06-05T20:00:00", "seen_after": "2026-06-05T21:30:00", "gap_min": "90.0"},
]


def load_passages():
    if DEMO:
        return list(DEMO_ROWS)
    rows = []
    if not os.path.exists(PASSAGE_LOG):
        return rows
    try:
        with open(PASSAGE_LOG, newline="") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        pass
    return rows


def summarize(rows):
    by_ship = defaultdict(lambda: {"name": "", "operator": "", "count": 0,
                                   "down": 0, "up": 0, "last": ""})
    for r in rows:
        mmsi = r.get("mmsi", "")
        s = by_ship[mmsi]
        s["name"] = r.get("name", "") or s["name"]
        s["operator"] = r.get("operator", "") or s["operator"]
        s["count"] += 1
        if r.get("direction") == "downbound":
            s["down"] += 1
        elif r.get("direction") == "upbound":
            s["up"] += 1
        pt = r.get("pass_time", "")
        if pt > s["last"]:
            s["last"] = pt
    leaderboard = sorted(
        ({"mmsi": m, **v} for m, v in by_ship.items()),
        key=lambda x: (-x["count"], x["name"]))
    recent = sorted(rows, key=lambda r: r.get("pass_time", ""), reverse=True)[:25]
    return leaderboard, recent


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Ship Wall &mdash; passages</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
  :root{color-scheme:dark}
  body{margin:0;background:#0e1116;color:#d8dee9;font-family:system-ui,-apple-system,sans-serif;padding:16px}
  h1{font-size:20px;margin:0 0 2px} h2{font-size:15px;color:#8b98a9;margin:24px 0 8px;font-weight:600}
  .sub{color:#6e7b8c;font-size:13px;margin-bottom:8px}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #1d2530}
  th{color:#8b98a9;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  tr:hover td{background:#141a22}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  .code{color:#9ad0ff;font-weight:600}
  .down{color:#50c8ff} .up{color:#ff963c}
  .big{font-size:15px;font-weight:600}
  .tot{display:flex;gap:24px;margin:10px 0 4px;flex-wrap:wrap}
  .tot div{background:#141a22;border-radius:8px;padding:10px 16px}
  .tot b{display:block;font-size:24px;color:#fff} .tot span{font-size:12px;color:#8b98a9}
  .empty{color:#6e7b8c;padding:20px 0}
  a{color:#9ad0ff}
</style></head><body>
<h1>St. Lawrence Ship Wall</h1>
<div class=sub>Vessels that have passed Danger Island &mdash; inferred from upstream/downstream sightings</div>
<div id=content><div class=empty>loading&hellip;</div></div>
<script>
async function load(){
  const r=await fetch('/data'); const d=await r.json();
  const c=document.getElementById('content');
  if(!d.leaderboard.length){
    c.innerHTML='<div class=empty>No passages logged yet. Once ships are seen '+
      'both above and below Danger Island, they\\'ll appear here.</div>'; return;
  }
  let h='<div class=tot>'+
    '<div><b>'+d.total+'</b><span>total passages</span></div>'+
    '<div><b>'+d.unique+'</b><span>distinct vessels</span></div>'+
    '<div><b>'+d.down+'</b><span>downbound</span></div>'+
    '<div><b>'+d.up+'</b><span>upbound</span></div></div>';
  h+='<h2>Most frequent visitors</h2><table><tr><th>Ship</th><th>Op</th>'+
     '<th class=num>Passes</th><th class=num>Down</th><th class=num>Up</th><th>Last seen</th></tr>';
  for(const s of d.leaderboard){
    h+='<tr><td class=big>'+esc(s.name)+'</td><td class=code>'+esc(s.operator)+'</td>'+
       '<td class="num big">'+s.count+'</td>'+
       '<td class="num down">'+(s.down||'')+'</td>'+
       '<td class="num up">'+(s.up||'')+'</td>'+
       '<td>'+fmt(s.last)+'</td></tr>';
  }
  h+='</table><h2>Recent passages</h2><table><tr><th>When</th><th>Ship</th>'+
     '<th>Direction</th></tr>';
  for(const r of d.recent){
    const dir=r.direction==='downbound'?'<span class=down>&#9660; downbound</span>'
                                       :'<span class=up>&#9650; upbound</span>';
    h+='<tr><td>'+fmt(r.pass_time)+'</td><td>'+esc(r.name)+'</td><td>'+dir+'</td></tr>';
  }
  h+='</table>';
  c.innerHTML=h;
}
function esc(s){return (s||'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function fmt(iso){ if(!iso)return '';
  const d=new Date(iso); if(isNaN(d))return iso;
  return d.toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
}
load(); setInterval(load, 30000);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/data":
            rows = load_passages()
            leaderboard, recent = summarize(rows)
            payload = json.dumps({
                "total": len(rows),
                "unique": len(leaderboard),
                "down": sum(1 for r in rows if r.get("direction") == "downbound"),
                "up": sum(1 for r in rows if r.get("direction") == "upbound"),
                "leaderboard": leaderboard,
                "recent": recent,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        elif self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE.encode())
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    import sys
    DEMO = "--demo" in sys.argv
    print("Ship Wall -- passage stats page")
    if DEMO:
        print("  DEMO MODE: serving built-in sample data (no real log read)")
    else:
        print(f"  Reading: {PASSAGE_LOG}")
    print(f"  Serving on http://0.0.0.0:{PORT}  (open from any device on the LAN)")
    print(f"  e.g. http://<pi-ip>:{PORT} or http://shipwall.local:{PORT}")
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()