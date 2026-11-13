"""A round's check-in call on the change queue (#439, slice 6b): asked by its timer, a restart or
`/attendance post-check-in`, posted and recorded by the call's own jobs.

The change is `attendance.check_in.call`. Its jobs are:

- `give_up` (a save), the change's opening job: the moment is judged here, when the change runs
  and not when it was asked, since a queue stopped for hours can bring a call up to run after its
  deadline. Before the round's check-in deadline it plans the call; at or past it, no call is
  posted (a call nobody could answer would record every driver as not having answered): the
  round's check-in is closed (``checkin_cleared``), any answers left for it and the reserve
  placements made on them are cleared, and the log channel is told, all in the one save
  (attendance spec :121).
- `post_call`: the call is posted (the graphic on the first try alone, text on every later one,
  Constitution XIV r8) and recorded with the job's mark: its message and channel, and a NO_RSVP row
  for each of the division's seated drivers. A call refused, a check-in channel unset or gone,
  stops the queue, the notice saying what is left for the round (`left_for_the_queue`). A call that
  replaces one standing plans a `delete_earlier` job for each of the earlier call's messages, after
  it, so the earlier call comes down only once the new one has landed and is recorded.

A round is owed one call: a call asked of a round that has one standing, a cancelled round, a round
whose results are in or whose check-in is over, or with attendance off is dropped, judged when it
is asked and again when it runs. A call is not repeatable.

A step that `/test-mode advance` asks carries `advanced` in its payload and is judged by no clock:
a past-dated round's call is posted rather than given up, its last notice is not dropped for the
real deadline, and its deadline places the reserves on every answer held. Under test mode the step
that ends a check-in is the one advance fires.

The last notice is `attendance.check_in.last_notice`, asked by its timer: one `post_last_notice`
job posts it in the division's check-in channel as it then stands, naming the silent full-time
drivers, and records its message and channel with the job's mark so that the round's clean-up
deletes it from there. It is owed a round with a call standing and before the check-in deadline:
asked late while the deadline is ahead, it is posted late; once the deadline has passed, or no call
stands, it is dropped.

The deadline is `attendance.check_in.deadline`, asked by its timer and the restart. Its `distribute`
job places the reserves on the answers as they stood at the deadline (an answer saved after it is
left out) and marks the call as run, in the one save; then `lock_call` takes the buttons off the
call, and `announce` posts the placements, or the notice that none were needed, in the division's
check-in channel as it then stands, recorded with that channel. A round whose deadline falls with
no call standing has `close_without_call` instead, which closes its check-in, clears the answers
kept for it and asks the clean-up. A refused lock or announcement stops the queue; a discarded
announcement leaves the placements, and is not run again, since the call's mark says the deadline
has run. `distribute` cannot be discarded (#439, slice 8): dropped, the
reserves would never be placed, so a Discard on it is refused and only a Retry moves it.

The clean-up is `attendance.check_in.clean_up`, asked by its timer a day after the round and by the
restart: its `plan` job reads the messages the round's check-in left recorded (the call, the last
notice and the announcement, each with the channel it was posted in), one `delete_message` job
takes each down, through the one delete step every standing post shares, and `mark_cleared` marks
the round's check-in over, naming in the log channel any message a league admin discarded the
deletion of, for deletion by hand. A refused deletion stops the queue. `mark_cleared` cannot be discarded
(#439, slice 8): dropped, the next restart would take the round's answers for a call whose
messages had not been cleared and delete them, so a Discard on it is refused and only a Retry
moves it.

The posts of a check-in (the call, and the last notice and announcement that follow) go through
`post_check_in_message`, the module's one send site, which keeps the mentions the post may notify
stated where it is sent; the call's edit goes through `edit_check_in_message`.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
import aiosqlite
import discord

from leaguebot.attendance.models.attendance import AttendanceConfig, RsvpEmbedMessage
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_config_service import AttendanceService
from leaguebot.attendance.services.module_switch_settings import attendance_enabled_on
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.cancellation import CalledOffSteps, CancellationRound
from leaguebot.core.models.change import (
    CHECK_IN_CALL,
    CHECK_IN_CLEAN_UP,
    CHECK_IN_DEADLINE,
    CHECK_IN_LAST_NOTICE,
    ChangeOrigin,
    FollowOn,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.attendance.services.check_in_record import checkin_record
from leaguebot.core.models.round import (
    ROUND_CANCELLABLE,
    AttendanceWindows,
    Round,
    RoundFormat,
    read_scheduled_at,
)
from leaguebot.core.services.seasons import season_service
from leaguebot.core.services.posting import standing_posts
from leaguebot.core.services.queue.queue_records import unfinished
from leaguebot.core.services.seasons.season_end_changes import test_mode_on
from leaguebot.core.services.queue.change_queue import (
    ChangeQueue,
)
from leaguebot.core.services.queue.change_types import (
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.settings.channel_registry_service import as_text_channel
from leaguebot.core.services.posting.notice_handler import delete_notice
from leaguebot.core.services.rounds.round_moments import clean_up_moment, round_event_moments
from leaguebot.core.services.rounds.round_steps import judged_windows, named_now
from leaguebot.core.services.test_mode.test_mode_service import (
    AdvanceOutcome,
    AdvanceStep,
    AdvanceStepFailed,
    PhaseEntry,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.in_a_job import posts_in_a_job

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.posting.output_router import OutputRouter

log = logging.getLogger(__name__)

#: How the buttons of a call are built for a round's id. A service may not import the cog that
#: owns them, so wiring hands this to the jobs that post or redraw a call.
CheckInViewFactory = Callable[[int], discord.ui.View]

GIVE_UP = "give_up"
POST_CALL = "post_call"
DELETE_EARLIER = "delete_earlier"
POST_LAST_NOTICE = "post_last_notice"
DISTRIBUTE = "distribute"
CLOSE_WITHOUT_CALL = "close_without_call"
LOCK_CALL = "lock_call"
ANNOUNCE = "announce"
PLAN_CLEAN_UP = "plan"
DELETE_MESSAGE = "delete_message"
MARK_CLEARED = "mark_cleared"
#: The job the amendment plans to take a standing check-in down a day after the round.
CLEAN_UP_CHECK_IN = "clean_up_check_in"
#: Taking a round's call down when a cancellation calls the round off (`take_down_steps`).
TAKE_DOWN_CALL = "take_down_call"


def _value(ctx: StepContext, key: str, default: Any = None) -> Any:
    """The job's own value, else the change's."""
    return ctx.step_payload.get(key, ctx.payload.get(key, default))


def _doing_notice(payload: dict[str, Any]) -> str:
    return (
        f"posting the last check-in notice of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}**"
    )


def _what(payload: dict[str, Any]) -> str:
    """The call, as a line names it mid-sentence."""
    return (
        f"posting the check-in call of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}**"
    )


def _doing(payload: dict[str, Any]) -> str:
    """The call, as the acknowledgement and the repeat refusal open with it: capitalised, as
    every member-facing *doing* is."""
    what = _what(payload)
    return what[:1].upper() + what[1:]


@posts_in_a_job
async def post_check_in_message(
    channel: discord.TextChannel,
    *,
    content: str | None,
    mentions: discord.AllowedMentions,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None,
) -> discord.Message:
    """Post one message of a round's check-in, the module's one send site.

    *mentions* is who the message may notify, stated by the caller and sent with it: the
    division's role for a call, the silent drivers for a last notice.
    """
    kwargs: dict[str, Any] = {"content": content, "allowed_mentions": mentions}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    return await channel.send(**kwargs)


@posts_in_a_job
async def edit_check_in_message(
    message: discord.Message, *, embed: discord.Embed, view: discord.ui.View | None
) -> None:
    """Edit one message of a round's check-in, the module's one edit site: the call, drawn again
    without its buttons (*view* None) once its deadline has run."""
    await message.edit(embed=embed, view=view)


#: A round as its check-in reads it: its row, with `attendance_on` from attendance's own switch
#: reader (`attendance_enabled_on`).
CallRow = Mapping[str, Any]


async def _call_row(db: aiosqlite.Connection, round_id: int) -> CallRow | None:
    cursor = await db.execute(
        "SELECT r.id, r.division_id, r.round_number, r.format, r.track_name, r.scheduled_at, "
        "       r.status AS round_status, r.not_held, r.checkin_cleared, "
        "       d.status AS division_status, d.name AS division_name, d.tier AS division_tier, "
        "       d.mention_role_id, d.season_id, s.season_number, "
        "       t.gp_name AS track_gp_name, t.country AS track_country, "
        "       (SELECT rsvp_deadline_hours FROM attendance_config LIMIT 1) AS deadline_hours, "
        "       (SELECT rsvp_notice_days FROM attendance_config LIMIT 1) AS notice_days, "
        "       (SELECT rsvp_last_notice_hours FROM attendance_config LIMIT 1) AS last_notice_hours "
        "FROM rounds r JOIN divisions d ON d.id = r.division_id "
        "JOIN seasons s ON s.id = d.season_id "
        "LEFT JOIN tracks t ON t.name = r.track_name WHERE r.id = ?",
        (round_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return {**dict(row), "attendance_on": await attendance_enabled_on(db)}


async def _standing(db: aiosqlite.Connection, round_id: int) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
    )
    return await cursor.fetchone()


def _round_not_owed(row: CallRow | None) -> str | None:
    """Why the round is owed nothing of its check-in (no call, no notice), else None."""
    if row is None:
        return "the round is gone"
    if row["round_status"] not in ROUND_CANCELLABLE:
        return "the round is cancelled or its results are in"
    if row["division_status"] == "CANCELLED":
        return "the division is cancelled"
    if not row["attendance_on"]:
        return "the attendance module is off"
    if row["checkin_cleared"]:
        return "the round's check-in is over"
    return None


def _not_due(
    row: CallRow | None, standing: aiosqlite.Row | None, replaces: str | None
) -> str | None:
    """Why the round is owed no call (or no call more), else None.

    A call standing is left alone, unless it is the very one this call was asked to replace."""
    reason = _round_not_owed(row)
    if reason is not None:
        return reason
    if standing is not None and (replaces is None or str(standing["message_id"]) != str(replaces)):
        return "a check-in call already stands for the round"
    return None


#: How long after its moment a timer's ask still counts as on time under test mode: the five
#: minutes the scheduler's own jobs are given, so that a timer a restart fires late is told from
#: one that fires as it falls due.
_ON_TIME = timedelta(minutes=5)


async def _left_to_advance(
    db: aiosqlite.Connection, row: CallRow | None, kind: str, ctx: CheckContext, *, now: datetime
) -> str | None:
    """Why a timer's call or last notice is left to `/test-mode advance`, else None.

    **Under test mode a restart posts neither late** (core specification, "Test mode"): the timers
    are armed with no lateness limit, so one that fell due while the bot was down fires when it is
    back, and the change judges its own moment against *now*, the queue's clock. A bot's request
    that is not advance's and comes more than `_ON_TIME` after the moment of *kind* (`rsvp_notice`
    or `rsvp_last_notice`) is not due, and the round's check-in is left open for the advance to
    take in its turn. A call replacing another, an amendment's, is not judged so.
    """
    if (
        row is None
        or ctx.origin is not ChangeOrigin.BOT
        or ctx.payload.get("advanced")
        or ctx.payload.get("replaces") is not None
        or not await test_mode_on(db)
    ):
        return None
    moments = round_event_moments(
        _scheduled(row), weather=None,
        attendance=AttendanceWindows(
            row["notice_days"] or 0, row["last_notice_hours"] or 0, row["deadline_hours"] or 0
        ),
    )
    moment = moments.get(kind)
    if moment is None or now - moment <= _ON_TIME:
        return None
    return "test mode leaves a moment that passed before the bot came up to /test-mode advance"


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
    reason = _round_not_owed(row)
    if reason is not None:
        return reason
    if standing is None and not call_in_hand:
        return "no check-in call stands for the round"
    assert row is not None
    if not advanced and now >= _deadline(row):
        return "the check-in deadline has passed"
    return None


def _scheduled(row: CallRow) -> datetime:
    return read_scheduled_at(str(row["scheduled_at"]))


def _deadline(row: CallRow) -> datetime:
    return _scheduled(row) - timedelta(hours=row["deadline_hours"] or 0)


def _closed_text(row: CallRow, deadline_hours: int) -> str:
    """How the moment a call can no longer be answered is told: the deadline, or with none set
    (zero disables the closing of a check-in, not the race) the round's own start."""
    at = int((_scheduled(row) - timedelta(hours=deadline_hours)).timestamp())
    return f"closed at <t:{at}:F>" if deadline_hours > 0 else f"started at <t:{at}:F>"


