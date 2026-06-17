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

PORT = 8000
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
    minis = {}
    for m in re.finditer(r"SPR_(\w+)\[\d+\]\s*PROGMEM\s*=\s*\{([^}]*)\}", text):
        key = m.group(1)
        vals = [int(x, 16) for x in re.findall(r"0x[0-9A-Fa-f]{4}", m.group(2))]
        if key.endswith("_MINI"):
            minis[key[:-5]] = vals          # strip suffix -> operator key
        else:
            sprites[key] = vals
    return sprites, size, minis


SPRITES, SPRITE_SIZE, MINIS = load_sprites()


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


def load_emmett_data(path="emmett_panel_data.json"):
    """Lakes map + icon sprites + placement for the Where's-Emmett frame.
    Optional: if the file is absent the panel simply never shows the frame."""
    try:
        return json.load(open(path))
    except (FileNotFoundError, ValueError):
        return None


EMMETT_DATA = load_emmett_data()


def rgb565_to_hex(c):
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return f"#{r*255//31:02x}{g*255//63:02x}{b*255//31:02x}"


SPRITES_HEX = {k: [rgb565_to_hex(c) for c in vals] for k, vals in SPRITES.items()}
MINIS_HEX = {k: [rgb565_to_hex(c) for c in vals] for k, vals in MINIS.items()}


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
const MINIS = %MINIS%;
const MINI_SIZE = 8;
const SPRITE_SIZE = %SPRITE_SIZE%;
const FONT = %FONT%;
const EMMETT = %EMMETT%;
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

