#!/usr/bin/env python3
"""
corridor.py  --  Great Lakes / Seaway sighting-location geography.

Emmett's ship log (unlike the home river log) tags many sightings with a
location OTHER than Cedar Ledge -- waypoints up the system where he saw a hull
in transit: the Soo Locks, the connecting rivers (St Marys / St Clair /
Detroit), open lakes, etc. This module turns those free-text location strings
into canonical waypoints with a geographic ordinal, so we can:

  - count sightings per waypoint along the downbound corridor, and
  - reconstruct a hull's "transit chain" (the ordered set of places it was
    logged) as it worked its way down toward the wall.

Cedar Ledge is the home station and the downbound end of the corridor; an
empty/blank location means Cedar Ledge.
"""

# Lakehead (upbound) -> Cedar Ledge (downbound, the home wall). The ordinal is
# a rough "distance down the system" used only to ORDER a hull's sightings into
# a readable chain and to lay out the corridor strip; it is not a real mileage.
CORRIDOR = [
    "Soo Locks",          # 0  -- St Marys Falls locks, top of the system
    "St Marys River",     # 1
    "Port Dolomite",      # 2  -- loading dock, Cedarville MI
    "De Tour",            # 3  -- DeTour Passage into Lake Huron
    "Lake Superior",      # 4
    "Lake Michigan",      # 5
    "Lake Huron",         # 6
    "Dunbar Park",        # 7  -- St Clair River viewpoint
    "Port Huron",         # 8  -- head of the St Clair River
    "St Clair River",     # 9
    "Lake St Clair",      # 10
    "Detroit River",      # 11
    "Lake Erie",          # 12
    "Lake Ontario",       # 13
    "S Dock Rd",          # 14 -- local home-area viewpoints
    "Rotary Park",        # 15
    "Traverse City",      # 16 -- Emmett's home port
    "Brockville Narrows", # 17 -- (downriver of home; see note below)
    "Cedar Ledge",        # 18 -- HOME STATION, downbound end
]
ORDINAL = {w: i for i, w in enumerate(CORRIDOR)}
HOME = "Cedar Ledge"

# NOTE: Brockville Narrows is actually east/downriver of Cedar Ledge on the St.
# Lawrence, not in the upper lakes. It's parked just before HOME here so it
# doesn't distort the upbound chain; with only a couple of sightings it's an
# edge case, not worth a branch in the ordering.

# Free-text -> canonical waypoint. Keyed on lowercased, whitespace-collapsed
# input. Anything not listed passes through cleaned-up (title-ish) and, if it's
# also not in ORDINAL, is treated as an unranked "other" location.
LOC_NORM = {
    "detour": "De Tour",
    "de tour": "De Tour",
    "state of michigan lake michigan": "Lake Michigan",
    "state of michigan lake huron": "Lake Huron",
    "lake st clair": "Lake St Clair",
    "lake st. clair": "Lake St Clair",
    "soo locks": "Soo Locks",
    "st marys river": "St Marys River",
    "st. marys river": "St Marys River",
    "st clair river": "St Clair River",
    "st. clair river": "St Clair River",
    "detroit river": "Detroit River",
    "port huron": "Port Huron",
    "lake huron": "Lake Huron",
    "lake superior": "Lake Superior",
    "lake erie": "Lake Erie",
    "lake ontario": "Lake Ontario",
    "port dolomite": "Port Dolomite",
    "brockville narrows": "Brockville Narrows",
    "dunbar park": "Dunbar Park",
    "s dock rd": "S Dock Rd",
    "rotary park": "Rotary Park",
    "traverse city": "Traverse City",
}


def norm_loc(raw):
    """Normalize a free-text location to a canonical waypoint.

    Blank/None -> HOME (Cedar Ledge), since that's the default station.
    """
    if raw is None or not str(raw).strip():
        return HOME
    key = " ".join(str(raw).split()).lower()
    if key in LOC_NORM:
        return LOC_NORM[key]
    return " ".join(str(raw).split())  # unknown: pass through cleaned


def ordinal(waypoint):
    """Corridor position for sorting; unknown locations sort just before HOME."""
    return ORDINAL.get(waypoint, ORDINAL[HOME] - 1)


def build_corridor(loc_counts):
    """[{waypoint, count, ordinal}] in downbound order, for the strip view.

    loc_counts: dict waypoint -> sighting count.
    """
    out = []
    for w, n in loc_counts.items():
        out.append({"waypoint": w, "count": n, "ordinal": ordinal(w)})
    out.sort(key=lambda d: d["ordinal"])
    return out


def build_transit_chains(ship_locs, min_locations=2):
    """[{ship, waypoints:[...], span}] for hulls logged at 2+ waypoints.

    ship_locs: dict ship_name -> set(waypoints). Each chain's waypoints are
    ordered downbound; 'span' is how many distinct waypoints (chain length).
    """
    chains = []
    for ship, locs in ship_locs.items():
        if len(locs) < min_locations:
            continue
        ordered = sorted(locs, key=ordinal)
        chains.append({
            "ship": ship,
            "waypoints": ordered,
            "span": len(ordered),
            "reaches_home": HOME in locs,
        })
    # Longest chains first; then ships that made it all the way to the wall.
    chains.sort(key=lambda c: (-c["span"], not c["reaches_home"], c["ship"]))
    return chains
