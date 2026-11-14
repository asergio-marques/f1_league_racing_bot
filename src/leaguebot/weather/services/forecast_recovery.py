"""What a restart does about the forecasts: weather's two start-up steps.

The wiring only names these as steps, in the order they run in weather's turn of the start-up sweep
(architecture.md, "A module's wiring"), so it holds neither the reads nor the rules. Each is handed
the database path and the change queue, and judges every moment at the queue's clock
(`ChangeQueue.now`), the bot's clock, so that a test moving it moves every reading. Each asks the
queue for what a stop left undone and posts nothing itself; the queue runs what is asked once it
starts. The sweep passes over a module switched off, so neither reads weather's switch.

- `recover_missed_phases`: the phases, and the notice of a mystery round, whose horizon passed
  while the bot was down for a round whose race is still ahead.
- `recover_missed_forecast_clean_ups`: the forecasts whose clean-up fell due while the bot was
  down.

A timer that fell due while the bot was down fires late when the bot is back, having no lateness
limit, and each ask here meets the timer's on the change's key, so a phase is drawn once and a
forecast is taken down once.

**A round that fails stops none of the others.** Once every round has been gone through, a failure
for any is raised as one `RecoveryFailed` naming each round and its error, which the start-up sweep
reports to the log channel in one line. A database that cannot be read is raised as it is, for the
sweep to report: nothing is caught up, and nothing says so but that line.

Each reads core's tables only through core's readers, and its own, `forecast_messages` and the
phases drawn, here.
"""
from __future__ import annotations

import logging
from datetime import datetime

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import forecasts_named
from leaguebot.core.models.division import DivisionStatus
from leaguebot.core.models.round import RoundStatus, WeatherWindows
from leaguebot.core.services.queue.change_queue import ChangeQueue
from leaguebot.core.services.rounds.round_moments import clean_up_moment, round_event_moments
from leaguebot.core.services.seasons.season_service import (
    round_facts_on,
    rounds_of_the_active_season,
)
from leaguebot.weather.models.recovery_failed import RecoveryFailed
from leaguebot.weather.models.weather_config import WeatherPipelineConfig
from leaguebot.weather.services import forecast_changes, weather_config_service
from leaguebot.weather.services.round_phases import phases_drawn_on

log = logging.getLogger(__name__)

__all__ = ["recover_missed_forecast_clean_ups", "recover_missed_phases"]


async def _why_unreadable(db: aiosqlite.Connection, round_id: int) -> BaseException:
    """The error that reading *round_id* raises, which core's reader of the active season's rounds
    hands back the id of and not the reason, logged with its traceback for the host's log."""
    try:
        await round_facts_on(db, round_id)
    except ValueError as error:
        log.exception("Recovery: round %s cannot be read, its phases are not caught up", round_id)
        return error
    log.error("Recovery: round %s could not be read, its phases are not caught up", round_id)
    return ValueError(f"the start of round {round_id} cannot be read")


async def recover_missed_phases(db_path: str, *, queue: ChangeQueue) -> None:
    """Ask the change queue for any weather phases whose horizon has passed but were not executed.

    Every round is asked for alike, a mystery round included: the mystery rule is weather's
    (`forecast_changes.ask_forecast`), which turns a mystery round's phase 1 into its notice and
    asks nothing for its phases 2 and 3. A notice missed while the bot was down is therefore posted
    on starting, while the round's race is still ahead. A request no longer due is dropped by its
    change's check.

    The horizons are the league's own, read from ``weather_pipeline_config`` and placed by
    `round_event_moments`, not the packaged 5 / 2 / 2. Every other path that decides whether a
    phase is overdue reads that config — the confirmation of placements and a round amendment's
    judgement — and a restart judging by the defaults would make the same league see one set of
    timings on an enable and another on a restart: a longer phase 1 would never be published at
    all, a shorter one would be published days early. The config is resolved once rather than
    once per round, since the league has one.

    Only a round still to be run is recovered: never a cancelled round, a round of a cancelled
    division, a round of a season not live, or a round whose race time has passed
    (`rounds_of_the_active_season`, and the race time judged here at the queue's clock). It is the
    race time rather than the round's status that decides, since a round leaves ``NOT_RUN`` only
    by its result-submission job. The phases a round has drawn are read through weather's own
    `phases_drawn_on`. A live round whose start cannot be read is that round's own failure, as it is
    for the clean-ups: it is recorded, and the rounds that can be read are all asked.
    """
    now = queue.now()
    live, unreadable = await rounds_of_the_active_season(db_path)
    rounds = [each for each in live if each.scheduled_at > now]
    failures: dict[int, BaseException] = {}
    async with get_connection(db_path) as db:
        drawn = await phases_drawn_on(db, [each.id for each in rounds])
        for round_id in unreadable:
            failures[round_id] = await _why_unreadable(db, round_id)
    config: WeatherPipelineConfig | None = None

    for each in rounds:
        if config is None:
            config = await weather_config_service.get_weather_pipeline_config(db_path)
        moments = round_event_moments(
            each.scheduled_at,
            weather=WeatherWindows(config.phase_1_days, config.phase_2_days, config.phase_3_hours),
            attendance=None,
        )
        for phase in (1, 2, 3):
            if drawn[each.id][phase] or now < moments[f"weather_p{phase}"]:
                continue
            log.info("Recovery: firing Phase %d for round %s", phase, each.id)
            try:
                await forecast_changes.ask_forecast(queue, db_path, each.id, phase)
            except Exception as error:
                log.exception("Recovery: Phase %d of round %s could not be asked", phase, each.id)
                failures.setdefault(each.id, error)
    if failures:
        raise RecoveryFailed(failures)


