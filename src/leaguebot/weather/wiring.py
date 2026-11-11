"""Weather's part of the builder: what the entry point asks of each module as it builds the bot.

It signs weather up to the hooks core declares, registers weather's switch reader, change types and
settings, the handlers of its kinds of timed job with the steps `/test-mode advance` runs for them,
and holds the recoveries its start-up steps run. It is under the entry point's rules, not a
service's (architecture.md, "How the code is laid out"): it reads the bot as the builder does, so
each function below is handed the bot and reads a service off it when it signs up or, where a
lambda reads it, when it runs, which the tests that replace a service on the bot after registering
rely on. It holds no rule of weather's and no database code of its own: the two recoveries that
still read a table here are listed under weather's pass in `test_architecture_rules.py`, to become
handlers of the start-up sweep. Nothing imports it but the entry point.
"""
from __future__ import annotations

import logging
from functools import partial
from typing import TYPE_CHECKING

from leaguebot.core.models.round import read_scheduled_at

if TYPE_CHECKING:
    from leaguebot.core.services.modules.module_service import ModuleService
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


def register_switch(modules: "ModuleService") -> None:
    """Sign weather up with `ModuleService` for the reader of its own switch, keyed by the name
    `is_weather_enabled` asks it by."""
    from leaguebot.weather.services.draw_guard import weather_enabled_on

    modules.register_switch("weather", weather_enabled_on)


def sign_up(bot: "LeagueBot") -> None:
    """Sign weather up to each hook only weather joins. The hooks several modules join are signed
    up by the entry point, in the order they are called, through the functions below."""
    sign_up_to_phases_drawn_read(bot)
    sign_up_to_amendment_step_failure(bot)
    sign_up_to_test_mode_flushed(bot)


def sign_up_to_phases_drawn_read(bot: "LeagueBot") -> None:
    """Sign weather's reading of the phases it says are drawn up to the hook a round's phases are
    asked through, as it is judged and again inside the save on its connection, handed the
    database's path."""
    from leaguebot.weather.services import round_phases

    bot.phases_drawn_read.subscribe(partial(round_phases.phases_drawn, bot.db_path))


def sign_up_to_amendment_step_failure(bot: "LeagueBot") -> None:
    """Sign weather's words for a forecast step left undone up to the hook an outcome gives a step
    its words through."""
    from leaguebot.weather.services import forecast_changes

    bot.amendment_step_failure.subscribe(forecast_changes.forecast_failure)


def sign_up_to_test_mode_flushed(bot: "LeagueBot") -> None:
    """Sign weather's deletion of the forecasts test mode posted up to the hook that clears what
    test mode posted, handed the database's path."""
    from leaguebot.weather.services import forecast_cleanup_service

    bot.test_mode_flushed.subscribe(partial(forecast_cleanup_service.forecast_deletes, bot.db_path))


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


def sign_up_to_round_amended(bot: "LeagueBot") -> None:
    """Sign weather's withdrawal of the forecasts an amendment withdraws up to the hook an amended
    round declares, inside the save."""
    from leaguebot.weather.services import phase_withdrawal

    bot.round_amended.subscribe(phase_withdrawal.round_amended_on)


def sign_up_to_round_armed(bot: "LeagueBot") -> None:
    """Sign weather's posts up to the hook an amended round's arming again declares."""
    from leaguebot.weather.services import forecast_changes

    bot.round_armed.subscribe(forecast_changes.amendment_posts)


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


async def recover_missed_phases(bot: "LeagueBot") -> None:
    """Ask the change queue for any weather phases whose horizon has passed but were not executed.

    Every round is asked for alike, a mystery round included: the mystery rule is weather's
    (`forecast_changes.ask_forecast`), which turns a mystery round's phase 1 into its notice and
    asks nothing for its phases 2 and 3. A notice missed while the bot was down is therefore posted
    on starting, while the round's race is still ahead. A request no longer due is dropped by its
    change's check.

    The horizons are the league's own, read from ``weather_pipeline_config``, not the packaged
    5 / 2 / 2. Every other path that decides whether a phase is overdue reads that config — the
    confirmation of placements and a round amendment's judgement — and a restart judging by the
    defaults would make the same league see one set of timings on an enable and another on a
    restart: a longer phase 1 would never be published at all, a shorter one would be published
    days early.

    The config is resolved once rather than once per round, since the league has one, and only
    after the module gate, so a league with weather switched off is never queried for it.

    Only a round still to be run is recovered: never a cancelled round, a round of a
    cancelled division, or a round whose race time has passed. Cancellation is filtered in the
    query. The race time is judged here, on the aware moment the one reader hands back, and not in
    SQL, which would compare the stored text against a timestamp. It is the race time rather
    than the round's status that decides, since a round leaves ``NOT_RUN`` only by its
    result-submission job, and a job that fell due while the bot was down was discarded by the
    scheduler's misfire grace rather than run.
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
        scheduled_at = read_scheduled_at(scheduled_at_str)

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


async def recover_missed_forecast_clean_ups(bot: "LeagueBot") -> None:
    """Take down the forecasts whose clean-up fell due while the bot was down: weather's part of
    the start-up's missed clean-ups.

    A round's forecast (whichever of Phases 1 to 3 still stands, which the host's log line names)
    comes down `POST_RACE_CLEANUP_DELAY` after the round's start, by the ``cleanup`` job. A job
    falling due while the bot is stopped is discarded by the scheduler's misfire grace rather than
    run late, and nothing else would take those messages down: a forecast whose cleanup was missed
    would stay for good.

    A round called off, or of a division called off, is left alone. Its cancellation removed the
    job, and the weather module keeps the forecasts of a round called off. A round that ended
    cancelled because every session was entered as not held (`rounds.not_held`) is not left alone:
    its job was never removed. The season's status is not asked, since the live job fires after a
    season completes as well. Weather's own gate applies here. Running a cleanup a second time
    finds nothing left to take down.
    """
    from datetime import datetime, timezone

    from leaguebot.core.db.database import get_connection
    from leaguebot.core.models.change import forecasts_named
    from leaguebot.weather.services.forecast_changes import ask_forecast_clean_up
    from leaguebot.core.services.round_moments import POST_RACE_CLEANUP_DELAY

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
