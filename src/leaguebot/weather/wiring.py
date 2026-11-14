"""Weather's part of the builder: what the entry point asks of each module as it builds the bot.

It signs weather up to the hooks core declares, registers weather's switch reader, change types and
settings, the handlers of its kinds of timed job with the steps `/test-mode advance` runs for them,
and names the steps its start-up runs, each a function of the forecast recovery service handed the
database's path and the change queue. It is under the entry point's rules, not a
service's (architecture.md, "How the code is laid out"): it reads the bot as the builder does, so
each function below is handed the bot and reads a service off it when it signs up or, where a
lambda reads it, when it runs, which the tests that replace a service on the bot after registering
rely on. It holds no rule of weather's and no database code of its own. Nothing imports it but the
entry point.
"""
from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from leaguebot.core.models.amendment import AmendmentPart
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.hooks import StartUpStep
    from leaguebot.core.utils.league_bot import LeagueBot


def register_switch(modules: "ModuleService") -> None:
    """Sign weather up with `ModuleService` for the reader of its own switch, keyed by the name
    `is_weather_enabled` asks it by."""
    from leaguebot.weather.services.draw_guard import weather_enabled_on

    modules.register_switch("weather", weather_enabled_on)


def sign_up(bot: "LeagueBot") -> None:
    """Sign weather up to each hook only weather joins. The hooks several modules join are signed
    up by the entry point, in the order they are called, through the functions below."""
    sign_up_to_phases_drawn_read(bot)
    sign_up_to_test_mode_flushed(bot)


def sign_up_to_phases_drawn_read(bot: "LeagueBot") -> None:
    """Sign weather's reading of the phases it says are drawn up to the hook a round's phases are
    asked through, as it is judged and again inside the save on its connection, handed the
    database's path."""
    from leaguebot.weather.services import round_phases

    bot.phases_drawn_read.subscribe(partial(round_phases.phases_drawn, bot.db_path))


def sign_up_to_amendment_step_failure(bot: "LeagueBot") -> None:
    """Sign weather's words for a step of its own left undone up to the hook an outcome gives a
    step its words through."""
    from leaguebot.weather.services import forecast_changes

    bot.amendment_step_failure.subscribe(forecast_changes.forecast_failure)


def sign_up_to_test_mode_flushed(bot: "LeagueBot") -> None:
    """Sign weather's clearing of the forecasts test mode posted up to the hook that clears what
    test mode posted: asked on the save's connection, it hands back weather's own job."""
    from leaguebot.weather.services import forecast_changes

    bot.test_mode_flushed.subscribe(forecast_changes.test_mode_flushed_on)


def sign_up_to_round_windows(bot: "LeagueBot") -> None:
    """Sign weather's window up to the hook a round's timing windows read declares. It is handed
    whether the round is being amended and checks its own switch."""
    from leaguebot.weather.services import weather_config_service

    bot.round_windows_read.subscribe(partial(
        weather_config_service.round_window, bot.module_service, bot.db_path
    ))


def sign_up_to_review_sections(bot: "LeagueBot") -> None:
    """Sign weather's section up to the hook a season review gathers its lines through: handed
    which review is gathered, the season and its divisions, it checks its own switch."""
    from leaguebot.weather.services import weather_config_service

    bot.review_sections_read.subscribe(partial(
        weather_config_service.review_section, bot.module_service, bot.db_path
    ))


def sign_up_to_amendment_judged(bot: "LeagueBot") -> None:
    """Sign weather's part of judging an amendment up to the hook a round's amendment is judged
    through, handed the database's path for where the amendment is asked outside a save.

    Whether weather is on is asked of the module switch's reader (`bot.module_service`) as the
    judgement is asked for, a read of its own that holds no connection of the save's, so that
    what the confirmation promises of the redraw is what the switch says."""
    from leaguebot.weather.services import forecast_amendment

    async def judged(*handed: Any, **context: Any) -> AmendmentPart:
        return await forecast_amendment.amendment_judged(
            *handed, db_path=bot.db_path,
            weather_on=await bot.module_service.is_weather_enabled(), **context,
        )

    bot.amendment_judged.subscribe(judged)


