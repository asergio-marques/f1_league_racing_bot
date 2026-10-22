"""`/results standings sync` carried out on the change queue, whole (#439, slice 6b).

It only reposts, so the command is a member's change, acknowledged at once and the acknowledgement
updated with the outcome ("synced", or what a league admin discarded). Each round's standings are
worked out again from the results as they stand when its job runs, as the command always has
(`post_standings_afresh`), posted as a new message and the message it replaces deleted by a job
of its own (`review_posting`'s `delete_message`), round by round in round order, so each
replacement stands before its old one goes. A table Discord refuses stops the queue and its next
try posts the text (Constitution XIV, rule 8).

**The gates are checked again when the change runs** (core specification, "How a change is carried
out"), as `/results rounds sync`'s are: the module, the season's stage, the division, its
standings channel and an amendment open in the division. The results are `/results rounds sync`'s
and are not posted.
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
)
from leaguebot.core.utils.league_server import league_guild
from leaguebot.core.utils.season_gate import stage_refusal
from leaguebot.results.services import review_posting
from leaguebot.results.services.result_submission_service import sync_held_by_amendment
from leaguebot.results.services.results_post_service import standings_repost_status

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.core.services.season_service import SeasonService

STANDINGS_SYNC = "results.standings.sync"
PLAN_REPOST = "plan_repost"
CLOSE = "close"

_COMMAND = "results standings sync"


def standings_sync_change(seasons: "SeasonService", modules: "ModuleService") -> ChangeType:
    """`results.standings.sync`: a member's change, repeatable, that posts a division's
    standings of every round again. The payload is `{"division_id", "division_name"}`."""

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
        status = await standings_repost_status(
            ctx.db_path, division_id, await league_guild(ctx.bot)
        )
        if status == "no_channel":
            return Verdict.refuse(f"❌ Division '{name}' has no standings channel configured.")
        if status == "no_rounds":
            return Verdict.refuse(f"No completed rounds found for **{name}**. No standings to post.")
        return Verdict.go()

    async def plan_repost(ctx: StepContext) -> StepResult:
        payload = ctx.payload
        async with get_connection(ctx.db_path) as db:
            planned = await review_posting.plan_division_posts(
                db, int(payload["division_id"]), division_name=str(payload["division_name"]),
                standings_only=True,
            )
        return StepResult(result={"planned": len(planned)}, then=tuple(planned))

    async def describe_plan(ctx: StepContext) -> str:
        return f"finding the standings of {ctx.payload['division_name']} to post again"

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
        return "recording the standings sync"

    def outcome(ctx: OutcomeContext) -> str:
        name = ctx.payload["division_name"]
        left = not_synced(ctx)
        if left:
            return "\n".join([f"⚠️ The standings of **{name}** were not all synced:", *left])
        return f"✅ Standings for **{name}** synced to the standings channel."

    steps: dict[str, Step] = {
        **review_posting.posting_steps(),
        PLAN_REPOST: Step(PLAN_REPOST, StepKind.ACT, plan_repost, describe=describe_plan),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=STANDINGS_SYNC,
        opening=(PlannedStep(PLAN_REPOST), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: f"{STANDINGS_SYNC}:{payload['division_id']}",
        doing=lambda payload: (
            f"Syncing the standings of every round of **{payload['division_name']}**"
        ),
        outcome=outcome,
        repeatable=True,
    )


__all__ = ["STANDINGS_SYNC", "PLAN_REPOST", "CLOSE", "standings_sync_change"]
