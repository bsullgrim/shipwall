#!/usr/bin/env python3
"""
clean_register.py  --  Remove restart-duplicate rows from a register.csv.

Before restart-dedup was added, the service re-logged every vessel's first_seen
row on each restart, so the log filled with redundant repeats. This rebuilds the
log as the FIXED logger would have written it: keep each row only when it
represents a genuine change for that MMSI -- the first time it appears, and any
time its (name, operator) signature actually changes -- dropping the repeats.

Output is the same schema the service writes, in chronological order, so it's a
drop-in replacement for register.csv. The operator/code columns are RE-RESOLVED
against the current operators.py rules + MMSI table, so the cleaned file
reflects today's identification (not whatever was logged at the time).

    python3 clean_register.py register.csv > register_clean.csv
"""

import csv
import os
import sys

# Re-resolve operators against current rules if operators.py is importable.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import operators as _ops
except Exception:
    _ops = None

OUT_COLS = ["timestamp", "event", "mmsi", "name", "operator", "code", "flag",
            "type", "type_code", "length", "beam", "lat", "lon", "dest"]

# Bounding box (matches register_service.BOUNDING_BOX): Cape Vincent -> the
# Snell/Beauharnois lock. Rows with a logged position outside this box are
# pruned -- they predate the box change (when Montreal was still included) and
# no longer belong. Legitimate through-traffic re-logs with an in-box position.
BOX_LAT_MIN, BOX_LAT_MAX = 44.10, 45.3237
BOX_LON_MIN, BOX_LON_MAX = -76.40, -73.9132


def in_box(lat, lon):
    """True if the position is inside the box, or unknown (keep unknowns)."""
    if lat is None or lon is None:
        return True
    return (BOX_LAT_MIN <= lat <= BOX_LAT_MAX
            and BOX_LON_MIN <= lon <= BOX_LON_MAX)


def fnum(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def is_int(s):
    try:
        int(s); return True
    except (ValueError, TypeError):
        return False


def normalize(cells):
    """Map a raw row (old or new column order) to the service's field set."""
    if len(cells) < 5:
        return None
    ts, event, mmsi, name = cells[0], cells[1], cells[2], cells[3]
    if not is_int(mmsi):
        return None
    out = {c: "" for c in OUT_COLS}
    out.update({"timestamp": ts, "event": event, "mmsi": mmsi,
                "name": name.strip()})
    rest = cells[4:]
    if rest and rest[0] and not is_int(rest[0]):     # new schema
        for k, v in zip(["operator", "code", "flag", "type", "type_code",
                         "length", "beam", "lat", "lon", "dest"], rest):
            out[k] = v.strip()
    else:                                            # old schema, salvage type
        if len(rest) >= 2:
            out["type"] = rest[0].strip()
            out["type_code"] = rest[1].strip()
    return out


def main(path):
    # Read all rows, normalize, sort chronologically.
    rows = []
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader, None)
        for raw in reader:
            r = normalize(raw)
            if r:
                rows.append(r)
    rows.sort(key=lambda r: r["timestamp"])

    # Geographic prune (matches the current bounding box). Determine, from the
    # FULL set of rows, which MMSIs ever had any position and which had an
    # in-box position. Then:
    #   - drop rows whose position is outside the box;
    #   - drop a vessel entirely if it had position data but none of it in-box
    #     (everything we saw of it was Montreal-area / beyond the lock);
    #   - keep vessels that never had any position (pure identity rows).
    had_any_pos, had_inbox_pos = set(), set()
    for r in rows:
        lat, lon = fnum(r["lat"]), fnum(r["lon"])
        if lat is not None and lon is not None:
            had_any_pos.add(r["mmsi"])
            if in_box(lat, lon):
                had_inbox_pos.add(r["mmsi"])

    def keep(r):
        m = r["mmsi"]
        lat, lon = fnum(r["lat"]), fnum(r["lon"])
        # drop out-of-box positioned rows
        if lat is not None and lon is not None and not in_box(lat, lon):
            return False
        # drop position-less rows belonging to a vessel only ever seen out-of-box
        if m in had_any_pos and m not in had_inbox_pos:
            return False
        return True

    rows = [r for r in rows if keep(r)]

    # Replay as the fixed logger would. Crucially, the fixed logger's dedup
    # memory persists across restarts: once a ship is known by name, a later
    # nameless position report does NOT downgrade or re-log it. So we track the
    # best-known (name, operator) per MMSI and only emit a row when that
    # best-known signature genuinely advances (new ship, name resolves, or
    # name/operator actually changes) -- not when a nameless ping arrives for a
    # ship we already know.
    def good(v):
        return v and v not in ("UNKNOWN", "???", "")

    known = {}                    # mmsi -> last-emitted (name, operator)
    out = []
    for r in rows:
        m = r["mmsi"]
        name, op = r["name"], r["operator"]
        prev = known.get(m)
        if prev is None:
            known[m] = (name, op)
            out.append(r)
            continue
        pname, pop = prev
        # A nameless sighting of an already-named ship is not a change.
        if not name and pname:
            continue
        # UNKNOWN/blank operator doesn't override a resolved one.
        eff_op = op if good(op) else (pop if good(pop) else op)
        eff_name = name if name else pname
        sig = (eff_name, eff_op)
        if sig == prev:
            continue              # no real advance -> drop
        known[m] = sig
        out.append(r)

    # Re-resolve operator/code against the CURRENT rules + MMSI table, so the
    # cleaned file reflects today's identification rather than what was logged
    # at the time (e.g. a name rule added since then now resolves the operator).
    if _ops is not None:
        for r in out:
            name = r.get("name", "")
            if not name:
                continue            # ghosts stay UNKNOWN/blank
            try:
                op = _ops.operator_for(int(r["mmsi"]), name)
            except (ValueError, TypeError):
                continue
            r["operator"] = op
            r["code"] = _ops.operator_code(op)

    w = csv.DictWriter(sys.stdout, fieldnames=OUT_COLS)
    w.writeheader()
    for r in out:
        w.writerow({c: r.get(c, "") for c in OUT_COLS})


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 clean_register.py register.csv > register_clean.csv",
              file=sys.stderr)
        sys.exit(1)
    main(sys.argv[1])