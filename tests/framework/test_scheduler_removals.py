"""Removing a scheduled job: "already gone" is the aim, and any other fault is kept (#442).

Split from `tests/core/test_round_jobs_cancels.py` (#544), whose round cancels stayed in core with
core's round jobs. These are the scheduler's own removals: one job by its id, every job, and a
one-off timer by its kind and key.

A job that has already fired, or was never armed, is the ordinary case, and APScheduler says so
with `JobLookupError`: that is passed over in silence. Anything else — a job store that cannot be
read or written — is a real fault, and it is logged as a warning with its traceback. Neither
raises, because the callers are teardown lists where one job that will not go must not stop the
rest.

`cancel_all` counts only the jobs that actually went.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from apscheduler.jobstores.base import JobLookupError

from leaguebot.framework.services.jobs.scheduler import SchedulerService

LOGGER = "leaguebot.framework.services.jobs.scheduler"

#: Each of the scheduler's removals and the arguments it is called with.
REMOVALS = [
    ("cancel_job", ("wizard_inactivity_101",)),
    ("cancel_all", ()),
    # A wizard's timer, which the signup switch-off disarms whether or not it has fired.
    ("disarm", ("signup_inactivity", "101")),
]


def _service(jobs) -> SchedulerService:
    """A scheduler service whose APScheduler is stubbed, listing *jobs*."""
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = MagicMock()
    service._scheduler.get_jobs = MagicMock(return_value=list(jobs))
    return service


def _job(job_id: str):
    return SimpleNamespace(id=job_id, kwargs={"kind": "results", "key": 5}, next_run_time=None)


@pytest.mark.parametrize("cancel, args", REMOVALS, ids=[name for name, _ in REMOVALS])
def test_a_cancel_passes_over_a_job_already_gone(caplog, cancel, args):
    """Fired or never armed: nothing raises and nothing is logged. `cancel_all`, given two
    jobs of which one is already gone, counts only the one that went."""
    jobs = [_job("season_end_gone"), _job("season_end_kept")]
    service = _service(jobs if cancel == "cancel_all" else jobs[:1])

    def _remove(job_id):
        if cancel == "cancel_all" and job_id == "season_end_kept":
            return None
        raise JobLookupError(job_id)

    service._scheduler.remove_job = MagicMock(side_effect=_remove)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = getattr(service, cancel)(*args)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    if cancel == "cancel_all":
        assert result == 1


@pytest.mark.parametrize("cancel, args", REMOVALS, ids=[name for name, _ in REMOVALS])
def test_a_cancel_logs_the_traceback_of_any_other_failure(caplog, cancel, args):
    """A job store that cannot be written is a real fault: a warning carrying the error and
    its traceback, and still no raise. `cancel_all` goes on to the next job and counts it."""
    jobs = [_job("season_end_broken"), _job("season_end_kept")]
    service = _service(jobs if cancel == "cancel_all" else jobs[:1])
    error = RuntimeError("the job store is locked")

    def _remove(job_id):
        if cancel == "cancel_all" and job_id == "season_end_kept":
            return None
        raise error

    service._scheduler.remove_job = MagicMock(side_effect=_remove)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = getattr(service, cancel)(*args)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "the fault was not logged"
    assert any(r.exc_info and r.exc_info[1] is error for r in warnings)
    if cancel == "cancel_all":
        assert result == 1


async def test_a_job_stands_while_the_store_holds_it(tmp_path, monkeypatch):
    """`has_job` answers whether a job is armed and not yet fired, read of a real job store: the
    signup close timer, armed a day ahead, stands until it is disarmed, and a job never armed
    does not."""
    from datetime import datetime, timedelta, timezone

    from leaguebot.framework.db.database import run_migrations
    from leaguebot.framework.services.jobs import scheduler as scheduler_service

    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    monkeypatch.setattr(scheduler_service, "_GLOBAL_SERVICE", None)
    service = SchedulerService(db_path, str(tmp_path / "jobs.db"), report=AsyncMock())
    service.start()
    try:
        fire_at = datetime.now(timezone.utc) + timedelta(days=1)
        service.arm_once("signup.close_timer", 1, fire_at)

        assert service.has_job("once:signup.close_timer:1") is True
        assert service.has_job("wizard_channel_delete_102") is False

        service.disarm("signup.close_timer", 1)
        assert service.has_job("once:signup.close_timer:1") is False
    finally:
        service._scheduler.shutdown(wait=False)
        service._scheduler._jobstores["default"].engine.dispose()
