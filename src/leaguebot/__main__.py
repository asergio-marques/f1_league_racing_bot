"""Entry point for the F1 League Weather Randomizer Bot."""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import discord
from discord.ext import commands
from dotenv import load_dotenv

from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.services.season_review_changes import recover_expired_review_prompts
from leaguebot.core.services.start_up import StartUpStep, start_up
from leaguebot.core.utils.hooks import AwaitedHook, Hook
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild, LeagueCommandTree, warn_if_serving_several
from leaguebot.core.utils.log_filters import install_late_autocomplete_filter

if TYPE_CHECKING:
    from leaguebot.core.services.calendar_post_service import CalendarCommandOutcome
    from leaguebot.image.services.image_lineup_post import LineupPostOutcome

#: The oldest discord.py the bot runs on. `/team add` and `/team modify` take their input in a
#: modal carrying a role picker, which needs ``discord.ui.Label`` — new in 2.6 (#381). A host
#: installing discord.py from apt rather than into a virtualenv gets an older one, and would
#: otherwise fail deep inside the team cog with an AttributeError naming no cause.
MINIMUM_DISCORD_PY: tuple[int, int] = (2, 6)


def require_discord_py(version_info=None) -> None:
    """Refuse to start on a discord.py older than :data:`MINIMUM_DISCORD_PY`, saying so."""
    found = tuple(version_info or discord.version_info)[:2]
    if found < MINIMUM_DISCORD_PY:
        wanted = ".".join(str(part) for part in MINIMUM_DISCORD_PY)
        raise RuntimeError(
            f"This bot needs discord.py {wanted} or later, but {'.'.join(str(p) for p in found)}"
            f" is installed. Install the pinned requirements into a virtualenv "
            f"(`pip install -r requirements.txt`) and run it with that interpreter."
        )


require_discord_py()

log = logging.getLogger(__name__)


class MissingBotToken(RuntimeError):
    """``BOT_TOKEN`` is not set, in the environment or in ``.env``."""


@dataclass(frozen=True)
class Settings:
    """What the host's environment sets, read once by :func:`read_settings` and handed on.

    ``jobstore_path`` is the scheduler's job store, kept apart from the league database on
    purpose (see the module docstring in ``core/services/scheduler_service.py``).
    """

    token: str = field(repr=False)
    db_path: str
    jobstore_path: str


def read_settings(environ: Mapping[str, str]) -> Settings:
    """The three settings, read from *environ*: ``BOT_TOKEN``, ``DB_PATH`` (default ``bot.db``)
    and ``SCHEDULER_DB_PATH``.

    An unset or empty ``SCHEDULER_DB_PATH`` is resolved here, once, to the job store beside the
    database, so nothing downstream decides it again. A missing ``BOT_TOKEN`` raises
    :class:`MissingBotToken`. Called by :func:`main` after ``load_dotenv()`` and by nothing at import
    (``tests/repository/test_suite_needs_no_dotenv.py``). INKSCAPE, a module's own host setting, is
    not one of these: the image module reads it when it renders.
    """
    from leaguebot.core.services.scheduler_service import default_jobstore_path

    token = environ.get("BOT_TOKEN")
    if not token:
        raise MissingBotToken("BOT_TOKEN is not set: put it in the environment or in .env")
    db_path = environ.get("DB_PATH", "bot.db")
    return Settings(
        token=token,
        db_path=db_path,
        jobstore_path=environ.get("SCHEDULER_DB_PATH") or default_jobstore_path(db_path),
    )


def create_bot() -> LeagueBot:
    intents = discord.Intents.default()
    intents.guilds = True
    intents.members = True
    intents.message_content = True  # required for signup wizard on_message dispatch

    # The tree refuses every command from a server that is not the league's (issue #244).
    # Every attribute attached below is declared on `LeagueBot` first (issue #228).
    bot = LeagueBot(
        command_prefix="!", intents=intents, help_command=None, tree_cls=LeagueCommandTree
    )
    # Tell the host whenever the bot finds itself in more than one server (issue #244).
    async def _warn_if_serving_several(*_: object) -> None:
        warn_if_serving_several(bot)

    bot.add_listener(_warn_if_serving_several, "on_ready")
    bot.add_listener(_warn_if_serving_several, "on_guild_join")

    return bot


def _cancel_wizard_timers(bot: LeagueBot) -> "Callable[[str], None]":
    """Removes one driver's signup inactivity and channel-deletion timers, for the close that
    returns them to Not Signed Up (`signup.close`, and a season's end's close of the window) and
    for the signup module's switch-off."""
    from leaguebot.signup.services.closing_steps import DELETE_TIMER, INACTIVITY_TIMER
    from leaguebot.signup.services.review_changes import CORRECTION_TIMER
    from leaguebot.signup.services.wizard_service import channel_delete_job_id, inactivity_job_id

    def cancel(user_id: str) -> None:
        bot.scheduler_service.cancel_job(inactivity_job_id(user_id))
        bot.scheduler_service.cancel_job(channel_delete_job_id(user_id))
        bot.scheduler_service.disarm(INACTIVITY_TIMER, user_id)
        bot.scheduler_service.disarm(DELETE_TIMER, user_id)
        bot.scheduler_service.disarm(CORRECTION_TIMER, user_id)

    return cancel


def _sign_up_to_round_windows(bot: LeagueBot) -> None:
    """Make the hook a round's timing windows read declares, and sign each module up to it, in
    the order core gathers them: attendance's window, then weather's. Each is handed whether the
    round is being amended and checks its own switch."""
    from leaguebot.attendance.services import attendance_service
    from leaguebot.weather.services import weather_config_service

    bot.round_windows_read = AwaitedHook()
    bot.round_windows_read.subscribe(partial(
        attendance_service.round_window, bot.attendance_service, bot.module_service
    ))
    bot.round_windows_read.subscribe(partial(
        weather_config_service.round_window, bot.module_service, bot.db_path
    ))


def _sign_up_to_base_role_changed(bot: LeagueBot) -> None:
    """Make the hook the base role changed declares, and sign signup's move of the signup
    channel's overwrite up to it, so core never reaches into the signup module itself
    (#283)."""
    bot.base_role_changed = AwaitedHook()
    bot.base_role_changed.subscribe(bot.signup_module_service.move_base_role_overwrite)


def _sign_up_to_amendments(bot: LeagueBot) -> None:
    """Make the hooks an amended round declares, and sign each module's part up to them, in the
    order core calls them: inside the save, weather's withdrawal of the forecasts the amendment
    withdraws, then attendance's reopening of its check-in; as the round is armed again, the posts
    each owes, attendance's then weather's, then the clean-ups due a day after the round,
    weather's then attendance's; the steps each defines for the amendment, weather's then
    attendance's; and the words an outcome gives a step left undone, weather's for a forecast."""
    from leaguebot.attendance.services import check_in_changes
    from leaguebot.weather.services import forecast_changes, phase_withdrawal

    bot.round_amended = AwaitedHook()
    bot.round_amended.subscribe(phase_withdrawal.round_amended_on)
    bot.round_amended.subscribe(check_in_changes.round_amended_on)
    bot.round_armed = AwaitedHook()
    bot.round_armed.subscribe(check_in_changes.amendment_posts)
    bot.round_armed.subscribe(forecast_changes.amendment_posts)
    bot.round_clean_ups_due = AwaitedHook()
    bot.round_clean_ups_due.subscribe(forecast_changes.amendment_clean_ups)
    bot.round_clean_ups_due.subscribe(check_in_changes.amendment_clean_ups)
    bot.amendment_steps = Hook()
    bot.amendment_steps.subscribe(partial(
        forecast_changes.amendment_steps,
        now=lambda: bot.change_queue.now(), seasons=bot.season_service, modules=bot.module_service,
    ))
    bot.amendment_steps.subscribe(partial(
        check_in_changes.amendment_steps,
        bot.attendance_service,
        now=lambda: bot.change_queue.now(),
        seasons=bot.season_service,
        modules=bot.module_service,
        deletes=check_in_changes.check_in_deletes(bot, bot.attendance_service),
    ))
    bot.amendment_step_failure = Hook()
    bot.amendment_step_failure.subscribe(forecast_changes.forecast_failure)


def _sign_up_to_cancellations(bot: LeagueBot) -> None:
    """Make the hooks a cancellation of rounds declares, and sign each module's part up to them,
    in the order core calls them: what stands in the way (results' refusal of a round whose
    submission accepted results), the rounds called off (results closes their empty submissions,
    in the save, then attendance's take-down of their calls), the notices told of a division
    (attendance's check-in notice, then weather's forecast note, then results' results note,
    #283), and the steps a module plans (results' deletion of a submission channel, attendance's
    take-down of a call, attendance's reading of a round's check-in record, then each module's
    own notice, attendance's, weather's, then results'). Results' not-held round end is handed
    `round_ended_not_held` too, to which attendance signs up the reading of a round's check-in
    record (#283)."""
    from leaguebot.attendance.services import cancellation_notice as attendance_cancellation_notice
    from leaguebot.attendance.services import check_in_changes, check_in_record
    from leaguebot.results.services import cancellation_notice as results_cancellation_notice
    from leaguebot.results.services import result_submission_service, review_posting
    from leaguebot.weather.services import cancellation_notice as weather_cancellation_notice

    bot.cancellation_refused = AwaitedHook()
    bot.cancellation_refused.subscribe(result_submission_service.cancellation_refusal)
    bot.rounds_called_off = AwaitedHook()
    bot.rounds_called_off.subscribe(result_submission_service.close_called_off_on)
    bot.rounds_called_off.subscribe(check_in_changes.take_down_called_off_on)
    bot.cancellation_notices = AwaitedHook()
    bot.cancellation_notices.subscribe(attendance_cancellation_notice.notices_on)
    bot.cancellation_notices.subscribe(weather_cancellation_notice.notices_on)
    bot.cancellation_notices.subscribe(results_cancellation_notice.notices_on)
    bot.round_ended_not_held = AwaitedHook()
    bot.round_ended_not_held.subscribe(check_in_record.read_check_in_on)
    bot.cancellation_steps = Hook()
    bot.cancellation_steps.subscribe(review_posting.cancellation_steps)
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
        attendance_cancellation_notice.notify_checkin_steps,
        modules=bot.module_service,
        seasons=bot.season_service,
    ))
    bot.cancellation_steps.subscribe(partial(
        weather_cancellation_notice.notify_forecast_steps,
        modules=bot.module_service,
        seasons=bot.season_service,
    ))
    bot.cancellation_steps.subscribe(partial(
        results_cancellation_notice.notify_results_steps,
        modules=bot.module_service,
        seasons=bot.season_service,
    ))


