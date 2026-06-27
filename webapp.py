#!/usr/bin/env python3
"""
webapp.py -- unified public front door for the Ship Wall.

Serves ONE site (one port, one URL -> one Tailscale Funnel) with two tabs:

  Live Panel  -- a browser mirror of exactly what the LED wall is showing,
                 fed by register_service.py pushing frames to POST /frame
                 (set MIRROR_HOST=<this-host>:<port> on the service).
  Passages    -- the crossing leaderboards / Hall of Fame from passages.csv.

It reuses the existing renderers and stats logic rather than reimplementing
them: the panel page and its sprite/font assets come from register_panel.py,
and the leaderboard data + page come from passage_stats.py. Each tab is loaded
in a SAME-ORIGIN iframe so the two self-contained front-ends keep their own
JS/CSS without colliding, while still living under one origin so their
fetch('/latest') and fetch('/data') calls resolve here.

IMPORTANT: run from the repo root -- register_panel loads ship_sprites.h,
font5x7.js and emmett_panel_data.json by RELATIVE path on import. Use an
absolute PASSAGE_LOG so stats resolve regardless of CWD.

Typical Pi deployment:

    cd /home/grims/shipwall
    PASSAGE_LOG=/home/grims/shipwall/passages.csv \
    FUN_STATS=/home/grims/shipwall/fun_stats.json \
    MMSI_DB=/home/grims/shipwall/mmsi_db.json \
    WEBAPP_PORT=8080 python3 webapp.py

Point the service at it (best-effort mirror, never blocks the wall):

    MIRROR_HOST=localhost:8080 ... python3 register_service.py

Expose it publicly with no guest accounts:

    sudo tailscale funnel --bg 8080
"""

import json
import os
import threading
import http.server
import socketserver

# Panel renderer + assets. Importing does NOT start a server -- register_panel
# guards serve_forever behind __main__.
from register_panel import (
    SPRITES_HEX, MINIS_HEX, SPRITE_SIZE, FONT, EMMETT_DATA,
    PAGE as PANEL_PAGE,
)

# Stats page + the very functions its /data endpoint calls, so the payload here
# is identical to passage_stats.py -- one source of truth, no schema fork.
# (_year is reused deliberately rather than re-implemented for the same reason.)
from passage_stats import (
    load_passages, summarize, totals, busiest_day, load_fun_stats, _year,
    PAGE as STATS_PAGE,
)

PORT = int(os.environ.get("WEBAPP_PORT", "8080"))

# Latest live frame, fed by register_service.py via POST /frame. Same contract
# as register_panel.py's sink: best-effort, last-write-wins, served at /latest.
_latest = {"ts": 0, "bright": 128, "closed": False, "hours": 18, "ships": []}
_lock = threading.Lock()


# Self-contained FEED OFFLINE banner, injected into the panel page so
# register_panel.py needs no edits. It polls /latest on its own and manages its
# own element, depending on nothing in the page but a </body> anchor. Prefers an
# explicit feed_age_secs from the service if the frame carries one; otherwise it
# falls back to the frame's build timestamp (ts).
OFFLINE_BANNER = """
<script>
(function(){
  var STALE = 300;  // seconds without a fresh frame -> show offline
  var bar = document.createElement('div');
  bar.style.cssText = 'display:none;position:fixed;top:0;left:0;right:0;z-index:9999;'
    + 'background:#5a1a1a;color:#ffd0d0;border-bottom:1px solid #a33;padding:6px 12px;'
    + 'text-align:center;font:600 14px system-ui,-apple-system,sans-serif;letter-spacing:.03em';
  document.body.appendChild(bar);
  function hhmm(ts){ return new Date(ts*1000).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'}); }
  function check(){
    fetch('/latest').then(function(r){ return r.json(); }).then(function(f){
      var now = Date.now()/1000, age, last;
      if (f && typeof f.feed_age_secs === 'number') { age = f.feed_age_secs; last = now - age; }
      else if (f && f.ts) { age = now - f.ts; last = f.ts; }
      else { age = 1e9; last = null; }
      if (age > STALE) {
        bar.textContent = 'FEED OFFLINE \\u2014 last seen ' + (last ? hhmm(last) : '--:--');
        bar.style.display = 'block';
      } else { bar.style.display = 'none'; }
    }).catch(function(){});
  }
  setInterval(check, 2000); check();
})();
</script>
"""


def panel_page():
    """Register panel HTML with sprite/font/emmett tables baked in (the same
    substitution register_panel.py performs for '/'), plus the injected offline
    banner so the panel file itself needs no changes."""
    html = (PANEL_PAGE
            .replace("%SPRITES%", json.dumps(SPRITES_HEX))
            .replace("%MINIS%", json.dumps(MINIS_HEX))
            .replace("%SPRITE_SIZE%", str(SPRITE_SIZE))
            .replace("%FONT%", json.dumps(FONT))
            .replace("%EMMETT%", json.dumps(EMMETT_DATA)))
    return html.replace("</body>", OFFLINE_BANNER + "</body>")


