"""Rendering a date and a time for a graphic, in the league's chosen format.

One reader for ``DATE_FORMATS`` and ``TIME_FORMATS``. The calendar and the check-in call
each carried a byte-identical copy of this, which is one more place for the two to drift
apart than is useful — a format added to the table would otherwise have to be correct in
two functions to be correct at all.

Two tokens the pattern may carry are substituted before ``strftime`` is applied, because
``strftime`` cannot produce either portably:

``{ordinal}``
    The day of the month with its English suffix — ``1st``, ``2nd``, ``3rd``, ``14th``.
    There is no directive for this at all.

``{day}``
    The day of the month with no leading zero. ``%-d`` does this on glibc and ``%#d`` on
    Windows, and each raises ``ValueError`` on the other. The bot is developed on Windows
    and runs on Debian, and the suite must pass on both, so neither is used.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from models.image_constants import DATE_FORMATS, TIME_FORMATS

#: What an unset or unrecognised setting falls back to. The weekday-carrying format is the
#: default because a season run on the same weekday every second week makes the weekday
#: the part of a date a driver reads for (FR-023).
DEFAULT_DATE_FORMAT = "DDD_DD_MON_YYYY"
DEFAULT_TIME_FORMAT = "24H"


def ordinal(day: int) -> str:
    """``1`` → ``"1st"``. The English suffix, including the 11th–13th exceptions."""
    if 11 <= (day % 100) <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def format_date(moment: datetime, date_format: str | None) -> str:
    """The date of *moment*, written the way *date_format* names.

    An unknown or absent format falls back to the default rather than raising: the value
    comes from a stored configuration column, and a graphic that cannot be drawn because
    a league once held a since-renamed token would be the worse failure.
    """
    pattern = DATE_FORMATS.get(
        date_format or "", DATE_FORMATS[DEFAULT_DATE_FORMAT]
    )[0]
    resolved = pattern.replace("{ordinal}", ordinal(moment.day)).replace(
        "{day}", str(moment.day)
    )
    return moment.strftime(resolved)


def format_time(moment: datetime, time_format: str | None) -> str:
    """The time of *moment*, without the zone abbreviation."""
    pattern = TIME_FORMATS.get(time_format or "", TIME_FORMATS[DEFAULT_TIME_FORMAT])
    return moment.strftime(pattern)


def to_zone(moment: datetime, zone: ZoneInfo) -> datetime:
    """*moment* in *zone*, reading a naive value as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=ZoneInfo("UTC"))
    return moment.astimezone(zone)


def format_date_and_time(
    moment: datetime,
    zone: ZoneInfo,
    date_format: str | None,
    time_format: str | None,
) -> tuple[str, str]:
    """The date and time of *moment* in *zone*, as a graphic carries them.

    A graphic cannot carry a Discord timestamp, which each reader would see in their own
    zone, so it carries one zone for every reader and says which (Constitution XIV.15).
    The abbreviation is appended to the time where the zone answers one.
    """
    local = to_zone(moment, zone)
    date_text = format_date(local, date_format)
    time_text = format_time(local, time_format)
    abbreviation = local.strftime("%Z")
    if abbreviation:
        time_text = f"{time_text} {abbreviation}"
    return date_text, time_text
