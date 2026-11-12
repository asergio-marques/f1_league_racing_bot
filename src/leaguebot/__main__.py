"""Entry point for the F1 League Racing Bot."""

import asyncio
import logging
import os
from pathlib import Path
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Any

import discord
from discord.ext import commands
from dotenv import load_dotenv

from leaguebot.attendance import wiring as attendance_wiring
from leaguebot.core.services.seasons.season_review_changes import recover_expired_review_prompts
from leaguebot.core.services.upkeep.start_up import start_up
from leaguebot.core.utils.hooks import AwaitedHook, Hook, StartingHook
from leaguebot.core.utils.interaction_errors import start_up_failure_line
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import (
    league_guild,
    LeagueCommandTree,
    on_event_error,
    warn_if_serving_several,
)
from leaguebot.core.utils.log_filters import install_late_autocomplete_filter
from leaguebot.image import wiring as image_wiring
from leaguebot.results import wiring as results_wiring
from leaguebot.signup import wiring as signup_wiring
from leaguebot.weather import wiring as weather_wiring

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.services.modules.hub_service import HubOptions

#: The oldest discord.py the bot runs on. `/team add` and `/team modify` take their input in a
#: modal carrying a role picker, which needs ``discord.ui.Label`` — new in 2.6. A host
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

    # The tree refuses every command from a server that is not the league's.
    # Every attribute attached below is declared on `LeagueBot` first.
    bot = LeagueBot(
        command_prefix="!", intents=intents, help_command=None, tree_cls=LeagueCommandTree
    )
    # Tell the host whenever the bot finds itself in more than one server.
    async def _warn_if_serving_several(*_: object) -> None:
        warn_if_serving_several(bot)

    bot.add_listener(_warn_if_serving_several, "on_ready")
    bot.add_listener(_warn_if_serving_several, "on_guild_join")
    # A listener that raises is reported by one handler, never by a method of the bot. It is set
    # with `setattr` because the type check reads an assignment to `on_error` as one to a method,
    # and no function named `on_error` may stand outside `league_server.py`.
    setattr(bot, "on_error", partial(on_event_error, bot))

    return bot


def register_module_switches(modules: "ModuleService") -> None:
    """Sign each module up with `ModuleService` for the reader of its own switch, keyed by the
    name `is_<module>_enabled` asks it by."""

    weather_wiring.register_switch(modules)
    signup_wiring.register_switch(modules)
    results_wiring.register_switch(modules)
    attendance_wiring.register_switch(modules)
    image_wiring.register_switch(modules)


def register_hub_options(hub_options: "HubOptions") -> None:
    """Sign each option the hub's panel carries up on *hub_options*: core's About. A module's
    option is signed up here, beside it, by its module's own function."""
    from leaguebot.core.services.modules.about_service import ABOUT_OPTION

    hub_options.register_option(ABOUT_OPTION)


def _make_hooks(bot: LeagueBot) -> None:
    """Make every hook only one module joins, before any module's `sign_up` runs, so that its
    wiring signs up on a hook that exists. The hooks several modules join are made by the function that
    signs those modules up, in the order the hook calls them."""
    bot.confirmation_refused = AwaitedHook()
    bot.cancellation_refused = AwaitedHook()
    bot.rounds_made_final = AwaitedHook()
    bot.test_mode_turned_on = AwaitedHook()
    bot.divisions_raced_read = AwaitedHook()
    bot.season_standing_read = AwaitedHook()
    bot.final_round_read = AwaitedHook()
    bot.season_end_refused = AwaitedHook()
    bot.base_role_changed = AwaitedHook()
    bot.phases_drawn_read = AwaitedHook()
    bot.amendment_step_failure = Hook()
    bot.test_mode_flushed = AwaitedHook()
    bot.drivers_deleted = AwaitedHook()
    bot.configuration_confirmed = AwaitedHook()
    bot.driver_names_read = AwaitedHook()
    bot.driver_turned_down = AwaitedHook()
    bot.driver_account_changed = AwaitedHook()
    bot.driver_account_changed_steps = Hook()
    bot.season_end_closes = Hook()
    bot.drivers_returned = AwaitedHook()
    bot.driver_returned_jobs = Hook()


def _sign_up_to_round_windows(bot: LeagueBot) -> None:
    """Make the hook a round's timing windows read declares, and sign each module up to it, in
    the order core gathers them: attendance's window, then weather's. Each is handed whether the
    round is being amended and checks its own switch."""
    bot.round_windows_read = AwaitedHook()
    attendance_wiring.sign_up_to_round_windows(bot)
    weather_wiring.sign_up_to_round_windows(bot)


def _sign_up_to_review_sections(bot: LeagueBot) -> None:
    """Make the hook a season review gathers its lines through, and sign each module's section
    up to it, in the order the review lays them out: signup's, attendance's, results' then
    weather's. Each is handed which review is gathered, the season and its divisions, and checks
    its own switch."""
    bot.review_sections_read = AwaitedHook()
    signup_wiring.sign_up_to_review_sections(bot)
    attendance_wiring.sign_up_to_review_sections(bot)
    results_wiring.sign_up_to_review_sections(bot)
    weather_wiring.sign_up_to_review_sections(bot)


