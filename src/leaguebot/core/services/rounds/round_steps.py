"""Naming a round in a step of an amendment by the number it bears when the name is read.

An amendment ahead of another on the change queue can renumber the division, so the step a module
defines for the amendment (weather's, attendance's) names the round by the number it bears when
the step is described, and the press's number only where the round can no longer be read. Core's
own steps of the amendment name it the same way.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from leaguebot.core.services.queue.change_types import StepContext
    from leaguebot.core.services.seasons.season_service import SeasonService


#: The amendment's job that judges it and keeps what each module's part of the judgement keeps.
JUDGE = "judge"


def judge_kept(steps: Iterable[Any]) -> Mapping[str, Any]:
    """What the amendment's judging kept for the later jobs, from the change's *steps* (a step
    context's ``steps``): what each module's part of the judgement kept, merged, which core stores
    as it was given and reads for no module. Each module reads back its own windows from it, and
    reads them afresh where it finds none (a record kept before the update, or a job discarded)."""
    kept = next((each for each in reversed(tuple(steps)) if each.name == JUDGE), None)
    return ((kept.result if kept else None) or {}).get("kept") or {}


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
