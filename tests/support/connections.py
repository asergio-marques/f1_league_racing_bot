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
connection to a loop, so a connection opened on the test's loop closes on this one. Where the
calling thread is already running a loop, as an `async def` test calling `stop_started_queues` is,
a second loop cannot be run on that thread, so the closing runs on a short-lived thread of its own,
which is joined before this returns.
"""
from __future__ import annotations

import asyncio
import sys
import threading


def close_lent_connections() -> None:
    """Close every connection the bot keeps idle to lend again, and wait for their threads.

    Nothing is imported where nothing has imported the database module, and nothing is done
    where it keeps no connections.
    """
    database = sys.modules.get("leaguebot.core.db.database")
    close = getattr(database, "close_connections", None)
    if close is None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(close())
        finally:
            loop.close()
        return
    failure: list[BaseException] = []

    def _close_on_a_thread_of_its_own() -> None:
        try:
            asyncio.run(close())
        except BaseException as exc:
            failure.append(exc)

    closer = threading.Thread(target=_close_on_a_thread_of_its_own, name="close-lent-connections")
    closer.start()
    closer.join()
    if failure:
        raise failure[0]
