 #!/usr/bin/env python3
"""
backfill_passages.py  --  Recover passages from logged register positions.

Live passage detection only fires while the service is running and PASSAGE_LOG
is set. If it was off, or the service restarted mid-transit, crossings of Danger
Island are missed. This scans a register CSV for any vessel whose consecutive
logged positions straddle the home point and emits the inferred passages -- the
same crossing test the live detector uses, applied after the fact.

It is necessarily less complete than live detection: it can only find ships that
were *logged* on both sides of the island, and the pass time is interpolated. It
won't invent passages for ships seen only on one side (the honest limit of the
register's coverage).

De-dupes against an existing passages.csv (by mmsi + interpolated day +
direction) so re-running is safe and won't double-log.

    python3 backfill_passages.py register.csv                  # -> passages.csv
    python3 backfill_passages.py register.csv -o passages.csv
    python3 backfill_passages.py register.csv --since 2026-06-08
"""

import argparse
import csv
import datetime as dt
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import register_service as rs   # reuse the EXACT geometry the live detector uses

PASS_COLS = ["pass_time", "mmsi", "name", "operator", "direction",
             "seen_before", "seen_after", "gap_min"]


def _epoch(iso):
    try:
        return dt.datetime.fromisoformat(iso).timestamp()
    except (ValueError, TypeError):
        return None


def load_sightings(path, since=None):
    """mmsi -> time-sorted list of (epoch, progress, name, operator). Only rows
    with a usable position contribute."""
    by_mmsi = defaultdict(list)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            m = (row.get("mmsi") or "").strip()
            if not m.isdigit():
                continue
            try:
                lat = float(row["lat"]); lon = float(row["lon"])
            except (ValueError, TypeError, KeyError):
                continue
            t = _epoch(row.get("timestamp", ""))
            if t is None:
                continue
            if since and row.get("timestamp", "") < since:
                continue
            p = rs.river_progress(lat, lon)
            if p is None:
                continue
            by_mmsi[int(m)].append(
                (t, p, (row.get("name") or "").strip(),
                 (row.get("operator") or "").strip()))
    for m in by_mmsi:
        by_mmsi[m].sort(key=lambda r: r[0])
    return by_mmsi


def find_crossings(by_mmsi):
    """Yield an inferred passage dict for each consecutive straddle of HOME."""
    HP = rs.HOME_PROGRESS
    out = []
    for mmsi, pts in by_mmsi.items():
        # carry the best-known name/operator forward (later rows resolve them)
        name = op = ""
        for (t, p, nm, o) in pts:
            if nm:
                name = nm
            if o and o != "UNKNOWN":
                op = o
        for (t0, p0, _, _), (t1, p1, _, _) in zip(pts, pts[1:]):
            direction = None
            if p0 < HP <= p1:
                direction = "downbound"
            elif p0 > HP >= p1:
                direction = "upbound"
            if not direction:
                continue
            span = p1 - p0
            frac = (HP - p0) / span if span else 0.5
            t_pass = t0 + frac * (t1 - t0)
            out.append({
                "pass_time": dt.datetime.fromtimestamp(t_pass).isoformat(timespec="seconds"),
                "mmsi": mmsi,
                "name": name or f"MMSI {mmsi}",
                "operator": op or "UNKNOWN",
                "direction": direction,
                "seen_before": dt.datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
                "seen_after": dt.datetime.fromtimestamp(t1).isoformat(timespec="seconds"),
                "gap_min": round((t1 - t0) / 60.0, 1),
            })
    out.sort(key=lambda r: r["pass_time"])
    return out


def existing_keys(path):
    """Set of (mmsi, day, direction) already present, so we don't double-log."""
    keys = set()
    if not os.path.exists(path):
        return keys
    try:
        with open(path, newline="") as f:
            for row in csv.DictReader(f):
                keys.add((row.get("mmsi", ""), row.get("pass_time", "")[:10],
                          row.get("direction", "")))
    except Exception:
        pass
    return keys


def main(register, out, since):
    if not os.path.exists(register):
        print(f"register not found: {register}", file=sys.stderr)
        sys.exit(1)
    by_mmsi = load_sightings(register, since)
    crossings = find_crossings(by_mmsi)
    have = existing_keys(out)
    new = [c for c in crossings
           if (str(c["mmsi"]), c["pass_time"][:10], c["direction"]) not in have]

    write_header = not os.path.exists(out)
    with open(out, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PASS_COLS)
        if write_header:
            w.writeheader()
        for c in new:
            w.writerow(c)

    print(f"scanned {len(by_mmsi)} vessels; found {len(crossings)} crossings, "
          f"{len(new)} new (rest already in {out}).")
    if new:
        print("recovered:")
        for c in new:
            print(f"  {c['pass_time'][:16]}  {c['name'][:22]:22} {c['direction']:9} "
                  f"(gap {c['gap_min']}m)")
    if crossings and not new:
        print("(all found crossings were already logged -- nothing to add.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Backfill passages from a register CSV.")
    ap.add_argument("register", help="register CSV with logged positions")
    ap.add_argument("-o", "--output", default="passages.csv",
                    help="passages CSV to append to (default passages.csv)")
    ap.add_argument("--since", default=None,
                    help="only consider sightings on/after this ISO date "
                         "(e.g. 2026-06-08), to recover a specific gap")
    args = ap.parse_args()
    main(args.register, args.output, args.since)
