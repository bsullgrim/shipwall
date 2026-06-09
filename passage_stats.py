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
FUN_STATS = os.environ.get("FUN_STATS", "fun_stats.json").strip()
DEMO = False                       # set by --demo; serves built-in sample data


def load_fun_stats():
    try:
        with open(FUN_STATS, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return None

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


def _year(r):
    return (r.get("pass_time", "") or "")[:4]


def summarize(rows):
    """Leaderboard keyed on name+operator (works without MMSI, and counts a
    ship as itself across years). Returns leaderboard + recent passages."""
    by_ship = defaultdict(lambda: {"name": "", "operator": "", "count": 0,
                                   "down": 0, "up": 0, "last": ""})
    for r in rows:
        name = (r.get("name", "") or "").strip()
        op = (r.get("operator", "") or "").strip()
        key = (name.upper(), op)
        s = by_ship[key]
        s["name"] = name or s["name"]
        s["operator"] = op or s["operator"]
        s["count"] += 1
        d = r.get("direction")
        if d == "downbound":
            s["down"] += 1
        elif d == "upbound":
            s["up"] += 1
        pt = r.get("pass_time", "")
        if pt > s["last"]:
            s["last"] = pt
    leaderboard = sorted(by_ship.values(),
                         key=lambda x: (-x["count"], x["name"]))
    recent = sorted(rows, key=lambda r: r.get("pass_time", ""), reverse=True)[:25]
    return leaderboard, recent


def totals(rows):
    return {
        "total": len(rows),
        "unique": len({((r.get("name") or "").upper(), r.get("operator") or "")
                       for r in rows}),
        "down": sum(1 for r in rows if r.get("direction") == "downbound"),
        "up": sum(1 for r in rows if r.get("direction") == "upbound"),
    }


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Ship Wall &mdash; passages</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
  :root{color-scheme:dark}
  body{margin:0;background:#0e1116;color:#d8dee9;font-family:system-ui,-apple-system,sans-serif;padding:16px}
  h1{font-size:20px;margin:0 0 2px} h2{font-size:15px;color:#8b98a9;margin:24px 0 8px;font-weight:600}
  .sub{color:#6e7b8c;font-size:13px;margin-bottom:12px}
  table{border-collapse:collapse;width:100%;font-size:14px}
  th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #1d2530}
  th{color:#8b98a9;font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.04em}
  tr:hover td{background:#141a22}
  .num{text-align:right;font-variant-numeric:tabular-nums}
  .code{color:#9ad0ff;font-weight:600}
  .down{color:#50c8ff} .up{color:#ff963c}
  .big{font-size:15px;font-weight:600}
  .rank{color:#6e7b8c;font-variant-numeric:tabular-nums;width:1.6em}
  .tot{display:flex;gap:16px;margin:10px 0 4px;flex-wrap:wrap}
  .tot div{background:#141a22;border-radius:8px;padding:10px 16px}
  .tot b{display:block;font-size:24px;color:#fff} .tot span{font-size:12px;color:#8b98a9}
  .empty{color:#6e7b8c;padding:20px 0}
  .tabs{display:flex;gap:4px;margin:4px 0 4px;flex-wrap:wrap}
  .tabs button{background:#141a22;color:#9aa7b6;border:1px solid #1d2530;border-radius:7px;
    padding:7px 14px;font-size:14px;cursor:pointer}
  .tabs button.on{background:#1d3550;color:#cfe6ff;border-color:#2a4a6e}
  select{background:#141a22;color:#d8dee9;border:1px solid #1d2530;border-radius:7px;padding:6px 10px;font-size:14px}
  a{color:#9ad0ff}
</style></head><body>
<h1>St. Lawrence Ship Wall</h1>
<div class=sub>Vessels passing Danger Island &mdash; live detections plus the historical spotting log</div>
<div class=tabs id=tabs></div>
<div id=content><div class=empty>loading&hellip;</div></div>
<script>
let DATA=null, VIEW='current';
async function load(){
  const r=await fetch('/data'); DATA=await r.json();
  renderTabs(); render();
}
function renderTabs(){
  const t=document.getElementById('tabs');
  const tabs=[['current','This year ('+DATA.current_year+')'],
              ['lifetime','Lifetime'],
              ['history','By year']];
  if(DATA.fun) tabs.push(['fun','Hall of Fame']);
  t.innerHTML=tabs.map(([k,label])=>
    '<button class="'+(VIEW===k?'on':'')+'" data-view="'+k+'">'+label+'</button>'
  ).join('');
  t.querySelectorAll('button').forEach(btn=>{
    btn.onclick=()=>{ VIEW=btn.getAttribute('data-view'); renderTabs(); render(); };
  });
}
function totsBlock(t){
  return '<div class=tot>'+
    '<div><b>'+t.total+'</b><span>passages</span></div>'+
    '<div><b>'+t.unique+'</b><span>distinct vessels</span></div>'+
    '<div><b>'+t.down+'</b><span>downbound</span></div>'+
    '<div><b>'+t.up+'</b><span>upbound</span></div></div>';
}
function leaderboardTable(lb, limit){
  if(!lb.length) return '<div class=empty>No passages recorded.</div>';
  let h='<table><tr><th class=rank>#</th><th>Ship</th><th>Op</th>'+
     '<th class=num>Passes</th><th class=num>Down</th><th class=num>Up</th><th>Last</th></tr>';
  lb.slice(0,limit||lb.length).forEach((s,i)=>{
    h+='<tr><td class=rank>'+(i+1)+'</td>'+
       '<td class=big>'+esc(s.name)+'</td><td class=code>'+esc(s.operator)+'</td>'+
       '<td class="num big">'+s.count+'</td>'+
       '<td class="num down">'+(s.down||'')+'</td>'+
       '<td class="num up">'+(s.up||'')+'</td>'+
       '<td>'+fmtDay(s.last)+'</td></tr>';
  });
  return h+'</table>';
}
function recentTable(recent){
  if(!recent||!recent.length) return '';
  let h='<h2>Recent passages</h2><table><tr><th>When</th><th>Ship</th><th>Direction</th></tr>';
  for(const r of recent){
    h+='<tr><td>'+fmtDay(r.pass_time)+'</td><td>'+esc(r.name)+'</td><td>'+dirLabel(r.direction)+'</td></tr>';
  }
  return h+'</table>';
}
function dirLabel(d){
  if(d==='downbound') return '<span class=down>&#9660; downbound</span>';
  if(d==='upbound') return '<span class=up>&#9650; upbound</span>';
  return '<span style="color:#6e7b8c">&mdash;</span>';
}
function render(){
  const c=document.getElementById('content');
  if(VIEW==='current'){
    const v=DATA.current;
    c.innerHTML='<h2>'+DATA.current_year+' season</h2>'+totsBlock(v.totals)+
      '<h2>Most frequent this year</h2>'+leaderboardTable(v.leaderboard)+
      recentTable(v.recent);
  } else if(VIEW==='lifetime'){
    const v=DATA.lifetime;
    c.innerHTML='<h2>All-time ('+DATA.years[DATA.years.length-1]+'&ndash;'+DATA.years[0]+')</h2>'+
      totsBlock(v.totals)+
      '<h2>Most frequent visitors, all-time</h2>'+leaderboardTable(v.leaderboard);
  } else if(VIEW==='history'){
    // history: a year picker + that year's board
    let h='<h2>Browse a year</h2><select id=yrsel onchange="renderYear()">';
    for(const y of DATA.years) h+='<option value="'+y+'">'+y+'</option>';
    h+='</select><div id=yearbox></div>';
    c.innerHTML=h; renderYear();
  } else {
    renderFun(c);
  }
}
function renderFun(c){
  const f=DATA.fun;
  if(!f){ c.innerHTML='<div class=empty>No log stats available.</div>'; return; }
  const t=f.totals;
  let h='<div class=tot>'+
    '<div><b>'+t.distinct_ships+'</b><span>distinct ships ('+t.span+')</span></div>'+
    '<div><b>'+t.distinct_fleets+'</b><span>operators seen</span></div>'+
    '<div><b>'+t.one_timers+'</b><span>seen only once</span></div></div>';

  // Milestones first -- the personal ones
  if(f.milestones && f.milestones.length){
    h+='<h2>Milestones</h2><table>';
    for(const m of f.milestones)
      h+='<tr><td style="color:#8b98a9;white-space:nowrap">'+esc(m.date)+'</td>'+
         '<td class=big>'+esc(m.name)+'</td><td>'+esc(m.comment)+'</td></tr>';
    h+='</table>';
  }
  // Exotics
  if(f.exotics && f.exotics.length){
    h+='<h2>Exotics &amp; oddities</h2><table>'+
       '<tr><th>Type</th><th>Ship</th><th>Note</th></tr>';
    for(const e of f.exotics)
      h+='<tr><td class=code style="white-space:nowrap">'+esc(e.category)+'</td>'+
         '<td class=big>'+esc(e.name)+'</td>'+
         '<td style="color:#9aa7b6">'+esc(e.comment)+'</td></tr>';
    h+='</table>';
  }
  // New ships per year (lifers)
  if(f.lifers_per_year && f.lifers_per_year.length){
    const mx=Math.max(...f.lifers_per_year.map(x=>x.new));
    h+='<h2>New ships added each year</h2><table>';
    for(const y of f.lifers_per_year){
      const w=Math.round(y.new/mx*180);
      h+='<tr><td style="white-space:nowrap">'+esc(y.year)+'</td>'+
         '<td style="width:100%"><span style="display:inline-block;height:11px;'+
         'background:#2a6cb8;border-radius:2px;width:'+w+'px;vertical-align:middle"></span> '+
         '<span class=num>'+y.new+'</span></td></tr>';
    }
    h+='</table>';
  }
  // Busiest days
  if(f.busiest_days && f.busiest_days.length){
    h+='<h2>Busiest days ever</h2><table><tr><th>Date</th><th class=num>Ships</th></tr>';
    for(const b of f.busiest_days)
      h+='<tr><td>'+fmtDay(b.date)+'</td><td class="num big">'+b.count+'</td></tr>';
    h+='</table>';
  }
  // Rarities
  if(f.rarities_sample && f.rarities_sample.length){
    h+='<h2>One and done <span style="color:#6e7b8c;font-weight:400">('+
       f.rarities_total+' ships seen exactly once)</span></h2>'+
       '<div style="color:#9aa7b6;line-height:1.7">'+
       f.rarities_sample.map(esc).join(' &middot; ')+
       (f.rarities_total>f.rarities_sample.length?' &hellip;':'')+'</div>';
  }
  c.innerHTML=h;
}
function renderYear(){
  const y=document.getElementById('yrsel').value;
  const v=DATA.by_year[y];
  document.getElementById('yearbox').innerHTML=
    totsBlock(v.totals)+leaderboardTable(v.leaderboard);
}
function esc(s){return (s||'').replace(/[&<>]/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[m]));}
function fmtDay(iso){ if(!iso)return '';
  const d=new Date(iso); if(isNaN(d))return iso;
  return d.toLocaleDateString([], {year:'numeric',month:'short',day:'numeric'});
}
load(); setInterval(load, 60000);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/data":
            rows = load_passages()
            years = sorted({_year(r) for r in rows if _year(r)}, reverse=True)
            cur_year = years[0] if years else ""
            cur_rows = [r for r in rows if _year(r) == cur_year]
            cur_lb, cur_recent = summarize(cur_rows)
            life_lb, life_recent = summarize(rows)
            # per-year leaderboards (top entries each) for the history view
            by_year = {}
            for y in years:
                yr_rows = [r for r in rows if _year(r) == y]
                lb, _ = summarize(yr_rows)
                by_year[y] = {"totals": totals(yr_rows), "leaderboard": lb}
            payload = json.dumps({
                "current_year": cur_year,
                "years": years,
                "current": {"totals": totals(cur_rows),
                            "leaderboard": cur_lb, "recent": cur_recent},
                "lifetime": {"totals": totals(rows),
                             "leaderboard": life_lb, "recent": life_recent},
                "by_year": by_year,
                "fun": load_fun_stats(),
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
