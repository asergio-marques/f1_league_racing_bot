"""The arithmetic a time penalty is actually made of.

Issue #208. `penalty_service.py`'s validation and application already have cover; the five
helpers underneath — the ones that turn a driver's finishing time into a number, add the
penalty, and turn it back into a string — did not.

**This is where a penalty becomes a result.** A steward types "+5 seconds"; these functions are
what move the driver down the order. An error here does not raise, it produces a plausible
finishing time that is wrong, and the standings are computed from it without anything
re-checking.

**Parsing and formatting must round-trip.** They are separate functions with separate format
strings, and a disagreement between them would shift every penalised time by a constant nobody
would spot in one result. `test_a_time_survives_the_round_trip` drives both directions over
values either side of the minute and hour boundaries, which is where the two formats differ.

**The millisecond field is padded, not truncated silently.** `1:23.4` is four hundred
milliseconds, not four — the same rule the submission validators hold, and it has to agree here
or a penalty applied to a time typed with one decimal place would move it by nearly half a
second in the wrong direction.

**An unparseable time is returned unchanged rather than guessed at.** `_apply_time_penalty` is
explicit about it: a penalty that cannot be applied leaves the result alone, so a malformed cell
costs the league a penalty rather than a fabricated finishing time. That is the safe direction,
and `test_a_time_that_cannot_be_read_is_left_alone` pins it.

**Negative penalties are supported and are not clamped here.** An appeal can give time back,
and the docstring is explicit that guaranteeing a non-negative result is the *caller's* job —
so a floor added here would silently disagree with the validator that already does it.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.results.services.penalty_service import (  # noqa: E402
    _apply_time_penalty,
    _delta_to_ms,
    _ms_to_delta,
    _ms_to_time,
    _time_to_ms,
)


# ---------------------------------------------------------------------------
# Reading a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1:23.456", 83_456),
        ("0:00.001", 1),
        ("59:59.999", 3_599_999),
        ("1:00:00.000", 3_600_000),
        ("2:03:04.005", 7_384_005),
        ("  1:23.456  ", 83_456),
    ],
)
def test_a_finishing_time_is_read_as_milliseconds(raw, expected):
    """Both shapes the submission stores: minutes and seconds, and hours as well for a
    race long enough to need them."""
    assert _time_to_ms(raw) == expected


@pytest.mark.parametrize("raw", ["1:23.4", "1:23.45", "1:23.4567", "1:23"])
def test_a_time_outside_the_strict_form_reads_as_nothing(raw):
    """Every stored time has exactly three digits after its dot, being written by the results
    paste or by `_ms_to_time`. One that has not is refused rather than guessed (#362); padding
    it once moved nothing, but guessing is how a wrong number becomes a finishing position."""
    assert _time_to_ms(raw) is None


def test_the_penalty_reader_reads_a_time_under_a_minute():
    """`58.123` is a form the results paste accepts and stores, and the old reader, needing a
    colon, could not read it (#362)."""
    assert _time_to_ms("58.123") == 58_123


@pytest.mark.parametrize("raw", ["", "   ", "-", "N/A", "n/a", None])
def test_an_absent_time_reads_as_nothing(raw):
    """A driver who did not finish has no time, and `-` and `N/A` are both how a league
    writes that."""
    assert _time_to_ms(raw) is None


@pytest.mark.parametrize("raw", ["quick", "83456", "1:23:45:67.890", "abc.def"])
def test_an_unreadable_time_reads_as_nothing(raw):
    """Refused rather than guessed. A guess here becomes a finishing position."""
    assert _time_to_ms(raw) is None


# ---------------------------------------------------------------------------
# Writing a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ms,expected",
    [
        (83_456, "1:23.456"),
        (1, "0:00.001"),
        (3_599_999, "59:59.999"),
        (3_600_000, "1:00:00.000"),
        (7_384_005, "2:03:04.005"),
    ],
)
def test_a_time_is_written_back_in_the_shape_it_came_in(ms, expected):
    """Under an hour the hours field is omitted, which is what a league writes; above it
    the field appears. A time always carrying `0:` would not match any submission."""
    assert _ms_to_time(ms) == expected


def test_seconds_and_milliseconds_are_padded():
    """`1:3.45` could not be told from `1:30.45` by a reader or by the parser."""
    assert _ms_to_time(63_045) == "1:03.045"


@pytest.mark.parametrize(
    "raw", ["1:23.456", "0:00.001", "59:59.999", "1:00:00.000", "2:03:04.005"]
)
def test_a_time_survives_the_round_trip(raw):
    """Parsing and formatting are separate functions with separate format strings, and a
    disagreement would shift every penalised time by a constant nobody would spot in one
    result."""
    assert _ms_to_time(_time_to_ms(raw)) == raw


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("+5.000", 5_000),
        ("+0:05.000", 5_000),
        ("+1:23.456", 83_456),
        ("+1:00:00.000", 3_600_000),
    ],
)
def test_a_gap_is_read_as_milliseconds(raw, expected):
    """Three shapes a league writes a gap in, all meaning the same thing."""
    assert _delta_to_ms(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "5.000", "miles", "N/A"])
