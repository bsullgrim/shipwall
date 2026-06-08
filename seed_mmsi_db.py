#!/usr/bin/env python3
"""
seed_mmsi_db.py  --  Bootstrap the MMSI identity database from a register CSV.

The live service builds mmsi_database.json as static AIS messages arrive, but a
fresh database starts empty -- so vessels you've already logged would have to be
re-observed before they're "known on sight." This seeds the database from a
register you already have, so it starts populated with everything seen so far.

Each named vessel in the register is written to the DB with its name, type,
dimensions, flag, and an operator RE-RESOLVED through the current operators.py
rules (so it reflects today's identification, not whatever was logged). Vessels
with no name are skipped (nothing to identify). Existing DB entries are kept
unless the register has one for the same MMSI (freshest wins, like the service).

    python3 seed_mmsi_db.py register_clean.csv
    python3 seed_mmsi_db.py register.csv --db mmsi_database.json
"""

import argparse
import csv
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    import operators as ops
except Exception:
    ops = None


def fnum(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def main(register_path, db_path):
    if not os.path.exists(register_path):
        print(f"register not found: {register_path}", file=sys.stderr)
        sys.exit(1)

    # Load existing DB (so we merge, not clobber).
    db = {}
    if os.path.exists(db_path):
        try:
            db = json.load(open(db_path))
        except Exception:
            db = {}
    before = len(db)

    # Walk the register, keeping the most-resolved row per MMSI.
    rows = list(csv.DictReader(open(register_path, newline="")))
    added = updated = 0
    for r in rows:
        mmsi = (r.get("mmsi") or "").strip()
        name = (r.get("name") or "").strip()
        if not mmsi.isdigit() or not name:
            continue                    # need a real MMSI and a name to identify
        # Re-resolve operator through current rules (UNKNOWN stays UNKNOWN).
        if ops:
            op = ops.operator_for(int(mmsi), name)
            code = ops.operator_code(op)
        else:
            op, code = r.get("operator", "UNKNOWN"), r.get("code", "???")
        tc = r.get("type_code")
        rec = {
            "name": name,
            "operator": op,
            "code": code,
            "flag": (r.get("flag") or "").strip(),
            "type": (r.get("type") or "").strip(),
            "type_code": int(tc) if (tc and tc.lstrip("-").isdigit()) else None,
            "length": fnum(r.get("length")),
            "beam": fnum(r.get("beam")),
            "updated": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        if mmsi not in db:
            added += 1
        else:
            updated += 1
        db[mmsi] = rec

    # Write back (UTF-8, pretty-printed and sorted for easy reading).
    tmp = db_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, sort_keys=True)
    os.replace(tmp, db_path)

    named_unknown = sum(1 for v in db.values() if v["operator"] in ("UNKNOWN", ""))
    print(f"Seeded {db_path}: {before} -> {len(db)} vessels "
          f"({added} new, {updated} refreshed).")
    print(f"  {len(db) - named_unknown} have a resolved operator; "
          f"{named_unknown} are named but operator-UNKNOWN.")
    print("The service will keep growing this as new vessels resolve live.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Seed the MMSI DB from a register CSV.")
    ap.add_argument("register", help="register CSV to seed from (e.g. register_clean.csv)")
    ap.add_argument("--db", default=os.path.join(HERE, "mmsi_database.json"),
                    help="MMSI database JSON to write (default mmsi_database.json)")
    args = ap.parse_args()
    main(args.register, args.db)