"""Results' part of the builder: what the entry point asks of each module as it builds the bot.

It signs results up to the hooks core declares, registers results' switch reader, change types and
settings, the handlers of its kinds of timed job with the steps `/test-mode advance` runs for them,
and names the start-up steps it runs: the arming of the amendment sweep and the recoveries. It is
under the entry point's rules, not a service's (architecture.md, "How the code is laid out"): it
reads the bot as the builder does, so each function below is handed the bot (or what the builder
hands results of another module) and reads a service off it when it signs up or, where a lambda
reads it, when it runs, which the tests that replace a service on the bot after registering rely
on. It holds no rule of results' and no database code of its own: each start-up step stands for a
function of results' services, which reads and decides. Nothing imports it but the entry point.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.hooks import StartUpStep
    from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview
    from leaguebot.core.utils.league_bot import LeagueBot


def register_switch(modules: "ModuleService") -> None:
    """Sign results up with `ModuleService` for the reader of its own switch, keyed by the name
    `is_results_enabled` asks it by."""
    from leaguebot.results.services.module.module_switch_settings import results_enabled_on

    modules.register_switch("results", results_enabled_on)


def sign_up(bot: "LeagueBot") -> None:
    """Sign results up to each hook only results joins. The hooks several modules join are signed
    up by the entry point, in the order they are called, through the functions below."""
    sign_up_to_confirmation_refused(bot)
    sign_up_to_cancellation_refused(bot)
    sign_up_to_rounds_made_final(bot)
    sign_up_to_test_mode_turned_on(bot)
    sign_up_to_divisions_raced_read(bot)
    sign_up_to_season_standing_read(bot)
    sign_up_to_final_round_read(bot)
    sign_up_to_season_end_refused(bot)


def sign_up_to_confirmation_refused(bot: "LeagueBot") -> None:
    """Sign results' refusals up to the hook a season's confirmation is refused through: handed the
    league's modules and the database, then the season and which confirmation is judged. Results
    checks its own switch."""
    from leaguebot.results.services.points import season_points_service

    bot.confirmation_refused.subscribe(partial(
        season_points_service.confirmation_refusals, bot.module_service, bot.db_path
    ))


def sign_up_to_cancellation_refused(bot: "LeagueBot") -> None:
    """Sign up results' refusal of a round whose submission accepted results, to the hook of what
    stands in the way of a cancellation."""
    from leaguebot.results.services.submission import result_submission_service

    bot.cancellation_refused.subscribe(result_submission_service.cancellation_refusal)


def sign_up_to_rounds_made_final(bot: "LeagueBot") -> None:
    """Sign results' marking of the drivers who raced each round as former drivers up to the hook a
    round's results made final declares, on the closing's connection, inside its save
    (architecture.md, "How modules and core fit together"). The builder hands it to the two closers
    that make a round FINAL: results turned off, and a season cancelled."""
    from leaguebot.results.services.submission import result_submission_service

    bot.rounds_made_final.subscribe(result_submission_service.rounds_made_final_on)


def sign_up_to_test_mode_turned_on(bot: "LeagueBot") -> None:
    """Sign results' seeding for a season under test up to the hook test mode turned on declares,
    on the connection of the save that turns test mode on, given the season's id (architecture.md,
    "How modules and core fit together"). Results' seeding runs whether or not the results module
    is on, as the core specification's test mode section says without a condition."""
    from leaguebot.results.services.points import points_config_service

    bot.test_mode_turned_on.subscribe(points_config_service.seed_test_configs_on)


def sign_up_to_divisions_raced_read(bot: "LeagueBot") -> None:
    """Sign results' read of its own records up to the hook `/driver reassign` asks which divisions
    a driver's accounts raced in through, handed the connection of the check or the save that asks
    and the accounts."""
    from leaguebot.results.services.module import results_purge_service

    bot.divisions_raced_read.subscribe(results_purge_service.divisions_raced_on)


def sign_up_to_season_standing_read(bot: "LeagueBot") -> None:
    """Sign results' read of its own standings up to the hook a season's history is written
    through, handed the connection of the save that writes the history, the division and the
    driver's accounts."""
    from leaguebot.results.services.standings import season_standings_posts

    bot.season_standing_read.subscribe(season_standings_posts.season_standing_on)


def sign_up_to_final_round_read(bot: "LeagueBot") -> None:
    """Sign results' read of its own `session_results` up to the hook a division's final round is
    read through."""
    from leaguebot.results.services.standings import season_standings_posts

    bot.final_round_read.subscribe(season_standings_posts.final_round_on)


