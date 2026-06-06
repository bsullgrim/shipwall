#!/usr/bin/env python3
"""
photo_to_sprite.py  --  Turn real funnel photos into panel sprites.

My hand-drawn liveries were guesses (and wrong). This builds sprites from
actual photographs instead: crop the funnel, downsample with area averaging,
quantize to a handful of flat colors so the bands read cleanly at 16px, and
emit the same ship_sprites.h the firmware + mock_panel already consume.

WORKFLOW
--------
1. Put funnel photos in  funnels/<OPERATOR_KEY>.<ext>   e.g. funnels/CSL.jpg
   The filename (uppercased, sans extension) becomes the sprite key, so it
   MUST match the operator keys in operators.py (CSL, ALGOMA, FEDNAV, ...).

2. (Optional) add a crop + tuning entry to funnels/config.json -- see below.
   Without it, the script uses the whole image and auto-detects background.

3. Run:
       pip install pillow
       python3 photo_to_sprite.py

   Outputs:
       sprites_preview.png   (scaled-up grid to eyeball every sprite)
       ship_sprites.h        (drop-in replacement; reflash / restart mock)

CONFIG (funnels/config.json) -- all fields optional, per operator key:
{
  "CSL": {
    "crop": [120, 40, 360, 400],   // left, top, right, bottom (px) on the photo
    "size": 16,                    // sprite edge (default 16)
    "colors": 4,                   // palette size after quantize (default 5)
    "bg": "auto",                  // "auto", "none", or [r,g,b] to treat as transparent
    "bg_tol": 40                   // how close to bg color counts as background
  }
}
"""

import json
import os

from PIL import Image

FUNNEL_DIR = "funnels"
CONFIG_PATH = os.path.join(FUNNEL_DIR, "config.json")
DEFAULT_SIZE = 16
DEFAULT_COLORS = 5
TRANSPARENT = (0, 0, 0)   # rendered as transparent by firmware + mock

# An LED panel can't show true black against a black background, so in-funnel
# black is lifted to a visible dark grey. (Background transparency is handled
# separately via alpha / bg knockout, so the funnel silhouette still reads.)
DARK_LIFT = (45, 45, 45)        # what in-funnel black becomes
BLACK_THRESHOLD = 50            # quantized colors at/below this (max channel) are "black"

