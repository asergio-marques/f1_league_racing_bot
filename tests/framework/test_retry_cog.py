"""The background worker that retries failed posts.

Issue #208, where it was one of two small core cogs at under half coverage. The other, the
reset command, was withdrawn with issue #247 and its tests with it.

**`RetryCog` exists because Discord fails transiently** (Constitution VII). Its loop must
survive a database it cannot read and a message it cannot deliver — a worker that died on the
first failure would stop retrying everything else queued behind it, which is the one thing it
is for.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from leaguebot.framework.cogs.retry_cog import RetryCog

#: The change queue's clock, well before the host's, so that a time read from the host shows.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# RetryCog
# ---------------------------------------------------------------------------


def _retry_cog():
    """A `RetryCog` over a bot double; its loop is not started, only its body is run."""
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    return RetryCog(bot)


def _log_channel(cog):
    """Give the cog's router a log channel that takes every line, and return that channel and the
    router's `post_log`, which a line the worker writes of its own reaches with `channel=` named:
    the path that never queues it (`retry_service._post_own_line`)."""
    channel = MagicMock()
    router = cog._bot.output_router
    router.log_destination = AsyncMock(return_value=channel)
    router.post_log = AsyncMock(return_value=MagicMock())
    return channel, router.post_log


def _pending(entries, error=None):
    return patch(
        "leaguebot.framework.cogs.retry_cog.get_all_pending",
        new=AsyncMock(side_effect=error, return_value=None if error else entries),
    )


async def test_every_queued_message_is_retried():
    """Constitution VII: messages that fail to post are retried until delivered. Each try is
    handed the change queue's clock, the bot's clock, as its "now"."""
    cog = _retry_cog()
    cog._bot.change_queue.now = MagicMock(return_value=NOW)
    entries = [MagicMock(), MagicMock(), MagicMock()]

    with _pending(entries), patch(
        "leaguebot.framework.cogs.retry_cog.attempt_delivery", new=AsyncMock(return_value=None)
    ) as attempt:
        await cog.retry_loop()

    assert attempt.await_args_list == [call(entry, cog._bot, router=cog._bot.output_router, now=NOW) for entry in entries]


async def test_an_empty_queue_does_nothing():
    """The ordinary case, every five minutes."""
    cog = _retry_cog()

    with _pending([]), patch(
        "leaguebot.framework.cogs.retry_cog.attempt_delivery", new=AsyncMock(return_value=None)
    ) as attempt:
        await cog.retry_loop()

    attempt.assert_not_awaited()


async def test_a_database_it_cannot_read_does_not_kill_the_worker(caplog):
    """The loop is long-lived. An exception escaping here would stop the task for good,
    and nothing would retry anything again until the bot was restarted. The kept lines cannot be
    read: the fault reaches the host's log with its traceback, and one line, the timed job's
    failure line for `log line retry`, goes to the log channel by the path that never queues it
    (#453), so a cycle that keeps failing never queues a line of its own."""
    from leaguebot.framework.utils.interaction_errors import job_failure_line

    cog = _retry_cog()
    channel, post_log = _log_channel(cog)
    error = RuntimeError("database is locked")

    with _pending(None, error=error), caplog.at_level("ERROR"):
        await cog.retry_loop()

    assert any(r.exc_info and r.exc_info[1] is error for r in caplog.records)
    post_log.assert_awaited_once_with(job_failure_line("log line retry", error), channel=channel)


@pytest.mark.xfail(strict=True, reason="#584: the failure line still names the exception's class, not the kind of fault")
async def test_one_entry_that_raises_does_not_stop_the_rest_or_the_loop(caplog):
    """Three kept lines, 101, 102 and 103, are due a retry; the database is locked as the first
    two are tried, so trying each of them raises `sqlite3.OperationalError`. The third is still
    tried, each fault reaches the host's log with its traceback, and the loop raises nothing, so
    it runs again in five minutes. One line goes to the log channel for the cycle, by the path
    that never queues it: it names the kind of fault and how many kept lines faulted, never the
    fault's message nor the lines' ids, which the host's log alone holds (#453)."""
    cog = _retry_cog()
    cog._bot.change_queue.now = MagicMock(return_value=NOW)
    channel, post_log = _log_channel(cog)
    entries = [MagicMock(id=101), MagicMock(id=102), MagicMock(id=103)]
    errors = [sqlite3.OperationalError("database is locked") for _ in range(2)]

    with _pending(entries), patch(
        "leaguebot.framework.cogs.retry_cog.attempt_delivery",
        new=AsyncMock(side_effect=[*errors, None]),
    ) as attempt, caplog.at_level("ERROR"):
        await cog.retry_loop()  # must not raise

    assert [c.args[0] for c in attempt.await_args_list] == entries
    logged = [r.exc_info[1] for r in caplog.records if r.exc_info]
    assert all(any(found is error for found in logged) for error in errors)
    assert "101" in caplog.text and "102" in caplog.text
    post_log.assert_awaited_once()
    [line] = post_log.await_args.args
    assert post_log.await_args.kwargs == {"channel": channel}
    assert line.startswith("❌")
    assert "the bot could not read or write its database" in line
    assert "OperationalError" not in line
    assert "2 kept lines" in line
    assert "database is locked" not in line
    assert "101" not in line and "102" not in line
