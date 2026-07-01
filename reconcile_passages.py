#!/usr/bin/env python3
"""Rebuild the LIVE portion of passages.csv from register.csv using the fixed
confirmed-crossing detector, so the phantom reversals and multi-day stitches the
OLD logic wrote get dropped to match what the deployed detector now produces.
Historical spotting-log rows (blank mmsi, noon pass_time, no seen_before/after)
are preserved untouched.

DRY RUN by design: the rebuilt CSV goes to stdout, the ADD/DROP diff to stderr.
Inspect the diff first; only when it looks right do you redirect stdout to disk.

  cd ~/shipwall
  sudo systemctl stop shipwall.service          # no concurrent appends
  cp passages.csv passages.csv.bak              # back up
  python3 reconcile_passages.py passages.csv register.csv > passages.clean.csv
  #   ^ read the DROP/ADD lines on stderr; sanity-check nothing real is lost
  mv passages.clean.csv passages.csv            # commit only if happy
  sudo systemctl start shipwall.service         # re-primes the dedup guard

Caveat worth reading the diff for: register.csv is sparser than what live
detection saw in real time, so a genuine crossing whose register fixes don't
straddle HOME can show up as a DROP with no matching ADD. If a DROP looks real,
keep that row by hand rather than trusting the rebuild blindly.
"""
import csv
import sys
import datetime as dt

WINDOW = 6 * 3600          # match old/new rows for the same physical crossing
COLS = ["pass_time", "mmsi", "name", "operator", "direction",
        "seen_before", "seen_after", "gap_min"]


def epoch(s):
    try:
        return dt.datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def same_crossing(a, b):
    """Two rows describe the same physical crossing: same ship + direction, and
    interpolated pass times within WINDOW (they can differ when one detection
    bridged a gap and another saw adjacent fixes)."""
    return (a["mmsi"] == b["mmsi"] and a["direction"] == b["direction"]
            and abs((epoch(a["pass_time"]) or 0) - (epoch(b["pass_time"]) or 0)) <= WINDOW)


def main():
    import register_service as rs        # deferred: importing loads .env / key
    import crossing

    pass_csv, reg_csv = sys.argv[1], sys.argv[2]

    # current passages: split history (no mmsi) from live (has mmsi)
    cur = list(csv.DictReader(open(pass_csv, newline="")))
    hist = [r for r in cur if not (r.get("mmsi") or "").strip()]
    old_live = [r for r in cur if (r.get("mmsi") or "").strip()]

    # per-vessel (t, progress, name, op) from the register
    by = {}
    for row in csv.DictReader(open(reg_csv, newline="")):
        m = (row.get("mmsi") or "").strip()
        if not m.isdigit():
            continue
        try:
            lat = float(row["lat"]); lon = float(row["lon"])
        except (ValueError, TypeError, KeyError):
            continue
        t = epoch(row.get("timestamp", ""))
        p = rs.river_progress(lat, lon)
        if t is None or p is None:
            continue
        by.setdefault(int(m), []).append(
            (t, p, (row.get("name") or "").strip(),
             (row.get("operator") or "").strip()))

    HP = rs.HOME_PROGRESS
    margin = getattr(rs, "CROSSING_MARGIN", crossing.DEFAULT_MARGIN)
    max_gap = getattr(rs, "MAX_CROSSING_GAP_S", crossing.DEFAULT_MAX_GAP_S)

    new_live = []
    for m, pts in by.items():
        pts.sort(key=lambda r: r[0])
        name = op = ""
        for (_, _, nm, o) in pts:
            if nm:
                name = nm
            if o and o != "UNKNOWN":
                op = o
        points = [(t, p) for (t, p, _, _) in pts]
        for c in crossing.find_confirmed_crossings(points, HP, margin, max_gap):
            t_pass = rs._passage_pass_time(c["t_before"], c["p_before"],
                                           c["t_after"], c["p_after"])
            new_live.append({
                "pass_time": dt.datetime.fromtimestamp(t_pass).isoformat(timespec="seconds"),
                "mmsi": str(m),
                "name": name or f"MMSI {m}",
                "operator": op or "UNKNOWN",
                "direction": c["direction"],
                "seen_before": dt.datetime.fromtimestamp(c["t_before"]).isoformat(timespec="seconds"),
                "seen_after": dt.datetime.fromtimestamp(c["t_after"]).isoformat(timespec="seconds"),
                "gap_min": round((c["t_after"] - c["t_before"]) / 60.0, 1),
            })

    dropped = [o for o in old_live if not any(same_crossing(o, n) for n in new_live)]
    added = [n for n in new_live if not any(same_crossing(n, o) for o in old_live)]

    out = hist + new_live
    out.sort(key=lambda r: epoch(r["pass_time"]) or 0)
    w = csv.DictWriter(sys.stdout, fieldnames=COLS)
    w.writeheader()
    for r in out:
        w.writerow({k: r.get(k, "") for k in COLS})

    for r in sorted(dropped, key=lambda r: epoch(r["pass_time"]) or 0):
        sys.stderr.write(f"DROP {r['pass_time']} {r.get('name')} "
                         f"{r['direction']} gap={r.get('gap_min')}\n")
    for r in sorted(added, key=lambda r: epoch(r["pass_time"]) or 0):
        sys.stderr.write(f"ADD  {r['pass_time']} {r.get('name')} "
                         f"{r['direction']} gap={r.get('gap_min')}\n")
    sys.stderr.write(f"# live {len(old_live)} -> {len(new_live)} "
                     f"({len(dropped)} dropped, {len(added)} added); "
                     f"{len(hist)} historical rows preserved\n")


if __name__ == "__main__":
    main()