# Near-white pixels get snapped to clean white, countering the pink/grey tint
# that downscaling produces when a white band blends into adjacent red/black.
WHITE_SNAP = (235, 235, 235)
WHITE_THRESHOLD = 165           # if all channels >= this, snap to clean white


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def auto_bg_color(img):
    """Guess the background color from the image corners (usually sky/water).
    img is a small RGB PIL image. Returns an (r,g,b) tuple."""
    w, h = img.size
    k = max(2, min(w, h) // 10)
    px = img.load()
    samples = []
    for (x0, y0) in [(0, 0), (w - k, 0), (0, h - k), (w - k, h - k)]:
        for dx in range(k):
            for dy in range(k):
                samples.append(px[x0 + dx, y0 + dy])
    # Median per channel, robust to a stray dark pixel in a corner.
    samples.sort(key=lambda c: c[0] + c[1] + c[2])
    return samples[len(samples) // 2]


def quantize(img, n_colors):
    """Reduce a small RGB PIL image to n_colors flat colors using Pillow's
    median-cut quantizer. Returns an RGB image with at most n_colors colors."""
    q = img.quantize(colors=max(1, n_colors), method=Image.MEDIANCUT)
    return q.convert("RGB")


def _dist(a, b):
    return ((a[0]-b[0])**2 + (a[1]-b[1])**2 + (a[2]-b[2])**2) ** 0.5


def make_sprite(path, cfg):
    size = cfg.get("size", DEFAULT_SIZE)
    n_colors = cfg.get("colors", DEFAULT_COLORS)

    img = Image.open(path)
    # Preserve alpha so transparent-background PNG stacks knock out cleanly.
    has_alpha = img.mode in ("RGBA", "LA") or (
        img.mode == "P" and "transparency" in img.info)
    alpha = None
    if has_alpha:
        rgba = img.convert("RGBA")
        alpha = rgba.split()[3]
        img = rgba.convert("RGB")
    else:
        img = img.convert("RGB")

    if "crop" in cfg:
        box = tuple(cfg["crop"])
        img = img.crop(box)
        if alpha is not None:
            alpha = alpha.crop(box)

    # Downsample with area averaging (LANCZOS) so bands blend cleanly, not alias.
    small = img.resize((size, size), Image.LANCZOS)
    small_alpha = alpha.resize((size, size), Image.LANCZOS) if alpha else None

    # Background color to knock out (-> transparent).
    bg_mode = cfg.get("bg", "auto")
    bg_tol = cfg.get("bg_tol", 40)
    bg_rgb = None
    if bg_mode == "auto":
        bg_rgb = auto_bg_color(img.resize((64, 64), Image.LANCZOS))
    elif isinstance(bg_mode, (list, tuple)):
        bg_rgb = tuple(bg_mode)
    # bg_mode == "none" leaves bg_rgb None (keep everything opaque)

    quant = quantize(small, n_colors)
    qpx = quant.load()
    apx = small_alpha.load() if small_alpha else None

    # Per-operator overrides for the cleanup constants.
    dark_lift = tuple(cfg.get("dark_lift", DARK_LIFT))
    white_snap = bool(cfg.get("white_snap", True))

    # Fixed-palette mode: if the operator specifies its real livery colors,
    # snap every pixel to the nearest one. This eliminates the muddy greys that
    # median-cut + downscale produce on funnels with crests/fine detail -- every
    # pixel becomes an unambiguous livery color. The default (no palette) keeps
    # the quantizer + black-lift/white-snap heuristics.
    palette = cfg.get("palette")            # e.g. [[40,40,40],[200,30,30],[235,235,235]]
    if palette:
        palette = [tuple(c) for c in palette]
        spx = small.load()                  # use the downsampled image directly
        out = Image.new("RGB", (size, size), TRANSPARENT)
        opx = out.load()
        for y in range(size):
            for x in range(size):
                if apx is not None and apx[x, y] < 128:
                    opx[x, y] = TRANSPARENT
                    continue
                color = spx[x, y]
                if bg_rgb is not None and _dist(color, bg_rgb) < bg_tol:
                    opx[x, y] = TRANSPARENT
                    continue
                nearest = min(palette, key=lambda p: _dist(color, p))
                # A palette black still needs lifting so it shows on the panel.
                if max(nearest) <= BLACK_THRESHOLD:
                    nearest = dark_lift
                opx[x, y] = nearest
        return out, size

    out = Image.new("RGB", (size, size), TRANSPARENT)
    opx = out.load()
    for y in range(size):
        for x in range(size):
            # Alpha-transparent pixels (from PNG) become panel-transparent.
            if apx is not None and apx[x, y] < 128:
                opx[x, y] = TRANSPARENT
                continue
            color = qpx[x, y]
            # Background-colored pixels become transparent.
            if bg_rgb is not None and _dist(color, bg_rgb) < bg_tol:
                opx[x, y] = TRANSPARENT
                continue
            r, g, b = color
            # In-funnel black would be invisible on a black panel -> lift to
            # a visible dark grey so the funnel's black bands/cap read.
            if max(r, g, b) <= BLACK_THRESHOLD:
                opx[x, y] = dark_lift
            # Near-white -> clean white, undoing the pink/grey downscale tint.
            elif white_snap and min(r, g, b) >= WHITE_THRESHOLD:
                opx[x, y] = WHITE_SNAP
            else:
                opx[x, y] = color
    return out, size


def rgb565(r, g, b):
    return ((int(r) & 0xF8) << 8) | ((int(g) & 0xFC) << 3) | (int(b) >> 3)


def export_preview(sprites, scale=14):
    keys = list(sprites.keys())
    if not keys:
        return
    size = sprites[keys[0]][1]
    cols = len(keys)
    pad = 4
    canvas = Image.new("RGB", (cols * (size * scale + pad) + pad,
                               size * scale + 2 * pad + 16), (30, 30, 30))
    for i, k in enumerate(keys):
        im = sprites[k][0].resize((size * scale, size * scale), Image.NEAREST)
        canvas.paste(im, (pad + i * (size * scale + pad), pad))
    canvas.save("sprites_preview.png")
    print("wrote sprites_preview.png")


def _make_unknown(size):
    """A plain grey funnel as the fallback sprite."""
    img = Image.new("RGB", (size, size), (0, 0, 0))
    px = img.load()
    for y in range(2, size - 2):
        for x in range(size // 4, size * 3 // 4):
            px[x, y] = (110, 110, 110)
    return img


def export_header(sprites):
    if not sprites:
        print("no sprites to write")
        return
    size = sprites[next(iter(sprites))][1]
    lines = [
        "// ship_sprites.h  --  generated by photo_to_sprite.py from real photos.",
        "// RGB565 stack-livery sprites for the ESP32 firmware + mock panel.",
        "#pragma once",
        "#include <Arduino.h>",
        f"#define SPRITE_SIZE {size}",
        "",
    ]
    keys = list(sprites.keys())
    # Guarantee an UNKNOWN fallback exists.
    if "UNKNOWN" not in keys:
        sprites["UNKNOWN"] = (_make_unknown(size), size)
        keys.append("UNKNOWN")

    for k in keys:
        px = sprites[k][0].load()
        vals = [f"0x{rgb565(*px[x, y]):04X}"
                for y in range(size) for x in range(size)]
        lines.append(
            f"const uint16_t SPR_{k}[{size*size}] PROGMEM = {{ {', '.join(vals)} }};")
    lines.append("")
    lines.append("struct SpriteEntry { const char* key; const uint16_t* data; };")
    entries = ", ".join(f'{{"{k}", SPR_{k}}}' for k in keys)
    lines.append(f"const SpriteEntry SPRITE_TABLE[] = {{ {entries} }};")
    lines.append(f"const int SPRITE_COUNT = {len(keys)};")
    lines.append("")
    lines.append("inline const uint16_t* spriteForKey(const char* key) {")
    lines.append("  for (int i = 0; i < SPRITE_COUNT; i++) {")
    lines.append("    if (strcmp(SPRITE_TABLE[i].key, key) == 0) return SPRITE_TABLE[i].data;")
    lines.append("  }")
    lines.append("  return SPRITE_TABLE[SPRITE_COUNT - 1].data; // UNKNOWN fallback")
    lines.append("}")
    with open("ship_sprites.h", "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"wrote ship_sprites.h ({len(keys)} sprites at {size}x{size})")


def main():
    cfg_all = load_config()
    if not os.path.isdir(FUNNEL_DIR):
        os.makedirs(FUNNEL_DIR, exist_ok=True)
        print(f"created {FUNNEL_DIR}/ -- drop funnel photos in as <KEY>.jpg and rerun")
        return

    sprites = {}
    exts = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
    # Build a filename -> config lookup. A config entry may set "file" to bind
    # itself to a specific source filename, so descriptive names like
    # "Stack-ASC.png" can map to the operator key "ASC" without renaming.
    by_file = {c["file"]: (k, c) for k, c in cfg_all.items()
               if isinstance(c, dict) and "file" in c}

    for fname in sorted(os.listdir(FUNNEL_DIR)):
        if not fname.lower().endswith(exts):
            continue
        if fname in by_file:
            key, cfg = by_file[fname]
        else:
            key = os.path.splitext(fname)[0].upper()
            cfg = cfg_all.get(key, {})
        try:
            arr, size = make_sprite(os.path.join(FUNNEL_DIR, fname), cfg)
            sprites[key] = (arr, size)
            print(f"  {key}: {fname} -> {size}x{size}"
                  + (" (cropped)" if "crop" in cfg else "")
                  + (" (palette)" if "palette" in cfg else ""))
        except Exception as e:
            print(f"  {key}: FAILED ({e})")

    if not sprites:
        print(f"no photos found in {FUNNEL_DIR}/. Add <KEY>.jpg files and rerun.")
        return

    export_preview(sprites)
    export_header(sprites)
    print("\nReview sprites_preview.png. Tune crops/colors in "
          f"{CONFIG_PATH} and rerun until they look right.")


if __name__ == "__main__":
    main()