def _make_configuration_faults(
    bot: LeagueBot,
) -> Callable[[int, discord.Guild | None], Awaitable[list[str]]]:
    """The reader of a season configuration's faults the review is handed: the gate
    (`approval_checks.configuration_faults`) handed the bot's services by name, read as they
    stand when it is asked, so that core's review reads no module's service on the bot."""
    from leaguebot.core.services.seasons import approval_checks

    async def configuration_faults(season_id: int, guild: discord.Guild | None = None) -> list[str]:
        return await approval_checks.configuration_faults(
            season_id,
            guild,
            module_service=bot.module_service,
            signup_module_service=bot.signup_module_service,
            config_service=bot.config_service,
            team_service=bot.team_service,
            confirmation_refused=bot.confirmation_refused,
            image_drawing=bot.image_drawing,
        )

    return configuration_faults


def _sign_up_to_amendments(bot: LeagueBot) -> None:
    """Make the hooks an amended round declares, and sign each module's part up to them, in the
    order core calls them: the phases weather says are drawn, asked of a round as it is judged and
    again inside the save on its connection; inside the save, weather's withdrawal of the
    forecasts the amendment withdraws, then attendance's reopening of its check-in; as the round
    is armed again, the posts each owes, attendance's then weather's, then the clean-ups due a day after the round,
    weather's then attendance's; the steps each defines for the amendment, weather's then
    attendance's; and the words an outcome gives a step left undone, weather's for a forecast."""
    bot.round_amended = AwaitedHook()
    weather_wiring.sign_up_to_round_amended(bot)
    attendance_wiring.sign_up_to_round_amended(bot)
    bot.round_armed = AwaitedHook()
    attendance_wiring.sign_up_to_round_armed(bot)
    weather_wiring.sign_up_to_round_armed(bot)
    bot.round_clean_ups_due = AwaitedHook()
    weather_wiring.sign_up_to_round_clean_ups_due(bot)
    attendance_wiring.sign_up_to_round_clean_ups_due(bot)
    bot.amendment_steps = Hook()
    weather_wiring.sign_up_to_amendment_steps(bot)
    attendance_wiring.sign_up_to_amendment_steps(bot)


def _sign_up_to_cancellations(bot: LeagueBot) -> None:
    """Make the hooks a cancellation of rounds declares, and sign each module's part up to them,
    in the order core calls them: what stands in the way (results' refusal of a round whose
    submission accepted results), the rounds called off (results closes their empty submissions,
    in the save, then attendance's take-down of their calls), the notices told of a division
    (attendance's check-in notice, then weather's forecast note, then results' results note),
    and the steps a module plans (results' deletion of a submission channel, attendance's
    take-down of a call, attendance's reading of a round's check-in record, then each module's
    own notice, attendance's, weather's, then results'). Results' not-held round end is handed
    `cancellation_notices` too, which core's writer of it calls."""
    bot.rounds_called_off = AwaitedHook()
    results_wiring.sign_up_to_rounds_called_off(bot)
    attendance_wiring.sign_up_to_rounds_called_off(bot)
    bot.cancellation_notices = AwaitedHook()
    attendance_wiring.sign_up_to_cancellation_notices(bot)
    weather_wiring.sign_up_to_cancellation_notices(bot)
    results_wiring.sign_up_to_cancellation_notices(bot)
    bot.cancellation_steps = Hook()
    results_wiring.sign_up_to_cancellation_steps(bot)
    attendance_wiring.sign_up_to_cancellation_steps(bot)
    weather_wiring.sign_up_to_cancellation_steps(bot)
    results_wiring.sign_up_to_cancellation_notice_steps(bot)


def _sign_up_to_deletes(bot: LeagueBot) -> None:
    """Make the hooks a season, a division, a round or a driver deleted, or a driver merged,
    declares, and sign each module's part up to them, in the order core calls them: each module
    deleting or re-pointing its own rows, on the delete's connection, inside its save
    (architecture.md, "The database")."""
    bot.rounds_deleted = AwaitedHook()
    bot.divisions_deleted = AwaitedHook()
    bot.season_deleted = AwaitedHook()
    bot.profiles_deleted = AwaitedHook()
    bot.profiles_merged = AwaitedHook()
    # Attendance's parts, then results', then signup's and weather's, so that each hook is called
    # in the order it always was.
    attendance_wiring.sign_up_to_deletes(bot)
    results_wiring.sign_up_to_deletes(bot)
    signup_wiring.sign_up_to_season_deleted(bot)
    weather_wiring.sign_up_to_deletes(bot)


def _sign_up_to_pack(bot: LeagueBot) -> None:
    """Make the hook the bot packed declares, for the modules to sign up to: each module clearing
    its own rows tied to the server the bot leaves, on the pack's connection, inside its one
    transaction (architecture.md, "The database"). The builder hands it to `/bot pack`."""
    bot.bot_packed = AwaitedHook()
    signup_wiring.sign_up_to_pack(bot)
    weather_wiring.sign_up_to_pack(bot)
    attendance_wiring.sign_up_to_pack(bot)
    results_wiring.sign_up_to_pack(bot)