def test_a_gap_that_is_not_a_delta_reads_as_nothing(raw):
    """A bare time without the `+` is an absolute time, not a gap — reading it as one
    would put a driver a lap ahead of the leader."""
    assert _delta_to_ms(raw) is None


@pytest.mark.parametrize(
    "ms,expected",
    [
        (5_000, "+5.000"),
        (83_456, "+1:23.456"),
        (3_600_000, "+1:00:00.000"),
        (500, "+0.500"),
    ],
)
def test_a_gap_is_written_with_only_the_fields_it_needs(ms, expected):
    """A five-second gap written `+0:00:05.000` reads as a much bigger one at a glance,
    and the gap column is what a driver looks at first."""
    assert _ms_to_delta(ms) == expected


@pytest.mark.parametrize("raw", ["+5.000", "+1:23.456", "+1:00:00.000"])
def test_a_gap_survives_the_round_trip(raw):
    assert _ms_to_delta(_delta_to_ms(raw)) == raw


# ---------------------------------------------------------------------------
# Applying a penalty
# ---------------------------------------------------------------------------


def test_a_penalty_is_added_to_the_finishing_time():
    """The whole point: a steward types five seconds and the driver moves down the order."""
    assert _apply_time_penalty("1:23.456", 5) == "1:28.456"


def test_a_penalty_carries_across_the_minute():
    """The arithmetic is done in milliseconds rather than on the string, so a penalty that
    pushes a time past a minute rolls over rather than producing `1:63.456`."""
    assert _apply_time_penalty("1:57.000", 5) == "2:02.000"


def test_a_penalty_carries_across_the_hour():
    """And changes the shape of the string it comes back in."""
    assert _apply_time_penalty("59:58.000", 5) == "1:00:03.000"


def test_a_penalty_given_back_on_appeal_is_subtracted():
    """An appeal that succeeds reduces a penalty, so negative values are a real case."""
    assert _apply_time_penalty("1:28.456", -5) == "1:23.456"


def test_no_floor_is_applied_here():
    """The docstring is explicit that guaranteeing a non-negative result is the caller's
    job — `validate_penalty_input` does it. A floor added here would silently disagree
    with the validator and the two would refuse different things."""
    result = _apply_time_penalty("0:03.000", -5)

    assert result != "0:03.000"


def test_a_penalty_of_nothing_changes_nothing():
    assert _apply_time_penalty("1:23.456", 0) == "1:23.456"


@pytest.mark.parametrize("raw", ["", "-", "N/A", "DNF", "quick"])
def test_a_time_that_cannot_be_read_is_left_alone(raw):
    """A malformed cell costs the league a penalty rather than a fabricated finishing
    time, which is the safe direction — the wrong one produces a plausible result nobody
    would question."""
    assert _apply_time_penalty(raw, 5) == raw


def test_a_penalised_time_stays_readable_by_the_parser():
    """The result is written back into the submission and read again by the next penalty,
    so it has to be in a shape this same module can parse."""
    once = _apply_time_penalty("1:23.456", 5)
    twice = _apply_time_penalty(once, 5)

    assert twice == "1:33.456"
