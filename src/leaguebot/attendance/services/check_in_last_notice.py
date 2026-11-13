"""A round's last check-in notice on the change queue: the change `attendance.check_in.last_notice`.

The notice is asked by its timer: one `post_last_notice` job posts it in the division's check-in
channel as it then stands, naming the silent full-time drivers, and records its message and
channel with the job's mark so that the round's clean-up deletes it from there. It is owed a round
with a call standing and before the check-in deadline: asked late while the deadline is ahead, it
is posted late; once the deadline has passed, or no call stands, it is dropped.

A notice asked while a late call is still on the queue waits behind the call, and is judged again
as it starts. A step that `/test-mode advance` asks carries `advanced` in its payload and is judged
by no clock, the deadline that ends the check-in being the one advance fires; under test mode a
restart posts no late notice, a timer's ask of one more than five minutes past its moment being
left to the advance (`left_to_advance`).

The notice goes through core's notice handler (`send_notice`), stating the mentions it may notify
(architecture.md, "Posting to Discord"). What the call, the deadline and the clean-up share, the
round's row among it, is in `check_in_changes`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp
import aiosqlite
import discord

from leaguebot.attendance.models.attendance import CHECK_IN_CALL, CHECK_IN_LAST_NOTICE
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_config_service import AttendanceService
from leaguebot.attendance.services.check_in_changes import (
    POST_LAST_NOTICE,
    CallRow,
    call_row,
    deadline_of,
    left_to_advance,
    round_not_owed,
    scheduled_at_of,
    standing_call_row,
)
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.posting.notice_handler import send_notice
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.queue.queue_records import unfinished
from leaguebot.core.services.settings.channel_registry_service import as_text_channel

log = logging.getLogger(__name__)


def _doing_notice(payload: dict[str, Any]) -> str:
    return (
        f"posting the last check-in notice of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}**"
    )


def _notice_not_due(
    row: CallRow | None, standing: aiosqlite.Row | None, *, now: datetime,
    advanced: bool = False, call_in_hand: bool = False,
) -> str | None:
    """Why the round is owed no last notice, else None: it needs a call standing to remind of,
    and is posted late while the check-in deadline is ahead, never after it. A step *advanced* by
    test mode is judged by no clock: the deadline that ends the check-in is the one advance fires.

    A notice asked while the call it reminds of is *call_in_hand* on the queue, a late call a
    restart asked for, is owed all the same: it waits behind the call, and is judged again as it
    starts, when the call stands or has been given up."""
    reason = round_not_owed(row)
    if reason is not None:
        return reason
    if standing is None and not call_in_hand:
        return "no check-in call stands for the round"
    assert row is not None
    if not advanced and now >= deadline_of(row):
        return "the check-in deadline has passed"
    return None


def check_in_last_notice_steps(
    service: AttendanceService, *, now: Callable[[], datetime]
) -> dict[str, Step]:
    """The job of a last notice, *service* being attendance's and *now* the queue's clock."""

    async def still_due(ctx: StepContext) -> bool:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, round_id)
            standing = await standing_call_row(db, round_id)
        reason = _notice_not_due(
            row, standing, now=now(), advanced=bool(ctx.payload.get("advanced"))
        )
        if reason is not None:
            log.info("The last notice of round %s is no longer owed: %s.", round_id, reason)
        return reason is None

    async def post(ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, round_id)
        if row is None:
            raise LookupError(f"round {round_id} is no longer there")
        division_id: int = row["division_id"]
        division = row["division_name"]
        config = await service.get_division_config(division_id)
        if config is None or not config.rsvp_channel_id:
            raise StepFailedOnDiscord(
                f"**{division}** has no check-in channel set: set one with "
                f"`/attendance channel rsvp`, then press Retry."
            )
        channel = as_text_channel(ctx.bot.get_channel(int(config.rsvp_channel_id)))
        if channel is None:
            raise StepFailedOnDiscord(
                f"the configured RSVP channel ({config.rsvp_channel_id}) could not be reached: "
                f"set one with `/attendance channel rsvp`, then press Retry."
            )
        silent = await rsvp_service.silent_full_time_drivers(ctx.db_path, round_id, division_id)
        content = rsvp_service.last_notice_content(silent, int(scheduled_at_of(row).timestamp()))
        mentions = (
            discord.AllowedMentions(
                users=[discord.Object(id=int(each["discord_user_id"])) for each in silent]
            )
            if silent else discord.AllowedMentions.none()
        )
        try:
            message = await send_notice(channel, content, mentions=mentions)
        except (discord.HTTPException, aiohttp.ClientError, OSError) as exc:
            raise StepFailedOnDiscord(
                f"the last notice could not be posted: {str(exc) or type(exc).__name__}"
            ) from exc
        return StepResult(result={
            "new": [message.id], "channel_id": message.channel.id,
            "round_id": round_id, "division_id": division_id,
        })

    async def record(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        saved = result.result
        await service.update_embed_last_notice_msg_on(
            db, int(saved["round_id"]), int(saved["division_id"]),
            str(saved["new"][0]), str(saved["channel_id"]),
        )

    async def describe(ctx: StepContext) -> str:
        return _doing_notice(ctx.payload)

    return {
        POST_LAST_NOTICE: Step(POST_LAST_NOTICE, StepKind.ACT, post, still_due=still_due,
                               describe=describe, record=record),
    }


def check_in_last_notice_change(
    service: AttendanceService, *, now: Callable[[], datetime]
) -> ChangeType:
    """`attendance.check_in.last_notice`: the bot's change that posts a round's last check-in
    notice. Not repeatable: a round is owed one."""

    async def check(ctx: CheckContext) -> Verdict:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, round_id)
            standing = await standing_call_row(db, round_id)
            # Asked while a late call is still on the queue, the notice waits behind it.
            call_in_hand = ctx.change_id is None and standing is None and any(
                int(each["round_id"]) == round_id
                for each in await unfinished(ctx.db_path, [CHECK_IN_CALL])
            )
            reason = _notice_not_due(
                row, standing, now=now(), advanced=bool(ctx.payload.get("advanced")),
                call_in_hand=call_in_hand,
            )
            if reason is None:
                reason = await left_to_advance(db, row, "rsvp_last_notice", ctx, now=now())
        return Verdict.go() if reason is None else Verdict.not_due(reason)

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=CHECK_IN_LAST_NOTICE,
        opening=(PlannedStep(POST_LAST_NOTICE),),
        steps=check_in_last_notice_steps(service, now=now),
        check=check,
        key=lambda payload: f"{CHECK_IN_LAST_NOTICE}:{payload['round_id']}",
        doing=_doing_notice,
        outcome=outcome,
    )


async def ask_check_in_last_notice(
    queue: ChangeQueue, db_path: str, round_id: int, *, origin: ChangeOrigin
) -> None:
    """Ask the queue to post *round_id*'s last check-in notice, as its timer does. A round that
    is not there asks nothing; the change's check drops a request no longer owed."""
    async with get_connection(db_path) as db:
        row = await call_row(db, round_id)
    if row is None:
        log.error("Last check-in notice: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
    }
    await queue.ask(CHECK_IN_LAST_NOTICE, payload, what=_doing_notice(payload), origin=origin)
