"""Integration tests for the message retry worker — Feature 017.

Covers:
  - Failed OutputRouter._send enqueues a row
  - attempt_delivery succeeds → row deleted, log notification scheduled
  - attempt_delivery fails → retry_count incremented, row retained
  - Warning fires when retry_count >= RETRY_WARN_THRESHOLD
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.retry_service import (
    RETRY_WARN_THRESHOLD,
    attempt_delivery,
    enqueue,
    get_all_pending,
    mark_failed,
)
from utils.output_router import OutputRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path: str) -> str:
    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    return db_path


async def _row_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM pending_messages")
        row = await cursor.fetchone()
        return row[0]


def _make_text_channel_mock(send_side_effect=None) -> MagicMock:
    """Return a MagicMock that passes isinstance(x, discord.TextChannel)."""
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.__class__ = discord.TextChannel  # make isinstance() pass
    if send_side_effect is not None:
        mock_channel.send = AsyncMock(side_effect=send_side_effect)
    else:
        mock_channel.send = AsyncMock()
    return mock_channel


def _make_bot(db_path: str, channel: "discord.TextChannel | None" = None) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.get_channel.return_value = channel
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    return bot


# ---------------------------------------------------------------------------
# OutputRouter enqueue integration
# ---------------------------------------------------------------------------

class TestOutputRouterEnqueuesOnFailure:

    @pytest.mark.asyncio
    async def test_http_exception_enqueues_row(self, tmp_path):
        """A discord.HTTPException in _send should enqueue a pending_messages row."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, retry_db_path=db_path)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(
                MagicMock(status=503), "upstream overflow"
            )
        )
        mock_bot.get_channel.return_value = mock_channel

        result = await router._send(1234, "hello", server_id=99, fallback_label="forecast")

        assert result is None
        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_forbidden_enqueues_row(self, tmp_path):
        """A discord.Forbidden in _send should also enqueue."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, retry_db_path=db_path)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.Forbidden(MagicMock(status=403), "missing access")
        )
        mock_bot.get_channel.return_value = mock_channel

        await router._send(1234, "hello", server_id=99, fallback_label="forecast")

        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_no_enqueue_when_retry_db_path_is_none(self, tmp_path):
        """Without retry_db_path, failures are logged but not enqueued."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, retry_db_path=None)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "error")
        )
        mock_bot.get_channel.return_value = mock_channel

        await router._send(1234, "hello", server_id=99, fallback_label="forecast")

        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_no_enqueue_when_server_id_zero(self, tmp_path):
        """server_id=0 (unknown) means we cannot attribute the entry; skip enqueue."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, retry_db_path=db_path)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "error")
        )
        mock_bot.get_channel.return_value = mock_channel

        await router._send(1234, "hello", server_id=0, fallback_label="log")

        assert await _row_count(db_path) == 0


# ---------------------------------------------------------------------------
# attempt_delivery — success path
# ---------------------------------------------------------------------------

class TestAttemptDeliverySuccess:

    @pytest.mark.asyncio
    async def test_successful_delivery_returns_true(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "test message content", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock()
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("services.retry_service._safe_post_log"):
            result = await attempt_delivery(entry, bot)
        assert result is True

    @pytest.mark.asyncio
    async def test_successful_delivery_removes_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "test message content", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock()
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("services.retry_service._safe_post_log"):
            await attempt_delivery(entry, bot)
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_successful_delivery_sends_to_correct_channel(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "hello world", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock()
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("services.retry_service._safe_post_log"):
            await attempt_delivery(entry, bot)
        mock_channel.send.assert_called_once_with("hello world")


# ---------------------------------------------------------------------------
# attempt_delivery — failure path
# ---------------------------------------------------------------------------

class TestAttemptDeliveryFailure:

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "msg", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "still down")
        )
        bot = _make_bot(db_path, channel=mock_channel)

        result = await attempt_delivery(entry, bot)
        assert result is False

    @pytest.mark.asyncio
    async def test_send_failure_increments_retry_count(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "msg", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "still down")
        )
        bot = _make_bot(db_path, channel=mock_channel)

        await attempt_delivery(entry, bot)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT retry_count FROM pending_messages WHERE id = ?", (entry.id,)
            )
            row = await cursor.fetchone()
        assert row["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_send_failure_does_not_delete_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "msg", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "still down")
        )
        bot = _make_bot(db_path, channel=mock_channel)

        await attempt_delivery(entry, bot)
        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_channel_not_found_increments_retry(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "msg", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        bot = _make_bot(db_path, channel=None)
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )

        result = await attempt_delivery(entry, bot)
        assert result is False
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT retry_count FROM pending_messages WHERE id = ?", (entry.id,)
            )
            row = await cursor.fetchone()
        assert row["retry_count"] == 1


# ---------------------------------------------------------------------------
# Warning threshold — patches _safe_post_log directly (avoids ensure_future timing)
# ---------------------------------------------------------------------------

class TestRetryWarningThreshold:

    @pytest.mark.asyncio
    async def test_warning_posted_at_threshold(self, tmp_path):
        """When retry_count >= RETRY_WARN_THRESHOLD, _safe_post_log is called with a warning."""
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "stuck message", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        for _ in range(RETRY_WARN_THRESHOLD):
            await mark_failed(db_path, entry.id)
        pending2 = await get_all_pending(db_path)
        entry_at_threshold = pending2[0]
        assert entry_at_threshold.retry_count == RETRY_WARN_THRESHOLD

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "still down")
        )
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("services.retry_service._safe_post_log") as mock_safe_log:
            await attempt_delivery(entry_at_threshold, bot)

        assert mock_safe_log.call_count >= 1
        # First call is the warning; args are (bot, server_id, message)
        warn_message = mock_safe_log.call_args_list[0][0][2]
        assert "Stuck retry" in warn_message

    @pytest.mark.asyncio
    async def test_no_warning_below_threshold(self, tmp_path):
        """Under the threshold, _safe_post_log is never called."""
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 1, 555, "msg", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        # retry_count = 0 (below threshold)
        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "down")
        )
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("services.retry_service._safe_post_log") as mock_safe_log:
            await attempt_delivery(entry, bot)

        mock_safe_log.assert_not_called()
