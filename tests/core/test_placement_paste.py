"""Reading the list `/driver assign-bulk` takes: one driver a line, `<@id>, division, team`.

No fixtures: the parser resolves nothing against Discord or the database. It reads each line,
names every line it cannot read at once, so a manager corrects one paste rather than one line per
attempt, and numbers a line as the manager sees it in the box. The module does not exist until
#633 is built, so each test imports it where it runs.
"""
from __future__ import annotations

import pytest


def test_a_paste_is_read_line_by_line_and_trimmed():
    """Two lines: `<@101>` into Pro's Red Bull Racing, padded with spaces, then `<@102>` into Am as
    a Reserve. Both are read, in the order pasted, each field without its spaces, and nothing is
    refused."""
    from leaguebot.core.utils.placement_paste import PastedPlacement, parse_placement_paste

    placements, errors = parse_placement_paste(
        "  <@101> ,  Pro ,  Red Bull Racing  \n<@102>, Am, Reserve"
    )

    assert errors == []
    assert placements == [
        PastedPlacement(line=1, account=101, division="Pro", team="Red Bull Racing"),
        PastedPlacement(line=2, account=102, division="Am", team="Reserve"),
    ]


def test_a_line_is_numbered_as_the_box_shows_it():
    """`<@101>` into Pro's Ferrari on line 1, two blank lines, then `<@102>` into Pro's Ferrari on
    line 4. The blank lines are skipped, but counted: the second driver is line 4, not line 2."""
    from leaguebot.core.utils.placement_paste import parse_placement_paste

    placements, errors = parse_placement_paste("<@101>, Pro, Ferrari\n\n   \n<@102>, Pro, Ferrari")

    assert errors == []
    assert [placement.line for placement in placements] == [1, 4]


def test_a_comma_in_a_division_name_survives():
    """`<@101>, Pro, Europe, Red Bull Racing`: the division is everything between the first comma
    and the last, so a division named "Pro, Europe" is read whole and the team is Red Bull
    Racing."""
    from leaguebot.core.utils.placement_paste import parse_placement_paste

    placements, errors = parse_placement_paste("<@101>, Pro, Europe, Red Bull Racing")

    assert errors == []
    assert [(p.division, p.team) for p in placements] == [("Pro, Europe", "Red Bull Racing")]


@pytest.mark.parametrize(
    "paste",
    [
        pytest.param("<@101>, Ferrari", id="two_fields"),
        pytest.param("<@101>", id="one_field"),
        pytest.param("101, Pro, Ferrari", id="a_bare_id"),
        pytest.param("Max Verstappen, Pro, Ferrari", id="a_name_instead_of_a_mention"),
    ],
)
def test_a_line_that_cannot_be_read_is_named(paste):
    """A paste of one line naming no division and team, or naming its driver other than by a
    mention: a bare Discord ID, or the driver's name. The line is refused as line 1, and nothing
    is read from it."""
    from leaguebot.core.utils.placement_paste import parse_placement_paste

    placements, errors = parse_placement_paste(paste)

    assert placements == []
    assert len(errors) == 1
    assert errors[0].startswith("Line 1:")


def test_every_line_that_cannot_be_read_is_named_at_once():
    """Four lines: `<@101>` into Pro's Ferrari, then a bare ID, a line with no team and a name in
    place of a mention. Lines 2, 3 and 4 are each refused, by their number, in one go."""
    from leaguebot.core.utils.placement_paste import parse_placement_paste

    _placements, errors = parse_placement_paste(
        "<@101>, Pro, Ferrari\n102, Pro, Ferrari\n<@103>, Pro\nLewis, Pro, Ferrari"
    )

    assert [error.split(":")[0] for error in errors] == ["Line 2", "Line 3", "Line 4"]


@pytest.mark.parametrize("paste", ["", "\n   \n"], ids=["nothing", "blank_lines"])
def test_an_empty_paste_is_refused(paste):
    """A paste holding nothing, or blank lines alone, places nobody and is refused."""
    from leaguebot.core.utils.placement_paste import parse_placement_paste

    placements, errors = parse_placement_paste(paste)

    assert placements == []
    assert errors


def test_the_refusal_names_every_fault():
    """Forty lines that cannot be read. The refusal the manager reads names all forty, with no
    "…and N more" line; a reply too long for one message is divided as it is sent."""
    from leaguebot.core.utils.placement_paste import format_placement_errors

    errors = [f"Line {n}: expected `<@driver>, division, team`." for n in range(1, 41)]

    text = format_placement_errors(errors)

    for error in errors:
        assert error in text
    assert "more." not in text
