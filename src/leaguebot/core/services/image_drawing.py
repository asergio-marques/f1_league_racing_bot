"""What core asks of the image module.

`ImageDrawing` is the protocol the builder hands `PlacementService` and the calendar's posting,
so that core imports nothing of the image module: core calls these methods and the image module
implements them (`leaguebot.image.services.image_drawing.ImageDrawing`). It is attached to the
bot as `bot.image_drawing` and declared on `LeagueBot`, as results declares
`AttendanceAfterReview` for attendance (architecture.md, "How modules and core fit together").

It holds the four placement capacity questions and the calendar. Each capacity question answers
a `CapacityProblem` — the problem as a sentence with no final stop, and the image module's own
words for what to do about it — or `None` where nothing is wrong. Core joins the two into its
own refusal, adding what is core's own to say. The calendar's `draw_calendar` answers a
`CalendarPicture` instead, because core itself posts what it hands back rather than wording a
refusal from it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class CapacityProblem:
    """What a capacity question found wrong, and how a league puts it right.

    *problem* is the image module's sentence naming the overflow, with no final stop. *remedy*
    is the image module's own words for what to do about it, a full sentence. The caller joins
    them, adding what is its own to say.
    """

    problem: str
    remedy: str


@dataclass(frozen=True)
class CalendarPicture:
    """What drawing one division's calendar produced.

    *path* is the drawn PNG's path, or None where it could not be drawn. *problem* is the image
    module's words for why, with no final stop, or None where it drew. *notices* are the
    drawing's own words for what it survived; `draw_calendar` has already reported them to the
    log channel, so the caller never posts them there a second time — only in a command's own
    output, which is the caller's to decide.
    """

    path: Path | None
    problem: str | None
    notices: tuple[str, ...] = field(default_factory=tuple)


class ImageDrawing(Protocol):
    """The image module's answers to what core asks of it in order to place a driver and to
    convey a division's calendar."""

    async def reserve_capacity_problem(self, reserves: int) -> CapacityProblem | None:
        """Whether *reserves* reserve drivers would outgrow the configured lineup template's
        reserve block, or None. Nothing where the lineup is not drawn (the module off, the
        `lineup` aspect off, or no valid template configured)."""

    async def sheet_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        """Whether *drivers*, of either kind, would outgrow the configured attendance sheet's
        rows, or None. Nothing where the sheet is not drawn."""

    async def standings_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        """Whether *drivers*, classified drivers alone, would outgrow the configured driver
        standings template's rows, or None. Nothing where the standings are not drawn."""

    async def declared_capacity_problem(self, drivers: int) -> CapacityProblem | None:
        """Whether *drivers* seated in the division would outgrow the smallest capacity any
        image template declares in code, or None. Nothing while the images module is off, or
        while no catalogue declares one."""

    async def calendar_wanted(self) -> bool:
        """Whether this league conveys its calendar as a picture: both the module and the
        `calendar` aspect must be on. A gate that cannot be read answers False, so the text
        calendar goes out and a league never loses its calendar to a fault reading a switch."""

    async def draw_calendar(
        self, division: Any, rounds: Any, tracks: Any, *, season_number: Any = None,
    ) -> CalendarPicture:
        """Draw *division*'s calendar of *rounds*, named against *tracks*. Hands back the
        picture drawn, or the problem naming why it could not be, and the notices it survived —
        already reported to the log channel in one message."""

    def discard_picture(self, path: Path | None) -> None:
        """Release a path `draw_calendar` handed back, whichever way its posting ended. A path
        of None is a no-op, covering a calendar that was never drawn."""
