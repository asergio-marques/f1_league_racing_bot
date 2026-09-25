"""The module-level job functions APScheduler actually calls, and why they never raise.

Issue #208. `scheduler_service` has eight of these and they were uncovered. They are
module-level and not methods for a stated reason — `SQLAlchemyJobStore` pickles what it
schedules, and a closure over the service cannot be pickled — so each one reaches the service
through a module global and each has to cope with that global, or its callback, being absent.

**A job that fires with nothing behind it skips; it does not raise.** These are called by
APScheduler, not by the bot, and an exception inside one is logged by the scheduler and
otherwise swallowed: the round quietly loses its forecast, its check-in notice or its submission
prompt, and nobody finds out until the session. Skipping is not tidiness, it is the difference
between a silent failure and a logged one. Both absences — no service and no callback — are
tested for every job, because a restart produces the first and an unregistered module produces
the second, and they are both ordinary rather than exceptional states.

**A callback that is registered is called with the id the job was scheduled for.** That is the
one thing these functions do, and getting it wrong sends a division's forecast to another
division's round.

**A mystery round has a phase 1 and nothing else.** `_weather_phase_job` is a single dispatcher
for every format: a mystery round's phase 1 is a notice that the track is secret, and its phases
2 and 3 are deliberate no-ops because there is nothing to forecast for a track nobody knows. The
no-op is tested explicitly — a reader who does not know it is intentional has no way to tell it
from a missing branch.

**A round that has disappeared is skipped.** A job outlives the round it was scheduled for when
a season is deleted or a round cancelled with the job store still holding it, so the format
lookup finding nothing is an ordinary consequence rather than a broken state.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import leaguebot.core.services.scheduler_service as scheduler_service  # noqa: E402
from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 10908
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21

#: job function, the attribute holding its callback, and the argument it is called with.
ROUND_JOBS = [
    ("_forecast_cleanup_job", "_forecast_cleanup_callback"),
    ("_result_submission_job_wrapper", "_result_submission_callback"),
    ("_rsvp_notice_job", "_rsvp_notice_callback"),
    ("_rsvp_last_notice_job", "_rsvp_last_notice_callback"),
    ("_rsvp_deadline_job", "_rsvp_deadline_callback"),
    ("_rsvp_cleanup_job", "_rsvp_cleanup_callback"),
]
ALL_JOBS = ROUND_JOBS

#: Jobs of the league itself, called with nothing: one bot serves one league (issue #244).
LEAGUE_JOBS = [
    ("_signup_close_timer_job", "_signup_close_callback"),
    ("_portrait_refresh_job", "_portrait_refresh_callback"),
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_leaked_global():
    """The global is process-wide, so a test that sets it must put it back — otherwise the
    next test in the file reads the previous one's service."""
    before = scheduler_service._GLOBAL_SERVICE
    yield
    scheduler_service._GLOBAL_SERVICE = before


async def _make_db(tmp_path, *, name: str = "scheduler_jobs", fmt: str = "NORMAL") -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 1, '2026-02-01T18:00:00+00:00', ?)",
            (ROUND_ID, DIVISION_ID, fmt),
        )
        await db.commit()
    return db_path


class _Service:
    """Only what the job functions reach for — a real `SchedulerService` would start one."""

    def __init__(self, db_path: str = "/tmp/not-read.db") -> None:
        self._db_path = db_path
        self._phase_callbacks: dict[int, AsyncMock] = {}
        self._mystery_notice_callback = None
        self._forecast_cleanup_callback = None
        self._result_submission_callback = None
        self._rsvp_notice_callback = None
        self._rsvp_last_notice_callback = None
        self._rsvp_deadline_callback = None
        self._signup_close_callback = None
        self._portrait_refresh_callback = None


def _install(service) -> None:
    scheduler_service._GLOBAL_SERVICE = service


def _job(name: str):
    return getattr(scheduler_service, name)


# ---------------------------------------------------------------------------
# Nothing behind the job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("job_name,_cb", ALL_JOBS)
async def test_a_job_that_fires_before_the_service_exists_skips(job_name, _cb):
    """A restart produces exactly this: the job store is durable and rearms jobs before the
    service that serves them is registered. Raising here is logged by APScheduler and
    otherwise swallowed, so the round would lose its notice with nothing to show for it."""
    _install(None)

    await _job(job_name)(ROUND_ID)  # must not raise


@pytest.mark.parametrize("job_name,callback_attr", ALL_JOBS)
async def test_a_job_with_no_callback_registered_skips(job_name, callback_attr):
    """An unregistered callback is a module that is switched off, which is ordinary — and
    the job store still holds jobs from when it was on."""
    service = _Service()
    setattr(service, callback_attr, None)
    _install(service)

    await _job(job_name)(ROUND_ID)  # must not raise


@pytest.mark.parametrize("job_name,callback_attr", ALL_JOBS)
async def test_a_registered_callback_is_called_with_the_id(job_name, callback_attr):
    """The one thing these functions do. Getting it wrong sends a division's forecast to
    another division's round."""
    service = _Service()
    callback = AsyncMock()
    setattr(service, callback_attr, callback)
    _install(service)

    await _job(job_name)(4242)

    callback.assert_awaited_once_with(4242)


