#!/usr/bin/env python3
"""
register_panel.py  --  Browser mock for the REGISTER variant of the Ship Wall.

Stands in for the ESP32: same POST /frame endpoint, but renders the recent-
sightings register (see register_service.py) in two auto-alternating modes:

  BOARD   -- a departure-board list, one line per ship:
             [funnel chip] CODE  dir  NAME .......... Xh ago
             Scrolls vertically when there are more ships than fit.

  DETAIL  -- one ship at a time, full screen: big 32px funnel on the left,
             rich AIS fields on the right (type/flag, dimensions, draught,
             nav status, destination, ETA, last-seen). Cycles through the
             register, then hands back to the board.

Run alongside the register service:

    python3 register_panel.py            # http://localhost:8080
    REGISTER_HOURS=18 AISSTREAM_KEY=... ESP32_HOST=localhost:8080 \
        python3 register_service.py

Shares ship_sprites.h and font5x7.js with the live version, so sprites and
text render identically to the hardware.
"""

import base64
import datetime
import http.server
import json
import os
import re
import socketserver
import threading

PORT = 8080
CAPTURE_DIR = "captures"
_latest = {"ts": 0, "bright": 128, "closed": False, "hours": 18, "ships": []}
_lock = threading.Lock()


def save_capture(payload):
    """Save a base64 PNG from the browser to captures/<ship>_<timestamp>.png."""
    name = payload.get("name", "ship")
    data_url = payload.get("image", "")
    if "," not in data_url:
        return
    raw = base64.b64decode(data_url.split(",", 1)[1])
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "ship"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(CAPTURE_DIR, f"{ts}_{safe}.png")
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img = img.resize((img.width * 6, img.height * 6), Image.NEAREST)
        img.save(path)
    except Exception:
        with open(path, "wb") as f:
            f.write(raw)
    print(f"[capture] saved {path}")


def load_sprites(path="ship_sprites.h"):
    try:
        text = open(path).read()
    except FileNotFoundError:
        return {}, 16
    size_m = re.search(r"#define SPRITE_SIZE (\d+)", text)
    size = int(size_m.group(1)) if size_m else 16
    sprites = {}
    for m in re.finditer(r"SPR_(\w+)\[\d+\]\s*PROGMEM\s*=\s*\{([^}]*)\}", text):
        key = m.group(1)
        vals = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{4}", m.group(2))]
        sprites[key] = vals
    return sprites, size


SPRITES, SPRITE_SIZE = load_sprites()


def load_font(path="font5x7.js"):
    try:
        text = open(path).read()
    except FileNotFoundError:
        return {}
    start = text.index("{", text.index("FONT5x7"))
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                body = text[start:i + 1]
                break
    body = re.sub(r"//.*", "", body)
    font = {}
    for m in re.finditer(r"'((?:\\.|[^'\\])*)'\s*:\s*\[([^\]]*)\]", body):
        key = m.group(1).encode().decode("unicode_escape")
        vals = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]+", m.group(2))]
        font[key] = vals
    return font


FONT = load_font()


