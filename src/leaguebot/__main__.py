"""Entry point for the F1 League Weather Randomizer Bot."""

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, cast

import aiosqlite
import discord
from discord.ext import commands
from dotenv import load_dotenv

from leaguebot.core.services.channel_registry_service import as_text_channel
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import league_guild, LeagueCommandTree, warn_if_serving_several
from leaguebot.core.utils.log_filters import install_late_autocomplete_filter

if TYPE_CHECKING:
    from leaguebot.core.services.calendar_post_service import CalendarCommandOutcome
    from leaguebot.image.services.image_lineup_post import LineupPostOutcome
    from leaguebot.core.services.cancellation_changes import CancellationHooks, SubmissionHooks
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
    from leaguebot.core.services.round_amend_change import AmendHooks
    from leaguebot.core.services.season_lifecycle_service import SeasonEndHooks

load_dotenv()

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

TOKEN: str = os.environ["BOT_TOKEN"]
DB_PATH: str = os.getenv("DB_PATH", "bot.db")
#: The scheduler's job store, kept apart from the league database on purpose — see the
#: module docstring in `core/services/scheduler_service.py`. Empty means "beside DB_PATH".
SCHEDULER_DB_PATH: str = os.getenv("SCHEDULER_DB_PATH", "")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
# A late autocomplete is a race with Discord's three-second budget, not a fault in this
# code, and discord.py logs it as an ERROR with a full traceback. Downgrade just that one.
install_late_autocomplete_filter()
log = logging.getLogger(__name__)


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
    bot.db_path = DB_PATH

    # Tell the host whenever the bot finds itself in more than one server (issue #244).
    async def _warn_if_serving_several(*_: object) -> None:
        warn_if_serving_several(bot)

    bot.add_listener(_warn_if_serving_several, "on_ready")
    bot.add_listener(_warn_if_serving_several, "on_guild_join")

    return bot


async def read_approval_windows(
    bot: LeagueBot,
) -> "tuple[AttendanceWindows | None, WeatherWindows | None]":
    """The lead times the enabled modules configure, as `(attendance, weather)`.

    Either is None where that module is off: there is no window to be inside. The builder is
    where attendance and weather meet core, so the season's approval reads them through
    `bot.approval_windows`, which is this, and core imports neither module (#439).
    """
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
    from leaguebot.core.services.round_amend_change import AmendHooks

    attendance = None
    if await bot.module_service.is_attendance_enabled():
        att = await bot.attendance_service.get_or_create_config()
        attendance = AttendanceWindows(
            notice_days=att.rsvp_notice_days,
            last_notice_hours=att.rsvp_last_notice_hours,
            deadline_hours=att.rsvp_deadline_hours,
        )

    weather = None
    if await bot.module_service.is_weather_enabled():
        from leaguebot.weather.services.weather_config_service import get_weather_pipeline_config

        wx = await get_weather_pipeline_config(bot.db_path)
        weather = WeatherWindows(
            phase_1_days=wx.phase_1_days,
            phase_2_days=wx.phase_2_days,
            phase_3_hours=wx.phase_3_hours,
        )

    return attendance, weather


async def read_amendment_windows(
    bot: LeagueBot,
) -> "tuple[AttendanceWindows | None, WeatherWindows]":
    """The lead times a round amendment is judged against, as `(attendance, weather)`.

    Attendance's is None where that module is off: a league without attendance has no check-in
    to lose. Weather's is read whatever the module's state, because a forecast posted while
    weather was on is still posted, and whether it survives the amendment is what the track and
    format rules turn on. `/round amend` offers and judges through `bot.amendment_windows`, which
    is this, and so does its change on the queue, so the two cannot read different windows (#439).
    """
    from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
    from leaguebot.core.services.round_amend_change import AmendHooks
    from leaguebot.weather.services.weather_config_service import get_weather_pipeline_config

    attendance = None
    if await bot.module_service.is_attendance_enabled():
        att = await bot.attendance_service.get_or_create_config()
        attendance = AttendanceWindows(
            notice_days=att.rsvp_notice_days,
            last_notice_hours=att.rsvp_last_notice_hours,
            deadline_hours=att.rsvp_deadline_hours,
        )

    wx = await get_weather_pipeline_config(bot.db_path)
    weather = WeatherWindows(
        phase_1_days=wx.phase_1_days,
        phase_2_days=wx.phase_2_days,
        phase_3_hours=wx.phase_3_hours,
    )
    return attendance, weather