// Baked 8x8 chip blit (1:1, no rescale). Falls back to downscaling the full
// sprite if a mini wasn't generated for this operator.
function spriteMini(key,ox,oy){
  const px=MINIS[key]||MINIS['UNKNOWN'];
  if(!px){spriteScaled(key,ox,oy,MINI_SIZE);return;}
  for(let y=0;y<MINI_SIZE;y++){
    for(let x=0;x<MINI_SIZE;x++){
      const c=px[y*MINI_SIZE+x];
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
  else if(dir==='M'){
    // small anchor: ring, stock crossbar, shank, curved flukes
    const px=[[2,0],[1,1],[2,1],[3,1],[2,2],[0,3],[2,3],[4,3],[1,4],[2,4],[3,4]];
    for(let i=0;i<px.length;i++) cx.fillRect(x+px[i][0],y+px[i][1],1,1);
  }
  else {cx.fillRect(x+1,y+2,3,1);}     // '?' / unknown: dim dash, not a moored block
}

// ---- BOARD mode: scrolling departure-board list ----------------------------
const ROW_H=10;                       // px per ship row
const BOARD_SCROLL_SPEED=8;           // px/sec
const BOARD_SCROLL_PAUSE=2000;        // ms top/bottom hold

// How long one full board cycle takes (ms): if the list fits, a fixed dwell;
// if it overflows, long enough to scroll down and back with pauses.
function boardCycleMs(n){
  const totalH=n*ROW_H;
  if(totalH<=H) return 8000;          // fits: fixed dwell
  const travel=totalH-H;
  const scrollMs=travel/BOARD_SCROLL_SPEED*1000;
  return BOARD_SCROLL_PAUSE+scrollMs+BOARD_SCROLL_PAUSE+scrollMs;
}

function drawBoard(ships, animT){
  const n=ships.length;
  if(n===0){ centered('NO SHIPS',H/2-8,C.accent); centered('in window',H/2+2,C.dim); return; }
  let scroll=0;
  const totalH=n*ROW_H;
  if(totalH>H){
    const SPEED=BOARD_SCROLL_SPEED;
    const PAUSE=BOARD_SCROLL_PAUSE;
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
  // funnel chip (8px, baked mini), code, dir, name, age right-aligned
  spriteMini(s.op,0,y+1);
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

// ---- EMMETT mode: "Where's Emmett" Great Lakes tracker ---------------------
// Ported from the Python reference renderer. Draws green land + lakes, the
// home icons (cherries/mai tai) always, one port landmark icon when it's the
// AIS destination or the nearest port in range, 1px port dots, Emmett's live
// position dot, and a detail box (lake / nav / SOG / distance to Traverse City).
const E_MAP_OX=0, E_MAP_OY=7;
const E_COL={
  base:'#688e32', alt:'#60842e', dark:'#3c5c22',
  water:'#4a8ec8', coast:'#2c583c',
  dot:'#ff3c28', dothl:'#ffc878',
  boxFill:'#0a0e0c', boxEdge:'#96af8c', halo:'#46553c',
  portMark:'#ffeb78',
  tHead:'#6ec878', tMain:'#d2dccd', tDim:'#96aa96', tStale:'#c8965a'
};
let _eWaterSet=null, _eLakeOf=null;
function eWaterSet(){
  if(!_eWaterSet){
    _eWaterSet=new Set(); for(const p of EMMETT.water) _eWaterSet.add(p[0]+','+p[1]);
    _eLakeOf=EMMETT.lake_of;
  }
  return _eWaterSet;
}
function eLL2XY(lon,lat){
  const p=EMMETT.proj, W=EMMETT.w, Hh=EMMETT.h;
  const fx=(lon-p.lon_min)/(p.lon_max-p.lon_min);
  const fy=(p.lat_max-lat)/(p.lat_max-p.lat_min);
  return [Math.round(fx*(W-1)), Math.round(fy*(Hh-1))];
}
function ePut(x,y,c){ if(x>=0&&x<W&&y>=0&&y<H){ cx.fillStyle=c; cx.fillRect(x,y,1,1);} }

// Compact 3x5 font, scoped to the Emmett frame so we can pack more info than the
// panel's 5x7 font allows. Each glyph is 5 rows of a 3-bit mask (bit0=left).
const E_FONT={
 'A':[2,5,7,5,5],'B':[6,5,6,5,6],'C':[3,4,4,4,3],'D':[6,5,5,5,6],'E':[7,4,6,4,7],
 'F':[7,4,6,4,4],'G':[3,4,5,5,3],'H':[5,5,7,5,5],'I':[7,2,2,2,7],'J':[1,1,1,5,2],
 'K':[5,6,4,6,5],'L':[4,4,4,4,7],'M':[5,7,7,5,5],'N':[5,7,7,7,5],'O':[2,5,5,5,2],
 'P':[6,5,6,4,4],'Q':[2,5,5,3,1],'R':[6,5,6,6,5],'S':[3,4,2,1,6],'T':[7,2,2,2,2],
 'U':[5,5,5,5,3],'V':[5,5,5,2,2],'W':[5,5,7,7,5],'X':[5,5,2,5,5],'Y':[5,5,2,2,2],
 'Z':[7,1,2,4,7],
 '0':[2,5,5,5,2],'1':[2,6,2,2,7],'2':[6,1,2,4,7],'3':[6,1,2,1,6],'4':[5,5,7,1,1],
 '5':[7,4,6,1,6],'6':[3,4,6,5,2],'7':[7,1,2,2,2],'8':[2,5,2,5,2],'9':[2,5,3,1,6],
 '.':[0,0,0,0,2],'-':[0,0,7,0,0],':':[0,2,0,2,0],' ':[0,0,0,0,0],'/':[1,1,2,4,4],
 '?':[6,1,2,0,2]
};
const E_GW=3, E_GADV=4, E_GH=5;
function eGlyph(ch,x,y,col){
  const g=E_FONT[ch]||E_FONT['?'];
  cx.fillStyle=col;
  for(let r=0;r<E_GH;r++){ const bits=g[r];
    for(let c=0;c<E_GW;c++){ if(bits&(1<<(E_GW-1-c))) cx.fillRect(x+c,y+r,1,1); } }
}
function eTxt(s,x,y,col,maxX){
  let gx=x; s=String(s).toUpperCase();
  for(const ch of s){ if(maxX&&gx+E_GW>maxX) break; eGlyph(ch,gx,y,col); gx+=E_GADV; }
  return gx;
}
function eTxtW(s){ return String(s).length*E_GADV-1; }

function eHaversineMi(la1,lo1,la2,lo2){
  const R=3958.7613, rad=Math.PI/180;
  const dp=(la2-la1)*rad, dl=(lo2-lo1)*rad;
  const a=Math.sin(dp/2)**2+Math.cos(la1*rad)*Math.cos(la2*rad)*Math.sin(dl/2)**2;
  return 2*R*Math.asin(Math.min(1,Math.sqrt(a)));
}
function eLakeName(lon,lat){
  eWaterSet();
  const [wx,wy]=eLL2XY(lon,lat);
  for(let r=0;r<=4;r++){
    let best=null,bd=1e9;
    for(let dy=-r;dy<=r;dy++)for(let dx=-r;dx<=r;dx++){
      if(Math.max(Math.abs(dx),Math.abs(dy))!==r) continue;
      const nm=_eLakeOf[(wx+dx)+','+(wy+dy)];
      if(nm&&nm!=='?'){ const d=dx*dx+dy*dy; if(d<bd){bd=d;best=nm;} }
    }
    if(best) return best;
  }
  return '?';
}
function eNavLabel(nav,sog){
  const NAV={0:'UNDERWAY',1:'ANCHORED',2:'NOT UNDER CMD',3:'RESTRICTED',
    4:'DRAFT LIMITED',5:'MOORED',6:'AGROUND',7:'FISHING',8:'SAILING',15:'UNDEFINED'};
  const moving=(sog!=null&&sog>0.5);
  let label=(nav!=null)?NAV[nav]:null;
  if(moving&&(label==null||label==='MOORED'||label==='ANCHORED'||label==='UNDEFINED')) return 'UNDERWAY';
  if(!moving&&(label==null||label==='UNDERWAY'||label==='UNDEFINED')) return 'STOPPED';
  return label||(moving?'UNDERWAY':'STOPPED');
}
function eActivePort(e){
  // destination match first
  if(e.dest){
    const up=e.dest.toUpperCase();
    for(const k in EMMETT.ports){
      for(const kw of EMMETT.ports[k].kw){ if(up.indexOf(kw)>=0) return k; }
    }
  }
  // nearest within 60 mi
  let best=null,bd=1e9;
  for(const k in EMMETT.ports){
    const ll=EMMETT.ports[k].ll;
    const d=eHaversineMi(e.lat,e.lon,ll[0],ll[1]);
    if(d<bd){bd=d;best=k;}
  }
  return bd<=60?best:null;
}
function eDrawIcon(key,ax,ay){
  const off=EMMETT.offsets[key]; if(!off) return;
  const [ox,oy]=off.off;
  const cells=EMMETT.icons[key];
  // halo: exterior 8-neighbours of the silhouette
  const occ=new Set(); for(const c of cells) occ.add(c[0]+','+c[1]);
  const halo=new Set();
  for(const c of cells){
    for(let hx=-1;hx<=1;hx++)for(let hy=-1;hy<=1;hy++){
      const k=(c[0]+hx)+','+(c[1]+hy);
      if(!occ.has(k)) halo.add(k);
    }
  }
  for(const k of halo){ const [dx,dy]=k.split(',').map(Number);
    ePut(E_MAP_OX+ax+ox+dx, E_MAP_OY+ay+oy+dy, E_COL.halo); }
  for(const c of cells){ ePut(E_MAP_OX+ax+ox+c[0], E_MAP_OY+ay+oy+c[1], c[2]); }
}
function drawEmmett(e){
  if(!EMMETT||!e) return;
  eWaterSet();
  const W2=EMMETT.w, H2=EMMETT.h, p=EMMETT.proj;
  // green dithered field (stable pattern)
  for(let y=0;y<H;y++)for(let x=0;x<W;x++){
    // cheap deterministic dither matching the python seed feel
    const r=((x*73856093)^(y*19349663))>>>0;
    if((r%83)===0) ePut(x,y,E_COL.dark);
    else if(((x+y)&1)) ePut(x,y,E_COL.alt);
    else ePut(x,y,E_COL.base);
  }
  // water + coast
  for(const pt of EMMETT.water) ePut(E_MAP_OX+pt[0], E_MAP_OY+pt[1], E_COL.water);
  for(const pt of EMMETT.water){
    const x=pt[0],y=pt[1];
    const nb=[[1,0],[-1,0],[0,1],[0,-1]];
    for(const d of nb){ if(!_eWaterSet.has((x+d[0])+','+(y+d[1]))){ ePut(E_MAP_OX+x,E_MAP_OY+y,E_COL.coast); break; } }
  }
  // home icons always + one active port icon
  const show=new Set(EMMETT.home_icons);
  const port=eActivePort(e); if(port) show.add(port);
  for(const key of show){
    const off=EMMETT.offsets[key]; if(!off) continue;
    const [ax,ay]=eLL2XY(off.ll[0],off.ll[1]); eDrawIcon(key,ax,ay);
  }
  // port dots (1px)
  for(const key in EMMETT.offsets){
    if(EMMETT.home_icons.indexOf(key)>=0) continue;
    const off=EMMETT.offsets[key]; const [ax,ay]=eLL2XY(off.ll[0],off.ll[1]);
    ePut(E_MAP_OX+ax,E_MAP_OY+ay,E_COL.portMark);
  }
  // Emmett position dot (if in window)
  const inWin=(e.lon>=p.lon_min&&e.lon<=p.lon_max&&e.lat>=p.lat_min&&e.lat<=p.lat_max);
  if(inWin){
    const [dx,dy]=eLL2XY(e.lon,e.lat);
    for(const a of [[1,0],[-1,0],[0,1],[0,-1]]) ePut(E_MAP_OX+dx+a[0],E_MAP_OY+dy+a[1],E_COL.dothl);
    ePut(E_MAP_OX+dx,E_MAP_OY+dy,E_COL.dot);
  }
  // title chip (3x5 font: EMMETT = 6*4-1 = 23px)
  eBox(0,0,29,7,E_COL.boxFill,'#b4c8aa'); eTxt('EMMETT',3,1,'#dce6d2');
  // detail box: compact, back to the mock proportions. Left edge clears Huron,
  // bottom clears Erie/Ontario. 3x5 font fits ~14 chars per line here.
  const BX=62,BY=0,BW=W-BX,BH=29;
  eBox(BX,BY,BW,BH,E_COL.boxFill,E_COL.boxEdge);
  const tx=BX+3, maxX=W-2;
  const lake=eLakeName(e.lon,e.lat);
  const lakeDisp=(lake!=='?')?lake:'AT SEA';
  const nav=eNavLabel(e.navstat,e.sog);
  const sogDisp=(e.sog!=null)?(e.sog.toFixed(1)+' KN'):'-- KN';
  const dist=eHaversineMi(e.lat,e.lon,EMMETT.home.lat,EMMETT.home.lon);
  const distDisp='TVC '+Math.round(dist)+'MI';
  eTxt(lakeDisp,tx,BY+3,E_COL.tHead,maxX);
  eTxt(nav,tx,BY+9,E_COL.tMain,maxX);
  eTxt(sogDisp,tx,BY+15,E_COL.tDim,maxX);
  eTxt(distDisp,tx,BY+21,E_COL.tMain,maxX);
}
function eBox(x,y,w,h,fill,edge){
  for(let yy=y;yy<y+h;yy++)for(let xx=x;xx<x+w;xx++){
    const isEdge=(xx===x||xx===x+w-1||yy===y||yy===y+h-1);
    cx.fillStyle=isEdge?edge:fill; cx.fillRect(xx,yy,1,1);
  }
}


// BOARD shown long enough to complete a full scroll, then DETAIL cycles through
// the NAMED ships (ghosts stay on the board but get no solo card), then back.
const DETAIL_MS=5000;
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
  const n=ships.length;
  // Only named ships get detail cards; ghosts (MMSI-only) stay on the board.
  const named=ships.filter(s=>s.name && s.name.indexOf('MMSI ')!==0);
  const boardMs=boardCycleMs(n);
  // Emmett frame joins the rotation only when the service sends a fresh fix.
  const EMMETT_MS=6000;
  const hasEmmett=!!(EMMETT && f.emmett);
  const emmettMs=hasEmmett?EMMETT_MS:0;
  const cycle=boardMs + named.length*DETAIL_MS + emmettMs;
  const p=animT%cycle;
  if(p < boardMs && (n>0 || !hasEmmett)){
    drawBoard(ships,animT);
  }else if(p < boardMs + named.length*DETAIL_MS){
    if(named.length===0){ drawBoard(ships,animT); }
    else{
      const idx=Math.floor((p-boardMs)/DETAIL_MS)%named.length;
      drawDetail(named[idx],animT);
    }
  }else if(hasEmmett){
    drawEmmett(f.emmett);
  }else{
    drawBoard(ships,animT);
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
function frame(t){ animT=t; render(latest,animT); if(CAPTURE) maybeCapture(latest); requestAnimationFrame(frame); }

// First-appearance screenshot capture. OFF by default; set CAPTURE=true to
// re-enable (saves a PNG to captures/ the first time each vessel appears).
const CAPTURE=false;
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
                        .replace("%MINIS%", json.dumps(MINIS_HEX))
                        .replace("%SPRITE_SIZE%", str(SPRITE_SIZE))
                        .replace("%FONT%", json.dumps(FONT))
                        .replace("%EMMETT%", json.dumps(EMMETT_DATA)))
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