def stats_payload():
    """Rebuild the /data JSON exactly as passage_stats.py does, by calling the
    same functions it calls."""
    rows = load_passages()
    years = sorted({_year(r) for r in rows if _year(r)}, reverse=True)
    cur_year = years[0] if years else ""
    cur_rows = [r for r in rows if _year(r) == cur_year]
    cur_lb, cur_recent = summarize(cur_rows)
    life_lb, life_recent = summarize(rows)
    by_year = {}
    for y in years:
        yr_rows = [r for r in rows if _year(r) == y]
        lb, _ = summarize(yr_rows)
        by_year[y] = {"totals": totals(yr_rows), "leaderboard": lb}
    return {
        "current_year": cur_year,
        "years": years,
        "current": {"totals": totals(cur_rows), "leaderboard": cur_lb,
                    "recent": cur_recent, "busiest": busiest_day(cur_rows)},
        "lifetime": {"totals": totals(rows), "leaderboard": life_lb,
                     "recent": life_recent},
        "by_year": by_year,
        "fun": load_fun_stats(),
    }


SHELL = """<!doctype html><html><head><meta charset=utf-8>
<title>St. Lawrence Ship Wall</title>
<meta name=viewport content="width=device-width, initial-scale=1">
<style>
  :root{color-scheme:dark}
  html,body{margin:0;height:100%;background:#0e1116;color:#d8dee9;
    font-family:system-ui,-apple-system,sans-serif}
  header{padding:10px 14px 0}
  h1{font-size:18px;margin:0 0 2px}
  .sub{color:#6e7b8c;font-size:12px;margin-bottom:8px}
  .tabs{display:flex;gap:6px}
  .tabs button{background:#141a22;color:#9aa7b6;border:1px solid #1d2530;
    border-top-left-radius:8px;border-top-right-radius:8px;border-bottom:none;
    padding:9px 18px;font-size:14px;cursor:pointer}
  .tabs button.on{background:#0e1116;color:#cfe6ff;border-color:#2a4a6e}
  .frame-wrap{position:absolute;top:96px;left:0;right:0;bottom:0;
    border-top:1px solid #1d2530}
  iframe{width:100%;height:100%;border:0;background:#0e1116}
  iframe.hide{display:none}
</style></head><body>
<header>
  <h1>St. Lawrence Ship Wall</h1>
  <div class=sub>Live vessel traffic past Danger Island, upper St. Lawrence Seaway</div>
  <div class=tabs>
    <button id=tab-panel class=on onclick="show('panel')">Live Panel</button>
    <button id=tab-stats onclick="show('stats')">Passages</button>
  </div>
</header>
<div class=frame-wrap>
  <iframe id=if-panel src="/panel"></iframe>
  <iframe id=if-stats class=hide src="/stats"></iframe>
</div>
<script>
function show(which){
  const panel = which==='panel';
  document.getElementById('if-panel').classList.toggle('hide',!panel);
  document.getElementById('if-stats').classList.toggle('hide',panel);
  document.getElementById('tab-panel').classList.toggle('on',panel);
  document.getElementById('tab-stats').classList.toggle('on',!panel);
}
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self):
        if self.path == "/frame":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            try:
                frame = json.loads(body)
                with _lock:
                    global _latest
                    _latest = frame
                self._send(200, b"ok", "text/plain")
            except Exception as e:
                self._send(400, str(e).encode(), "text/plain")
        elif self.path == "/capture":
            # Public mirror: swallow capture posts, never write to disk.
            n = int(self.headers.get("Content-Length", 0))
            self.rfile.read(n)
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, SHELL.encode())
        elif self.path == "/panel":
            self._send(200, panel_page().encode())
        elif self.path == "/stats":
            self._send(200, STATS_PAGE.encode())
        elif self.path == "/latest":
            with _lock:
                payload = json.dumps(_latest).encode()
            self._send(200, payload, "application/json")
        elif self.path == "/data":
            self._send(200, json.dumps(stats_payload()).encode(), "application/json")
        else:
            self._send(404, b"")


if __name__ == "__main__":
    print("Ship Wall -- unified web app")
    print(f"  Panel assets: {len(SPRITES_HEX)} sprites @ {SPRITE_SIZE}x{SPRITE_SIZE}, "
          f"{len(FONT)} glyphs")
    print(f"  Serving on http://0.0.0.0:{PORT}   (/, /panel, /stats)")
    print(f"  Service mirror -> POST http://<host>:{PORT}/frame  (set MIRROR_HOST)")
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()
