#!/usr/bin/env python3
"""
flip_sprites.py  --  Horizontally mirror every sprite in ship_sprites.h, in
place. One-time use: aligns the existing sprite set to the Know Your Ships scan
orientation (cap leaning left, body bulging right). Writes a .bak first.

    python3 flip_sprites.py [ship_sprites.h]
"""
import re
import shutil
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else "ship_sprites.h"


def main():
    txt = open(PATH).read()
    m = re.search(r'#define SPRITE_SIZE (\d+)', txt)
    size = int(m.group(1)) if m else 32

    def flip_array(match):
        key = match.group(1)
        vals = match.group(2)
        nums = re.findall(r'0x[0-9A-Fa-f]+', vals)
        if len(nums) != size * size:
            return match.group(0)            # leave anything unexpected alone
        # mirror each row left-right
        out = []
        for y in range(size):
            row = nums[y * size:(y + 1) * size]
            out.extend(reversed(row))
        return (f"const uint16_t SPR_{key}[{size*size}] PROGMEM = "
                f"{{ {', '.join(out)} }};")

    new = re.sub(
        r'const uint16_t SPR_(\w+)\[\d+\]\s*PROGMEM\s*=\s*\{([^}]+)\}\s*;',
        flip_array, txt)

    shutil.copy(PATH, PATH + ".bak")
    open(PATH, "w").write(new)
    n = len(re.findall(r'const uint16_t SPR_\w+\[', new))
    print(f"flipped {n} sprites in {PATH} (backup at {PATH}.bak)")


if __name__ == "__main__":
    main()
