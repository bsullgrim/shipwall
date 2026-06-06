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

import http.server
import json
import re
import socketserver
import threading

PORT = 8080
_latest = {"ts": 0, "bright": 128, "closed": False, "vessels": []}
_lock = threading.Lock()


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
function textWidth(s){ return s.length*GLYPH_ADV-1; }
function centered(s,y,col){ txt(s,Math.floor((W-textWidth(s))/2),y,col); }

function sprite(key,ox,oy){
  const px=SPRITES[key]||SPRITES['UNKNOWN'];if(!px)return;
  for(let y=0;y<SPRITE_SIZE;y++)for(let x=0;x<SPRITE_SIZE;x++){
    const c=px[y*SPRITE_SIZE+x];if(c&&c!=='#000000'){cx.fillStyle=c;cx.fillRect(ox+x,oy+y,1,1);}}
}

function band(v,top,bandH,big){
  const sprX=1, sprY=top+((bandH-SPRITE_SIZE)>>1);   // funnel on the LEFT edge
  const tx=SPRITE_SIZE+3;                            // text starts after sprite
  txt(v.name,tx,top+1,C.name);
  if(big){
    txt(v.type+' '+v.sog.toFixed(1)+'kt',tx,top+15,C.value);
    txt('CRS '+v.cog+'\\u00b0',tx,top+27,C.value);
    if(v.drft!=null)txt('DRAFT '+v.drft.toFixed(1)+'m',tx,top+39,C.value);
    if(v.dest)txt('>'+v.dest,tx,top+51,C.dim);
  }else{
    txt(v.type+' '+v.sog.toFixed(1)+'kt',tx,top+10,C.value);
    let s='CRS '+v.cog+'\\u00b0';if(v.drft!=null)s+=' '+v.drft.toFixed(1)+'m';
    txt(s,tx,top+20,C.value);
  }
  sprite(v.op,sprX,sprY);
}

let pairIndex=0, lastFrame=0;
function render(f){
  cx.clearRect(0,0,W,H);
  const now=Date.now();
  if(lastFrame && now-lastFrame>60000){centered('WAITING',H/2-8,C.accent);centered('for data',H/2+2,C.dim);return;}
  const n=f.vessels.length;
  if(f.closed && n===0){centered('SEAWAY',H/2-16,C.accent);centered('CLOSED',H/2-4,C.name);centered('reopens March',H/2+8,C.dim);return;}
  if(n===0){centered('ST LAWRENCE',H/2-8,C.accent);centered('no vessels',H/2+2,C.dim);return;}
  if(n===1){band(f.vessels[0],0,H,true);}
  else{
    const a=pairIndex%n, b=(pairIndex+1)%n;
    band(f.vessels[a],0,H/2,false);
    cx.fillStyle=C.dim;for(let x=0;x<W;x+=2)cx.fillRect(x,H/2,1,1);
    band(f.vessels[b],H/2+1,H/2-1,false);
  }
  // emulate the panel brightness via canvas opacity overlay
  cv.style.opacity=(0.25+0.75*(f.bright/255)).toFixed(2);
}

async function poll(){
  try{
    const r=await fetch('/latest');const f=await r.json();
    if(f.ts) lastFrame=Date.now();
    render(f);
    document.getElementById('meta').textContent=
      f.vessels.length+' vessel(s)  |  brightness '+f.bright+'/255  |  '+
      (f.closed?'SEAWAY CLOSED':'in season');
  }catch(e){}
}
setInterval(poll,1000);
setInterval(()=>{pairIndex+=2;},6000);  // cycle pairs like the firmware
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