def sign_up_to_season_end_refused(bot: "LeagueBot") -> None:
    """Sign results' refusal of a season with a round being amended up to the hook what stands in
    the way of a season's completion and of its cancellation is read through."""
    from leaguebot.results.services.round_amendment import amendment_writes

    bot.season_end_refused.subscribe(amendment_writes.season_end_refusal)


def sign_up_to_review_sections(bot: "LeagueBot") -> None:
    """Sign results' section up to the hook a season review gathers its lines through: handed
    which review is gathered, the season and its divisions, it checks its own switch."""
    from leaguebot.results.services.points import season_points_service

    bot.review_sections_read.subscribe(partial(
        season_points_service.review_section, bot.module_service, bot.db_path
    ))


def sign_up_to_rounds_called_off(bot: "LeagueBot") -> None:
    """Sign results' closing of the empty submissions of the rounds called off up to the hook of
    the rounds called off, in the save."""
    from leaguebot.results.services.submission import result_submission_service

    bot.rounds_called_off.subscribe(result_submission_service.close_called_off_on)


def sign_up_to_cancellation_notices(bot: "LeagueBot") -> None:
    """Sign results' note up to the hook the notices told of a division are gathered through."""
    from leaguebot.results.services.publishing import cancellation_notice as results_cancellation_notice

    bot.cancellation_notices.subscribe(results_cancellation_notice.notices_on)


def sign_up_to_cancellation_steps(bot: "LeagueBot") -> None:
    """Sign results' deletion of a submission channel up to the hook the steps a cancellation
    plans are gathered through."""
    from leaguebot.results.services.review import review_posting

    bot.cancellation_steps.subscribe(review_posting.cancellation_steps)


def sign_up_to_cancellation_notice_steps(bot: "LeagueBot") -> None:
    """Sign results' notice of a cancellation up to the hook the steps a cancellation plans are
    gathered through."""
    from leaguebot.results.services.publishing import cancellation_notice as results_cancellation_notice

    bot.cancellation_steps.subscribe(partial(
        results_cancellation_notice.notify_results_steps,
        modules=bot.module_service,
        seasons=bot.season_service,
    ))


def sign_up_to_deletes(bot: "LeagueBot") -> None:
    """Sign results up to the hooks a season, a division, a round or a driver deleted, or a driver
    merged, declares: deleting or re-pointing its own rows, on the delete's connection, inside its
    save (architecture.md, "The database")."""
    from leaguebot.results.services.module import results_purge_service

    bot.rounds_deleted.subscribe(results_purge_service.rounds_deleted_on)
    bot.divisions_deleted.subscribe(results_purge_service.divisions_deleted_on)
    bot.season_deleted.subscribe(results_purge_service.season_deleted_on)
    bot.profiles_deleted.subscribe(results_purge_service.profiles_deleted_on)
    bot.profiles_merged.subscribe(results_purge_service.profiles_merged_on)


def sign_up_to_pack(bot: "LeagueBot") -> None:
    """Sign results' clearing of its own rows tied to the server the bot leaves up to the hook the
    bot packed declares, on the pack's connection, inside its one transaction (architecture.md,
    "The database")."""
    from leaguebot.results.services.module import results_purge_service

    bot.bot_packed.subscribe(results_purge_service.bot_packed_on)


def sign_up_to_division_lines_read(bot: "LeagueBot") -> None:
    """Sign results' three channels up to the hook the placements review gathers each division's
    module lines through, handed the database, the season and the divisions the review shows."""
    from leaguebot.results.services.module import division_channels

    bot.division_lines_read.subscribe(partial(division_channels.division_lines, bot.db_path))


def sign_up_to_channel_uses_read(bot: "LeagueBot") -> None:
    """Sign results' uses of a channel up to the hook a channel being set is asked of each module
    through, handed the database's path."""
    from leaguebot.results.services.module import division_channels as results_channels

    bot.channel_uses_read.subscribe(partial(results_channels.channel_uses, bot.db_path))


def sign_up_to_setting_channels_read(bot: "LeagueBot") -> None:
    """Sign results' channels up to the hook the factory reset asks each module through for every
    channel it holds, handed the database's path."""
    from leaguebot.results.services.module import division_channels as results_channels

    bot.setting_channels_read.subscribe(partial(results_channels.setting_channels, bot.db_path))


def sign_up_to_division_channel_faults_read(bot: "LeagueBot") -> None:
    """Sign results' lines naming its own channels not set or not on the server up to the hook a
    division's channel faults are read through, handed the module service and the database."""
    from leaguebot.results.services.module import division_channels

    bot.division_channel_faults_read.subscribe(
        partial(division_channels.division_channel_faults, bot.module_service, bot.db_path)
    )