def hand_post_refusal(
    row: CallRow,
    standing: aiosqlite.Row | None,
    cfg: AttendanceConfig | None,
    *,
    now: datetime,
) -> str | None:
    """Why `/attendance post-check-in` may not post *row*'s round a call now, in the command's
    words, else None.

    A last resort confined to the window in which a call should stand and does not: not before it
    is due (the scheduled one is still coming), not once its deadline has passed (the buttons
    would lock on arrival), and never over a call standing, which an amendment replaces with the
    answers carried across. *cfg* is attendance's configuration, None where there is none. The
    command asks this before it asks the queue, and the change's check asks it again when the
    change runs.
    """
    name, number = row["division_name"], row["round_number"]
    if row["round_status"] == "CANCELLED":
        return f"⛔ Round {number} of **{name}** is cancelled, so it has no check-in to answer."
    if row["checkin_cleared"]:
        return (
            f"⛔ Round {number} of **{name}** has no check-in left to answer: the round's "
            f"check-in is over. Nothing was posted."
        )
    if standing is not None:
        return (
            f"⛔ A check-in call is already standing for round {number} of **{name}**, so "
            f"nothing was posted. Amend the round if it needs to go out again — that carries "
            f"every answer already given across."
        )
    if cfg is None:
        return "\u274c No attendance configuration found. Enable the module first."
    notice_days, deadline_hours = cfg.rsvp_notice_days, cfg.rsvp_deadline_hours
    due_at = _scheduled(row) - timedelta(days=notice_days)
    if now < due_at:
        return (
            f"⛔ The check-in call for round {number} of **{name}** is not due until "
            f"<t:{int(due_at.timestamp())}:F>, and is still scheduled to post then. Nothing "
            f"was posted."
        )
    if now >= _scheduled(row) - timedelta(hours=deadline_hours):
        return (
            f"⛔ Round {number} of **{name}** {_closed_text(row, deadline_hours)}, so a call "
            f"posted now could not be answered. Nothing was posted."
        )
    return None


