"""Image's daily driver-portrait refresh, armed and cancelled by image's portrait services (#544).

Image arms the refresh through the framework's scheduler, by its generic daily call, under its own
job id (`pfp_daily`) and kind (`portrait_refresh`); the scheduler names neither.

The one recurring trigger among the bot's jobs besides the amendment sweep. Every round's job is a
one-shot DateTrigger, so these pin the departure deliberately: a later reader finding a CronTrigger
should find a test saying it is meant.

Cancelling is a clean-up, as every cancel of a job is (#442): a refresh already gone is passed over
in silence, and any other fault is logged as a warning with its traceback, and neither raises.
These were cases of the scheduler's cancel tests in `tests/core/test_scheduler_cancels.py`.

Every test here tears its files down, sidecars included: the suite also runs on `windows-latest`,
where a job store left connected cannot be deleted.
"""

from __future__ import annotations

import logging
import os
from unittest.mock import MagicMock

import pytest

from leaguebot.core.services import scheduler_service
from leaguebot.core.services.scheduler_service import SchedulerService

LOGGER = scheduler_service.__name__

#: Why each test here fails until image arms its own refresh.
NO_PORTRAIT_JOB = "#544: image has no portrait refresh of its own; the scheduler still arms it"


def _image_job():
    """Image's arming and cancelling of its daily refresh, each handed the scheduler."""
    from leaguebot.image.services.portraits.driver_portrait_service import (
        cancel_portrait_refresh,
        schedule_portrait_refresh,
    )

    return schedule_portrait_refresh, cancel_portrait_refresh


def _dispose(service: SchedulerService) -> None:
    """Close every connection the job store holds, so the file can be deleted."""
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        service._scheduler._jobstores["default"].engine.dispose()
    except Exception:
        pass


def _remove_database(path: str) -> None:
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(path + suffix)
        except OSError:
            pass


@pytest.fixture
def workspace(tmp_path):
    """A league database path and a teardown that leaves nothing behind."""
    db_path = str(tmp_path / "bot.db")
    made: list[SchedulerService] = []

    def build() -> SchedulerService:
        service = SchedulerService(db_path)
        made.append(service)
        return service

    yield db_path, build

    for service in made:
        _dispose(service)
        _remove_database(service.jobstore_path)
    _remove_database(db_path)


@pytest.mark.xfail(strict=True, reason=NO_PORTRAIT_JOB)
def test_the_portrait_refresh_is_a_recurring_daily_trigger(workspace):
    from apscheduler.triggers.cron import CronTrigger

    schedule_portrait_refresh, _cancel = _image_job()
    _db_path, build = workspace
    service = build()

    schedule_portrait_refresh(service, "03:00")

    job = service._scheduler.get_job("pfp_daily")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("3", "0")
    assert job.func is scheduler_service._one_off_timer_job
    assert job.kwargs in ({"kind": "portrait_refresh"}, {"kind": "portrait_refresh", "key": None})


@pytest.mark.xfail(strict=True, reason=NO_PORTRAIT_JOB)
def test_the_portrait_refresh_is_scheduled_in_utc(workspace):
    # The league is told the zone when it names the time; a stored local time would need a
    # zone stored with it and would drift against daylight saving twice a year.
    schedule_portrait_refresh, _cancel = _image_job()
    _db_path, build = workspace
    service = build()

    schedule_portrait_refresh(service, "23:30")

    assert str(service._scheduler.get_job("pfp_daily").trigger.timezone) == "UTC"


@pytest.mark.xfail(strict=True, reason=NO_PORTRAIT_JOB)
async def test_naming_a_new_time_re_arms_rather_than_duplicating(workspace):
    """Started, because a stopped scheduler queues `add_job` and applies
    `replace_existing` only when it flushes -- so a stopped one shows two pending jobs
    under one id and would pass this test for the wrong reason."""
    from leaguebot.core.services import scheduler_service as m

    schedule_portrait_refresh, _cancel = _image_job()
    _db_path, build = workspace
    service = build()
    service.start()
    try:
        schedule_portrait_refresh(service, "03:00")
        schedule_portrait_refresh(service, "07:45")

        jobs = [j for j in service._scheduler.get_jobs() if j.id == "pfp_daily"]
        assert len(jobs) == 1
        fields = {f.name: str(f) for f in jobs[0].trigger.fields}
        assert (fields["hour"], fields["minute"]) == ("7", "45")
    finally:
        m._GLOBAL_SERVICE = None


@pytest.mark.xfail(strict=True, reason=NO_PORTRAIT_JOB)
def test_cancelling_the_portrait_refresh_removes_it_and_tolerates_a_second_call(
    workspace, caplog
):
    """The refresh is armed for 03:00 and cancelled twice: it is gone, and the second cancel,
    finding it already gone, neither raises nor logs a warning."""
    schedule_portrait_refresh, cancel_portrait_refresh = _image_job()
    _db_path, build = workspace
    service = build()
    schedule_portrait_refresh(service, "03:00")

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        cancel_portrait_refresh(service)
        cancel_portrait_refresh(service)  # never scheduled is not an error

    assert service._scheduler.get_job("pfp_daily") is None
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


@pytest.mark.xfail(strict=True, reason=NO_PORTRAIT_JOB)
def test_cancelling_the_refresh_logs_the_traceback_of_any_other_failure(caplog):
    """The job store cannot be written when the refresh is cancelled: a warning carrying the
    error and its traceback is logged, and the cancel still does not raise."""
    _schedule, cancel_portrait_refresh = _image_job()
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = MagicMock()
    error = RuntimeError("the job store is locked")
    service._scheduler.remove_job = MagicMock(side_effect=error)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        cancel_portrait_refresh(service)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "the fault was not logged"
    assert any(r.exc_info and r.exc_info[1] is error for r in warnings)