@pytest.mark.parametrize("job_name,_cb", LEAGUE_JOBS)
async def test_a_league_job_that_fires_before_the_service_exists_skips(job_name, _cb):
    _install(None)

    await _job(job_name)()  # must not raise


@pytest.mark.parametrize("job_name,callback_attr", LEAGUE_JOBS)
async def test_a_league_job_with_no_callback_registered_skips(job_name, callback_attr):
    service = _Service()
    setattr(service, callback_attr, None)
    _install(service)

    await _job(job_name)()  # must not raise


@pytest.mark.parametrize("job_name,callback_attr", LEAGUE_JOBS)
async def test_a_league_job_calls_its_callback_with_nothing(job_name, callback_attr):
    service = _Service()
    callback = AsyncMock()
    setattr(service, callback_attr, callback)
    _install(service)

    await _job(job_name)()

    callback.assert_awaited_once_with()


# ---------------------------------------------------------------------------
# The weather dispatcher
# ---------------------------------------------------------------------------


async def test_a_weather_phase_job_before_the_service_exists_skips(tmp_path):
    _install(None)

    await scheduler_service._weather_phase_job(1, ROUND_ID)  # must not raise


@pytest.mark.parametrize("phase", [1, 2, 3])
async def test_each_phase_reaches_its_own_callback(tmp_path, phase):
    """One dispatcher serves all three, and the phase number is the only thing that tells
    them apart — a phase 3 forecast posted where phase 1 was due is a week early."""
    db_path = await _make_db(tmp_path, name=f"phase_{phase}")
    service = _Service(db_path)
    callbacks = {n: AsyncMock() for n in (1, 2, 3)}
    service._phase_callbacks = callbacks
    _install(service)

    await scheduler_service._weather_phase_job(phase, ROUND_ID)

    callbacks[phase].assert_awaited_once_with(ROUND_ID)
    for other in (1, 2, 3):
        if other != phase:
            callbacks[other].assert_not_awaited()


async def test_a_phase_with_no_callback_registered_skips(tmp_path):
    """The weather module being off is exactly this, and the job store outlives the
    toggle."""
    db_path = await _make_db(tmp_path, name="phase_nocb")
    service = _Service(db_path)
    _install(service)

    await scheduler_service._weather_phase_job(1, ROUND_ID)  # must not raise


async def test_a_round_that_no_longer_exists_skips(tmp_path):
    """A job outlives its round when a season is deleted or a round cancelled with the job
    store still holding it."""
    db_path = await _make_db(tmp_path, name="phase_noround")
    service = _Service(db_path)
    service._phase_callbacks = {1: AsyncMock()}
    _install(service)

    await scheduler_service._weather_phase_job(1, 9999)

    service._phase_callbacks[1].assert_not_awaited()


# ---------------------------------------------------------------------------
# Mystery rounds
# ---------------------------------------------------------------------------


async def test_a_mystery_rounds_phase_one_is_a_notice_not_a_forecast(tmp_path):
    """There is no track to forecast; what a division gets instead is the notice that the
    track is secret."""
    db_path = await _make_db(tmp_path, name="mystery_p1", fmt="MYSTERY")
    service = _Service(db_path)
    service._mystery_notice_callback = AsyncMock()
    service._phase_callbacks = {1: AsyncMock()}
    _install(service)

    await scheduler_service._weather_phase_job(1, ROUND_ID)

    service._mystery_notice_callback.assert_awaited_once_with(ROUND_ID)
    service._phase_callbacks[1].assert_not_awaited()


@pytest.mark.parametrize("phase", [2, 3])
async def test_a_mystery_rounds_later_phases_are_deliberate_no_ops(tmp_path, phase):
    """Tested explicitly because a reader who does not know it is intentional has no way to
    tell it from a missing branch — and "fixing" it would post forecasts for a track the
    division is not supposed to know."""
    db_path = await _make_db(tmp_path, name=f"mystery_p{phase}", fmt="MYSTERY")
    service = _Service(db_path)
    service._mystery_notice_callback = AsyncMock()
    service._phase_callbacks = {2: AsyncMock(), 3: AsyncMock()}
    _install(service)

    await scheduler_service._weather_phase_job(phase, ROUND_ID)

    service._mystery_notice_callback.assert_not_awaited()
    service._phase_callbacks[phase].assert_not_awaited()


async def test_a_mystery_round_with_no_notice_callback_skips(tmp_path):
    db_path = await _make_db(tmp_path, name="mystery_nocb", fmt="MYSTERY")
    service = _Service(db_path)
    _install(service)

    await scheduler_service._weather_phase_job(1, ROUND_ID)  # must not raise


@pytest.mark.parametrize("fmt", ["NORMAL", "SPRINT", "ENDURANCE"])
async def test_every_other_format_takes_the_forecast_path(tmp_path, fmt):
    """Only MYSTERY diverts, and a format added to the bot must keep forecasting rather
    than silently falling into the mystery branch."""
    db_path = await _make_db(tmp_path, name=f"format_{fmt}", fmt=fmt)
    service = _Service(db_path)
    service._phase_callbacks = {1: AsyncMock()}
    service._mystery_notice_callback = AsyncMock()
    _install(service)

    await scheduler_service._weather_phase_job(1, ROUND_ID)

    service._phase_callbacks[1].assert_awaited_once_with(ROUND_ID)
    service._mystery_notice_callback.assert_not_awaited()
