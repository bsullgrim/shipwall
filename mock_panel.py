#!/usr/bin/env python3
"""
mock_panel.py  --  Test the Ship Wall with live data, no hardware.

Stands in for the ESP32: exposes the same POST /frame endpoint the firmware
does, stores the latest frame, and serves a browser page at / that draws the
frame exactly as the 128x64 panel would -- adaptive layout, funnel sprites,
sun-based brightness, and all the idle/closed/waiting screens.

Run alongside the real Pi service:

    # terminal 1 -- the mock panel (this file)
    python3 mock_panel.py
    #   -> serving on http://localhost:8080  (open it in a browser)

    # terminal 2 -- the real service, pointed at the mock instead of an ESP32
    AISSTREAM_KEY=your_key ESP32_HOST=localhost:8080 python3 shipwall_service.py

Then watch live St. Lawrence traffic render in the browser. The sprite colors
are read from the same ship_sprites.h the firmware uses, so what you see is
what the panel will show.
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
_latest = {"ts": 0, "bright": 128, "closed": False, "vessels": []}
_lock = threading.Lock()


def save_capture(payload):
    """Save a base64 PNG from the browser to captures/<ship>_<timestamp>.png."""
    name = payload.get("name", "ship")
    data_url = payload.get("image", "")
    if "," not in data_url:
        return
    raw = base64.b64decode(data_url.split(",", 1)[1])
    os.makedirs(CAPTURE_DIR, exist_ok=True)
    # Sanitize the ship name into a safe filename fragment.
    safe = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "ship"
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(CAPTURE_DIR, f"{ts}_{safe}.png")
    # Upscale 6x (nearest-neighbour, crisp pixels) so the 128x64 is viewable.
    try:
        import io
        from PIL import Image
        img = Image.open(io.BytesIO(raw))
        img = img.resize((img.width * 6, img.height * 6), Image.NEAREST)
        img.save(path)
    except Exception:
        # PIL missing or failed: save the raw 128x64 PNG as-is.
        with open(path, "wb") as f:
            f.write(raw)
    print(f"[capture] saved {path}")


# --- Parse the generated sprite header so the preview matches the firmware ---
def load_sprites(path="ship_sprites.h"):
    """Return {key: [rgb565,...]} and the sprite size, parsed from the .h."""
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
    """Extract the FONT5x7 object from font5x7.js as a JSON-ready dict."""
    try:
        text = open(path).read()
    except FileNotFoundError:
        return {}
    # Pull the object literal between the first '{' after FONT5x7 and its close.
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
    # Convert the JS literal to JSON: strip comments, quote keys are already
    # quoted, hex numbers -> decimals, single quotes -> double quotes.
    body = re.sub(r"//.*", "", body)
    # keys are single-quoted; values are [0xNN,...]. Build dict by eval-free parse.
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


# Pre-convert sprites to nested hex arrays for the browser.
SPRITES_HEX = {
    k: [rgb565_to_hex(c) for c in vals] for k, vals in SPRITES.items()
}


PAGE = """<!doctype html><html><head><meta charset=utf-8>
<title>Ship Wall preview</title>
<style>
  body{background:#111;color:#888;font-family:monospace;text-align:center;padding-top:30px}
  #panel{image-rendering:pixelated;background:#000;border:2px solid #333;
         width:768px;height:384px;margin:20px auto;display:block}
  #meta{font-size:13px}
</style></head><body>
<h3>St. Lawrence Ship Wall &mdash; live preview (128&times;64)</h3>
<canvas id=panel width=128 height=64></canvas>
<div id=meta>waiting for first frame&hellip;</div>
<script>
const SPRITES = %SPRITES%;
const SPRITE_SIZE = %SPRITE_SIZE%;
const FONT = %FONT%;
const W=128, H=64;
const C={name:'#ffc828',label:'#5a8cff',value:'#e6e6e6',dim:'#6e6e6e',accent:'#3cdc78'};
const cv=document.getElementById('panel'), cx=cv.getContext('2d');
cx.imageSmoothingEnabled=false;

