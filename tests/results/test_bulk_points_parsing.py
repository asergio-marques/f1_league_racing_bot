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

import pytest

from leaguebot.results.cogs.results_cog import _parse_bulk_lines


# ---------------------------------------------------------------------------
# The ordinary case
# ---------------------------------------------------------------------------


def test_a_clean_table_is_read_in_full():
    valid, errors, _overrides = _parse_bulk_lines("1, 25\n2, 18\n3, 15")

    assert valid == [(1, 25), (2, 18), (3, 15)]
    assert errors == []


@pytest.mark.parametrize(
    ("paste", "expected"),
    [
        ("  1 ,   25  \n2,18", [(1, 25), (2, 18)]),
        ("  1 ,  25  ", [(1, 25)]),
    ],
    ids=["two-lines", "one-padded-line"],
)
def test_whitespace_around_the_comma_is_forgiven(paste, expected):
    """A pasted table carries it, and refusing over spacing would refuse most real input."""
    valid, errors, _overrides = _parse_bulk_lines(paste)

    assert valid == expected
    assert errors == []


@pytest.mark.parametrize(
    ("paste", "expected"),
    [
        ("\n1, 25\n\n\n2, 18\n\n", [(1, 25), (2, 18)]),
        ("1, 25\n\n2, 18\n\n3, 15", [(1, 25), (2, 18), (3, 15)]),
    ],
    ids=["leading-trailing-and-between", "between-only"],
)
def test_blank_lines_are_skipped(paste, expected):
    """A paste picks up trailing newlines and blank separators between blocks."""
    valid, errors, _overrides = _parse_bulk_lines(paste)

    assert valid == expected
    assert errors == []


@pytest.mark.parametrize(
    ("paste", "expected"),
    [("11, 0", [(11, 0)]), ("1, 0", [(1, 0)])],
    ids=["p11", "p1"],
)
def test_points_of_zero_are_accepted(paste, expected):
    """Most of a table is zero — only the top positions score — so refusing zero would
    make the common case impossible."""
    valid, errors, _overrides = _parse_bulk_lines(paste)

    assert valid == expected
    assert errors == []


def test_a_line_with_more_than_one_comma_keeps_the_rest_as_points():
    """Split on the first comma only. A trailing comment after the points is a manager's
    note, and it earns an error about the points rather than a malformed-line error that
    would send them looking at the position."""
    valid, errors, _overrides = _parse_bulk_lines("1, 25, fastest lap")

    assert valid == []
    assert any("points" in e.lower() for e in errors)


# ---------------------------------------------------------------------------
# What is refused, and how it reads
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("line", ["1 25", "not a valid line"], ids=["numbers", "words"])
def test_a_line_with_no_comma_is_malformed(line):
    valid, errors, _overrides = _parse_bulk_lines(line)

    assert valid == []
    assert len(errors) == 1
    assert "Malformed line" in errors[0]
    assert line in errors[0]


@pytest.mark.parametrize(
    ("position", "points"),
    [*((position, "25") for position in ["first", "1.5", "one", "", "+1"]), ("1.5", "10")],
    ids=["first", "1.5", "one", "", "+1", "1.5-on-10-points"],
)
def test_a_position_that_is_not_a_plain_integer_is_refused(position, points):
    """The round-trip check catches what `int()` alone would accept or truncate."""
    valid, errors, _overrides = _parse_bulk_lines(f"{position}, {points}")

    assert valid == []
    assert len(errors) == 1
    assert any("position" in e.lower() for e in errors)


@pytest.mark.parametrize("points", ["twenty-five", "25.5", "", "+25", "10.5"])
def test_points_that_are_not_a_plain_integer_are_refused(points):
    valid, errors, _overrides = _parse_bulk_lines(f"1, {points}")

    assert valid == []
    assert len(errors) == 1
    assert any("points" in e.lower() for e in errors)


@pytest.mark.parametrize(
    ("paste", "refused", "first_position"),
    [("0, 25\n-1, 25", 2, 0), ("0, 10", 1, 0), ("-1, 10", 1, -1)],
    ids=["zero-and-negative", "zero", "negative"],
)
def test_a_position_below_one_is_refused(paste, refused, first_position):
    """There is no position zero, and a negative one would sort above the winner. The error
    names the position it refused."""
    valid, errors, _overrides = _parse_bulk_lines(paste)

    assert valid == []
    assert len(errors) == refused
    assert all(">= 1" in e for e in errors)
    assert f"got {first_position}" in errors[0]


def test_negative_points_are_refused():
    """A championship does not take points away for finishing."""
    valid, errors, _overrides = _parse_bulk_lines("1, -5")

    assert valid == []
    assert len(errors) == 1
    assert ">= 0" in errors[0]
    assert "-5" in errors[0]


def test_every_error_quotes_the_line_it_came_from():
    """A manager staring at twenty lines and the words "invalid position" has no way to
    find it."""
    _, errors, _overrides = _parse_bulk_lines("1, 25\nbroken line\n3, 15")

    assert "broken line" in errors[0]


# ---------------------------------------------------------------------------
# Good lines and bad lines together
# ---------------------------------------------------------------------------


def test_a_bad_line_does_not_discard_the_good_ones():
    """The parser reads every line, so a bad one does not stop the good ones being read and
    every fault can be reported at once. Whether any of it is applied is the caller's rule."""
    valid, errors, _overrides = _parse_bulk_lines("1, 25\nnonsense\n3, 15")

    assert valid == [(1, 25), (3, 15)]
    assert len(errors) == 1


def test_several_bad_lines_are_all_reported():
    """One fix at a time, twenty lines deep, is how a manager comes to give up."""
    _, errors, _overrides = _parse_bulk_lines("bad\n0, 5\n2, -1")

    assert len(errors) == 3


# ---------------------------------------------------------------------------
# Duplicates
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("paste", "expected"),
    [("1, 25\n1, 30", [(1, 30)]), ("1, 25\n1, 10", [(1, 10)])],
    ids=["raised", "lowered"],
)
def test_a_repeated_position_keeps_the_last_value_and_says_so(paste, expected):
    """A manager correcting themselves mid-paste means the second value — and discarding
    both would be the one outcome neither reading supports. The note is what stops the
    change being silent."""
    valid, errors, overrides = _parse_bulk_lines(paste)

    assert valid == expected
    assert errors == [], "an override is reported, never counted as a bad line"
    assert len(overrides) == 1
    assert "Duplicate position 1" in overrides[0]


def test_the_duplicate_note_names_both_values():
    """So a manager can tell which of their two lines won without re-reading the paste."""
    _, _errors, overrides = _parse_bulk_lines("1, 25\n1, 30")

    assert "25" in overrides[0]
    assert "30" in overrides[0]


def test_a_duplicate_does_not_move_the_position_in_the_table():
    """The row keeps the place its first mention gave it, so a correction does not
    reorder the table underneath the manager."""
    valid, _errors, _overrides = _parse_bulk_lines("1, 25\n2, 18\n1, 30")

    assert valid == [(1, 30), (2, 18)]


def test_an_empty_paste_yields_nothing_and_complains_about_nothing():
    """A manager who opened the modal and thought better of it."""
    valid, errors, _overrides = _parse_bulk_lines("")

    assert valid == []
    assert errors == []


@pytest.mark.parametrize(
    "paste", ["\n   \n\t\n", "   \n\n   "], ids=["with-a-tab", "spaces-only"]
)
def test_a_paste_of_only_whitespace_is_the_same(paste):
    valid, errors, _overrides = _parse_bulk_lines(paste)

    assert valid == []
    assert errors == []