def _sign_up_to_division_lines_read(bot: LeagueBot) -> None:
    """Make the hook the placements review gathers each division's module lines through, and sign
    each module up to it, in the order the review lays the lines out: results' three channels,
    handed the database, then attendance's two, handed its service and the module service, then
    weather's one, handed the database. Each is handed the season and the divisions the review
    shows (architecture.md, "How modules and
    core fit together")."""
    bot.division_lines_read = AwaitedHook()
    results_wiring.sign_up_to_division_lines_read(bot)
    attendance_wiring.sign_up_to_division_lines_read(bot)
    weather_wiring.sign_up_to_division_lines_read(bot)


def _sign_up_to_channel_uses_read(bot: LeagueBot) -> None:
    """Make the hook a channel being set is asked of each module through, so that a channel does
    one job, and sign each module up to it, handed the database's path: the uses of the channel its
    settings hold in the live season, in the order a division's channels read (weather, results,
    attendance, then signup's own). The builder hands it to every command that sets a channel
    (architecture.md, "How modules and core fit together")."""
    bot.channel_uses_read = AwaitedHook()
    weather_wiring.sign_up_to_channel_uses_read(bot)
    results_wiring.sign_up_to_channel_uses_read(bot)
    attendance_wiring.sign_up_to_channel_uses_read(bot)
    signup_wiring.sign_up_to_channel_uses_read(bot)


def _sign_up_to_setting_channels_read(bot: LeagueBot) -> None:
    """Make the hook the factory reset asks each module through for every channel it
    holds (those its settings name, those it posted to and those it created), in any season, and
    sign each module up to it, handed the database's path, in the same
    order (architecture.md, "How modules and core fit together")."""
    bot.setting_channels_read = AwaitedHook()
    weather_wiring.sign_up_to_setting_channels_read(bot)
    results_wiring.sign_up_to_setting_channels_read(bot)
    attendance_wiring.sign_up_to_setting_channels_read(bot)
    signup_wiring.sign_up_to_setting_channels_read(bot)


def _sign_up_to_division_channel_faults_read(bot: LeagueBot) -> None:
    """Make the hook a division's channel faults are read through, and sign each module's part up
    to it: the lines naming that module's own channels not set or not on the server, by division,
    handed the divisions judged and the server. Weather and results are handed the module service
    and the database, attendance its service and the module service, each asking its own switch;
    they sign up in the order a division's lines read. The builder hands it to the placements
    review and the confirmations (architecture.md, "How modules and core fit together")."""
    bot.division_channel_faults_read = AwaitedHook()
    weather_wiring.sign_up_to_division_channel_faults_read(bot)
    results_wiring.sign_up_to_division_channel_faults_read(bot)
    attendance_wiring.sign_up_to_division_channel_faults_read(bot)


def _sign_up_to_fingerprint_areas_read(bot: LeagueBot) -> None:
    """Make the hook a season's fingerprint is taken through, and sign each module's part up to
    it: the rows of that module's own configuration, by area, handed the fingerprint's connection
    and the season's id. They sign up in the order a division's channels read (architecture.md,
    "How modules and core fit together"). The builder hands it to every caller of
    `take_fingerprint`."""
    bot.fingerprint_areas_read = AwaitedHook()
    # Weather, results and attendance read their channels by division, as the season service
    # reads the divisions of the season; signup and image read the server's configuration.
    weather_wiring.sign_up_to_fingerprint_areas_read(bot)
    results_wiring.sign_up_to_fingerprint_areas_read(bot)
    attendance_wiring.sign_up_to_fingerprint_areas_read(bot)
    signup_wiring.sign_up_to_fingerprint_areas_read(bot)
    image_wiring.sign_up_to_fingerprint_areas_read(bot)


def _sign_up_to_pending_steps_read(bot: LeagueBot) -> None:
    """Make the hook `/test-mode advance` reads the pending steps through, and sign each module's
    part up to it: the steps of the rounds core hands it that the module still has pending, and
    those advance passes over, from its own state, handed the database path, the rounds and the
    moment advance judges at. Weather, attendance and results sign up; core orders what they hand
    back by its own step order (architecture.md, "How modules and core fit together"). The builder
    hands it to the command."""
    bot.pending_steps_read = AwaitedHook()
    weather_wiring.sign_up_to_pending_steps_read(bot)
    attendance_wiring.sign_up_to_pending_steps_read(bot)
    results_wiring.sign_up_to_pending_steps_read(bot)


def _sign_up_to_review_marks_read(bot: LeagueBot) -> None:
    """Make the hook `/test-mode review` reads each module's marks through, and sign each module's
    part up to it: its own marks of the rounds core hands it, from its own state, handed the
    database path, the rounds, the moment the review judges at and the jobs the scheduler holds.
    Weather, results and attendance sign up, in the order the review lays their marks out; core
    lays out the divisions and rounds around them (architecture.md, "How modules and core fit
    together"). The builder hands it to the command."""
    bot.review_marks_read = AwaitedHook()
    weather_wiring.sign_up_to_review_marks_read(bot)
    results_wiring.sign_up_to_review_marks_read(bot)
    attendance_wiring.sign_up_to_review_marks_read(bot)


