#!/usr/bin/env python3
"""
autoplace.py  (BUILD-TIME ONLY -- needs emmett_decorations + lakes_mask)

Resolves each icon's placement: a single-pixel port dot sits on the real
shoreline location, and the themed icon is offset into the nearest clean GREEN
area (no water, on-panel, with a 1px margin so it doesn't hug the coast), biased
toward a preferred direction. Writes icon_offsets.json, which the runtime panel
reads. Re-run only if you change the projection, the icon set, or an anchor.

    python3 autoplace.py
"""
import json, math
import emmett_decorations as dec
from emmett_geo import mask

PANEL_W, PANEL_H = 128, 64
MAP_OX, MAP_OY = 0, 7
m = mask(); W, H = m["w"], m["h"]; water = m["_water_set"]; p = m["proj"]

def ll2xy(lon, lat):
    fx = (lon-p["lon_min"])/(p["lon_max"]-p["lon_min"])
    fy = (p["lat_max"]-lat)/(p["lat_max"]-p["lat_min"])
    return int(round(fx*(W-1))), int(round(fy*(H-1)))

# anchor + preferred push direction (mask px) + optional lat-lock
MARKERS = {
 'duluth':         {'ll':(-92.10,46.78), 'bias':( 1, 1)},
 'marquette_dock': {'ll':(-87.40,46.54), 'bias':( 0,-1)},
 'soo':            {'ll':(-84.35,46.50), 'bias':( 1,-1)},
 'detroit':        {'ll':(-83.05,42.33), 'bias':(-1, 1)},
 'cleveland':      {'ll':(-81.69,41.50), 'bias':( 0, 1)},
 'rochester':      {'ll':(-77.62,43.21), 'bias':( 1, 1)},
 'cherry':         {'ll':(-85.62,44.76), 'bias':( 1, 0)},
 'shovel':         {'ll':(-87.95,43.04), 'bias':(-1, 0), 'lock_y':True},
 'maitai':         {'ll':(-75.93,44.33), 'bias':( 1, 1)},
}

def icon_clean_at(key, ax, ay, ox, oy):
    for (dx,dy,col) in dec.ICONS[key]:
        mx,my = ax+ox+dx, ay+oy+dy
        px,py = MAP_OX+mx, MAP_OY+my
        if not (0<=px<PANEL_W and 0<=py<PANEL_H): return False
        if (mx,my) in water: return False
    return True

def margin_clear(key, ax, ay, ox, oy):
    for (dx,dy,col) in dec.ICONS[key]:
        for mx in range(ax+ox+dx-1, ax+ox+dx+2):
            for my in range(ay+oy+dy-1, ay+oy+dy+2):
                px,py = MAP_OX+mx, MAP_OY+my
                if not (0<=px<PANEL_W and 0<=py<PANEL_H): return False
                if (mx,my) in water: return False
    return True

def place(key, info, max_r=16):
    ax,ay = ll2xy(*info['ll'])
    bx,by = info['bias']; blen = math.hypot(bx,by) or 1
    bxn,byn = bx/blen, by/blen
    best=None; bestscore=1e9
    for r in range(4, max_r+1):
        for oy in range(-r, r+1):
            if info.get('lock_y') and oy != 0: continue
            for ox in range(-r, r+1):
                if max(abs(ox),abs(oy)) != r: continue
                if not icon_clean_at(key, ax, ay, ox, oy): continue
                if not margin_clear(key, ax, ay, ox, oy): continue
                dist = math.hypot(ox,oy)
                align = (ox*bxn + oy*byn)/(math.hypot(ox,oy) or 1)
                score = abs(dist - 7) - 2.5*align
                if score < bestscore: bestscore=score; best=(ox,oy)
        if best is not None: break
    if best is None:   # fallback: drop the margin requirement
        for r in range(4, max_r+1):
            for oy in range(-r, r+1):
                if info.get('lock_y') and oy != 0: continue
                for ox in range(-r, r+1):
                    if max(abs(ox),abs(oy))!=r: continue
                    if icon_clean_at(key,ax,ay,ox,oy): return (ox,oy),(ax,ay)
    return best, (ax,ay)

if __name__ == "__main__":
    offsets={}
    for key,info in MARKERS.items():
        best,(ax,ay) = place(key,info)
        offsets[key]={'off':list(best) if best else [0,-7], 'll':list(info['ll'])}
        print(f"{key:16s} dot=({ax:2d},{ay:2d}) off={best}")
    json.dump(offsets, open('icon_offsets.json','w'), indent=2)
    print('wrote icon_offsets.json')
