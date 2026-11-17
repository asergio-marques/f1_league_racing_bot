"""The scheduler's lifecycle: starting it, pausing it, shutting it down, removing a job.

Split from `tests/core/test_round_jobs_scheduling.py` (#544), whose round scheduling
stayed in core.

**The service registers itself as the global only when it starts.** Constructing a second
service — a test, a reset — must not redirect jobs already firing at the running one.

**Cancelling something that is not scheduled is not an error.** A job that has already fired,
or was never armed, is the ordinary case for every cancellation; that is pinned, for each of
them, in `tests/core/test_round_jobs_cancels.py`.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import leaguebot.core.services.scheduler_service as scheduler_module
from leaguebot.core.services.scheduler_service import SchedulerService


@pytest.fixture(autouse=True)
def _restore_global():
    before = scheduler_module._GLOBAL_SERVICE
    yield
    scheduler_module._GLOBAL_SERVICE = before


def _service():
    service = SchedulerService.__new__(SchedulerService)
    service._db_path = "/tmp/not-read.db"
    service._jobstore_path = "/tmp/not-read-jobs.db"
    service._scheduler = MagicMock()
    service._scheduler.running = False
    return service



# ---------------------------------------------------------------------------
# Starting
# ---------------------------------------------------------------------------


def test_starting_makes_the_service_the_one_jobs_reach():
    service = _service()

    service.start()

    assert scheduler_module._GLOBAL_SERVICE is service
    service._scheduler.start.assert_called_once()


def test_constructing_a_service_does_not_redirect_running_jobs(tmp_path):
    """Only `start` sets the global — a second service built for a reset or a test must not
    take jobs from the one already running."""
    running = _service()
    running.start()

    second = SchedulerService(str(tmp_path / "bot.db"))
    try:
        assert scheduler_module._GLOBAL_SERVICE is running
    finally:
        # Released explicitly: Windows will not remove a SQLite file an engine still holds.
        second._scheduler._jobstores["default"].engine.dispose()


def test_paused_pauses_and_resumes_the_scheduler():
    """A running scheduler is paused for the block and resumed after it, and resumed too where
    the block raises, the error going on to the caller."""
    service = _service()
    service._scheduler.running = True

    with service.paused():
        service._scheduler.pause.assert_called_once_with()
        service._scheduler.resume.assert_not_called()
    service._scheduler.resume.assert_called_once_with()

    with pytest.raises(OSError, match="the disk is full"):
        with service.paused():
            raise OSError("the disk is full")
    assert service._scheduler.pause.call_count == 2
    assert service._scheduler.resume.call_count == 2


def test_a_stopped_scheduler_is_neither_paused_nor_resumed():
    """Resuming a scheduler nobody paused would start one the bot deliberately left down."""
    service = _service()

    with service.paused():
        pass

    service._scheduler.pause.assert_not_called()
    service._scheduler.resume.assert_not_called()


def test_starting_a_running_scheduler_does_not_start_it_again():
    service = _service()
    service._scheduler.running = True

    service.start()

    service._scheduler.start.assert_not_called()


def test_shutting_down_stops_a_running_scheduler():
    service = _service()
    service._scheduler.running = True

    service.shutdown(wait=False)

    service._scheduler.shutdown.assert_called_once_with(wait=False)


def test_shutting_down_a_stopped_scheduler_does_nothing():
    service = _service()

    service.shutdown()

    service._scheduler.shutdown.assert_not_called()


# ---------------------------------------------------------------------------
# Removing a job
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cancel,args,job_id",
    [
        ("cancel_job", ("weather_p1_x",), "weather_p1_x"),
    ],
)
def test_a_cancellation_removes_its_job(cancel, args, job_id):
    service = _service()

    getattr(service, cancel)(*args)

    service._scheduler.remove_job.assert_called_once_with(job_id)
