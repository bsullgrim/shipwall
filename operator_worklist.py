#!/usr/bin/env python3
"""
operator_worklist.py  --  Help assign operators to vessels seen but unidentified.

AIS never carries the operating company, so operator is resolved by name-prefix
rules (operators.py) or an explicit MMSI -> operator table
(mmsi_to_operator.json). This tool surfaces the vessels that have a real name
but still resolve to UNKNOWN, so you can map the ones that matter in a few
minutes instead of hunting through the logs.

Sources it reads (whichever exist):
  - mmsi_database.json   (the persistent identity DB the service builds)
  - register.csv         (the register log; --register to point elsewhere)

Usage:
  python3 operator_worklist.py                 # list unidentified named vessels
  python3 operator_worklist.py --assign MMSI OPERATOR
                                               # write one mapping into the table
  python3 operator_worklist.py --register register_clean.csv

Operators with sprites (valid assignment targets):
  ALGOMA CSL FEDNAV ASC INTERLAKE LOWERLAKES DESGAGNES
  ANDRIE CLIFFS G3 GLF HOLCIM MCASPHALT NACC VTB
A vessel whose operator you don't have a sprite for is best left UNKNOWN (it
will show the ghost sprite) -- only assign operators that exist in the set.
"""

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MMSI_DB = os.path.join(HERE, "mmsi_database.json")
MMSI_TABLE = os.path.join(HERE, "mmsi_to_operator.json")

VALID_OPERATORS = {
    "ALGOMA", "CSL", "FEDNAV", "ASC", "INTERLAKE", "LOWERLAKES", "DESGAGNES",
    "ANDRIE", "CLIFFS", "G3", "GLF", "HOLCIM", "MCASPHALT", "NACC", "VTB",
}

try:
    import operators as ops
except Exception:
    ops = None


def load_known_vessels(register_path):
    """Collect {mmsi: name} for every named vessel from the DB and register."""
    named = {}
    # MMSI identity database
    if os.path.exists(MMSI_DB):
        try:
            for k, rec in json.load(open(MMSI_DB)).items():
                nm = (rec.get("name") or "").strip()
                if nm:
                    named[k] = nm
        except Exception:
            pass
    # register CSV
    if register_path and os.path.exists(register_path):
        try:
            for row in csv.DictReader(open(register_path, newline="")):
                m, nm = row.get("mmsi", ""), (row.get("name") or "").strip()
                if m and nm:
                    named[m] = nm
        except Exception:
            pass
    return named


def current_operator(mmsi, name):
    if ops:
        return ops.operator_for(int(mmsi), name)
    return "UNKNOWN"


def load_table():
    if os.path.exists(MMSI_TABLE):
        try:
            return json.load(open(MMSI_TABLE))
        except Exception:
            pass
    return {}


def save_table(table):
    with open(MMSI_TABLE, "w") as f:
        json.dump(table, f, indent=2, sort_keys=True)


def cmd_list(register_path):
    named = load_known_vessels(register_path)
    unresolved = []
    for mmsi, name in sorted(named.items(), key=lambda kv: kv[1]):
        if current_operator(mmsi, name) == "UNKNOWN":
            unresolved.append((mmsi, name))
    if not unresolved:
        print("No named-but-unidentified vessels. Everything with a name resolves.")
        return
    print(f"{len(unresolved)} named vessels still resolve to UNKNOWN:\n")
    print(f"  {'MMSI':<11} {'NAME':<26} suggestion")
    print(f"  {'-'*11} {'-'*26} {'-'*30}")
    for mmsi, name in unresolved:
        hint = ""
        flag = mmsi[:3] if len(mmsi) >= 3 else ""
        # gentle, honest hints -- no guessing operators we can't be sure of
        if flag in ("636", "538", "229", "259"):
            hint = "(foreign-flag salty; sprite only if a known fleet)"
        elif "DESGAGNES" in name.upper():
            hint = "-> DESGAGNES"
        print(f"  {mmsi:<11} {name[:26]:<26} {hint}")
    print("\nAssign one with:")
    print("  python3 operator_worklist.py --assign <MMSI> <OPERATOR>")
    print("Valid operators:", " ".join(sorted(VALID_OPERATORS)))
    print("(Leave foreign/own-operator ships unmapped -- they show the ghost.)")


def cmd_assign(mmsi, operator):
    operator = operator.upper()
    if operator not in VALID_OPERATORS:
        print(f"'{operator}' is not a sprite-backed operator. Choose from:")
        print("  " + " ".join(sorted(VALID_OPERATORS)))
        sys.exit(1)
    if not mmsi.isdigit():
        print(f"MMSI must be numeric, got '{mmsi}'")
        sys.exit(1)
    table = load_table()
    table[mmsi] = operator
    save_table(table)
    print(f"Mapped MMSI {mmsi} -> {operator} in {os.path.basename(MMSI_TABLE)}")
    print("Restart the service to pick it up (it loads the table on startup).")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Assign operators to seen vessels.")
    ap.add_argument("--register", default=os.path.join(HERE, "register.csv"),
                    help="register CSV to read (default register.csv)")
    ap.add_argument("--assign", nargs=2, metavar=("MMSI", "OPERATOR"),
                    help="write one MMSI->operator mapping into the table")
    args = ap.parse_args()
    if args.assign:
        cmd_assign(args.assign[0], args.assign[1])
    else:
        cmd_list(args.register)
