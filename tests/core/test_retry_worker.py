"""Integration tests for the message retry worker — Feature 017.

Covers:
  - Failed OutputRouter._send enqueues a row
  - attempt_delivery succeeds → row deleted, log notification scheduled
  - attempt_delivery fails → retry_count incremented, row retained
  - Warning fires when retry_count >= RETRY_WARN_THRESHOLD
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.retry_service import (
    RETRY_WARN_THRESHOLD,
    attempt_delivery,
    enqueue,
    get_all_pending,
    mark_failed,
)
from leaguebot.core.services.output_router import OutputRouter


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
    # No log channel named now, so a row goes where it was queued for.
    bot.output_router.log_destination = AsyncMock(return_value=None)
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
        router = OutputRouter(mock_bot, mock_bot.config_service, retry_db_path=db_path)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(
                MagicMock(status=503), "upstream overflow"
            )
        )
        mock_bot.get_channel.return_value = mock_channel

        result = await router._send(1234, "hello", enqueue_on_failure=True, fallback_label="forecast")

        assert result is None
        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_forbidden_enqueues_row(self, tmp_path):
        """A discord.Forbidden in _send should also enqueue."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, mock_bot.config_service, retry_db_path=db_path)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.Forbidden(MagicMock(status=403), "missing access")
        )
        mock_bot.get_channel.return_value = mock_channel

        await router._send(1234, "hello", enqueue_on_failure=True, fallback_label="forecast")

        assert await _row_count(db_path) == 1

    @pytest.mark.asyncio
    async def test_no_enqueue_when_retry_db_path_is_none(self, tmp_path):
        """Without retry_db_path, failures are logged but not enqueued."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, mock_bot.config_service, retry_db_path=None)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "error")
        )
        mock_bot.get_channel.return_value = mock_channel

        await router._send(1234, "hello", enqueue_on_failure=True, fallback_label="forecast")

        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_no_enqueue_when_the_caller_does_not_want_one(self, tmp_path):
        """The factory reset's line to a given channel asks for no retry, and gets none."""
        db_path = await _make_db(str(tmp_path))

        mock_bot = MagicMock()
        router = OutputRouter(mock_bot, mock_bot.config_service, retry_db_path=db_path)

        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "error")
        )
        mock_bot.get_channel.return_value = mock_channel

        await router._send(1234, "hello", enqueue_on_failure=False, fallback_label="log")

        assert await _row_count(db_path) == 0


# ---------------------------------------------------------------------------
# attempt_delivery — success path
# ---------------------------------------------------------------------------

