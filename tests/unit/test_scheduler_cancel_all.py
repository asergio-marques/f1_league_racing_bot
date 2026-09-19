"""`SchedulerService.cancel_all` — every job at once, for pack and factory reset (#247)."""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.scheduler_service import (  # noqa: E402
    PORTRAIT_REFRESH_JOB_ID,
    SIGNUP_CLOSE_JOB_ID,
    SchedulerService,
)


def _dispose(service: SchedulerService) -> None:
    """Close the job store's pool, or Windows will not let the file go."""
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    service._scheduler._jobstores["default"].engine.dispose()


async def _noop() -> None:
    return None


async def test_every_job_goes_but_those_kept(tmp_path):
    service = SchedulerService(str(tmp_path / "bot.db"))
    try:
        service.start()
        later = (datetime.now(timezone.utc) + timedelta(days=3)).replace(tzinfo=None)
        service.schedule_signup_close_timer(later.isoformat())
        service.schedule_portrait_refresh("03:00")
        service._scheduler.add_job(
            _noop, "date", run_date=later.replace(tzinfo=timezone.utc), id="wizard_inactivity_1"
        )

        removed = service.cancel_all(keep=frozenset({PORTRAIT_REFRESH_JOB_ID}))

        assert removed == 2
        assert [job.id for job in service._scheduler.get_jobs()] == [PORTRAIT_REFRESH_JOB_ID]
    finally:
        _dispose(service)


async def test_nothing_kept_leaves_no_job(tmp_path):
    service = SchedulerService(str(tmp_path / "bot.db"))
    try:
        service.start()
        later = (datetime.now(timezone.utc) + timedelta(days=3)).replace(tzinfo=None)
        service.schedule_signup_close_timer(later.isoformat())
        service.schedule_portrait_refresh("03:00")

        assert service.cancel_all() == 2
        assert service._scheduler.get_jobs() == []
        assert service._scheduler.get_job(SIGNUP_CLOSE_JOB_ID) is None
    finally:
        _dispose(service)
