"""Attendance's part of the builder: what the entry point asks of each module as it builds the bot.

It signs attendance up to the hooks core declares, registers attendance's switch reader, change
types and settings, the handlers of its kinds of timed job with the steps `/test-mode advance`
runs for them, and names the recoveries its start-up steps run. It is under the entry point's
rules, not a service's (architecture.md, "How the code is laid out"): it reads the bot as the
builder does, so each function below is handed the bot and reads a service off it when it signs up
or, where a lambda reads it, when it runs, which the tests that replace a service on the bot after
registering rely on. It holds no rule of attendance's, no database code and no clock of its own:
the recoveries are services, `check_in_recovery`'s, which it hands the bot and its queue, the
RSVP recovery the way to build the check-in buttons too, as the call's jobs are. Nothing imports it
but the entry point.
"""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leaguebot.attendance.services.check_in.check_in_changes import CheckInViewFactory
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.hooks import StartUpStep
    from leaguebot.core.utils.league_bot import LeagueBot


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
    from leaguebot.attendance.services.check_in import check_in_amendment

    bot.round_amended.subscribe(check_in_amendment.round_amended_on)


def sign_up_to_round_armed(bot: "LeagueBot") -> None:
    """Sign attendance's posts up to the hook an amended round's arming again declares."""
    from leaguebot.attendance.services.check_in import check_in_amendment

    bot.round_armed.subscribe(check_in_amendment.amendment_posts)


def sign_up_to_round_clean_ups_due(bot: "LeagueBot") -> None:
    """Sign attendance's clean-up up to the hook the clean-ups due a day after a round are read
    through."""
    from leaguebot.attendance.services.check_in import check_in_amendment

    bot.round_clean_ups_due.subscribe(check_in_amendment.amendment_clean_ups)


def _check_in_view() -> "CheckInViewFactory":
    """The way the call's jobs build the buttons a call carries: the cog's `RsvpView`, which a
    service may not import and so is handed."""
    from leaguebot.attendance.cogs.check_in_buttons import RsvpView

    return lambda round_id: RsvpView(round_id=round_id)


def sign_up_to_amendment_steps(bot: "LeagueBot") -> None:
    """Sign attendance's steps for an amendment up to the hook the steps each module defines for
    it are read through."""
    from leaguebot.attendance.services.check_in import check_in_amendment, check_in_clean_up

    bot.amendment_steps.subscribe(partial(
        check_in_amendment.amendment_steps,
        bot.attendance_service,
        bot.output_router,
        now=lambda: bot.change_queue.now(),
        view_of=_check_in_view(),
        seasons=bot.season_service,
        modules=bot.module_service,
        deletes=check_in_clean_up.check_in_deletes(bot, bot.attendance_service),
    ))


def sign_up_to_rounds_called_off(bot: "LeagueBot") -> None:
    """Sign attendance's take-down of the calls of the rounds called off up to the hook of the
    rounds called off, in the save."""
    from leaguebot.attendance.services.check_in import check_in_clean_up

    bot.rounds_called_off.subscribe(check_in_clean_up.take_down_called_off_on)


def sign_up_to_cancellation_notices(bot: "LeagueBot") -> None:
    """Sign attendance's check-in notice up to the hook the notices told of a division are read
    through."""
    from leaguebot.attendance.services import cancellation_notice

    bot.cancellation_notices.subscribe(cancellation_notice.notices_on)


def sign_up_to_cancellation_steps(bot: "LeagueBot") -> None:
    """Sign attendance's steps up to the hook the steps a cancellation plans are read through, in
    the order they run: the take-down of a call, the reading of a round's check-in record, then
    attendance's own notice."""
    from leaguebot.attendance.services import cancellation_notice
    from leaguebot.attendance.services.check_in import check_in_clean_up, check_in_record

    bot.cancellation_steps.subscribe(partial(
        check_in_clean_up.take_down_steps,
        seasons=bot.season_service,
        modules=bot.module_service,
        deletes=check_in_clean_up.check_in_deletes(bot, bot.attendance_service),
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
    """Make the queue known attendance's kinds of change: the four check-in jobs and the answer,
    `/attendance sync`, the module's switch, its eight settings and its channel settings."""
    from leaguebot.attendance.services.attendance_settings import attendance_config_settings
    from leaguebot.attendance.services.channel_settings import attendance_channel_settings
    from leaguebot.attendance.services.check_in.check_in_answer_changes import check_in_answer_change
    from leaguebot.attendance.services.check_in.check_in_call import check_in_call_change
    from leaguebot.attendance.services.check_in.check_in_clean_up import check_in_clean_up_change
    from leaguebot.attendance.services.check_in.check_in_deadline import check_in_deadline_change
    from leaguebot.attendance.services.check_in.check_in_last_notice import check_in_last_notice_change
    from leaguebot.attendance.services.module_switch_settings import (
        module_off_attendance,
        module_on_attendance,
    )
    from leaguebot.attendance.services.sanctions.attendance_sync_change import attendance_sync_change
    from leaguebot.core.services.queue.setting_change import setting_change

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
    from leaguebot.attendance.services.check_in.check_in_call import ask_check_in_call
    from leaguebot.attendance.services.check_in.check_in_clean_up import ask_check_in_clean_up
    from leaguebot.attendance.services.check_in.check_in_deadline import ask_check_in_deadline
    from leaguebot.attendance.services.check_in.check_in_amendment import (
        advance_steps as check_in_advance_steps,
    )
    from leaguebot.attendance.services.check_in.check_in_last_notice import ask_check_in_last_notice
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
    other, and is handed the bot's change queue, which a service does not look up on the bot."""
    from leaguebot.attendance.services.check_in import check_in_recovery

    return (
        # The check-in calls that fell due while the bot was down, their deadline still ahead,
        # posted late. Before the deadlines below, as a call comes before its deadline.
        ("missed check-in calls",
         lambda: check_in_recovery.recover_missed_check_in_calls(bot, queue=bot.change_queue)),
        # Persistent RSVP embed views re-armed for all stored embed messages, and missed RSVP
        # deadline jobs run for rounds whose deadline already passed.
        ("RSVP views and deadlines",
         lambda: check_in_recovery.recover_rsvp_views_and_deadlines(
             bot, queue=bot.change_queue, view_of=_check_in_view())),
        # The check-in messages due to come down while the bot was stopped. After the deadlines
        # above, so a check-in is distributed before it goes.
        ("missed check-in clean-ups",
         lambda: check_in_recovery.recover_missed_check_in_clean_ups(bot, queue=bot.change_queue)),
    )