async def recover_missed_forecast_clean_ups(db_path: str, *, queue: ChangeQueue) -> None:
    """Take down the forecasts whose clean-up fell due while the bot was down: weather's part of
    the start-up's missed clean-ups.

    A round's forecast (whichever of Phases 1 to 3 still stands, which the host's log line names)
    comes down `POST_RACE_CLEANUP_DELAY` after the round's start, by the ``cleanup`` job. A job
    falling due while the bot is stopped fires late when the bot is back, having no lateness
    limit, and this ask meets the timer's on the change's key, so the forecast comes down once.

    A round called off, or of a division called off, is left alone. Its cancellation removed the
    job, and the weather module keeps the forecasts of a round called off. A round that ended
    cancelled because every session was entered as not held (`rounds.not_held`) is not left alone:
    its job was never removed. The season's status is not asked, since the live job fires after a
    season completes as well. Running a clean-up a second time finds nothing left to take down.
    The rounds are asked in the order they raced; a round whose start cannot be read fails alone.
    """
    now = queue.now()
    due: list[tuple[datetime, int, list[int]]] = []
    failures: dict[int, BaseException] = {}
    async with get_connection(db_path) as db:
        for round_id, phases in (await _standing_forecasts(db)).items():
            try:
                facts = await round_facts_on(db, round_id)
            except ValueError as error:
                log.exception("recover_missed_forecast_clean_ups: round %d cannot be read", round_id)
                failures[round_id] = error
                continue
            if facts is None or facts.division_status == DivisionStatus.CANCELLED:
                continue
            if facts.status == RoundStatus.CANCELLED and not facts.not_held:
                continue
            if clean_up_moment(facts.scheduled_at) <= now:
                due.append((facts.scheduled_at, round_id, phases))

    for _, round_id, phases in sorted(due, key=lambda each: (each[0], each[1])):
        log.info(
            "recover_missed_forecast_clean_ups: deleting %s of round_id=%d",
            forecasts_named(phases), round_id,
        )
        try:
            await forecast_changes.ask_forecast_clean_up(queue, db_path, round_id)
        except Exception as error:
            log.exception(
                "recover_missed_forecast_clean_ups: forecast cleanup failed for round %d", round_id
            )
            failures[round_id] = error
    if failures:
        raise RecoveryFailed(failures)


async def _standing_forecasts(db: aiosqlite.Connection) -> dict[int, list[int]]:
    """The phases of Phases 1 to 3 whose forecast is recorded as standing, by round id, in round
    and phase order. A mystery round's notice (phase 0) is no forecast the clean-up deletes."""
    cursor = await db.execute(
        "SELECT round_id, phase_number FROM forecast_messages "
        "WHERE phase_number IN (1, 2, 3) ORDER BY round_id, phase_number"
    )
    standing: dict[int, list[int]] = {}
    for row in await cursor.fetchall():
        standing.setdefault(int(row["round_id"]), []).append(int(row["phase_number"]))
    return standing