def sign_up_to_round_amended(bot: "LeagueBot") -> None:
    """Sign weather's withdrawal of the forecasts an amendment withdraws up to the hook an amended
    round declares, inside the save."""
    from leaguebot.weather.services import phase_withdrawal

    bot.round_amended.subscribe(phase_withdrawal.round_amended_on)


def sign_up_to_round_armed(bot: "LeagueBot") -> None:
    """Sign weather's posts and its forecasts' timers up to the hook an amended round's arming again
    declares."""
    from leaguebot.weather.services import forecast_amendment

    bot.round_armed.subscribe(forecast_amendment.round_armed)


def sign_up_to_round_clean_ups_due(bot: "LeagueBot") -> None:
    """Sign weather's clean-up up to the hook the clean-ups due a day after a round are read
    through."""
    from leaguebot.weather.services import forecast_changes

    bot.round_clean_ups_due.subscribe(forecast_changes.amendment_clean_ups)


def sign_up_to_amendment_steps(bot: "LeagueBot") -> None:
    """Sign weather's steps for an amendment up to the hook the steps each module defines for it
    are read through."""
    from leaguebot.weather.services import forecast_changes

    bot.amendment_steps.subscribe(partial(
        forecast_changes.amendment_steps,
        now=lambda: bot.change_queue.now(), seasons=bot.season_service, modules=bot.module_service,
    ))


def sign_up_to_cancellation_notices(bot: "LeagueBot") -> None:
    """Sign weather's forecast note up to the hook the notices told of a division are read
    through."""
    from leaguebot.weather.services import cancellation_notice

    bot.cancellation_notices.subscribe(cancellation_notice.notices_on)


def sign_up_to_cancellation_steps(bot: "LeagueBot") -> None:
    """Sign weather's notice step up to the hook the steps a cancellation plans are read through."""
    from leaguebot.weather.services import cancellation_notice

    bot.cancellation_steps.subscribe(partial(
        cancellation_notice.notify_forecast_steps,
        modules=bot.module_service,
        seasons=bot.season_service,
    ))


def sign_up_to_deletes(bot: "LeagueBot") -> None:
    """Sign weather's deleting of the forecasts of the rounds deleted up to the hook a round
    deleted declares, on the delete's connection, inside its save."""
    from leaguebot.weather.services import forecast_cleanup_service

    bot.rounds_deleted.subscribe(forecast_cleanup_service.rounds_deleted_on)


def sign_up_to_pack(bot: "LeagueBot") -> None:
    """Sign weather's clearing of its own rows tied to the server the bot leaves up to the hook the
    bot packed declares, on the pack's connection."""
    from leaguebot.weather.services import forecast_cleanup_service

    bot.bot_packed.subscribe(forecast_cleanup_service.bot_packed_on)


def sign_up_to_division_lines_read(bot: "LeagueBot") -> None:
    """Sign weather's line up to the hook the placements review gathers each division's module
    lines through, handed the database's path."""
    from leaguebot.weather.services import forecast_channel

    bot.division_lines_read.subscribe(partial(forecast_channel.division_lines, bot.db_path))


def sign_up_to_channel_uses_read(bot: "LeagueBot") -> None:
    """Sign weather up to the hook a channel being set is asked of each module through, handed the
    database's path."""
    from leaguebot.weather.services import forecast_channel

    bot.channel_uses_read.subscribe(partial(forecast_channel.channel_uses, bot.db_path))


def sign_up_to_setting_channels_read(bot: "LeagueBot") -> None:
    """Sign weather up to the hook the factory reset asks each module through for every channel it
    holds, handed the database's path."""
    from leaguebot.weather.services import forecast_channel

    bot.setting_channels_read.subscribe(partial(forecast_channel.setting_channels, bot.db_path))


def sign_up_to_division_channel_faults_read(bot: "LeagueBot") -> None:
    """Sign weather's lines naming its forecast channel not set or not on the server up to the hook
    a division's channel faults are read through, handed the module service and the database's
    path."""
    from leaguebot.weather.services import forecast_channel

    bot.division_channel_faults_read.subscribe(
        partial(forecast_channel.division_channel_faults, bot.module_service, bot.db_path)
    )