class TestAttemptDeliverySuccess:

    @pytest.mark.asyncio
    async def test_successful_delivery_returns_true(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "test message content", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock()
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("leaguebot.core.services.retry_service._safe_post_log"):
            result = await attempt_delivery(entry, bot)
        assert result is True

    @pytest.mark.asyncio
    async def test_successful_delivery_removes_row(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "test message content", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock()
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("leaguebot.core.services.retry_service._safe_post_log"):
            await attempt_delivery(entry, bot)
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_successful_delivery_sends_to_correct_channel(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "hello world", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        mock_channel = _make_text_channel_mock()
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("leaguebot.core.services.retry_service._safe_post_log"):
            await attempt_delivery(entry, bot)
        # The content alone: how it is sent, mentioning nobody, is test_retry_service's to pin.
        (call,) = mock_channel.send.call_args_list
        assert (call.args[0] if call.args else call.kwargs["content"]) == "hello world"


# ---------------------------------------------------------------------------
# attempt_delivery — failure path
# ---------------------------------------------------------------------------

class TestAttemptDeliveryFailure:

    @pytest.mark.asyncio
    async def test_send_failure_returns_false(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "msg", "503")
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
        await enqueue(db_path, 555, "msg", "503")
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
        await enqueue(db_path, 555, "msg", "503")
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
        await enqueue(db_path, 555, "msg", "503")
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
        await enqueue(db_path, 555, "stuck message", "503")
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

        with patch("leaguebot.core.services.retry_service._safe_post_log") as mock_safe_log:
            await attempt_delivery(entry_at_threshold, bot)

        assert mock_safe_log.call_count >= 1
        # First call is the warning; args are (bot, message)
        warn_message = mock_safe_log.call_args_list[0][0][1]
        assert "Stuck retry" in warn_message

    @pytest.mark.asyncio
    async def test_no_warning_below_threshold(self, tmp_path):
        """Under the threshold, _safe_post_log is never called."""
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "msg", "503")
        pending = await get_all_pending(db_path)
        entry = pending[0]

        # retry_count = 0 (below threshold)
        mock_channel = _make_text_channel_mock(
            send_side_effect=discord.HTTPException(MagicMock(status=503), "down")
        )
        bot = _make_bot(db_path, channel=mock_channel)

        with patch("leaguebot.core.services.retry_service._safe_post_log") as mock_safe_log:
            await attempt_delivery(entry, bot)

        mock_safe_log.assert_not_called()


# ---------------------------------------------------------------------------
# The retry worker's own lines are never queued (FR-009)
#
# The "Stuck retry entry" warning and the "Retry delivery succeeded" line are the worker's own.
# Queued where the log channel could not take them, each would wait on the queue beside the row it
# reports on, and a stuck row would queue a fresh warning on every cycle, each warning becoming a
# stuck row of its own: the queue would grow for as long as the log channel stayed unreachable.
# They go to the log channel where it can take them, and to the host's log where it cannot.
# ---------------------------------------------------------------------------

def _worker_bot(db_path: str, channel, *, configured: bool = True) -> MagicMock:
    """A bot whose router is the real one, writing the retry queue in *db_path*, with the log
    channel 99 answered by *channel*."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.get_channel.return_value = channel
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=99) if configured else None
    )
    bot.output_router = OutputRouter(bot, bot.config_service, retry_db_path=db_path)
    return bot


class TestTheRetryWorkersOwnLinesAreNotQueued:

    @pytest.mark.asyncio
    async def test_its_own_line_goes_to_the_log_channel(self, tmp_path):
        from leaguebot.core.services.retry_service import _post_own_line

        db_path = await _make_db(str(tmp_path))
        channel = _make_text_channel_mock()
        bot = _worker_bot(db_path, channel)

        await _post_own_line(bot, "✅ Retry delivery succeeded for channel <#99>")

        [call] = channel.send.await_args_list
        assert "Retry delivery succeeded" in call.args[0]
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fault", ["refused", "channel-gone", "not-a-text-channel"])
    async def test_its_own_line_the_log_channel_cannot_take_is_not_queued(
        self, tmp_path, caplog, fault
    ):
        """The log channel refuses the line, is gone, or is no text channel: no row is queued,
        and the line is written to the host's log."""
        from leaguebot.core.services.retry_service import _post_own_line

        db_path = await _make_db(str(tmp_path))
        if fault == "refused":
            channel = _make_text_channel_mock(
                send_side_effect=discord.Forbidden(MagicMock(status=403), "Missing Access")
            )
        elif fault == "channel-gone":
            channel = None
        else:
            channel = MagicMock(spec=discord.VoiceChannel)
        bot = _worker_bot(db_path, channel)
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )

        with caplog.at_level("WARNING"):
            await _post_own_line(bot, "⚠️ Stuck retry entry id=7")

        assert await _row_count(db_path) == 0
        assert "Stuck retry entry id=7" in caplog.text

    @pytest.mark.asyncio
    async def test_its_own_line_before_the_bot_is_set_up_goes_to_the_host_log(
        self, tmp_path, caplog
    ):
        from leaguebot.core.services.retry_service import _post_own_line

        db_path = await _make_db(str(tmp_path))
        bot = _worker_bot(db_path, _make_text_channel_mock(), configured=False)

        with caplog.at_level("WARNING"):
            await _post_own_line(bot, "⚠️ Stuck retry entry id=8")

        assert await _row_count(db_path) == 0
        assert "Stuck retry entry id=8" in caplog.text


# ---------------------------------------------------------------------------
# A waiting line goes to the log channel as it is now
#
# The retry queue holds log lines alone, and a line is delivered to the log channel set when it is
# tried: one set again after the old was deleted takes the lines that waited, which would otherwise
# never be delivered, a stuck warning about each of them posted in the new channel every cycle.
# ---------------------------------------------------------------------------

def _channels(**by_id) -> "MagicMock":
    """`get_channel` answering each id given, and None for any other."""
    return MagicMock(side_effect=lambda channel_id: by_id.get(f"c{channel_id}"))


class TestAWaitingLineGoesToTheLogChannelAsItIsNow:

    @pytest.mark.asyncio
    async def test_a_line_queued_for_a_deleted_channel_is_delivered_to_the_new_one(self, tmp_path):
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "Admin | /module enable weather | Success", "Unknown Channel")
        [entry] = await get_all_pending(db_path)
        new = _make_text_channel_mock()
        bot = _worker_bot(db_path, None)
        bot.get_channel = _channels(c99=new)
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )

        assert await attempt_delivery(entry, bot) is True

        assert new.send.await_args_list[0].args[0] == "Admin | /module enable weather | Success"
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_a_line_a_change_saved_is_delivered_to_the_log_channel_set_since(self, tmp_path):
        """A change's line is queued in its save for the log channel of the moment, 555, and
        delivered once the save commits: the log channel is 99 by then, and takes it."""
        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 555, "Admin | /module enable weather | Success", "")
        [entry] = await get_all_pending(db_path)
        new = _make_text_channel_mock()
        bot = _worker_bot(db_path, None)
        bot.get_channel = _channels(c99=new)
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )

        await bot.output_router.deliver_queued([entry.id])

        assert new.send.await_count == 1
        assert await _row_count(db_path) == 0

    @pytest.mark.asyncio
    async def test_a_stuck_line_whose_log_channel_cannot_take_it_queues_nothing_more(self, tmp_path):
        """Through the real router: a row retried RETRY_WARN_THRESHOLD times, the log channel
        refusing everything. The try fails, the stuck warning cannot be posted either, and the
        queue still holds the one row."""
        import asyncio

        db_path = await _make_db(str(tmp_path))
        await enqueue(db_path, 99, "Admin | /module enable weather | Success", "503")
        [entry] = await get_all_pending(db_path)
        for _ in range(RETRY_WARN_THRESHOLD):
            await mark_failed(db_path, entry.id)
        [entry] = await get_all_pending(db_path)
        refusing = _make_text_channel_mock(
            send_side_effect=discord.Forbidden(MagicMock(status=403), "Missing Access")
        )
        bot = _worker_bot(db_path, refusing)

        assert await attempt_delivery(entry, bot) is False
        for _ in range(5):
            await asyncio.sleep(0)

        assert await _row_count(db_path) == 1

