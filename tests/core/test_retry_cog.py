"""The background worker that retries failed posts.

Issue #208, where it was one of two small core cogs at under half coverage. The other, the
reset command, was withdrawn with issue #247 and its tests with it.

**`RetryCog` exists because Discord fails transiently** (Constitution VII). Its loop must
survive a database it cannot read and a message it cannot deliver — a worker that died on the
first failure would stop retrying everything else queued behind it, which is the one thing it
is for.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.cogs.retry_cog import RetryCog  # noqa: E402


# ---------------------------------------------------------------------------
# RetryCog
# ---------------------------------------------------------------------------


def _retry_cog():
    """A `RetryCog` built without starting its loop.

    `__init__` calls `retry_loop.start()`, which needs a running scheduler and would leave
    a live task behind after the test; the loop body is what is under test.
    """
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    cog = RetryCog.__new__(RetryCog)
    cog._bot = bot
    return cog


def _pending(entries, error=None):
    return patch(
        "leaguebot.core.cogs.retry_cog.get_all_pending",
        new=AsyncMock(side_effect=error, return_value=None if error else entries),
    )


async def test_every_queued_message_is_retried():
    """Constitution VII: messages that fail to post are retried until delivered."""
    cog = _retry_cog()
    entries = [MagicMock(), MagicMock(), MagicMock()]

    with _pending(entries), patch(
        "leaguebot.core.cogs.retry_cog.attempt_delivery", new=AsyncMock(return_value=None)
    ) as attempt:
        await cog.retry_loop()

    assert attempt.await_count == 3


async def test_an_empty_queue_does_nothing():
    """The ordinary case, every five minutes."""
    cog = _retry_cog()

    with _pending([]), patch(
        "leaguebot.core.cogs.retry_cog.attempt_delivery", new=AsyncMock(return_value=None)
    ) as attempt:
        await cog.retry_loop()

    attempt.assert_not_awaited()


async def test_a_database_it_cannot_read_does_not_kill_the_worker(caplog):
    """The loop is long-lived. An exception escaping here would stop the task for good,
    and nothing would retry anything again until the bot was restarted."""
    cog = _retry_cog()

    with _pending(None, error=RuntimeError("database is locked")), caplog.at_level("ERROR"):
        await cog.retry_loop()

    assert "failed to load pending messages" in caplog.text
