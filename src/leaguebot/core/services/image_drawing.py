"""What core asks of the image module.

`ImageDrawing` is the protocol the builder hands `PlacementService`, so that core imports
nothing of the image module: core calls these methods and the image module implements them (`leaguebot.image.services.image_drawing.ImageDrawing`). It is attached to the
bot as `bot.image_drawing` and declared on `LeagueBot`, as results declares
`AttendanceAfterReview` for attendance (architecture.md, "How modules and core fit together").

It holds the four placement capacity questions. Each answers a `CapacityProblem` — the problem
as a sentence with no final stop, and the image module's own words for what to do about it — or
`None` where nothing is wrong. Core joins the two into its own
refusal, adding what is core's own to say.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CapacityProblem:
    """What a capacity question found wrong, and how a league puts it right.

    *problem* is the image module's sentence naming the overflow, with no final stop. *remedy*
    is the image module's own words for what to do about it, a full sentence. The caller joins
    them, adding what is its own to say.
    """

    problem: str
    remedy: str


class ImageDrawing(Protocol):
    """The image module's answers to what core asks of it in order to place a driver."""

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
