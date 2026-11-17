"""Reading a time of day a person typed, in whatever shape they typed it.

One parser, because there were two: the signup module's availability slots and the daily
driver-portrait refresh both take a time of day from free text, and a league that learns
`7pm` works in one place will type it in the other.

Lenient on input and strict on output. Every accepted spelling normalises to `HH:MM`, which
is what is stored and what is shown back, so the storage format never depends on how the
value happened to be typed.
"""
from __future__ import annotations

import re

#: `15`, `15:30`, `15:30:45`, `1530`, each optionally followed by am/pm.
_TIME_RE = re.compile(
    r"""^\s*
    (?P<hour>\d{1,2})
    (?:
        [:.h]\s*(?P<minute>\d{2})(?:[:.]\d{2})?   # 15:30, 15.30, 15h30, 15:30:45
      | (?P<compact>\d{2})                        # 1530
    )?
    \s*(?P<meridiem>am|pm|a\.m\.|p\.m\.)?
    \s*$""",
    re.IGNORECASE | re.VERBOSE,
)


def parse_time_of_day(raw: str) -> str | None:
    """Normalise *raw* to ``HH:MM``, or None where it is not a time.

    Accepts, case-insensitively and with any surrounding space::

        3        3:00      3.00     3h00    0300     03:00:00
        3pm      3:30 pm   3:30PM   3 p.m.

    A bare hour means the hour exactly. Seconds are accepted and discarded, a time of day
    being stored to the minute. `12am` is midnight and `12pm` is noon, which is the one
    place a 12-hour clock is genuinely ambiguous to write and must not be to read.

    Total: any string in, a string or None out, and never an exception.
    """
    if not raw:
        return None
    match = _TIME_RE.match(str(raw))
    if match is None:
        return None

    hour = int(match.group("hour"))
    minute = int(match.group("minute") or match.group("compact") or 0)
    meridiem = (match.group("meridiem") or "").replace(".", "").lower()

    if minute > 59:
        return None

    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 23:
        return None

    return f"{hour:02d}:{minute:02d}"