def _forget_setup(bot: LeagueBot) -> None:
    """Let go of the setup the season cog holds in memory, as `clear_in_memory_state` does."""
    from leaguebot.core.cogs.season_cog import SeasonCog

    cog = bot.get_cog("SeasonCog")
    if isinstance(cog, SeasonCog):
        cog.clear_pending()


def _cancel_wizard_timers(bot: LeagueBot) -> "Callable[[str], None]":
    """Removes one driver's signup inactivity and channel-deletion timers, for the close that
    returns them to Not Signed Up (`signup.close`, and a season's end's through
    `execute_forced_close`) and for the signup module's switch-off."""
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


def _season_end_hooks(bot: LeagueBot) -> "SeasonEndHooks":
    """What a season's end needs of signup, results, weather, image and the cog, handed it here so
    that core imports none of them. Each is looked up as it is called, so that nothing is bound
    before it is needed."""
    from leaguebot.core.cogs.module_cog import close_signups_unattended
    from leaguebot.core.models.change import PlannedStep
    from leaguebot.core.services import backup_service
    from leaguebot.core.services.season_lifecycle_service import SeasonEndHooks
    from leaguebot.image.services import driver_portrait_service
    from leaguebot.results.services import result_submission_service
    from leaguebot.signup.services.wizard_service import inactivity_job_id
    from leaguebot.signup.services.closing_steps import (
        INACTIVITY_TIMER, window_close_jobs, window_close_steps,
    )
    from leaguebot.weather.services import forecast_cleanup_service
    from leaguebot.weather.services.forecast_changes import forecast_steps

    async def close_signups(bot_: LeagueBot, cause: str):
        return await close_signups_unattended(
            bot_, cause=cause, cancel_timers=_cancel_wizard_timers(bot_)
        )

    async def window_open() -> bool:
        if not await bot.module_service.is_signup_enabled():
            return False
        config = await bot.signup_module_service.get_config()
        return config is not None and config.signups_open

    async def post_signup_notice(user_id: str, guild: discord.Guild, notice: str):
        return await bot.wizard_service.trigger_channel_hold(
            user_id, guild, notice, lock=False, raise_on_failure=True
        )

    async def lock_signup_channel(user_id: str, guild: discord.Guild) -> None:
        await bot.wizard_service.lock_signup_channel(user_id, guild)

    def cancel_signup_timeout(user_id: str) -> None:
        bot.scheduler_service.cancel_job(inactivity_job_id(user_id))
        bot.scheduler_service.disarm(INACTIVITY_TIMER, user_id)

    async def discard_portraits(accounts):
        return await driver_portrait_service.discard_portraits(bot, accounts)

    async def amendment_open(db_path: str, season_id: int):
        return await result_submission_service.open_amendment_in_season(db_path, season_id)

    async def plan_forecast_flush() -> "tuple[PlannedStep, ...]":
        return await forecast_cleanup_service.forecast_deletes(bot.db_path)

    def discard_backup() -> object:
        return backup_service.discard(
            bot.db_path, backup_service.jobstore_path_of(bot), raise_on_failure=True
        )

    async def owed_closing_notices() -> list[str]:
        return await bot.signup_module_service.owed_closing_notices()

    async def clear_closing_notices_on(db: aiosqlite.Connection, accounts: Sequence[str]) -> None:
        await bot.signup_module_service.clear_closing_notices_on(db, accounts)

    async def take_closing_notices_on(db: aiosqlite.Connection) -> list[str]:
        return await bot.signup_module_service.take_closing_notices_on(db)

    async def end_wizards_on(db: aiosqlite.Connection, accounts: Sequence[str]) -> None:
        for account in accounts:
            await bot.signup_module_service.end_wizard_on(db, str(account))

    async def window_jobs(cause: str, returned: int) -> "tuple[PlannedStep, ...]":
        config = await bot.signup_module_service.get_config()
        return window_close_jobs(
            cause, returned,
            channel_id=None if config is None else config.signup_channel_id,
            button_message_id=None if config is None else config.signup_button_message_id,
        )

    return SeasonEndHooks(
        window_jobs=window_jobs,
        window_steps=window_close_steps(
            signup_module_service=lambda: bot.signup_module_service,
            now=lambda: bot.change_queue.now(),
        ),
        close_signups=close_signups,
        window_open=window_open,
        cancel_close_timer=lambda: bot.scheduler_service.cancel_signup_close_timer(),
        post_signup_notice=post_signup_notice,
        lock_signup_channel=lock_signup_channel,
        cancel_signup_timeout=cancel_signup_timeout,
        discard_portraits=discard_portraits,
        amendment_open=amendment_open,
        plan_forecast_flush=plan_forecast_flush,
        forecast_steps=forecast_steps(now=lambda: bot.change_queue.now()),
        discard_backup=discard_backup,
        forget_setup=lambda: _forget_setup(bot),
        owed_closing_notices=owed_closing_notices,
        clear_closing_notices_on=clear_closing_notices_on,
        take_closing_notices_on=take_closing_notices_on,
        end_wizards_on=end_wizards_on,
    )


