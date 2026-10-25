"""A driver's seeding total, the sum of their signup lap times, as signup works it out.

**The seeding total is the number a whole season's divisions are built from.** A league asks
drivers for a time at several tracks, and the sum of those times is the driver's seed, so a sum
that silently came out wrong would sort the grid wrongly and nothing downstream could tell.
`total_lap_ms` (`leaguebot.signup.services.review_changes`) is what the approval writes to the
signup record, and its docstring records the decision these tests pin: a league that asked for no
times stores nothing rather than a zero, and **one time that cannot be read discards the whole
total** rather than seeding the driver on a partial sum, which would flatter them against drivers
whose times all parsed. `test_one_unreadable_time_discards_the_whole_total` holds that, and it is
the test most likely to be broken by someone making the reader more forgiving.

A lap time is read by the bot's one strict reader, `parse_time` (#362), whose forms
`tests/core/test_input_validator.py` pins; the cases here are the ones a stored lap time can take
that it does not already cover.
"""

from __future__ import annotations

import pytest

from leaguebot.core.utils.input_validator import parse_time
from leaguebot.signup.services.review_changes import total_lap_ms


# ---------------------------------------------------------------------------
# Reading one time
# ---------------------------------------------------------------------------


def test_a_lap_time_of_ten_minutes_or_more_is_read():
    """Two digits of minutes, as a long track's time trial gives."""
    assert parse_time("10:00.000") == 600_000


def test_a_lap_time_with_two_digits_after_the_dot_is_refused():
    """Signup accepts a lap time in one form only, three digits after the dot (#362), so one
    outside it is refused rather than padded into a seed."""
    assert parse_time("1:23.45") is None


@pytest.mark.parametrize("raw", ["nonsense", "a:bc.def"])
def test_a_time_that_is_not_one_is_refused_rather_than_guessed(raw):
    """The seed is a number a whole season's divisions are built from, so a wrong one is worse
    than no number at all."""
    assert parse_time(raw) is None


# ---------------------------------------------------------------------------
# The seeding total
# ---------------------------------------------------------------------------


def test_times_across_tracks_are_summed():
    assert total_lap_ms({"1": "1:00.000", "2": "0:30.500"}) == 90_500


def test_no_times_at_all_gives_no_total():
    """A league that asked for no lap times seeds nobody, and a zero would seed every driver
    identically at the top of the grid."""
    assert total_lap_ms({}) is None


def test_one_unreadable_time_discards_the_whole_total():
    """Not a partial sum. Seeding a driver on two of their three times would flatter them
    against drivers whose times all parsed: the most dangerous way this could fail, because the
    result still looks like a plausible seed."""
    assert total_lap_ms({"1": "1:00.000", "2": "rubbish"}) is None


def test_a_total_of_zero_is_treated_as_no_total():
    """All-zero times are not a real submission."""
    assert total_lap_ms({"1": "0:00.000"}) is None
