#!/usr/bin/env python3
"""
operators.py  --  Identify the operator (and thus stack livery) for a vessel.

AIS gives us no livery, so we infer the operator two ways, in order:

  1. Explicit MMSI -> operator lookup (mmsi_to_operator.json), grown over time.
  2. Vessel-name prefix matching, which on the Great Lakes / St. Lawrence
     Seaway is remarkably reliable because fleets use consistent name stems
     (Algoma -> "ALGO", Canada Steamship Lines -> "CSL"/"BAIE", Fednav ->
     "FEDERAL", etc.).

The returned operator KEY must match a sprite key compiled into the ESP32
firmware (see sprites/). Unknown vessels return "UNKNOWN", and the service
logs their MMSI + name so you can classify them later.
"""

import json
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_MMSI_TABLE_PATH = os.path.join(_HERE, "mmsi_to_operator.json")

# Operator keys are short, uppercase, and MUST match firmware sprite keys.
# Name-prefix rules: (prefix, operator_key). Checked in order; first hit wins.
# Prefixes are matched case-insensitively against the start of the AIS name.
NAME_PREFIX_RULES = [
    ("CSL ",     "CSL"),       # Canada Steamship Lines
    ("BAIE ",    "CSL"),       # CSL's Trillium-class "Baie" ships
    ("THUNDER",  "CSL"),       # CSL Thunder Bay etc.
    ("ALGO",     "ALGOMA"),    # Algoma Central (Algoma*, Algo*)
    ("ALGOMA",   "ALGOMA"),
    ("FEDERAL",  "FEDNAV"),    # Fednav salties
    ("UMIAK",    "FEDNAV"),    # Fednav Arctic-class
    ("NUNAVIK",  "FEDNAV"),
    ("MISSISSAGI","LOWERLAKES"),# Lower Lakes Towing / Rand
    ("MANITOULIN","LOWERLAKES"),
    ("CUYAHOGA", "LOWERLAKES"),
    ("AMERICAN", "ASC"),       # American Steamship Company (US-flag)
    ("BURNS",    "ASC"),       # Burns Harbor
    ("INDIANA",  "ASC"),       # Indiana Harbor
    ("STEWART",  "INTERLAKE"), # Interlake Steamship
    ("MESABI",   "INTERLAKE"),
    ("PAUL",     "INTERLAKE"), # Paul R. Tregurtha
    ("HON ",     "OGLEBAY"),   # Honorable James L. Oberstar (Oglebay/KK)
    ("MANITOWOC","OGLEBAY"),
]


def _load_mmsi_table():
    try:
        with open(_MMSI_TABLE_PATH, "r") as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    table = {}
    for k, v in raw.items():
        if k.isdigit():            # skip _comment and any non-MMSI keys
            table[int(k)] = v
    return table


_MMSI_TABLE = _load_mmsi_table()


def operator_for(mmsi, name):
    """Return an operator key, or 'UNKNOWN'."""
    if mmsi in _MMSI_TABLE:
        return _MMSI_TABLE[mmsi]
    if name:
        upper = name.upper().strip()
        for prefix, key in NAME_PREFIX_RULES:
            if upper.startswith(prefix):
                return key
    return "UNKNOWN"


# --- Unknown-vessel logging ---------------------------------------------------
# The service calls log_unknown() so you can periodically review who showed up
# and add them to mmsi_to_operator.json (or a new prefix rule).

_UNKNOWN_LOG = os.path.join(_HERE, "unknown_vessels.json")


def log_unknown(mmsi, name):
    try:
        with open(_UNKNOWN_LOG, "r") as f:
            seen = json.load(f)
    except (FileNotFoundError, ValueError):
        seen = {}
    key = str(mmsi)
    if key not in seen:
        seen[key] = name or ""
        with open(_UNKNOWN_LOG, "w") as f:
            json.dump(seen, f, indent=2, sort_keys=True)
