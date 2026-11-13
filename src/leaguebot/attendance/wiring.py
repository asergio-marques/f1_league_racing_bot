"""Attendance's part of the builder: what the entry point asks of each module as it builds the bot.

It signs attendance up to the hooks core declares, registers attendance's switch reader, change
types and settings, the handlers of its kinds of timed job with the steps `/test-mode advance`
runs for them, and holds the recoveries its start-up steps run. It is under the entry point's
rules, not a service's (architecture.md, "How the code is laid out"): it reads the bot as the
builder does, so each function below is handed the bot and reads a service off it when it signs up
or, where a lambda reads it, when it runs, which the tests that replace a service on the bot after
registering rely on. It holds no rule of attendance's and no database code of its own: the
recoveries that still read a table here are listed under attendance's pass in
`test_architecture_rules.py`, to become handlers of the start-up sweep. Nothing imports it but the
entry point.
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING

from leaguebot.core.models.round import read_scheduled_at

if TYPE_CHECKING:
    from leaguebot.attendance.services.check_in_changes import CheckInViewFactory
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.hooks import StartUpStep
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


def register_switch(modules: "ModuleService") -> None:
    """Sign attendance up with `ModuleService` for the reader of its own switch, keyed by the name
    `is_attendance_enabled` asks it by."""
    from leaguebot.attendance.services.module_switch_settings import attendance_enabled_on

    modules.register_switch("attendance", attendance_enabled_on)


def sign_up(bot: "LeagueBot") -> None:
    """Sign attendance up to each hook only attendance joins: none at present. The hooks several
    modules join are signed up by the entry point, in the order they are called, through the
    functions below."""


def sign_up_to_round_windows(bot: "LeagueBot") -> None:
    """Sign attendance's window up to the hook a round's timing windows read declares. It is
    handed whether the round is being amended and checks its own switch."""
    from leaguebot.attendance.services import attendance_hooks

    bot.round_windows_read.subscribe(partial(
        attendance_hooks.round_window, bot.attendance_service, bot.module_service
    ))


def sign_up_to_review_sections(bot: "LeagueBot") -> None:
    """Sign attendance's section up to the hook a season review gathers its lines through: handed
    which review is gathered, the season and its divisions, it checks its own switch."""
    from leaguebot.attendance.services import attendance_hooks

    bot.review_sections_read.subscribe(partial(
        attendance_hooks.review_section, bot.attendance_service, bot.module_service
    ))


def sign_up_to_round_amended(bot: "LeagueBot") -> None:
    """Sign attendance's reopening of its check-in up to the hook an amended round declares,
    inside the save."""
    from leaguebot.attendance.services import check_in_changes

    bot.round_amended.subscribe(check_in_changes.round_amended_on)


def sign_up_to_round_armed(bot: "LeagueBot") -> None:
    """Sign attendance's posts up to the hook an amended round's arming again declares."""
    from leaguebot.attendance.services import check_in_changes

    bot.round_armed.subscribe(check_in_changes.amendment_posts)


def sign_up_to_round_clean_ups_due(bot: "LeagueBot") -> None:
    """Sign attendance's clean-up up to the hook the clean-ups due a day after a round are read
    through."""
    from leaguebot.attendance.services import check_in_changes

    bot.round_clean_ups_due.subscribe(check_in_changes.amendment_clean_ups)


def _check_in_view() -> "CheckInViewFactory":
    """The way the call's jobs build the buttons a call carries: the cog's `RsvpView`, which a
    service may not import and so is handed."""
    from leaguebot.attendance.cogs.attendance_cog import RsvpView

    return lambda round_id: RsvpView(round_id=round_id)


def sign_up_to_amendment_steps(bot: "LeagueBot") -> None:
    """Sign attendance's steps for an amendment up to the hook the steps each module defines for
    it are read through."""
    from leaguebot.attendance.services import check_in_changes

    bot.amendment_steps.subscribe(partial(
        check_in_changes.amendment_steps,
        bot.attendance_service,
        bot.output_router,
        now=lambda: bot.change_queue.now(),
        view_of=_check_in_view(),
        seasons=bot.season_service,
        modules=bot.module_service,
        deletes=check_in_changes.check_in_deletes(bot, bot.attendance_service),
    ))


def sign_up_to_rounds_called_off(bot: "LeagueBot") -> None:
    """Sign attendance's take-down of the calls of the rounds called off up to the hook of the
    rounds called off, in the save."""
    from leaguebot.attendance.services import check_in_changes

    bot.rounds_called_off.subscribe(check_in_changes.take_down_called_off_on)


def sign_up_to_cancellation_notices(bot: "LeagueBot") -> None:
    """Sign attendance's check-in notice up to the hook the notices told of a division are read
    through."""
    from leaguebot.attendance.services import cancellation_notice

    bot.cancellation_notices.subscribe(cancellation_notice.notices_on)


def sign_up_to_cancellation_steps(bot: "LeagueBot") -> None:
    """Sign attendance's steps up to the hook the steps a cancellation plans are read through, in
    the order they run: the take-down of a call, the reading of a round's check-in record, then
    attendance's own notice."""
    from leaguebot.attendance.services import cancellation_notice, check_in_changes, check_in_record

    bot.cancellation_steps.subscribe(partial(
        check_in_changes.take_down_steps,
        seasons=bot.season_service,
        modules=bot.module_service,
        deletes=check_in_changes.check_in_deletes(bot, bot.attendance_service),
    ))
    bot.cancellation_steps.subscribe(partial(
        check_in_record.read_check_in_steps,
        modules=bot.module_service,
    ))
    bot.cancellation_steps.subscribe(partial(
        cancellation_notice.notify_checkin_steps,
        modules=bot.module_service,
        seasons=bot.season_service,
    ))


