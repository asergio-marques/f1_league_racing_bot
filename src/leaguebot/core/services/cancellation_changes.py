"""Cancelling a round on the change queue (#439, slice 4b).

`/round cancel` checks its confirmation word, the season and the names at the press, in the season
cog, then asks the queue for this change. Every other gate it had is the change type's `check`,
which judges the request when it is asked and again when it comes up to run, in the words the
command used, so that a round whose results were entered, or whose submission opened, while the
cancellation waited is refused with the same reply. It imports nothing from a module and no cog:
results' `is_submission_open` reaches it through the builder, and it reads the season through the
service it is handed.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import Verdict
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.core.services.change_queue import ChangeType, CheckContext
from leaguebot.core.services.season_service import SeasonImmutableError, SeasonService

__all__ = ["ROUND_CANCEL", "round_cancel_change"]

ROUND_CANCEL = "season.round.cancel"

_COMMAND = "`/round cancel`"
NOT_ONGOING = f"❌ {_COMMAND} is available only while the season is ongoing."
ARCHIVED = "❌ This season is archived (COMPLETED) and cannot be modified."


def _already_cancelled(payload: dict[str, Any]) -> str:
    return f"❌ Round {payload['round_number']} in **{payload['division_name']}** is already cancelled."


def _results_entered(payload: dict[str, Any]) -> str:
    return (
        f"❌ Cannot cancel Round {payload['round_number']} — its results have already been "
        "entered, and the drivers' reports and appeals depend on it."
    )


def _submission_open(payload: dict[str, Any]) -> str:
    return (
        f"❌ Cannot cancel Round {payload['round_number']} — a results submission channel is "
        "currently open. Close the submission first."
    )


def round_cancel_change(
    *,
    seasons: SeasonService,
    submission_open: Callable[[str, int], Awaitable[bool]],
) -> ChangeType:
    """The change that cancels a round; see the module.

    The payload is ``{"round_id", "round_number", "track_name", "division_id", "division_name",
    "season_number"}``. It names no season: a payload that does is read as holding every division
    of that season (`review_changes.division_job_in_hand`). The builder hands in the *seasons*
    service and results' *submission_open*.
    """

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        season = await seasons.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            return Verdict.refuse(NOT_ONGOING)
        try:
            await seasons.assert_season_mutable(season)
        except SeasonImmutableError:
            return Verdict.refuse(ARCHIVED)

        round_id = int(payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
            row = await cursor.fetchone()
        if row is None:
            return Verdict.refuse(
                f"❌ Round {payload['round_number']} not found in division "
                f"`{payload['division_name']}`."
            )
        if row["status"] == RoundStatus.CANCELLED.value:
            return Verdict.refuse(_already_cancelled(payload))
        if row["status"] not in ROUND_CANCELLABLE:
            return Verdict.refuse(_results_entered(payload))
        if await submission_open(ctx.db_path, round_id):
            return Verdict.refuse(_submission_open(payload))
        return Verdict.go()

    return ChangeType(
        kind=ROUND_CANCEL,
        opening=(),
        steps={},
        check=check,
        key=lambda payload: f"{ROUND_CANCEL}:{payload['round_id']}",
        doing=lambda payload: (
            f"Cancelling round {payload['round_number']} in **{payload['division_name']}**"
        ),
        outcome=lambda _ctx: "",
    )
