#!/usr/bin/env python3
"""Generate 32x32 LED-matrix sprites of Great Lakes / Seaway funnel markings.
Blacks are lifted to a visible dark grey so funnel shapes read on LED panels."""
import math, os, zipfile
from PIL import Image, ImageDraw

S = 32
OUT = "/home/claude/out"
os.makedirs(OUT, exist_ok=True)

# ---------------- palette (saturated for LED) ----------------
BLACK  = (45, 45, 45)     # lifted black
INK    = (40, 40, 40)     # "black" linework/text (also lifted)
WHITE  = (255, 255, 255)
RED    = (240, 45, 30)
RED2   = (228, 40, 32)
ORANGE = (250, 95, 25)
GOLD   = (250, 170, 25)
YELLOW = (255, 210, 0)
BUFF   = (232, 208, 145)
CREAM  = (246, 238, 210)
GREEN  = (40, 165, 60)
BLUE   = (35, 95, 215)
ROYAL  = (40, 80, 200)
NAVY   = (28, 48, 135)
TAN    = (235, 200, 150)

# ---------------- tiny variable-width 3x5 font ----------------
F = {
 'A':["###","#.#","###","#.#","#.#"],
 'B':["##.","#.#","##.","#.#","##."],
 'C':["###","#..","#..","#..","###"],
 'D':["##.","#.#","#.#","#.#","##."],
 'E':["###","#..","##.","#..","###"],
 'F':["###","#..","##.","#..","#.."],
 'G':["###","#..","#.#","#.#","###"],
 'H':["#.#","#.#","###","#.#","#.#"],
 'I':["#","#","#","#","#"],
 'K':["#.#","##.","#..","##.","#.#"],
 'L':["#..","#..","#..","#..","###"],
 'M':["#...#","##.##","#.#.#","#...#","#...#"],
 'N':["#..#","##.#","#.##","#..#","#..#"],
 'O':["###","#.#","#.#","#.#","###"],
 'P':["###","#.#","###","#..","#.."],
 'R':["##.","#.#","##.","#.#","#.#"],
 'S':["###","#..","###","..#","###"],
 'T':["###",".#.",".#.",".#.",".#."],
 'U':["#.#","#.#","#.#","#.#","###"],
 'V':["#.#","#.#","#.#","#.#",".#."],
 'W':["#...#","#...#","#.#.#","##.##","#...#"],
 'Z':["###","..#",".#.","#..","###"],
 '3':["###","..#",".##","..#","###"],
}

def px(im, x, y, c):
    x, y = int(x), int(y)
    if 0 <= x < S and 0 <= y < S:
        im.putpixel((x, y), c)

def rect(im, x0, y0, x1, y1, c):
    for y in range(int(y0), int(y1)+1):
        for x in range(int(x0), int(x1)+1):
            px(im, x, y, c)

def hband(im, y0, y1, c): rect(im, 0, y0, 31, y1, c)

def disc(im, cx, cy, r, c):
    for y in range(S):
        for x in range(S):
            if math.hypot(x-cx, y-cy) <= r + 0.25:
                px(im, x, y, c)

def ring(im, cx, cy, r, c, th=1.0):
    for y in range(S):
        for x in range(S):
            if abs(math.hypot(x-cx, y-cy) - r) <= th*0.5 + 0.1:
                px(im, x, y, c)

def arc(im, cx, cy, r, a0, a1, c, th=1.0):
    for y in range(S):
        for x in range(S):
            d = math.hypot(x-cx, y-cy)
            if abs(d - r) > th*0.5 + 0.1:
                continue
            a = math.degrees(math.atan2(cy-y, x-cx)) % 360
            if a0 <= a <= a1:
                px(im, x, y, c)

