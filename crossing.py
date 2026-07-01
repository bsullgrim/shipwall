#!/usr/bin/env python3
"""
crossing.py  --  One shared HOME-crossing detector for every passage path.

The live detector, the in-service startup backfill, and the standalone
backfill_passages.py all decide "did this ship cross Danger Island, and which
way?" from a track of river-progress fixes. They had drifted into three copies
of the same pairwise test, each firing on a *single* straddling pair -- so one
jittery or duplicate AIS fix projected across HOME logged a phantom reversal
(VLIEBORG: down / down / up(14.8m) / down / up(307m) for a single downbound
transit), and two appearances weeks apart got stitched into one "crossing" by a
straight line (gaps of 13-22 days in the log).

This module fixes both, once, for all three:

  * CONFIRMATION (debounce): a crossing is emitted only when the vessel is
    established on the new side -- the fix that first goes past the margin must
    be followed by another fix still on that side. A lone fix that immediately
    reverts is jitter, not a transit.

  * GAP CAP: a crossing whose straddle interval (last old-side fix -> first
    new-side fix) exceeds `max_gap_s` is rejected. Because the anchor always
    advances to the *most recent* old-side fix, a large straddle means a
    coverage hole spanning HOME -- i.e. two unrelated appearances, not a passage.

Pure and stateless: pass in a time-sorted [(t, progress)] track and the home
geometry; get back the confirmed crossings. No import of register_service, so it
stays testable in isolation and can't create an import cycle.
"""

DEFAULT_MARGIN = 0.02
DEFAULT_MAX_GAP_S = 24 * 3600      # 24h: observed real crossings top out ~15.7h;
                                   # the shortest stitched garbage is ~4.8 days.


def side(p, home, margin):
    """Which bank of HOME a progress value is on: +1 downstream (toward the
    sea), -1 upstream (toward the lakes), 0 in the dead-band around HOME where
    we refuse to commit (jitter zone)."""
    if p > home + margin:
        return +1
    if p < home - margin:
        return -1
    return 0


def find_confirmed_crossings(points, home, margin=DEFAULT_MARGIN,
                             max_gap_s=DEFAULT_MAX_GAP_S):
    """Yield confirmed HOME crossings from a time-sorted [(t, progress)] track.

    Returns a list of dicts:
        {"direction": "downbound"|"upbound",
         "t_before", "p_before",   # last fix clearly on the OLD side (anchor)
         "t_after",  "p_after"}    # first fix clearly on the NEW side

    `t_before/after` are the actual straddle pair, so callers interpolate the
    pass time exactly as before; the *confirming* fix only gates the crossing,
    it doesn't move the timestamps.
    """
    crossings = []
    committed = None          # last confirmed side (+1 / -1)
    anchor = None             # (t, p) most recent fix on the committed side
    cand_side = None          # opposite side awaiting a confirming fix
    cand_first = None         # (t, p) first fix on the candidate side
    cand_prev = None          # (t, p) anchor at the moment the candidate opened

    for t, p in points:
        s = side(p, home, margin)
        if s == 0:
            continue                       # dead-band: hold state, ignore
        if committed is None:              # first decisive fix seeds the side
            committed, anchor = s, (t, p)
            continue
        if s == committed:                 # still on the committed side
            anchor = (t, p)                # advance the anchor
            cand_side = cand_first = cand_prev = None   # any candidate reverted
            continue
        # s is the opposite side
        if cand_side != s:                 # first fix on a new opposite side
            cand_side, cand_first, cand_prev = s, (t, p), anchor
        else:                              # second consecutive -> CONFIRMED
            t_prev, p_prev = cand_prev
            t_first, p_first = cand_first
            gap = t_first - t_prev
            if max_gap_s is None or gap <= max_gap_s:
                crossings.append({
                    "direction": "downbound" if s == +1 else "upbound",
                    "t_before": t_prev, "p_before": p_prev,
                    "t_after": t_first, "p_after": p_first,
                })
            committed, anchor = s, (t, p)  # commit the new side either way
            cand_side = cand_first = cand_prev = None
    return crossings


# --- self-test: proves the VLIEBORG phantoms die and real crossings survive ---
if __name__ == "__main__":
    HOME = 0.313
    DN, UP, MID = 0.60, 0.20, 0.31     # decisively down / up / in dead-band
    H = 3600

    def dirs(pts, **kw):
        return [c["direction"] for c in find_confirmed_crossings(pts, HOME, **kw)]

    # 1) clean downbound: upstream, then confirmed downstream -> one 'downbound'
    t = [(0, UP), (1*H, UP), (2*H, DN), (3*H, DN)]
    assert dirs(t) == ["downbound"], dirs(t)

    # 2) VLIEBORG 06-29: down, down, ONE up-jitter, down again -> nothing new
    t = [(0, DN), (1*H, DN), (2*H, UP), (3*H, DN), (4*H, DN)]
    assert dirs(t) == [], dirs(t)

    # 3) VLIEBORG 06-30: downstream, ONE up-jitter at the end (never confirmed)
    t = [(0, DN), (1*H, DN), (2*H, UP)]
    assert dirs(t) == [], dirs(t)

    # 4) genuine reversal (two confirmed transits) still logs both
    t = [(0, UP), (1*H, DN), (2*H, DN), (3*H, UP), (4*H, UP)]
    assert dirs(t) == ["downbound", "upbound"], dirs(t)

    # 5) dead-band dithering right at HOME never fabricates a crossing
    t = [(0, UP), (1*H, MID), (2*H, MID), (3*H, UP)]
    assert dirs(t) == [], dirs(t)

    # 6) gap cap: two appearances 5 days apart are NOT a transit (confirmed but
    #    over the cap) -> rejected; same track under a huge cap would log it
    t = [(0, UP), (1*H, UP), (120*H, DN), (121*H, DN)]
    assert dirs(t) == [], dirs(t)
    assert dirs(t, max_gap_s=200*H) == ["downbound"], dirs(t, max_gap_s=200*H)

    # 7) real 15.7h straddle (cf. VLIEBORG 06-13 upbound, gap 940m) survives the
    #    24h cap -- direction here is downbound by construction of the track
    t = [(0, UP), (15.7*H, DN), (17*H, DN)]
    assert dirs(t) == ["downbound"], dirs(t)

    print("crossing.py self-test: all 7 cases pass")