def sign_up_to_fingerprint_areas_read(bot: "LeagueBot") -> None:
    """Sign results' rows of its own configuration up to the hook a season's fingerprint is taken
    through, by area, handed the season service. Results reads its channels by division, as the
    season service reads the divisions of the season."""
    from leaguebot.results.services.module import fingerprint_areas as results_areas

    bot.fingerprint_areas_read.subscribe(partial(results_areas.fingerprint_areas, bot.season_service))


def sign_up_to_pending_steps_read(bot: "LeagueBot") -> None:
    """Sign results' pending steps up to the hook `/test-mode advance` reads the pending steps
    through."""
    from leaguebot.results.services.module import test_mode_steps as results_steps

    bot.pending_steps_read.subscribe(results_steps.pending_steps)


def sign_up_to_review_marks_read(bot: "LeagueBot") -> None:
    """Sign results' marks up to the hook `/test-mode review` reads each module's marks through."""
    from leaguebot.results.services.module import test_mode_steps as results_steps

    bot.review_marks_read.subscribe(results_steps.review_marks)


def sign_up_to_placements_confirmed(bot: "LeagueBot") -> None:
    """Sign results' opening posts up to the hooks placements confirmed (the season's approval)
    declares: the jobs it plans for a division and the step it defines for its own job. Results
    signs up first, so each division's jobs stay lineup, calendar, standings, sheet."""
    from leaguebot.results.services.standings import season_standings_posts

    bot.placements_confirmed.subscribe(season_standings_posts.placements_confirmed_on)
    bot.placements_confirmed_steps.subscribe(
        partial(season_standings_posts.opening_steps, bot.module_service)
    )


def sign_up_to_season_completed(bot: "LeagueBot") -> None:
    """Sign results' final post up to the hooks a season completed declares: its final standings,
    and the step it defines for them. Results signs up first."""
    from leaguebot.results.services.standings import season_standings_posts

    bot.season_completed.subscribe(season_standings_posts.season_completed_on)
    bot.season_completed_steps.subscribe(
        partial(season_standings_posts.final_steps, bot.module_service)
    )


def sign_up_to_in_memory_state_cleared(bot: "LeagueBot") -> None:
    """Sign up what results holds in memory to the hook the bot's in-memory state is cleared
    through (`/bot pack`, `/bot factory-reset`)."""
    from leaguebot.results.services.review.review_state import clear_held_reviews

    bot.in_memory_state_cleared.subscribe(clear_held_reviews)


