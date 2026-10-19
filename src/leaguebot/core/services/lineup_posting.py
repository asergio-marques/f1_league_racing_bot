"""A division's lineup, asked of the change queue (#439, slice 6).

A command that changes the drivers (`/driver assign`, `unassign`, `move`, `release`, `sack`, the
mid-season confirmation of placements) makes its own change and then asks the queue, as the bot's
and repeatably, to post each lineup it touched (`lineup.post`): its reply says what the command
did, and the post follows. A lineup Discord refuses stops the queue at the `post_lineup` job, to be
tried again as text (Constitution XIV, rule 8) or discarded, instead of being lost. A division
with no lineup channel set stops it too, naming the command that sets one.

The post and the deletion of the lineup it replaces are two jobs (`standing_posts`): the post job
saves the new id and the audit with its mark, and plans the deletion after it. The same job
(`lineup_step`) posts the lineups of a season's approval; a review's `refresh_lineup` reaches the
same post through the attendance hook.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    GuildUnavailable,
    PlannedStep,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services import standing_posts
from leaguebot.core.services.audit_service import record_change_on
from leaguebot.core.utils.league_server import league_guild

if TYPE_CHECKING:
    from leaguebot.core.services.placement_service import LineupPosted, PlacementService

POST_LINEUP = "post_lineup"
LINEUP_POST = "lineup.post"

_LIVE = ("SETUP", "ACTIVE")


def posted_result(posted: "LineupPosted | None") -> dict[str, Any]:
    """What a lineup post job returns: the new message and the one it replaces, or an empty
    result where nothing was posted (the division is gone, or its picture is not posted by this
    job)."""
    if posted is None:
        return {}
    return {
        "new": posted.message_id, "old": posted.previous, "channel_id": posted.channel_id,
        "division_name": posted.division_name, "form": posted.form,
    }


def _then(result: Mapping[str, Any] | None) -> tuple[PlannedStep, ...]:
    """The `delete_replaced` job for the lineup a post replaced, in `result`'s own words."""
    if not result or result.get("new") is None:
        return ()
    return standing_posts.replaced(
        result.get("channel_id"), result.get("old"), result.get("new"),
        what="lineup", division_name=str(result.get("division_name") or "the division"),
    )


async def record_lineup_on(
    db: aiosqlite.Connection, ctx: StepContext, result: StepResult
) -> None:
    """Save the new lineup message on the division, and the audit of the posting, in the save
    that marks the post job done (#439). Nothing is committed here.

    The audit is the system's (actor 0), as both paths wrote it before; a picture says so.
    """
    new = result.result.get("new")
    if new is None:
        return
    division_id = int(ctx.step_payload.get("division_id") or ctx.payload["division_id"])
    await db.execute(
        "UPDATE divisions SET lineup_message_id = ? WHERE id = ?", (int(new), division_id)
    )
    value: dict[str, Any] = {
        "channel_id": result.result.get("channel_id"),
        "division": result.result.get("division_name"),
    }
    if result.result.get("form") == "image":
        value["form"] = "image"
    await record_change_on(
        db, actor_id=0, actor_name="system", change_type="SIGNUP_LINEUP_POSTED",
        old_value={}, new_value=value, now=datetime.now(timezone.utc), division_id=division_id,
    )


def lineup_step(
    placement: "PlacementService", *, name: str = POST_LINEUP
) -> Step:
    """The job that posts a division's lineup, the job's own `division_id` or else the change's.

    The first try posts as configured (the picture where the image module has one); a try
    again posts the text. A division with no lineup channel raises, naming the command that sets it. A try after a save
    that failed removes the copy it sent first. The job returns the message posted and the one it
    replaces, saves the new one with its mark (`record_lineup_on`) and plans the deletion of the
    old one (`standing_posts`).
    """

    def value_of(ctx: StepContext, key: str, default: Any = None) -> Any:
        """The job's own value, else the change's."""
        return ctx.step_payload.get(key, ctx.payload.get(key, default))

    async def run(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        posted = await placement.refresh_lineup(
            guild, int(value_of(ctx, "division_id")), as_text=ctx.tries > 0
        )
        result = posted_result(posted)
        return StepResult(result=result, then=_then(result))

    async def describe(ctx: StepContext) -> str:
        return f"posting the lineup of **{value_of(ctx, 'division_name', 'the division')}**"

    return Step(name, StepKind.ACT, run, describe=describe, record=record_lineup_on)


def lineup_post_change(placement: "PlacementService") -> ChangeType:
    """`lineup.post`: the bot's change, repeatable, that posts one division's lineup.

    The check drops the request where the division is gone or its season is no longer live, and
    refuses where it has no lineup channel set: the bot's change so refused stops the queue with
    the reason, to be put right and retried.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            cursor = await db.execute(
                "SELECT d.name, d.lineup_channel_id, s.status FROM divisions d "
                "JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (int(ctx.payload["division_id"]),),
            )
            row = await cursor.fetchone()
        if row is None or row["status"] not in _LIVE:
            return Verdict.not_due("the division is no longer in a live season")
        if row["lineup_channel_id"] is None:
            return Verdict.refuse(
                f"**{row['name']}** has no lineup channel set: set one with "
                f"`/division lineup-channel`, then press Retry."
            )
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=LINEUP_POST,
        opening=(PlannedStep(POST_LINEUP),),
        steps={
            POST_LINEUP: lineup_step(placement),
            standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step(),
        },
        check=check,
        key=lambda payload: f"{LINEUP_POST}:{payload['division_id']}",
        doing=lambda payload: f"posting the lineup of **{payload.get('division_name', 'the division')}**",
        outcome=outcome,
        repeatable=True,
    )


async def ask_lineup_posts(
    queue: ChangeQueue, db_path: str, division_ids: Sequence[int]
) -> None:
    """Ask the queue to post the lineup of each division in *division_ids*, in the order given and
    each once.

    A command which makes its own change and wants a standing post after it asks the queue for
    that post as a change of the bot's, of a kind that may be repeated (slice 6's decision 2); the
    command's reply is unchanged and the post follows. The names are read here, on a connection of
    its own.
    """
    wanted = list(dict.fromkeys(int(each) for each in division_ids))
    if not wanted:
        return
    async with get_connection(db_path) as db:
        names: dict[int, str] = {}
        for division_id in wanted:
            cursor = await db.execute("SELECT name FROM divisions WHERE id = ?", (division_id,))
            row = await cursor.fetchone()
            names[division_id] = row["name"] if row is not None else str(division_id)
    for division_id in wanted:
        payload: dict[str, Any] = {"division_id": division_id, "division_name": names[division_id]}
        await queue.ask(
            LINEUP_POST,
            payload,
            what=f"posting the lineup of **{names[division_id]}**",
            origin=ChangeOrigin.BOT,
        )
