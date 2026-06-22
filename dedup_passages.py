#!/usr/bin/env python3
"""Collapse duplicate passages: same mmsi+direction within WINDOW seconds.
Keeps the row with the smaller gap_min (real adjacent fixes beat a bridged gap).
Pre-2026 rows (no seen_before/after, noon pass_time) are spotting-log history
and are left untouched."""
import csv, sys, datetime as dt

WINDOW = 6 * 3600
COLS = ["pass_time","mmsi","name","operator","direction",
        "seen_before","seen_after","gap_min"]

def epoch(s):
    try: return dt.datetime.fromisoformat(s).timestamp()
    except Exception: return None

def gap(r):
    try: return float(r.get("gap_min") or "inf")
    except ValueError: return float("inf")

rows = list(csv.DictReader(open(sys.argv[1], newline="")))
# only de-dupe live rows (those carrying an mmsi); leave historical rows alone
hist = [r for r in rows if not (r.get("mmsi") or "").strip()]
live = [r for r in rows if (r.get("mmsi") or "").strip()]

kept, dropped = [], []
# group by (mmsi, direction), walk by time, collapse within-window clusters
live.sort(key=lambda r: (r["mmsi"], r["direction"], epoch(r["pass_time"]) or 0))
i = 0
while i < len(live):
    j = i + 1
    cluster = [live[i]]
    while j < len(live) and live[j]["mmsi"] == live[i]["mmsi"] \
            and live[j]["direction"] == live[i]["direction"] \
            and (epoch(live[j]["pass_time"]) - epoch(cluster[-1]["pass_time"])) <= WINDOW:
        cluster.append(live[j]); j += 1
    best = min(cluster, key=gap)
    kept.append(best)
    dropped.extend(r for r in cluster if r is not best)
    i = j

out = hist + kept
out.sort(key=lambda r: epoch(r["pass_time"]) or 0)
w = csv.DictWriter(sys.stdout, fieldnames=COLS)
w.writeheader()
for r in out:
    w.writerow({k: r.get(k, "") for k in COLS})

for r in dropped:
    sys.stderr.write(f"DROP {r['pass_time']} {r['name']} {r['direction']} "
                     f"gap={r.get('gap_min')}\n")