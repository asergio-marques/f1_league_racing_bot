"""`/results rounds sync` carried out on the change queue, whole (#439, slice 6).

It only reposts, so the command is a member's change, acknowledged at once and the acknowledgement
updated with the outcome ("synced", or what a league admin discarded). Each session's table is
posted, then the message it replaces deleted by a job of its own (`review_posting`'s
`post_session_results` and `delete_message`), session by session in round order, so each
replacement stands before its old one goes and the channel ends in round order. A table Discord
refuses stops the queue and its next try posts the text (Constitution XIV, rule 8).

**The gates are checked again when the change runs** (core specification, "How a change is carried
out"), so a sync waiting behind a stopped queue is refused as the command would be: the module, the
season's stage (results specification, pending completion), the division, its results channel and
an amendment open in the division (results specification, an amendment publishes nothing until it
is approved). The standings are `/results standings sync`'s and are not posted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.core.services.change_queue import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
    StepView,
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.season_gate import stage_refusal
from leaguebot.results.services import review_posting
from leaguebot.results.services.result_submission_service import sync_held_by_amendment
from leaguebot.results.services.results_post_service import division_repost_status

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

ROUNDS_SYNC = "results.rounds.sync"
PLAN_REPOST = "plan_repost"
CLOSE = "close"

_COMMAND = "results rounds sync"


def _discarded(view: StepView) -> bool:
    return "discarded" in (view.result or {})


def rounds_sync_change(seasons: "SeasonService", modules: "ModuleService") -> ChangeType:
    """`results.rounds.sync`: a member's change, repeatable, that posts a division's results of
    every round again. The payload is `{"division_id", "division_name"}`."""

    async def check(ctx: CheckContext) -> Verdict:
        name = str(ctx.payload["division_name"])
        division_id = int(ctx.payload["division_id"])
        if not await modules.is_results_enabled():
            return Verdict.refuse("❌ The Results & Standings module is not enabled on this server.")
        season = await seasons.get_setup_or_active_season()
        refusal = stage_refusal(season.stage if season is not None else None, _COMMAND)
        if refusal is not None or season is None:
            return Verdict.refuse(refusal or "")
        division = next(
            (each for each in await seasons.get_divisions(season.id) if each.id == division_id),
            None,
        )
        if division is None:
            return Verdict.refuse(f"❌ Division '{name}' not found.")
        held = await sync_held_by_amendment(ctx.db_path, division_id)
        if held is not None:
            return Verdict.refuse(held)
        status = await division_repost_status(ctx.db_path, division_id, await league_guild(ctx.bot))
        if status == "no_channel":
            return Verdict.refuse(f"❌ Division '{name}' has no results channel configured.")
        if status == "no_rounds":
            return Verdict.refuse(f"No completed rounds found for **{name}**. No results to post.")
        return Verdict.go()

    async def plan_repost(ctx: StepContext) -> StepResult:
        payload = ctx.payload
        async with get_connection(ctx.db_path) as db:
            planned = await review_posting.plan_division_posts(
                db, int(payload["division_id"]), division_name=str(payload["division_name"]),
                results_only=True,
            )
        return StepResult(result={"planned": len(planned)}, then=tuple(planned))

    async def describe_plan(ctx: StepContext) -> str:
        return f"finding the tables of {ctx.payload['division_name']} to post again"

    def not_synced(ctx: OutcomeContext) -> list[str]:
        return review_posting.not_done(ctx)

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        name = ctx.payload["division_name"]
        left = not_synced(ctx)
        if not left:
            return StepResult(
                result={"closed": True},
                lines=(f"{ctx.named} | /{_COMMAND} | Success\n  division: {name}",),
            )
        return StepResult(
            result={"closed": True},
            lines=(
                f"{ctx.named} | /{_COMMAND} | Incomplete\n  division: {name}\n"
                + "\n".join(f"  {line}" for line in left),
            ),
        )

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the results sync"

    def outcome(ctx: OutcomeContext) -> str:
        name = ctx.payload["division_name"]
        left = not_synced(ctx)
        if left:
            return "\n".join([f"⚠️ The results of **{name}** were not all synced:", *left])
        return f"✅ Results for all rounds in **{name}** synced to the results channel."

    steps: dict[str, Step] = {
        **review_posting.posting_steps(),
        PLAN_REPOST: Step(PLAN_REPOST, StepKind.ACT, plan_repost, describe=describe_plan),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=ROUNDS_SYNC,
        opening=(PlannedStep(PLAN_REPOST), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{ROUNDS_SYNC}:{payload['division_id']}",
        doing=lambda payload: (
            f"Syncing the results of every round of **{payload['division_name']}**"
        ),
        outcome=outcome,
        repeatable=True,
    )


__all__ = ["ROUNDS_SYNC", "PLAN_REPOST", "CLOSE", "rounds_sync_change"]
