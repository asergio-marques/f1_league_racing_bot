"""How a date is written on a graphic, in the format the league chose.

A graphic cannot carry a Discord timestamp, so it carries a written date in one zone for
every reader. Eleven formats are offered, six of them written out — a calendar is read
rather than parsed, and a league that wants it to look like a poster wants the weekday
and the month spelled.

Two of the written-out forms need what `strftime` cannot portably give:

  * an English ordinal — `1st`, `2nd`, `14th` — which has no directive at all;
  * a day with no leading zero, which is `%-d` on glibc and `%#d` on Windows, each
    raising `ValueError` on the other. The bot is developed on Windows and runs on
    Debian, and the suite must pass on both, so the pattern carries `{day}` and this
    module substitutes it.

`DATE_FORMATS` states a worked example beside every pattern, and the command offers
those examples as the choice names — so a manager picks by appearance. The examples are
therefore load-bearing text, and `test_every_stated_example_is_what_is_actually_drawn`
is what stops one going stale.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import DATE_FORMATS, TIME_FORMATS  # noqa: E402
from utils.date_formatting import (  # noqa: E402
    DEFAULT_DATE_FORMAT,
    format_date,
    format_date_and_time,
    format_time,
    ordinal,
)

#: A Sunday, matching the worked examples in DATE_FORMATS.
SUNDAY = datetime(2026, 6, 14, 15, 30)


# ── The ordinal ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "day, expected",
    [
        (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"),
        (10, "10th"),
        # The exceptions: eleven, twelve and thirteen take "th" despite ending 1, 2, 3.
        (11, "11th"), (12, "12th"), (13, "13th"),
        (21, "21st"), (22, "22nd"), (23, "23rd"),
        (30, "30th"), (31, "31st"),
    ],
)
def test_the_english_suffix(day, expected):
    assert ordinal(day) == expected


def test_every_day_of_a_month_gets_a_suffix():
    for day in range(1, 32):
        assert ordinal(day).endswith(("st", "nd", "rd", "th"))


# ── The formats ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("token", sorted(DATE_FORMATS))
def test_every_stated_example_is_what_is_actually_drawn(token):
    """The example is the text a manager picks from, so it cannot drift from the pattern."""
    _, example = DATE_FORMATS[token]

    assert format_date(SUNDAY, token) == example


def test_the_written_out_format_the_league_asked_for():
    """"Sunday 6th September 2026" — spelled out, ordinal, and no leading zero."""
    assert (
        format_date(datetime(2026, 9, 6), "DDDD_ORD_MONTH_YYYY")
        == "Sunday 6th September 2026"
    )


@pytest.mark.parametrize(
    "token", ["DDDD_DD_MONTH_YYYY", "DD_MONTH_YYYY", "MONTH_DD_YYYY"]
)
def test_a_single_digit_day_carries_no_leading_zero(token):
    """`%d` would render "06", which is wrong in a date written out in words."""
    rendered = format_date(datetime(2026, 9, 6), token)

    assert "6" in rendered
    assert "06" not in rendered


@pytest.mark.parametrize("token", ["DDDD_ORD_MONTH_YYYY", "DDDD_MONTH_ORD_YYYY"])
def test_an_ordinal_format_spells_the_suffix(token):
    assert "6th" in format_date(datetime(2026, 9, 6), token)


def test_no_pattern_uses_a_platform_specific_directive():
    """`%-d` raises on Windows and `%#d` on glibc; the suite runs on both."""
    for token, (pattern, _) in DATE_FORMATS.items():
        assert "%-" not in pattern, f"{token} uses a glibc-only directive"
        assert "%#" not in pattern, f"{token} uses a Windows-only directive"


def test_every_pattern_renders_on_this_host():
    """Whatever the host, no configured format may raise mid-render."""
    for token in DATE_FORMATS:
        assert format_date(SUNDAY, token)


# ── Falling back ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, "", "   ", "A_WITHDRAWN_TOKEN"])
def test_an_unusable_setting_falls_back_rather_than_raising(value):
    """The value comes from a stored column; a graphic must not die over one."""
    assert format_date(SUNDAY, value) == DATE_FORMATS[DEFAULT_DATE_FORMAT][1]


def test_the_default_carries_the_weekday():
    """FR-023: a season run on the same weekday makes the weekday what a driver reads for."""
    assert format_date(SUNDAY, None).startswith("Sun")


# ── The time, and the pair ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "token, expected", [("24H", "15:30"), ("12H", "03:30 PM")]
)
def test_the_clock(token, expected):
    assert format_time(SUNDAY, token) == expected


def test_an_unknown_clock_falls_back_to_24_hour():
    assert format_time(SUNDAY, "37H") == SUNDAY.strftime(TIME_FORMATS["24H"])


def test_the_pair_names_the_zone_it_is_drawn_in():
    """XIV.15: one zone for every reader, and the graphic says which."""
    date_text, time_text = format_date_and_time(
        SUNDAY, ZoneInfo("Europe/Lisbon"), "DDDD_ORD_MONTH_YYYY", "24H"
    )

    assert date_text == "Sunday 14th June 2026"
    assert time_text.startswith("16:30 "), time_text  # 15:30 UTC is 16:30 in Lisbon
    assert time_text.split(" ", 1)[1], "the zone abbreviation is missing"


def test_a_naive_moment_is_read_as_utc():
    """Round times are stored naive and mean UTC; reading them as local would shift them."""
    naive = format_date_and_time(SUNDAY, ZoneInfo("UTC"), "YYYY_MM_DD", "24H")
    aware = format_date_and_time(
        SUNDAY.replace(tzinfo=ZoneInfo("UTC")), ZoneInfo("UTC"), "YYYY_MM_DD", "24H"
    )

    assert naive == aware


def test_the_zone_actually_moves_the_date_where_it_should():
    """Late on one day in UTC is the next day further east — the date must follow."""
    late = datetime(2026, 6, 14, 23, 30)

    utc_date, _ = format_date_and_time(late, ZoneInfo("UTC"), "YYYY_MM_DD", "24H")
    tokyo_date, _ = format_date_and_time(late, ZoneInfo("Asia/Tokyo"), "YYYY_MM_DD", "24H")

    assert utc_date == "2026-06-14"
    assert tokyo_date == "2026-06-15"


# ── The two callers agree ─────────────────────────────────────────────────


def test_the_calendar_and_the_check_in_call_format_identically():
    """Both drew a date through a byte-identical copy of this logic before it was shared.

    A format added to the table had to be correct in two functions to be correct at all,
    which is one more place to drift than is useful.
    """
    from services.image_calendar_service import _format_moment as calendar_moment
    from services.image_rsvp_service import format_moment as rsvp_moment

    for token in DATE_FORMATS:
        assert calendar_moment(SUNDAY, token, "24H", "Europe/Lisbon") == rsvp_moment(
            SUNDAY, token, "24H", "Europe/Lisbon"
        )
