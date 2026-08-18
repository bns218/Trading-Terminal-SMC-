"""ICT kill-zone windows.

Kill zones are ICT concepts originally defined in New York / London local
time — NOT a fixed IST offset. A fixed-hour IST conversion (e.g. "NY kill
zone is always 19:00-21:30 IST") silently breaks twice a year around US/UK
daylight-saving transitions, since India does not observe DST and the US/UK
do. This module avoids that bug structurally: each window's start/end is
defined in ITS OWN native IANA timezone (`America/New_York` or
`Europe/London`), and membership is tested by converting each candle
timestamp INTO that zone (via zoneinfo, which applies DST correctly for that
date) rather than converting the window into a fixed IST clock time once.

The specific hour ranges below are ONE commonly-cited convention among
several — different ICT educators give slightly different boundaries (e.g.
some define the NY kill zone as 07:00-10:00, others as 08:30-10:00 or add a
separate "NY lunch" and "PM session" window). Treat these as a documented
starting point to calibrate, not an authoritative definition.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class KillZone:
    name: str
    tz: str  # IANA zone the start/end times below are defined in
    start: time
    end: time


KILL_ZONES: list[KillZone] = [
    KillZone("asian", "America/New_York", time(20, 0), time(0, 0)),
    KillZone("london_open", "America/New_York", time(2, 0), time(5, 0)),
    KillZone("new_york_am", "America/New_York", time(7, 0), time(10, 0)),
    KillZone("london_close", "America/New_York", time(10, 0), time(12, 0)),
]


def active_kill_zones(ts: datetime) -> list[str]:
    """Return the names of every kill zone active at timestamp `ts` (which
    must be timezone-aware — it is converted into each zone's own local time
    for the comparison, DST included)."""
    if ts.tzinfo is None:
        raise ValueError("active_kill_zones requires a timezone-aware datetime")
    active = []
    for kz in KILL_ZONES:
        local = ts.astimezone(ZoneInfo(kz.tz)).time()
        if kz.start <= kz.end:
            if kz.start <= local < kz.end:
                active.append(kz.name)
        else:  # window crosses midnight (e.g. the Asian session)
            if local >= kz.start or local < kz.end:
                active.append(kz.name)
    return active
