"""A round's check-in deadline on the change queue: the change `attendance.check_in.deadline`.

The deadline is asked by its timer and the restart. Its `distribute` job places the reserves on
the answers as they stood at the deadline (an answer saved after it is left out) and marks the
call as run, in the one save; then `lock_call` takes the buttons off the call, and `announce`
posts the placements, or the notice that none were needed, in the division's check-in channel as
it then stands, recorded with that channel. A round whose deadline falls with no call standing has
`close_without_call` instead, which closes its check-in, clears the answers kept for it and asks
the clean-up.

A refused lock or announcement stops the queue; a discarded announcement leaves the placements,
and is not run again, since the call's mark says the deadline has run. `distribute` cannot be
discarded: dropped, the reserves would never be placed, so a Discard on it is refused and only a
Retry moves it.

A step that `/test-mode advance` asks carries `advanced` in its payload, and its deadline places
the reserves on every answer held. What the call, the last notice and the clean-up share, the
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

from leaguebot.attendance.models.attendance import CHECK_IN_CLEAN_UP, CHECK_IN_DEADLINE
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_config_service import AttendanceService
from leaguebot.attendance.services.check_in_changes import (
    ANNOUNCE,
    CLOSE_WITHOUT_CALL,
    DISTRIBUTE,
    LOCK_CALL,
    CallRow,
    call_row,
    close_check_in_on,
    deadline_of,
    round_not_owed,
)
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.posting.notice_handler import edit_notice, send_notice
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.seasons import season_service
from leaguebot.core.services.settings.channel_registry_service import as_text_channel

log = logging.getLogger(__name__)


def _doing_deadline(payload: dict[str, Any]) -> str:
    return (
        f"running the check-in deadline of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}**"
    )


def _deadline_not_due(
    row: CallRow | None, stored: aiosqlite.Row | None
) -> str | None:
    """Why the round is owed no deadline run, else None: a deadline runs once for each call."""
    reason = round_not_owed(row)
    if reason is not None:
        return reason
    if stored is not None and stored["deadline_run_at"] is not None:
        return "the call's deadline has already run"
    return None


def check_in_deadline_steps(
    service: AttendanceService, *, now: Callable[[], datetime]
) -> dict[str, Step]:
    """The jobs of a round's check-in deadline, *service* being attendance's and *now* the queue's
    clock."""

    async def still_due(ctx: StepContext) -> bool:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, round_id)
        # Once the distribution has marked the call, what is left (the lock, the announcement) is
        # owed whatever the round's state, unless it has been called off.
        reason = round_not_owed(row) if ctx.step_payload.get("distributed") is None else (
            None if row is not None and row["round_status"] != "CANCELLED"
            and row["attendance_on"] else "the round is off"
        )
        if reason is not None:
            log.info("The check-in deadline of round %s is no longer owed: %s.", round_id, reason)
        return reason is None

    async def distribute(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await call_row(db, round_id)
        if row is None:
            return StepResult(result={"dropped": True})
        cursor = await db.execute(
            "SELECT * FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
        )
        stored = await cursor.fetchone()
        if stored is None:
            return StepResult(result={"no_call": True}, then=(PlannedStep(CLOSE_WITHOUT_CALL),))
        if stored["deadline_run_at"] is not None:
            return StepResult(result={"dropped": True})
        division_id: int = row["division_id"]
        assignments, standby = await rsvp_service.reserve_distribution_on(
            db, round_id, division_id,
            answered_by=None if ctx.payload.get("advanced") else deadline_of(row),
        )
        await rsvp_service.write_distribution_on(
            db, round_id, division_id, assignments, standby, at=now()
        )
        placed = bool(assignments or standby)
        base = {"round_id": round_id, "division_id": division_id, "distributed": True}
        return StepResult(
            result={"placed": placed, "assigned": len(assignments), "standby": len(standby)},
            then=(
                PlannedStep(LOCK_CALL, {
                    **base, "channel_id": int(stored["channel_id"]),
                    "message_id": int(stored["message_id"]),
                }),
                PlannedStep(ANNOUNCE, {**base, "placed": placed}),
            ),
        )

    async def describe_distribute(ctx: StepContext) -> str:
        return (
            f"placing the reserves of round {ctx.payload.get('round_number', '?')} in "
            f"**{ctx.payload.get('division_name', '?')}**"
        )

    async def close_without_call(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await call_row(db, round_id)
        cursor = await db.execute(
            "SELECT 1 FROM driver_round_attendance WHERE round_id = ? LIMIT 1", (round_id,)
        )
        # Only a round holding answers kept from an earlier call is touched: with none there is
        # nothing to close, and the check-in is left as it stood.
        if row is None or await cursor.fetchone() is None or not await close_check_in_on(
            db, round_id
        ):
            log.info("round %d's deadline passed with no call standing — nothing done", round_id)
            return StepResult(result={"dropped": True})
        log.info("round %d's deadline passed with no call standing — answers cleared", round_id)
        line = rsvp_service.call_failure_text(
            division_id=row["division_id"],
            division_name=row["division_name"],
            season_number=row["season_number"],
            round_number=row["round_number"],
            reason="the round's check-in deadline passed with no call standing",
            note=(
                "the answers given to an earlier call of this round no longer count, so they were "
                "cleared. No attendance rows are left, and this round will count nothing against "
                "anyone."
            ),
        )
        return StepResult(
            result={"closed": True},
            lines=(line,),
            follow_ons=(FollowOn(
                CHECK_IN_CLEAN_UP, dict(ctx.payload),
                f"taking down the check-in of round {row['round_number']} in "
                f"**{row['division_name']}**, its deadline having passed with no call",
            ),),
        )

    async def describe_close(ctx: StepContext) -> str:
        return (
            f"closing the check-in of round {ctx.payload.get('round_number', '?')} in "
            f"**{ctx.payload.get('division_name', '?')}**, which has no call"
        )

    async def lock(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        channel = as_text_channel(ctx.bot.get_channel(int(payload["channel_id"])))
        if channel is None:
            return StepResult(result={"gone": True}, lines=(_gone_call_line(ctx),))
        # A message gone raises NotFound, which completes the job with its gone line.
        message = await channel.fetch_message(int(payload["message_id"]))
        embed = await rsvp_service._rebuild_embed_for_round(
            int(payload["round_id"]), int(payload["division_id"]), ctx.db_path, service
        )
        try:
            await edit_notice(message, mentions=discord.AllowedMentions.none(), embed=embed, view=None)
        except (discord.HTTPException, aiohttp.ClientError, OSError) as exc:
            if isinstance(exc, discord.NotFound):
                raise
            raise StepFailedOnDiscord(
                f"the call's buttons could not be taken off: {str(exc) or type(exc).__name__}",
            ) from exc
        return StepResult(result={"locked": True})

    async def describe_lock(ctx: StepContext) -> str:
        return (
            f"taking the buttons off the check-in call of round "
            f"{ctx.payload.get('round_number', '?')} in <#{ctx.step_payload['channel_id']}>"
        )

    async def announce(ctx: StepContext) -> StepResult:
        round_id = int(ctx.step_payload["round_id"])
        division_id = int(ctx.step_payload["division_id"])
        division = ctx.payload.get("division_name", "?")
        if ctx.step_payload.get("placed"):
            composed = await rsvp_service.distribution_announcement(
                ctx.db_path, round_id, division_id
            )
            if composed is None:
                return StepResult(result={"none": True})
            text, named = composed
            mentions = discord.AllowedMentions(users=[discord.Object(id=each) for each in named])
        else:
            text, mentions = rsvp_service.NO_RESERVE_NOTICE, discord.AllowedMentions.none()
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
        try:
            message = await send_notice(channel, text, mentions=mentions)
        except (discord.HTTPException, aiohttp.ClientError, OSError) as exc:
            raise StepFailedOnDiscord(
                f"the reserve announcement could not be posted: {str(exc) or type(exc).__name__}"
            ) from exc
        return StepResult(result={
            "new": [message.id], "channel_id": message.channel.id,
            "round_id": round_id, "division_id": division_id,
        })

    async def record_announcement(
        db: aiosqlite.Connection, ctx: StepContext, result: StepResult
    ) -> None:
        saved = result.result
        if "new" not in saved:
            return
        await service.update_embed_distribution_msg_on(
            db, int(saved["round_id"]), int(saved["division_id"]),
            str(saved["new"][0]), str(saved["channel_id"]),
        )

    async def describe_announce(ctx: StepContext) -> str:
        return (
            f"posting the reserve announcement of round {ctx.payload.get('round_number', '?')} "
            f"in **{ctx.payload.get('division_name', '?')}**"
        )

    async def would_never_be_placed(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return f"the reserves of {called} would never be placed"

    return {
        DISTRIBUTE: Step(DISTRIBUTE, StepKind.SAVE, distribute, still_due=still_due,
                         describe=describe_distribute, undiscardable=would_never_be_placed),
        CLOSE_WITHOUT_CALL: Step(CLOSE_WITHOUT_CALL, StepKind.SAVE, close_without_call,
                                 still_due=still_due, describe=describe_close),
        LOCK_CALL: Step(LOCK_CALL, StepKind.EDIT, lock, still_due=still_due,
                        describe=describe_lock, gone_line=_gone_call_line),
        ANNOUNCE: Step(ANNOUNCE, StepKind.ACT, announce, still_due=still_due,
                       describe=describe_announce, record=record_announcement),
    }


def _gone_call_line(ctx: StepContext) -> str:
    return (
        f"ATTENDANCE | check-in deadline | CALL GONE\n"
        f"  division: {ctx.payload.get('division_name', '?')}\n"
        f"  round: {ctx.payload.get('round_number', '?')}\n"
        f"  note: the check-in call was deleted before its buttons could be taken off; the "
        f"placements stand."
    )


def check_in_deadline_change(
    service: AttendanceService, *, now: Callable[[], datetime]
) -> ChangeType:
    """`attendance.check_in.deadline`: the bot's change that runs a round's check-in deadline.
    Not repeatable: a deadline runs once for each call."""

    async def check(ctx: CheckContext) -> Verdict:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, round_id)
            stored = await _standing_with_mark(db, round_id)
        reason = _deadline_not_due(row, stored)
        return Verdict.go() if reason is None else Verdict.not_due(reason)

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=CHECK_IN_DEADLINE,
        opening=(PlannedStep(DISTRIBUTE),),
        steps=check_in_deadline_steps(service, now=now),
        check=check,
        key=lambda payload: f"{CHECK_IN_DEADLINE}:{payload['round_id']}",
        doing=_doing_deadline,
        outcome=outcome,
    )


async def _standing_with_mark(db: aiosqlite.Connection, round_id: int) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT message_id, deadline_run_at FROM rsvp_embed_messages WHERE round_id = ?",
        (round_id,),
    )
    return await cursor.fetchone()


async def ask_check_in_deadline(
    queue: ChangeQueue, db_path: str, round_id: int, *, origin: ChangeOrigin
) -> None:
    """Ask the queue to run *round_id*'s check-in deadline, as its timer does. A round that is not
    there asks nothing; the change's check drops a request no longer owed."""
    async with get_connection(db_path) as db:
        row = await call_row(db, round_id)
    if row is None:
        log.error("Check-in deadline: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
    }
    await queue.ask(CHECK_IN_DEADLINE, payload, what=_doing_deadline(payload), origin=origin)