async def judge_hand_post(
    db_path: str,
    cfg: AttendanceConfig | None,
    division_id: int,
    division_name: str,
    round_number: int,
    *,
    now: datetime,
) -> tuple[int | None, str | None]:
    """The round *round_number* of a division, and the refusal `/attendance post-check-in`
    answers with if it may not post it a call: (round id, None) to go ahead, (None, the refusal)
    otherwise. *cfg* is attendance's configuration, None where there is none."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM rounds WHERE division_id = ? AND round_number = ?",
            (division_id, round_number),
        )
        found = await cursor.fetchone()
        if found is None:
            return None, f"❌ **{division_name}** has no round {round_number}."
        row = await _call_row(db, found["id"])
        standing = await _standing(db, found["id"])
    if row is None:
        return None, f"❌ **{division_name}** has no round {round_number}."
    refusal = hand_post_refusal(row, standing, cfg, now=now)
    return (None, refusal) if refusal is not None else (int(found["id"]), None)


def _earlier_messages(call: RsvpEmbedMessage) -> list[tuple[str, str]]:
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
        for message_id, channel_id in _earlier_messages(stored):
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


async def _forget_vanished_call(
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


async def round_amended_on(
    db: aiosqlite.Connection, round_id: int, *, reopened: bool, **_context: Any
) -> dict[str, Any]:
    """Attendance's part of the amendment's save: reopen the round's check-in on *db* where the
    amendment *reopened* it, committing nothing. Subscribed to core's `round_amended`.

    Hands back what the amendment's arming reads of it, kept with the save: whether a call stood
    when the save was made, read before the check-in was reopened, and whether it was reopened.
    """
    cursor = await db.execute(
        "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
    )
    called = await cursor.fetchone() is not None
    if reopened:
        await rsvp_service.reopen_check_in_on(db, round_id)
    return {"called": called, "reopened": reopened}


def _call_fell_due(
    rnd: Round, attendance: AttendanceWindows | None, now: Callable[[], datetime]
) -> bool:
    """Whether the round's check-in call has fallen due at *now*: its moment has passed."""
    if attendance is None:
        return False
    moments = round_event_moments(rnd.scheduled_at, weather=None, attendance=attendance)
    return moments["rsvp_notice"] <= now()


def _deadline_passed(
    rnd: Round, attendance: AttendanceWindows, now: Callable[[], datetime]
) -> bool:
    """Whether the round's check-in deadline has passed at *now*."""
    moments = round_event_moments(rnd.scheduled_at, weather=None, attendance=attendance)
    return moments["rsvp_deadline"] <= now()


async def amendment_posts(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    rnd: Round,
    kept: Mapping[str, Any],
    attendance: AttendanceWindows | None,
    now: Callable[[], datetime],
    **_context: Any,
) -> tuple[PlannedStep, ...]:
    """The posts an amendment owes of attendance's, in order, planned as the round is armed
    again, reading the round's call on *db*: the check-in call taken down and posted again, or
    posted where none stands, and the deadline run where it passed. Subscribed to core's
    `round_armed`.

    A call that stood at the save (*kept*, `round_amended_on`) is taken down and its post planned
    with it, the post judging when it runs whether the call is due then: the take-down can stop on
    Discord and be retried after the new call's moment, which the timer armed meanwhile has found
    taken by the old call. Otherwise the call is planned only where none stands and the check-in
    is not over and it has fallen due as the round is armed, saved or not; one still to come is
    the timer's. A standing call left in place whose deadline has passed with no distribution
    recorded has its deadline run, as the start-up recovery runs it.
    """
    found = await _amendment_standing(db, round_id)
    taking_down = bool(kept.get("reopened") and kept.get("called"))
    call_left = bool(found["called"]) and not taking_down
    planned: list[PlannedStep] = []
    if taking_down:
        planned += [
            PlannedStep(TAKE_DOWN_CALL, {
                "division_id": rnd.division_id,
                "division_name": found["division_name"],
                "season_id": found["season_id"],
                "round_id": round_id,
                "round_number": rnd.round_number,
            }),
            PlannedStep(POST_CALL, {"repost": True}),
        ]
    elif (not found["called"] and not found["checkin_cleared"]
          and _call_fell_due(rnd, attendance, now)):
        planned.append(PlannedStep(POST_CALL, {"repost": False}))
    if (attendance is not None and call_left and found["undistributed"]
            and _deadline_passed(rnd, attendance, now)):
        planned.append(PlannedStep(DISTRIBUTE))
    return tuple(planned)


async def amendment_clean_ups(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    rnd: Round,
    kept: Mapping[str, Any],
    now: Callable[[], datetime],
    **_context: Any,
) -> tuple[PlannedStep, ...]:
    """The clean-up of the round's check-in a day after the round, planned where that day has
    come and a call stands that the amendment did not take down. Read on *db*. Subscribed to
    core's `round_clean_ups_due`."""
    if clean_up_moment(rnd.scheduled_at) > now():
        return ()
    found = await _amendment_standing(db, round_id)
    if found["called"] and not (kept.get("reopened") and kept.get("called")):
        return (PlannedStep(CLEAN_UP_CHECK_IN),)
    return ()


