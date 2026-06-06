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
       pip install pillow numpy scikit-learn
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

import numpy as np
from PIL import Image
from sklearn.cluster import KMeans

FUNNEL_DIR = "funnels"
CONFIG_PATH = os.path.join(FUNNEL_DIR, "config.json")
DEFAULT_SIZE = 16
DEFAULT_COLORS = 5
TRANSPARENT = (0, 0, 0)   # rendered as transparent by firmware + mock


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def auto_bg_color(arr):
    """Guess the background color from the image corners (usually sky/water)."""
    h, w, _ = arr.shape
    k = max(2, min(h, w) // 10)
    corners = np.concatenate([
        arr[:k, :k].reshape(-1, 3),
        arr[:k, -k:].reshape(-1, 3),
        arr[-k:, :k].reshape(-1, 3),
        arr[-k:, -k:].reshape(-1, 3),
    ])
    # Median is robust to a stray dark pixel in one corner.
    return np.median(corners, axis=0)


def quantize(arr, n_colors):
    """Reduce to n_colors flat colors via k-means; return (labels, palette)."""
    flat = arr.reshape(-1, 3).astype(float)
    n = min(n_colors, len(np.unique(flat, axis=0)))
    if n < 1:
        n = 1
    km = KMeans(n_clusters=n, n_init=4, random_state=0).fit(flat)
    palette = km.cluster_centers_.astype(int)
    labels = km.labels_.reshape(arr.shape[:2])
    return labels, palette


def make_sprite(path, cfg):
    size = cfg.get("size", DEFAULT_SIZE)
    n_colors = cfg.get("colors", DEFAULT_COLORS)

    img = Image.open(path).convert("RGB")
    if "crop" in cfg:
        img = img.crop(tuple(cfg["crop"]))

    # Downsample with area averaging (LANCZOS) so bands blend cleanly, not alias.
    small = img.resize((size, size), Image.LANCZOS)
    arr = np.array(small)

    # Determine the background color to knock out (-> transparent).
    bg_mode = cfg.get("bg", "auto")
    bg_tol = cfg.get("bg_tol", 40)
    bg_rgb = None
    if bg_mode == "auto":
        bg_rgb = auto_bg_color(np.array(img.resize((64, 64), Image.LANCZOS)))
    elif isinstance(bg_mode, (list, tuple)):
        bg_rgb = np.array(bg_mode, dtype=float)
    # bg_mode == "none" leaves bg_rgb None (keep everything opaque)

    labels, palette = quantize(arr, n_colors)

    # Build the final RGB sprite, mapping background-ish pixels to transparent.
    out = np.zeros((size, size, 3), dtype=int)
    for y in range(size):
        for x in range(size):
            color = palette[labels[y, x]]
            if bg_rgb is not None and np.linalg.norm(color - bg_rgb) < bg_tol:
                out[y, x] = TRANSPARENT
            else:
                # Never let a real funnel pixel be pure black (that's transparent);
                # nudge near-black up by one level so it still shows.
                if tuple(color) == TRANSPARENT:
                    color = np.array([8, 8, 8])
                out[y, x] = color
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
        arr = sprites[k][0]
        im = Image.fromarray(arr.astype("uint8"), "RGB").resize(
            (size * scale, size * scale), Image.NEAREST)
        canvas.paste(im, (pad + i * (size * scale + pad), pad))
    canvas.save("sprites_preview.png")
    print("wrote sprites_preview.png")


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
        grey = np.full((size, size, 3), 0, dtype=int)
        grey[2:size-2, size//4:size*3//4] = [110, 110, 110]
        sprites["UNKNOWN"] = (grey, size)
        keys.append("UNKNOWN")

    for k in keys:
        arr = sprites[k][0]
        vals = [f"0x{rgb565(*arr[y, x]):04X}"
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
    for fname in sorted(os.listdir(FUNNEL_DIR)):
        if not fname.lower().endswith(exts):
            continue
        key = os.path.splitext(fname)[0].upper()
        cfg = cfg_all.get(key, {})
        try:
            arr, size = make_sprite(os.path.join(FUNNEL_DIR, fname), cfg)
            sprites[key] = (arr, size)
            print(f"  {key}: {fname} -> {size}x{size}"
                  + (" (cropped)" if "crop" in cfg else ""))
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
