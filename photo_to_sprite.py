#!/usr/bin/env python3
"""
photo_to_sprite.py  --  Turn a funnel photo (e.g. from boatnerd.com) into a
32x32 sprite that uses the project's CANONICAL TRAPEZOID shape.

Shape vs. livery are kept separate:
  * SHAPE  -- one fixed trapezoid silhouette (CANON_MASK_SPANS.json), the shape
    already used by every existing sprite. Every output uses it, so the whole
    set stays visually consistent regardless of how each photo was cropped.
  * LIVERY -- the colors come from the photo: the funnel's horizontal color
    bands are sampled top-to-bottom and painted into the canonical shape.

Usage:
    python3 photo_to_sprite.py WAGENBORG          # uses funnels/WAGENBORG.png
    python3 photo_to_sprite.py POLSTEAM polsteam.png   # explicit snip name
    python3 photo_to_sprite.py NMBULGARE --rows 44 88  # funnels/NMBULGARE.png
    # Snip the funnel and save it as funnels/<KEY>.png; then just pass the key.
    # --rows TOP BOTTOM: percent of image height bounding just the funnel, if
    #   the snip has sky/ship around it (default: full image height).

It updates ship_sprites.h in place (adds/replaces SPR_<KEY>) and writes a
preview PNG. Run once per operator as you collect funnel references.
"""

import argparse
import json
import os
import re
import sys

from PIL import Image

SIZE = 32
MINI = 8
# --- LED-panel tunables ------------------------------------------------------
# Dimmest a LIT pixel may be: dark liveries are floored to this so a black
# funnel glows faintly instead of vanishing into the off (unlit) panel.
BLACK_FLOOR = (40, 40, 40)
# Single color tracing the canonical trapezoid's inner border. Defines the
# silhouette (so dark fills still read as a shape) and overwrites edge noise.
# Set RING_COLOR = None to disable the ring entirely.
RING_COLOR = (80, 80, 80)
# Canonical anchors for the no-palette path: when an operator has NO configured
# palette, k-means clusters that are near-grey (saturation <= GREY_SAT) are
# snapped to one of these by lightness, killing scanner sheen and giving the
# same black/white across every unconfigured operator. Saturated hues (a red or
# blue funnel) pass through untouched. A configured palette skips all of this
# and is always more reliable -- this is just a sensible automatic fallback.
CANON_DARK = (40, 40, 40)
CANON_LIGHT = (235, 235, 235)
GREY_SAT = 28          # max (maxchannel - minchannel) to count a color as grey
HERE = os.path.dirname(os.path.abspath(__file__))
MASK_PATH = os.path.join(HERE, "CANON_MASK_SPANS.json")
HEADER = os.path.join(HERE, "register_esp32", "ship_sprites.h")
PALETTES_PATH = os.path.join(HERE, "LIVERY_PALETTES.json")


def load_mask():
    """Per-row [lo,hi) spans of the canonical trapezoid. Falls back to a built
    trapezoid if the spans file is missing."""
    if os.path.exists(MASK_PATH):
        return [tuple(s) for s in json.load(open(MASK_PATH))]
    spans = []
    cx = SIZE / 2
    for y in range(SIZE):
        frac = y / (SIZE - 1)
        half = (0.30 + 0.68 * frac) * (SIZE / 2)
        spans.append((max(0, int(round(cx - half))),
                      min(SIZE, int(round(cx + half)))))
    return spans


MASK = load_mask()


def _autotrim(img):
    """Trim surrounding near-white background to the funnel's bounding box, so
    the funnel fills the frame (and the logo isn't shrunk by stray margin)."""
    import numpy as np
    a = np.asarray(img.convert("L"))
    fg = a < 224
    ys = np.where(fg.any(axis=1))[0]
    xs = np.where(fg.any(axis=0))[0]
    if len(ys) == 0 or len(xs) == 0:
        return img
    return img.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))



def load_palettes():
    """Per-operator livery config (colors verified out-of-band). Optional;
    operators without an entry fall back to adaptive k-means."""
    if os.path.exists(PALETTES_PATH):
        return json.load(open(PALETTES_PATH))
    return {}


PALETTES = load_palettes()


