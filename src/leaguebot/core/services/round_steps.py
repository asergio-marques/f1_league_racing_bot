"""Naming a round in a step of an amendment by the number it bears when the name is read.

An amendment ahead of another on the change queue can renumber the division, so the step a module
defines for the amendment (weather's, attendance's) names the round by the number it bears when
the step is described, and the press's number only where the round can no longer be read. Core's
own steps of the amendment name it the same way.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from leaguebot.core.models.round import AttendanceWindows, WeatherWindows

if TYPE_CHECKING:
    from leaguebot.core.services.queue.change_types import StepContext
    from leaguebot.core.services.season_service import SeasonService


#: The amendment's job that judges it and keeps the windows the later jobs judge it by.
JUDGE = "judge"


def judged_windows(ctx: Any) -> tuple[AttendanceWindows | None, WeatherWindows | None]:
    """The windows the amendment's judging kept, as the later jobs of a change judge by them:
    `(attendance, weather)`, each None where it kept none (it was discarded, or has not run)."""
    kept = next((each for each in reversed(ctx.steps) if each.name == JUDGE), None)
    windows = ((kept.result if kept else None) or {}).get("windows") or {}
    attendance, weather = windows.get("attendance"), windows.get("weather")
    return (
        AttendanceWindows(**attendance) if attendance is not None else None,
        WeatherWindows(**weather) if weather is not None else None,
    )


async def round_number_now(seasons: SeasonService, ctx: Any) -> Any:
    """The number the round of *ctx*'s change bears now, the press's where it cannot be read."""
    rnd = await seasons.get_round(int(ctx.payload["round_id"]))
    return ctx.payload["round_number"] if rnd is None else rnd.round_number


def named_now(seasons: SeasonService, text: str) -> Callable[[StepContext], Awaitable[str]]:
    """A step's description, *text* with ``{number}`` the number the round bears now and
    ``{division}`` the division's name as the change was asked."""

    async def describe(ctx: StepContext) -> str:
        return text.format(
            number=await round_number_now(seasons, ctx), division=ctx.payload["division_name"]
        )

    return describe