def _sign_up_to_placements_confirmed(bot: LeagueBot) -> None:
    """Make the hooks placements confirmed (the season's approval) declares, and sign each
    module's opening post up to them: the jobs it plans for a division, handed the save's
    connection and the division's id and name (architecture.md, "How modules and core fit
    together"), and the step it defines for its own job, by name. The builder hands them to
    `season_approval_change`. Results signs up first, so each division's jobs stay lineup,
    calendar, standings, sheet."""
    bot.placements_confirmed = AwaitedHook()
    bot.placements_confirmed_steps = Hook()
    results_wiring.sign_up_to_placements_confirmed(bot)
    attendance_wiring.sign_up_to_placements_confirmed(bot)


def _sign_up_to_season_completed(bot: LeagueBot) -> None:
    """Make the hooks a division's final round read and a season completed declare, and sign
    each module's final post up to them: results' read of its own `session_results` and its
    final standings, then attendance's final sheet. The builder hands them to
    `season_complete_change`."""
    bot.season_completed = AwaitedHook()
    bot.season_completed_steps = Hook()
    results_wiring.sign_up_to_season_completed(bot)
    attendance_wiring.sign_up_to_season_completed(bot)


def _sign_up_to_season_end(bot: LeagueBot) -> None:
    """Make the hooks a season's end declares, and sign each module's part up to them, in the order
    core calls them: what stands in the way of the completion and of the cancellation (results'
    refusal of a season with a round being amended); the jobs that close what a module holds open
    (signup's close of its window); inside its saves, signup's ending of the wizards of the
    drivers returned; the jobs planned for each driver returned (signup's); the steps the modules
    define for it (signup's close of its window, the button's deletion and the closed post, each
    driver's notice and channel's close, then weather's deletion of a forecast); the jobs that
    clear what test mode posted (weather's)."""
    bot.season_end_steps = Hook()
    signup_wiring.sign_up_to_season_end_steps(bot)
    weather_wiring.sign_up_to_season_end_steps(bot)


async def _draw_review_picture(bot: LeagueBot, picture: Mapping[str, Any]) -> "Path":
    """Draw one picture of a season review to a file, and give where it is.

    A lineup is the image module's, drawn obtaining each seated driver's missing portrait first,
    so a review is not judged on a placeholder the league will not see, and, mid-season, with its
    uncommitted drivers in it. A calendar is core's, of the division's rounds as they stand when it
    is drawn. A picture the renderer will not draw raises, with the renderer's message.
    """
    kind, division_id = str(picture["kind"]), int(picture["division_id"])
    if kind == "lineup":
        lineup = await bot.image_drawing.draw_review_lineup(
            await league_guild(bot), division_id,
            include_uncommitted=bool(picture.get("include_uncommitted")),
        )
        if lineup.path is None:
            raise RuntimeError(lineup.problem or "the lineup could not be drawn")
        return lineup.path
    from leaguebot.core.services.rounds import calendar_post_service

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
    from leaguebot.core.services.seasons.season_review_changes import await_from_worker

    def drawing(picture: Mapping[str, Any]) -> "Path":
        return await_from_worker(_draw_review_picture(bot, picture))

    return drawing


def _approve_view_for(bot: LeagueBot) -> "Callable[[dict[str, Any]], discord.ui.View]":
    """The factory of a review question's view, handed to `season.review.post`: the view of the
    review the payload names (its ``view``: ``approve``, ``mid_season`` or ``configuration``),
    handed the services it reads as the bot carries them and the queue's clock. It finds and
    builds no cog, so a review's buttons are served whether or not the season cog is loaded."""
    from leaguebot.core.services.seasons.season_review_views import (
        ApproveView,
        ConfirmConfigurationView,
        ConfirmMidSeasonPlacementsView,
    )
    families = {
        "approve": ApproveView,
        "mid_season": ConfirmMidSeasonPlacementsView,
        "configuration": ConfirmConfigurationView,
    }

    def make(payload: dict[str, Any]) -> discord.ui.View:
        return families[str(payload.get("view", "approve"))](
            int(payload["reviewer_id"]),
            season_service=bot.season_service,
            module_service=bot.module_service,
            signup_module_service=bot.signup_module_service,
            config_service=bot.config_service,
            team_service=bot.team_service,
            db_path=bot.db_path,
            image_drawing=bot.image_drawing,
            change_queue=bot.change_queue,
            scheduler_service=bot.scheduler_service,
            output_router=bot.output_router,
            round_windows_read=bot.round_windows_read,
            confirmation_refused=bot.confirmation_refused,
            driver_names_read=bot.driver_names_read,
            division_channel_faults_read=bot.division_channel_faults_read,
            fingerprint_areas_read=bot.fingerprint_areas_read,
            now=bot.change_queue.now,
        )

    return make


