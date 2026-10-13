"""A division's attendance sheet, asked of the change queue (#439, slice 6).

A command that makes its own change and wants the sheet posted after it (`/attendance sync`, and
the sanctions it enforces) asks the queue, as the bot's and repeatably, to post it
(`attendance.sheet.post`): the command's reply says what it did, and the post follows. A sheet
Discord refuses stops the queue at the `post_sheet` job, to be tried again as text (Constitution
XIV, rule 8) or discarded, instead of being put on the old retry queue. A division with no
attendance channel set stops it too, naming the command that sets one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import aiosqlite

from leaguebot.attendance.services import attendance_service
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
from leaguebot.core.utils.league_server import league_guild

POST_SHEET = "post_sheet"
SHEET_POST = "attendance.sheet.post"


def sheet_step() -> Step:
    """The job that posts a division's attendance sheet for a round.

    The payload is `{"division_id", "division_name", "round_id", "sanctioned"}`, `sanctioned`
    being the ids of the driver profiles the sheet marks as having reached the limit. The first
    try posts as configured (the picture where the image module has one); a try again posts the
    text. The job returns the sheet posted, saves its id as the division's live sheet with its
    mark (`record_sheet_on`) and plans, where the sheet takes the slot, the deletion of the one it
    replaces; a sheet discarded plans none, so the earlier one stands.
    """

    async def run(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        guild = await league_guild(ctx.bot)
        if guild is None:
            raise GuildUnavailable("the league's server is not in the cache")
        payload = ctx.step_payload or ctx.payload
        sanctioned = {int(each) for each in payload.get("sanctioned") or []}
        posted = await attendance_service.post_attendance_sheet(
            ctx.bot, guild, ctx.db_path, int(payload["round_id"]), int(payload["division_id"]),
            sanctioned_profile_ids=sanctioned or None, as_text=ctx.tries > 0,
        )
        result = attendance_service.sheet_result(posted)
        then: tuple[PlannedStep, ...] = ()
        if posted is not None and posted.takes_slot:
            then = standing_posts.replaced(
                posted.channel_id, posted.previous, posted.message_id,
                what="attendance sheet",
                division_name=str(payload.get("division_name", "the division")),
            )
        return StepResult(result=result, then=then)

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        payload = ctx.step_payload or ctx.payload
        await attendance_service.record_sheet_on(db, int(payload["division_id"]), result.result)

    async def still_due(ctx: StepContext) -> bool:
        return await ctx.bot.module_service.is_attendance_enabled()

    async def describe(ctx: StepContext) -> str:
        payload = ctx.step_payload or ctx.payload
        return f"posting the attendance sheet of **{payload.get('division_name', 'the division')}**"

    return Step(
        POST_SHEET, StepKind.ACT, run, still_due=still_due, describe=describe, record=record,
    )


def sheet_post_change() -> ChangeType:
    """`attendance.sheet.post`: the bot's change, repeatable, that posts one division's sheet.

    The check drops the request where attendance is off, the division is gone or the round
    named is cancelled, and refuses where the division has no attendance channel set: the bot's
    change so refused stops the queue with the reason, to be put right and retried.
    """

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            enabled = await (await db.execute(
                "SELECT module_enabled FROM attendance_config"
            )).fetchone()
            division = await (await db.execute(
                "SELECT d.name, c.attendance_channel_id FROM divisions d "
                "LEFT JOIN attendance_division_config c ON c.division_id = d.id WHERE d.id = ?",
                (int(ctx.payload["division_id"]),),
            )).fetchone()
            round_row = await (await db.execute(
                "SELECT status FROM rounds WHERE id = ?", (int(ctx.payload["round_id"]),)
            )).fetchone()
        if not (enabled and enabled["module_enabled"]):
            return Verdict.not_due("attendance is off")
        if division is None:
            return Verdict.not_due("the division is gone")
        if round_row is not None and round_row["status"] == "CANCELLED":
            return Verdict.not_due("the round was cancelled")
        if not division["attendance_channel_id"]:
            return Verdict.refuse(
                f"**{division['name']}** has no attendance channel set: set one with "
                f"`/attendance channel attendance`, then press Retry."
            )
        return Verdict.go()

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=SHEET_POST,
        opening=(PlannedStep(POST_SHEET),),
        steps={POST_SHEET: sheet_step(), standing_posts.DELETE_REPLACED: standing_posts.delete_replaced_step()},
        check=check,
        key=lambda payload: f"{SHEET_POST}:{payload['division_id']}",
        doing=lambda payload: (
            f"posting the attendance sheet of **{payload.get('division_name', 'the division')}**"
        ),
        outcome=outcome,
        repeatable=True,
    )


async def ask_sheet_post(
    queue: ChangeQueue,
    db_path: str,
    division_id: int,
    round_id: int,
    sanctioned: Iterable[int] = (),
) -> None:
    """Ask the queue to post the attendance sheet of *division_id* for *round_id*, marking the
    driver profiles in *sanctioned* as having reached the limit.

    A command which makes its own change and wants a standing post after it asks the queue for
    that post as a change of the bot's, of a kind that may be repeated (slice 6's decision 2); the
    command's reply is unchanged and the post follows. The division's name is read here, on a
    connection of its own.
    """
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT name FROM divisions WHERE id = ?", (division_id,)
        )).fetchone()
    name = row["name"] if row is not None else str(division_id)
    payload: dict[str, Any] = {
        "division_id": division_id, "division_name": name, "round_id": round_id,
        "sanctioned": sorted(int(each) for each in sanctioned),
    }
    await queue.ask(
        SHEET_POST, payload, what=f"posting the attendance sheet of **{name}**",
        origin=ChangeOrigin.BOT,
    )
