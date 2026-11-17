"""`lineups_for_command`: `/team lineup`'s pictures, drawn as the command's own output.

The rules the command relies on, tested with no interaction: every division asked for is drawn, in
order, as the command's output and never as the lineup of record; the first division that cannot
be drawn refuses the command, and none after it is drawn; and every picture drawn is released
however the drawing ends, a refusal or a fault part way through included, the one leak no posting
path accounts for. The replies are the command's, in `test_team_lineup_command.py`.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.core.services.drivers.lineup_command import CommandLineups, lineups_for_command
from tests.support.lineup_drawing import LineupDrawingStub

GUILD = object()


def _divisions(*ids: int) -> list:
    return [SimpleNamespace(id=div_id, name=f"Division {div_id}") for div_id in ids]


def _pictures(tmp_path, *ids: int) -> dict:
    pictures = {}
    for div_id in ids:
        png = tmp_path / f"lineup{div_id}.png"
        png.write_bytes(b"\x89PNG")
        pictures[div_id] = png
    return pictures


async def test_a_league_that_does_not_draw_its_lineup_draws_nothing():
    """The lineup graphic is off: nothing is drawn, and the answer says so, for the command to
    write the lineup as text."""
    drawing = LineupDrawingStub(wanted=False)

    async with lineups_for_command(drawing, GUILD, _divisions(1, 2)) as lineups:
        assert lineups == CommandLineups(drawn=False)

    assert drawing.drawn_for_command == []
    assert drawing.discarded == []


async def test_every_division_is_drawn_in_order_and_released_once_done(tmp_path):
    """Three divisions, all drawn: each is drawn as the command's output, never as the lineup of
    record, its picture handed back in the order of the divisions; the pictures are released only
    once the block is left, so the command can send them first."""
    pictures = _pictures(tmp_path, 1, 2, 3)
    drawing = LineupDrawingStub(by_division={i: {"picture": p} for i, p in pictures.items()})

    async with lineups_for_command(drawing, GUILD, _divisions(1, 2, 3)) as lineups:
        assert lineups == CommandLineups(drawn=True, paths=tuple(pictures.values()))
        assert drawing.discarded == []

    assert drawing.drawn_for_command == [1, 2, 3]
    assert drawing.drawn == []
    assert drawing.discarded == list(pictures.values())


async def test_a_division_that_cannot_be_drawn_refuses_and_releases_what_was_drawn(tmp_path):
    """Division 1 is drawn, Division 2 cannot be (the template is at fault), and Division 3 is
    never drawn: the answer names Division 2 and the problem, for the command to refuse rather than
    fall back to text, and the picture drawn for Division 1, never to be sent, is released."""
    pictures = _pictures(tmp_path, 1, 3)
    drawing = LineupDrawingStub(by_division={
        1: {"picture": pictures[1]},
        2: {"problem": "the template is at fault"},
        3: {"picture": pictures[3]},
    })

    async with lineups_for_command(drawing, GUILD, _divisions(1, 2, 3)) as lineups:
        assert lineups.refused == "Division 2"
        assert lineups.problem == "the template is at fault"

    assert drawing.drawn_for_command == [1, 2]
    assert drawing.discarded == [pictures[1]], (
        "a picture drawn before the refusal was abandoned and never released"
    )


async def test_a_fault_drawing_a_later_division_is_let_out_and_releases_what_was_drawn(tmp_path):
    """Drawing Division 2 raises (the database cannot be read): the fault is let out, for the
    command's failure path to report, and the picture drawn for Division 1 is still released."""
    pictures = _pictures(tmp_path, 1)
    drawing = LineupDrawingStub(by_division={
        1: {"picture": pictures[1]}, 2: {"fault": RuntimeError("database is locked")},
    })

    with pytest.raises(RuntimeError, match="database is locked"):
        async with lineups_for_command(drawing, GUILD, _divisions(1, 2)):
            pytest.fail("the block ran although a division could not be drawn")

    assert drawing.drawn_for_command == [1, 2]
    assert drawing.discarded == [pictures[1]]


async def test_a_fault_sending_the_pictures_still_releases_them(tmp_path):
    """Every division is drawn and the send inside the block fails: the fault leaves, and every
    picture is released all the same."""
    pictures = _pictures(tmp_path, 1, 2)
    drawing = LineupDrawingStub(by_division={i: {"picture": p} for i, p in pictures.items()})

    with pytest.raises(RuntimeError, match="Discord refused"):
        async with lineups_for_command(drawing, GUILD, _divisions(1, 2)):
            raise RuntimeError("Discord refused the files")

    assert drawing.discarded == list(pictures.values())