def rgb565_to_hex(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return f"#{r*255//31:02x}{g*255//63:02x}{b*255//31:02x}"


SPRITES_HEX = {k: [rgb565_to_hex(c) for c in vals] for k, vals in SPRITES.items()}


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Ship Wall register</title>
<style>
  body{background:#111;color:#888;font-family:monospace;text-align:center;padding-top:30px}
  #panel{image-rendering:pixelated;background:#000;border:2px solid #333;
         width:768px;height:384px;margin:20px auto;display:block}
  #meta{font-size:13px}
</style></head><body>
<h3>St. Lawrence Ship Wall &mdash; recent-sightings register (128&times;64)</h3>
<canvas id=panel width=128 height=64></canvas>
<div id=meta>waiting for first frame&hellip;</div>
<script>
const SPRITES = %SPRITES%;
const SPRITE_SIZE = %SPRITE_SIZE%;
const FONT = %FONT%;
const W=128, H=64;
const C={name:'#ffc828',label:'#5a8cff',value:'#e6e6e6',dim:'#6e6e6e',accent:'#3cdc78',
         code:'#9ad0ff'};
const C2={down:'#50c8ff',up:'#ff963c'};
const cv=document.getElementById('panel'), cx=cv.getContext('2d');
cx.imageSmoothingEnabled=false;

const GLYPH_W=5, GLYPH_ADV=6, GLYPH_H=7;
function glyph(ch,x,y,col){
  const cols=FONT[ch]||FONT['?'];
  cx.fillStyle=col;
  for(let c=0;c<GLYPH_W;c++){
    const bits=cols[c];
    for(let r=0;r<GLYPH_H;r++){ if(bits&(1<<r)) cx.fillRect(x+c,y+r,1,1); }
  }
}
function txt(s,x,y,col){ let gx=x; for(const ch of s){ glyph(ch,gx,y,col); gx+=GLYPH_ADV; } }
function txtClip(s,x,y,maxX,col){
  let gx=x;
  for(const ch of s){ if(gx+GLYPH_W>maxX) break; glyph(ch,gx,y,col); gx+=GLYPH_ADV; }
}
function textWidth(s){ return s.length*GLYPH_ADV-1; }
function centered(s,y,col){ txt(s,Math.floor((W-textWidth(s))/2),y,col); }
function rightAlign(s,rx,y,col){ txt(s,rx-textWidth(s),y,col); }

// Scaled nearest-neighbour sprite blit; black transparent.
function spriteScaled(key,ox,oy,dim){
  const px=SPRITES[key]||SPRITES['UNKNOWN']; if(!px)return;
  for(let y=0;y<dim;y++){
    const sy=(y*SPRITE_SIZE/dim)|0;
    for(let x=0;x<dim;x++){
      const sx=(x*SPRITE_SIZE/dim)|0;
      const c=px[sy*SPRITE_SIZE+sx];
      if(c&&c!=='#000000'){cx.fillStyle=c;cx.fillRect(ox+x,oy+y,1,1);}
    }
  }
}

// Reused up/down/moored direction glyph (corrected: D points down, U points up).
function dirGlyph(dir,x,y){
  const col=dir==='D'?C2.down:dir==='U'?C2.up:C.dim;
  cx.fillStyle=col;
  if(dir==='D'){for(let r=0;r<4;r++)for(let c=r;c<=4-r;c++)cx.fillRect(x+c,y+r,1,1);}
  else if(dir==='U'){for(let r=0;r<4;r++)for(let c=r;c<=4-r;c++)cx.fillRect(x+c,y+(3-r),1,1);}
  else if(dir==='M'){for(let r=1;r<4;r++)for(let c=1;c<4;c++)cx.fillRect(x+c,y+r,1,1);}
  else cx.fillRect(x+2,y+2,1,1);
}

// ---- BOARD mode: scrolling departure-board list ----------------------------
const ROW_H=10;                       // px per ship row
function drawBoard(ships, animT){
  const n=ships.length;
  if(n===0){ centered('NO SHIPS',H/2-8,C.accent); centered('in window',H/2+2,C.dim); return; }
  // Header strip
  const visibleRows=Math.floor(H/ROW_H);
  // Vertical scroll if more ships than fit. Smooth wrap with pauses.
  let scroll=0;
  const totalH=n*ROW_H;
  if(totalH>H){
    const SPEED=8;                    // px/sec
    const PAUSE=2000;                 // ms top/bottom hold
    const travel=totalH-H;
    const scrollMs=travel/SPEED*1000;
    const cycle=PAUSE+scrollMs+PAUSE+scrollMs;  // hold,down,hold,up
    let p=animT%cycle;
    if(p<PAUSE) scroll=0;
    else if(p<PAUSE+scrollMs) scroll=(p-PAUSE)/scrollMs*travel;
    else if(p<PAUSE+scrollMs+PAUSE) scroll=travel;
    else scroll=travel-(p-PAUSE-scrollMs-PAUSE)/scrollMs*travel;
  }
  for(let i=0;i<n;i++){
    const y=Math.round(i*ROW_H-scroll);
    if(y+ROW_H<0||y>H) continue;
    drawRow(ships[i],y);
  }
}
function drawRow(s,y){
  // funnel chip (8px), code, dir, name, age right-aligned
  spriteScaled(s.op,0,y+1,8);
  txt(s.code,10,y+2,C.code);
  dirGlyph(s.dir,29,y+2);
  // name fills the middle; age right-aligned, name clipped before it
  const age=s.age||'';
  const ageX=W-textWidth(age);
  txtClip(s.name,36,y+2,ageX-2,C.value);
  txt(age,ageX,y+2,C.dim);
}

// ---- DETAIL mode: one ship, full screen ------------------------------------
const marquee={};
function txtMarquee(s,x,y,maxX,col,key,animT){
  const winW=maxX-x, fullW=s.length*GLYPH_ADV;
  if(fullW<=winW){ txt(s,x,y,col); return; }
  const SPEED=18, PAUSE=1500, travel=fullW-winW, scrollMs=travel/SPEED*1000;
  const cycle=PAUSE+scrollMs+PAUSE;
  if(!marquee[key]) marquee[key]={t0:animT};
  let p=(animT-marquee[key].t0)%cycle, off;
  if(p<PAUSE) off=0; else if(p<PAUSE+scrollMs) off=(p-PAUSE)/scrollMs*travel; else off=travel;
  let gx=x-off;
  for(const ch of s){ if(gx>x-GLYPH_ADV&&gx<maxX) glyph(ch,Math.round(gx),y,col); gx+=GLYPH_ADV; }
}
function drawDetail(s,animT){
  const edge=W-1;
  // Full-width name across the very top (marquee if it overflows), with the
  // direction glyph pinned at the far right of that row.
  if(s.dir) dirGlyph(s.dir,edge-5,1);
  txtMarquee(s.name,1,1,edge-6,C.name,'d-name',animT);

  // Funnel justified to the top-left, just below the name row.
  const fdim=SPRITE_SIZE;
  const ftop=9;                          // first row under the name
  spriteScaled(s.op,0,ftop,fdim);

  // Fields beside the funnel (right of it, starting at the name's baseline).
  const tx=fdim+4;
  let yy=ftop+2;
  let l1=s.type||'VSL'; if(s.flag) l1+=' '+s.flag;
  txtClip(l1,tx,yy,edge,C.label); yy+=10;
  if(s.length&&s.beam){ txtClip(s.length+'x'+s.beam+'m',tx,yy,edge,C.value); yy+=10; }
  if(s.age){ txtClip('seen '+s.age,tx,yy,edge,C.dim); yy+=10; }

  // Below the funnel: full-width rows for the remaining fields.
  let by=ftop+fdim+1;                    // first row under the funnel (~42)
  let line2=[];
  if(s.draught!=null) line2.push(s.draught.toFixed(1)+'m draft');
  if(s.navstat) line2.push(s.navstat);
  if(line2.length){ txtClip(line2.join('  '),1,by,edge,C.value); by+=10; }
  if(s.dest){ let d='>'+s.dest; if(s.eta) d+=' '+s.eta; txtClip(d,1,by,edge,C.value); }

  // River progress line along the very bottom: Lake Ontario (left) -> Montreal
  // (right), with the ship's position and the home reference (Danger Island).
  drawRiverLine(s.progress, latest.home, s.dir, H-2);
}

// Horizontal "where on the river" indicator at row `y`.
// Lake Ontario at the left end, Montreal at the right; ship dot + home tick.
// Kept compact (within ~3px) so it stays clear of the text rows above.
function drawRiverLine(progress, home, dir, y){
  const x0=2, x1=W-3, span=x1-x0;
  cx.fillStyle=C.dim;
  for(let x=x0;x<=x1;x++) cx.fillRect(x,y,1,1);
  // end caps (lake / montreal)
  cx.fillStyle='#555';
  for(let d=-1;d<=1;d++){ cx.fillRect(x0,y+d,1,1); cx.fillRect(x1,y+d,1,1); }
  // home reference tick (Danger Island)
  if(home!=null){
    const hx=Math.round(x0+home*span);
    cx.fillStyle='#3cdc78';
    for(let d=-1;d<=1;d++) cx.fillRect(hx,y+d,1,1);
  }
  // ship position dot, colored by direction
  if(progress!=null){
    const sx=Math.round(x0+progress*span);
    const col=dir==='D'?C2.down:dir==='U'?C2.up:'#e6e6e6';
    cx.fillStyle=col;
    cx.fillRect(sx-1,y-1,3,3);
  }
}

// ---- Mode controller --------------------------------------------------------
// BOARD for BOARD_MS, then DETAIL cycling DETAIL_MS each, then back to BOARD.
const BOARD_MS=12000, DETAIL_MS=5000;
let lastFrame=0, animT=0, latest=_blank();
function _blank(){return {ts:0,bright:128,closed:false,hours:18,ships:[]};}

function render(f,animT){
  cx.clearRect(0,0,W,H);
  if(lastFrame && Date.now()-lastFrame>60000){
    centered('WAITING',H/2-8,C.accent); centered('for data',H/2+2,C.dim);
    cv.style.opacity=1; return;
  }
  const ships=f.ships||[];
  if(f.closed && ships.length===0){
    centered('SEAWAY',H/2-16,C.accent); centered('CLOSED',H/2-4,C.name);
    centered('reopens March',H/2+8,C.dim); cv.style.opacity=1; return;
  }
  // Mode timing
  const n=ships.length;
  const cycle=BOARD_MS + (n>0?n*DETAIL_MS:0);
  const p=animT%cycle;
  if(n===0 || p<BOARD_MS){
    drawBoard(ships,animT);
  }else{
    const idx=Math.floor((p-BOARD_MS)/DETAIL_MS)%n;
    drawDetail(ships[idx],animT);
  }
  cv.style.opacity=(0.25+0.75*(f.bright/255)).toFixed(2);
}

async function poll(){
  try{
    const r=await fetch('/latest'); const f=await r.json();
    if(f.ts) lastFrame=Date.now();
    latest=f;
    document.getElementById('meta').textContent=
      (f.ships||[]).length+' ships in last '+(f.hours||18)+'h  |  bright '+
      f.bright+'/255  |  '+(f.closed?'CLOSED':'in season');
  }catch(e){}
}
function frame(t){ animT=t; render(latest,animT); maybeCapture(latest); requestAnimationFrame(frame); }

// Capture a screenshot the first time a vessel appears in the register.
// Deduped by MMSI so each ship is grabbed once per visit, not every frame.
let capturedMMSI = new Set();
function maybeCapture(f){
  const ships=f.ships||[];
  const present=new Set(ships.map(s=>s.mmsi));
  for(const s of ships){
    if(!capturedMMSI.has(s.mmsi)){
      capturedMMSI.add(s.mmsi);
      // Defer slightly so the current draw is on the canvas.
      const nm=s.name;
      setTimeout(()=>captureCanvas(nm),50);
    }
  }
  capturedMMSI=new Set([...capturedMMSI].filter(m=>present.has(m)));
}
function captureCanvas(name){
  try{
    const dataUrl=cv.toDataURL('image/png');
    fetch('/capture',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name,image:dataUrl})}).catch(()=>{});
  }catch(e){}
}
setInterval(poll,1000);
poll();
requestAnimationFrame(frame);
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        if self.path == "/frame":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            try:
                frame = json.loads(body)
                with _lock:
                    global _latest
                    _latest = frame
                print(f"frame: {len(frame.get('ships', []))} ships, "
                      f"bright {frame.get('bright')}, "
                      f"{'CLOSED' if frame.get('closed') else 'open'}")
                self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
            except Exception as e:
                self.send_response(400); self.end_headers(); self.wfile.write(str(e).encode())
        elif self.path == "/capture":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            try:
                save_capture(json.loads(body))
                self.send_response(200); self.end_headers()
            except Exception as e:
                print(f"[capture] failed: {e}")
                self.send_response(400); self.end_headers()
        else:
            self.send_response(404); self.end_headers()

    def do_GET(self):
        if self.path == "/latest":
            with _lock:
                payload = json.dumps(_latest).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers(); self.wfile.write(payload)
        elif self.path == "/" or self.path == "/index.html":
            page = (PAGE.replace("%SPRITES%", json.dumps(SPRITES_HEX))
                        .replace("%SPRITE_SIZE%", str(SPRITE_SIZE))
                        .replace("%FONT%", json.dumps(FONT)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers(); self.wfile.write(page.encode())
        else:
            self.send_response(404); self.end_headers()


if __name__ == "__main__":
    print("Ship Wall REGISTER panel")
    print(f"  Loaded {len(SPRITES)} sprites at {SPRITE_SIZE}x{SPRITE_SIZE}")
    print(f"  Open http://localhost:{PORT}")
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()