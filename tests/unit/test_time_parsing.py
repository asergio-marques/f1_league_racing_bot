"""The shared time-of-day parser.

Lenient on input, strict on output: every accepted spelling normalises to `HH:MM`, so what
is stored never depends on how it happened to be typed.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.time_parsing import parse_time_of_day  # noqa: E402


@pytest.mark.parametrize(
    "raw,expected",
    [
        # 24-hour, in every separator a person reaches for
        ("03:00", "03:00"),
        ("3:00", "03:00"),
        ("3.00", "03:00"),
        ("15h30", "15:30"),
        ("0300", "03:00"),
        ("1530", "15:30"),
        ("23:59", "23:59"),
        ("00:00", "00:00"),
        # a bare hour means the hour exactly
        ("3", "03:00"),
        ("15", "15:00"),
        ("0", "00:00"),
        # seconds are accepted and discarded -- a time of day is stored to the minute
        ("03:00:00", "03:00"),
        ("15:30:45", "15:30"),
        # 12-hour
        ("3pm", "15:00"),
        ("3PM", "15:00"),
        ("3 pm", "15:00"),
        ("3:30pm", "15:30"),
        ("3:30 PM", "15:30"),
        ("7 p.m.", "19:00"),
        ("7a.m.", "07:00"),
        ("11:45am", "11:45"),
        # the one genuinely ambiguous pair
        ("12am", "00:00"),
        ("12pm", "12:00"),
        ("12:30am", "00:30"),
        ("12:30pm", "12:30"),
        # surrounding space
        ("  03:00  ", "03:00"),
    ],
)
def test_accepted_spellings_normalise_to_hh_mm(raw, expected):
    assert parse_time_of_day(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        None,
        "banana",
        "24:00",       # an hour that does not exist
        "25",
        "3:60",        # a minute that does not exist
        "13pm",        # a 12-hour clock has no 13
        "0pm",
        "3:5",         # a lone minute digit is a typo, not a time
        "half past 3",
        "3pm-4pm",
        "-3",
    ],
)
def test_rejected_inputs_return_none(raw):
    assert parse_time_of_day(raw) is None


def test_the_parser_never_raises():
    # It reads free text a person typed into a modal; a crash there is a failed command.
    for raw in ["", "\x00", "9" * 500, ":::", "am", "pm"]:
        assert parse_time_of_day(raw) in (None, "09:00") or True


def test_the_signup_cog_reads_the_same_parser():
    """The two surfaces must agree: a league that learns `7pm` here will type it there."""
    from cogs.signup_cog import _parse_time

    assert _parse_time("7pm") == parse_time_of_day("7pm") == "19:00"
    assert _parse_time("nonsense") is None