def sign_up_to_fingerprint_areas_read(bot: "LeagueBot") -> None:
    """Sign weather's rows of its own configuration up to the hook a season's fingerprint is taken
    through; it reads its channels by division, as the season service reads them."""
    from leaguebot.weather.services import fingerprint_areas

    bot.fingerprint_areas_read.subscribe(
        partial(fingerprint_areas.fingerprint_areas, bot.season_service)
    )


def sign_up_to_pending_steps_read(bot: "LeagueBot") -> None:
    """Sign weather's pending steps up to the hook `/test-mode advance` reads them through."""
    from leaguebot.weather.services import test_mode_steps

    bot.pending_steps_read.subscribe(test_mode_steps.pending_steps)


def sign_up_to_review_marks_read(bot: "LeagueBot") -> None:
    """Sign weather's marks up to the hook `/test-mode review` reads each module's marks through."""
    from leaguebot.weather.services import test_mode_steps

    bot.review_marks_read.subscribe(test_mode_steps.review_marks)


def sign_up_to_season_end_steps(bot: "LeagueBot") -> None:
    """Sign weather's deletion of a forecast up to the hook the steps a season's end defines are
    read through."""
    from leaguebot.weather.services import forecast_changes

    bot.season_end_steps.subscribe(partial(
        forecast_changes.season_end_steps, now=lambda: bot.change_queue.now()
    ))


def register_change_types(bot: "LeagueBot") -> None:
    """Make the queue known weather's kinds of change: the forecast and its clean-up, the module's
    switch and its two settings."""
    from leaguebot.core.services.queue.setting_change import setting_change
    from leaguebot.weather.services.forecast_changes import (
        forecast_change,
        forecast_clean_up_change,
    )
    from leaguebot.weather.services.module_switch_settings import (
        module_off_weather,
        module_on_weather,
    )
    from leaguebot.weather.services.weather_settings import (
        deadline_setting,
        forecast_channel_setting,
    )

    bot.change_queue.register(forecast_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(forecast_clean_up_change(now=lambda: bot.change_queue.now()))
    bot.change_queue.register(
        setting_change(deadline_setting(modules=bot.module_service, seasons=bot.season_service))
    )
    bot.change_queue.register(
        setting_change(
            forecast_channel_setting(
                modules=bot.module_service,
                seasons=bot.season_service,
                channel_uses_read=bot.channel_uses_read,
            )
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


def register_scheduler_callbacks(bot: "LeagueBot") -> None:
    """Sign up the handlers of weather's kinds of job through `register_timer`, the three phases
    and the clean-up, and beside them the step `/test-mode advance` runs for each, signed up on the
    bot's `advance_steps`.

    Each asks the change queue and holds the queue it was handed here (`timer_callbacks`). The bot
    keeps the one queue `build` made; a test that replaces the queue must register these handlers
    again."""
    from leaguebot.weather.services.forecast_changes import advance_steps, timer_callbacks

    scheduler = bot.scheduler_service
    callbacks = timer_callbacks(bot.change_queue, bot.db_path)
    for phase, handler in callbacks.phases.items():
        scheduler.register_timer(f"weather_p{phase}", handler)
    scheduler.register_timer("cleanup", callbacks.clean_up)
    for kind, advance in advance_steps(bot.change_queue, bot.db_path).items():
        bot.advance_steps.register_step(kind, advance)


def start_up_steps(bot: "LeagueBot") -> "tuple[StartUpStep, ...]":
    """Weather's start-up steps, named and in the order they run in weather's turn of the start-up
    sweep (the entry point signs them up to `bot.starting` under weather's switch key). Each
    recovery is looked up when its step runs, so a step stands for that function and no other, and
    is handed the bot's database path and its change queue, which a service does not look up on
    the bot."""
    from leaguebot.weather.services import forecast_recovery

    return (
        ("missed phases",
         lambda: forecast_recovery.recover_missed_phases(bot.db_path, queue=bot.change_queue)),
        # The forecasts due to come down while the bot was stopped.
        ("missed forecast clean-ups",
         lambda: forecast_recovery.recover_missed_forecast_clean_ups(
             bot.db_path, queue=bot.change_queue)),
    )