def _sign_up_to_deletes(bot: LeagueBot) -> None:
    """Make the hooks a season, a division, a round or a driver deleted, or a driver merged,
    declares, and sign each module's part up to them, in the order core calls them: each module
    deleting or re-pointing its own rows, on the delete's connection, inside its save
    (architecture.md, "The database")."""
    from leaguebot.attendance.services import attendance_service
    from leaguebot.results.services import results_purge_service
    from leaguebot.signup.services import signup_module_service
    from leaguebot.weather.services import forecast_cleanup_service

    bot.rounds_deleted = AwaitedHook()
    bot.rounds_deleted.subscribe(attendance_service.rounds_deleted_on)
    bot.rounds_deleted.subscribe(results_purge_service.rounds_deleted_on)
    bot.rounds_deleted.subscribe(forecast_cleanup_service.rounds_deleted_on)
    bot.divisions_deleted = AwaitedHook()
    bot.divisions_deleted.subscribe(attendance_service.divisions_deleted_on)
    bot.divisions_deleted.subscribe(results_purge_service.divisions_deleted_on)
    bot.season_deleted = AwaitedHook()
    bot.season_deleted.subscribe(results_purge_service.season_deleted_on)
    bot.season_deleted.subscribe(signup_module_service.season_deleted_on)
    bot.profiles_deleted = AwaitedHook()
    bot.profiles_deleted.subscribe(attendance_service.profiles_deleted_on)
    bot.profiles_deleted.subscribe(results_purge_service.profiles_deleted_on)
    bot.profiles_merged = AwaitedHook()
    bot.profiles_merged.subscribe(attendance_service.profiles_merged_on)
    bot.profiles_merged.subscribe(results_purge_service.profiles_merged_on)


def _sign_up_to_pack(bot: LeagueBot) -> None:
    """Make the hook the bot packed declares, for the modules to sign up to: each module clearing
    its own rows tied to the server the bot leaves, on the pack's connection, inside its one
    transaction (architecture.md, "The database"). The builder hands it to `/bot pack`."""
    from leaguebot.attendance.services import attendance_service
    from leaguebot.results.services import results_purge_service
    from leaguebot.signup.services import signup_module_service
    from leaguebot.weather.services import forecast_cleanup_service

    bot.bot_packed = AwaitedHook()
    bot.bot_packed.subscribe(signup_module_service.bot_packed_on)
    bot.bot_packed.subscribe(forecast_cleanup_service.bot_packed_on)
    bot.bot_packed.subscribe(attendance_service.bot_packed_on)
    bot.bot_packed.subscribe(results_purge_service.bot_packed_on)


def _sign_up_to_rounds_made_final(bot: LeagueBot) -> None:
    """Make the hook a round's results made final declares, and sign each module's part up to it:
    results marking the drivers who raced each round as former drivers, on the closing's
    connection, inside its save (architecture.md, "How modules and core fit together"). The
    builder hands it to the two closers that make a round FINAL: results turned off, and a
    season cancelled."""
    from leaguebot.results.services import result_submission_service

    bot.rounds_made_final = AwaitedHook()
    bot.rounds_made_final.subscribe(result_submission_service.rounds_made_final_on)


def _sign_up_to_test_mode_turned_on(bot: LeagueBot) -> None:
    """Make the hook test mode turned on declares, and sign each module's part up to it: what a
    module seeds for a season under test, on the connection of the save that turns test mode on,
    given the season's id (architecture.md, "How modules and core fit together"). The builder
    hands it to the toggle's change (`test_mode_toggle_change`). Results' seeding runs whether or
    not the results module is on, as the core specification's test mode section says without a
    condition."""
    from leaguebot.results.services import points_config_service

    bot.test_mode_turned_on = AwaitedHook()
    bot.test_mode_turned_on.subscribe(points_config_service.seed_test_configs_on)


def _sign_up_to_configuration_confirmed(bot: LeagueBot) -> None:
    """Make the hook a season's configuration confirmed declares, and sign each module's part up
    to it: what a module keeps for the season from that moment, on the connection of the
    confirmation's save, given the season's id and the queue's clock (architecture.md, "How
    modules and core fit together"). Signup keeps its settings and slots. The builder hands it to
    the confirmation."""
    from leaguebot.signup.services import signup_module_service

    bot.configuration_confirmed = AwaitedHook()
    bot.configuration_confirmed.subscribe(signup_module_service.configuration_confirmed_on)


def _sign_up_to_placements_confirmed(bot: LeagueBot) -> None:
    """Make the hooks placements confirmed (the season's approval) declares, and sign each
    module's opening post up to them: the jobs it plans for a division, handed the save's
    connection and the division's id and name (architecture.md, "How modules and core fit
    together"), and the step it defines for its own job, by name. The builder hands them to
    `season_approval_change`. Results signs up first, so each division's jobs stay lineup,
    calendar, standings, sheet."""
    from leaguebot.attendance.services import sheet_posting
    from leaguebot.results.services import season_standings_posts

    bot.placements_confirmed = AwaitedHook()
    bot.placements_confirmed.subscribe(season_standings_posts.placements_confirmed_on)
    bot.placements_confirmed.subscribe(sheet_posting.placements_confirmed_on)
    bot.placements_confirmed_steps = Hook()
    bot.placements_confirmed_steps.subscribe(
        partial(season_standings_posts.opening_steps, bot.module_service)
    )
    bot.placements_confirmed_steps.subscribe(partial(sheet_posting.opening_steps, bot.module_service))


def _sign_up_to_season_completed(bot: LeagueBot) -> None:
    """Make the hooks a division's final round read and a season completed declare, and sign
    each module's final post up to them: results' read of its own `session_results` and its
    final standings, then attendance's final sheet (#283). The builder hands them to
    `season_complete_change`."""
    from leaguebot.attendance.services import sheet_posting
    from leaguebot.results.services import season_standings_posts

    bot.final_round_read = AwaitedHook()
    bot.final_round_read.subscribe(season_standings_posts.final_round_on)
    bot.season_completed = AwaitedHook()
    bot.season_completed.subscribe(season_standings_posts.season_completed_on)
    bot.season_completed.subscribe(sheet_posting.season_completed_on)
    bot.season_completed_steps = Hook()
    bot.season_completed_steps.subscribe(
        partial(season_standings_posts.final_steps, bot.module_service)
    )
    bot.season_completed_steps.subscribe(
        partial(sheet_posting.final_steps, bot.module_service)
    )


def _sign_up_to_season_end(bot: LeagueBot) -> None:
    """Make the hooks a season's end declares, and sign each module's part up to them, in the order
    core calls them: what stands in the way of the completion and of the cancellation (results'
    refusal of a season with a round being amended); the jobs that close what a module holds open
    (signup's close of its window); inside its saves, signup's ending of the wizards of the
    drivers returned; the jobs planned for each driver returned (signup's); the steps the modules
    define for it (signup's close of its window, the button's deletion and the closed post, each
    driver's notice and channel's close, then weather's deletion of a forecast); the jobs that
    clear what test mode posted (weather's); and the drivers deleted (image's discard of their
    portraits)."""
    from leaguebot.image.services import driver_portrait_service
    from leaguebot.results.services import result_submission_service
    from leaguebot.signup.services import closing_steps, window_changes
    from leaguebot.signup.services.window_close import RETURNED_BY_CLOSE
    from leaguebot.weather.services import forecast_changes, forecast_cleanup_service

    bot.season_end_refused = AwaitedHook()
    bot.season_end_refused.subscribe(result_submission_service.season_end_refusal)
    bot.season_end_closes = Hook()
    bot.season_end_closes.subscribe(closing_steps.season_end_closes)
    bot.drivers_returned = AwaitedHook()
    bot.drivers_returned.subscribe(
        partial(closing_steps.drivers_returned_on, lambda: bot.signup_module_service)
    )
    bot.driver_returned_jobs = Hook()
    bot.driver_returned_jobs.subscribe(closing_steps.driver_returned_jobs)
    bot.season_end_steps = Hook()
    bot.season_end_steps.subscribe(partial(
        window_changes.season_end_steps,
        wizard_service=lambda: bot.wizard_service,
        scheduler_service=lambda: bot.scheduler_service,
        modules=lambda: bot.module_service,
        signup_module_service=lambda: bot.signup_module_service,
        returned_by_close=RETURNED_BY_CLOSE,
        cancel_timers=_cancel_wizard_timers(bot),
        # Read when it runs: a test replaces the scheduler's method after registering.
        cancel_close_timer=lambda: bot.scheduler_service.cancel_signup_close_timer(),
        now=lambda: bot.change_queue.now(),
    ))
    bot.season_end_steps.subscribe(partial(
        forecast_changes.season_end_steps, now=lambda: bot.change_queue.now()
    ))
    bot.test_mode_flushed = AwaitedHook()
    bot.test_mode_flushed.subscribe(partial(forecast_cleanup_service.forecast_deletes, bot.db_path))
    bot.drivers_deleted = AwaitedHook()
    bot.drivers_deleted.subscribe(
        partial(driver_portrait_service.discard_portraits, bot.db_path, bot.image_config_service)
    )


