#!/usr/bin/env python3
"""
heal_passage_names.py  --  retroactively name the "MMSI <digits>" passages.

A passage row freezes the vessel name at the interpolated crossing instant. If a
ship's static name broadcast (AIS Type 5) hadn't arrived when it crossed -- only
its position reports had -- the row is written as "MMSI 316023341" and never
re-resolved. But the name almost always shows up moments later and is recorded
in mmsi_db.json and/or register.csv. This script stitches that identity back in.

Resolution order for each "MMSI <n>" row:
    1. mmsi_db.json   (the live service's persistent identity store)  -- freshest
    2. register.csv   (any sighting of that MMSI that carried a name)

Operator is left alone: passage_stats.py already re-resolves it by name at read
time, so once the name is healed the operator follows automatically.

Usage (PowerShell):
    python heal_passage_names.py passages.csv --mmsi-db mmsi_db.json `
        --register register.csv -o passages_healed.csv

Run without -o to do a dry run (reports what *would* change, writes nothing).
"""
import argparse
import csv
import json
import os
import re
import sys

MMSI_NAME_RE = re.compile(r"^MMSI\s+(\d+)$")


def load_mmsi_db(path):
    """mmsi_db.json: { "<mmsi>": {"name": "...", ...}, ... } -> {mmsi: name}"""
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, rec in data.items():
                nm = (rec.get("name") or "").strip() if isinstance(rec, dict) else ""
                if nm:
                    out[str(k).strip()] = nm
    except Exception as e:
        print(f"[warn] could not read mmsi_db {path}: {e}", file=sys.stderr)
    return out


def load_register_names(path):
    """register.csv -> {mmsi: name}, last non-empty name seen wins (freshest)."""
    out = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                m = (row.get("mmsi") or "").strip()
                nm = (row.get("name") or "").strip()
                if m and nm:
                    out[m] = nm        # later rows overwrite -> freshest name
    except Exception as e:
        print(f"[warn] could not read register {path}: {e}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("passages", help="passages.csv to heal")
    ap.add_argument("--mmsi-db", default="", help="mmsi_db.json (primary source)")
    ap.add_argument("--register", default="", help="register.csv (fallback source)")
    ap.add_argument("-o", "--out", default="",
                    help="output path; omit for a dry run")
    args = ap.parse_args()

    db = load_mmsi_db(args.mmsi_db)
    reg = load_register_names(args.register)

    with open(args.passages, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)

    healed = 0
    unresolved = []
    for r in rows:
        m = MMSI_NAME_RE.match((r.get("name") or "").strip())
        if not m:
            continue
        mmsi = m.group(1)
        name = db.get(mmsi) or reg.get(mmsi)
        if name:
            src = "mmsi_db" if db.get(mmsi) else "register"
            print(f"  heal  {r['pass_time']}  MMSI {mmsi} -> {name}  ({src})")
            r["name"] = name
            healed += 1
        else:
            unresolved.append((r["pass_time"], mmsi))

    print(f"\nHealed {healed} row(s); {len(unresolved)} still unresolved.")
    for t, mmsi in unresolved:
        print(f"  unresolved  {t}  MMSI {mmsi}  (no name in db or register yet)")

    if not args.out:
        print("\nDry run -- no file written. Re-run with -o to save.")
        return

    # preserve CRLF to match the repo convention
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    with open(args.out, "rb") as f:
        data = f.read()
    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    with open(args.out, "wb") as f:
        f.write(data)
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