// Draw one character from the 5x7 bitmap font, pixel by pixel, exactly like
// the GFX library on hardware: each pixel is fully on or off, no anti-alias.
// Glyphs are 5 cols wide; characters advance 6px (5 + 1 spacing).
const GLYPH_W=5, GLYPH_ADV=6, GLYPH_H=7;
function glyph(ch,x,y,col){
  const cols=FONT[ch]||FONT['?'];
  cx.fillStyle=col;
  for(let c=0;c<GLYPH_W;c++){
    const bits=cols[c];
    for(let r=0;r<GLYPH_H;r++){
      if(bits&(1<<r)) cx.fillRect(x+c,y+r,1,1);
    }
  }
}
function txt(s,x,y,col){
  let cx0=x;
  for(const ch of s){ glyph(ch,cx0,y,col); cx0+=GLYPH_ADV; }
}
// Print clipped so nothing crosses maxX (exclusive). GLYPH_ADV px per char.
function txtClip(s,x,y,maxX,col){
  const maxChars=Math.floor((maxX-x)/GLYPH_ADV);
  if(maxChars<1)return;
  txt(s.length<=maxChars?s:s.slice(0,maxChars),x,y,col);
}
function textWidth(s){ return s.length*GLYPH_ADV-1; }
function centered(s,y,col){ txt(s,Math.floor((W-textWidth(s))/2),y,col); }

// Scalable nearest-neighbour sprite blit; black is transparent.
function spriteScaled(key,ox,oy,dim){
  const px=SPRITES[key]||SPRITES['UNKNOWN'];if(!px)return;
  for(let y=0;y<dim;y++){
    const sy=(y*SPRITE_SIZE/dim)|0;
    for(let x=0;x<dim;x++){
      const sx=(x*SPRITE_SIZE/dim)|0;
      const c=px[sy*SPRITE_SIZE+sx];
      if(c&&c!=='#000000'){cx.fillStyle=c;cx.fillRect(ox+x,oy+y,1,1);}
    }
  }
}

function band(v,top,bandH,big){
  const edge=DETAIL_W-1;                     // text stops before divider
  if(big){
    // Name across the top, large funnel lower-left, metrics to its right.
    txtClip(v.name,1,top+1,edge-5,C.name);
    const dirX=1+Math.min(v.name.length,Math.floor((edge-5-1)/6))*6+2;
    if(v.dir && dirX<edge-4) dirGlyph(v.dir,dirX,top+1);
    const fdim=SPRITE_SIZE;                 // 32px
    const fy=top+bandH-fdim;
    spriteScaled(v.op,1,fy,fdim);
    const tx=fdim+4;
    txt(v.sog.toFixed(1)+'kt',tx,top+14,C.value);
    txt('CRS'+v.cog,tx,top+26,C.value);
    if(v.drft!=null)txt('DR'+v.drft.toFixed(1),tx,top+38,C.value);
    if(v.dest)txtClip('>'+v.dest,tx,top+50,edge,C.dim);
  }else{
    // Split row: ~28px funnel inset left, two compact text rows right.
    let fdim=bandH-4; if(fdim>SPRITE_SIZE)fdim=SPRITE_SIZE;
    const fy=top+((bandH-fdim)>>1);
    spriteScaled(v.op,1,fy,fdim);
    const tx=fdim+4;
    txtClip(v.name,tx,top+1,edge-5,C.name);
    const dirX=tx+Math.min(v.name.length,Math.floor((edge-5-tx)/6))*6+2;
    if(v.dir && dirX<edge-4) dirGlyph(v.dir,dirX,top+1);
    // speed + course, no degree sign, clipped
    txtClip(v.sog.toFixed(1)+'kt '+v.cog,tx,top+12,edge,C.value);
    if(v.dest)txtClip(v.dest,tx,top+22,edge,C.dim);
  }
}

const ROSTER_W=40, DETAIL_W=W-ROSTER_W;
const C2={down:'#50c8ff',up:'#ff963c'};

function dirGlyph(dir,x,y){
  const col=dir==='D'?C2.down:dir==='U'?C2.up:C.dim;
  cx.fillStyle=col;
  if(dir==='D'){for(let r=0;r<4;r++)for(let c=r;c<4-r+1;c++)cx.fillRect(x+c,y+r,1,1);}
  else if(dir==='U'){for(let r=0;r<4;r++)for(let c=3-r;c<=r+1;c++)cx.fillRect(x+c,y+3-r,1,1);}
  else if(dir==='M'){for(let r=1;r<4;r++)for(let c=1;c<4;c++)cx.fillRect(x+c,y+r,1,1);}
  else cx.fillRect(x+2,y+2,1,1);
}
function centeredIn(s,zoneW,y,col){txt(s,Math.floor((zoneW-textWidth(s))/2),y,col);}

