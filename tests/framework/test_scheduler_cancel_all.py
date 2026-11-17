"""`SchedulerService.cancel_all` — every job at once, for pack and factory reset (#247).

The job kept is a standing one armed straight into the store: which job a module keeps, and how
it arms it, is the module's (#544), and the scheduler names none.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from leaguebot.framework.services.jobs.scheduler import SchedulerService

#: A standing job's id, kept through the clearing as a module asks.
KEPT_JOB_ID = "standing_daily"


def _dispose(service: SchedulerService) -> None:
    """Close the job store's pool, or Windows will not let the file go."""
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    service._scheduler._jobstores["default"].engine.dispose()


async def _noop() -> None:
    return None


def _arm_standing_job(service: SchedulerService) -> None:
    """A job that recurs, as a module's daily work stands in the store."""
    service._scheduler.add_job(_noop, "interval", hours=24, id=KEPT_JOB_ID)


async def test_every_job_goes_but_those_kept(tmp_path):
    service = SchedulerService(str(tmp_path / "bot.db"))
    try:
        service.start()
        later = (datetime.now(timezone.utc) + timedelta(days=3)).replace(tzinfo=None)
        service.arm_once("signup.close_timer", 1, later.replace(tzinfo=timezone.utc))
        _arm_standing_job(service)
        service._scheduler.add_job(
            _noop, "date", run_date=later.replace(tzinfo=timezone.utc), id="wizard_inactivity_1"
        )

        removed = service.cancel_all(keep=frozenset({KEPT_JOB_ID}))

        assert removed == 2
        assert [job.id for job in service._scheduler.get_jobs()] == [KEPT_JOB_ID]
    finally:
        _dispose(service)


async def test_nothing_kept_leaves_no_job(tmp_path):
    service = SchedulerService(str(tmp_path / "bot.db"))
    try:
        service.start()
        later = (datetime.now(timezone.utc) + timedelta(days=3)).replace(tzinfo=None)
        service.arm_once("signup.close_timer", 1, later.replace(tzinfo=timezone.utc))
        _arm_standing_job(service)

        assert service.cancel_all() == 2
        assert service._scheduler.get_jobs() == []
        assert service._scheduler.get_job("once:signup.close_timer:1") is None
    finally:
        _dispose(service)