async def _draw_review_picture(bot: LeagueBot, picture: Mapping[str, Any]) -> "Path":
    """Draw one picture of a season review to a file, and give where it is.

    A lineup is the image module's, drawn obtaining each seated driver's missing portrait first
    (#407), so a review is not judged on a placeholder the league will not see, and, mid-season,
    with its uncommitted drivers in it (#374). A calendar is core's, of the division's rounds as
    they stand when it is drawn. A picture the renderer will not draw raises, with the renderer's
    message.
    """
    kind, division_id = str(picture["kind"]), int(picture["division_id"])
    if kind == "lineup":
        from leaguebot.image.services import image_lineup_post

        outcome: "LineupPostOutcome | CalendarCommandOutcome"
        outcome = await image_lineup_post.render_for_command(
            bot, await league_guild(bot), division_id,
            include_uncommitted=bool(picture.get("include_uncommitted")),
            obtain_missing_portraits=True,
        )
    else:
        from leaguebot.core.services import calendar_post_service

        division = await bot.season_service.get_division(division_id)
        outcome = await calendar_post_service.render_for_command(
            bot.image_drawing, division, await bot.season_service.get_division_rounds(division_id),
            await calendar_post_service.tracks_by_name(bot.db_path),
            season_number=picture.get("season_number"),
        )
    if outcome.png_path is None:
        raise RuntimeError(outcome.message or f"the {kind} could not be drawn")
    return outcome.png_path


def _review_drawing(bot: LeagueBot) -> "Callable[[Mapping[str, Any]], Path]":
    """The drawing `season.review.post` is handed: it draws a picture in the worker thread the
    change runs it in, its coroutine awaited on the bot's event loop (`await_from_worker`)."""
    from leaguebot.core.services.season_review_changes import await_from_worker

    def drawing(picture: Mapping[str, Any]) -> "Path":
        return await_from_worker(_draw_review_picture(bot, picture))

    return drawing


def _approve_view_for(bot: LeagueBot) -> "Callable[[dict[str, Any]], discord.ui.View]":
    """The factory of a review question's view, handed to `season.review.post`: the season cog's
    view of the review the payload names (its ``view``: ``approve``, ``mid_season`` or
    ``configuration``), holding the evidence the report was computed from."""
    from leaguebot.core.cogs.season_cog import (
        SeasonCog,
        _ApproveView,
        _ConfirmConfigurationView,
        _ConfirmMidSeasonPlacementsView,
    )
    families = {
        "approve": _ApproveView,
        "mid_season": _ConfirmMidSeasonPlacementsView,
        "configuration": _ConfirmConfigurationView,
    }

    def make(payload: dict[str, Any]) -> discord.ui.View:
        # The season cog is loaded by the time a review is posted. A restart routes the buttons of
        # the reviews left standing before it is, and those are served by a cog of the same bot.
        loaded = bot.get_cog("SeasonCog")
        cog = loaded if isinstance(loaded, SeasonCog) else SeasonCog(bot)
        return families[str(payload.get("view", "approve"))](cog, int(payload["reviewer_id"]))

    return make


