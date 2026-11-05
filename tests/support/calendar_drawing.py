"""A stand-in for the image module's answers to core's calendar drawing (#283).

Core asks the drawing the builder hands it whether the league draws its calendar, has it draw one
division's calendar, and hands it back the drawn file to release. The stand-in answers as a test
sets it, and keeps what it was asked, so a test of core's posting reads no template and runs no
rasteriser. The drawing itself is the image module's, tested against its implementation under
`tests/image/`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


class CalendarDrawingStub:
    """The calendar part of core's drawing protocol, answering as set.

    *wanted* is whether the league draws its calendar, or the error asking raises. A drawing
    raises *fault* where one is given; otherwise it hands back *picture* (a PNG's path, or None),
    *problem* (the words for why it could not be drawn, or None) and *notices* (their words).
    `drawn` names each division drawn, in order; `discarded` holds each path handed back to
    release."""

    def __init__(self, *, wanted: bool | Exception = True, picture: Path | None = None,
                 problem: str | None = None, notices: tuple[str, ...] = (),
                 fault: Exception | None = None) -> None:
        self.wanted = wanted
        self.picture = picture
        self.problem = problem
        self.notices = tuple(notices)
        self.fault = fault
        self.drawn: list[str] = []
        self.discarded: list[Path | None] = []

    async def calendar_wanted(self) -> bool:
        if isinstance(self.wanted, Exception):
            raise self.wanted
        return self.wanted

    async def draw_calendar(self, division: Any, rounds: Any, tracks: Any, *,
                            season_number: Any = None) -> Any:
        from leaguebot.core.services.image_drawing import CalendarPicture

        self.drawn.append(division.name)
        if self.fault is not None:
            raise self.fault
        return CalendarPicture(path=self.picture, problem=self.problem, notices=self.notices)

    def discard_picture(self, path: Path | None) -> None:
        self.discarded.append(path)


def draw_calendars_with(bot: Any, monkeypatch: Any, stub: CalendarDrawingStub) -> CalendarDrawingStub:
    """Answer the calendar part of *bot*'s drawing, the one the builder handed every change that
    posts a calendar, with *stub*'s; gives *stub*."""
    for name in ("calendar_wanted", "draw_calendar", "discard_picture"):
        monkeypatch.setattr(bot.image_drawing, name, getattr(stub, name))
    return stub
