"""The clean-up of a round's check-in, and the take-down of its call, on the change queue.

The clean-up is `attendance.check_in.clean_up`, asked by its timer a day after the round and by the
restart: its `plan` job reads the messages the round's check-in left recorded (the call, the last
notice and the announcement, each with the channel it was posted in), one `delete_message` job
takes each down, through the one delete step every standing post shares, and `mark_cleared` marks
the round's check-in over, naming in the log channel any message a league admin discarded the
deletion of, for deletion by hand. A refused deletion stops the queue. `mark_cleared` cannot be
discarded (#439, slice 8): dropped, the next restart would take the round's answers for a call
whose messages had not been cleared and delete them, so a Discard on it is refused and only a
Retry moves it.

The take-down is the one job (`take_down_call`) that takes a round's call down with the last notice
and the reserve announcement beside it, from the channel each was posted in (`check_in_deletes`),
for a cancellation (`take_down_called_off_on`, `take_down_steps`) and for an amendment, which
borrows the same deletion; the call's failures use it too (`forget_vanished_call`). The call's
record is forgotten by the job's own record, in the save that marks the job done
(`forget_check_in_call_on`), never where the messages are deleted.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any

import aiosqlite
import discord

from leaguebot.attendance.models.attendance import CHECK_IN_CLEAN_UP, RsvpEmbedMessage
from leaguebot.attendance.services.check_in.attendance_config_service import AttendanceService
from leaguebot.attendance.services.check_in.check_in_changes import (
    DELETE_MESSAGE,
    MARK_CLEARED,
    PLAN_CLEAN_UP,
    TAKE_DOWN_CALL,
    CallRow,
    call_row,
)
from leaguebot.attendance.services.check_in.check_in_record import checkin_record
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.cancellation import CalledOffSteps, CancellationRound
from leaguebot.core.models.change import (
    ChangeOrigin,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.posting.notice_handler import delete_notice
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
from leaguebot.core.utils.league_bot import LeagueBot

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.posting.output_router import OutputRouter

log = logging.getLogger(__name__)


#: The columns a check-in message is recorded in, each with the one naming the channel it was posted
#: in, so that a message deleted is forgotten by the column that held it.
_MESSAGE_COLUMNS = {
    "message_id": "channel_id",
    "last_notice_msg_id": "last_notice_channel_id",
    "distribution_msg_id": "distribution_channel_id",
}


_MESSAGE_WHAT = {
    "message_id": "check-in call",
    "last_notice_msg_id": "last check-in notice",
    "distribution_msg_id": "reserve announcement",
}


def _doing_clean_up(payload: dict[str, Any]) -> str:
    return (
        f"taking down the check-in of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}** a day after it"
    )


async def _forget_message(db: aiosqlite.Connection, payload: dict[str, Any]) -> None:
    """Forget a check-in message the clean-up took down, or whose deletion a league admin
    discarded, on *db* and committing nothing: the call's deletion drops the call's record, the
    others clear their id and channel from it."""
    column = str(payload["column"])
    where = "round_id = ? AND division_id = ? AND " + column + " = ?"
    args = (int(payload["round_id"]), int(payload["division_id"]), str(payload["message_id"]))
    if column == "message_id":
        await db.execute("DELETE FROM rsvp_embed_messages WHERE " + where, args)  # noqa: S608
        return
    await db.execute(  # noqa: S608 — the column is one of the three above
        f"UPDATE rsvp_embed_messages SET {column} = NULL, {_MESSAGE_COLUMNS[column]} = NULL "
        f"WHERE {where}",
        args,
    )


def _not_cleaned(row: CallRow | None) -> str | None:
    """Why the round is owed no clean-up, else None: unlike a call, a clean-up is owed a round
    whose results are in and whose check-in is over, and so is one that ended cancelled because
    every session was entered as not held (its call stands until the day after, as any round's);
    only a round called off is owed none, and a round whose check-in is marked over is owed no
    second: the restart's ask and the timer's both reach the queue, and whichever comes second
    finds the first done and is dropped as not due."""
    if row is None:
        return "the round is gone"
    if row["round_status"] == "CANCELLED" and not row["not_held"]:
        return "the round is cancelled"
    if row["division_status"] == "CANCELLED":
        return "the division is cancelled"
    if not row["attendance_on"]:
        return "the attendance module is off"
    if row["checkin_cleared"]:
        return "the round's check-in is over"
    return None


def check_in_clean_up_steps() -> dict[str, Step]:
    """The jobs of a round's check-in clean-up."""

    async def still_due(ctx: StepContext) -> bool:
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, int(ctx.payload["round_id"]))
        reason = _not_cleaned(row)
        if reason is not None:
            log.info("The check-in clean-up of round %s is no longer owed: %s.",
                     ctx.payload["round_id"], reason)
        return reason is None

    async def plan(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await call_row(db, round_id)
        if row is None:
            return StepResult(result={"dropped": True})
        cursor = await db.execute(
            "SELECT * FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
        )
        call = await cursor.fetchone()
        base = {
            "round_id": round_id, "division_id": row["division_id"],
            "division_name": row["division_name"], "round_number": row["round_number"],
        }
        deletes: list[PlannedStep] = []
        if call is not None:
            for column, channel_column in _MESSAGE_COLUMNS.items():
                if call[column] is None:
                    continue
                deletes.append(PlannedStep(DELETE_MESSAGE, {
                    **base, "column": column, "message_id": int(call[column]),
                    "channel_id": int(call[channel_column] or call["channel_id"]),
                    "what": _MESSAGE_WHAT[column],
                }))
        return StepResult(
            result={"planned": len(deletes)},
            then=(*deletes, PlannedStep(MARK_CLEARED)),
        )

    async def describe_plan(ctx: StepContext) -> str:
        return f"finding what is left of the check-in of round {ctx.payload.get('round_number', '?')}"

    async def record_delete(
        db: aiosqlite.Connection, ctx: StepContext, _result: StepResult
    ) -> None:
        await _forget_message(db, ctx.step_payload)

    async def describe_delete(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return (
            f"deleting the {payload.get('what', 'message')} of round "
            f"{payload.get('round_number', '?')} in **{payload.get('division_name', '?')}** from "
            f"<#{payload['channel_id']}>"
        )

    async def mark_cleared(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        left: list[str] = []
        for view in ctx.steps:
            if view.name == DELETE_MESSAGE and "discarded" in (view.result or {}):
                # Nothing would take it down again from its record, so the record goes with it.
                await _forget_message(db, view.payload)
                # Named by the link its refused deletion kept, for an admin to open; by its
                # channel where it kept none.
                link = (view.result or {}).get("link") or f"in <#{view.payload['channel_id']}>"
                left.append(f"{link} ({view.payload.get('what', 'message')})")
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = ?", (round_id,))
        lines: tuple[str, ...] = ()
        if left:
            lines = (
                f"ATTENDANCE | check-in clean-up | NOT DELETED\n"
                f"  division: {ctx.payload.get('division_name', '?')}\n"
                f"  round: {ctx.payload.get('round_number', '?')}\n"
                f"  messages: {', '.join(left)}\n"
                f"  note: Discord refused to delete them and a league admin discarded the job; "
                f"delete them by hand.",
            )
        return StepResult(result={"cleared": True}, lines=lines)

    async def describe_mark(ctx: StepContext) -> str:
        return f"marking the check-in of round {ctx.payload.get('round_number', '?')} over"

    async def would_lose_the_answers(ctx: StepContext) -> str:
        called = await season_service.round_called(ctx.db_path, int(ctx.payload["round_id"]))
        return (
            f"the check-in of {called} would never be marked over, and the next restart would "
            "delete every attendance answer of the round"
        )

    delete = dataclasses.replace(
        standing_posts.delete_replaced_step(DELETE_MESSAGE),
        record=record_delete, describe=describe_delete,
    )
    return {
        PLAN_CLEAN_UP: Step(PLAN_CLEAN_UP, StepKind.SAVE, plan, still_due=still_due,
                            describe=describe_plan),
        DELETE_MESSAGE: delete,
        MARK_CLEARED: Step(
            MARK_CLEARED, StepKind.SAVE, mark_cleared, describe=describe_mark,
            undiscardable=would_lose_the_answers,
        ),
    }


def check_in_clean_up_change() -> ChangeType:
    """`attendance.check_in.clean_up`: the bot's change that takes a round's check-in down a day
    after it. Not repeatable: a second request for a round still waiting is dropped."""

    async def check(ctx: CheckContext) -> Verdict:
        async with get_connection(ctx.db_path) as db:
            row = await call_row(db, int(ctx.payload["round_id"]))
        reason = _not_cleaned(row)
        return Verdict.go() if reason is None else Verdict.not_due(reason)

    def outcome(_ctx: OutcomeContext) -> str:
        return ""

    return ChangeType(
        kind=CHECK_IN_CLEAN_UP,
        opening=(PlannedStep(PLAN_CLEAN_UP),),
        steps=check_in_clean_up_steps(),
        check=check,
        key=lambda payload: f"{CHECK_IN_CLEAN_UP}:{payload['round_id']}",
        doing=_doing_clean_up,
        outcome=outcome,
    )


async def ask_check_in_clean_up(
    queue: ChangeQueue, db_path: str, round_id: int, *, origin: ChangeOrigin
) -> None:
    """Ask the queue to take *round_id*'s check-in down, as its timer does a day after the round.
    A round that is not there asks nothing; the change's check drops a request no longer owed."""
    async with get_connection(db_path) as db:
        row = await call_row(db, round_id)
    if row is None:
        log.error("Check-in clean-up: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
    }
    await queue.ask(CHECK_IN_CLEAN_UP, payload, what=_doing_clean_up(payload), origin=origin)


def earlier_messages(call: RsvpEmbedMessage) -> list[tuple[str, str]]:
    """Each message of *call*, with the channel it was posted in: the call, its last notice and
    its distribution announcement. A message with no channel recorded is the call's."""
    return [
        (message_id, channel_id or call.channel_id)
        for message_id, channel_id in (
            (call.message_id, call.channel_id),
            (call.last_notice_msg_id, call.last_notice_channel_id),
            (call.distribution_msg_id, call.distribution_channel_id),
        )
        if message_id is not None
    ]


def check_in_deletes(
    bot: LeagueBot, service: AttendanceService
) -> Callable[[int, int], Awaitable[bool]]:
    """Attendance's delete of a round's check-in call, as a cancellation's take-down and an
    amendment's ask it (`take_down_call`, through `take_down_steps`).

    Called with the round and the division it takes the call down with the last notice and the
    reserve announcement beside it, each from the channel it was posted in (the last two go to the
    division's check-in channel as it was when they were posted, which may not be the call's any
    more), and gives True where a call stood, False where none did. A message already gone is as
    good as deleted. Every message is tried; where any cannot be, it raises `StepFailedOnDiscord`
    whose `result` is `{"undeleted": [ids]}`, `from` the last Discord failure (none where a
    channel is gone), and leaves the call's record for the next try, which reads the ids again.
    The record is not forgotten here but by the job's record (`forget_check_in_call_on`), in the
    save that marks the job done. The answers are not touched: a repost carries them over.
    """

    async def deletes(round_id: int, division_id: int) -> bool:
        stored = await service.get_embed_message(round_id, division_id)
        if stored is None:
            return False
        left: list[str] = []
        cause: discord.HTTPException | None = None
        for message_id, channel_id in earlier_messages(stored):
            channel = as_text_channel(bot.get_channel(int(channel_id)))
            if channel is None:
                left.append(str(message_id))
                continue
            try:
                await delete_notice(channel.get_partial_message(int(message_id)))
            except discord.NotFound:
                pass  # Already gone, which is what was asked.
            except discord.HTTPException as exc:
                left.append(str(message_id))
                cause = exc
        if left:
            raise StepFailedOnDiscord(
                f"{len(left)} message(s) of the check-in call for round {round_id} could not be"
                f" deleted (ids {', '.join(left)})",
                result={"undeleted": left},
            ) from cause
        return True

    return deletes


async def forget_check_in_call_on(
    db: aiosqlite.Connection, round_id: int, division_id: int
) -> None:
    """Drop *round_id*'s check-in call record on *db*, committing nothing: the call, and with it
    the record of its last notice and announcement, which cannot be taken down from it again."""
    await db.execute(
        "DELETE FROM rsvp_embed_messages WHERE round_id = ? AND division_id = ?",
        (round_id, division_id),
    )


async def forget_vanished_call(
    ctx: StepContext,
    service: AttendanceService,
    router: OutputRouter,
    row: CallRow,
    round_id: int,
    division_id: int,
) -> Callable[[aiosqlite.Connection], Awaitable[None]]:
    """What a stop leaves of a check-in call nobody can see (its channel or its own message
    gone): what is left of it is deleted now, and the record it returns drops the call's record in
    the stop's save, queueing a line naming any message that could not be deleted, for deletion
    by hand. Reporting never masks the stop."""
    undeleted: list[str] = []
    try:
        await check_in_deletes(ctx.bot, service)(round_id, division_id)
    except StepFailedOnDiscord as exc:
        undeleted = [str(each) for each in (exc.result or {}).get("undeleted", [])]

    async def forget(db: aiosqlite.Connection) -> None:
        await forget_check_in_call_on(db, round_id, division_id)
        if undeleted:
            await router.queue_log_on(
                db,
                f"ATTENDANCE | check-in call | NOT DELETED\n"
                f"  division: {row['division_name']}\n"
                f"  round: {row['round_number']}\n"
                f"  messages: {', '.join(undeleted)}\n"
                f"  note: Discord refused to delete them as the call was taken down; delete "
                f"them by hand.",
            )

    return forget


async def take_down_call(
    bot: LeagueBot,
    division: Any,
    round_id: int,
    *,
    check_in_deletes: Callable[[int, int], Awaitable[bool]],
) -> dict[str, Any]:
    """Read *round_id*'s check-in, then take its call down, as a job on the change queue.

    *division* is anything with an `id` and a `name`. The call comes down through
    *check_in_deletes* (`check_in_deletes`): called with the round and the division, it gives True
    where a call stood. Its record is not forgotten here: the job's record does it, in the save
    that marks the job done.

    Returns `{"audit", "taken_down"}`: the log-channel record of the check-in (attendance's own
    `checkin_record`), read **before** the call comes down, and whether a call stood. Where a
    message cannot be deleted it raises `StepFailedOnDiscord` whose `result` is `{"audit",
    "undeleted"}`, so the audit is kept on the job whether it goes through or is discarded, and
    the call's record stays for the next try, which reads the audit again. The raise is `from`
    the Discord failure that caused it, as *check_in_deletes* raises it.
    """
    audit = await checkin_record(bot.db_path, division, round_id)
    try:
        taken_down = await check_in_deletes(round_id, division.id)
    except StepFailedOnDiscord as exc:
        undeleted = (exc.result or {}).get("undeleted", [])
        raise StepFailedOnDiscord(
            exc.reason, result={"audit": audit, "undeleted": undeleted}
        ) from exc.__cause__
    return {"audit": audit, "taken_down": taken_down}


async def take_down_called_off_on(
    db: aiosqlite.Connection, _scope: str, rounds: Iterable[CancellationRound]
) -> CalledOffSteps:
    """Plan the take-down of the check-in call of each of *rounds* that a cancellation calls off
    and that has one standing, reading attendance's own table on the save's connection and
    writing nothing. Subscribed to core's `rounds_called_off`.

    A call is taken down for a round in round order, planned for its division straight after the
    division's check-in notice (`take_down_steps` runs each).

    The job's own payload carries `call_target` ("check-in call"), attendance's own word for what
    a discard of it leaves undeleted, so that core's `not_notified` can name it without naming the
    job — whether or not the discarded result carries `undeleted` (a fault that is not Discord's,
    such as the division going missing, raises with no partial result to keep).
    """
    called_off = sorted(
        (each for each in rounds if each.called_off), key=lambda each: each.round_number
    )
    if not called_off:
        return CalledOffSteps()
    marks = ",".join("?" * len(called_off))
    cursor = await db.execute(
        f"SELECT DISTINCT round_id FROM rsvp_embed_messages WHERE round_id IN ({marks})",  # noqa: S608
        [each.round_id for each in called_off],
    )
    standing = {int(row["round_id"]) for row in await cursor.fetchall()}
    with_division: dict[int, list[PlannedStep]] = {}
    for each in called_off:
        if each.round_id in standing:
            with_division.setdefault(each.division_id, []).append(PlannedStep(TAKE_DOWN_CALL, {
                "division_id": each.division_id,
                "division_name": each.division_name,
                "season_id": each.season_id,
                "round_id": each.round_id,
                "round_number": each.round_number,
                "call_target": "check-in call",
            }))
    return CalledOffSteps(
        with_division={division_id: tuple(steps) for division_id, steps in with_division.items()}
    )


def take_down_steps(
    *,
    seasons: season_service.SeasonService,
    modules: ModuleService,
    deletes: Callable[[int, int], Awaitable[bool]],
) -> dict[str, Step]:
    """The step a cancellation takes a round's call down with, by name: `take_down_call` reads the
    check-in, deletes the call's messages through *deletes* (`check_in_deletes`) and, in the save
    that marks the job done, forgets the call's record. A job that fails to delete stops the
    queue; it is due only while *modules* has attendance on. The builder hands in *seasons* (the
    division the job names) and signs this up, with them bound, to core's `cancellation_steps`."""

    async def take_down(ctx: StepContext) -> StepResult:
        payload = ctx.step_payload
        division = next(
            (
                each
                for each in await seasons.get_divisions(int(payload["season_id"]))
                if each.id == int(payload["division_id"])
            ),
            None,
        )
        if division is None:
            raise LookupError(f"division {payload['division_id']} is no longer there")
        return StepResult(
            result=await take_down_call(
                ctx.bot, division, int(payload["round_id"]),
                check_in_deletes=deletes,
            )
        )

    async def forget_call(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """Drop the call's record once its messages are down, in the save that marks the job done."""
        if result.result.get("taken_down"):
            await forget_check_in_call_on(
                db, int(ctx.step_payload["round_id"]), int(ctx.step_payload["division_id"])
            )

    async def attendance_on(_ctx: StepContext) -> bool:
        return await modules.is_attendance_enabled()

    async def describe(ctx: StepContext) -> str:
        payload = ctx.step_payload
        return (
            f"taking down the check-in call of round {payload['round_number']} in "
            f"**{payload['division_name']}**"
        )

    return {
        TAKE_DOWN_CALL: Step(
            TAKE_DOWN_CALL, StepKind.ACT, take_down, still_due=attendance_on, record=forget_call,
            describe=describe,
        ),
    }