def _srgb_to_oklab(rgb):
    """Vectorized sRGB(0-255) -> OKLab. Perceptual space: a saturated minority
    color (a thin red band) keeps its own identity instead of averaging into
    the dominant field color."""
    import numpy as np
    def lin(c):
        c = c / 255.0
        return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    r, g, b = lin(rgb[..., 0]), lin(rgb[..., 1]), lin(rgb[..., 2])
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = np.cbrt(l), np.cbrt(m), np.cbrt(s)
    return np.stack([
        0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s,
    ], -1)


def _snap_to_palette(arr, palette):
    """arr (N,3) uint8 -> (labels, palette_uint8). Each pixel snaps to its
    nearest supplied livery color in OKLab. Deterministic: no clustering, no
    invented edge colors, so the trapezoid border stays clean."""
    import numpy as np
    pal = np.array(palette, dtype=float)
    la = _srgb_to_oklab(arr.astype(float))
    lp = _srgb_to_oklab(pal)
    lbl = ((la[:, None, :] - lp[None, :, :]) ** 2).sum(2).argmin(1)
    return lbl, pal.round().astype(np.uint8)


def _canon_snap(pal):
    """For the no-palette path: snap each near-grey palette color to CANON_DARK
    or CANON_LIGHT (by lightness), leaving saturated hues alone. Collapses
    scanner sheen (a spread of greys) to two consistent values shared across all
    unconfigured operators. Saturation = maxchannel - minchannel."""
    import numpy as np
    out = []
    for c in pal:
        c = np.asarray(c, dtype=int)
        sat = int(c.max()) - int(c.min())
        if sat <= GREY_SAT:
            out.append(CANON_DARK if c.mean() < 128 else CANON_LIGHT)
        else:
            out.append(tuple(int(v) for v in c))
    return np.array(out, dtype=np.uint8)


def _quantize_oklab(arr, k, iters=20, seed=1):
    """Fallback for operators with no configured palette: k-means (k-means++
    init) in OKLab. Seeded for reproducibility; bump seed if a livery clusters
    oddly. Prefer adding the operator to LIVERY_PALETTES.json instead."""
    import numpy as np
    lab = _srgb_to_oklab(arr.astype(float))
    rng = np.random.default_rng(seed)
    cents = [lab[rng.integers(len(lab))]]
    for _ in range(k - 1):
        d = np.min([((lab - c) ** 2).sum(1) for c in cents], axis=0)
        cents.append(lab[rng.choice(len(lab), p=d / d.sum())])
    cents = np.array(cents)
    for _ in range(iters):
        lbl = ((lab[:, None, :] - cents[None, :, :]) ** 2).sum(2).argmin(1)
        new = np.array([lab[lbl == i].mean(0) if (lbl == i).any() else cents[i]
                        for i in range(k)])
        if np.allclose(new, cents):
            break
        cents = new
    lbl = ((lab[:, None, :] - cents[None, :, :]) ** 2).sum(2).argmin(1)
    pal = np.array([arr[lbl == i].mean(0) if (lbl == i).any() else arr[0]
                    for i in range(k)]).round().astype(np.uint8)
    return lbl, pal


def _auto_palette(arr, kmax=8, rel_merge=0.33, min_frac=0.02):
    """Fully automatic palette: over-cluster to kmax, drop tiny clusters, then
    merge clusters that are close *relative to the palette's own spread* (so a
    high-contrast 2-color livery barely merges while a sheen-spread multicolor
    collapses its near-duplicates), canonicalize greys to shared dark/light, and
    re-snap every pixel to the final set. Returns (labels_flat, palette).
    No per-operator config or --colors needed."""
    import numpy as np
    N = len(arr)
    lbl, rgbpal = _quantize_oklab(arr, kmax)
    cnt = np.array([(lbl == i).sum() for i in range(kmax)])
    keep = [i for i in range(kmax) if cnt[i] >= min_frac * N]
    labp = _srgb_to_oklab(rgbpal.astype(float))
    # scale the merge threshold to the widest gap among kept clusters
    if len(keep) > 1:
        dd = [np.sqrt(((labp[i] - labp[j]) ** 2).sum())
              for a, i in enumerate(keep) for j in keep[a + 1:]]
        scale = max(dd)
    else:
        scale = 1.0
    thresh = rel_merge * scale
    groups = []
    for i in keep:
        for g in groups:
            if np.sqrt(((labp[i] - labp[g[0]]) ** 2).sum()) < thresh:
                g.append(i)
                break
        else:
            groups.append([i])
    colors = []
    for g in groups:
        w = cnt[g]
        col = (rgbpal[g] * w[:, None]).sum(0) / w.sum()
        col = col.round().astype(int)
        sat = int(col.max()) - int(col.min())
        if sat <= GREY_SAT:
            col = np.array(CANON_DARK if col.mean() < 128 else CANON_LIGHT)
        c = tuple(int(v) for v in col)
        if c not in colors:
            colors.append(c)
    pal = np.array(colors, dtype=np.uint8)
    la = _srgb_to_oklab(arr.astype(float))
    lp = _srgb_to_oklab(pal.astype(float))
    final = ((la[:, None, :] - lp[None, :, :]) ** 2).sum(2).argmin(1)
    return final, pal