def sign_up_to_deletes(bot: "LeagueBot") -> None:
    """Sign attendance's deleting of its own rows up to the hooks a round, a division, a driver
    deleted or a driver merged declares, on the delete's connection, inside its save."""
    from leaguebot.attendance.services import attendance_hooks

    bot.rounds_deleted.subscribe(attendance_hooks.rounds_deleted_on)
    bot.divisions_deleted.subscribe(attendance_hooks.divisions_deleted_on)
    bot.profiles_deleted.subscribe(attendance_hooks.profiles_deleted_on)
    bot.profiles_merged.subscribe(attendance_hooks.profiles_merged_on)


def sign_up_to_pack(bot: "LeagueBot") -> None:
    """Sign attendance's clearing of its own rows tied to the server the bot leaves up to the hook
    the bot packed declares, on the pack's connection."""
    from leaguebot.attendance.services import attendance_hooks

    bot.bot_packed.subscribe(attendance_hooks.bot_packed_on)


def sign_up_to_division_lines_read(bot: "LeagueBot") -> None:
    """Sign attendance's two lines up to the hook the placements review gathers each division's
    module lines through, handed its service and the module service."""
    from leaguebot.attendance.services import attendance_hooks

    bot.division_lines_read.subscribe(
        partial(attendance_hooks.division_lines, bot.attendance_service, bot.module_service)
    )


def sign_up_to_channel_uses_read(bot: "LeagueBot") -> None:
    """Sign attendance up to the hook a channel being set is asked of each module through,
    handed the database's path."""
    from leaguebot.attendance.services import division_channels

    bot.channel_uses_read.subscribe(partial(division_channels.channel_uses, bot.db_path))


def sign_up_to_setting_channels_read(bot: "LeagueBot") -> None:
    """Sign attendance up to the hook the factory reset asks each module through for every channel
    it holds, handed the database's path."""
    from leaguebot.attendance.services import division_channels

    bot.setting_channels_read.subscribe(partial(division_channels.setting_channels, bot.db_path))


def sign_up_to_division_channel_faults_read(bot: "LeagueBot") -> None:
    """Sign attendance's lines naming its own channels not set or not on the server up to the hook
    a division's channel faults are read through, handed its service and the module service."""
    from leaguebot.attendance.services import attendance_hooks

    bot.division_channel_faults_read.subscribe(
        partial(
            attendance_hooks.division_channel_faults, bot.attendance_service, bot.module_service
        )
    )


def sign_up_to_fingerprint_areas_read(bot: "LeagueBot") -> None:
    """Sign attendance's rows of its own configuration up to the hook a season's fingerprint is
    taken through; it reads its channels by division, as the season service reads them."""
    from leaguebot.attendance.services import fingerprint_areas

    bot.fingerprint_areas_read.subscribe(
        partial(fingerprint_areas.fingerprint_areas, bot.season_service)
    )


def sign_up_to_pending_steps_read(bot: "LeagueBot") -> None:
    """Sign attendance's pending steps up to the hook `/test-mode advance` reads them through."""
    from leaguebot.attendance.services import test_mode_steps

    bot.pending_steps_read.subscribe(test_mode_steps.pending_steps)


