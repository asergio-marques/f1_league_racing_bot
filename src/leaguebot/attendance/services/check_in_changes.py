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

The posts of a check-in (the call, and the last notice and announcement that follow) go through
`post_check_in_message`, the module's one send site, which keeps the mentions the post may notify
stated where it is sent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
import aiosqlite
import discord

from leaguebot.attendance.models.attendance import AttendanceConfig, RsvpEmbedMessage
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import (
    ChangeOrigin,
    PlannedStep,
    StepFailedOnDiscord,
    StepKind,
    StepResult,
    Verdict,
)
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundFormat
from leaguebot.core.services import standing_posts
from leaguebot.core.services.change_queue import (
    ChangeQueue,
    ChangeType,
    CheckContext,
    OutcomeContext,
    Step,
    StepContext,
)
from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.in_a_job import posts_in_a_job

log = logging.getLogger(__name__)

CHECK_IN_CALL = "attendance.check_in.call"
GIVE_UP = "give_up"
POST_CALL = "post_call"
DELETE_EARLIER = "delete_earlier"


def _value(ctx: StepContext, key: str, default: Any = None) -> Any:
    """The job's own value, else the change's."""
    return ctx.step_payload.get(key, ctx.payload.get(key, default))


def _doing(payload: dict[str, Any]) -> str:
    return (
        f"posting the check-in call of round {payload.get('round_number', '?')} in "
        f"**{payload.get('division_name', '?')}**"
    )


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


async def _call_row(db: aiosqlite.Connection, round_id: int) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT r.id, r.division_id, r.round_number, r.format, r.track_name, r.scheduled_at, "
        "       r.status AS round_status, r.checkin_cleared, "
        "       d.status AS division_status, d.name AS division_name, d.tier AS division_tier, "
        "       d.mention_role_id, s.season_number, "
        "       t.gp_name AS track_gp_name, t.country AS track_country, "
        "       (SELECT module_enabled FROM attendance_config LIMIT 1) AS attendance_on, "
        "       (SELECT rsvp_deadline_hours FROM attendance_config LIMIT 1) AS deadline_hours "
        "FROM rounds r JOIN divisions d ON d.id = r.division_id "
        "JOIN seasons s ON s.id = d.season_id "
        "LEFT JOIN tracks t ON t.name = r.track_name WHERE r.id = ?",
        (round_id,),
    )
    return await cursor.fetchone()


async def _standing(db: aiosqlite.Connection, round_id: int) -> aiosqlite.Row | None:
    cursor = await db.execute(
        "SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", (round_id,)
    )
    return await cursor.fetchone()


def _not_due(
    row: aiosqlite.Row | None, standing: aiosqlite.Row | None, replaces: str | None
) -> str | None:
    """Why the round is owed no call (or no call more), else None.

    A call standing is left alone, unless it is the very one this call was asked to replace."""
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
    if standing is not None and (replaces is None or str(standing["message_id"]) != str(replaces)):
        return "a check-in call already stands for the round"
    return None


def _scheduled(row: aiosqlite.Row) -> datetime:
    scheduled = datetime.fromisoformat(str(row["scheduled_at"]))
    return scheduled if scheduled.tzinfo else scheduled.replace(tzinfo=timezone.utc)


def _deadline(row: aiosqlite.Row) -> datetime:
    return _scheduled(row) - timedelta(hours=row["deadline_hours"] or 0)


def _closed_text(row: aiosqlite.Row, deadline_hours: int) -> str:
    """How the moment a call can no longer be answered is told: the deadline, or with none set
    (zero disables the closing of a check-in, not the race) the round's own start."""
    at = int((_scheduled(row) - timedelta(hours=deadline_hours)).timestamp())
    return f"closed at <t:{at}:F>" if deadline_hours > 0 else f"started at <t:{at}:F>"


def hand_post_refusal(
    row: aiosqlite.Row,
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


def given_up_text(row: aiosqlite.Row) -> str:
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


def check_in_steps(service: AttendanceService, *, now: Callable[[], datetime]) -> dict[str, Step]:
    """The jobs of a check-in call, *service* being attendance's and *now* the queue's clock."""

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
        if now() < _deadline(row):
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
            call cannot be seen either, its record is dropped, what is left of it deleted or named
            (`withdraw_rsvp_call`), its answers kept."""
            vanished = earlier is not None and await rsvp_service._call_vanished(ctx.bot, earlier)
            if vanished:
                await rsvp_service.withdraw_rsvp_call(round_id, division_id, ctx.bot)
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
                view=rsvp_service.RsvpView(round_id=round_id),
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
            then = tuple(
                PlannedStep(DELETE_EARLIER, {
                    "channel_id": int(channel_id), "message_id": int(message_id),
                    "what": "check-in call", "division_name": division,
                })
                for message_id, channel_id in _earlier_messages(earlier)
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
            f"{_doing(ctx.payload)} (a call discarded is posted by hand with "
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


def check_in_call_change(service: AttendanceService, *, now: Callable[[], datetime]) -> ChangeType:
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
        steps=check_in_steps(service, now=now),
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
        CHECK_IN_CALL, payload, interaction=interaction, what=_doing(payload),
        refusal_what=refusal_what, origin=origin,
    )
