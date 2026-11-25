"""`results.channel.carry`: the posts already up follow a results or standings channel that moved.

`/results channel results` and `standings` save the new channel as any setting does, never holding
up the queue, and ask this change as the bot's own follow-on where the channel they moved away from
still stands (`channel_settings`). The carry is a change of its own on the league lane, so it waits
behind any stopped job as a sync does, and it posts as a sync does: `plan_division_posts` plans
every round's tables (the results carry) or standings (the standings carry), each posted in the new
channel and its old message then deleted from the old one, the table that Discord refuses stopping
the lane. Its scope is what the syncs repost. The payload is `{"division_id", "division_name",
"which", "old_channel_id", "new_channel_id"}`, `which` being "results" or "standings".

**Whether the carry is still due is judged as it starts, never at the ask**, for a change ahead of
it may have switched results off, opened an amendment, ended the season's racing or moved the
channel again. A carry no longer due is dropped, and the log channel is told that the posts stay in
the old channel and why (`Verdict.not_due`'s line): the posts are not lost, and a sync posts them in
the new channel once the league wants them there. A carry whose new channel has gone, or which the
bot cannot post in, is refused, which stops the lane at the carry for the league to set a channel
and press Retry: the move is saved, and the posts have still to be carried.

**The old channel is noted on posts that record none before the carry posts anything.** A post
that records no channel is taken to stand in the division's present channel (`standing_posts.
posted_in`), which after a move is the new one, so its old message would be deleted from a channel
it was never in. Every post the bot makes records its channel; the note is for one that does not,
and aims that deletion at the channel the carry moves it from.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.settings.channel_registry_service import (
    ChannelUse,
    unusable_refusal,
)
from leaguebot.framework.db.database import get_connection
from leaguebot.framework.models.change import PlannedStep, StepKind, StepResult, Verdict
from leaguebot.framework.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.framework.utils.hooks import AwaitedHook
from leaguebot.framework.utils.league_server import league_guild
from leaguebot.results.models.switch import SWITCH_KEY
from leaguebot.results.services.module.channel_settings import held_channel
from leaguebot.results.services.module.division_channels import channel_needs
from leaguebot.results.services.review import review_posting
from leaguebot.results.services.round_amendment.amendment_writes import open_amendment_in_division

if TYPE_CHECKING:
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.framework.services.switches.module_service import ModuleService
    from leaguebot.image.services.render.image_drawing import ImageDrawing

CHANNEL_CARRY = "results.channel.carry"
NOTE_OLD_CHANNEL = "note_old_channel"
PLAN_CARRY = "plan_carry"
CLOSE = "close"

RESULTS = "results"
STANDINGS = "standings"

#: The column of `division_results_config` holding each channel the carry follows.
_COLUMNS = {RESULTS: "results_channel_id", STANDINGS: "standings_channel_id"}

#: The sync command that posts each kind of post where a league wants it.
_SYNCS = {RESULTS: "/results rounds sync", STANDINGS: "/results standings sync"}


def carry_what(payload: dict[str, Any]) -> str:
    """What the carry of *payload* is called in the log: the posts, the division and both channels."""
    return (
        f"Carrying the {payload['which']} of **{payload['division_name']}** from "
        f"<#{payload['old_channel_id']}> to <#{payload['new_channel_id']}>"
    )


def _stay(payload: dict[str, Any], why: str) -> str:
    """The log channel's line of a carry dropped: the posts stay where they are, and *why*."""
    return (
        f"ℹ️ The {payload['which']} of **{payload['division_name']}** stay in "
        f"<#{payload['old_channel_id']}> because {why}."
    )


