"""A stand-in for the image module's answers to core's lineup drawing (#283).

Core asks the drawing the builder hands `PlacementService` whether the league draws its lineup,
has it draw one division's lineup, as the lineup of record or as `/team lineup`'s output, and
hands it back the drawn file to release. The stand-in
answers as a test sets it, and keeps what it was asked, so a test of core's posting reads no
template and runs no rasteriser. The drawing itself is the image module's, tested against its
implementation under `tests/image/`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class LineupDrawingStub:
    """The lineup part of core's drawing protocol, answering as set.

    *wanted* is whether the league draws its lineup. A drawing raises *fault* where one is given;
    otherwise it hands back *picture* (a PNG's path, or None), *problem* (the words for why it
    could not be drawn, or None) and *notices* (their words). *by_division* answers a division
    named in it, by id, its own way instead: a mapping of `picture`, `problem`, `notices` and
    `fault` overriding those above, so a batch can have one division drawn and the next refused.
    `wanted_asked` counts the times it was asked whether the league draws its lineup; `drawn`
    names each division drawn as the lineup of record, by id, in order, and `drawn_for_command`
    each drawn as a command's output; `discarded` holds each path handed back to release."""

    def __init__(self, *, wanted: bool = True, picture: Path | None = None,
                 problem: str | None = None, notices: tuple[str, ...] = (),
                 fault: Exception | None = None,
                 by_division: dict[int, dict[str, Any]] | None = None) -> None:
        self.wanted = wanted
        self.picture = picture
        self.problem = problem
        self.notices = tuple(notices)
        self.fault = fault
        self.by_division = dict(by_division or {})
        self.wanted_asked = 0
        self.drawn: list[int] = []
        self.drawn_for_command: list[int] = []
        self.discarded: list[Path | None] = []

    def _answer(self, division_id: int) -> Any:
        from leaguebot.core.services.image_drawing import LineupPicture

        own = self.by_division.get(int(division_id), {})
        fault = own.get("fault", self.fault)
        if fault is not None:
            raise fault
        return LineupPicture(
            path=own.get("picture", self.picture),
            problem=own.get("problem", self.problem),
            notices=tuple(own.get("notices", self.notices)),
        )

    async def lineup_wanted(self) -> bool:
        self.wanted_asked += 1
        return self.wanted

    async def draw_lineup(self, guild: Any, division_id: int) -> Any:
        self.drawn.append(int(division_id))
        return self._answer(division_id)

    async def draw_lineup_for_command(self, guild: Any, division_id: int) -> Any:
        self.drawn_for_command.append(int(division_id))
        return self._answer(division_id)

    def discard_picture(self, path: Path | None) -> None:
        self.discarded.append(path)


def draw_lineups_with(bot: Any, monkeypatch: Any, stub: LineupDrawingStub) -> LineupDrawingStub:
    """Answer the lineup part of *bot*'s drawing, the one the builder handed `PlacementService`,
    with *stub*'s; gives *stub*. The lineup's two questions are set where the drawing does not
    answer them yet, so a test written before them fails on what it asserts."""
    for name in ("lineup_wanted", "draw_lineup", "discard_picture"):
        monkeypatch.setattr(bot.image_drawing, name, getattr(stub, name), raising=False)
    return stub
