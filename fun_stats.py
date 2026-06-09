#!/usr/bin/env python3
"""
fun_stats.py  --  Mine the ship-spotting log for a "Hall of Fame & Oddities"
companion to the passage leaderboards.

Reads the multi-year Excel log and writes fun_stats.json, which passage_stats.py
serves on its "Hall of Fame" tab:
  - exotics:    notable sightings flagged by comment keyword (tall ships,
                schooners, yachts, coast guard, "very cool", etc.)
  - milestones: personal/sentimental comments (kids' first ships, etc.)
  - rarities:   ships seen exactly once across the whole log
  - busiest:    the highest ship-count days ever
  - lifers:     count of brand-new (never-seen-before) ships added each year
  - diversity:  distinct ships / fleets totals

    python3 fun_stats.py Ship_Log_Current.xlsx          # -> fun_stats.json
    python3 fun_stats.py Ship_Log_Current.xlsx -o out.json
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime

import openpyxl


def find_cols(ws):
    for r in range(1, 5):
        vals = [str(ws.cell(row=r, column=c).value) for c in range(1, 15)]
        if "Ship" in vals:
            return r, {v: i + 1 for i, v in enumerate(vals)}
    return 2, {}


def load_rows(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    years = sorted(s for s in wb.sheetnames if s.isdigit())
    rows = []
    for sn in years:
        ws = wb[sn]
        hdr, d = find_cols(ws)
        y = int(sn)
        for r in range(hdr + 1, ws.max_row + 1):
            g = lambda c: ws.cell(row=r, column=d[c]).value if c in d else None
            ship = g("Ship")
            if not ship or not str(ship).strip():
                continue
            name = " ".join(str(ship).strip().split())
            if name.lower() in ("ship", "none") or name.replace("-", "").isdigit():
                continue
            dt = g("Date")
            if isinstance(dt, datetime) and dt.year != y:
                try:
                    dt = dt.replace(year=y)
                except ValueError:
                    dt = datetime(y, 1, 1)
            rows.append({
                "year": y, "name": name,
                "fleet": str(g("Fleet") or "").strip(),
                "comment": str(g("Comments") or "").strip(),
                "date": dt if isinstance(dt, datetime) else None,
            })
    return rows, years


# Comment keywords -> a display category for the exotics showcase.
EXOTIC_RULES = [
    ("tall ship", "Tall ship"), ("schooner", "Sail"), ("barque", "Sail"),
    ("brig", "Sail"), ("sailing", "Sail"),
    ("yacht", "Yacht"),
    ("coast guard", "Coast Guard"), ("ccg", "Coast Guard"),
    ("navy", "Naval"), ("warship", "Naval"), ("submarine", "Naval"),
    ("icebreaker", "Icebreaker"),
    ("hovercraft", "Oddity"), ("very cool", "Noteworthy"),
    ("rare", "Noteworthy"), ("cruise", "Cruise"),
]

MILESTONE_KW = ["emma", "first boat", "first ship", "james",
                "favorite", "favourite", "best ever", "lifer"]


def categorize(comment):
    cl = comment.lower()
    for kw, cat in EXOTIC_RULES:
        if kw in cl:
            return cat
    return None


def build(rows, years):
    seen = Counter(r["name"] for r in rows)

    # Exotics: dedupe to first occurrence per ship, keep the comment.
    exotics, seen_exotic = [], set()
    for r in rows:
        if not r["comment"]:
            continue
        cat = categorize(r["comment"])
        if cat and r["name"] not in seen_exotic:
            seen_exotic.add(r["name"])
            exotics.append({"name": r["name"], "category": cat,
                            "comment": r["comment"], "year": r["year"]})
    cat_order = {c: i for i, c in enumerate(
        ["Tall ship", "Sail", "Naval", "Coast Guard", "Icebreaker",
         "Yacht", "Cruise", "Oddity", "Noteworthy"])}
    exotics.sort(key=lambda e: (cat_order.get(e["category"], 99), e["year"]))

    # Milestones: personal/sentimental comments.
    milestones = []
    for r in rows:
        cl = r["comment"].lower()
        if any(k in cl for k in MILESTONE_KW) and len(r["comment"]) < 100:
            iso = r["date"].date().isoformat() if r["date"] else str(r["year"])
            milestones.append({"name": r["name"], "comment": r["comment"],
                               "date": iso, "year": r["year"]})
    milestones.sort(key=lambda m: m["date"])

    # Rarities: seen exactly once, ever.
    rarities = sorted(n for n, c in seen.items() if c == 1)

    # Busiest days.
    byday = Counter()
    for r in rows:
        if r["date"]:
            byday[r["date"].date().isoformat()] += 1
    busiest = [{"date": d, "count": c} for d, c in byday.most_common(10)]

    # Lifers: first year each ship appears -> count per year.
    first_year = {}
    for r in sorted(rows, key=lambda r: r["year"]):
        first_year.setdefault(r["name"], r["year"])
    lifer_counts = Counter(first_year.values())
    lifers = [{"year": str(y), "new": lifer_counts.get(int(y), 0)} for y in years]

    fleets = Counter(r["fleet"] for r in rows
                     if r["fleet"] and r["fleet"].lower() != "none")

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "totals": {
            "sightings": len(rows),
            "distinct_ships": len(seen),
            "distinct_fleets": len(fleets),
            "one_timers": len(rarities),
            "span": f"{years[0]}\u2013{years[-1]}" if years else "",
        },
        "exotics": exotics,
        "milestones": milestones,
        "busiest_days": busiest,
        "lifers_per_year": lifers,
        "rarities_sample": rarities[:60],
        "rarities_total": len(rarities),
    }


def main(path, out):
    rows, years = load_rows(path)
    data = build(rows, years)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    t = data["totals"]
    print(f"wrote {out}")
    print(f"  {t['sightings']} sightings, {t['distinct_ships']} distinct ships, "
          f"{t['distinct_fleets']} fleets, {t['one_timers']} one-timers")
    print(f"  {len(data['exotics'])} exotics, {len(data['milestones'])} milestones, "
          f"{len(data['busiest_days'])} busiest-days")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Mine the ship log for fun stats.")
    ap.add_argument("log", help="the ship-log .xlsx")
    ap.add_argument("-o", "--output", default="fun_stats.json")
    args = ap.parse_args()
    main(args.log, args.output)
