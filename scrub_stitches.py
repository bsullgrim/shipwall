#!/usr/bin/env python3
"""Drop ONLY the multi-day 'stitched' passages from passages.csv -- live rows
whose gap_min exceeds CAP minutes. Those are two unrelated appearances joined by
a straight line across a coverage hole, not real transits. Everything else is
kept verbatim: historical spotting-log rows (blank mmsi / blank gap) and every
plausibly-real crossing (gap <= CAP).

Operates on the real file in place-safe fashion: reads passages.csv, writes the
scrubbed CSV to stdout and a DROP list + counts to stderr. It does NOT modify the
input. Redirect stdout, verify the counts on stderr, THEN move it into place.

  python3 scrub_stitches.py passages.csv > passages.scrubbed.csv
  #   read stderr: live N -> M, historical preserved
  mv passages.scrubbed.csv passages.csv     # only if the counts look right

Uses stdlib only (no register_service import), so plain python3 is fine.
"""
import csv
import sys

CAP_MIN = 1440.0        # 24h. Real crossings here top out ~23h; the stitched
                        # garbage starts ~41h and runs to weeks. Tune if needed.


def gapval(r):
    g = (r.get("gap_min") or "").strip()
    if not g:
        return None                 # historical rows have no gap -> always kept
    try:
        return float(g)
    except ValueError:
        return None


def main():
    rows = list(csv.DictReader(open(sys.argv[1], newline="")))
    if not rows:
        sys.stderr.write("empty input\n")
        return
    cols = list(rows[0].keys())

    kept, dropped = [], []
    for r in rows:
        g = gapval(r)
        if (r.get("mmsi") or "").strip() and g is not None and g > CAP_MIN:
            dropped.append(r)
        else:
            kept.append(r)

    w = csv.DictWriter(sys.stdout, fieldnames=cols)
    w.writeheader()
    for r in kept:
        w.writerow(r)

    for r in sorted(dropped, key=lambda r: r.get("pass_time", "")):
        sys.stderr.write(f"DROP {r.get('pass_time')} {r.get('name')} "
                         f"{r.get('direction')} gap={r.get('gap_min')}\n")
    live_in = sum(1 for r in rows if (r.get("mmsi") or "").strip())
    live_out = sum(1 for r in kept if (r.get("mmsi") or "").strip())
    hist = sum(1 for r in kept if not (r.get("mmsi") or "").strip())
    sys.stderr.write(f"# live {live_in} -> {live_out} ({len(dropped)} dropped); "
                     f"{hist} historical preserved\n")


if __name__ == "__main__":
    main()
