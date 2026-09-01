"""The scheduler's job store lives in its own file, apart from the league database.

Why it was moved: `SQLAlchemyJobStore` is synchronous and is attached to an
`AsyncIOScheduler`, so its writes land **on the event-loop thread**. Pointed at the league
database they contended with the hundreds of `aiosqlite` readers using the same file, and
the stall reached the HTTP send that answers a Discord interaction — which is how a
`/images test lineup` autocomplete came back as `404 Unknown interaction`.

Every test here tears its files down, sidecars included. That is not tidiness: the suite
also runs on `windows-latest`, where a file still held open cannot be deleted, so a job
store left connected fails there and nowhere else.
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.scheduler_service import (
    SchedulerService,
    default_jobstore_path,
    prepare_jobstore,
)


def _dispose(service: SchedulerService) -> None:
    """Close every connection the job store holds, so the file can be deleted.

    SQLAlchemy keeps a pool open behind the job store. On Linux an open handle does not
    stop an unlink, so forgetting this passes locally and fails only on Windows.
    """
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

    def build(jobstore_path: str | None = None) -> SchedulerService:
        service = SchedulerService(db_path, jobstore_path)
        made.append(service)
        return service

    yield db_path, build

    for service in made:
        _dispose(service)
        _remove_database(service.jobstore_path)
    _remove_database(db_path)


def test_the_jobstore_defaults_to_its_own_file_beside_the_database(workspace):
    db_path, build = workspace

    service = build()

    assert service.jobstore_path == default_jobstore_path(db_path)
    assert os.path.basename(service.jobstore_path) == "scheduler.db"
    assert os.path.dirname(service.jobstore_path) == os.path.dirname(
        os.path.abspath(db_path)
    )


def test_league_data_and_the_jobstore_are_different_files(workspace):
    """The whole point of the change, stated as an assertion."""
    db_path, build = workspace

    service = build()

    assert os.path.abspath(service.jobstore_path) != os.path.abspath(db_path)


def test_an_explicit_jobstore_path_is_honoured(workspace, tmp_path):
    _db_path, build = workspace
    chosen = str(tmp_path / "elsewhere" / "jobs.db")

    service = build(chosen)

    assert service.jobstore_path == chosen
    assert os.path.exists(chosen), "the file should be created ready for the job store"


def test_the_jobstore_file_is_in_wal_mode(workspace):
    """Layer 1 only reaches the league database; this file needs WAL in its own right."""
    _db_path, build = workspace

    service = build()

    conn = sqlite3.connect(service.jobstore_path)
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    assert mode.lower() == "wal"


def test_the_jobstore_keeps_full_durability(workspace):
    """FULL here too: a scheduled job lost to a power cut is worse than a slower commit.

    The job store carries pending weather phases, RSVP notices and result submissions, and
    only `/season approve` can rebuild them. Same trade as the league database, declined for
    the same reason on 2026-08-27.
    """
    _db_path, build = workspace
    service = build()

    engine = service._scheduler._jobstores["default"].engine
    with engine.connect() as connection:
        level = connection.exec_driver_sql("PRAGMA synchronous").scalar()

    assert level == 2, "expected FULL (2) on the job store's connections"


def test_preparing_a_jobstore_twice_is_harmless(tmp_path):
    """Startup runs this every time; it must be idempotent."""
    path = str(tmp_path / "scheduler.db")

    prepare_jobstore(path)
    prepare_jobstore(path)

    conn = sqlite3.connect(path)
    try:
        (mode,) = conn.execute("PRAGMA journal_mode").fetchone()
    finally:
        conn.close()
    _remove_database(path)
    assert mode.lower() == "wal"


def test_a_jobstore_in_a_missing_directory_is_created(tmp_path):
    """An explicit SCHEDULER_DB_PATH may name a directory that does not exist yet."""
    path = str(tmp_path / "nested" / "deeper" / "scheduler.db")

    prepare_jobstore(path)

    assert os.path.exists(path)
    _remove_database(path)


# ── The daily driver-portrait refresh ─────────────────────────────────────
#
# The one recurring trigger in this service. Every other job is a one-shot DateTrigger, so
# these pin the departure deliberately: a later reader finding a CronTrigger among them
# should find a test saying it is meant.


def test_the_portrait_refresh_is_a_recurring_daily_trigger(workspace):
    from apscheduler.triggers.cron import CronTrigger

    _db_path, build = workspace
    service = build()

    service.schedule_portrait_refresh(4242, "03:00")

    job = service._scheduler.get_job("pfp_daily_4242")
    assert job is not None
    assert isinstance(job.trigger, CronTrigger)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("3", "0")
    assert job.kwargs == {"server_id": 4242}


def test_the_portrait_refresh_is_scheduled_in_utc(workspace):
    # The league is told the zone when it names the time; a stored local time would need a
    # zone stored with it and would drift against daylight saving twice a year.
    _db_path, build = workspace
    service = build()

    service.schedule_portrait_refresh(4242, "23:30")

    assert str(service._scheduler.get_job("pfp_daily_4242").trigger.timezone) == "UTC"


async def test_naming_a_new_time_re_arms_rather_than_duplicating(workspace):
    """Started, because a stopped scheduler queues `add_job` and applies
    `replace_existing` only when it flushes -- so a stopped one shows two pending jobs
    under one id and would pass this test for the wrong reason."""
    from services import scheduler_service as m

    _db_path, build = workspace
    service = build()
    service.start()
    try:
        service.schedule_portrait_refresh(4242, "03:00")
        service.schedule_portrait_refresh(4242, "07:45")

        jobs = [j for j in service._scheduler.get_jobs() if j.id.startswith("pfp_daily_")]
        assert len(jobs) == 1
        fields = {f.name: str(f) for f in jobs[0].trigger.fields}
        assert (fields["hour"], fields["minute"]) == ("7", "45")
    finally:
        m._GLOBAL_SERVICE = None


def test_cancelling_the_portrait_refresh_removes_it_and_tolerates_a_second_call(workspace):
    _db_path, build = workspace
    service = build()
    service.schedule_portrait_refresh(4242, "03:00")

    service.cancel_portrait_refresh(4242)
    service.cancel_portrait_refresh(4242)  # never scheduled is not an error

    assert service._scheduler.get_job("pfp_daily_4242") is None


async def test_the_job_delegates_to_the_registered_callback(workspace):
    from services import scheduler_service as m

    _db_path, build = workspace
    service = build()
    seen: list[int] = []

    async def _cb(server_id: int) -> None:
        seen.append(server_id)

    service.register_portrait_refresh_callback(_cb)
    m._GLOBAL_SERVICE = service
    try:
        await m._portrait_refresh_job(4242)
    finally:
        m._GLOBAL_SERVICE = None

    assert seen == [4242]


async def test_the_job_is_silent_where_no_callback_was_registered(workspace):
    from services import scheduler_service as m

    _db_path, build = workspace
    service = build()
    m._GLOBAL_SERVICE = service
    try:
        await m._portrait_refresh_job(4242)  # must not raise
    finally:
        m._GLOBAL_SERVICE = None
