#!/usr/bin/env python3
"""
schedule.py  --  Sun-based brightness + Seaway-season logic for the Ship Wall.

No external dependencies and no extra hardware: sunrise/sunset are computed
from latitude/longitude with a standard solar-position approximation, so the
display dims at real dusk and brightens at real dawn, tracking the seasons.

The display location is the river reach being watched (Chippewa Bay -> Oak
Point), not the viewer's home, so dusk on the panel matches dusk on the water.
"""

import datetime
import math

# Center of the watched reach (American Narrows). Used for solar times.
DISPLAY_LAT = 44.50
DISPLAY_LON = -75.68

# Brightness targets (0-255, matching the ESP32 setBrightness8 range).
BRIGHT_DAY   = 150      # full daytime brightness (tune to taste / room)
BRIGHT_NIGHT = 28       # ~11% faint overnight glow
FADE_MINUTES = 30       # linear ramp across dawn/dusk instead of a hard step

# Seaway navigation season. The St. Lawrence Seaway closes roughly the last
# week of December to about the third week of March. Dates are approximate and
# announced yearly; adjust if you want exact official dates.
SEASON_CLOSE = (12, 26)   # (month, day) on/after which it's closed
SEASON_OPEN  = (3, 22)    # (month, day) on/after which it's open again


def _solar_event_minutes(date, lat, lon, sunrise=True):
    """
    Return minutes-from-local-midnight (in UTC offset terms) for sunrise or
    sunset on `date` at (lat, lon), using the standard NOAA-style approximation.
    Returns None for polar day/night (never an issue at this latitude).
    """
    # Day of year.
    n = date.timetuple().tm_yday
    # Approximate solar declination (radians).
    decl = math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (n - 81)))
    lat_r = math.radians(lat)
    # Hour angle at sunrise/sunset (sun at -0.833 deg for refraction + radius).
    cos_h = (math.sin(math.radians(-0.833)) - math.sin(lat_r) * math.sin(decl)) \
            / (math.cos(lat_r) * math.cos(decl))
    if cos_h > 1 or cos_h < -1:
        return None
    h = math.degrees(math.acos(cos_h))           # degrees
    # Equation of time (minutes), simple approximation.
    b = math.radians(360.0 / 365.0 * (n - 81))
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    # Solar noon in minutes UTC for this longitude.
    solar_noon = 720 - 4 * lon - eot
    if sunrise:
        return solar_noon - 4 * h
    return solar_noon + 4 * h


def target_brightness(now_utc=None):
    """
    Compute the desired panel brightness (0-255) for the current moment,
    ramping linearly across a FADE_MINUTES window at dawn and dusk.
    """
    if now_utc is None:
        now_utc = datetime.datetime.now(datetime.timezone.utc)
    date = now_utc.date()
    minutes_now = now_utc.hour * 60 + now_utc.minute + now_utc.second / 60.0

    sr = _solar_event_minutes(date, DISPLAY_LAT, DISPLAY_LON, sunrise=True)
    ss = _solar_event_minutes(date, DISPLAY_LAT, DISPLAY_LON, sunrise=False)
    if sr is None or ss is None:
        return BRIGHT_DAY

    def ramp(t, start, full_after, lo, hi):
        """Linear interpolate from lo at `start` to hi at `full_after`."""
        if t <= start:
            return lo
        if t >= full_after:
            return hi
        frac = (t - start) / (full_after - start)
        return lo + (hi - lo) * frac

    if minutes_now < sr:
        # Before dawn ramp.
        b = ramp(minutes_now, sr - FADE_MINUTES, sr, BRIGHT_NIGHT, BRIGHT_DAY)
    elif minutes_now < ss - FADE_MINUTES:
        # Full day.
        b = BRIGHT_DAY
    elif minutes_now < ss:
        # Dusk ramp down.
        b = ramp(minutes_now, ss - FADE_MINUTES, ss, BRIGHT_DAY, BRIGHT_NIGHT)
    else:
        b = BRIGHT_NIGHT
    return int(round(max(BRIGHT_NIGHT, min(BRIGHT_DAY, b))))


def seaway_closed(now=None):
    """True if today falls in the approximate Seaway winter closure."""
    if now is None:
        now = datetime.datetime.now()
    md = (now.month, now.day)
    # Closed from SEASON_CLOSE (Dec) through SEASON_OPEN (Mar), wrapping year-end.
    if md >= SEASON_CLOSE or md < SEASON_OPEN:
        return True
    return False


if __name__ == "__main__":
    # Quick self-check across a day and across seasons.
    import datetime as dt
    print("Brightness across a June day (UTC hours):")
    for hr in range(0, 24, 2):
        t = dt.datetime(2026, 6, 21, hr, 0, tzinfo=dt.timezone.utc)
        print(f"  {hr:02d}:00Z -> {target_brightness(t)}")
    print("\nSeaway status samples:")
    for m, d in [(1, 15), (3, 1), (3, 25), (7, 4), (12, 28)]:
        print(f"  {m:02d}/{d:02d}: {'CLOSED' if seaway_closed(dt.datetime(2026,m,d)) else 'open'}")
