#!/usr/bin/env python3
"""
gallery.py  --  Render a labeled gallery of all sprites in ship_sprites.h.

Shows each funnel larger than the cramped default sprites_preview.png, on a
black background like the panel, with its operator key underneath. Run after
regenerating sprites to eyeball the whole fleet.

    python3 gallery.py
    -> writes gallery.png
"""

from PIL import Image, ImageDraw

# Reuse the mock panel's sprite-header parser.
import mock_panel as m


def build(out="gallery.png", scale=9, cols=4):
    sprites, sz = m.load_sprites("ship_sprites.h")
    keys = sorted(sprites.keys())
    if not keys:
        print("no sprites found in ship_sprites.h")
        return

    cell = sz * scale
    labelh, pad = 16, 8
    rows = (len(keys) + cols - 1) // cols
    W = cols * (cell + pad) + pad
    H = rows * (cell + labelh + pad) + pad

    canvas = Image.new("RGB", (W, H), (15, 15, 15))
    d = ImageDraw.Draw(canvas)

    def rgb565_to_rgb(c):
        r = (c >> 11) & 0x1F
        g = (c >> 5) & 0x3F
        b = c & 0x1F
        return (r * 255 // 31, g * 255 // 63, b * 255 // 31)

    def draw_sprite(key, ox, oy):
        vals = sprites[key]
        spr = Image.new("RGB", (sz, sz), (0, 0, 0))
        px = spr.load()
        for y in range(sz):
            for x in range(sz):
                c = vals[y * sz + x]
                if c != 0:
                    px[x, y] = rgb565_to_rgb(c)
        canvas.paste(spr.resize((cell, cell), Image.NEAREST), (ox, oy))

    for i, k in enumerate(keys):
        r, c = divmod(i, cols)
        ox = pad + c * (cell + pad)
        oy = pad + r * (cell + labelh + pad)
        d.rectangle([ox - 1, oy - 1, ox + cell, oy + cell], outline=(50, 50, 50))
        draw_sprite(k, ox, oy)
        d.text((ox + 2, oy + cell + 3), k, fill=(200, 200, 200))

    canvas.save(out)
    print(f"wrote {out}: {len(keys)} sprites at {sz}x{sz}")


if __name__ == "__main__":
    build()
