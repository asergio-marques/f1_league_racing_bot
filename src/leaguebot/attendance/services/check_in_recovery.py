"""What a restart does about check-in: attendance's three start-up steps.

The wiring only names these as steps, in the order they run in attendance's turn of the start-up
sweep (architecture.md, "A module's wiring"), so it holds neither the reads nor the rules. Each is
handed the bot and the change queue, and judges every moment at the queue's clock
(`ChangeQueue.now`), the bot's clock, so that a test moving it moves every reading. Each asks the
queue for what a stop left undone and posts nothing itself; the queue runs what is asked once it
starts.

- `recover_missed_check_in_calls`: the call that fell due while the bot was down is posted late.
- `recover_rsvp_views_and_deadlines`: the buttons of every standing call are armed again, and a
  deadline that passed while the bot was down is run.
- `recover_missed_check_in_clean_ups`: the check-in messages due to come down are taken down.

**What a league's rounds are judged by.** A season is owed a call and a deadline while it is
raced, in one of the ongoing stages (`ONGOING_STAGES`), which core's `rounds_of_ongoing_seasons`
reads; the clean-up is owed every season but a cancelled one, as its timer is, so a season
awaiting completion still has its last round's check-in taken down a day after the race. Each
reads core's tables only through core's readers and its own, `attendance_config` and
`rsvp_embed_messages`, here.

**A round that fails stops none of the others.** Once every round has been gone through, a failure
for any is raised as one `RecoveryFailed` naming each round and its error, which the start-up sweep
reports to the log channel in one line. A database that cannot be read is raised as it is, for the
sweep to report: nothing is caught up, and nothing says so but that line.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import aiosqlite

from leaguebot.attendance.models.attendance import CHECK_IN_DEADLINE
from leaguebot.attendance.models.recovery_failed import RecoveryFailed
from leaguebot.attendance.services.check_in_changes import (
    CheckInViewFactory,
    ask_check_in_call,
    ask_check_in_clean_up,
    ask_check_in_deadline,
)
from leaguebot.attendance.services.module_switch_settings import attendance_enabled_on
from leaguebot.attendance.services.test_mode_steps import check_ins_cleared
from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.division import DivisionStatus
from leaguebot.core.models.round import ROUND_CANCELLABLE, RoundStatus
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.queue.queue_records import unfinished
from leaguebot.core.services.rounds.round_moments import clean_up_moment
from leaguebot.core.services.seasons.season_end_changes import test_mode_on
from leaguebot.core.services.seasons.season_service import (
    rounds_of_ongoing_seasons,
    round_facts_on,
)
from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

__all__ = [
    "recover_missed_check_in_calls",
    "recover_missed_check_in_clean_ups",
    "recover_rsvp_views_and_deadlines",
]


async def _timing(db: aiosqlite.Connection) -> tuple[int, int] | None:
    """The league's check-in timing, its notice days and its deadline hours, or None where
    attendance is off (or has never been switched on): the module's own gate, read on *db*."""
    cursor = await db.execute(
        "SELECT rsvp_notice_days, rsvp_deadline_hours FROM attendance_config "
        "WHERE module_enabled = 1"
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    return int(row["rsvp_notice_days"] or 0), int(row["rsvp_deadline_hours"] or 0)


async def _standing_round_ids(
    db: aiosqlite.Connection, *, deadline_unrun: bool = False
) -> list[int]:
    """The rounds with a check-in call standing, in round order; with *deadline_unrun*, those
    whose call carries no mark of its deadline having run."""
    cursor = await db.execute(
        "SELECT DISTINCT round_id FROM rsvp_embed_messages "
        "WHERE (? = 0 OR deadline_run_at IS NULL) ORDER BY round_id",
        (int(deadline_unrun),),
    )
    return [int(row["round_id"]) for row in await cursor.fetchall()]


async def recover_missed_check_in_calls(bot: LeagueBot, *, queue: ChangeQueue) -> None:
    """Post late the check-in calls that fell due while the bot was down.

    A call's timer fires late when the bot is back, having no lateness limit, and this ask meets
    the timer's on the change's key, so the call posts once. Until one of them posts it the round
    has asked nobody whether they were racing, opened no attendance rows, and counted nothing
    against anyone. A call that came due is posted late, provided the round's check-in deadline
    is still ahead, and the drivers answer in the time left. The deadline boundary is the one `/attendance post-check-in` holds to —
    with a deadline of zero, the round's own moment.

    A round is owed a late call where its season is in one of the three ongoing stages, neither
    it nor its division is cancelled, the attendance module is enabled, no call for it stands
    and its check-in is not over (``checkin_cleared``), and the moment its call was due has
    passed. **Nothing is posted while test mode is on**: `/test-mode advance` posts a test season's
    calls in their turn.

    The restart asks the change queue for each call owed, before the queue starts, and posts
    nothing itself: the call's change posts it, telling the log channel that it went out late, or,
    where the deadline has passed as well, gives it up (`give_up`), closing the round's check-in so
    that the next start does not ask again. A call already in hand on the queue is not asked for
    twice. Its last notice and its deadline need no arming: the season's approval armed
    them with the call, the job store outlives a restart, and both are still ahead of a call posted
    in time to be answered.

    Runs before `recover_rsvp_views_and_deadlines` in start-up, the call coming before its
    deadline.
    """
    moment = queue.now()
    async with get_connection(bot.db_path) as db:
        timing = await _timing(db)
        if timing is None or await test_mode_on(db):
            return
        standing = set(await _standing_round_ids(db))
    notice_days, deadline_hours = timing
    owed = [
        each for each in await rounds_of_ongoing_seasons(bot.db_path, ROUND_CANCELLABLE)
        if each.id not in standing
    ]
    async with get_connection(bot.db_path) as db:
        over = await check_ins_cleared(db, [each.id for each in owed]) if owed else set()

    failures: dict[int, BaseException] = {}
    for each in owed:
        if each.id in over:
            continue
        due_at = each.scheduled_at - timedelta(days=notice_days)
        deadline_at = each.scheduled_at - timedelta(hours=deadline_hours)
        if moment < due_at:
            continue
        # The change judges the deadline when it runs: it posts the call while the deadline is
        # ahead, and gives it up, closing the round's check-in, once it has passed.
        log.info("_recover_missed_check_in_calls: asking for round %d's call", each.id)
        try:
            await ask_check_in_call(
                queue, bot.db_path, each.id, origin=ChangeOrigin.BOT, late=moment < deadline_at,
            )
        except Exception as error:
            log.exception(
                "_recover_missed_check_in_calls: could not ask for round %d's call", each.id
            )
            failures[each.id] = error
    if failures:
        raise RecoveryFailed(failures)


async def recover_rsvp_views_and_deadlines(
    bot: LeagueBot, *, queue: ChangeQueue, view_of: CheckInViewFactory
) -> None:
    """Re-arm the check-in buttons and ask for the RSVP deadlines missed, on a restart.

    The buttons are armed again for every standing call (a row of `rsvp_embed_messages`), so that
    a driver's press survives a restart (FR-007). The buttons' view is the cog's, which a service
    may not import, so *view_of* builds it, handed by attendance's wiring. A button that
    cannot be armed is left and logged, and costs no other call its buttons.

    For any call whose check-in deadline has passed at the queue's clock and which carries no
    mark of its deadline having run, ask the change queue for the deadline (FR-027), unless one
    is in hand on the queue already. A round is owed it where its season is in one of the ongoing
    stages and the round is not cancelled.

    **The call's own mark decides, never the drivers' placements**. A deadline writes its
    distribution and marks the call (`deadline_run_at`) in one save, before it posts its
    announcement or its no-reserve notice; that is how `AttendanceService.get_current_embed_message`
    tells a deadline that has run as well, and the two must agree. The placements cannot: a
    deadline with no reserve to place writes none, so it would be run again at every start and
    its notice posted again, and a call posted again after an amendment carries over the
    placements of the call it replaced, so its own deadline would never be caught up. The
    announcement's id cannot either: a discarded announcement leaves none, and the deadline has
    run all the same.

    A last notice whose moment passed while the bot was down is not asked for here (FR-029): its
    timer is armed with no lateness limit, the job store keeps a job that fell due, and it fires
    when the bot is back, the notice's change sending it late while the deadline is ahead.

    A restart is one of the three paths the core specification names, so none of this runs
    for a server whose attendance module is switched off. Re-arming the buttons
    would revive check-in for a league that had turned it off, and the missed-deadline
    catch-up would distribute its reserves into seats hours after the fact.
    """
    moment = queue.now()
    async with get_connection(bot.db_path) as db:
        timing = await _timing(db)
        if timing is None:
            return
        cursor = await db.execute("SELECT round_id, message_id FROM rsvp_embed_messages")
        embeds = await cursor.fetchall()
        unrun = set(await _standing_round_ids(db, deadline_unrun=True))
    _, deadline_hours = timing

    for embed in embeds:
        try:
            bot.add_view(view_of(int(embed["round_id"])), message_id=int(embed["message_id"]))
        except Exception as exc:
            log.warning(
                "_recover_rsvp_views_and_deadlines: could not re-arm view for msg %s: %s",
                embed["message_id"], exc, exc_info=True,
            )

    owed = [
        each for each in await rounds_of_ongoing_seasons(
            bot.db_path, {each.value for each in RoundStatus} - {RoundStatus.CANCELLED.value}
        )
        if each.id in unrun and each.scheduled_at - timedelta(hours=deadline_hours) <= moment
    ]
    in_hand = {int(each["round_id"]) for each in await unfinished(bot.db_path, [CHECK_IN_DEADLINE])}

    failures: dict[int, BaseException] = {}
    for each in owed:
        if each.id in in_hand:
            continue  # already on the queue: its jobs are in hand
        log.info(
            "_recover_rsvp_views_and_deadlines: asking for missed deadline of round %d / "
            "division %d", each.id, each.division_id,
        )
        try:
            await ask_check_in_deadline(queue, bot.db_path, each.id, origin=ChangeOrigin.BOT)
        except Exception as error:
            log.exception(
                "_recover_rsvp_views_and_deadlines: deadline run failed for round %d", each.id
            )
            failures[each.id] = error
    if failures:
        raise RecoveryFailed(failures)


async def recover_missed_check_in_clean_ups(bot: LeagueBot, *, queue: ChangeQueue) -> None:
    """Take down the check-in calls, with their last notices and distribution messages, whose
    clean-up fell due while the bot was down: attendance's part of the start-up's missed clean-ups.

    A call comes down `POST_RACE_CLEANUP_DELAY` after its round's start, by the ``rsvp_cleanup``
    job. A job falling due while the bot is stopped fires late when the bot is back, having no
    lateness limit, and this ask meets the timer's on the change's key, so the messages come down
    once.

    A round called off, or of a division called off, is left alone: its cancellation removed the
    job. A round that ended cancelled because every session was entered as not held
    (`rounds.not_held`) is not left alone: its job was never removed. The season's stage is not
    asked, since the live job fires after a season completes as well. Attendance's gate is applied
    inside its clean-up change's check. Running a clean-up a second time finds nothing left to
    take down. The rounds are asked in the order they raced.
    """
    moment = queue.now()
    due: list[tuple[datetime, int]] = []
    async with get_connection(bot.db_path) as db:
        for round_id in await _standing_round_ids(db):
            try:
                facts = await round_facts_on(db, round_id)
            except ValueError:
                continue
            if facts is None or facts.division_status == DivisionStatus.CANCELLED:
                continue
            if facts.status == RoundStatus.CANCELLED and not facts.not_held:
                continue
            if clean_up_moment(facts.scheduled_at) <= moment:
                due.append((facts.scheduled_at, round_id))

    failures: dict[int, BaseException] = {}
    for _, round_id in sorted(due):
        log.info("recover_missed_check_in_clean_ups: taking down round %d's check-in", round_id)
        try:
            await ask_check_in_clean_up(queue, bot.db_path, round_id, origin=ChangeOrigin.BOT)
        except Exception as error:
            log.exception(
                "recover_missed_check_in_clean_ups: check-in cleanup failed for round %d", round_id
            )
            failures[round_id] = error
    if failures:
        raise RecoveryFailed(failures)
