"""Removing a scheduled job: "already gone" is the aim, and any other fault is kept (#442).

Every cancellation the scheduler service offers is a clean-up. A job that has already fired, or
was never armed, is the ordinary case, and APScheduler says so with `JobLookupError`: that is
passed over in silence. Anything else — a job store that cannot be read or written — is a real
fault, and it is logged as a warning with its traceback. Neither raises, because the callers are
teardown lists where one job that will not go must not stop the rest.

`cancel_all` counts only the jobs that actually went.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from apscheduler.jobstores.base import JobLookupError

from leaguebot.core.services.scheduler_service import SchedulerService

LOGGER = "leaguebot.core.services.scheduler_service"

#: Each cancel method, and the arguments it is called with. A job the store lists carries the
#: round id and the season-end prefix, so every method that walks the store finds it.
CANCELS = [
    ("cancel_job", ("wizard_inactivity_101",)),
    ("cancel_all", ()),
    ("cancel_round", (5,)),
    ("cancel_season_end", ()),
    ("cancel_portrait_refresh", ()),
    ("cancel_signup_close_timer", ()),
]


def _service(jobs) -> SchedulerService:
    """A scheduler service whose APScheduler is stubbed, listing *jobs*."""
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = MagicMock()
    service._scheduler.get_jobs = MagicMock(return_value=list(jobs))
    return service


def _job(job_id: str):
    return SimpleNamespace(id=job_id, kwargs={"round_id": 5})


@pytest.mark.parametrize("cancel, args", CANCELS, ids=[name for name, _ in CANCELS])
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


@pytest.mark.parametrize("cancel, args", CANCELS, ids=[name for name, _ in CANCELS])
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


def test_a_job_stands_while_the_store_holds_it():
    """`has_job` answers whether a job is armed and not yet fired: the store holds
    `wizard_channel_delete_101` and not `wizard_channel_delete_102`. The finish of a signup
    window's close a stop cut off reads it to pass over a channel already held."""
    service = _service([])
    held = _job("wizard_channel_delete_101")
    service._scheduler.get_job = MagicMock(
        side_effect=lambda job_id: held if job_id == held.id else None
    )

    assert service.has_job("wizard_channel_delete_101") is True
    assert service.has_job("wizard_channel_delete_102") is False