def register_change_types(bot: LeagueBot) -> None:
    """Make the queue known every kind of change the bot carries out.

    The builder is where modules meet: it hands each change type what it needs of another module,
    so that core's queue and cogs import none of them. The tests' support module calls this to get
    the real set.
    """
    from leaguebot.core.services.rounds.calendar_post_service import calendar_sync_change
    from leaguebot.core.services.cancellations.cancellation_changes import (
        division_cancel_change,
        round_cancel_change,
        season_cancel_change,
    )
    from leaguebot.core.services.settings.bot_init_change import bot_init_change
    from leaguebot.core.services.rounds.division_add_change import division_add_change
    from leaguebot.core.services.rounds.division_delete_change import division_delete_change
    from leaguebot.core.services.rounds.division_duplicate_change import division_duplicate_change
    from leaguebot.core.services.rounds.division_edit_changes import (
        division_amend_change,
        division_rename_change,
    )
    from leaguebot.core.services.drivers.team_changes import (
        team_add_change,
        team_modify_change,
        team_remove_change,
        team_reserve_role_change,
    )
    from leaguebot.core.services.drivers.driver_changes import (
        driver_assign_change,
        driver_move_change,
        driver_reassign_change,
        driver_reject_change,
        driver_release_change,
        driver_sack_change,
        driver_unassign_change,
    )
    from leaguebot.core.services.rounds.round_add_changes import round_add_change, round_import_change
    from leaguebot.core.services.rounds.round_delete_change import round_delete_change
    from leaguebot.core.services.seasons.season_setup_change import season_setup_change
    from leaguebot.core.services.rounds.setup_round_amend_change import setup_round_amend_change
    from leaguebot.core.services.settings.bot_settings import (
        admin_role_setting,
        base_role_setting,
        driver_role_setting,
        hub_channel_setting,
        interaction_channel_setting,
        interaction_role_setting,
        log_channel_setting,
    )
    from leaguebot.core.services.posting.channel_tell_change import channel_tell_change
    from leaguebot.core.services.settings.division_settings import division_channel_settings
    from leaguebot.core.services.test_mode.test_mode_settings import test_mode_nationality_setting
    from leaguebot.core.services.test_mode.test_mode_former_driver_change import (
        test_mode_former_driver_change,
    )
    from leaguebot.core.services.test_mode.test_roster_changes import (
        test_roster_add_bulk_change,
        test_roster_add_change,
        test_roster_clear_change,
        test_roster_remove_change,
    )
    from leaguebot.core.services.test_mode.test_mode_toggle_change import test_mode_toggle_change
    from leaguebot.core.services.upkeep.clean_change import clean_change
    from leaguebot.core.services.modules.hub_service import hub_refresh_change
    from leaguebot.core.services.drivers.lineup_posting import lineup_post_change
    from leaguebot.core.services.rounds.round_amend_change import (
        amendment_in_hand,
        round_amend_change,
        round_rearm_change,
    )
    from leaguebot.core.services.seasons.configuration_confirm_change import (
        configuration_confirm_change,
    )
    from leaguebot.core.services.seasons.mid_season_confirm_change import mid_season_confirm_change
    from leaguebot.core.services.seasons.season_approval_change import (
        season_approval_change,
        season_approval_tell_change,
    )
    from leaguebot.core.services.seasons import season_review_changes
    from leaguebot.core.services.seasons.season_review_changes import season_review_take_down_change
    from leaguebot.core.services.seasons.season_end_changes import season_abort_change, season_complete_change
    from leaguebot.core.services.seasons.season_lifecycle_service import (
        steps_of_the_modules,
        wind_down_change,
    )
    from leaguebot.image.services.image_render_service import discard_render
    from leaguebot.results.services.points.season_points_service import snapshot_configs_to_season_on
    from leaguebot.core.services.queue.setting_change import setting_change

    bot.change_queue.register(hub_refresh_change(
        bot.change_queue, bot.config_service, hub_options=bot.hub_options
    ))
    bot.change_queue.register(calendar_sync_change(bot.season_service, bot.image_drawing))
    bot.change_queue.register(
        lineup_post_change(bot.lineup_poster, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        driver_move_change(
            placement=bot.placement_service, roles=bot.team_role_service,
            lineups=bot.lineup_poster, now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        driver_sack_change(
            placement=bot.placement_service, roles=bot.team_role_service,
            lineups=bot.lineup_poster, now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        driver_release_change(
            placement=bot.placement_service, roles=bot.team_role_service,
            lineups=bot.lineup_poster, now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        driver_assign_change(placement=bot.placement_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        driver_unassign_change(placement=bot.placement_service, now=lambda: bot.change_queue.now())
    )
    bot.change_queue.register(
        driver_reject_change(
            roles=bot.team_role_service, lineups=bot.lineup_poster,
            driver_turned_down=bot.driver_turned_down,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        driver_reassign_change(
            driver=bot.driver_service,
            placement=bot.placement_service,
            roles=bot.team_role_service,
            profiles_merged=bot.profiles_merged,
            divisions_raced_read=bot.divisions_raced_read,
            driver_account_changed=bot.driver_account_changed,
            driver_account_changed_steps=bot.driver_account_changed_steps,
            drawing=bot.image_drawing,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        team_add_change(
            teams=bot.team_service, roles=bot.team_role_service,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        team_remove_change(teams=bot.team_service, roles=bot.team_role_service)
    )
    bot.change_queue.register(
        team_modify_change(
            teams=bot.team_service, roles=bot.team_role_service,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        team_reserve_role_change(
            roles=bot.team_role_service, now=lambda: bot.change_queue.now()
        )
    )
    bot.change_queue.register(
        wind_down_change(
            roles=bot.team_role_service,
            season_end_closes=bot.season_end_closes,
            drivers_returned=bot.drivers_returned,
            driver_turned_down=bot.driver_turned_down,
            driver_returned_jobs=bot.driver_returned_jobs,
            season_end_steps=bot.season_end_steps,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        season_complete_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            roles=bot.team_role_service,
            final_round_read=bot.final_round_read,
            season_standing_read=bot.season_standing_read,
            season_completed=bot.season_completed,
            season_completed_steps=bot.season_completed_steps,
            season_end_refused=bot.season_end_refused,
            season_end_closes=bot.season_end_closes,
            drivers_returned=bot.drivers_returned,
            driver_turned_down=bot.driver_turned_down,
            driver_returned_jobs=bot.driver_returned_jobs,
            season_end_steps=bot.season_end_steps,
            test_mode_flushed=bot.test_mode_flushed,
            drivers_deleted=bot.drivers_deleted,
            profiles_deleted=bot.profiles_deleted,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        season_cancel_change(
            seasons=bot.season_service,
            scheduler=bot.scheduler_service,
            roles=bot.team_role_service,
            drawing=bot.image_drawing,
            cancellation_refused=bot.cancellation_refused,
            rounds_called_off=bot.rounds_called_off,
            cancellation_notices=bot.cancellation_notices,
            rounds_made_final=bot.rounds_made_final,
            cancellation_steps=bot.cancellation_steps,
            season_end_refused=bot.season_end_refused,
            season_standing_read=bot.season_standing_read,
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
            roles=bot.team_role_service,
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
            now=lambda: bot.change_queue.now(),
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
            lineups=bot.lineup_poster,
            roles=bot.team_role_service,
            drawing=bot.image_drawing,
            round_windows_read=bot.round_windows_read,
            placements_confirmed=bot.placements_confirmed,
            placements_confirmed_steps=bot.placements_confirmed_steps,
            division_channel_faults_read=bot.division_channel_faults_read,
            fingerprint_areas_read=bot.fingerprint_areas_read,
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
            lineups=bot.lineup_poster,
            roles=bot.team_role_service,
            drawing=bot.image_drawing,
            rasteriser_fault=bot.image_drawing.rasteriser_fault,
            driver_names_read=bot.driver_names_read,
            division_channel_faults_read=bot.division_channel_faults_read,
            fingerprint_areas_read=bot.fingerprint_areas_read,
            now=lambda: bot.change_queue.now(),
        )
    )
    bot.change_queue.register(
        configuration_confirm_change(
            drawing=bot.image_drawing,
            modules=bot.module_service,
            signup_module=bot.signup_module_service,
            configs=bot.config_service,
            teams=bot.team_service,
            configuration_confirmed=bot.configuration_confirmed,
            confirmation_refused=bot.confirmation_refused,
            fingerprint_areas_read=bot.fingerprint_areas_read,
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
            scheduler=bot.scheduler_service, now=lambda: bot.change_queue.now(),
            discard=discard_render,
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
    bot.change_queue.register(test_mode_former_driver_change())
    bot.change_queue.register(
        test_roster_add_change(capacity=lambda: bot.template_capacity_service)
    )
    bot.change_queue.register(
        test_roster_add_bulk_change(capacity=lambda: bot.template_capacity_service)
    )
    bot.change_queue.register(
        test_roster_remove_change(profiles_deleted=bot.profiles_deleted)
    )
    bot.change_queue.register(
        test_roster_clear_change(profiles_deleted=bot.profiles_deleted)
    )
    for setting in (
        log_channel_setting(config=bot.config_service, channel_uses_read=bot.channel_uses_read),
        interaction_channel_setting(
            config=bot.config_service, channel_uses_read=bot.channel_uses_read
        ),
        interaction_role_setting(config=bot.config_service),
        admin_role_setting(config=bot.config_service),
        base_role_setting(
            config=bot.config_service, roles=bot.team_role_service,
            base_role_changed=bot.base_role_changed,
        ),
        driver_role_setting(config=bot.config_service, roles=bot.team_role_service),
        hub_channel_setting(config=bot.config_service, channel_uses_read=bot.channel_uses_read),
        *division_channel_settings(
            seasons=bot.season_service, channel_uses_read=bot.channel_uses_read
        ),
        test_mode_nationality_setting(config=bot.config_service),
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
            phases_drawn_read=bot.phases_drawn_read,
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
            phases_drawn_read=bot.phases_drawn_read,
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
    attendance_wiring.register_change_types(bot)
    signup_wiring.register_change_types(bot)
    weather_wiring.register_change_types(bot)
    image_wiring.register_change_types(bot)
    results_wiring.register_change_types(
        bot,
        attendance=bot.attendance_after_review,
        attendance_off_on=bot.attendance_service.switch_off_on,
    )


def register_scheduler_callbacks(bot: LeagueBot) -> None:
    """Sign up the handler of every kind of job the scheduler fires, through `register_timer`:
    the weather phases, the forecast clean-up, the result submission, the four check-in jobs, the
    portrait refresh, the amendment sweep and the signup close; and, beside the handler of each
    kind of a round's job, the step `/test-mode advance` runs for it, signed up on the bot's
    `advance_steps`.

    Each asks the change queue. Weather's handlers (`weather/wiring.py`) hold the queue they were
    handed there (`timer_callbacks`); the others read it from the bot when they fire. The bot keeps
    the one queue `build` made, so in the running bot the two come to the same; a test that
    replaces the queue must register these handlers again. Called by :func:`build`, so that every
    kind is registered before the bot starts and before the scheduler does.
    """
    weather_wiring.register_scheduler_callbacks(bot)

    results_wiring.register_scheduler_callbacks(bot)

    attendance_wiring.register_scheduler_callbacks(bot)

    image_wiring.register_scheduler_callbacks(bot)

    signup_wiring.register_scheduler_callbacks(bot)


async def build(settings: Settings) -> LeagueBot:
    """Make the bot and every service, each handed what it needs and complete once built, and
    return it, nothing started.

    Registers every change type, timer kind and scheduler callback, loads the cogs and the
    persistent views, and adds the owner's `!sync` command and the disconnect log line, before
    the bot connects and before the scheduler starts, so a press on a persistent button arriving
    while start-up is still recovering finds all of it in place. Reads no environment: the
    settings are handed in (:func:`read_settings`).
    """
    from leaguebot.core.services.settings.config_service import ConfigService
    from leaguebot.core.services.seasons.season_service import SeasonService
    from leaguebot.core.services.scheduler_service import SchedulerService
    from leaguebot.core.services.posting.output_router import OutputRouter

    bot = create_bot()
    bot.db_path = settings.db_path

    # The version is read once, here, and never when a command runs: it belongs to
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

    # Services are attached to bot for cog access
    from leaguebot.core.services.drivers.driver_service import DriverService
    from leaguebot.core.services.drivers.team_service import TeamService

    bot.config_service = ConfigService(settings.db_path)
    bot.season_service = SeasonService(settings.db_path)
    bot.scheduler_service = SchedulerService(
        settings.db_path, settings.jobstore_path
    )
    bot.output_router = OutputRouter(
        bot, bot.config_service, retry_db_path=settings.db_path,
        now=lambda: bot.change_queue.now(),
    )
    # The queue is handed the router, and is started by start-up, after the recoveries.
    from leaguebot.core.services.queue.change_queue import ChangeQueue

    bot.change_queue = ChangeQueue(settings.db_path, bot, bot.output_router, bot.config_service)
    # The moment the bot came up, by the queue's clock, recorded here once. The wiring hands that
    # one value, as a callable, to a module's handler of a timed job and to its start-up catch-up,
    # so both tell a start missed while the bot was down from one met while it is up.
    bot.came_up_at = bot.change_queue.now()
    bot.driver_service = DriverService(settings.db_path)
    bot.team_service = TeamService(settings.db_path)

    from leaguebot.core.services.modules.hub_service import HubOptions
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.image.services.image_config_service import ImageConfigService
    from leaguebot.image.services.image_validity_service import ImageValidityService

    bot.module_service = ModuleService(settings.db_path)
    register_module_switches(bot.module_service)
    bot.hub_options = HubOptions()
    register_hub_options(bot.hub_options)
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
        db_path=settings.db_path, bot=bot,
    )

    from leaguebot.core.services.drivers.lineup_posting import LineupPoster
    from leaguebot.core.services.drivers.placement_service import PlacementService
    from leaguebot.core.services.drivers.team_role_service import TeamRoleService
    from leaguebot.core.services.drivers.template_capacity_service import TemplateCapacityService
    # The drawing is handed so the capacity guards and the lineup poster can ask image's answer
    # without a bot of their own: neither posts but through the guild it is given, and each
    # reaches the image module only through this drawing. The placement service keeps the seats
    # alone, handed the capacity and the role services it asks.
    bot.team_role_service = TeamRoleService(settings.db_path)
    bot.template_capacity_service = TemplateCapacityService(
        settings.db_path, drawing=bot.image_drawing
    )
    bot.lineup_poster = LineupPoster(settings.db_path, drawing=bot.image_drawing)
    bot.placement_service = PlacementService(
        settings.db_path, capacity=bot.template_capacity_service, roles=bot.team_role_service
    )

    from leaguebot.signup.services.signup_module_service import SignupModuleService
    from leaguebot.signup.services.wizard_service import WizardService
    from leaguebot.attendance.services.attendance_service import AttendanceService

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
    # through, the team-role service and the lineup poster; results' change types reach
    # attendance through it alone.
    bot.attendance_after_review = AttendanceAfterReview(
        bot, bot.placement_service, roles=bot.team_role_service, lineups=bot.lineup_poster
    )
    _make_hooks(bot)
    _sign_up_to_round_windows(bot)
    _sign_up_to_review_sections(bot)
    bot.configuration_faults = _make_configuration_faults(bot)
    _sign_up_to_amendments(bot)

    _sign_up_to_cancellations(bot)
    _sign_up_to_division_channel_faults_read(bot)
    _sign_up_to_division_lines_read(bot)
    _sign_up_to_channel_uses_read(bot)
    _sign_up_to_setting_channels_read(bot)
    _sign_up_to_fingerprint_areas_read(bot)
    _sign_up_to_pending_steps_read(bot)
    _sign_up_to_review_marks_read(bot)
    _sign_up_to_placements_confirmed(bot)
    _sign_up_to_season_completed(bot)
    _sign_up_to_season_end(bot)
    _sign_up_to_deletes(bot)
    _sign_up_to_pack(bot)
    attendance_wiring.sign_up(bot)
    results_wiring.sign_up(bot)
    signup_wiring.sign_up(bot)
    weather_wiring.sign_up(bot)
    image_wiring.sign_up(bot)
    register_change_types(bot)
    from leaguebot.core.services.test_mode.test_mode_service import AdvanceSteps

    bot.advance_steps = AdvanceSteps()
    register_scheduler_callbacks(bot)

    # --- Load Cogs ---
    from leaguebot.core.cogs.bot_cog import BotCog
    from leaguebot.core.cogs.season_cog import SeasonCog
    from leaguebot.core.cogs.division_cog import DivisionCog
    from leaguebot.core.cogs.round_cog import RoundCog
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
    await bot.add_cog(DivisionCog(bot))
    await bot.add_cog(RoundCog(bot))
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
    bot.in_memory_state_cleared = Hook()
    results_wiring.sign_up_to_in_memory_state_cleared(bot)
    signup_wiring.sign_up_to_in_memory_state_cleared(bot)

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
    from leaguebot.results.services.review.penalty_review_views import PenaltyReviewView, ApprovalView
    from leaguebot.results.services.review.appeals_review_views import AppealsReviewView
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

    _sign_up_to_starting(bot)

    return bot


def _sign_up_to_starting(bot: LeagueBot) -> None:
    """Make the hook the start-up declares, and sign core's steps and each module's up to it: what
    `start_up` sweeps once, each step contained (architecture.md, "Timed work and restarts").

    Core's own steps come first, in the order they run: the season-review prompts a previous run
    left standing, then the hub. Then each module's steps, the modules in the order the core
    specification lists them (signup, results, attendance, weather, image), each under its switch
    key, the name its switch reader is signed up under (`images` for the image module); a module
    that is off is passed over in its turn. The order of a module's own steps is the module's. The
    scheduler is started paused before the first and resumed after the last, so no scheduled job
    runs while these do; each recovery is looked up when its step runs, so a step stands for that
    function and no other.
    """

    async def recover_the_review_prompts() -> None:
        # Any season-review approve button left standing by a previous run.
        await recover_expired_review_prompts(
            bot, approve_view=_approve_view_for(bot), queue=bot.change_queue)

    async def recover_the_hub() -> None:
        # Route the hub's buttons again, and post its panel afresh where it was deleted.
        from leaguebot.core.services.modules.hub_service import recover_hub

        await recover_hub(bot, queue=bot.change_queue, hub_options=bot.hub_options)

    bot.starting = StartingHook()
    bot.starting.subscribe(recover_the_review_prompts, name="expired review prompts")
    bot.starting.subscribe(recover_the_hub, name="hub")
    bot.starting.subscribe(partial(signup_wiring.start_up_steps, bot), module="signup")
    bot.starting.subscribe(partial(results_wiring.start_up_steps, bot), module="results")
    bot.starting.subscribe(partial(attendance_wiring.start_up_steps, bot), module="attendance")
    bot.starting.subscribe(partial(weather_wiring.start_up_steps, bot), module="weather")
    bot.starting.subscribe(partial(image_wiring.start_up_steps, bot), module="images")


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
    from leaguebot.core.services.upkeep.backup_service import apply_staged_restore

    if apply_staged_restore(settings.db_path, settings.jobstore_path):
        log.info("Started on a restored database.")

    # Applied before the bot is built and connects, so every service meets the finished schema.
    await run_migrations(settings.db_path)

    bot = await build(settings)

    # Held from here, before the bot connects: whatever a member or Discord asks of the queue
    # before the start-up's sweep has asked its own waits, so that it cannot land ahead of a
    # recovery asked a moment later. `start_up` releases the hold (`ChangeQueue.hold`).
    bot.change_queue.hold()

    @bot.event
    async def on_ready() -> None:
        # `on_ready` fires once the bot has logged in, which is when it has a user.
        assert bot.user is not None
        log.info("Logged in as %s (id=%s)", bot.user, bot.user.id)

        # Start-up runs once per process, the sweep over the steps `build` signed up; a second ready, after a network
        # drop, runs nothing again and syncs nothing (architecture.md, "Start-up runs once, in
        # one function").
        if not await start_up(
            starting=bot.starting,
            modules=bot.module_service,
            change_queue=bot.change_queue,
            scheduler=bot.scheduler_service,
            post_log=bot.output_router.post_log,
        ):
            return

        # Sync slash commands globally (may take up to 1 hour to propagate)
        try:
            synced = await bot.tree.sync()
            log.info("Synced %d slash command(s)", len(synced))
        except discord.HTTPException as exc:
            log.error("Failed to sync slash commands: %s", exc)
            await bot.output_router.post_log(start_up_failure_line("slash command sync", exc))

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


if __name__ == "__main__":
    asyncio.run(main())
