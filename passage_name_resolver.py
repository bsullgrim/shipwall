"""
Read-time passage-name resolution for passage_stats.py
------------------------------------------------------
Mirror of the operator re-resolution you already do: an "MMSI <n>" name is a
placeholder, not a final answer. Resolve it against mmsi_db each time you read a
passage, so a ship that was unnamed at its crossing instant shows its real name
the moment its identity is known -- without ever rewriting passages.csv.

Wire-up: load the name map once at module import, then call resolve_name() on
each passage row right where you currently re-resolve the operator.
"""
import json
import os
import re

_MMSI_NAME_RE = re.compile(r"^MMSI\s+(\d+)$")
_MMSI_DB_PATH = os.environ.get("MMSI_DB", "").strip()


def _load_name_map(path):
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
    except Exception:
        pass
    return out


_NAME_BY_MMSI = _load_name_map(_MMSI_DB_PATH)


def resolve_name(row):
    """Return the best-known display name for a passage row.

    If the stored name is a bare 'MMSI <n>' placeholder and we now know that
    MMSI's real name, return the real name; otherwise return the stored name
    unchanged. Pure read-time -- does not mutate the row or the file."""
    name = (row.get("name") or "").strip()
    m = _MMSI_NAME_RE.match(name)
    if not m:
        return name
    return _NAME_BY_MMSI.get(m.group(1), name)