def line(im, x0, y0, x1, y1, c, w=1):
    n = int(max(abs(x1-x0), abs(y1-y0)) * 2) + 1
    for i in range(n+1):
        t = i / n
        x = x0 + (x1-x0)*t
        y = y0 + (y1-y0)*t
        if w == 1:
            px(im, round(x), round(y), c)
        else:
            for dx in range(w):
                for dy in range(w):
                    px(im, round(x)+dx-(w//2), round(y)+dy-(w//2), c)

def bitmap(im, x0, y0, rows, cmap):
    for j, row in enumerate(rows):
        for i, ch in enumerate(row):
            if ch in cmap:
                px(im, x0+i, y0+j, cmap[ch])

def measure(s):
    return sum(len(F[ch][0]) for ch in s) + (len(s)-1)

def text(im, x, y, s, c):
    for ch in s:
        g = F[ch]
        w = len(g[0])
        for j, row in enumerate(g):
            for i, p in enumerate(row):
                if p == '#':
                    px(im, x+i, y+j, c)
        x += w + 1
    return x

def blank(c):
    return Image.new("RGB", (S, S), c)

# ================= company drawers =================

def biglift():
    return blank(ROYAL)

def granath():
    im = blank(YELLOW)
    G = ["..######...",
         ".########..",
         "##......##.",
         "##.........",
         "##.........",
         "##.........",
         "##.........",
         "##...######",
         "##...######",
         "##......##.",
         "##......##.",
         ".########..",
         "..######..."]
    bitmap(im, 10, 9, G, {'#': BLUE})
    return im

def groupocean():
    im = blank(WHITE)
    hband(im, 0, 6, BLACK)
    x = (32 - measure("OCEAN")) // 2
    text(im, x, 15, "OCEAN", BLUE)
    return im

def fednav():
    im = blank(BLACK)
    hband(im, 8, 23, WHITE)
    # red ring with a gap at upper-left for the maple-leaf arrow
    for y in range(S):
        for x in range(S):
            d = math.hypot(x-16, y-16)
            if 6.1 <= d <= 7.6:
                a = math.degrees(math.atan2(16-y, x-16)) % 360
                if not (98 <= a <= 152):
                    px(im, x, y, RED)
    # maple leaf arrow blob pointing into the gap
    leaf = [(9,8),(10,8),(8,9),(9,9),(10,9),(11,9),(9,10),(10,10),(11,10),
            (10,11),(11,11),(12,11),(11,12),(12,12)]
    for (x, y) in leaf:
        px(im, x, y, RED)
    # bold F
    rect(im, 14, 12, 15, 21, RED)
    rect(im, 14, 12, 19, 13, RED)
    rect(im, 14, 16, 18, 17, RED)
    return im

def cliffs():
    im = blank(BLACK)
    rect(im, 9, 0, 13, 31, WHITE)          # vertical stripe
    hband(im, 10, 21, WHITE)               # band
    # diamond logo: gold top / navy bottom
    for (x, y) in [(4,13),(5,13),(3,14),(4,14),(5,14),(6,14)]:
        px(im, x, y, GOLD)
    for (x, y) in [(3,15),(4,15),(5,15),(6,15),(4,16),(5,16)]:
        px(im, x, y, NAVY)
    text(im, 8, 13, "CLIFFS", NAVY)
    return im

def calfornav():
    im = blank(CREAM)
    def tri(ax, ay, by, sl, c):
        for y in range(ay, by+1):
            hw = int((y-ay)*sl + 0.5)
            rect(im, ax-hw, y, ax+hw, y, c)
    tri(10, 7, 14, 0.62, GREEN)
    tri(22, 7, 14, 0.62, GREEN)
    tri(16, 4, 14, 0.55, GREEN)
    wav = [2, 1, 0, 0, 1, 2]
    for base in (18, 22, 26):
        for x in range(6, 26):
            yo = wav[(x-6) % 6]
            px(im, x, base+yo, BLUE)
            px(im, x, base+yo+1, BLUE)
    return im

def briese():
    im = blank(WHITE)
    bounds = {6:(20,22),7:(19,23),8:(18,23),9:(17,23),10:(15,22),11:(14,21),
              12:(13,21),13:(12,20),14:(11,20),15:(10,19),16:(9,19),17:(9,19),
              18:(8,20),19:(8,21),20:(9,22),21:(10,22),22:(12,21),23:(14,19)}
    # interior fill with diagonal stripes
    for y, (xl, xr) in bounds.items():
        for x in range(xl, xr+1):
            v = y - round((x-8) * 0.35)
            if v in (12, 13): c = INK
            elif v in (14, 15): c = RED
            elif v in (16, 17): c = BLUE
            else: c = WHITE
            px(im, x, y, c)
    # outline
    ys = sorted(bounds)
    for y in ys:
        xl, xr = bounds[y]
        px(im, xl, y, INK); px(im, xr, y, INK)
    for x in range(bounds[ys[0]][0], bounds[ys[0]][1]+1): px(im, x, ys[0], INK)
    for x in range(bounds[ys[-1]][0], bounds[ys[-1]][1]+1): px(im, x, ys[-1], INK)
    return im

def ccg():
    im = blank(WHITE)
    LEAF = [
    "........#........",
    ".......###.......",
    ".......###.......",
    "..#...#####...#..",
    "..##.#######.##..",
    "..#############..",
    "..#############..",
    ".###############.",
    "#################",
    ".###############.",
    "..#############..",
    "...###########...",
    ".....#######.....",
    "......#####......",
    ".......###.......",
    "........#........",
    "........#........",
    "........#........",
    ]
    bitmap(im, 8, 6, LEAF, {'#': RED})
    return im

def asc():
    im = blank(BLACK)
    hband(im, 9, 12, WHITE)
    hband(im, 13, 19, RED)
    hband(im, 20, 23, WHITE)
    return im

def vtb():
    im = blank(BLACK)
    rect(im, 0, 25, 31, 31, BUFF)
    disc(im, 16, 12, 9.5, WHITE)
    ring(im, 16, 12, 8.2, INK, 1.0)
    # compass star
    for y in range(3, 12):
        w = 1 if y < 8 else 3
        rect(im, 16-(w//2), y, 16+(w//2), y, INK)
    for y in range(12, 16):
        w = 3 if y < 14 else 1
        rect(im, 16-(w//2), y, 16+(w//2), y, INK)
    for x in range(8, 25):
        w = 3 if 13 <= x <= 19 else 1
        rect(im, x, 11-(w//2)+0, x, 11+(w//2)-0, INK) if False else None
    for x in range(8, 25):
        w = 3 if 13 <= x <= 19 else 1
        for dy in range(-(w//2), (w//2)+1):
            px(im, x, 11+dy, INK)
    for (dx, dy) in [(1,1),(-1,1),(1,-1),(-1,-1)]:
        for k in range(2, 6):
            px(im, 16+dx*k, 11+dy*k, INK)
    x = (32 - measure("VTB")) // 2
    text(im, x, 16, "VTB", RED)
    return im

def uscg():
    im = blank(GOLD)
    hband(im, 0, 5, BLACK)
    # anchor stubs behind the disc
    for (sx, sy, ex, ey) in [(21,12,25,8),(11,12,7,8),(21,24,25,28),(11,24,7,28)]:
        line(im, sx, sy, ex, ey, WHITE, 2)
    disc(im, 16, 18, 9, WHITE)
    ring(im, 16, 18, 8.6, BLUE, 1.0)
    ring(im, 16, 18, 5.2, BLUE, 1.0)
    # shield
    rect(im, 13, 15, 19, 16, BLUE)
    for y in range(17, 23):
        hw = max(0, 3 - max(0, y-19))
        rect(im, 16-hw, y, 16+hw, y, RED)
    for y in range(17, 21):
        px(im, 14, y, WHITE); px(im, 16, y, WHITE); px(im, 18, y, WHITE)
    return im

def wagenborg():
    im = blank(BLACK)
    hband(im, 9, 12, WHITE)
    hband(im, 16, 19, WHITE)
    return im

def tbmarine():
    im = blank(WHITE)
    ring(im, 15, 16, 8, NAVY, 1.0)
    for (sx, sy, ex, ey) in [(20,11,23,8),(10,11,7,8),(20,21,23,24),(10,21,7,24)]:
        line(im, sx, sy, ex, ey, NAVY, 1)
    # up arrow
    rect(im, 15, 6, 16, 15, NAVY)
    for (x, y) in [(15,3),(16,3),(14,4),(15,4),(16,4),(17,4),(13,5),(14,5),(15,5),(16,5),(17,5),(18,5)]:
        px(im, x, y, NAVY)
    # left arrow
    rect(im, 4, 16, 9, 17, NAVY)
    for (x, y) in [(2,16),(2,17),(3,15),(3,16),(3,17),(3,18)]:
        px(im, x, y, NAVY)
    # TB (scale 2)
    def big(ch, x0, y0):
        g = F[ch]
        for j, row in enumerate(g):
            for i, p in enumerate(row):
                if p == '#':
                    rect(im, x0+i*2, y0+j*2, x0+i*2+1, y0+j*2+1, NAVY)
    big('T', 9, 12)
    big('B', 17, 12)
    x = (32 - measure("MARINE")) // 2
    text(im, x, 25, "MARINE", NAVY)
    return im

def csl():
    im = blank(WHITE)
    rect(im, 0, 0, 31, 4, BLACK)
    rect(im, 25, 0, 31, 31, BLACK)
    for y in range(12, 32):
        xr = min(20, 17 + (y-12)//5)
        rect(im, 0, y, xr, y, RED2)
    return im

def spliethoff():
    im = blank(ORANGE)
    rect(im, 8, 11, 23, 20, WHITE)
    for y in range(12, 20):
        t = abs(y - 15.5)
        ext = max(0, round((3.5 - t) * 1.15) + 1)
        if ext > 0:
            rect(im, 9, y, 8+ext, y, RED)
            rect(im, 23-ext, y, 22, y, BLUE)
    text(im, 14, 13, "S", INK)
    return im

def g3():
    im = blank(BLACK)
    for r in (21, 17, 13):
        arc(im, 28, 16, r, 160, 200, WHITE, 1.8)
    G = [".####.","#....#","#.....","#.....","#.....","#..###","#....#","#....#","#....#",".####."]
    bitmap(im, 17, 11, G, {'#': WHITE})
    T3 = ["####.","....#","....#",".###.","....#","....#","....#","####."]
    bitmap(im, 24, 12, T3, {'#': WHITE})
    return im

def andrie():
    im = blank((35, 95, 205))
    hband(im, 11, 20, WHITE)
    return im

def carisbrooke():
    im = blank(BUFF)
    hband(im, 9, 23, NAVY)
    C6 = [".#####","######","##....","##....","##....","##....","##....","######",".#####"]
    S6 = [".#####","######","##....","#####.",".#####","....##","....##","######","#####."]
    bitmap(im, 9, 12, C6, {'#': BUFF})
    bitmap(im, 17, 12, S6, {'#': BUFF})
    return im

def polsteam():
    im = blank(BLUE)
    hband(im, 13, 13, WHITE)
    hband(im, 14, 19, RED)
    hband(im, 20, 20, WHITE)
    # shield: white outline, red field
    shield = {}
    for y in range(5, 26):
        if y <= 20: shield[y] = (11, 21)
        else:
            k = y - 20
            shield[y] = (11+k, 21-k)
    for y, (xl, xr) in shield.items():
        rect(im, xl, y, xr, y, WHITE)
    for y, (xl, xr) in shield.items():
        if y >= 6 and y <= 24:
            nxl, nxr = xl+1, xr-1
            if nxl <= nxr:
                rect(im, nxl, y, nxr, y, RED)
    # trident above
    for (x, ys, ye) in [(13,2,4),(16,1,4),(19,2,4)]:
        for y in range(ys, ye+1): px(im, x, y, WHITE)
    rect(im, 13, 5, 19, 5, WHITE)
    # stacked P Z M
    text(im, 13, 7, "P", WHITE)
    text(im, 17, 11, "Z", WHITE)
    px(im, 18, 9, WHITE)  # dot of Ż
    text(im, 13, 17, "M", WHITE)
    return im

def coastal():
    im = blank((30, 75, 195))
    for (bx, ty) in [(5, 6), (12, 9), (19, 12)]:
        for y in range(ty, 22):
            x0 = bx + int((y-ty) * 0.35)
            rect(im, x0, y, x0+3, y, RED)
    rect(im, 5, 24, 27, 24, RED)
    return im

def mckeil():
    im = blank(BLACK)
    hband(im, 7, 25, WHITE)
    # slanted marks: one green, two blue
    for (topx, c) in [(13, GREEN), (18, BLUE), (23, BLUE)]:
        for y in range(9, 15):
            x = topx - round((y-9) * 0.8)
            rect(im, x, y, x+2, y, c)
    x = (32 - measure("MCKEIL")) // 2
    text(im, x, 17, "MCKEIL", INK)
    return im

def nmbulgare():
    im = blank(YELLOW)
    hband(im, 9, 21, RED2)
    rect(im, 10, 10, 21, 21, WHITE)
    ring(im, 15.5, 15.5, 4.3, BLUE, 1.0)
    for (x, y) in [(14,13),(15,13),(15,14),(16,14),(14,15),(15,15),(16,16),(14,17),(15,17)]:
        px(im, x, y, BLUE)
    rect(im, 13, 19, 18, 19, BLUE)
    return im

def neas():
    im = blank((225, 35, 28))
    rect(im, 5, 8, 25, 25, WHITE)
    rect(im, 6, 9, 24, 9, RED)
    # syllabics suggestion
    for (x, y) in [(8,11),(9,12),(11,11),(12,11),(14,12),(15,11),(17,11),(18,12)]:
        px(im, x, y, RED)
    # blue globe oval
    for y in range(12, 18):
        t = abs(y - 14.5)
        hw = round(math.sqrt(max(0, 1-(t/2.8)**2)) * 5)
        rect(im, 13-hw, y, 13+hw, y, (45, 110, 230))
    for (x, y) in [(10,16),(11,15),(12,15),(13,14),(14,14),(15,13)]:
        px(im, x, y, WHITE)
    text(im, 7, 18, "NEAS", RED)
    # anchor swoosh
    for x in range(9, 22):
        y = 24 if 10 <= x <= 20 else 23
        px(im, x, y, RED)
    # corner tab
    rect(im, 22, 10, 23, 13, RED)
    rect(im, 22, 14, 23, 19, INK)
    return im

def usespa():
    im = blank(WHITE)
    B = (35, 120, 215); Gn = (70, 170, 40)
    ring(im, 16, 16, 12.3, B, 2.0)
    disc(im, 16, 11, 4.3, B)
    px(im, 15, 9, WHITE); px(im, 16, 9, WHITE)
    for x in range(10, 23):
        yy = 13 if (x // 2) % 2 == 0 else 14
        px(im, x, yy, Gn)
        px(im, x, 14, Gn)
    # leaves
    left = {17:(13,14),18:(11,15),19:(10,15),20:(10,14),21:(11,13)}
    for y, (xl, xr) in left.items():
        rect(im, xl, y, xr, y, Gn)
        rect(im, 31-xr, y, 31-xl, y, Gn)
    rect(im, 15, 15, 16, 17, Gn)
    return im

def lowerlakes():
    im = blank(WHITE)
    hband(im, 0, 4, BLACK)
    rect(im, 0, 25, 31, 26, ORANGE)
    ring(im, 16, 15, 6, INK, 1.4)
    for k in range(8):
        a = math.radians(k * 45)
        sx, sy = 16 + 6*math.cos(a), 15 - 6*math.sin(a)
        ex, ey = 16 + 9.5*math.cos(a), 15 - 9.5*math.sin(a)
        line(im, sx, sy, ex, ey, INK, 1)
        px(im, round(16 + 9.5*math.cos(a)), round(15 - 9.5*math.sin(a)), INK)
    for k in range(8):
        a = math.radians(k * 45 + 22.5)
        px(im, round(16 + 6*math.cos(a)), round(15 - 6*math.sin(a)), RED)
    # head: hair, face, feathers
    for (x, y) in [(13,12),(13,13),(13,14),(13,15),(14,11),(15,11)]:
        px(im, x, y, INK)
    for (x, y) in [(14,12),(15,12),(14,13),(15,13),(14,14),(15,14),(14,15),(15,15),(14,16),(15,16)]:
        px(im, x, y, TAN)
    for (x, y) in [(16,12),(17,12),(16,13),(17,13),(18,13),(16,14),(17,14),(18,15),(17,15),(16,16)]:
        px(im, x, y, RED)
    return im

def algoma():
    im = blank(BLACK)
    hband(im, 5, 10, RED2)
    hband(im, 11, 19, WHITE)
    hband(im, 20, 25, RED2)
    ring(im, 16, 15, 9.4, INK, 1.2)
    for y in range(S):
        for x in range(S):
            d = math.hypot(x-16, y-15)
            if 5.2 < d <= 8.8:
                px(im, x, y, RED2)
    disc(im, 16, 15, 5.2, WHITE)
    for k in range(12):
        a = math.radians(k * 30 + 15)
        px(im, round(16 + 7.1*math.cos(a)), round(15 - 7.1*math.sin(a)), WHITE)
    BEAR = ["...##....",
            ".#####...",
            ".#######.",
            ".########",
            ".#..#..#.",
            ".#..#..#."]
    bitmap(im, 12, 11, BEAR, {'#': INK})
    for x in range(12, 20, 2):
        px(im, x, 17, INK)
    return im

def desgagnes():
    im = blank((22, 45, 125))
    rect(im, 4, 5, 27, 18, WHITE)
    NV = (25, 50, 150)
    rect(im, 7, 7, 24, 15, NV)
    # outline + pole
    for x in range(7, 25):
        px(im, x, 6, INK); px(im, x, 16, INK)
    for y in range(6, 17):
        px(im, 24, y, INK)
    for y in range(6, 18):
        px(im, 6, y, INK)
    rect(im, 20, 8, 20, 14, GREEN)
    rect(im, 21, 8, 22, 14, YELLOW)
    # text dashes
    for x in (9, 10, 12, 13, 15, 16, 18):
        px(im, x, 11, WHITE)
    return im

def mcasphalt():
    im = blank(BLACK)
    rect(im, 0, 15, 31, 31, (235, 75, 35))
    for y in range(S):
        for x in range(S):
            m = abs(x-16) + abs(y-14)
            if m < 9:
                px(im, x, y, INK)
            elif m == 9:
                px(im, x, y, WHITE)
    # mc mini
    bitmap(im, 12, 10, ["#.#","###","#.#"], {'#': WHITE})
    bitmap(im, 16, 10, ["##","#.","##"], {'#': WHITE})
    rect(im, 17, 12, 19, 13, RED)
    A = ["..###..",
         "..#.#..",
         ".##.##.",
         ".#...#.",
         ".#####.",
         "##...##",
         "#.....#"]
    bitmap(im, 13, 14, A, {'#': WHITE})
    return im

# ================= build all =================
ORDER = [
    ("BIGLIFT", biglift), ("GRANATH", granath), ("GROUPOCEAN", groupocean),
    ("FEDNAV", fednav), ("CLIFFS", cliffs), ("CALFORNAV", calfornav),
    ("BRIESE", briese), ("CCG", ccg), ("ASC", asc), ("VTB", vtb),
    ("USCG", uscg), ("WAGENBORG", wagenborg), ("TBMARINE", tbmarine),
    ("CSL", csl), ("SPLIETHOFF", spliethoff), ("G3", g3), ("ANDRIE", andrie),
    ("CARISBROOKE", carisbrooke), ("POLSTEAM", polsteam), ("COASTAL", coastal),
    ("MCKEIL", mckeil), ("NMBULGARE", nmbulgare), ("NEAS", neas),
    ("USEPA", usespa), ("LOWERLAKES", lowerlakes), ("ALGOMA", algoma),
    ("DESGAGNES", desgagnes), ("MCASPHALT", mcasphalt),
]

def tapered(im):
    out = im.convert("RGBA")
    pxd = out.load()
    for y in range(S):
        ins = round(3 * (31 - y) / 31)
        for x in range(S):
            if x < ins or x > 31 - ins:
                pxd[x, y] = (0, 0, 0, 0)
    return out

sq_dir = os.path.join(OUT, "full_square")
fn_dir = os.path.join(OUT, "funnel_shape")
os.makedirs(sq_dir, exist_ok=True)
os.makedirs(fn_dir, exist_ok=True)

squares, funnels = {}, {}
for name, fn in ORDER:
    im = fn()
    squares[name] = im
    funnels[name] = tapered(im)
    im.convert("RGBA").save(os.path.join(sq_dir, f"{name}_32.png"))
    funnels[name].save(os.path.join(fn_dir, f"{name}_32.png"))

# sprite sheet (square versions, 7x4)
sheet = Image.new("RGB", (7*S, 4*S), (0, 0, 0))
for i, (name, _) in enumerate(ORDER):
    sheet.paste(squares[name], ((i % 7)*S, (i // 7)*S))
sheet.save(os.path.join(OUT, "spritesheet_224x128.png"))

# preview: funnel versions, 8x upscale on dark bg with labels
sc, pad, lab = 8, 10, 14
cw, ch = S*sc, S*sc + lab
prev = Image.new("RGB", (7*cw + 8*pad, 4*ch + 5*pad), (14, 14, 14))
d = ImageDraw.Draw(prev)
for i, (name, _) in enumerate(ORDER):
    cx = pad + (i % 7) * (cw + pad)
    cy = pad + (i // 7) * (ch + pad)
    big = funnels[name].resize((S*sc, S*sc), Image.NEAREST)
    prev.paste(big, (cx, cy), big)
    d.text((cx + 2, cy + S*sc + 2), name, fill=(200, 200, 200))
prev.save(os.path.join(OUT, "preview.png"))
print("done", len(ORDER))