def register_change_types(
    bot: "LeagueBot",
    *,
    attendance: "AttendanceAfterReview",
    attendance_off_on: Callable[[aiosqlite.Connection], Awaitable[bool]],
) -> None:
    """Make the queue known results' change types and settings.

    Results reaches attendance only through what the entry point hands it: *attendance* is
    attendance's share of a round's review (`AttendanceAfterReview`), and *attendance_off_on* its
    switch-off, which results turned off carries out for it.
    """
    from leaguebot.core.services.seasons.season_lifecycle_service import steps_of_the_modules
    from leaguebot.core.services.queue.setting_change import setting_change
    from leaguebot.results.services.round_amendment.amend_changes import amend_open_change
    from leaguebot.results.services.round_amendment.amend_changes import TIMER as AMEND_TIMER
    from leaguebot.results.services.round_amendment.amend_end_changes import (
        amend_cancel_change,
        amend_lapse_change,
        amend_lapse_timer,
    )
    from leaguebot.results.services.round_amendment.amend_turn_change import (
        amend_close_change,
        amend_config_change,
        amend_stage_one_change,
        amend_turn_change,
    )
    from leaguebot.results.services.points.amendment_settings import amendment_settings
    from leaguebot.results.services.round_amendment.amendment_stage_changes import amendment_stage_changes
    from leaguebot.results.services.review.appeals_approval_change import appeals_approval_change
    from leaguebot.results.services.module.channel_settings import results_channel_settings
    from leaguebot.results.services.module.module_switch_settings import module_on_results
    from leaguebot.results.services.points.points_amendment_change import points_amendment_change
    from leaguebot.results.services.points.points_settings import points_settings
    from leaguebot.results.services.review.report_approval_change import report_approval_change
    from leaguebot.results.services.module.results_off_change import results_off_change
    from leaguebot.results.services.submission.resubmission_changes import (
        resubmission_start_change,
        resubmission_swap_change,
    )
    from leaguebot.results.services.review.review_confirm_change import review_confirm_change
    from leaguebot.results.services.review.review_lock_change import (
        TIMER,
        lock_change,
        lock_clear_change,
        lock_clear_timer,
    )
    from leaguebot.results.services.review.review_open_change import (
        appeals_open_change,
        close_stale_change,
        review_open_change,
        review_prompt_change,
    )
    from leaguebot.results.services.review.review_stage_change import review_stage_change
    from leaguebot.results.services.publishing.rounds_sync_change import rounds_sync_change
    from leaguebot.results.services.standings.standings_sync_change import standings_sync_change
    from leaguebot.results.services.submission.submission_changes import (
        submission_all_cancelled_change,
        submission_close_change,
        submission_open_change,
        submission_restart_change,
    )
    from leaguebot.results.services.submission.submission_turn_change import (
        submission_config_change,
        submission_turn_change,
    )

    bot.change_queue.register(
        results_off_change(
            attendance_off_on=attendance_off_on,
            attendance_enabled=bot.module_service.is_attendance_enabled,
            rounds_made_final=bot.rounds_made_final,
            disarm_amendment_sweep=bot.scheduler_service.cancel_amendment_sweep,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(submission_open_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(submission_restart_change())
    bot.change_queue.register(
        submission_all_cancelled_change(
            seasons=bot.season_service,
            drawing=bot.image_drawing,
            round_ended_not_held=bot.round_ended_not_held,
            cancellation_notices=bot.cancellation_notices,
            cancellation_steps=steps_of_the_modules(bot.cancellation_steps),
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(submission_close_change())
    bot.change_queue.register(
        resubmission_start_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config
        )
    )
    bot.change_queue.register(resubmission_swap_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(
        submission_turn_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(), resubmission=True,
        )
    )
    bot.change_queue.register(
        submission_config_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(), resubmission=True,
        )
    )
    bot.change_queue.register(
        submission_turn_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        submission_config_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(),
        )
    )
    # A change type or timer helper whose parameter is a getter of the queue (`queue_of`, `queue`
    # given a lambda) is handed `lambda: bot.change_queue`, as its signature asks: it reads the
    # bot's queue when it runs. The bot keeps the one queue `build` made, and the tests'
    # `restart_queue` sets a new one on it and registers again, which a getter and a queue handed
    # over would both follow; the getter is kept for the signature's sake.
    bot.change_queue.register(
        amend_open_change(
            modules=bot.module_service, server_config=bot.config_service.get_server_config,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
            queue_of=lambda: bot.change_queue,
        )
    )
    bot.change_queue.register(amend_cancel_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(amend_lapse_change(now=lambda: bot.change_queue.now()))

    # The timer asks the queue when it fires, by the helper's getter, as above.
    bot.scheduler_service.register_timer(
        AMEND_TIMER, amend_lapse_timer(bot.db_path, lambda: bot.change_queue)
    )
    bot.change_queue.register(
        amend_turn_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        amend_config_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        amend_stage_one_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            teams=bot.team_service,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(amend_close_change())
    bot.change_queue.register(
        review_open_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            router=bot.output_router,
        )
    )
    bot.change_queue.register(
        review_prompt_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            router=bot.output_router,
        )
    )
    bot.change_queue.register(review_stage_change())
    bot.change_queue.register(review_confirm_change())
    bot.change_queue.register(
        lock_change(
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once
        )
    )
    bot.change_queue.register(lock_clear_change())
    # The lock-clearing timer asks the queue when it fires, by the helper's getter.
    bot.scheduler_service.register_timer(
        TIMER, lock_clear_timer(bot.db_path, lambda: bot.change_queue)
    )
    bot.change_queue.register(
        appeals_open_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            router=bot.output_router,
        )
    )
    bot.change_queue.register(close_stale_change())
    # The review's change types stamp what they write with the queue's own clock, read when they
    # run, as the getters above are, and reach attendance through the hook alone.
    bot.change_queue.register(
        report_approval_change(attendance=attendance, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        appeals_approval_change(attendance=attendance, now=lambda: bot.change_queue.now())
    )
    for stage in amendment_stage_changes(
        attendance=attendance,
        now=lambda: bot.change_queue.now(),
        queue=bot.change_queue,
        server_config=bot.config_service.get_server_config,
        router=bot.output_router,
    ):
        bot.change_queue.register(stage)
    bot.change_queue.register(
        points_amendment_change(
            attendance=attendance,
            now=lambda: bot.change_queue.now(),
            module_service=bot.module_service,
        )
    )
    bot.change_queue.register(rounds_sync_change(bot.season_service, bot.module_service))
    bot.change_queue.register(standings_sync_change(bot.season_service, bot.module_service))
    bot.change_queue.register(
        setting_change(
            module_on_results(
                modules=bot.module_service,
                seasons=bot.season_service,
                scheduler=bot.scheduler_service,
            )
        )
    )
    for setting in (
        *points_settings(modules=bot.module_service, seasons=bot.season_service),
        *amendment_settings(modules=bot.module_service, seasons=bot.season_service),
        *results_channel_settings(
            modules=bot.module_service, seasons=bot.season_service,
            channel_of=lambda channel_id: bot.get_channel(channel_id),
            channel_uses_read=bot.channel_uses_read,
        ),
    ):
        bot.change_queue.register(setting_change(setting))


def register_scheduler_callbacks(bot: "LeagueBot") -> None:
    """Sign up the handler of results' kinds of job through `register_timer`, the result
    submission and the amendment sweep, and beside the submission's the step `/test-mode advance`
    runs for it, signed up on the bot's `advance_steps`.

    Each asks the change queue, read from the bot when it fires. The bot keeps the one queue
    `build` made; a test that replaces the queue must register these handlers again."""
    from leaguebot.results.services.submission.round_start import ask_round_start
    from leaguebot.results.services.submission.submission_changes import advance_step

    scheduler = bot.scheduler_service

    async def _result_submission_cb(round_id: int) -> None:
        await ask_round_start(
            bot.change_queue, bot.db_path, round_id, came_up_at=lambda: bot.came_up_at,
        )

    scheduler.register_timer("results", _result_submission_cb)
    bot.advance_steps.register_step("results", advance_step(bot.change_queue, bot.db_path))

    # The standing sweep that undoes an amendment nobody carried through; start-up arms it.
    async def _amendment_sweep_cb() -> None:
        from leaguebot.results.services.round_amendment.amendment_writes import sweep_expired_amendments
        await sweep_expired_amendments(
            bot.db_path, queue=bot.change_queue, now=bot.change_queue.now()
        )

    scheduler.register_timer("amendment_sweep", _amendment_sweep_cb)



def start_up_steps(bot: "LeagueBot") -> "tuple[StartUpStep, ...]":
    """Results' start-up steps, named and in the order they run in results' turn of the start-up
    sweep (the entry point signs them up to `bot.starting` under results' switch key). Each
    recovery is looked up when its step runs, so a step stands for that function and no other."""
    from leaguebot.results.services.round_amendment.amend_end_changes import recover_orphaned_amend_channels
    from leaguebot.results.services.submission.submission_recovery import (
        recover_missed_round_starts,
        recover_orphaned_submission_channels,
    )

    return (
        ("amendment sweep", lambda: arm_amendment_sweep(bot)),
        # Results-amend channels left open by a previous run. **Before the submission channels,
        # not after**. An amendment open at the restart has its corrections in the
        # database, unapproved; recovering a submission channel whose results were saved but never
        # posted posts its standings from that database. Reverting the amendments first means
        # those standings are built from the round as it raced, wherever the revert succeeds.
        ("orphaned amend channels", lambda: recover_orphaned_amend_channels(bot, queue=bot.change_queue)),
        # Submission channels left open by a previous run. The paste they were waiting for died
        # with the process, so each is asked of the queue as a review put back or a submission
        # restarted, which asks `results.submission.open` afresh as a follow-on.
        ("orphaned submission channels", lambda: recover_orphaned_submission_channels(bot, queue=bot.change_queue)),
        # Rounds whose start passed while the bot was down, opened late. **After both recoveries
        # above**: a round whose submission one of them cleared and asked open again is then in
        # hand on the queue, and is left to that opening rather than asked a second time.
        ("missed round starts", lambda: recover_missed_round_starts(
            bot, queue=bot.change_queue, came_up_at=lambda: bot.came_up_at,
        )),
    )


async def arm_amendment_sweep(bot: "LeagueBot") -> None:
    """Arm the standing sweep that undoes an amendment nobody carried through, its callback
    registered by `register_scheduler_callbacks`. Run in results' turn of the start-up sweep, so
    only while results is on, and also when results is turned on (`module_on_results`). Armed
    without a setting of its own: the amendment's first stage commits, so a league running the results module at all can
    leave a round half-amended, and the sweep is what makes that recoverable without anybody
    noticing it happened."""
    bot.scheduler_service.schedule_amendment_sweep()
