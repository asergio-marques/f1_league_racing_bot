"""The IANA zone list, read once rather than per lookup.

`available_timezones()` walks the whole `TZPATH` tree — some 600 entries under
`/usr/share/zoneinfo` — and CPython does not cache it. It was measured at **325 ms cold**
on the Raspberry Pi the bot runs on, which is a third of Discord's three-second budget for
a question that never touches the database.

Two callers make that matter. The time-zone autocomplete runs on *every keystroke*, and
the XML round importer validates a zone for every round in the payload. Both would pay the
walk repeatedly without this.

Lifted out of `cogs/image_cog.py`, where it began, so that a parser can validate a zone
without importing a cog — and with it, Discord — into the test import graph.
"""

from __future__ import annotations

from functools import lru_cache
from zoneinfo import available_timezones

__all__ = ["zone_names", "is_known_zone", "clear_zone_cache"]


@lru_cache(maxsize=1)
def zone_names() -> tuple[tuple[str, str], ...]:
    """Every IANA zone paired with its case-folded form, sorted, built once.

    The case-folded half is carried alongside so that an autocomplete matching on it does
    not lowercase ~600 strings per keystroke.

    Memoised for the life of the process, in the same spirit as the font index in
    `utils/font_metrics.py`. This is not the caching layer the constitution cautions about
    at "Performance & Storage Considerations" — that concerns league data at scale, whereas
    the zone list is a static enumeration shipped by the operating system.
    """
    return tuple(sorted((zone, zone.casefold()) for zone in available_timezones()))


def is_known_zone(name: str) -> bool:
    """Whether *name* is an IANA zone, matched exactly.

    Case-sensitive, because IANA names are: ``ZoneInfo("europe/lisbon")`` resolves on a
    Windows development machine and raises on the Raspberry Pi, whose filesystem is case
    sensitive. Accepting the folded form here would let a payload pass on one host and
    fail on the other.
    """
    return any(zone == name for zone, _folded in zone_names())


def clear_zone_cache() -> None:
    """Drop the memoised zone list. For tests, mirroring `font_metrics.clear_cache`."""
    zone_names.cache_clear()
