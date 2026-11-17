"""The one runner APScheduler calls for every job the scheduler arms, and why it never raises.

Issue #208, and #283's one runner. The runner, `_one_off_timer_job(kind, key)`, is module-level
and not a method for a stated reason — `SQLAlchemyJobStore` pickles what it schedules, and a
closure over the service cannot be pickled — so it reaches the service through a module global,
and has to cope with that global, or the kind's handler, being absent.

**A job that fires with nothing behind it skips; it does not raise.** The runner is called by
APScheduler, not by the bot, and an exception inside it is logged by the scheduler and otherwise
swallowed: the round quietly loses its forecast, its check-in notice or its submission prompt,
and nobody finds out until the session. Skipping is not tidiness, it is the difference between
a silent failure and a logged one. Both absences — no service and no handler — are tested for
every kind the builder signs up, because a restart produces the first and an unregistered module
produces the second, and they are both ordinary rather than exceptional states.

That a registered handler is called with the key its job was armed for, its kind's alone, and
that a handler which raises is logged with its traceback, are pinned for every kind as its job is
armed, in `test_scheduler_one_off.py`. What a mystery round's phases ask is weather's to decide,
and is pinned in `tests/weather/test_forecast_changes.py`.
"""
from __future__ import annotations

import logging

import pytest

import leaguebot.core.services.scheduler_service as scheduler_service
from leaguebot.core.services.scheduler_service import SchedulerService

ROUND_ID = 21

#: Every kind the builder signs up for the scheduler's own jobs, with the key its job is armed
#: with: a round's id, or None for a standing job, whose handler is handed nothing.
EVERY_KIND = [
    ("weather_p1", ROUND_ID),
    ("weather_p2", ROUND_ID),
    ("weather_p3", ROUND_ID),
    ("cleanup", ROUND_ID),
    ("results", ROUND_ID),
    ("rsvp_notice", ROUND_ID),
    ("rsvp_last_notice", ROUND_ID),
    ("rsvp_deadline", ROUND_ID),
    ("rsvp_cleanup", ROUND_ID),
    ("portrait_refresh", None),
    ("amendment_sweep", None),
]


@pytest.fixture(autouse=True)
def _no_leaked_global():
    """The global is process-wide, so a test that sets it must put it back — otherwise the
    next test in the file reads the previous one's service."""
    before = scheduler_service._GLOBAL_SERVICE
    yield
    scheduler_service._GLOBAL_SERVICE = before


@pytest.mark.parametrize("kind,key", EVERY_KIND)
async def test_a_job_that_fires_before_the_service_exists_skips(caplog, kind, key):
    """A restart produces exactly this: the job store is durable and rearms jobs before the
    service that serves them is registered. Raising here is logged by APScheduler and
    otherwise swallowed, so the round would lose its notice with nothing to show for it. The
    skip is logged as a warning naming the kind, so that it is never silent."""
    scheduler_service._GLOBAL_SERVICE = None

    with caplog.at_level(logging.WARNING, logger=scheduler_service.__name__):
        await scheduler_service._one_off_timer_job(kind, key)  # must not raise

    assert any(
        r.levelno == logging.WARNING and kind in r.getMessage() for r in caplog.records
    ), "the skip was not logged"


@pytest.mark.parametrize("kind,key", EVERY_KIND)
async def test_a_job_with_no_callback_registered_skips(caplog, kind, key):
    """An unregistered handler is a module that is switched off, which is ordinary — and the
    job store still holds jobs from when it was on. The skip is logged as a warning naming the
    kind, so that it is never silent."""
    service = SchedulerService.__new__(SchedulerService)
    service._timer_callbacks = {}
    scheduler_service._GLOBAL_SERVICE = service

    with caplog.at_level(logging.WARNING, logger=scheduler_service.__name__):
        await scheduler_service._one_off_timer_job(kind, key)  # must not raise

    assert any(
        r.levelno == logging.WARNING and kind in r.getMessage() for r in caplog.records
    ), "the skip was not logged"
