"""The suite's wait for database threads after a test is held to the threads that test started.

`tests/conftest.py` stops the change queues a test started and waits for the aiosqlite threads a
stopped worker left running, so that none answers a closed event loop against a later test
(`tests/support/change_queue.py`, `stop_started_queues`). A connection a fixture opened in set-up
keeps its thread alive until the fixture's teardown, which runs after that wait. Waited for, it
would never finish in time, and every test holding one would pay the whole grace period: about
eighty seconds a full run, from `tests/core/test_track_service.py` alone, when this was found.
"""

from __future__ import annotations

import asyncio
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


async def test_the_idle_connections_are_closed_on_one_loop_for_the_whole_run(
    tmp_path, lending, monkeypatch
):
    """Closing the idle connections around every test builds no event loop each time (#540). A
    Windows loop makes its wake-up pipe from loopback TCP sockets, and three loops a test ran the
    runner out of ephemeral ports a third of the way through the suite. So once the closing has
    its loop, closing again builds none, and still ends every idle connection's thread; with
    nothing idle, nothing is handed to it at all."""
    from tests.support.connections import close_lent_connections

    db_path = os.path.join(str(tmp_path), "lent.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as first:
        pass
    close_lent_connections()  # the closing has its loop from here on, whatever ran before

    def no_new_loop():
        raise AssertionError("a new event loop was built to close the idle connections")

    monkeypatch.setattr(asyncio, "new_event_loop", no_new_loop)
    monkeypatch.setattr(asyncio.events, "new_event_loop", no_new_loop)  # how `asyncio.run` builds one
    async with get_connection(db_path) as second:
        pass
    assert second._thread.is_alive(), "precondition: the connection is kept idle to be lent again"

    close_lent_connections()
    for idle in (first, second):
        idle._thread.join(timeout=5)  # aiosqlite answers the close before its thread exits
        assert not idle._thread.is_alive()

    def handed_over(*_args, **_kwargs):
        raise AssertionError("the closing was handed to its loop with nothing idle")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", handed_over)
    close_lent_connections()