async def _amendment_standing(db: aiosqlite.Connection, round_id: int) -> dict[str, Any]:
    """What an amendment's arming reads of a round's check-in: whether a call stands, whether
    one stands that no deadline has run (no distribution recorded), whether the check-in is over
    (``checkin_cleared``), and the division's name and season."""
    row = await _call_row(db, round_id)
    if row is None:
        raise LookupError(f"round {round_id} is no longer there")
    cursor = await db.execute(
        "SELECT EXISTS (SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?) AS called, "
        "EXISTS (SELECT 1 FROM rsvp_embed_messages WHERE round_id = ? "
        "AND deadline_run_at IS NULL AND distribution_msg_id IS NULL) AS undistributed",
        (round_id, round_id),
    )
    found = await cursor.fetchone()
    return {
        "checkin_cleared": row["checkin_cleared"],
        "division_name": row["division_name"],
        "season_id": row["season_id"],
        "called": bool(found["called"]) if found else False,
        "undistributed": bool(found["undistributed"]) if found else False,
    }


def given_up_text(row: CallRow) -> str:
    """The log channel's line for a round whose call is given up, as the start-up has always
    worded it."""
    return rsvp_service.call_failure_text(
        division_id=row["division_id"],
        division_name=row["division_name"],
        season_number=row["season_number"],
        round_number=row["round_number"],
        reason="the round's check-in deadline passed before its call could be posted",
        note=(
            "a call posted now could not be answered, so none was posted. No attendance "
            "rows were opened, and this round will count nothing against anyone."
        ),
    ) + "\n  result: the round's check-in is closed and its answers cleared; the round " \
        "counts nothing against anyone."


async def close_check_in_on(db: aiosqlite.Connection, round_id: int) -> bool:
    """Close *round_id*'s check-in as a call given up closes it, on *db* and committing nothing:
    the round marked ``checkin_cleared``, and any answers left for it and the placements made on
    them cleared in the same save, so that the line saying so is true.

    Only a round whose results are not in (its status in `ROUND_CANCELLABLE`) and whose check-in
    is not over is closed: a round further on has had its attendance charged, and a check-in over
    is settled, and clearing either would erase a record a league has acted on. Says whether it
    closed the round; nothing is written for one it left as it stood."""
    to_run = sorted(ROUND_CANCELLABLE)
    cursor = await db.execute(
        "UPDATE rounds SET checkin_cleared = 1 WHERE id = ? AND checkin_cleared = 0 "
        f"AND status IN ({', '.join('?' for _ in to_run)})",
        (round_id, *to_run),
    )
    if cursor.rowcount != 1:
        log.info("check-in call give-up: round %d's results are in or its check-in is over — "
                 "nothing given up", round_id)
        return False
    await rsvp_service.clear_check_in_answers_on(db, round_id)
    return True


async def give_up_text_for(db_path: str, round_id: int) -> str | None:
    """The line giving up *round_id*'s call, or None where there is nothing to give up: no call
    is owed a round that is gone, whose results are in or whose check-in is over, and a call
    standing is the round's live one."""
    async with get_connection(db_path) as db:
        row = await _call_row(db, round_id)
        standing = await _standing(db, round_id)
    if row is None or standing is not None:
        return None
    if row["checkin_cleared"] or row["round_status"] not in ROUND_CANCELLABLE:
        log.info("check-in call give-up: round %d's results are in or its check-in is over — "
                 "nothing given up", round_id)
        return None
    return given_up_text(row)


