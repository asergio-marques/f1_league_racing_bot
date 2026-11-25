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
    could not be drawn, or None), *notices* (their words), where *log_link* is given, the link
    to the log channel's message the drawing reported the notices in, and, where *fault_source*
    is given, where the picture's fault lies. *by_division* answers a division named in it, by
    id, its own way instead: a mapping of `picture`, `problem`, `notices`, `log_link`,
    `fault_source` and `fault` overriding those above, so a batch can have one division
    drawn and the next refused.
    `wanted_asked` counts the times it was asked whether the league draws its lineup; `drawn`
    names each division drawn as the lineup of record, by id, in order, and `drawn_for_command`
    each drawn as a command's output, `commanded` the member and the command each such drawing
    was handed (`named`, `what`), to open the log entry holding its notices; `discarded` holds
    each path handed back to release."""

    def __init__(self, *, wanted: bool = True, picture: Path | None = None,
                 problem: str | None = None, notices: tuple[str, ...] = (),
                 fault: Exception | None = None, log_link: str | None = None,
                 fault_source: Any = None,
                 by_division: dict[int, dict[str, Any]] | None = None) -> None:
        self.wanted = wanted
        self.picture = picture
        self.problem = problem
        self.notices = tuple(notices)
        self.fault = fault
        self.log_link = log_link
        self.fault_source = fault_source
        self.by_division = dict(by_division or {})
        self.wanted_asked = 0
        self.drawn: list[int] = []
        self.drawn_for_command: list[int] = []
        self.commanded: list[tuple[str | None, str | None]] = []
        self.discarded: list[Path | None] = []

    def _answer(self, division_id: int) -> Any:
        from leaguebot.core.services.modules.image_drawing import LineupPicture

        own = self.by_division.get(int(division_id), {})
        fault = own.get("fault", self.fault)
        if fault is not None:
            raise fault
        log_link = own.get("log_link", self.log_link)
        fault_source = own.get("fault_source", self.fault_source)
        return LineupPicture(
            path=own.get("picture", self.picture),
            problem=own.get("problem", self.problem),
            notices=tuple(own.get("notices", self.notices)),
            **({"log_link": log_link} if log_link is not None else {}),
            **({"fault_source": fault_source} if fault_source is not None else {}),
        )

    async def lineup_wanted(self) -> bool:
        self.wanted_asked += 1
        return self.wanted

    async def draw_lineup(self, guild: Any, division_id: int) -> Any:
        self.drawn.append(int(division_id))
        return self._answer(division_id)

    async def draw_lineup_for_command(
        self, guild: Any, division_id: int, *, named: str | None = None, what: str | None = None,
    ) -> Any:
        self.drawn_for_command.append(int(division_id))
        self.commanded.append((named, what))
        return self._answer(division_id)

    def discard_picture(self, path: Path | None) -> None:
        self.discarded.append(path)


def draw_lineups_with(bot: Any, monkeypatch: Any, stub: LineupDrawingStub) -> LineupDrawingStub:
    """Answer the lineup part of *bot*'s drawing, the one the builder hands the lineup poster,
    with *stub*'s; gives *stub*. The lineup's two questions are set where the drawing does not
    answer them yet, so a test written before them fails on what it asserts."""
    for name in ("lineup_wanted", "draw_lineup", "discard_picture"):
        monkeypatch.setattr(bot.image_drawing, name, getattr(stub, name), raising=False)
    return stub
