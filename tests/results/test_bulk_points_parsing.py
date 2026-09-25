"""`_parse_bulk_lines` — reading a whole points table pasted in at once.

Issue #208. A league setting up its scoring types twenty positions into a modal in one go, and
this is what reads them. It is the only place a points table arrives as free text, so every
mistake a manager can make in a twenty-line paste has to be caught here and reported in terms
of the line it came from.

**Errors and valid rows are returned together, not one or the other.** A paste with one bad
line still carries nineteen good ones, and refusing the lot would have the manager retype
everything to fix one typo. The caller decides what to do with the pair; this function's job is
to be precise about which lines were which.

**Every error quotes the line it came from.** A manager staring at twenty lines and the words
"invalid position" has no way to find it. That is why each message carries the offending value
*and* the whole line, and why these tests assert on the content rather than merely counting.

**A duplicate position is not an error that discards the row** — the last value wins, and the
override is *noted*. A manager correcting themselves mid-paste means the second value, and
discarding both would be the one outcome neither reading supports. `test_a_repeated_position_
keeps_the_last_value_and_says_so` holds both halves.

**Floats are rejected rather than truncated.** `int("1.5")` raises, but `int(1.5)` would not —
the round-trip check (`pos_str != str(position)`) is what catches `"1.5"`, `" 1"`, `"+1"` and
`"01"`. Truncating would silently score position 1 from a line that said 1.5, and the manager
would never know which of their lines had been reinterpreted.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.results.cogs.results_cog import _parse_bulk_lines  # noqa: E402


# ---------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------


def test_a_clean_table_is_read_in_full():
    valid, errors = _parse_bulk_lines("1, 25\n2, 18\n3, 15")

    assert valid == [(1, 25), (2, 18), (3, 15)]
    assert errors == []


def test_whitespace_around_the_comma_is_forgiven():
    """A pasted table carries it, and refusing over spacing would refuse most real input."""
    valid, errors = _parse_bulk_lines("  1 ,   25  \n2,18")

    assert valid == [(1, 25), (2, 18)]
    assert errors == []


def test_blank_lines_are_skipped():
    """A paste picks up trailing newlines and blank separators between blocks."""
    valid, errors = _parse_bulk_lines("\n1, 25\n\n\n2, 18\n\n")

    assert valid == [(1, 25), (2, 18)]
    assert errors == []


def test_points_of_zero_are_accepted():
    """Most of a table is zero — only the top positions score — so refusing zero would
    make the common case impossible."""
    valid, errors = _parse_bulk_lines("11, 0")

    assert valid == [(11, 0)]
    assert errors == []


def test_a_line_with_more_than_one_comma_keeps_the_rest_as_points():
    """Split on the first comma only. A trailing comment after the points is a manager's
    note, and it earns an error about the points rather than a malformed-line error that
    would send them looking at the position."""
    valid, errors = _parse_bulk_lines("1, 25, fastest lap")

    assert valid == []
    assert any("points" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# What is refused, and how it reads
# ---------------------------------------------------------------------------


def test_a_line_with_no_comma_is_malformed():
    valid, errors = _parse_bulk_lines("1 25")

    assert valid == []
    assert "Malformed line" in errors[0]
    assert "1 25" in errors[0]


@pytest.mark.parametrize("position", ["first", "1.5", "one", "", "+1"])
def test_a_position_that_is_not_a_plain_integer_is_refused(position):
    """The round-trip check catches what `int()` alone would accept or truncate."""
    valid, errors = _parse_bulk_lines(f"{position}, 25")

    assert valid == []
    assert any("position" in e.lower() for e in errors)


@pytest.mark.parametrize("points", ["twenty-five", "25.5", "", "+25"])
def test_points_that_are_not_a_plain_integer_are_refused(points):
    valid, errors = _parse_bulk_lines(f"1, {points}")

    assert valid == []
    assert any("points" in e.lower() for e in errors)


def test_a_float_position_is_refused_rather_than_truncated():
    """`int(1.5)` would be 1 — silently scoring position 1 from a line that said 1.5, with
    the manager never knowing which line had been reinterpreted."""
    valid, errors = _parse_bulk_lines("1.5, 25")

    assert valid == []


def test_a_position_below_one_is_refused():
    """There is no position zero, and a negative one would sort above the winner."""
    valid, errors = _parse_bulk_lines("0, 25\n-1, 25")

    assert valid == []
    assert len(errors) == 2
    assert all(">= 1" in e for e in errors)


def test_negative_points_are_refused():
    """A championship does not take points away for finishing."""
    valid, errors = _parse_bulk_lines("1, -5")

    assert valid == []
    assert ">= 0" in errors[0]


def test_every_error_quotes_the_line_it_came_from():
    """A manager staring at twenty lines and the words "invalid position" has no way to
    find it."""
    _, errors = _parse_bulk_lines("1, 25\nbroken line\n3, 15")

    assert "broken line" in errors[0]


# ---------------------------------------------------------------------------
# Good lines and bad lines together
# ---------------------------------------------------------------------------


def test_a_bad_line_does_not_discard_the_good_ones():
    """A paste with one bad line still carries nineteen good ones, and refusing the lot
    would have the manager retype everything to fix one typo."""
    valid, errors = _parse_bulk_lines("1, 25\nnonsense\n3, 15")

    assert valid == [(1, 25), (3, 15)]
    assert len(errors) == 1


def test_several_bad_lines_are_all_reported():
    """One fix at a time, twenty lines deep, is how a manager comes to give up."""
    _, errors = _parse_bulk_lines("bad\n0, 5\n2, -1")

    assert len(errors) == 3


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


def test_a_repeated_position_keeps_the_last_value_and_says_so():
    """A manager correcting themselves mid-paste means the second value — and discarding
    both would be the one outcome neither reading supports. The note is what stops the
    change being silent."""
    valid, errors = _parse_bulk_lines("1, 25\n1, 30")

    assert valid == [(1, 30)]
    assert len(errors) == 1
    assert "Duplicate position 1" in errors[0]


def test_the_duplicate_note_names_both_values():
    """So a manager can tell which of their two lines won without re-reading the paste."""
    _, errors = _parse_bulk_lines("1, 25\n1, 30")

    assert "25" in errors[0]
    assert "30" in errors[0]


def test_a_duplicate_does_not_move_the_position_in_the_table():
    """The row keeps the place its first mention gave it, so a correction does not
    reorder the table underneath the manager."""
    valid, _ = _parse_bulk_lines("1, 25\n2, 18\n1, 30")

    assert valid == [(1, 30), (2, 18)]


def test_an_empty_paste_yields_nothing_and_complains_about_nothing():
    """A manager who opened the modal and thought better of it."""
    valid, errors = _parse_bulk_lines("")

    assert valid == []
    assert errors == []


def test_a_paste_of_only_whitespace_is_the_same():
    valid, errors = _parse_bulk_lines("\n   \n\t\n")

    assert valid == []
    assert errors == []