function drawRoster(roster){
  const rx=DETAIL_W;
  cx.fillStyle=C.dim;for(let y=0;y<H;y+=2)cx.fillRect(rx,y,1,1);
  if(!roster||!roster.length)return;
  const MAX_R=7, rowH=(H/MAX_R)|0;
  for(let i=0;i<roster.length&&i<MAX_R;i++){
    const y=i*rowH, x=rx+2;
    dirGlyph(roster[i].dir,x,y+((rowH-5)>>1));
    txtClip(roster[i].name,x+7,y+((rowH-7)>>1),W,C.value);
  }
}

let pairIndex=0, lastFrame=0;
function render(f){
  cx.clearRect(0,0,W,H);
  const now=Date.now();
  if(lastFrame && now-lastFrame>60000){centered('WAITING',H/2-8,C.accent);centered('for data',H/2+2,C.dim);cv.style.opacity=1;return;}
  const n=f.vessels.length;
  const rn=(f.roster||[]).length;
  if(f.closed && n===0 && rn===0){centered('SEAWAY',H/2-16,C.accent);centered('CLOSED',H/2-4,C.name);centered('reopens March',H/2+8,C.dim);cv.style.opacity=1;return;}

  // left: detail zone
  if(n===0){
    centeredIn('AMERICAN',DETAIL_W,H/2-12,C.accent);
    centeredIn('NARROWS',DETAIL_W,H/2-2,C.dim);
    centeredIn('clear',DETAIL_W,H/2+10,C.dim);
  }else if(n===1){band(f.vessels[0],0,H,true);}
  else{
    const a=pairIndex%n, b=(pairIndex+1)%n;
    band(f.vessels[a],0,H/2,false);
    cx.fillStyle=C.dim;for(let x=0;x<DETAIL_W;x+=2)cx.fillRect(x,H/2,1,1);
    band(f.vessels[b],H/2+1,H/2-1,false);
  }
  // right: roster
  drawRoster(f.roster);

  cv.style.opacity=(0.25+0.75*(f.bright/255)).toFixed(2);

  // Capture a screenshot when a NEW ship appears in the detail zone.
  maybeCapture(f);
}

// Track which detail ships we've already captured this visit, so we grab one
// PNG per ship rather than one per second.
let capturedNames = new Set();
function maybeCapture(f){
  const present = new Set((f.vessels||[]).map(v=>v.name));
  for(const v of (f.vessels||[])){
    if(!capturedNames.has(v.name)){
      capturedNames.add(v.name);
      captureCanvas(v.name);
    }
  }
  // Forget ships no longer present, so a return visit captures them again.
  capturedNames = new Set([...capturedNames].filter(n=>present.has(n)));
}

function captureCanvas(name){
  try{
    const dataUrl=cv.toDataURL('image/png');
    fetch('/capture',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({name:name,image:dataUrl})}).catch(()=>{});
  }catch(e){}
}

async function poll(){
  try{
    const r=await fetch('/latest');const f=await r.json();
    if(f.ts) lastFrame=Date.now();
    render(f);
    document.getElementById('meta').textContent=
      f.vessels.length+' in narrows  |  '+(f.roster||[]).length+' in seaway  |  '+
      'bright '+f.bright+'/255  |  '+(f.closed?'CLOSED':'in season');
  }catch(e){}
}
setInterval(poll,1000);
setInterval(()=>{pairIndex+=2;},6000);
poll();
</script></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass  # quiet

    def do_POST(self):
        if self.path == "/frame":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            try:
                frame = json.loads(body)
                with _lock:
                    global _latest
                    _latest = frame
                msg = f"frame: {len(frame.get('vessels', []))} vessel(s), " \
                      f"bright {frame.get('bright')}, " \
                      f"{'CLOSED' if frame.get('closed') else 'open'}"
                print(msg)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(str(e).encode())
        elif self.path == "/capture":
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            try:
                save_capture(json.loads(body))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            except Exception as e:
                print(f"[capture] failed: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/latest":
            with _lock:
                payload = json.dumps(_latest).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
        else:
            page = (PAGE
                    .replace("%SPRITES%", json.dumps(SPRITES_HEX))
                    .replace("%SPRITE_SIZE%", str(SPRITE_SIZE))
                    .replace("%FONT%", json.dumps(FONT)))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page.encode())


if __name__ == "__main__":
    print(f"Mock panel serving on http://localhost:{PORT}")
    print(f"  Loaded {len(SPRITES)} sprites at {SPRITE_SIZE}x{SPRITE_SIZE}")
    print(f"  Loaded {len(FONT)} font glyphs (5x7 bitmap)")
    print(f"  Point the service at it: ESP32_HOST=localhost:{PORT}")
    with socketserver.ThreadingTCPServer(("", PORT), Handler) as httpd:
        httpd.serve_forever()