def amendment_steps(
    service: AttendanceService,
    router: OutputRouter,
    *,
    now: Callable[[], datetime],
    view_of: CheckInViewFactory,
    seasons: season_service.SeasonService,
    modules: ModuleService,
    deletes: Callable[[int, int], Awaitable[bool]],
) -> dict[str, Step]:
    """The jobs attendance defines for an amendment, by name: taking the round's call down
    (`take_down_steps`, named for the number the round bears now), posting it again, the three of
    a check-in deadline (placing the reserves, taking the buttons off the call, announcing),
    closing a check-in that has no call, and the four of taking a check-in down (its plan under
    the amendment's name `CLEAN_UP_CHECK_IN`, each deletion, the mark that it is over). *service* is
    attendance's, *router* writes the log channel's lines, *now* the queue's clock, *seasons* reads the round, *modules* says whether
    attendance is on, *deletes* is `check_in_deletes` and *view_of* builds the buttons of a call
    posted again. Subscribed to core's `amendment_steps`.
    """
    taken_down = take_down_steps(seasons=seasons, modules=modules, deletes=deletes)
    call = check_in_steps(service, router, now=now, view_of=view_of)
    deadline = check_in_deadline_steps(service, now=now)
    clean_up = check_in_clean_up_steps()

    def named(text: str) -> Callable[[StepContext], Awaitable[str]]:
        return named_now(seasons, text)

    def view(ctx: StepContext, name: str) -> Any:
        return next((each for each in reversed(ctx.steps) if each.name == name), None)

    def reposting(ctx: StepContext) -> bool:
        """Whether the call is posted again in place of one the amendment took down (its step
        payload, ``{"repost": True}``), rather than posted for the first time."""
        return bool(ctx.step_payload.get("repost"))

    def replacing(ctx: StepContext) -> bool:
        """Whether the call to post again replaces one still standing: the old call, whose
        take-down a league admin discarded. Otherwise any call standing when the post runs was
        posted after the take-down, or after the arming, by its timer, a restart or
        `/attendance post-check-in`, and is the round's live call."""
        taking_down = view(ctx, TAKE_DOWN_CALL)
        return reposting(ctx) and taking_down is not None and "discarded" in (
            taking_down.result or {}
        )

    def window(ctx: StepContext) -> AttendanceWindows | None:
        """The window the amendment's judging kept, which the call is judged against."""
        return judged_windows(ctx)[0]

    async def call_state(db_path: str, round_id: int) -> tuple[bool, bool]:
        """Whether a call stands for the round, and whether its check-in is over
        (``checkin_cleared``), read when the job runs."""
        async with get_connection(db_path) as db:
            row = await _call_row(db, round_id)
            standing = await _standing(db, round_id)
        if row is None:
            return False, False
        return standing is not None, bool(row["checkin_cleared"])

    async def call_still_due(ctx: StepContext) -> bool:
        """The call is posted only while attendance is on and the call has fallen due as the
        round stands when this job runs, and, as the start-up recovery of a missed call judges it
        (`_recover_missed_check_in_calls`), only where no call stands and the round's check-in
        is not over, both read then (owner, 2026-10-09). A call posted between the arming and
        this job, by its timer, a restart or `/attendance post-check-in`, is the round's live
        call, and posting again would call the division twice. The one exception is the old call
        a discarded take-down left standing, which a repost replaces (`replacing`), unless the
        check-in is over by then, as a clean-up a day after the round leaves it. A call still to
        come is posted by its timer, armed with the round; one whose deadline has passed is given
        up (`post_call`)."""
        if not await modules.is_attendance_enabled():
            return False
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None or not _call_fell_due(rnd, window(ctx), now):
            return False
        standing, cleared = await call_state(ctx.db_path, rnd.id)
        if cleared:
            return False
        return replacing(ctx) or not standing

    async def post_call(ctx: StepContext) -> StepResult:
        """Post the call, carrying every answer already given. Discord refusing it stops the queue
        (owner, 2026-10-10, decision 3), attendance raising with what is left for the round, which
        the stop notice says (owner, 2026-10-11) and a Discard reads in the amendment's outcome.

        The call is posted by the check-in call's own job, which posts nothing it cannot record:
        the job's record writes the call in the save that marks the job done. A call posted again
        in place of one the amendment took down (`reposting`) carries the answers of the drivers
        still of the division, and where a discarded take-down left the old call standing
        (`replacing`) the new call's record replaces the old one's. The old call's messages are
        not deleted again: the discarded take-down names those left, for deletion by hand.

        Where the round's check-in deadline has passed by the time it runs, as after a stop,
        nothing is posted: the round's check-in is closed and the log channel told in the job's
        record, as the start-up recovery gives up a missed call (owner, 2026-10-09). A call is
        given up only where none stands and the check-in is not over: one standing is the round's
        live call, or the old one a discarded take-down left, and a check-in over is settled, and
        either is left as it is. Given up, any answers left to a call the amendment took down,
        and the placements made on them, are cleared in the same save, so that the log channel's
        line that the round counts nothing against anyone holds true (owner, 2026-10-09: "Clear
        the answers, keep the line true"). The call is posted under test mode too, unlike the
        start-up recovery, which leaves a test season's calls to `/test-mode advance` (owner,
        2026-10-09: "Post it anyway")."""
        rnd = await seasons.get_round(int(ctx.payload["round_id"]))
        if rnd is None:
            raise LookupError(f"round {ctx.payload['round_id']} is no longer there")
        attendance = window(ctx)
        if attendance is not None and _deadline_passed(rnd, attendance, now):
            standing, cleared = await call_state(ctx.db_path, rnd.id)
            if standing or cleared:
                return StepResult(result={"left": True})
            text = await give_up_text_for(ctx.db_path, rnd.id)
            if text is None:
                # Its results came in, or its check-in closed, as the give-up reads it.
                return StepResult(result={"left": True})
            return StepResult(result={"given_up": True}, lines=(text,))
        carried = dataclasses.replace(
            ctx, step_payload={**ctx.step_payload, "replacing": replacing(ctx)}
        )
        return await call[POST_CALL].run(carried)

    async def record_call(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        """The call's record, or the give-up's close, in the save that marks the job done; a call
        left as it stood is recorded as nothing."""
        if (result.result or {}).get("given_up"):
            await close_check_in_on(db, int(ctx.payload["round_id"]))
            return
        if not (result.result or {}).get("new"):
            return  # left as it stood: nothing posted, nothing to record
        record = call[POST_CALL].record
        if record is not None:
            await record(db, ctx, result)

    return {
        TAKE_DOWN_CALL: dataclasses.replace(
            taken_down[TAKE_DOWN_CALL],
            describe=named("taking down the check-in call of round {number} in **{division}**"),
        ),
        POST_CALL: Step(
            POST_CALL, StepKind.ACT, post_call, still_due=call_still_due, record=record_call,
            describe=named("posting the check-in call of round {number} in **{division}** again"),
        ),
        # The jobs of a deadline, named for the round the amendment stands for.
        DISTRIBUTE: dataclasses.replace(
            deadline[DISTRIBUTE],
            describe=named("placing the reserves of round {number} in **{division}**"),
        ),
        CLOSE_WITHOUT_CALL: dataclasses.replace(
            deadline[CLOSE_WITHOUT_CALL],
            describe=named(
                "closing the check-in of round {number} in **{division}**, which has no call"
            ),
        ),
        LOCK_CALL: deadline[LOCK_CALL],
        ANNOUNCE: dataclasses.replace(
            deadline[ANNOUNCE],
            describe=named("posting the reserve announcement of round {number} in **{division}**"),
        ),
        # The jobs of a clean-up: its plan under the amendment's name for it, the deletions and
        # the mark that the check-in is over.
        CLEAN_UP_CHECK_IN: dataclasses.replace(
            clean_up[PLAN_CLEAN_UP], name=CLEAN_UP_CHECK_IN,
            describe=named(
                "finding what is left of the check-in of round {number} in **{division}**"
            ),
        ),
        DELETE_MESSAGE: clean_up[DELETE_MESSAGE],
        MARK_CLEARED: clean_up[MARK_CLEARED],
    }


def check_in_steps(
    service: AttendanceService,
    router: OutputRouter,
    *,
    now: Callable[[], datetime],
    view_of: CheckInViewFactory,
) -> dict[str, Step]:
    """The jobs of a check-in call, *service* being attendance's, *router* the log channel's
    writer, *now* the queue's clock and *view_of* the way to build the buttons the call
    carries."""

    async def still_due(ctx: StepContext) -> bool:
        async with get_connection(ctx.db_path) as db:
            row = await _call_row(db, int(ctx.payload["round_id"]))
            standing = await _standing(db, int(ctx.payload["round_id"]))
        reason = _not_due(row, standing, ctx.payload.get("replaces"))
        if reason is not None:
            log.info("The check-in call of round %s is no longer owed: %s.",
                     ctx.payload["round_id"], reason)
        return reason is None

    async def give_up(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await _call_row(db, round_id)
        if row is None:
            return StepResult(result={"dropped": True})
        if ctx.payload.get("advanced") or now() < _deadline(row):
            return StepResult(result={"in_time": True}, then=(PlannedStep(POST_CALL),))
        if not await close_check_in_on(db, round_id):
            return StepResult(result={"dropped": True})
        log.info("round %d's deadline has passed — its check-in call is given up", round_id)
        return StepResult(result={"given_up": True}, lines=(given_up_text(row),))

    async def describe_give_up(ctx: StepContext) -> str:
        return (
            f"judging whether the check-in call of round {ctx.payload.get('round_number', '?')} "
            f"in **{ctx.payload.get('division_name', '?')}** is still in time"
        )

    async def post(ctx: StepContext) -> StepResult:
        await standing_posts.remove_kept(ctx.bot, ctx.kept)
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await _call_row(db, round_id)
        if row is None:
            raise LookupError(f"round {round_id} is no longer there")
        division_id: int = row["division_id"]
        division = row["division_name"]
        standing = await service.get_embed_message(round_id, division_id)
        # An amendment's repost says in its job's payload whether the call it replaces still
        # stands (``replacing``: a discarded take-down left it), and a call it posts again carries
        # the answers given to the one it took down (``repost``).
        replaces = ctx.payload.get("replaces") is not None or bool(ctx.step_payload.get("replacing"))
        earlier = standing if replaces else None
        amending = bool(ctx.step_payload.get("repost"))
        answers_kept = earlier is None and await rsvp_service._answers_kept(ctx.db_path, round_id)

        async def stops(
            reason: str, *, timed_out: bool = False, said: str | None = None
        ) -> StepFailedOnDiscord:
            """The stop this failure makes, saying what it leaves for the round. Where the earlier
            call cannot be seen either, what is left of it is deleted and its record dropped in the
            stop's own save (`StepFailedOnDiscord.record`), a message that cannot be deleted named
            in the log channel for deletion by hand, its answers kept."""
            vanished = earlier is not None and await rsvp_service._call_vanished(ctx.bot, earlier)
            forget: Callable[[aiosqlite.Connection], Awaitable[None]] | None = None
            if vanished:
                forget = await _forget_vanished_call(ctx, service, router, row, round_id, division_id)
            if answers_kept and not timed_out and not amending:
                left = rsvp_service.ANSWERS_KEPT.format(
                    division=division, round=row["round_number"]
                )
            else:
                left = rsvp_service.left_for_the_queue(
                    earlier is not None, answers_kept, vanished, timed_out=timed_out
                )
            return StepFailedOnDiscord(
                reason,
                result={"earlier_left": earlier is not None and not vanished,
                        "vanished": vanished},
                notice=left if said is None else f"{left}; Discord said: {said}",
                record=forget,
            )

        config = await service.get_division_config(division_id)
        if config is None or not config.rsvp_channel_id:
            raise await stops(
                f"**{division}** has no check-in channel set: set one with "
                f"`/attendance channel rsvp`, then press Retry."
            )
        channel = as_text_channel(ctx.bot.get_channel(int(config.rsvp_channel_id)))
        if channel is None:
            raise await stops(
                f"the configured RSVP channel ({config.rsvp_channel_id}) could not be reached"
            )

        roster = await rsvp_service.query_division_roster(ctx.db_path, division_id)
        driver_ids = [d["driver_profile_id"] for team in roster for d in team["drivers"]]
        embed = rsvp_service.build_rsvp_embed(
            season_number=row["season_number"],
            round_number=row["round_number"],
            track_name=row["track_name"],
            scheduled_at=_scheduled(row),
            round_format=RoundFormat(row["format"]),
            teams=[
                {
                    "name": team["name"],
                    "is_reserve": team["is_reserve"],
                    "drivers": [
                        {"display_str": rsvp_service._driver_display_str(d),
                         "rsvp_status": "NO_RSVP"}
                        for d in team["drivers"]
                    ],
                }
                for team in roster
            ],
        )
        role = row["mention_role_id"]
        # The check-in graphic is drawn once, on the first try (Constitution XIV r8): a retry
        # posts the text. It is the one generation call of the module (XIV.17).
        attachment = None
        if ctx.tries == 0:
            attachment = await rsvp_service._checkin_attachment(
                ctx.bot,
                service,
                division_id=division_id,
                division_name=division,
                division_tier=row["division_tier"],
                season_number=row["season_number"],
                round_number=row["round_number"],
                round_format=RoundFormat(row["format"]),
                scheduled_at=_scheduled(row),
                track_name=row["track_name"],
                race_name=row["track_gp_name"],
                country_name=row["track_country"],
            )
        try:
            message = await post_check_in_message(
                channel,
                content=f"<@&{role}>\n" if role else None,
                embed=embed,
                view=view_of(round_id),
                file=attachment,
                mentions=discord.AllowedMentions(roles=bool(role)),
            )
        except (discord.HTTPException, aiohttp.ClientError, OSError) as exc:
            # A fault on the connection (a timeout among them) is handled as Discord refusing is.
            raise await stops(
                f"the call could not be posted: {str(exc) or type(exc).__name__}",
                timed_out=isinstance(exc, TimeoutError),
                said=str(exc) or type(exc).__name__,
            ) from exc
        finally:
            from leaguebot.image.services.image_rsvp_post import discard_attachment

            discard_attachment(attachment)

        # The messages of the call this one replaces come down by jobs of their own after it only
        # where the request said so. An amendment's repost, which replaces the call a discarded
        # take-down left standing, plans none: the admin who discarded the take-down was told
        # which messages stand, and the reply names them for deletion by hand.
        then: tuple[PlannedStep, ...] = ()
        if earlier is not None and ctx.payload.get("replaces") is not None:
            # The earlier call is read again now it has been replaced, as it stands just before the
            # new call takes its record: a last notice or announcement posted while the repost was
            # being sent was recorded on it, and comes down with it.
            latest = await service.get_embed_message(round_id, division_id)
            coming_down = (
                latest if latest is not None and latest.message_id == earlier.message_id
                else earlier
            )
            then = tuple(
                PlannedStep(DELETE_EARLIER, {
                    "channel_id": int(channel_id), "message_id": int(message_id),
                    "what": "check-in call", "division_name": division,
                })
                for message_id, channel_id in _earlier_messages(coming_down)
            )
        lines: tuple[str, ...] = ()
        if ctx.actor_id is not None and ctx.kind == CHECK_IN_CALL:
            # Only the call's own change was asked by the command: an amendment that borrows this
            # step to repost the call has its own line, and nobody ran /attendance post-check-in.
            lines += (
                f"{ctx.named} | /attendance post-check-in | Success\n"
                f"  division: {division}\n"
                f"  round: {row['round_number']}",
            )
        if ctx.payload.get("late"):
            lines += (
                f"ATTENDANCE | check-in call | POSTED LATE\n"
                f"  season: {row['season_number']}\n"
                f"  division: {division} (id={division_id})\n"
                f"  round: {row['round_number']}\n"
                f"  reason: the call was not posted when it fell due\n"
                f"  deadline: <t:{int(_deadline(row).timestamp())}:F>",
            )
        return StepResult(
            result={
                "new": [message.id], "channel_id": message.channel.id,
                "round_id": round_id, "division_id": division_id, "driver_ids": driver_ids,
                "replacing": earlier is not None or amending,
            },
            then=then,
            lines=lines,
        )

    async def record_call(db: aiosqlite.Connection, ctx: StepContext, result: StepResult) -> None:
        saved = result.result
        round_id, division_id = int(saved["round_id"]), int(saved["division_id"])
        await service.insert_embed_message_on(
            db, round_id, division_id, str(saved["new"][0]), str(saved["channel_id"]),
            posted_at=now(),
        )
        drivers = [int(each) for each in saved["driver_ids"]]
        if drivers:
            await service.bulk_insert_attendance_rows_on(db, round_id, division_id, drivers)
        if saved["replacing"]:
            # The answers carried over are those of drivers the division still holds.
            marks = ", ".join("?" for _ in drivers)
            await db.execute(
                "DELETE FROM driver_round_attendance WHERE round_id = ?"
                + (f" AND driver_profile_id NOT IN ({marks})" if drivers else ""),
                (round_id, *drivers),
            )

    async def describe_post(ctx: StepContext) -> str:
        return (
            f"{_what(ctx.payload)} (a call discarded is posted by hand with "
            f"`/attendance post-check-in division: {ctx.payload.get('division_name', '?')} "
            f"round: {ctx.payload.get('round_number', '?')}`)"
        )

    return {
        GIVE_UP: Step(GIVE_UP, StepKind.SAVE, give_up, still_due=still_due,
                      describe=describe_give_up),
        POST_CALL: Step(POST_CALL, StepKind.ACT, post, still_due=still_due,
                        describe=describe_post, record=record_call),
        DELETE_EARLIER: standing_posts.delete_replaced_step(DELETE_EARLIER),
    }


def check_in_call_change(
    service: AttendanceService,
    router: OutputRouter,
    *,
    now: Callable[[], datetime],
    view_of: CheckInViewFactory,
) -> ChangeType:
    """`attendance.check_in.call`: the bot's change that posts a round's check-in call, or gives
    it up where its deadline has passed. Not repeatable: a round is owed one call."""

    async def check(ctx: CheckContext) -> Verdict:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await _call_row(db, round_id)
            standing = await _standing(db, round_id)
        if ctx.origin is ChangeOrigin.MEMBER and row is not None:
            # A hand-posted call is judged in the command's own words, as the command judged it
            # when it asked and again here, since a queue stopped for a while can bring it up to
            # run after its deadline or beside a call the timer has posted meanwhile.
            refusal = hand_post_refusal(row, standing, await service.get_config(), now=now())
            if refusal is not None:
                return Verdict.refuse(refusal)
        reason = _not_due(row, standing, ctx.payload.get("replaces"))
        if reason is None:
            async with get_connection(ctx.db_path) as db:
                reason = await _left_to_advance(db, row, "rsvp_notice", ctx, now=now())
        return Verdict.go() if reason is None else Verdict.not_due(reason)

    def outcome(ctx: OutcomeContext) -> str:
        """What a member who posted the call by hand is told: it was posted, or why it was not.
        The bot's own request tells nobody."""
        if ctx.actor_id is None:
            return ""
        name, number = ctx.payload.get("division_name", "?"), ctx.payload.get("round_number", "?")
        steps = {step.name: step for step in ctx.steps}
        posted = steps.get(POST_CALL)
        if posted is not None and posted.done and (posted.result or {}).get("new"):
            return f"✅ Check-in call posted for round {number} of **{name}**."
        if posted is not None and posted.done and "discarded" in (posted.result or {}):
            return (
                f"⛔ The check-in call for round {number} of **{name}** was not posted: a league "
                f"admin discarded it. Post it with `/attendance post-check-in division: {name} "
                f"round: {number}` once the cause is cleared."
            )
        return (
            f"⛔ The check-in call for round {number} of **{name}** was not posted: it was "
            f"no longer owed when it came to run."
        )

    return ChangeType(
        kind=CHECK_IN_CALL,
        opening=(PlannedStep(GIVE_UP),),
        steps=check_in_steps(service, router, now=now, view_of=view_of),
        check=check,
        key=lambda payload: f"{CHECK_IN_CALL}:{payload['round_id']}",
        doing=_doing,
        outcome=outcome,
    )


async def ask_check_in_call(
    queue: ChangeQueue,
    db_path: str,
    round_id: int,
    *,
    origin: ChangeOrigin,
    replacing: bool = False,
    late: bool = False,
    interaction: discord.Interaction | None = None,
    refusal_what: str | None = None,
) -> None:
    """Ask the queue to post *round_id*'s check-in call, as its timer does.

    *replacing* asks that a call standing now be replaced: it is taken down by jobs after the new
    one, and only that very call; any other call found standing when the change runs is left
    alone. *late* marks a call asked by a restart for one that fell due while the bot was down:
    the log channel is told it was posted late. A round that is not there asks nothing; the
    change's check drops a request that is no longer owed.

    *interaction*, with a member's *origin*, is the manager who posts the call by hand: they are
    told it is under way, then what became of it, and a refusal is made to them in the command's
    words, recorded as *refusal_what*.
    """
    async with get_connection(db_path) as db:
        row = await _call_row(db, round_id)
        standing = await _standing(db, round_id) if replacing else None
    if row is None:
        log.error("Check-in call: round_id=%s not found — nothing asked of the queue.", round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
        "replaces": None if standing is None else str(standing["message_id"]),
        "late": late,
    }
    await queue.ask(
        CHECK_IN_CALL, payload, interaction=interaction, what=_what(payload),
        refusal_what=refusal_what, origin=origin,
    )


def check_in_last_notice_steps(
    service: AttendanceService, *, now: Callable[[], datetime]
) -> dict[str, Step]:
    """The job of a last notice, *service* being attendance's and *now* the queue's clock."""

    async def still_due(ctx: StepContext) -> bool:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await _call_row(db, round_id)
            standing = await _standing(db, round_id)
        reason = _notice_not_due(
            row, standing, now=now(), advanced=bool(ctx.payload.get("advanced"))
        )
        if reason is not None:
            log.info("The last notice of round %s is no longer owed: %s.", round_id, reason)
        return reason is None

    async def post(ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        async with get_connection(ctx.db_path) as db:
            row = await _call_row(db, round_id)
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
        content = rsvp_service.last_notice_content(silent, int(_scheduled(row).timestamp()))
        mentions = (
            discord.AllowedMentions(
                users=[discord.Object(id=int(each["discord_user_id"])) for each in silent]
            )
            if silent else discord.AllowedMentions.none()
        )
        try:
            message = await post_check_in_message(channel, content=content, mentions=mentions)
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
            row = await _call_row(db, round_id)
            standing = await _standing(db, round_id)
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
                reason = await _left_to_advance(db, row, "rsvp_last_notice", ctx, now=now())
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
        row = await _call_row(db, round_id)
    if row is None:
        log.error("Last check-in notice: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
    }
    await queue.ask(CHECK_IN_LAST_NOTICE, payload, what=_doing_notice(payload), origin=origin)


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
            row = await _call_row(db, int(ctx.payload["round_id"]))
        reason = _not_cleaned(row)
        if reason is not None:
            log.info("The check-in clean-up of round %s is no longer owed: %s.",
                     ctx.payload["round_id"], reason)
        return reason is None

    async def plan(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await _call_row(db, round_id)
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
            row = await _call_row(db, int(ctx.payload["round_id"]))
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
        row = await _call_row(db, round_id)
    if row is None:
        log.error("Check-in clean-up: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
    }
    await queue.ask(CHECK_IN_CLEAN_UP, payload, what=_doing_clean_up(payload), origin=origin)


def _doing_deadline(payload: dict[str, Any]) -> str:
    return (
        f"running the check-in deadline of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}**"
    )


def _deadline_not_due(
    row: CallRow | None, stored: aiosqlite.Row | None
) -> str | None:
    """Why the round is owed no deadline run, else None: a deadline runs once for each call."""
    reason = _round_not_owed(row)
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
            row = await _call_row(db, round_id)
        # Once the distribution has marked the call, what is left (the lock, the announcement) is
        # owed whatever the round's state, unless it has been called off.
        reason = _round_not_owed(row) if ctx.step_payload.get("distributed") is None else (
            None if row is not None and row["round_status"] != "CANCELLED"
            and row["attendance_on"] else "the round is off"
        )
        if reason is not None:
            log.info("The check-in deadline of round %s is no longer owed: %s.", round_id, reason)
        return reason is None

    async def distribute(db: aiosqlite.Connection, ctx: StepContext) -> StepResult:
        round_id = int(ctx.payload["round_id"])
        row = await _call_row(db, round_id)
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
            answered_by=None if ctx.payload.get("advanced") else _deadline(row),
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
        row = await _call_row(db, round_id)
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
            await edit_check_in_message(message, embed=embed, view=None)
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
            message = await post_check_in_message(channel, content=text, mentions=mentions)
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
            row = await _call_row(db, round_id)
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
        row = await _call_row(db, round_id)
    if row is None:
        log.error("Check-in deadline: round_id=%s not found — nothing asked of the queue.",
                  round_id)
        return
    payload: dict[str, Any] = {
        "round_id": round_id, "division_id": row["division_id"],
        "division_name": row["division_name"], "round_number": row["round_number"],
    }
    await queue.ask(CHECK_IN_DEADLINE, payload, what=_doing_deadline(payload), origin=origin)


def advance_steps(queue: ChangeQueue, db_path: str) -> dict[str, AdvanceStep]:
    """The step `/test-mode advance` runs for each of attendance's kinds of round job: the check-in
    call, last notice, deadline and clean-up, each asking *queue* as the bot, early.

    **Each is asked as advance's** (`advanced`): judged by no clock, so that under test mode the
    step that ends the check-in is the one advance fires, a past-dated round's call being posted
    however late it is. The payload carries the entry's division and round numbers and no
    division id, as the timers' does not carry the flag; the call's also says it replaces nothing
    and is not late. A fault is named as the command words the step ("the RSVP notice"). Each
    step's words, and the change it asks, are what advance has always said and asked.
    """

    def step_for(
        kind: str, *, step: str, what: str, emoji: str, name: str, job: str, taken_down: str | None
    ) -> AdvanceStep:
        async def run(entry: PhaseEntry) -> AdvanceOutcome:
            division, number = entry["division_name"], entry["round_number"]
            payload: dict[str, Any] = {
                "round_id": entry["round_id"], "division_name": division,
                "round_number": number, "advanced": True,
            }
            if kind == CHECK_IN_CALL:
                payload |= {"replaces": None, "late": False}
            try:
                change = await queue.ask(
                    kind, payload, what=f"{what} of round {number} in **{division}**",
                    origin=ChangeOrigin.BOT,
                )
            except Exception as exc:  # noqa: BLE001 — named by the command, which reports it
                raise AdvanceStepFailed(step) from exc
            if change is None:
                return AdvanceOutcome.not_due(
                    f"{emoji} The **{name}** for **{division}** — **Round {number}** was not due."
                )
            if taken_down is not None:
                reply = (
                    f"{emoji} The **{name}** for **{division}** — **Round {number}** is on the "
                    f"change queue: it {taken_down} as soon as the queue reaches it."
                )
            else:
                reply = (
                    f"{emoji} The **{name}** for **{division}** — **Round {number}** "
                    f"(**{entry['track_name']}**) is on the change queue: it goes to the RSVP "
                    f"channel as soon as the queue reaches it."
                )
            return AdvanceOutcome.asked(
                reply, f"  phase: {job}\n  division: {division}\n  round: {number}"
            )

        return run

    return {
        "rsvp_notice": step_for(
            CHECK_IN_CALL, step="the RSVP notice", what="the RSVP notice", emoji="📨",
            name="RSVP notice", job="rsvp_notice", taken_down=None,
        ),
        "rsvp_last_notice": step_for(
            CHECK_IN_LAST_NOTICE, step="the RSVP last-notice", what="the RSVP last-notice",
            emoji="⏰", name="RSVP last-notice", job="rsvp_last_notice", taken_down=None,
        ),
        "rsvp_deadline": step_for(
            CHECK_IN_DEADLINE, step="the RSVP deadline", what="the RSVP deadline", emoji="🏁",
            name="RSVP deadline", job="rsvp_deadline", taken_down=None,
        ),
        "rsvp_cleanup": step_for(
            CHECK_IN_CLEAN_UP, step="the check-in cleanup",
            what="taking down the check-in a day after the round", emoji="🧹",
            name="check-in cleanup", job="rsvp_cleanup",
            taken_down="takes down the check-in call, last notice and reserve announcement",
        ),
    }
