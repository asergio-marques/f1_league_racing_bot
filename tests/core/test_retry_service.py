"""Unit tests for retry_service — Feature 017.

Covers:
  - enqueue: inserts a row with correct fields
  - get_all_pending: returns rows ordered by enqueued_at
  - mark_delivered: deletes the row
  - mark_failed: increments retry_count and sets last_attempted_at
  - RETRY_WARN_THRESHOLD constant value
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.retry_service import (
    RETRY_WARN_THRESHOLD,
    enqueue,
    get_all_pending,
    mark_delivered,
    mark_failed,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path: str) -> str:
    """Run all migrations on a fresh SQLite file and return the path."""
    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    return db_path


async def _row_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM pending_messages")
        row = await cursor.fetchone()
        return row[0]


async def _fetch_all_rows(db_path: str) -> list:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, channel_id, content, failure_reason, "
            "       enqueued_at, retry_count, last_attempted_at "
            "FROM pending_messages ORDER BY enqueued_at ASC"
        )
        return await cursor.fetchall()


# ---------------------------------------------------------------------------
# RETRY_WARN_THRESHOLD
# ---------------------------------------------------------------------------

class TestRetryWarnThreshold:
    def test_default_value(self):
        assert RETRY_WARN_THRESHOLD == 12


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

class TestEnqueue:

    @pytest.mark.asyncio
    async def test_row_has_correct_fields(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        before = datetime.now(timezone.utc)
        await enqueue(db_path, channel_id=999, content="test msg", failure_reason="upstream error")
        rows = await _fetch_all_rows(db_path)
        assert len(rows) == 1
        r = rows[0]
        assert r["channel_id"] == 999
        assert r["content"] == "test msg"
        assert r["failure_reason"] == "upstream error"
        assert r["retry_count"] == 0
        assert r["last_attempted_at"] is None
        enqueued_at = datetime.fromisoformat(r["enqueued_at"])
        assert enqueued_at >= before


# ---------------------------------------------------------------------------
# get_all_pending
# ---------------------------------------------------------------------------

class TestGetAllPending:

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_rows(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        result = await get_all_pending(db_path)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_rows(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 100, "msg A", "err")
        await enqueue(db_path, 200, "msg B", "err")
        result = await get_all_pending(db_path)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_ordered_by_enqueued_at_asc(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        # Insert two rows with manually set enqueued_at timestamps
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO pending_messages (channel_id, content, failure_reason, enqueued_at, retry_count) "
                "VALUES (100, 'first', 'err', '2026-01-01T10:00:00+00:00', 0)"
            )
            await db.execute(
                "INSERT INTO pending_messages (channel_id, content, failure_reason, enqueued_at, retry_count) "
                "VALUES (200, 'second', 'err', '2026-01-01T09:00:00+00:00', 0)"
            )
            await db.commit()
        result = await get_all_pending(db_path)
        assert result[0].content == "second"
        assert result[1].content == "first"

    @pytest.mark.asyncio
    async def test_pending_message_fields_populated(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, channel_id=77, content="hello", failure_reason="503")
        result = await get_all_pending(db_path)
        pm = result[0]
        assert pm.channel_id == 77
        assert pm.content == "hello"
        assert pm.failure_reason == "503"
        assert pm.retry_count == 0
        assert pm.last_attempted_at is None
        assert isinstance(pm.enqueued_at, datetime)


# ---------------------------------------------------------------------------
# mark_delivered
# ---------------------------------------------------------------------------

class TestMarkDelivered:

    @pytest.mark.asyncio
    async def test_deletes_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]
        await mark_delivered(db_path, entry_id)
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_only_deletes_targeted_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 100, "msg A", "err")
        await enqueue(db_path, 200, "msg B", "err")
        rows = await _fetch_all_rows(db_path)
        first_id = rows[0]["id"]
        await mark_delivered(db_path, first_id)
        remaining = await get_all_pending(db_path)
        assert len(remaining) == 1
        assert remaining[0].content == "msg B"

    @pytest.mark.asyncio
    async def test_no_error_on_missing_id(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        # Should not raise
        await mark_delivered(db_path, 9999)


# ---------------------------------------------------------------------------
# mark_failed
# ---------------------------------------------------------------------------

class TestMarkFailed:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failures", [
        pytest.param(1, id="one failure"),
        pytest.param(5, id="failures accumulate"),
    ])
    async def test_each_failure_is_counted(self, tmp_path, failures):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]

        for _ in range(failures):
            await mark_failed(db_path, entry_id)

        rows2 = await _fetch_all_rows(db_path)
        assert rows2[0]["retry_count"] == failures

    @pytest.mark.asyncio
    async def test_sets_last_attempted_at(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        before = datetime.now(timezone.utc)
        await enqueue(db_path, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]

        await mark_failed(db_path, entry_id)

        rows2 = await _fetch_all_rows(db_path)
        last_attempted = datetime.fromisoformat(rows2[0]["last_attempted_at"])
        assert last_attempted >= before

    @pytest.mark.asyncio
    async def test_does_not_delete_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 100, "msg", "err")
        rows = await _fetch_all_rows(db_path)
        entry_id = rows[0]["id"]
        await mark_failed(db_path, entry_id)
        assert await _row_count(db_path) == 1


# ---------------------------------------------------------------------------
# A log line the change queue wrote and never tried (#439)
# ---------------------------------------------------------------------------


def _delivering_bot(db_path: str):
    """A bot whose channel 555 takes every message, recording how each was sent."""
    import discord
    from unittest.mock import AsyncMock, MagicMock

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.db_path = db_path
    bot.get_channel = MagicMock(return_value=channel)
    bot.output_router.post_log = AsyncMock()
    # The worker's own lines go to the log channel the router names (`_post_own_line`).
    bot.output_router.log_destination = AsyncMock(return_value=99)
    return bot, channel


async def _settle() -> None:
    import asyncio

    for _ in range(10):
        await asyncio.sleep(0)


async def test_a_line_never_tried_is_delivered_by_the_retry_loop_without_a_retry_notice(tmp_path):
    """The bot stopped between saving a line and delivering it: nothing failed, so there is no
    retry to announce."""
    from leaguebot.core.services.retry_service import attempt_delivery

    db_path = await _make_db(str(tmp_path))
    await enqueue(db_path, 555, "Admin (`<@4242>`) | /module disable results | Success", "")
    [entry] = await get_all_pending(db_path)
    bot, channel = _delivering_bot(db_path)

    assert await attempt_delivery(entry, bot) is True
    await _settle()

    assert channel.send.await_count == 1
    assert await _row_count(db_path) == 0
    bot.output_router.post_log.assert_not_awaited()


async def test_a_retried_line_is_sent_notifying_nobody(tmp_path):
    import discord

    from leaguebot.core.services.retry_service import attempt_delivery

    db_path = await _make_db(str(tmp_path))
    await enqueue(db_path, 555, "<@4242> did something", "503")
    [entry] = await get_all_pending(db_path)
    bot, channel = _delivering_bot(db_path)

    await attempt_delivery(entry, bot)
    await _settle()

    sent = channel.send.await_args.kwargs.get("allowed_mentions")
    assert sent is not None
    assert sent.to_dict() == discord.AllowedMentions.none().to_dict()


async def test_attempt_delivery_sends_through_the_log_writer(tmp_path, monkeypatch):
    """A log line for channel 555 that failed once (503) is retried. It is handed to the router's
    log writer with its channel and its stored text, not sent by the retry itself, and the row is
    marked delivered."""
    from unittest.mock import AsyncMock

    from leaguebot.core.services import output_router
    from leaguebot.core.services.retry_service import attempt_delivery

    db_path = await _make_db(str(tmp_path))
    await enqueue(db_path, 555, "Admin (`<@4242>`) | /module disable results | Success", "503")
    [entry] = await get_all_pending(db_path)
    bot, channel = _delivering_bot(db_path)
    writer = AsyncMock()
    monkeypatch.setattr(output_router, "send_log_text", writer, raising=False)

    assert await attempt_delivery(entry, bot) is True
    await _settle()

    writer.assert_awaited_once_with(
        channel, "Admin (`<@4242>`) | /module disable results | Success"
    )
    channel.send.assert_not_awaited()
    assert await _row_count(db_path) == 0


async def test_a_late_log_line_delivered_on_retry_reports_its_delivery_as_today(tmp_path):
    """A log line queued for channel 555 that failed once (503) is delivered on its first retry, to
    the log channel as it is now, 99. The log channel gets exactly one line, saying the delivery to
    99 succeeded, why it first failed and how many retries it took."""
    from leaguebot.core.services.retry_service import attempt_delivery

    db_path = await _make_db(str(tmp_path))
    await enqueue(db_path, 555, "Admin (`<@4242>`) | /module disable results | Success", "503")
    [entry] = await get_all_pending(db_path)
    bot, _channel = _delivering_bot(db_path)

    assert await attempt_delivery(entry, bot) is True
    await _settle()

    [call] = bot.output_router.post_log.await_args_list
    assert call.args[0].startswith(
        "\N{WHITE HEAVY CHECK MARK} Retry delivery succeeded for channel <#99> (id=99). "
        "Original failure: 503. Retries taken: 0. Delivered at: "
    )


async def test_a_stuck_late_log_line_warns_before_its_retry_as_today(tmp_path):
    """A log line queued for channel 555 that failed with 503 and has been retried
    RETRY_WARN_THRESHOLD times is retried once more and delivered to the log channel as it is now,
    99. The log channel is first warned that the entry is stuck, naming 99, the channel tried, how
    often it failed and why, and then told the delivery to 99 succeeded after that many retries."""
    from leaguebot.core.services.retry_service import attempt_delivery

    db_path = await _make_db(str(tmp_path))
    await enqueue(db_path, 555, "Admin (`<@4242>`) | /module disable results | Success", "503")
    [entry] = await get_all_pending(db_path)
    for _ in range(RETRY_WARN_THRESHOLD):
        await mark_failed(db_path, entry.id)
    [entry] = await get_all_pending(db_path)
    bot, _channel = _delivering_bot(db_path)

    assert await attempt_delivery(entry, bot) is True
    await _settle()

    warning, success = (call.args[0] for call in bot.output_router.post_log.await_args_list)
    assert warning.startswith(
        f"\N{WARNING SIGN}\N{VARIATION SELECTOR-16} Stuck retry entry id={entry.id}: message to "
        f"channel <#99> (id=99) has failed {RETRY_WARN_THRESHOLD} time(s) since "
    )
    assert warning.endswith("Original failure: 503")
    assert success.startswith(
        "\N{WHITE HEAVY CHECK MARK} Retry delivery succeeded for channel <#99> (id=99). "
        f"Original failure: 503. Retries taken: {RETRY_WARN_THRESHOLD}. Delivered at: "
    )
