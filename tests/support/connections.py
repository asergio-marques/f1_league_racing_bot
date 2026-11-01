"""Closing the connections the bot keeps idle to lend again, between tests (#540).

`get_connection` keeps a few connections idle once they are given back cleanly, and lends them to
the next caller (`leaguebot.core.db.database.close_connections`). Each holds a thread of
aiosqlite's that is not a daemon, and its database file open. Left to the next test, one would be
waited on by `stop_started_queues` for its whole grace period, would hold a test's `tmp_path`
database open where Windows refuses to delete it, and would carry one test's database into the next
test's connections. So `tests/conftest.py` closes them around every test, and
`stop_started_queues` before it waits on the threads a test left.

They are closed on an event loop of their own, never on the test's: the one pytest-asyncio gives a
test may be closed already, or not yet open, at each point this runs. aiosqlite binds no
connection to a loop, so a connection opened on the test's loop closes on this one.

**One loop for the whole run, on a thread of its own, never one a call** (#540). The closing runs
three times a test or more, and a loop made each time cost the Windows runner its ephemeral ports:
a Windows event loop makes its wake-up pipe from a pair of loopback TCP sockets, each closed one
holds its port for two minutes, and at a third of the way through the suite every new loop waited
in `socket.accept()` for a port to come free, so the run crawled at a test every thirty seconds
until it timed out. Linux's pipe is a Unix socket, and never showed it. So one daemon thread runs
one loop for the life of the process, started the first time anything is to be closed, and each
call hands it the closing and waits. That also serves a caller already running a loop of its own,
as an `async def` test calling `stop_started_queues` is, where a second loop could not run on the
calling thread. Where nothing is kept idle, nothing is handed over at all.
"""
from __future__ import annotations

import asyncio
import sys
import threading

_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _closing_loop() -> asyncio.AbstractEventLoop:
    """The one loop the closing runs on, started on a daemon thread the first time it is wanted."""
    global _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(
                target=loop.run_forever, name="close-lent-connections", daemon=True
            ).start()
            _loop = loop
        return _loop


def close_lent_connections() -> None:
    """Close every connection the bot keeps idle to lend again, and wait for their threads.

    Nothing is imported where nothing has imported the database module, and nothing is done
    where it keeps no connections and is closing none.
    """
    database = sys.modules.get("leaguebot.core.db.database")
    close = getattr(database, "close_connections", None)
    if close is None:
        return
    if not any(database._idle.values()) and not database._closing:
        return
    asyncio.run_coroutine_threadsafe(close(), _closing_loop()).result()