def channel_move_change(
    seasons: "SeasonService", modules: "ModuleService", drawing: "ImageDrawing | None" = None,
    driver_names_read: AwaitedHook | None = None,
) -> ChangeType:
    """`results.channel.carry`: the bot's own change that carries a division's tables or standings
    from the channel they stood in to the one the division was moved to."""

    async def check(ctx: CheckContext) -> Verdict:
        payload = ctx.payload
        which = str(payload["which"])
        division_id = int(payload["division_id"])
        if not await modules.is_enabled(SWITCH_KEY):
            return Verdict.not_due(
                "results is off", line=_stay(payload, "the Results & Standings module is switched off")
            )
        season = await seasons.get_setup_or_active_season()
        if season is None:
            return Verdict.not_due(
                "no season", line=_stay(payload, "there is no season in progress")
            )
        if season.stage == SeasonStage.PENDING_COMPLETION:
            return Verdict.not_due(
                "pending completion", line=_stay(payload, "the season is pending completion")
            )
        division = next(
            (each for each in await seasons.get_divisions(season.id) if each.id == division_id), None
        )
        if division is None:
            return Verdict.not_due(
                "no division", line=_stay(payload, "the division is no longer in the season")
            )
        amendment = await open_amendment_in_division(ctx.db_path, division_id)
        if amendment is not None:
            return Verdict.not_due(
                "amendment open",
                line=_stay(
                    payload,
                    f"round {amendment['round_number']} is being amended in "
                    f"<#{amendment['channel_id']}>, so nothing is carried; run "
                    f"`{_SYNCS[which]}` once it ends to post them in "
                    f"<#{payload['new_channel_id']}>",
                ),
            )
        held = await held_channel(ctx.db_path, division_id, _COLUMNS[which])
        if held != int(payload["new_channel_id"]):
            now = "none" if held is None else f"<#{held}>"
            return Verdict.not_due(
                "channel changed",
                line=_stay(payload, f"the {which} channel has changed since: it is now {now}"),
            )
        refusal = await unusable_refusal(
            await league_guild(ctx.bot), int(payload["new_channel_id"]),
            (await channel_needs(drawing))[which], ChannelUse(which, division.name),
        )
        if refusal is not None:
            return Verdict.refuse(refusal)
        return Verdict.go()

    async def note_old_channel(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        payload = ctx.payload
        division_id, old = int(payload["division_id"]), int(payload["old_channel_id"])
        if payload["which"] == RESULTS:
            await db.execute(
                "UPDATE session_results SET results_message_channel_id = ? WHERE division_id = ? "
                "AND results_message_id IS NOT NULL AND results_message_channel_id IS NULL",
                (old, division_id),
            )
        else:
            for anchor, channel in (
                ("standings_message_id", "standings_message_channel_id"),
                ("constructor_standings_message_id", "constructor_standings_message_channel_id"),
            ):
                await db.execute(
                    f"UPDATE round_standings_posts SET {channel} = ? WHERE division_id = ? "  # noqa: S608
                    f"AND {anchor} IS NOT NULL AND {channel} IS NULL",
                    (old, division_id),
                )
        return StepResult()

    async def plan_carry(ctx: StepContext) -> StepResult:
        payload = ctx.payload
        results = payload["which"] == RESULTS
        async with get_connection(ctx.db_path) as db:
            planned = await review_posting.plan_division_posts(
                db, int(payload["division_id"]), division_name=str(payload["division_name"]),
                results_only=results, standings_only=not results,
            )
        return StepResult(result={"planned": len(planned)}, then=tuple(planned))

    async def close(_db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        left = review_posting.not_done(ctx)
        outcome = "Incomplete" if left else "Success"
        line = "\n".join((
            f"{ctx.named} | {ctx.what} | {outcome}",
            f"  division: {ctx.payload['division_name']}",
            f"  from: <#{ctx.payload['old_channel_id']}>",
            f"  to: <#{ctx.payload['new_channel_id']}>",
            *(f"  {each}" for each in left),
        ))
        return StepResult(result={"closed": True}, lines=(review_posting.with_drawn_notices(ctx, line),))

    async def describe_note(_ctx: StepContext) -> str:
        return "noting the channel the posts stand in"

    async def describe_plan(ctx: StepContext) -> str:
        return f"finding the {ctx.payload['which']} of {ctx.payload['division_name']} to carry"

    async def describe_close(_ctx: StepContext) -> str:
        return "recording the carry"

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    steps: dict[str, Step] = {
        **review_posting.posting_steps(drawing, driver_names_read),
        NOTE_OLD_CHANNEL: Step(
            NOTE_OLD_CHANNEL, StepKind.SAVE, note_old_channel, describe=describe_note
        ),
        PLAN_CARRY: Step(PLAN_CARRY, StepKind.ACT, plan_carry, describe=describe_plan),
        CLOSE: Step(CLOSE, StepKind.SAVE, close, describe=describe_close),
    }
    return ChangeType(
        kind=CHANNEL_CARRY,
        opening=(PlannedStep(NOTE_OLD_CHANNEL), PlannedStep(PLAN_CARRY), PlannedStep(CLOSE)),
        steps=steps,
        check=check,
        key=lambda payload: (
            f"{CHANNEL_CARRY}:{payload['division_id']}:{payload['which']}:"
            f"{payload['old_channel_id']}:{payload['new_channel_id']}"
        ),
        doing=carry_what,
        outcome=outcome,
    )


__all__ = [
    "CHANNEL_CARRY", "CLOSE", "NOTE_OLD_CHANNEL", "PLAN_CARRY", "RESULTS", "STANDINGS",
    "carry_what", "channel_move_change",
]
