"""The suite's wait for database threads after a test is held to the threads that test started.

`tests/conftest.py` stops the change queues a test started and waits for the aiosqlite threads a
stopped worker left running, so that none answers a closed event loop against a later test
(`tests/support/change_queue.py`, `stop_started_queues`). A connection a fixture opened in set-up
keeps its thread alive until the fixture's teardown, which runs after that wait. Waited for, it
would never finish in time, and every test holding one would pay the whole grace period: about
eighty seconds a full run, from `tests/core/test_track_service.py` alone, when this was found.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support import change_queue


@pytest.fixture()
def short_grace(monkeypatch: pytest.MonkeyPatch) -> float:
    """A grace period short enough to measure, long enough to tell a wait from none."""
    monkeypatch.setattr(change_queue, "THREAD_GRACE_SECONDS", 1.0)
    return 1.0


async def test_stop_started_queues_waits_on_no_thread_a_fixture_holds(tmp_path, short_grace):
    """A database thread alive before the test's call began is not waited for; the same thread,
    not named as earlier, is waited for to the end of the grace period, which shows the check
    tells the two apart."""
    db_path = os.path.join(str(tmp_path), "teardown.db")
    await run_migrations(db_path)
    async with get_connection(db_path):
        before = set(threading.enumerate())

        start = time.monotonic()
        change_queue.stop_started_queues(started_before=before)
        assert time.monotonic() - start < short_grace / 2

        start = time.monotonic()
        change_queue.stop_started_queues()
        assert time.monotonic() - start >= short_grace * 0.9


@pytest.mark.xfail(strict=True, reason="#540: a connection returned cleanly is not lent again yet")
async def test_stop_started_queues_waits_on_no_idle_lent_connection(tmp_path, short_grace, lending):
    """A connection the test's call gave back cleanly, kept idle to be lent again, is closed before
    the wait rather than waited on: the stop returns well inside the grace period, and the idle
    connection's thread has ended."""
    db_path = os.path.join(str(tmp_path), "teardown.db")
    await run_migrations(db_path)
    before = set(threading.enumerate())
    async with get_connection(db_path) as idle:
        await (await idle.execute("SELECT 1")).fetchall()
    assert idle._thread.is_alive(), "precondition: the connection is kept idle to be lent again"

    start = time.monotonic()
    change_queue.stop_started_queues(started_before=before)

    assert time.monotonic() - start < short_grace / 2
    assert not idle._thread.is_alive()