def sign_up_to_review_marks_read(bot: "LeagueBot") -> None:
    """Sign attendance's marks up to the hook `/test-mode review` reads each module's marks
    through."""
    from leaguebot.attendance.services import test_mode_steps

    bot.review_marks_read.subscribe(test_mode_steps.review_marks)


def sign_up_to_placements_confirmed(bot: "LeagueBot") -> None:
    """Sign attendance's opening post up to the hooks the season's approval declares: the job it
    plans for a division, and the step it defines for its own job."""
    from leaguebot.attendance.services import sheet_posting

    bot.placements_confirmed.subscribe(sheet_posting.placements_confirmed_on)
    bot.placements_confirmed_steps.subscribe(partial(sheet_posting.opening_steps, bot.module_service))


def sign_up_to_season_completed(bot: "LeagueBot") -> None:
    """Sign attendance's final sheet up to the hooks a season completed declares."""
    from leaguebot.attendance.services import sheet_posting

    bot.season_completed.subscribe(sheet_posting.season_completed_on)
    bot.season_completed_steps.subscribe(
        partial(sheet_posting.final_steps, bot.module_service)
    )


def register_change_types(bot: "LeagueBot") -> None:
    """Make the queue known attendance's kinds of change: the sheet post, the four check-in jobs
    and the answer, the sanctions announcement, `/attendance sync`, the module's switch, its eight settings and its
    channel settings."""
    from leaguebot.attendance.services.attendance_settings import attendance_config_settings
    from leaguebot.attendance.services.channel_settings import attendance_channel_settings
    from leaguebot.attendance.services.check_in_answer_changes import check_in_answer_change
    from leaguebot.attendance.services.check_in_changes import (
        check_in_call_change,
        check_in_clean_up_change,
        check_in_deadline_change,
        check_in_last_notice_change,
    )
    from leaguebot.attendance.services.module_switch_settings import (
        module_off_attendance,
        module_on_attendance,
    )
    from leaguebot.attendance.services.sanctions_announce_change import (
        sanctions_announce_change,
    )
    from leaguebot.attendance.services.attendance_sync_change import attendance_sync_change
    from leaguebot.attendance.services.sheet_posting import sheet_post_change
    from leaguebot.core.services.queue.setting_change import setting_change

    bot.change_queue.register(sheet_post_change(bot.module_service))
    bot.change_queue.register(
        check_in_call_change(
            bot.attendance_service, bot.output_router,
            now=lambda: bot.change_queue.now(), view_of=_check_in_view(),
        )
    )
    bot.change_queue.register(
        check_in_last_notice_change(bot.attendance_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        check_in_deadline_change(bot.attendance_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(check_in_clean_up_change())
    bot.change_queue.register(check_in_answer_change(bot.attendance_service, _check_in_view()))
    bot.change_queue.register(
        sanctions_announce_change(
            attendance=bot.attendance_after_review, now=lambda: bot.change_queue.now()
        )
    )
    bot.change_queue.register(
        attendance_sync_change(
            placement=bot.placement_service, roles=bot.team_role_service,
            lineups=bot.lineup_poster, attendance=bot.attendance_after_review,
            modules=bot.module_service, now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        setting_change(
            module_on_attendance(modules=bot.module_service, seasons=bot.season_service)
        )
    )
    bot.change_queue.register(
        setting_change(
            module_off_attendance(modules=bot.module_service, attendance=bot.attendance_service)
        )
    )
    for setting in attendance_config_settings(
        modules=bot.module_service, seasons=bot.season_service, attendance=bot.attendance_service,
    ):
        bot.change_queue.register(setting_change(setting))
    for setting in attendance_channel_settings(
        modules=bot.module_service, seasons=bot.season_service,
        channel_of=lambda channel_id: bot.get_channel(channel_id),
        channel_uses_read=bot.channel_uses_read,
    ):
        bot.change_queue.register(setting_change(setting))


def register_scheduler_callbacks(bot: "LeagueBot") -> None:
    """Sign up the handler of attendance's four check-in kinds of job through `register_timer`,
    and beside them the step `/test-mode advance` runs for each, signed up on the bot's
    `advance_steps`.

    Each asks the change queue, read from the bot when it fires. The bot keeps the one queue
    `build` made; a test that replaces the queue must register these handlers again."""
    from leaguebot.attendance.services.check_in_changes import (
        advance_steps as check_in_advance_steps,
        ask_check_in_call,
        ask_check_in_clean_up,
        ask_check_in_deadline,
        ask_check_in_last_notice,
    )
    from leaguebot.core.models.change import ChangeOrigin

    scheduler = bot.scheduler_service

    async def _rsvp_notice_cb(round_id: int) -> None:
        await ask_check_in_call(
            bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
        )

    async def _rsvp_last_notice_cb(round_id: int) -> None:
        await ask_check_in_last_notice(
            bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
        )

    async def _rsvp_deadline_cb(round_id: int) -> None:
        await ask_check_in_deadline(
            bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
        )

    async def _rsvp_cleanup_cb(round_id: int) -> None:
        await ask_check_in_clean_up(
            bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
        )

    scheduler.register_timer("rsvp_notice", _rsvp_notice_cb)
    scheduler.register_timer("rsvp_last_notice", _rsvp_last_notice_cb)
    scheduler.register_timer("rsvp_deadline", _rsvp_deadline_cb)
    scheduler.register_timer("rsvp_cleanup", _rsvp_cleanup_cb)
    for kind, advance in check_in_advance_steps(bot.change_queue, bot.db_path).items():
        bot.advance_steps.register_step(kind, advance)



def start_up_steps(bot: "LeagueBot") -> "tuple[StartUpStep, ...]":
    """Attendance's start-up steps, named and in the order they run in attendance's turn of the
    start-up sweep (the entry point signs them up to `bot.starting` under attendance's switch key).
    Each recovery is looked up when its step runs, so a step stands for that function and no
    other."""
    return (
        # The check-in calls that fell due while the bot was down, their deadline still ahead,
        # posted late. Before the deadlines below, as a call comes before its deadline.
        ("missed check-in calls", lambda: recover_missed_check_in_calls(bot)),
        # Persistent RSVP embed views re-armed for all stored embed messages, and missed RSVP
        # deadline jobs run for rounds whose deadline already passed.
        ("RSVP views and deadlines", lambda: recover_rsvp_views_and_deadlines(bot)),
        # The check-in messages due to come down while the bot was stopped. After the deadlines
        # above, so a check-in is distributed before it goes.
        ("missed check-in clean-ups", lambda: recover_missed_check_in_clean_ups(bot)),
    )



async def recover_missed_check_in_calls(
    bot: LeagueBot, *, now: datetime | None = None
) -> None:
    """Post late the check-in calls that fell due while the bot was down.

    A call's job is one of those the scheduler discards when the bot comes back more than its
    misfire grace after it, and nothing else posted it: the round asked nobody whether they
    were racing, opened no attendance rows, and counted nothing against anyone. A call that came
    due is posted late, provided the round's check-in deadline is still ahead, and the drivers
    answer in the time left. The deadline boundary is the one `/attendance post-check-in` holds to —
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
    deadline. *now* is the wall clock unless a test pins it.
    """
    from datetime import timedelta, timezone

    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.round import ROUND_CANCELLABLE
    from leaguebot.core.models.season import ONGOING_STAGES
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    moment = now or datetime.now(timezone.utc)
    ongoing = [stage.value for stage in ONGOING_STAGES]
    to_run = sorted(ROUND_CANCELLABLE)
    try:
        async with get_connection(bot.db_path) as db:
            cursor = await db.execute(
                f"""
                SELECT r.id AS round_id, r.round_number, r.scheduled_at,
                       d.id AS division_id, d.name AS division_name,
                       s.season_number,
                       ac.rsvp_notice_days, ac.rsvp_deadline_hours
                  FROM rounds r
                  JOIN divisions d ON d.id = r.division_id
                  JOIN seasons s ON s.id = d.season_id
                  CROSS JOIN attendance_config ac
                 WHERE ac.module_enabled = 1
                   AND s.stage IN ({",".join("?" for _ in ongoing)})
                   AND r.status IN ({",".join("?" for _ in to_run)})
                   AND d.status != 'CANCELLED'
                   AND r.checkin_cleared = 0
                   AND NOT EXISTS (
                       SELECT 1 FROM rsvp_embed_messages rem WHERE rem.round_id = r.id
                   )
                   AND NOT EXISTS (SELECT 1 FROM server_configs WHERE test_mode_active = 1)
                 ORDER BY r.scheduled_at, r.id
                """,
                [*ongoing, *to_run],
            )
            rows = await cursor.fetchall()
    except Exception:
        log.exception("_recover_missed_check_in_calls: failed to read the rounds owed a call")
        return

    for row in rows:
        try:
            scheduled_at = read_scheduled_at(str(row["scheduled_at"]))
        except ValueError:
            continue
        due_at = scheduled_at - timedelta(days=row["rsvp_notice_days"] or 0)
        deadline_at = scheduled_at - timedelta(hours=row["rsvp_deadline_hours"] or 0)
        if moment < due_at:
            continue

        # The change judges the deadline when it runs: it posts the call while the deadline is
        # ahead, and gives it up, closing the round's check-in, once it has passed.
        log.info("_recover_missed_check_in_calls: asking for round %d's call", row["round_id"])
        try:
            await ask_check_in_call(
                bot.change_queue, bot.db_path, row["round_id"], origin=ChangeOrigin.BOT,
                late=moment < deadline_at,
            )
        except Exception:
            log.exception(
                "_recover_missed_check_in_calls: could not ask for round %d's call",
                row["round_id"],
            )


async def recover_rsvp_views_and_deadlines(bot: LeagueBot) -> None:
    """Re-arm RsvpView buttons and run missed RSVP deadline jobs on bot restart.

    Re-arm persistent RsvpView for every row in rsvp_embed_messages so button interactions survive
    bot restarts (FR-007).

    For any call whose rsvp_deadline fire time has already passed and which carries no
    mark of its deadline having run, ask the change queue for the deadline (FR-027).

    **The call's own mark decides, never the drivers' placements**. A deadline writes its
    distribution and marks the call (`deadline_run_at`) in one save, before it posts its
    announcement or its no-reserve notice; that is how `AttendanceService.get_current_embed_message`
    tells a deadline that has run as well, and the two must agree. The placements cannot: a
    deadline with no reserve to place writes none, so it would be run again at every start and
    its notice posted again, and a call posted again after an amendment carries over the
    placements of the call it replaced, so its own deadline would never be caught up. The
    announcement's id cannot either: a discarded announcement leaves none, and the deadline has
    run all the same.

    For any round whose rsvp_last_notice fire time has already passed, silently skip — do NOT fire
    retroactively (FR-029 edge case).

    A restart is one of the three paths the core specification names, so none of this runs
    for a server whose attendance module is switched off. Re-arming the buttons
    would revive check-in for a league that had turned it off, and the missed-deadline
    catch-up would distribute its reserves into seats hours after the fact.
    """
    from leaguebot.attendance.services.check_in_changes import (
        CHECK_IN_DEADLINE,
        ask_check_in_deadline,
    )
    from leaguebot.attendance.cogs.attendance_cog import RsvpView
    from leaguebot.core.db.database import get_connection as _gc
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.queue.queue_records import unfinished
    from datetime import datetime as _dt, timezone as _tz

    # Re-arm all embed views by message_id so persistent buttons survive restarts
    try:
        embed_rows = await bot.attendance_service.get_all_embed_messages()
    except Exception:
        log.exception("_recover_rsvp_views_and_deadlines: failed to fetch embed messages")
        return

    # Re-arm nothing while the attendance module is disabled.
    try:
        async with _gc(bot.db_path) as db:
            cur = await db.execute(
                """
                SELECT DISTINCT rem.round_id
                  FROM rsvp_embed_messages rem
                  JOIN rounds r ON r.id = rem.round_id
                 WHERE EXISTS (SELECT 1 FROM attendance_config WHERE module_enabled = 1)
                """
            )
            enabled_round_ids = {r[0] for r in await cur.fetchall()}
    except Exception:
        log.exception("_recover_rsvp_views_and_deadlines: failed to resolve enabled rounds")
        return

    for row in embed_rows:
        if row.round_id not in enabled_round_ids:
            log.info(
                "_recover_rsvp_views_and_deadlines: attendance module disabled for round %s — "
                "view not re-armed",
                row.round_id,
            )
            continue
        try:
            bot.add_view(RsvpView(round_id=row.round_id), message_id=int(row.message_id))
        except Exception as exc:
            log.warning(
                "_recover_rsvp_views_and_deadlines: could not re-arm view for msg %s: %s",
                row.message_id, exc, exc_info=True,
            )

    # Check for missed deadline jobs
    now_utc = _dt.now(_tz.utc)
    try:
        async with _gc(bot.db_path) as db:
            cur = await db.execute(
                """
                SELECT DISTINCT rem.round_id, rem.division_id,
                       r.scheduled_at,
                       ac.rsvp_deadline_hours
                  FROM rsvp_embed_messages rem
                  JOIN rounds r ON r.id = rem.round_id
                  JOIN divisions d ON d.id = r.division_id
                  JOIN seasons s ON s.id = d.season_id
                  CROSS JOIN attendance_config ac
                 WHERE r.status != 'CANCELLED'
                   AND s.status = 'ACTIVE'
                   AND ac.module_enabled = 1
                   AND rem.deadline_run_at IS NULL
                """
            )
            round_rows = await cur.fetchall()
    except Exception:
        log.exception("_recover_rsvp_views_and_deadlines: failed to fetch rounds for deadline check")
        return

    try:
        in_hand = {int(each["round_id"]) for each in await unfinished(bot.db_path, [CHECK_IN_DEADLINE])}
    except Exception:
        log.exception("_recover_rsvp_views_and_deadlines: failed to read the deadlines in hand")
        return

    for rrow in round_rows:
        scheduled_at_raw = rrow["scheduled_at"]
        try:
            scheduled_at = read_scheduled_at(scheduled_at_raw)
        except (ValueError, TypeError):
            continue

        deadline_hours = rrow["rsvp_deadline_hours"] or 0
        from datetime import timedelta as _td
        deadline_at = scheduled_at - _td(hours=deadline_hours)

        if deadline_at <= now_utc:
            # Deadline has passed and the call records no message from it, so it has not run.
            round_id = rrow["round_id"]
            division_id = rrow["division_id"]
            if round_id in in_hand:
                continue  # already on the queue: its jobs are in hand
            log.info(
                "_recover_rsvp_views_and_deadlines: asking for missed deadline of round %d / division %d",
                round_id, division_id,
            )
            try:
                await ask_check_in_deadline(
                    bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
                )
            except Exception:
                log.exception(
                    "_recover_rsvp_views_and_deadlines: deadline run failed for round %d",
                    round_id,
                )


async def recover_missed_check_in_clean_ups(bot: "LeagueBot") -> None:
    """Take down the check-in calls, with their last notices and distribution messages, whose
    clean-up fell due while the bot was down: attendance's part of the start-up's missed clean-ups.

    A call comes down `POST_RACE_CLEANUP_DELAY` after its round's start, by the ``rsvp_cleanup``
    job. A job falling due while the bot is stopped is discarded by the scheduler's misfire grace
    rather than run late, and nothing else would take those messages down.

    A round called off, or of a division called off, is left alone: its cancellation removed the
    job. A round that ended cancelled because every session was entered as not held
    (`rounds.not_held`) is not left alone: its job was never removed. The season's status is not
    asked, since the live job fires after a season completes as well. Attendance's gate is applied
    inside its clean-up change's check. Running a clean-up a second time finds nothing left to
    take down.
    """
    from datetime import timezone

    from leaguebot.core.db.database import get_connection
    from leaguebot.attendance.services.check_in_changes import ask_check_in_clean_up
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.rounds.round_moments import POST_RACE_CLEANUP_DELAY

    now = datetime.now(timezone.utc)

    def _due(scheduled_at_raw: object) -> bool:
        try:
            scheduled_at = read_scheduled_at(str(scheduled_at_raw))
        except ValueError:
            return False
        return scheduled_at + POST_RACE_CLEANUP_DELAY <= now

    try:
        async with get_connection(bot.db_path) as db:
            cursor = await db.execute(
                """
                SELECT DISTINCT rem.round_id, r.scheduled_at
                  FROM rsvp_embed_messages rem
                  JOIN rounds r ON r.id = rem.round_id
                  JOIN divisions d ON d.id = r.division_id
                 WHERE (r.status != 'CANCELLED' OR r.not_held = 1)
                   AND d.status != 'CANCELLED'
                 ORDER BY r.scheduled_at, rem.round_id
                """
            )
            check_ins = [r["round_id"] for r in await cursor.fetchall() if _due(r["scheduled_at"])]
    except Exception:
        log.exception("recover_missed_check_in_clean_ups: failed to read the messages still standing")
        return

    for round_id in check_ins:
        log.info("recover_missed_check_in_clean_ups: taking down round %d's check-in", round_id)
        try:
            await ask_check_in_clean_up(
                bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
            )
        except Exception:
            log.exception(
                "recover_missed_check_in_clean_ups: check-in cleanup failed for round %d", round_id
            )