def _configuration_faults_of(bot: LeagueBot) -> Callable[[int, Any], Awaitable[list[str]]]:
    """The season cog's one reading of every fault that stops a configuration being confirmed,
    handed to the confirmation's check so that it refuses on exactly what the review withholds its
    button on. Found when the check runs: the cog is loaded by then, and a restart's check is made
    by a cog of the same bot."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    async def faults(season_id: int, guild: Any) -> list[str]:
        loaded = bot.get_cog("SeasonCog")
        cog = loaded if isinstance(loaded, SeasonCog) else SeasonCog(bot)
        return await cog._configuration_faults(season_id, guild)

    return faults


def register_change_types(bot: LeagueBot) -> None:
    """Make the queue known every kind of change the bot carries out.

    The builder is where modules meet: it hands each change type what it needs of another module,
    so that core's queue and cogs import none of them. The tests' support module calls this to get
    the real set.
    """
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
    from leaguebot.attendance.services.sheet_posting import sheet_post_change
    from leaguebot.core.services.calendar_post_service import calendar_sync_change
    from leaguebot.core.services.cancellation_changes import (
        division_cancel_change,
        round_cancel_change,
        season_cancel_change,
    )
    from leaguebot.core.services.bot_init_change import bot_init_change
    from leaguebot.core.services.division_add_change import division_add_change
    from leaguebot.core.services.division_delete_change import division_delete_change
    from leaguebot.core.services.division_duplicate_change import division_duplicate_change
    from leaguebot.core.services.division_edit_changes import (
        division_amend_change,
        division_rename_change,
    )
    from leaguebot.core.services.driver_changes import driver_move_change, driver_sack_change
    from leaguebot.core.services.round_add_changes import round_add_change, round_import_change
    from leaguebot.core.services.round_delete_change import round_delete_change
    from leaguebot.core.services.season_setup_change import season_setup_change
    from leaguebot.core.services.setup_round_amend_change import setup_round_amend_change
    from leaguebot.core.services.bot_settings import (
        admin_role_setting,
        base_role_setting,
        driver_role_setting,
        hub_channel_setting,
        interaction_channel_setting,
        interaction_role_setting,
        log_channel_setting,
    )
    from leaguebot.core.services.channel_tell_change import channel_tell_change
    from leaguebot.core.services.division_settings import division_channel_settings
    from leaguebot.core.services.test_mode_settings import test_mode_nationality_setting
    from leaguebot.core.services.test_mode_toggle_change import test_mode_toggle_change
    from leaguebot.core.services.clean_change import clean_change
    from leaguebot.core.services.hub_service import hub_refresh_change
    from leaguebot.core.services.lineup_posting import lineup_post_change
    from leaguebot.core.services.round_amend_change import (
        amendment_in_hand,
        round_amend_change,
        round_rearm_change,
    )
    from leaguebot.core.services.configuration_confirm_change import (
        configuration_confirm_change,
    )
    from leaguebot.core.services.mid_season_confirm_change import mid_season_confirm_change
    from leaguebot.core.services.season_approval_change import (
        season_approval_change,
        season_approval_tell_change,
    )
    from leaguebot.core.services import season_review_changes
    from leaguebot.core.services.season_review_changes import season_review_take_down_change
    from leaguebot.core.services.season_end_changes import season_abort_change, season_complete_change
    from leaguebot.core.services.season_lifecycle_service import (
        steps_of_the_modules,
        wind_down_change,
    )
    from leaguebot.image.services.image_render_service import discard_render
    from leaguebot.results.services.season_points_service import snapshot_configs_to_season_on
    from leaguebot.core.services.setting_change import setting_change
    from leaguebot.image.services.image_settings import image_settings
    from leaguebot.image.services.module_switch_settings import (
        module_off_images,
        module_on_images,
    )
    from leaguebot.results.services.amendment_settings import amendment_settings
    from leaguebot.results.services.channel_settings import results_channel_settings
    from leaguebot.results.services.module_switch_settings import module_on_results
    from leaguebot.results.services.points_settings import points_settings
    from leaguebot.weather.services.weather_settings import (
        deadline_setting,
        forecast_channel_setting,
    )
    from leaguebot.weather.services.module_switch_settings import (
        module_off_weather,
        module_on_weather,
    )
    from leaguebot.weather.services.forecast_changes import (
        forecast_change,
        forecast_clean_up_change,
    )
    from leaguebot.results.services.amendment_stage_changes import amendment_stage_changes
    from leaguebot.results.services.appeals_approval_change import appeals_approval_change
    from leaguebot.results.services.points_amendment_change import points_amendment_change
    from leaguebot.results.services.results_off_change import results_off_change
    from leaguebot.results.services.report_approval_change import report_approval_change
    from leaguebot.results.services.amend_changes import amend_open_change
    from leaguebot.results.services.amend_changes import TIMER as AMEND_TIMER
    from leaguebot.results.services.amend_end_changes import (
        amend_cancel_change,
        amend_lapse_change,
        amend_lapse_timer,
    )
    from leaguebot.results.services.amend_turn_change import (
        amend_close_change,
        amend_config_change,
        amend_stage_one_change,
        amend_turn_change,
    )
    from leaguebot.results.services.rounds_sync_change import rounds_sync_change
    from leaguebot.signup.services.closing_steps import closing_change, reason_delete_change
    from leaguebot.signup.services.window_close import RETURNED_BY_CLOSE
    from leaguebot.signup.services.module_switch_settings import module_on_signup
    from leaguebot.signup.services.signup_off_change import signup_off_change
    from leaguebot.signup.services.window_changes import window_close_change, window_open_change
    from leaguebot.signup.services.review_changes import (
        CORRECTION_TIMER,
        approve_change,
        ask_lapse,
        choices_change,
        choose_change,
        lapse_change,
        recollect_change,
        request_changes_change,
    )
    from leaguebot.signup.services.wizard_answers import answer_change, panel_change, submit_change
    from leaguebot.signup.services.wizard_changes import (
        channel_delete_change,
        end_change,
        member_left_change,
        start_change,
    )
    from leaguebot.signup.services.closing_steps import (
        DELETE_TIMER,
        INACTIVITY_TIMER,
        ask_channel_delete,
        ask_expiry,
    )
    from leaguebot.signup.services.signup_off_change import KIND as OFF_KIND
    from leaguebot.signup.services.window_changes import CLOSE as CLOSE_KIND
    from leaguebot.results.services.standings_sync_change import standings_sync_change
    from leaguebot.results.services.resubmission_changes import (
        resubmission_start_change,
        resubmission_swap_change,
    )
    from leaguebot.results.services.submission_changes import (
        submission_all_cancelled_change,
        submission_close_change,
        submission_open_change,
        submission_restart_change,
    )
    from leaguebot.results.services.submission_turn_change import (
        submission_config_change,
        submission_turn_change,
    )
    from leaguebot.results.services.review_open_change import (
        appeals_open_change,
        close_stale_change,
        review_open_change,
        review_prompt_change,
    )
    from leaguebot.results.services.review_confirm_change import review_confirm_change
    from leaguebot.results.services.review_lock_change import (
        TIMER,
        lock_change,
        lock_clear_change,
        lock_clear_timer,
    )
    from leaguebot.results.services.review_stage_change import review_stage_change

    bot.change_queue.register(
        results_off_change(
            attendance_off_on=bot.attendance_service.switch_off_on,
            attendance_enabled=bot.module_service.is_attendance_enabled,
            rounds_made_final=bot.rounds_made_final,
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
    bot.change_queue.register(resubmission_start_change(queue=bot.change_queue))
    bot.change_queue.register(resubmission_swap_change())
    bot.change_queue.register(
        submission_turn_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            resubmission=True,
        )
    )
    bot.change_queue.register(
        submission_config_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config,
            resubmission=True,
        )
    )
    bot.change_queue.register(
        submission_turn_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config
        )
    )
    bot.change_queue.register(
        submission_config_change(
            queue=bot.change_queue, server_config=bot.config_service.get_server_config
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
            server_config=bot.config_service.get_server_config,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        amend_config_change(
            server_config=bot.config_service.get_server_config,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        amend_stage_one_change(
            server_config=bot.config_service.get_server_config,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(amend_close_change())
    bot.change_queue.register(reason_delete_change())
    bot.change_queue.register(
        closing_change(now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once)
    )
    # The queue is a getter here as well, read when the change runs.
    bot.change_queue.register(
        window_open_change(
            modules=bot.module_service, signup=bot.signup_module_service,
            config=bot.config_service,
            schedule_close=bot.scheduler_service.schedule_signup_close_timer,
            queue=lambda: bot.change_queue,
        )
    )
    # The signup, driver and team services are read when a change runs and not handed over in the
    # changes that follow: a test swaps each on the bot after registering.
    bot.change_queue.register(
        window_close_change(
            signup_module_service=lambda: bot.signup_module_service, returned_by_close=RETURNED_BY_CLOSE,
            cancel_timers=_cancel_wizard_timers(bot),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        signup_off_change(
            modules=bot.module_service, signup_module_service=lambda: bot.signup_module_service,
            returned_by_close=RETURNED_BY_CLOSE, cancel_timers=_cancel_wizard_timers(bot),
            # Read when it runs: a test replaces the scheduler's or the configuration's method
            # after registering.
            cancel_close_timer=lambda: bot.scheduler_service.cancel_signup_close_timer(),
            server_config=lambda: bot.config_service.get_server_config(),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        start_change(
            modules=bot.module_service, config=bot.config_service,
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            teams=lambda: bot.team_service,
            queue_of=lambda: bot.change_queue, closing_kinds=(CLOSE_KIND, OFF_KIND),
            cancel_timers=_cancel_wizard_timers(bot),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        end_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            cancel_timers=_cancel_wizard_timers(bot),
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        answer_change(
            signup=lambda: bot.signup_module_service, queue_of=lambda: bot.change_queue,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        submit_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            cancel_timers=_cancel_wizard_timers(bot),
        )
    )
    bot.change_queue.register(
        panel_change(signup=lambda: bot.signup_module_service, queue_of=lambda: bot.change_queue)
    )
    bot.change_queue.register(
        approve_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            config=bot.config_service, cancel_timers=_cancel_wizard_timers(bot),
        )
    )
    bot.change_queue.register(
        request_changes_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
        )
    )
    bot.change_queue.register(
        choices_change(
            signup=lambda: bot.signup_module_service, queue_of=lambda: bot.change_queue,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
        )
    )
    bot.change_queue.register(
        choose_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
            disarm=bot.scheduler_service.disarm,
        )
    )
    bot.change_queue.register(
        recollect_change(
            signup=lambda: bot.signup_module_service, queue_of=lambda: bot.change_queue,
        )
    )
    bot.change_queue.register(
        lapse_change(
            modules=bot.module_service, signup=lambda: bot.signup_module_service,
            drivers=lambda: bot.driver_service, queue_of=lambda: bot.change_queue,
        )
    )
    # The timers below read the bot's queue when they fire, as the getters above do. A correction
    # nobody chose within five minutes is lapsed by the change its timer asks of the queue, as the
    # bot.
    bot.scheduler_service.register_timer(
        CORRECTION_TIMER, lambda key: ask_lapse(bot.change_queue, str(key))
    )
    # A signup left unanswered for a day is ended by the expiry its timer asks of the queue, as
    # the bot.
    bot.scheduler_service.register_timer(
        INACTIVITY_TIMER, lambda key: ask_expiry(bot.change_queue, bot.db_path, str(key))
    )
    # A held signup channel's 24 hours ending asks its deletion, on the driver's own lane.
    bot.change_queue.register(channel_delete_change(signup=lambda: bot.signup_module_service))
    bot.change_queue.register(
        member_left_change(
            signup=lambda: bot.signup_module_service, drivers=lambda: bot.driver_service,
            cancel_timers=_cancel_wizard_timers(bot),
        )
    )
    bot.scheduler_service.register_timer(
        DELETE_TIMER,
        lambda key: ask_channel_delete(bot.change_queue, bot.db_path, str(key), own_lane=True),
    )
    bot.change_queue.register(review_open_change())
    bot.change_queue.register(review_prompt_change())
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
    bot.change_queue.register(appeals_open_change())
    bot.change_queue.register(close_stale_change())
    # The review's change types stamp what they write with the queue's own clock, read when they
    # run, as the getters above are, and reach attendance through the hook alone.
    bot.change_queue.register(
        report_approval_change(
            attendance=bot.attendance_after_review, now=lambda: bot.change_queue.now()
        )
    )
    bot.change_queue.register(
        appeals_approval_change(
            attendance=bot.attendance_after_review, now=lambda: bot.change_queue.now()
        )
    )
    for stage in amendment_stage_changes(
        attendance=bot.attendance_after_review, now=lambda: bot.change_queue.now()
    ):
        bot.change_queue.register(stage)
    bot.change_queue.register(
        points_amendment_change(
            attendance=bot.attendance_after_review, now=lambda: bot.change_queue.now()
        )
    )
    bot.change_queue.register(hub_refresh_change(bot.change_queue, bot.config_service))
    bot.change_queue.register(calendar_sync_change(bot.season_service, bot.image_drawing))
    bot.change_queue.register(lineup_post_change(bot.placement_service))
    bot.change_queue.register(driver_move_change(placement=bot.placement_service))
    bot.change_queue.register(driver_sack_change(placement=bot.placement_service))
    bot.change_queue.register(sheet_post_change())
    bot.change_queue.register(rounds_sync_change(bot.season_service, bot.module_service))
    bot.change_queue.register(standings_sync_change(bot.season_service, bot.module_service))
    bot.change_queue.register(forecast_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(
        check_in_call_change(bot.attendance_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        check_in_last_notice_change(bot.attendance_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        check_in_deadline_change(bot.attendance_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(check_in_clean_up_change())
    bot.change_queue.register(check_in_answer_change(bot.attendance_service))
    bot.change_queue.register(
        sanctions_announce_change(
            attendance=bot.attendance_after_review, now=lambda: bot.change_queue.now()
        )
    )
    bot.change_queue.register(forecast_clean_up_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(
        wind_down_change(
            placement=bot.placement_service,
            season_end_closes=bot.season_end_closes,
            drivers_returned=bot.drivers_returned,
            driver_returned_jobs=bot.driver_returned_jobs,
            season_end_steps=bot.season_end_steps,
        )
    )
    bot.change_queue.register(
        season_complete_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            final_round_read=bot.final_round_read,
            season_completed=bot.season_completed,
            season_completed_steps=bot.season_completed_steps,
            season_end_refused=bot.season_end_refused,
            season_end_closes=bot.season_end_closes,
            drivers_returned=bot.drivers_returned,
            driver_returned_jobs=bot.driver_returned_jobs,
            season_end_steps=bot.season_end_steps,
            test_mode_flushed=bot.test_mode_flushed,
            drivers_deleted=bot.drivers_deleted,
            profiles_deleted=bot.profiles_deleted,
        )
    )
    bot.change_queue.register(
        season_cancel_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            drawing=bot.image_drawing,
            cancellation_refused=bot.cancellation_refused,
            rounds_called_off=bot.rounds_called_off,
            cancellation_notices=bot.cancellation_notices,
            rounds_made_final=bot.rounds_made_final,
            cancellation_steps=bot.cancellation_steps,
            season_end_refused=bot.season_end_refused,
            season_end_closes=bot.season_end_closes,
            drivers_returned=bot.drivers_returned,
            driver_returned_jobs=bot.driver_returned_jobs,
            season_end_steps=bot.season_end_steps,
            test_mode_flushed=bot.test_mode_flushed,
            drivers_deleted=bot.drivers_deleted,
            profiles_deleted=bot.profiles_deleted,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        season_abort_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            season_end_closes=bot.season_end_closes,
            drivers_returned=bot.drivers_returned,
            driver_returned_jobs=bot.driver_returned_jobs,
            season_end_steps=bot.season_end_steps,
            test_mode_flushed=bot.test_mode_flushed,
            drivers_deleted=bot.drivers_deleted,
            rounds_deleted=bot.rounds_deleted,
            divisions_deleted=bot.divisions_deleted,
            season_deleted=bot.season_deleted,
            profiles_deleted=bot.profiles_deleted,
        )
    )
    # The season's approval is judged again as it runs: the clock is the queue's, the windows the
    # enabled modules', and the drawing program the host's, read through the image module's
    # drawing's own check when it is judged, the one the reviews ask too, so the three ask it
    # one way and cannot disagree.
    bot.change_queue.register(
        season_approval_change(
            modules=bot.module_service,
            config=bot.config_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            drawing=bot.image_drawing,
            round_windows_read=bot.round_windows_read,
            placements_confirmed=bot.placements_confirmed,
            placements_confirmed_steps=bot.placements_confirmed_steps,
            rasteriser_fault=bot.image_drawing.rasteriser_fault,
            snapshot_points_on=snapshot_configs_to_season_on,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(season_approval_tell_change())
    # The mid-season confirmation of placements is judged again as it runs, the host's drawing
    # program read when it is judged, as the approval's is.
    bot.change_queue.register(
        mid_season_confirm_change(
            modules=bot.module_service,
            placement=bot.placement_service,
            drawing=bot.image_drawing,
            rasteriser_fault=bot.image_drawing.rasteriser_fault,
        )
    )
    bot.change_queue.register(
        configuration_confirm_change(
            drawing=bot.image_drawing,
            faults=_configuration_faults_of(bot),
            configuration_confirmed=bot.configuration_confirmed,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(season_review_take_down_change())
    # The review's expiry timer asks the queue when it fires, by the helper's getter.
    bot.scheduler_service.register_timer(
        season_review_changes.TIMER,
        season_review_changes.review_expiry_timer(bot.db_path, lambda: bot.change_queue),
    )
    bot.change_queue.register(
        season_review_changes.season_review_post_change(
            drawing=_review_drawing(bot), approve_view=_approve_view_for(bot),
            scheduler=bot.scheduler_service, discard=discard_render,
        )
    )
    bot.change_queue.register(
        setting_change(deadline_setting(modules=bot.module_service, seasons=bot.season_service))
    )
    bot.change_queue.register(
        setting_change(
            forecast_channel_setting(modules=bot.module_service, seasons=bot.season_service)
        )
    )
    bot.change_queue.register(
        setting_change(
            module_on_weather(modules=bot.module_service, seasons=bot.season_service)
        )
    )
    bot.change_queue.register(
        setting_change(
            module_off_weather(modules=bot.module_service, scheduler=bot.scheduler_service)
        )
    )
    bot.change_queue.register(
        setting_change(
            module_on_images(modules=bot.module_service, seasons=bot.season_service)
        )
    )
    bot.change_queue.register(
        setting_change(module_off_images(modules=bot.module_service))
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
    bot.change_queue.register(
        setting_change(
            module_on_results(modules=bot.module_service, seasons=bot.season_service)
        )
    )
    bot.change_queue.register(
        setting_change(
            module_on_signup(modules=bot.module_service, config=bot.config_service)
        )
    )
    bot.change_queue.register(bot_init_change(config=bot.config_service))
    bot.change_queue.register(
        season_setup_change(seasons=bot.season_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        division_add_change(seasons=bot.season_service, teams=bot.team_service)
    )
    bot.change_queue.register(
        division_duplicate_change(
            seasons=bot.season_service, teams=bot.team_service,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(division_rename_change(seasons=bot.season_service))
    bot.change_queue.register(division_amend_change(seasons=bot.season_service))
    bot.change_queue.register(
        division_delete_change(
            seasons=bot.season_service,
            rounds_deleted=bot.rounds_deleted,
            divisions_deleted=bot.divisions_deleted,
        )
    )
    bot.change_queue.register(
        round_add_change(seasons=bot.season_service, drawing=bot.image_drawing)
    )
    bot.change_queue.register(
        round_import_change(seasons=bot.season_service, drawing=bot.image_drawing)
    )
    bot.change_queue.register(setup_round_amend_change(seasons=bot.season_service))
    bot.change_queue.register(
        round_delete_change(seasons=bot.season_service, rounds_deleted=bot.rounds_deleted)
    )
    bot.change_queue.register(
        test_mode_toggle_change(
            test_mode_turned_on=bot.test_mode_turned_on,
            profiles_deleted=bot.profiles_deleted,
            test_mode_flushed=bot.test_mode_flushed,
            season_end_steps=bot.season_end_steps,
            scheduler=bot.scheduler_service,
        )
    )
    for setting in (
        log_channel_setting(config=bot.config_service),
        interaction_channel_setting(config=bot.config_service),
        interaction_role_setting(config=bot.config_service),
        admin_role_setting(config=bot.config_service),
        base_role_setting(config=bot.config_service, placement=bot.placement_service),
        driver_role_setting(config=bot.config_service, placement=bot.placement_service),
        hub_channel_setting(config=bot.config_service),
        *division_channel_settings(seasons=bot.season_service),
        test_mode_nationality_setting(config=bot.config_service),
        *points_settings(modules=bot.module_service, seasons=bot.season_service),
        *amendment_settings(modules=bot.module_service, seasons=bot.season_service),
        *results_channel_settings(
            modules=bot.module_service, seasons=bot.season_service,
            channel_of=lambda channel_id: bot.get_channel(channel_id),
        ),
        *attendance_channel_settings(
            modules=bot.module_service, seasons=bot.season_service,
            channel_of=lambda channel_id: bot.get_channel(channel_id),
        ),
        *image_settings(
            modules=bot.module_service,
            config=bot.image_config_service,
            validity=bot.image_validity_service,
            render=bot.image_render_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
        ),
    ):
        bot.change_queue.register(setting_change(setting))
    bot.change_queue.register(channel_tell_change())
    bot.change_queue.register(clean_change())
    bot.change_queue.register(
        round_amend_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            amendment_steps=bot.amendment_steps,
            amendment_step_failure=bot.amendment_step_failure,
            round_windows_read=bot.round_windows_read,
            round_amended=bot.round_amended,
            round_armed=bot.round_armed,
            round_clean_ups_due=bot.round_clean_ups_due,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        round_rearm_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            amendment_steps=bot.amendment_steps,
            amendment_step_failure=bot.amendment_step_failure,
            round_windows_read=bot.round_windows_read,
            round_amended=bot.round_amended,
            round_armed=bot.round_armed,
            round_clean_ups_due=bot.round_clean_ups_due,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        round_cancel_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            drawing=bot.image_drawing,
            cancellation_refused=bot.cancellation_refused,
            rounds_called_off=bot.rounds_called_off,
            cancellation_notices=bot.cancellation_notices,
            cancellation_steps=bot.cancellation_steps,
            amendment_in_hand=lambda division_id: amendment_in_hand(bot.db_path, division_id),
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        division_cancel_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            drawing=bot.image_drawing,
            cancellation_refused=bot.cancellation_refused,
            rounds_called_off=bot.rounds_called_off,
            cancellation_notices=bot.cancellation_notices,
            cancellation_steps=bot.cancellation_steps,
            amendment_in_hand=lambda division_id: amendment_in_hand(bot.db_path, division_id),
            now=lambda: bot.change_queue.now(),
        )
    )


def register_scheduler_callbacks(bot: LeagueBot) -> None:
    """Sign up the handler of every kind of job the scheduler fires, through `register_timer`:
    the weather phases, the forecast clean-up, the result submission, the four check-in jobs, the
    portrait refresh, the amendment sweep and the signup close; and, beside the handler of each
    kind of a round's job, the step `/test-mode advance` runs for it (`register_advance`).

    Each asks the change queue. Weather's handlers hold the queue they were handed here
    (`timer_callbacks`); the others read it from the bot when they fire. The bot keeps the one
    queue `build` made, so in the running bot the two come to the same; a test that replaces the
    queue must register these handlers again. Called by :func:`build`, so that every kind is
    registered before the bot starts and before the scheduler does.
    """
    # Wire weather's handlers into the scheduler: each asks the change queue
    from leaguebot.core.services.test_mode_service import register_advance
    from leaguebot.weather.services.forecast_changes import advance_steps, timer_callbacks

    scheduler = bot.scheduler_service
    callbacks = timer_callbacks(bot.change_queue, bot.db_path)
    for phase, handler in callbacks.phases.items():
        scheduler.register_timer(f"weather_p{phase}", handler)
    scheduler.register_timer("cleanup", callbacks.clean_up)
    for kind, advance in advance_steps(bot.change_queue, bot.db_path).items():
        register_advance(kind, advance)

    # Register result submission callback
    from leaguebot.results.services.submission_changes import advance_step, ask_submission_open

    async def _result_submission_cb(round_id: int) -> None:
        await ask_submission_open(bot.change_queue, bot.db_path, round_id)

    scheduler.register_timer("results", _result_submission_cb)
    register_advance("results", advance_step(bot.change_queue, bot.db_path))

    # Register RSVP attendance callbacks
    from leaguebot.attendance.services.check_in_changes import (
        advance_steps as check_in_advance_steps,
        ask_check_in_call,
        ask_check_in_clean_up,
        ask_check_in_deadline,
        ask_check_in_last_notice,
    )
    from leaguebot.core.models.change import ChangeOrigin

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
        register_advance(kind, advance)

    # Register the daily driver-portrait refresh and re-arm it after a restart. Unlike
    # every other job here the trigger is recurring, so recovery re-adds it rather than
    # working out whether it was missed: a cron job that did not fire while the bot was
    # down simply fires at its next due time.
    async def _portrait_refresh_cb() -> None:
        from leaguebot.image.services.driver_portrait_service import run_daily_refresh
        await run_daily_refresh(bot)

    scheduler.register_timer("portrait_refresh", _portrait_refresh_cb)

    # The standing sweep that undoes an amendment nobody carried through (#345); start-up arms it.
    async def _amendment_sweep_cb() -> None:
        from leaguebot.results.services.result_submission_service import sweep_expired_amendments
        await sweep_expired_amendments(bot, queue=bot.change_queue)

    scheduler.register_timer("amendment_sweep", _amendment_sweep_cb)

    async def _signup_close_cb() -> None:
        from leaguebot.signup.services.window_close import close_signups_unattended
        await close_signups_unattended(
            change_queue=bot.change_queue,
            module_service=bot.module_service,
            signup_module_service=bot.signup_module_service,
            cause="timer",
        )

    scheduler.register_timer("signup_close", _signup_close_cb)


async def build(settings: Settings) -> LeagueBot:
    """Make the bot and every service, each handed what it needs and complete once built, and
    return it, nothing started.

    Registers every change type, timer kind and scheduler callback, loads the cogs and the
    persistent views, and adds the owner's `!sync` command and the disconnect log line, before
    the bot connects and before the scheduler starts, so a press on a
    persistent button arriving while start-up is still recovering finds all of it in place
    (#228). Reads no environment: the settings are handed in (:func:`read_settings`).
    """
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.output_router import OutputRouter

    bot = create_bot()
    bot.db_path = settings.db_path

    # The version is read once, here, and never when a command runs (#258): it belongs to
    # the code this process loaded. See `core/utils/version.py`.
    from leaguebot.core.utils.paths import PROJECT_ROOT
    from leaguebot.core.utils.version import read_version, read_version_date

    version = read_version(PROJECT_ROOT)
    made = read_version_date(PROJECT_ROOT)
    bot.running_version = version
    bot.running_version_date = made
    log.info(
        "Running %s%s",
        version or "an unknown version",
        f", made {made.isoformat()}" if made is not None else "",
    )

    # Registers the hub's About option, core's one, before start-up recovers the hub and
    # routes the panel's buttons (#258).
    import leaguebot.core.services.about_service  # noqa: F401

    # Services are attached to bot for cog access
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.services.team_service import TeamService

    bot.config_service = ConfigService(settings.db_path)
    bot.season_service = SeasonService(settings.db_path)
    bot.scheduler_service = SchedulerService(
        settings.db_path, settings.jobstore_path
    )
    bot.output_router = OutputRouter(bot, bot.config_service, retry_db_path=settings.db_path)
    # The queue is handed the router, and is started by start-up, after the recoveries.
    from leaguebot.core.services.change_queue import ChangeQueue

    bot.change_queue = ChangeQueue(settings.db_path, bot, bot.output_router, bot.config_service)
    bot.driver_service = DriverService(settings.db_path)
    bot.team_service = TeamService(settings.db_path)

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_validity_service import ImageValidityService

    bot.module_service = ModuleService(settings.db_path)
    bot.image_config_service = ImageConfigService(settings.db_path)
    bot.image_validity_service = ImageValidityService(
        bot.image_config_service,
        bot.module_service,
    )

    from leaguebot.image.services.image_render_service import ImageRenderService

    bot.image_render_service = ImageRenderService(
        bot.image_config_service,
        bot.image_validity_service,
    )

    from leaguebot.image.services.image_drawing import ImageDrawing

    # Core's drawing protocol, image's own implementation, made once and declared on the
    # bot so that core reaches it without importing this module (architecture.md, "How
    # modules and core fit together"), as results reaches attendance through its own hook.
    # The renderer draws the calendar's and the lineup's pictures; the log writer is where
    # their notices are reported, in one message, whichever change posts one. The
    # database path is what the lineup's drawing reads beyond the image module's own
    # services: the division, its committed seats, and whether nationality is collected.
    bot.image_drawing = ImageDrawing(
        bot.module_service, bot.image_config_service, bot.image_validity_service,
        renderer=bot.image_render_service, post_log=bot.output_router.post_log,
        db_path=settings.db_path,
    )

    from leaguebot.core.services.placement_service import PlacementService
    # The drawing is handed so the capacity guards and the lineup refresh can ask image's
    # answer without a bot of their own: PlacementService posts through the guild it is
    # given, and reaches the image module only through this drawing.
    bot.placement_service = PlacementService(settings.db_path, drawing=bot.image_drawing)

    from leaguebot.signup.services.signup_module_service import SignupModuleService
    from leaguebot.signup.services.wizard_service import WizardService
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.core.services.output_router import OutputRouter as _OutputRouter  # already imported above

    bot.signup_module_service = SignupModuleService(settings.db_path)
    bot.wizard_service = WizardService(
        settings.db_path,
        bot.scheduler_service,
        bot.output_router,
        change_queue=bot.change_queue,
        bot=bot,
    )
    bot.attendance_service = AttendanceService(settings.db_path)

    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    # Attendance's share of a round's review, handed the placement service it moves seats
    # through; results' change types reach attendance through it alone (#439).
    bot.attendance_after_review = AttendanceAfterReview(bot, bot.placement_service)
    _sign_up_to_round_windows(bot)
    _sign_up_to_base_role_changed(bot)
    _sign_up_to_amendments(bot)

    _sign_up_to_cancellations(bot)
    _sign_up_to_rounds_made_final(bot)
    _sign_up_to_test_mode_turned_on(bot)
    _sign_up_to_configuration_confirmed(bot)
    _sign_up_to_placements_confirmed(bot)
    _sign_up_to_season_completed(bot)
    _sign_up_to_season_end(bot)
    _sign_up_to_deletes(bot)
    _sign_up_to_pack(bot)
    register_change_types(bot)
    register_scheduler_callbacks(bot)

    # --- Load Cogs ---
    from leaguebot.core.cogs.bot_cog import BotCog
    from leaguebot.core.cogs.season_cog import SeasonCog
    from leaguebot.core.cogs.amendment_cog import AmendmentCog
    from leaguebot.core.cogs.test_mode_cog import TestModeCog
    from leaguebot.core.cogs.track_cog import TrackCog
    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.cogs.team_cog import TeamCog
    from leaguebot.core.cogs.module_cog import ModuleCog
    from leaguebot.signup.cogs.signup_cog import SignupCog
    from leaguebot.signup.cogs.admin_review_cog import AdminReviewCog
    from leaguebot.core.cogs.retry_cog import RetryCog
    from leaguebot.results.cogs.results_cog import ResultsCog
    from leaguebot.weather.cogs.weather_cog import WeatherCog
    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog
    from leaguebot.image.cogs.image_cog import ImageCog
    from leaguebot.core.cogs.clean_cog import CleanCog

    await bot.add_cog(BotCog(bot))
    season_cog = SeasonCog(bot)
    await bot.add_cog(season_cog)
    await bot.add_cog(AmendmentCog(bot))
    await bot.add_cog(TestModeCog(bot))
    await bot.add_cog(TrackCog(bot))
    await bot.add_cog(DriverCog(bot))
    await bot.add_cog(TeamCog(bot))
    await bot.add_cog(ModuleCog(bot))
    await bot.add_cog(SignupCog(bot))
    await bot.add_cog(AdminReviewCog(bot))
    retry_cog = RetryCog(bot)
    await bot.add_cog(retry_cog)
    bot.start_retry_loop = retry_cog.start_loop
    await bot.add_cog(ResultsCog(bot))
    await bot.add_cog(WeatherCog(bot))
    await bot.add_cog(AttendanceCog(bot))
    await bot.add_cog(ImageCog(bot))
    await bot.add_cog(CleanCog(bot))

    # The bot's in-memory state cleared (`/bot pack`, `/bot factory-reset`): each module signs up
    # what it holds, in the order the clear drops them.
    from leaguebot.results.services.penalty_wizard import clear_held_reviews
    from leaguebot.signup.services.signup_views import clear_pending_reasons

    bot.in_memory_state_cleared = Hook()
    bot.in_memory_state_cleared.subscribe(clear_held_reviews)
    bot.in_memory_state_cleared.subscribe(clear_pending_reasons)

    # Register ALL persistent views so button interactions survive bot restarts.
    # Views with optional __init__ params resolve driver context from channel at
    # interaction time, so a single registration handles all active wizard sessions.
    from leaguebot.signup.services.signup_views import (
        SignupButtonView,
        WithdrawButtonView,
        NoNotesButtonView,
        PlatformButtonView,
        DriverTypeButtonView,
        PreferredTeamsButtonView,
        NoPreferenceTeammateView,
    )
    from leaguebot.signup.services.signup_views import AdminReviewView, CorrectionParameterView
    from leaguebot.results.services.penalty_wizard import PenaltyReviewView, ApprovalView, AppealsReviewView
    from leaguebot.attendance.services.rsvp_service import RsvpView

    for _view in (
        SignupButtonView(bot.change_queue),
        WithdrawButtonView(None, bot.change_queue),
        NoNotesButtonView(None, bot.change_queue),
        PlatformButtonView(None, bot.change_queue),
        DriverTypeButtonView(None, bot.change_queue),
        PreferredTeamsButtonView(None, bot.change_queue),   # registers pteam_0..pteam_19 stubs + nopref + cancel
        NoPreferenceTeammateView(None, bot.change_queue),
        AdminReviewView(change_queue=bot.change_queue),
        CorrectionParameterView(change_queue=bot.change_queue),
        PenaltyReviewView(),
        ApprovalView(),
        AppealsReviewView(),
        RsvpView(),  # stub registration; message_id-specific re-arm done in _recover_rsvp_views_and_deadlines
    ):
        bot.add_view(_view)

    log.info("All cogs loaded.")

    @bot.event
    async def on_disconnect() -> None:
        log.warning("Bot disconnected from Discord")

    @bot.command(name="sync", hidden=True)
    @commands.is_owner()
    async def guild_sync(ctx: commands.Context) -> None:
        """Owner-only: clear guild command overrides and do a global sync."""
        # Remove any guild-specific copies (e.g. left over from a previous copy_global_to).
        bot.tree.clear_commands(guild=ctx.guild)
        await bot.tree.sync(guild=ctx.guild)
        # Re-sync globally so Discord has the latest command schema.
        synced = await bot.tree.sync()
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send(
            f"✅ Cleared guild overrides and synced {len(synced)} global command(s).",
            delete_after=15,
        )

    bot.start_up_steps = _start_up_steps(bot)

    return bot


def _start_up_steps(bot: LeagueBot) -> "tuple[StartUpStep, ...]":
    """The bot's start-up steps, named and in order: what `start_up` runs once, each contained.

    The order is today's and is load-bearing where a comment says so. The scheduler is started
    paused before the first step and resumed after the last, so no scheduled job runs while these
    do; each recovery is looked up when its step runs, so a step stands for that function and no
    other. The modules' recoveries stay steps as they stand.
    """

    async def arm_the_amendment_sweep() -> None:
        # The standing sweep that undoes an amendment nobody carried through (#345), its callback
        # registered by `build`. Armed unconditionally rather than behind a setting: the
        # amendment's first stage commits, so a league running the results module at all can leave
        # a round half-amended, and the sweep is what makes that recoverable without anybody
        # noticing it happened.
        bot.scheduler_service.schedule_amendment_sweep()

    async def recover_the_hub() -> None:
        # Route the hub's buttons again, and post its panel afresh where it was deleted (#279).
        from leaguebot.core.services.hub_service import recover_hub

        await recover_hub(bot, queue=bot.change_queue)

    async def recover_the_wizards() -> None:
        # Re-arm inactivity APScheduler jobs for any non-UNENGAGED wizard sessions that were
        # active before the last restart.
        await bot.wizard_service.recover_wizards()

    return (
        ("amendment sweep", arm_the_amendment_sweep),
        ("portrait refresh job", lambda: _recover_portrait_refresh_job(bot)),
        # A signup close timer lost on restart (T021); its callback is registered by `build`.
        ("signup close timer", lambda: _recover_signup_close_timers(bot)),
        # Results-amend channels left open by a previous run. **Before the submission channels,
        # not after** (#345). An amendment open at the restart has its corrections in the
        # database, unapproved; recovering a submission channel whose results were saved but never
        # posted posts its standings from that database. Reverting the amendments first means
        # those standings are built from the round as it raced, wherever the revert succeeds.
        ("orphaned amend channels", lambda: _recover_orphaned_amend_channels(bot)),
        # Submission channels left open by a previous run. The paste they were waiting for died
        # with the process, so they are reset so /test-mode advance can re-trigger submission.
        ("orphaned submission channels", lambda: _recover_orphaned_submission_channels(bot)),
        # Any season-review approve button left standing by a previous run.
        ("expired review prompts",
         lambda: recover_expired_review_prompts(
             bot, approve_view=_approve_view_for(bot), queue=bot.change_queue)),
        ("missed phases", lambda: _recover_missed_phases(bot)),
        # The check-in calls that fell due while the bot was down, their deadline still ahead
        # (#429), posted late. Before the deadlines below, as a call comes before its deadline.
        ("missed check-in calls", lambda: _recover_missed_check_in_calls(bot)),
        # Persistent RSVP embed views re-armed for all stored embed messages (T010), and missed
        # RSVP deadline jobs run for rounds whose deadline already passed (T019).
        ("RSVP views and deadlines", lambda: _recover_rsvp_views_and_deadlines(bot)),
        # The forecasts and check-in messages due to come down while the bot was stopped (#425).
        # After the deadlines above, so a check-in is distributed before it goes.
        ("missed clean-ups", lambda: _recover_missed_cleanups(bot)),
        ("hub", recover_the_hub),
        ("wizards", recover_the_wizards),
    )


async def _recover_signup_close_timers(bot: LeagueBot) -> None:
    """Close a signup window whose close time passed while the bot was down, or re-arm its timer."""
    from datetime import datetime as _dt, timezone as _tz

    from leaguebot.core.db.database import get_connection as _get_conn

    async with _get_conn(bot.db_path) as _db:
        _cur = await _db.execute(
            "SELECT close_at FROM signup_module_config WHERE close_at IS NOT NULL"
        )
        _row = await _cur.fetchone()
    if _row is None:
        return
    now_utc = _dt.now(_tz.utc)
    _close_at_iso = _row["close_at"]
    try:
        _close_dt = _dt.fromisoformat(_close_at_iso)
        if _close_dt.tzinfo is None:
            _close_dt = _close_dt.replace(tzinfo=_tz.utc)
    except ValueError:
        log.warning("start-up: invalid signup close_at value: %r", _close_at_iso)
        return
    if _close_dt <= now_utc:
        log.info("start-up: signup close_at is past — running forced close")
        from leaguebot.signup.services.window_close import close_signups_unattended
        await close_signups_unattended(
            change_queue=bot.change_queue,
            module_service=bot.module_service,
            signup_module_service=bot.signup_module_service,
            cause="restart",
        )
    else:
        from leaguebot.core.services.scheduler_service import SIGNUP_CLOSE_JOB_ID
        if not bot.scheduler_service.has_job(SIGNUP_CLOSE_JOB_ID):
            log.info("start-up: re-arming the signup close timer at %s", _close_at_iso)
            bot.scheduler_service.schedule_signup_close_timer(_close_at_iso)


async def main() -> None:
    load_dotenv()
    settings = read_settings(os.environ)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    # A late autocomplete is a race with Discord's three-second budget, not a fault in this
    # code, and discord.py logs it as an ERROR with a full traceback. Downgrade just that one.
    install_late_autocomplete_filter()

    from leaguebot.core.db.database import run_migrations

    # A staged restore is swapped in **here**, before a single service is constructed and
    # before the scheduler opens its job store. Every service captures its path and holds
    # connections open, so this is the one moment in the process at which replacing the
    # files underneath them is safe. `/test-mode backup restore` stages and does no more,
    # precisely so that the swap can happen at this point on the next start — under a
    # service or from a terminal alike.
    from leaguebot.core.services.backup_service import apply_staged_restore

    if apply_staged_restore(settings.db_path, settings.jobstore_path):
        log.info("Started on a restored database.")

    # Applied before the bot is built and connects, so every service meets the finished schema.
    await run_migrations(settings.db_path)

    bot = await build(settings)

    @bot.event
    async def on_ready() -> None:
        # `on_ready` fires once the bot has logged in, which is when it has a user.
        assert bot.user is not None
        log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)

        # Start-up runs once per process, the steps `build` made; a second ready, after a network
        # drop, runs nothing again and syncs nothing (architecture.md, "Start-up runs once, in
        # one function").
        if not await start_up(
            steps=bot.start_up_steps,
            change_queue=bot.change_queue,
            scheduler=bot.scheduler_service,
        ):
            return

        # Sync slash commands globally (may take up to 1 hour to propagate)
        try:
            synced = await bot.tree.sync()
            log.info("Synced %d slash command(s)", len(synced))
        except discord.HTTPException as exc:
            log.error("Failed to sync slash commands: %s", exc)

    log.info("Starting bot...")

    from leaguebot.core.db.database import close_connections

    try:
        async with bot:
            await bot.start(settings.token)
    finally:
        # The connections kept idle to be lent again each hold a thread that is not a daemon;
        # left open they would hang a Ctrl-C. Closed after the bot has closed, so that nothing
        # it does while closing opens one that is then left (`get_connection`).
        await close_connections()


async def _recover_missed_phases(bot: LeagueBot) -> None:
    """Ask the change queue for any weather phases whose horizon has passed but were not executed.

    Every round is asked for alike, a mystery round included: the mystery rule is weather's
    (`forecast_changes.ask_forecast`), which turns a mystery round's phase 1 into its notice and
    asks nothing for its phases 2 and 3. A notice missed while the bot was down is therefore posted
    on starting, while the round's race is still ahead. A request no longer due is dropped by its
    change's check.

    The horizons are the league's own, read from ``weather_pipeline_config``, not the packaged
    5 / 2 / 2 (issue #111). Every other path that decides whether a phase is overdue reads that
    config — the confirmation of placements and a round amendment's judgement — and a restart judging by the defaults made the same league see one set of
    timings on an enable and another on a restart: a longer phase 1 was never published at all,
    a shorter one was published days early.

    The config is resolved once rather than once per round, since the league has one, and only
    after the module gate, so a league with weather switched off is never queried for it.

    Only a round still to be run is recovered (issue #272): never a cancelled round, a round of a
    cancelled division, or a round whose race time has passed. Cancellation is filtered in the
    query. The race time is judged here, after the timestamp is made aware, because rows written
    without an offset still exist, and SQL would compare them as text against a timestamp that
    carries one. It is the race time rather than the round's status that decides, since a round leaves
    ``NOT_RUN`` only by its result-submission job, and a job that fell due while the bot was down
    was discarded by the scheduler's misfire grace rather than run.
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.weather.services.forecast_changes import ask_forecast
    from leaguebot.weather.models.weather_config import WeatherPipelineConfig
    from leaguebot.weather.services.weather_config_service import get_weather_pipeline_config
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cfg: WeatherPipelineConfig | None = None

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.id, r.scheduled_at,
                   r.phase1_done, r.phase2_done, r.phase3_done,
                   r.format
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE s.status = 'ACTIVE'
              AND r.status != 'CANCELLED'
              AND d.status != 'CANCELLED'
            """
        )
        rows = await cursor.fetchall()

    for row in rows:
        round_id, scheduled_at_str, p1, p2, p3, fmt = row
        scheduled_at = datetime.fromisoformat(scheduled_at_str)
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        # A round already raced has no forecast owing, whatever its flags say.
        if scheduled_at <= now:
            continue

        # Only recover phases for servers with weather module active
        if not await bot.module_service.is_weather_enabled():
            continue

        if cfg is None:
            cfg = await get_weather_pipeline_config(bot.db_path)

        phase1_horizon = scheduled_at - timedelta(days=cfg.phase_1_days)
        phase2_horizon = scheduled_at - timedelta(days=cfg.phase_2_days)
        phase3_horizon = scheduled_at - timedelta(hours=cfg.phase_3_hours)

        if not p1 and now >= phase1_horizon:
            log.info("Recovery: firing Phase 1 for round %s", round_id)
            await ask_forecast(bot.change_queue, bot.db_path, round_id, 1)
        if not p2 and now >= phase2_horizon:
            log.info("Recovery: firing Phase 2 for round %s", round_id)
            await ask_forecast(bot.change_queue, bot.db_path, round_id, 2)
        if not p3 and now >= phase3_horizon:
            log.info("Recovery: firing Phase 3 for round %s", round_id)
            await ask_forecast(bot.change_queue, bot.db_path, round_id, 3)


async def _recover_missed_check_in_calls(
    bot: LeagueBot, *, now: datetime | None = None
) -> None:
    """Post late the check-in calls that fell due while the bot was down (#429).

    A call's job is one of those the scheduler discards when the bot comes back more than its
    misfire grace after it, and nothing else posted it: the round asked nobody whether they
    were racing, opened no attendance rows, and counted nothing against anyone. Decided
    2026-09-24: a call that came due is posted late, provided the round's check-in deadline is
    still ahead, and the drivers answer in the time left. The deadline boundary is the one
    `/attendance post-check-in` holds to — with a deadline of zero, the round's own moment.

    A round is owed a late call where its season is in one of the three ongoing stages, neither
    it nor its division is cancelled, the attendance module is enabled, no call for it stands
    and its check-in is not over (``checkin_cleared``), and the moment its call was due has
    passed. **Nothing is posted while test mode is on** (decided 2026-09-24): `/test-mode
    advance` posts a test season's calls in their turn.

    The restart asks the change queue for each call owed, before the queue starts, and posts
    nothing itself (#439, slice 6b): the call's change posts it, telling the log channel that it
    went out late, or, where the deadline has passed as well, gives it up (`give_up`), closing the
    round's check-in so that the next start does not ask again. A call already in hand on the queue
    is not asked for twice. Its last notice and its deadline need no arming: the confirmation of
    placements armed them with the call, the job store outlives a restart, and both are still
    ahead of a call posted in time to be answered.

    Runs before `_recover_rsvp_views_and_deadlines` in start-up, the call coming before its
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
            scheduled_at = datetime.fromisoformat(str(row["scheduled_at"]))
        except ValueError:
            continue
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
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


async def _recover_rsvp_views_and_deadlines(bot: LeagueBot) -> None:
    """Re-arm RsvpView buttons and run missed RSVP deadline jobs on bot restart.

    T010: Re-arm persistent RsvpView for every row in rsvp_embed_messages so
    button interactions survive bot restarts (FR-007).

    T019: For any call whose rsvp_deadline fire time has already passed and which carries no
    mark of its deadline having run, ask the change queue for the deadline (FR-027).

    **The call's own mark decides, never the drivers' placements** (#429). A deadline writes its
    distribution and marks the call (`deadline_run_at`) in one save, before it posts its
    announcement or its no-reserve notice; that is how `AttendanceService.get_current_embed_message`
    tells a deadline that has run as well, and the two must agree. The placements cannot: a
    deadline with no reserve to place writes none, so it was run again at every start and its
    notice posted again, and a call posted again after an amendment carries over the placements
    of the call it replaced, so its own deadline was never caught up. The announcement's id cannot
    either: a discarded announcement leaves none, and the deadline has run all the same.

    T023: For any round whose rsvp_last_notice fire time has already passed,
    silently skip — do NOT fire retroactively (FR-029 edge case).

    A restart is one of the three paths the core specification names, so none of this runs
    for a server whose attendance module is switched off (issue #114). Re-arming the buttons
    would revive check-in for a league that had turned it off, and the missed-deadline
    catch-up would distribute its reserves into seats hours after the fact.
    """
    from leaguebot.attendance.services.check_in_changes import (
        CHECK_IN_DEADLINE,
        ask_check_in_deadline,
    )
    from leaguebot.attendance.services.rsvp_service import RsvpView
    from leaguebot.core.db.database import get_connection as _gc
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.change_queue import unfinished
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
            if isinstance(scheduled_at_raw, str):
                scheduled_at = _dt.fromisoformat(scheduled_at_raw)
            else:
                scheduled_at = scheduled_at_raw
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=_tz.utc)
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


async def _recover_missed_cleanups(bot: LeagueBot) -> None:
    """Take down what a round's cleanups would have, their moment passing while the bot was down.

    A round's forecast (whichever of Phases 1 to 3 still stands, which the host's log line names),
    and its check-in call with its last notice and its distribution message, come down
    `POST_RACE_CLEANUP_DELAY` after the round's start, by the ``cleanup`` and ``rsvp_cleanup``
    jobs (#425). A job falling due while the bot is stopped is discarded by the
    scheduler's misfire grace rather than run late, and nothing else would take those messages
    down: a forecast whose cleanup was missed used to stay for good.

    A round called off, or of a division called off, is left alone. Its cancellation removed both
    jobs, and the weather module keeps the forecasts of a round called off. A round that ended
    cancelled because every session was entered as not held (`rounds.not_held`) is not left alone:
    its jobs were never removed, and its call and forecasts are owed their clean-up as any round's. The season's status is not
    asked, since the live jobs fire after a season completes as well. Each module's own gate applies:
    weather's here, and attendance's inside its clean-up change's check.

    Running a cleanup a second time finds nothing left to take down, so a job the scheduler
    still runs inside its grace alongside this does no harm.
    """
    from datetime import datetime, timezone

    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import forecasts_named
    from leaguebot.weather.services.forecast_changes import ask_forecast_clean_up
    from leaguebot.attendance.services.check_in_changes import ask_check_in_clean_up
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.round_moments import POST_RACE_CLEANUP_DELAY

    now = datetime.now(timezone.utc)

    def _due(scheduled_at_raw: object) -> bool:
        try:
            scheduled_at = datetime.fromisoformat(str(scheduled_at_raw))
        except ValueError:
            return False
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        return scheduled_at + POST_RACE_CLEANUP_DELAY <= now

    try:
        async with get_connection(bot.db_path) as db:
            cursor = await db.execute(
                """
                SELECT fm.round_id, r.scheduled_at, fm.phase_number
                  FROM forecast_messages fm
                  JOIN rounds r ON r.id = fm.round_id
                  JOIN divisions d ON d.id = r.division_id
                 WHERE fm.phase_number IN (1, 2, 3)
                   AND (r.status != 'CANCELLED' OR r.not_held = 1)
                   AND d.status != 'CANCELLED'
                 ORDER BY r.scheduled_at, fm.round_id, fm.phase_number
                """
            )
            # Each round due, in order, with the phases of its forecast still standing.
            forecasts: dict[int, list[int]] = {}
            for r in await cursor.fetchall():
                if _due(r["scheduled_at"]):
                    forecasts.setdefault(r["round_id"], []).append(r["phase_number"])
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
        log.exception("_recover_missed_cleanups: failed to read the messages still standing")
        return

    if forecasts and not await bot.module_service.is_weather_enabled():
        forecasts = {}

    for round_id, phases in forecasts.items():
        log.info(
            "_recover_missed_cleanups: deleting %s of round_id=%d", forecasts_named(phases),
            round_id,
        )
        try:
            await ask_forecast_clean_up(bot.change_queue, bot.db_path, round_id)
        except Exception:
            log.exception(
                "_recover_missed_cleanups: forecast cleanup failed for round %d", round_id
            )

    for round_id in check_ins:
        log.info("_recover_missed_cleanups: taking down round %d's check-in", round_id)
        try:
            await ask_check_in_clean_up(
                bot.change_queue, bot.db_path, round_id, origin=ChangeOrigin.BOT
            )
        except Exception:
            log.exception(
                "_recover_missed_cleanups: check-in cleanup failed for round %d", round_id
            )


async def _recover_orphaned_submission_channels(bot: LeagueBot) -> None:
    """Clear any submission channels left open by a previous bot process, through the queue.

    When the bot restarts, the paste a submission was waiting for is lost: the row remains
    closed=0 and the Discord channel still stands, but nothing is listening.

    A mid-submission orphan (in_penalty_review=0) is asked of the queue as
    `results.submission.restart`: it clears the round's sessions (so the round is not mistaken
    for complete by get_next_pending_phase) and the submission row, deletes the channel, tells the
    log channel to re-submit and opens the submission afresh. A paste whose turn is in hand on the
    queue is left to it. This runs before the queue starts.

    Reviews are not decided here: `review_recovery.recover_review` leaves a round whose review a
    change on the queue is carrying out to it, and otherwise asks the queue to put the review back
    (#439). A resubmission a restart cut short is ended by the review's opening, its collection being lost
    though the round's results are not (issue #210).
    """
    from leaguebot.core.db.database import get_connection

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            """
            SELECT rsc.round_id, rsc.channel_id, rsc.in_penalty_review,
                   rsc.results_posted, rsc.prompt_message_id, rsc.appeals_prompt_message_id,
                   rsc.resubmitting, rsc.resubmit_prompt_message_id, rsc.resubmit_started_by,
                   r.division_id, r.status, r.round_number, d.name AS division_name
            FROM round_submission_channels rsc
            JOIN rounds r    ON r.id  = rsc.round_id
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons s   ON s.id  = d.season_id
            WHERE rsc.closed = 0
            """
        )
        orphans = await cursor.fetchall()

    from leaguebot.results.services.review_recovery import recover_review

    for row in orphans:
        round_id: int = row["round_id"]

        guild = await league_guild(bot)

        # A round that is a review is put back, or left to the change carrying it out, by the
        # results service (#439). What is left is a first paste cut short, which is no review.
        if await recover_review(
            bot, row, guild, queue=bot.change_queue,
        ):
            continue

        # A first paste cut short is cleared, its channel deleted and its submission opened
        # afresh by the change queue's `results.submission.restart`, before the queue starts. A
        # round with a change of its submission in hand is left to it above.
        from leaguebot.results.services.submission_changes import ask_submission_restart

        log.info("Recovery: asking the queue to clear and reopen round %s's submission", round_id)
        try:
            await ask_submission_restart(
                bot.change_queue, bot.db_path, round_id, int(row["channel_id"]),
            )
        except Exception:
            log.exception("Recovery: could not ask for round %s's submission restart", round_id)


async def _recover_portrait_refresh_job(bot: LeagueBot) -> None:
    """Re-arm the daily driver-portrait refresh, where the league has it switched on."""
    from leaguebot.core.db.database import get_connection

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT pfp_daily_time FROM image_config "
            "WHERE use_pfp = 1 AND pfp_daily = 1"
        )
        row = await cursor.fetchone()
    if row is not None:
        try:
            bot.scheduler_service.schedule_portrait_refresh(
                row["pfp_daily_time"]
            )
        except Exception:
            log.warning("Failed to re-arm the daily portrait refresh", exc_info=True)


async def _recover_orphaned_amend_channels(bot: LeagueBot) -> None:
    """Ask the change queue to end every amendment left open by a previous bot process.

    An amendment's pastes were once waited for in the command, which died with the process. They
    are turns on the queue now, so an amendment open at a restart has, at most, its waiting turns
    and its timer; but it was abandoned all the same, and a league manager whose amendment vanished
    needs to know to run the command again. Each amendment row is asked for as a
    ``results.amend.lapse`` of the restart's kind, as the bot, **before the queue starts**
    (core specification, "What survives a restart"): its jobs put the round back where stage one
    had written, delete the channel and close the row, and write the line, each amendment's own.
    Nothing is put back, deleted or written here.

    What the line says depends on what the restart found, and the row cannot always say:

    - Where the round was **put back**, the amendment lapsed unconfirmed, and the record is the
      lapse of `/results rounds amend`, naming who started it, with the sessions being amended
      and the wording of what happened and what to run beneath it.
    - Where there was **nothing to put back**, the corrections were either never entered or had
      already been approved and were being rebuilt when the bot stopped, and the row cannot say
      which. That is not a lapse, so the line is a neutral one — who started it, that a restart
      ended it — with the explanation of both cases and what to run in each beneath it.

    An amendment whose stage is in hand on the queue, stopped included, is finished by the queue
    ("Leave it while stuck"), and one whose lapse is already in hand from an earlier restart is
    asked for once: the lapse's check and the queue's repeat rule leave both alone. A failure to
    ask for one amendment is logged and does not stop the rest.
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.results.services.amend_end_changes import RESTART, ask_lapse

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute("SELECT id FROM round_amend_channels ORDER BY id")
        orphans = [int(row["id"]) for row in await cursor.fetchall()]

    for amend_id in orphans:
        try:
            await ask_lapse(bot.change_queue, bot.db_path, amend_id, cause=RESTART)
        except Exception:
            log.exception("Recovery: could not ask the lapse of amendment %s", amend_id)


if __name__ == "__main__":
    asyncio.run(main())