def _submission_hooks() -> "SubmissionHooks":
    """What a cancellation needs of results' submissions, handed it here so that core imports no
    module."""
    from leaguebot.core.services.cancellation_changes import SubmissionHooks
    from leaguebot.results.services import result_submission_service
    from leaguebot.results.services.review_posting import cancellation_delete_step

    return SubmissionHooks(
        open_submissions=result_submission_service.open_submissions,
        open_submissions_on=result_submission_service.open_submissions_on,
        close_submissions_on=result_submission_service.close_submissions_on,
        delete_step=cancellation_delete_step(),
    )


def _cancellation_hooks(bot: LeagueBot) -> "CancellationHooks":
    """What a cancellation needs of attendance's check-in call, handed it here so that core
    imports nothing of attendance."""
    from leaguebot.attendance.services.check_in_changes import (
        check_in_deletes,
        forget_check_in_call_on,
    )
    from leaguebot.core.services.cancellation_changes import CancellationHooks

    return CancellationHooks(
        check_in_deletes=check_in_deletes(bot, bot.attendance_service),
        forget_check_in_on=forget_check_in_call_on,
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
            bot, division, await bot.season_service.get_division_rounds(division_id),
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


def _amend_hooks(bot: LeagueBot) -> "AmendHooks":
    """What `/round amend`'s change needs of weather and attendance, handed it here so that core
    imports neither. Each is looked up as it is called, so that nothing is bound before it is
    needed."""
    from leaguebot.attendance.services import rsvp_service
    from leaguebot.attendance.services.check_in_changes import (
        check_in_clean_up_steps,
        check_in_deadline_steps,
        check_in_deletes,
        check_in_steps,
        close_check_in_on,
        forget_check_in_call_on,
        give_up_text_for,
    )
    from leaguebot.core.models.change import PlannedStep
    from leaguebot.core.services.round_amend_change import AmendHooks
    from leaguebot.weather.services import phase_withdrawal
    from leaguebot.weather.services.forecast_changes import (
        DRAW_FORECAST,
        forecast_failure,
        forecast_steps,
    )
    from leaguebot.weather.utils.message_builder import format_round_list, invalidation_message

    async def windows() -> "tuple[AttendanceWindows | None, WeatherWindows]":
        return await bot.amendment_windows()

    def plan_phase(round_id: int, phase: int) -> "tuple[PlannedStep, ...]":
        return (PlannedStep(DRAW_FORECAST, {"round_id": round_id, "phase": phase}),)

    return AmendHooks(
        windows=windows,
        withdraw_phases_on=phase_withdrawal.withdraw_phases_on,
        forecast_steps=forecast_steps(now=lambda: bot.change_queue.now()),
        plan_phase=plan_phase,
        forecast_failure=forecast_failure,
        invalidation_text=invalidation_message,
        reopen_check_in_on=rsvp_service.reopen_check_in_on,
        call_steps=check_in_steps(bot.attendance_service, now=lambda: bot.change_queue.now()),
        give_up_text=give_up_text_for,
        give_up_on=close_check_in_on,
        deadline_steps=check_in_deadline_steps(
            bot.attendance_service, now=lambda: bot.change_queue.now()
        ),
        clean_up_steps=check_in_clean_up_steps(),
        round_list=format_round_list,
        check_in_deletes=check_in_deletes(bot, bot.attendance_service),
        forget_check_in_on=forget_check_in_call_on,
    )


def register_change_types(bot: LeagueBot) -> None:
    """Make the queue known every kind of change the bot carries out.

    The builder is where modules meet: it hands each change type what it needs of another module,
    so that core's queue and cogs import none of them. The tests' support module calls this to get
    the real set.
    """
    from leaguebot.attendance.services.check_in_answer_changes import check_in_answer_change
    from leaguebot.attendance.services.check_in_changes import (
        check_in_call_change,
        check_in_clean_up_change,
        check_in_deadline_change,
        check_in_last_notice_change,
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
    from leaguebot.core.services.channel_tell_change import channel_tell_change
    from leaguebot.core.services.clean_change import clean_change
    from leaguebot.core.services.hub_service import hub_refresh_change
    from leaguebot.core.services.lineup_posting import lineup_post_change
    from leaguebot.core.services.round_amend_change import (
        amendment_in_hand,
        round_amend_change,
        round_rearm_change,
    )
    from leaguebot.core.services.season_approval_change import (
        season_approval_change,
        season_approval_tell_change,
    )
    from leaguebot.core.services import season_review_changes
    from leaguebot.core.services.season_review_changes import season_review_take_down_change
    from leaguebot.core.services.season_end_changes import season_abort_change, season_complete_change
    from leaguebot.core.services.season_lifecycle_service import (
        wind_down_change,
    )
    from leaguebot.image.services.image_render_service import (
        CONVERTER_NAME,
        converter_available,
        discard_render,
    )
    from leaguebot.results.services.season_points_service import snapshot_configs_to_season_on
    from leaguebot.core.services.setting_change import setting_change
    from leaguebot.image.services.image_settings import image_settings
    from leaguebot.results.services.amendment_settings import amendment_settings
    from leaguebot.results.services.points_settings import points_settings
    from leaguebot.weather.services.weather_settings import deadline_setting
    from leaguebot.weather.services.forecast_changes import (
        forecast_change,
        forecast_clean_up_change,
        forecast_flush_change,
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
    from leaguebot.core.cogs.module_cog import RETURNED_BY_CLOSE
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
        results_off_change(attendance_off_on=bot.attendance_service.switch_off_on)
    )
    bot.change_queue.register(submission_open_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(submission_restart_change())
    bot.change_queue.register(submission_all_cancelled_change())
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
    bot.change_queue.register(
        amend_open_change(
            modules=bot.module_service, server_config=bot.config_service.get_server_config,
            now=lambda: bot.change_queue.now(), arm_once=bot.scheduler_service.arm_once,
            queue_of=lambda: bot.change_queue,
        )
    )
    bot.change_queue.register(amend_cancel_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(amend_lapse_change(now=lambda: bot.change_queue.now()))

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
    bot.change_queue.register(
        window_open_change(
            modules=bot.module_service, signup=bot.signup_module_service,
            config=bot.config_service,
            schedule_close=lambda iso: bot.scheduler_service.schedule_signup_close_timer(iso),
            queue=lambda: bot.change_queue,
        )
    )
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
    # A correction nobody chose within five minutes is lapsed by the change its timer asks of the
    # queue, as the bot.
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
    bot.scheduler_service.register_timer(
        TIMER, lock_clear_timer(bot.db_path, lambda: bot.change_queue)
    )
    bot.change_queue.register(appeals_open_change())
    bot.change_queue.register(close_stale_change())
    # The review's change types stamp what they write with the queue's own clock, read when they
    # run (the queue is rebuilt at a restart), and reach attendance through the hook alone.
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
    bot.change_queue.register(hub_refresh_change(bot.change_queue))
    bot.change_queue.register(calendar_sync_change(bot.season_service))
    bot.change_queue.register(lineup_post_change(bot.placement_service))
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
    bot.change_queue.register(forecast_flush_change(now=lambda: bot.change_queue.now()))
    hooks = _season_end_hooks(bot)
    bot.change_queue.register(wind_down_change(placement=bot.placement_service, hooks=hooks))
    bot.change_queue.register(
        season_complete_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            hooks=hooks,
        )
    )
    bot.change_queue.register(
        season_cancel_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            submissions=_submission_hooks(),
            cancellation=_cancellation_hooks(bot),
            hooks=hooks,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        season_abort_change(
            seasons=bot.season_service, placement=bot.placement_service, hooks=hooks
        )
    )
    # The season's approval is judged again as it runs: the clock is the queue's, the windows the
    # enabled modules', and the drawing program the host's, read through `converter_available`
    # when it is judged, so a test that patches it moves the judgement.
    bot.change_queue.register(
        season_approval_change(
            modules=bot.module_service,
            config=bot.config_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            placement=bot.placement_service,
            windows=lambda: bot.approval_windows(),
            rasteriser_fault=lambda: None
            if converter_available()
            else f"{CONVERTER_NAME} is not installed on this host.",
            snapshot_points_on=snapshot_configs_to_season_on,
            forget_setup=lambda: _forget_setup(bot),
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(season_approval_tell_change())
    bot.change_queue.register(season_review_take_down_change())
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
    for setting in (
        *points_settings(modules=bot.module_service, seasons=bot.season_service),
        *amendment_settings(modules=bot.module_service, seasons=bot.season_service),
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
            hooks=_amend_hooks(bot),
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        round_rearm_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            hooks=_amend_hooks(bot),
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        round_cancel_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            submissions=_submission_hooks(),
            cancellation=_cancellation_hooks(bot),
            amendment_in_hand=lambda division_id: amendment_in_hand(bot.db_path, division_id),
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        division_cancel_change(
            modules=bot.module_service,
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            submissions=_submission_hooks(),
            cancellation=_cancellation_hooks(bot),
            amendment_in_hand=lambda division_id: amendment_in_hand(bot.db_path, division_id),
            now=lambda: bot.change_queue.now(),
        )
    )


async def main() -> None:
    from leaguebot.core.db.database import run_migrations
    from leaguebot.core.services.config_service import ConfigService
    from leaguebot.core.services.season_service import SeasonService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.output_router import OutputRouter

    # A staged restore is swapped in **here**, before a single service is constructed and
    # before the scheduler opens its job store. Every service captures its path and holds
    # connections open, so this is the one moment in the process at which replacing the
    # files underneath them is safe. `/test-mode backup restore` stages and does no more,
    # precisely so that the swap can happen at this point on the next start — under a
    # service or from a terminal alike.
    from leaguebot.core.services.backup_service import apply_staged_restore
    from leaguebot.core.services.scheduler_service import default_jobstore_path

    if apply_staged_restore(DB_PATH, SCHEDULER_DB_PATH or default_jobstore_path(DB_PATH)):
        log.info("Started on a restored database.")

    bot = create_bot()

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

    # Registers the hub's About option, core's one, before `on_ready` recovers the hub and
    # routes the panel's buttons (#258).
    import leaguebot.core.services.about_service  # noqa: F401

    # Services are attached to bot for cog access
    from leaguebot.core.services.driver_service import DriverService
    from leaguebot.core.services.team_service import TeamService

    bot.config_service = ConfigService(DB_PATH)
    bot.season_service = SeasonService(DB_PATH)
    bot.scheduler_service = SchedulerService(
        DB_PATH, SCHEDULER_DB_PATH or None
    )
    bot.output_router = OutputRouter(bot, bot.config_service, retry_db_path=DB_PATH)
    # The queue is handed the router, and is started by `on_ready`, after the recoveries.
    from leaguebot.core.services.change_queue import ChangeQueue

    bot.change_queue = ChangeQueue(DB_PATH, bot, bot.output_router, bot.config_service)
    bot.driver_service = DriverService(DB_PATH)
    bot.team_service = TeamService(DB_PATH)

    from leaguebot.core.services.placement_service import PlacementService
    # The bot is handed over so the lineup refresh can reach the image module (038). The
    # textual lineup does not use it.
    bot.placement_service = PlacementService(DB_PATH, bot)

    from leaguebot.core.services.module_service import ModuleService
    from leaguebot.signup.services.signup_module_service import SignupModuleService
    from leaguebot.signup.services.wizard_service import WizardService
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.core.services.output_router import OutputRouter as _OutputRouter  # already imported above

    bot.module_service = ModuleService(DB_PATH)
    bot.signup_module_service = SignupModuleService(DB_PATH)
    bot.wizard_service = WizardService(
        DB_PATH,
        bot.scheduler_service,
        bot.output_router,
        change_queue=bot.change_queue,
    )
    # Bound as soon as it exists rather than in `on_ready`. The persistent views are routed
    # from the moment the gateway opens, and a signup press arriving while `on_ready` was
    # still recovering found the wizard with no bot and failed (#228).
    bot.wizard_service.set_bot(bot)
    bot.attendance_service = AttendanceService(DB_PATH)

    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    # Attendance's share of a round's review, handed the placement service it moves seats
    # through; results' change types reach attendance through it alone (#439).
    bot.attendance_after_review = AttendanceAfterReview(bot, bot.placement_service)
    bot.approval_windows = lambda: read_approval_windows(bot)
    bot.amendment_windows = lambda: read_amendment_windows(bot)

    from leaguebot.results.services.penalty_wizard import clear_held_reviews

    bot.clear_held_reviews = clear_held_reviews

    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_validity_service import ImageValidityService

    bot.image_config_service = ImageConfigService(DB_PATH)
    bot.image_validity_service = ImageValidityService(
        bot.image_config_service,
        bot.module_service,
    )

    from leaguebot.image.services.image_render_service import ImageRenderService

    bot.image_render_service = ImageRenderService(
        bot.image_config_service,
        bot.image_validity_service,
    )

    register_change_types(bot)

    @bot.event
    async def on_ready() -> None:
        # `on_ready` fires once the bot has logged in, which is when it has a user.
        assert bot.user is not None
        log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)

        # Run DB migrations on startup
        await run_migrations(DB_PATH)

        # Start the persistent APScheduler
        bot.scheduler_service.start()

        # Wire weather's callbacks into the scheduler: each asks the change queue
        from leaguebot.weather.services.forecast_changes import timer_callbacks

        callbacks = timer_callbacks(bot.change_queue, bot.db_path)
        bot.scheduler_service.register_callbacks(
            callbacks.phases[1], callbacks.phases[2], callbacks.phases[3]
        )
        bot.scheduler_service.register_mystery_notice_callback(callbacks.mystery_notice)
        bot.scheduler_service.register_forecast_cleanup_callback(callbacks.clean_up)

        # Register result submission callback
        from leaguebot.results.services.submission_changes import ask_submission_open

        async def _result_submission_cb(round_id: int) -> None:
            await ask_submission_open(bot.change_queue, bot.db_path, round_id)

        bot.scheduler_service.register_result_submission_callback(_result_submission_cb)

        # Register RSVP attendance callbacks
        from leaguebot.attendance.services.check_in_changes import (
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

        bot.scheduler_service.register_rsvp_notice_callback(_rsvp_notice_cb)
        bot.scheduler_service.register_rsvp_last_notice_callback(_rsvp_last_notice_cb)
        bot.scheduler_service.register_rsvp_deadline_callback(_rsvp_deadline_cb)
        bot.scheduler_service.register_rsvp_cleanup_callback(_rsvp_cleanup_cb)

        # Register the daily driver-portrait refresh and re-arm it after a restart. Unlike
        # every other job here the trigger is recurring, so recovery re-adds it rather than
        # working out whether it was missed: a cron job that did not fire while the bot was
        # down simply fires at its next due time.
        async def _portrait_refresh_cb() -> None:
            from leaguebot.image.services.driver_portrait_service import run_daily_refresh
            await run_daily_refresh(bot)

        bot.scheduler_service.register_portrait_refresh_callback(_portrait_refresh_cb)

        # The standing sweep that undoes an amendment nobody carried through (#345). Armed
        # unconditionally rather than behind a setting: the amendment's first stage commits, so
        # a league running the results module at all can leave a round half-amended, and the
        # sweep is what makes that recoverable without anybody noticing it happened.
        async def _amendment_sweep_cb() -> None:
            from leaguebot.results.services.result_submission_service import sweep_expired_amendments
            await sweep_expired_amendments(bot, queue=bot.change_queue)

        bot.scheduler_service.register_amendment_sweep_callback(_amendment_sweep_cb)
        bot.scheduler_service.schedule_amendment_sweep()

        try:
            await _recover_portrait_refresh_job(bot)
        except Exception:
            log.warning("Portrait refresh recovery failed", exc_info=True)

        # Register signup auto-close callback and recover any timers lost on restart (T021)
        from leaguebot.signup.services.signup_module_service import SignupModuleService as _SignupModuleSvc
        from leaguebot.core.db.database import get_connection as _get_conn
        from datetime import datetime as _dt, timezone as _tz

        async def _signup_close_cb() -> None:
            from leaguebot.core.cogs.module_cog import close_signups_unattended
            await close_signups_unattended(bot, cause="timer")

        bot.scheduler_service.register_signup_close_callback(_signup_close_cb)

        async def _recover_signup_close_timers() -> None:
            async with _get_conn(DB_PATH) as _db:
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
                log.warning("on_ready: invalid signup close_at value: %r", _close_at_iso)
                return
            if _close_dt <= now_utc:
                log.info("on_ready: signup close_at is past — running forced close")
                from leaguebot.core.cogs.module_cog import close_signups_unattended
                await close_signups_unattended(bot, cause="restart")
            else:
                from leaguebot.core.services.scheduler_service import SIGNUP_CLOSE_JOB_ID
                if not bot.scheduler_service.has_job(SIGNUP_CLOSE_JOB_ID):
                    log.info("on_ready: re-arming the signup close timer at %s", _close_at_iso)
                    bot.scheduler_service.schedule_signup_close_timer(_close_at_iso)

        await _recover_signup_close_timers()

        # Close any results-amend channels left open by a previous run.
        #
        # **Before the submission channels, not after** (#345). An amendment open at the restart
        # has its corrections in the database, unapproved; recovering a submission channel whose
        # results were saved but never posted posts its standings from that database. Reverting
        # the amendments first means those standings are built from the round as it raced,
        # wherever the revert succeeds.
        await _recover_orphaned_amend_channels(bot)

        # Close any submission channels that were left open by a previous run.
        # Their wait_for loops died with the process, so we reset them here
        # so /test-mode advance can re-trigger submission.
        await _recover_orphaned_submission_channels(bot)

        # Clear any season-review approve button left standing by a previous run.
        await _recover_expired_review_prompts(bot)

        # Recover any missed phases from before bot restart
        await _recover_missed_phases(bot)

        # Post late the check-in calls that fell due while the bot was down, their deadline
        # still ahead (#429). Before the deadlines below, as a call comes before its deadline.
        await _recover_missed_check_in_calls(bot)

        # Re-arm persistent RSVP embed views for all stored embed messages (T010)
        # and run missed RSVP deadline jobs for rounds whose deadline already passed (T019)
        await _recover_rsvp_views_and_deadlines(bot)

        # Take down the forecasts and check-in messages due to come down while the bot was
        # stopped (#425). After the deadlines above, so a check-in is distributed before it goes.
        await _recover_missed_cleanups(bot)

        # Route the hub's buttons again, and post its panel afresh where it was deleted (#279).
        try:
            from leaguebot.core.services.hub_service import recover_hub

            await recover_hub(bot, queue=bot.change_queue)
        except Exception:
            log.warning("Hub recovery failed", exc_info=True)

        # Re-arm inactivity APScheduler jobs for any non-UNENGAGED wizard sessions
        # that were active before the last restart.
        try:
            await bot.wizard_service.recover_wizards()
        except NotImplementedError:
            pass  # stub until T030 is implemented

        # Restore in-memory pending setups from DB SETUP seasons
        await _recover_pending_setups(bot)

        # Carry on the changes a stop cut off. A change asked for while the recoveries ran has
        # waited, queued, until here. A second `on_ready` finds the worker running and does nothing.
        await bot.change_queue.start()

        # Sync slash commands globally (may take up to 1 hour to propagate)
        try:
            synced = await bot.tree.sync()
            log.info("Synced %d slash command(s)", len(synced))
        except discord.HTTPException as exc:
            log.error("Failed to sync slash commands: %s", exc)

    @bot.event
    async def on_disconnect() -> None:
        log.warning("Bot disconnected from Discord")

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
    await bot.add_cog(SeasonCog(bot))
    await bot.add_cog(AmendmentCog(bot))
    await bot.add_cog(TestModeCog(bot))
    await bot.add_cog(TrackCog(bot))
    await bot.add_cog(DriverCog(bot))
    await bot.add_cog(TeamCog(bot))
    await bot.add_cog(ModuleCog(bot))
    await bot.add_cog(SignupCog(bot))
    await bot.add_cog(AdminReviewCog(bot))
    await bot.add_cog(RetryCog(bot))
    await bot.add_cog(ResultsCog(bot))
    await bot.add_cog(WeatherCog(bot))
    await bot.add_cog(AttendanceCog(bot))
    await bot.add_cog(ImageCog(bot))
    await bot.add_cog(CleanCog(bot))

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

    log.info("All cogs loaded. Starting bot...")

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

    async with bot:
        await bot.start(TOKEN)


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

    Runs before `_recover_rsvp_views_and_deadlines` in `on_ready`, the call coming before its
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

    A cancelled round or division is left alone. Its cancellation removed both jobs, and the
    weather module keeps the forecasts of a round called off. The season's status is not asked,
    since the live jobs fire after a season completes as well. Each module's own gate applies:
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
    from leaguebot.core.services.scheduler_service import POST_RACE_CLEANUP_DELAY

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
                   AND r.status != 'CANCELLED'
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
                 WHERE r.status != 'CANCELLED'
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


async def _recover_expired_review_prompts(bot: LeagueBot) -> None:
    """Ask the change queue to take down every season review left standing by a previous run.

    The button expires five minutes after the review posts its question, by a timer held in
    memory and lost with the process. A bot restarted inside that window would otherwise leave a
    public message offering a button nothing is counting, for ever.

    Every row that survives to start-up is expired by definition: the bot was down, so the five
    minutes cannot have been served. Each is asked for as a ``season.review.take_down`` ending
    ``restart``, as the bot, **before the queue starts** (core specification, "What survives a
    restart"): its jobs delete the question and the report above it, post the notice pinging the
    reviewer, record the lapse in the log channel and drop the row. Nothing is deleted, posted or
    written here, so a deletion Discord refuses stops the queue with the row standing, rather than
    being lost.

    A row whose take-down is already queued, running or stopped is left to it and asked for no
    second time: what a review's press has marked is held in memory and empty after a restart, so
    the queue is what says. Each question's button is routed again by the id of its message, so a
    press made before the take-down has run reaches a view that refuses it and records the refusal
    (a press reads the row, which says its five minutes ran).
    """
    from leaguebot.core.db.database import get_connection
    from leaguebot.core.services.change_queue import unfinished
    from leaguebot.core.services.season_review_changes import RESTART, TAKE_DOWN, take_review_down

    try:
        async with get_connection(bot.db_path) as db:
            cursor = await db.execute(
                "SELECT message_id, reviewer_id, review, verb FROM season_review_prompts"
            )
            prompts = await cursor.fetchall()
        in_hand = {
            int(payload["question_id"])
            for payload in await unfinished(bot.db_path, [TAKE_DOWN])
        }
    except Exception:
        log.exception("could not read the standing season review prompts")
        return

    view_for = _approve_view_for(bot)
    for row in prompts:
        question_id = int(row["message_id"])
        try:
            bot.add_view(
                view_for({"view": _review_view_of(row), "reviewer_id": row["reviewer_id"]}),
                message_id=question_id,
            )
        except Exception:
            log.exception("could not route the button of the review at %s again", question_id)
        if question_id in in_hand:
            log.info("the take-down of the review at %s is in hand, so it is left to it", question_id)
            continue
        try:
            await take_review_down(bot.change_queue, bot.db_path, question_id, RESTART)
        except Exception:
            log.exception("could not ask for the take-down of the review at %s", question_id)


def _review_view_of(row: aiosqlite.Row) -> str:
    """The family of the button a standing review's row belongs to, as the builder's factory of
    review views names it. The configuration review is told by its command. The other command,
    `/season placements-review`, posts two kinds of question whose buttons carry different custom
    ids: the placements approval (``approve``, row verb ``approved``) and the mid-season
    confirmation (``mid_season``, row verb ``confirmed``), which `record_question` writes from the
    view it was posted with. Routed under the wrong id, a press finds no item and is dropped
    without a refusal, so the row's verb is what is read."""
    from leaguebot.core.services.season_review_changes import VERBS

    if str(row["review"]) == "/season config-review":
        return "configuration"
    return "approve" if str(row["verb"]) == VERBS["approve"] else "mid_season"


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


async def _recover_pending_setups(bot: LeagueBot) -> None:
    """Restore in-memory pending season configs from DB SETUP seasons."""
    from leaguebot.core.cogs.season_cog import SeasonCog
    season_cog = cast("SeasonCog | None", bot.get_cog("SeasonCog"))
    if season_cog is not None:
        await season_cog.recover_pending_setups()


if __name__ == "__main__":
    asyncio.run(main())