def _knock_bg(img, bg, tol):
    """Set pixels within `tol` of color `bg` to pure black (LED off). Used
    before scaling so background never bleeds into the livery palette."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(int)
    bg = np.array(bg, dtype=int)
    d = np.sqrt(((a - bg) ** 2).sum(2))
    a[d <= tol] = (0, 0, 0)
    return Image.fromarray(a.astype("uint8"), "RGB")


def _flatfield(img):
    """Correct directional scanner sheen on glossy prints. Estimates the smooth
    illumination across the image (a heavily-downscaled, then upscaled, blur of
    luminance) and divides it out, so a flat color reads the same brightness
    left-to-right. Without this, sheen on one side lifts dark pixels above the
    black/white midpoint and they wrongly snap to white (ragged band edges).
    Uses PIL resampling for the blur -- no scipy needed."""
    import numpy as np
    rgb = np.asarray(img.convert("RGB")).astype(float)
    lum = img.convert("L")
    w, h = lum.size
    # blur via shrink->grow: downscale to a tiny grid (kills band detail, keeps
    # the gradient), then bilinearly upscale back to a smooth illumination map.
    small = max(2, min(w, h) // 32)
    illum = np.asarray(
        lum.resize((small, small), Image.BILINEAR)
           .resize((w, h), Image.BILINEAR)
    ).astype(float)
    illum = np.clip(illum, 1.0, None)
    gain = (np.median(illum) / illum)[..., None]
    corr = np.clip(rgb * gain, 0, 255).astype("uint8")
    return Image.fromarray(corr, "RGB")


def _deorphan(grid, mask_bool):
    """Flip only pixels with ZERO same-color 4-neighbors (true specks) to the
    majority of their in-mask neighbors. Leaves connected 1px strokes intact,
    so logo detail survives."""
    import numpy as np
    g = grid.copy(); H, W = g.shape; out = g.copy()
    for y in range(H):
        for x in range(W):
            if not mask_bool[y, x]:
                continue
            n4 = [g[y + dy, x + dx] for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))
                  if 0 <= y + dy < H and 0 <= x + dx < W and mask_bool[y + dy, x + dx]]
            if n4 and g[y, x] not in n4:
                v, c = np.unique(n4, return_counts=True)
                out[y, x] = v[c.argmax()]
    return out


def conform(photo, rows_pct, colors=6, cfg=None):
    """Conform a funnel snip into the canonical trapezoid sprite for an LED
    matrix. Pixels OUTSIDE the mask are OFF (black). Inside: snapped to the
    operator's configured livery palette if available (clean, deterministic),
    else adaptive OKLab k-means. The mask forces a crisp trapezoid edge and
    discards the noisy photo rim; deorphan removes stray in-shape specks."""
    import numpy as np
    cfg = cfg or {}
    img = photo.convert("RGB")

    # --- source region: explicit crop box wins; else --rows %; else autotrim
    crop = cfg.get("crop")
    if crop:
        img = img.crop(tuple(crop))           # (left, top, right, bottom)
    else:
        w, h = img.size
        top = int(rows_pct[0] / 100 * h)
        bot = int(rows_pct[1] / 100 * h)
        if bot > top:
            img = img.crop((0, top, w, bot))
    img = _autotrim(img)

    # --- flat-field: correct directional glossy-scan sheen before anything
    # else samples color. On by cfg "flatfield": true (or --flatfield). Skipped
    # in textured mode, which deliberately keeps the raw punchy contrast.
    if cfg.get("flatfield") and not cfg.get("textured"):
        img = _flatfield(img)

    # --- background knockout (-> black/off) before scaling, if configured
    bg = cfg.get("bg")
    if isinstance(bg, (list, tuple)):
        img = _knock_bg(img, bg, cfg.get("bg_tol", 40))

    # --- BOX (area-average) downscale: no Lanczos ringing -> fewer phantom
    # edge colors. Then reduce to a small flat palette.
    scaled = img.resize((SIZE, SIZE), Image.BOX)
    arr = np.asarray(scaled).reshape(-1, 3)

    pal_cfg = cfg.get("palette")
    if pal_cfg:
        # optional override: a hand-specified palette still wins if present
        lbl, pal = _snap_to_palette(arr, pal_cfg)
    elif cfg.get("textured"):
        # textured mode: raw k-means clusters, greys KEPT (no canon-snap, no
        # merge) for a richer scanned-print look. Good for monochrome stacks
        # where the sheen reads as texture rather than noise.
        n = cfg.get("textured_colors", 6)
        lbl, pal = _quantize_oklab(arr, n)
    else:
        # default: fully automatic palette detection (adaptive merge + greys)
        lbl, pal = _auto_palette(arr)

    grid = lbl.reshape(SIZE, SIZE)

    # --- mask: in-shape boolean, then clean stray specks inside it
    mb = np.zeros((SIZE, SIZE), bool)
    for y in range(SIZE):
        lo, hi = MASK[y]
        mb[y, lo:hi] = True
    grid = _deorphan(grid, mb)

    # --- paint: outside mask = OFF (0,0,0); inside = snapped livery color,
    # floored to BLACK_FLOOR so dark liveries stay faintly lit (not invisible).
    out = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
    px = out.load()
    for y in range(SIZE):
        for x in range(SIZE):
            if mb[y, x]:
                r, g, b = (int(v) for v in pal[grid[y, x]])
                if max(r, g, b) < max(BLACK_FLOOR):
                    r, g, b = BLACK_FLOOR     # lift dim/near-black to the floor
                px[x, y] = (r, g, b)

    # --- ring: trace the mask's inner border in one color, drawn LAST so it
    # defines the silhouette and overwrites whatever edge noise was there.
    if RING_COLOR is not None:
        for y in range(SIZE):
            for x in range(SIZE):
                if not mb[y, x]:
                    continue
                edge = False
                for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                    yy, xx = y + dy, x + dx
                    if not (0 <= yy < SIZE and 0 <= xx < SIZE) or not mb[yy, xx]:
                        edge = True
                        break
                if edge:
                    px[x, y] = RING_COLOR
    return out


def make_mini(img):
    """Bake a faithful MINI x MINI chip from the finished SIZE x SIZE sprite
    (Method C: per-block dominant color, off-pixels ignored). Done at gen time
    so the device just blits MINI*MINI pixels instead of rescaling each frame.
    Block = (SIZE//MINI) square; the most common non-off color wins; a block
    that is mostly off stays off."""
    import numpy as np
    a = np.asarray(img.convert("RGB"))
    b = SIZE // MINI
    out = Image.new("RGB", (MINI, MINI), (0, 0, 0))
    px = out.load()
    for cy in range(MINI):
        for cx in range(MINI):
            blk = a[cy * b:(cy + 1) * b, cx * b:(cx + 1) * b].reshape(-1, 3)
            lit = blk[(blk.max(1) > 0)]            # drop off (0,0,0) pixels
            if len(lit) < (b * b) // 4:            # mostly off -> stay off
                continue
            # dominant color among lit pixels (exact match on flat palette)
            cols, cnts = np.unique(lit, axis=0, return_counts=True)
            px[cx, cy] = tuple(int(v) for v in cols[cnts.argmax()])
    return out


def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


def _decl(name, img, n):
    px = img.load(); w, h = img.size
    vals = [f"0x{rgb565(*px[x, y]):04X}" for y in range(h) for x in range(w)]
    return f"const uint16_t SPR_{name}[{n}] PROGMEM = {{ {', '.join(vals)} }};"


def upsert_header(key, img, mini=None):
    decl = _decl(key, img, SIZE * SIZE)
    mini_decl = _decl(f"{key}_MINI", mini, MINI * MINI) if mini is not None else None
    if not os.path.exists(HEADER):
        print(f"!! {HEADER} not found -- restore it first (git checkout "
              f"ship_sprites.h), then re-run.", file=sys.stderr)
        sys.exit(1)
    txt = open(HEADER).read()
    pat = re.compile(rf'const uint16_t SPR_{key}\[\d+\][^;]*;')
    if pat.search(txt):
        txt = pat.sub(decl, txt)
        action = "replaced"
    else:
        # insert before the SPRITE_TABLE line so it's picked up
        idx = txt.find("struct SpriteEntry")
        if idx == -1:
            txt = txt.rstrip() + "\n" + decl + "\n"
        else:
            txt = txt[:idx] + decl + "\n\n" + txt[idx:]
            # also add to the table + count if not present
            if f'"{key}"' not in txt:
                txt = re.sub(r'(const SpriteEntry SPRITE_TABLE\[\] = \{ )',
                             rf'\1{{"{key}", SPR_{key}}}, ', txt)
                txt = re.sub(r'(const int SPRITE_COUNT = )(\d+)',
                             lambda m: f"{m.group(1)}{int(m.group(2))+1}", txt)
        action = "added"
    # --- mini chip: replace if present, else append right after the main decl
    if mini_decl is not None:
        mpat = re.compile(rf'const uint16_t SPR_{key}_MINI\[\d+\][^;]*;')
        if mpat.search(txt):
            txt = mpat.sub(mini_decl, txt)
        else:
            # place it immediately after the main SPR_<key> declaration
            anchor = re.search(rf'const uint16_t SPR_{key}\[\d+\][^;]*;', txt)
            if anchor:
                i = anchor.end()
                txt = txt[:i] + "\n" + mini_decl + txt[i:]
            else:
                txt = txt.rstrip() + "\n" + mini_decl + "\n"
    open(HEADER, "w").write(txt)
    print(f"{action} SPR_{key}"
          + (" + _MINI" if mini_decl is not None else "")
          + " in ship_sprites.h")


FUNNELS_DIR = os.path.join(HERE, "funnels")


def resolve_photo(photo, key, cfg=None):
    cfg = cfg or {}
    """Find the funnel snip. Tries, in order: the path as given; that name
    inside funnels/; and funnels/<key>.<ext> so you can omit the photo and just
    name the operator (e.g. a snip saved as funnels/WAGENBORG.png)."""
    cands = []
    if photo:
        cands += [photo, os.path.join(FUNNELS_DIR, photo)]
    f = cfg.get("file")
    if f:
        cands += [os.path.join(FUNNELS_DIR, f), os.path.join(HERE, f), f]
    # by key, common extensions
    for ext in ("png", "jpg", "jpeg", "webp", "bmp"):
        cands.append(os.path.join(FUNNELS_DIR, f"{key}.{ext}"))
        cands.append(os.path.join(FUNNELS_DIR, f"{key.lower()}.{ext}"))
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def main():
    ap = argparse.ArgumentParser(
        description="Conform a funnel snip into the canonical trapezoid sprite. "
                    "Looks in funnels/ by name or by operator key.")
    ap.add_argument("key", help="operator key, e.g. WAGENBORG")
    ap.add_argument("photo", nargs="?", default=None,
                    help="snip path or filename; if omitted, uses "
                         "funnels/<KEY>.png (or .jpg/.jpeg/.webp/.bmp)")
    ap.add_argument("--rows", nargs=2, type=float, default=[0, 100],
                    metavar=("TOP%", "BOT%"),
                    help="funnel's vertical extent as %% of image height")
    ap.add_argument("--colors", type=int, default=6,
                    help="palette size; flattens gradients/stray off-whites "
                         "into N clean colors (default 6; use 1 to disable)")
    ap.add_argument("--flatfield", action="store_true",
                    help="correct directional glossy-scan sheen before "
                         "sampling (for scans where one side is lit brighter)")
    args = ap.parse_args()
    key = args.key.upper()
    cfg = dict(PALETTES.get(key, {}))
    if args.flatfield:
        cfg["flatfield"] = True
    path = resolve_photo(args.photo, key, cfg)
    if not path:
        looked = args.photo or f"funnels/{key}.png"
        print(f"snip not found for {key} (looked for '{looked}' and in "
              f"funnels/). Save the snip as funnels/{key}.png or pass its path.",
              file=sys.stderr)
        sys.exit(1)
    print(f"using {path}")
    if cfg.get("palette"):
        print(f"palette override: {len(cfg['palette'])} colors from config")
    else:
        print("auto-detecting palette")
    img = conform(Image.open(path), args.rows, args.colors, cfg)
    mini = make_mini(img)
    img.resize((SIZE * 10, SIZE * 10), Image.NEAREST).save(f"sprite_{key}.png")
    print(f"wrote sprite_{key}.png (review it)")
    upsert_header(key, img, mini)


if __name__ == "__main__":
    main()