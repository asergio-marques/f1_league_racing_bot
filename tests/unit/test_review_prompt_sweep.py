"""The season-review approve button, swept away by a restart.

The button expires five minutes after `/season review` posts it, and the timer that does
so is a `discord.ui.View` timeout — held in memory, and lost with the process. A bot
restarted inside that window leaves a public message offering a button nothing is
listening to, and nothing else would ever clear it.

Every row surviving to startup is expired by definition: the bot was down, so the five
minutes cannot have been served. These pin that the sweep deletes the message, says so,
and — the part that matters most — clears the row whatever Discord did, since a row left
behind would be swept again on every restart thereafter.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

# `bot.py` reads the token at import time and raises without it. Nothing here connects to
# Discord — the sweep is an ordinary function taking a bot object — but the module has to
# import before it can be reached, and this is the first test in the suite to need it.
os.environ.setdefault("BOT_TOKEN", "not-a-real-token")

from bot import _recover_expired_review_prompts  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 6501
CHANNEL_ID = 700
MESSAGE_ID = 800
REVIEWER_ID = 4242


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "prompts.db")
    await run_migrations(path)
    return path


async def _store(path: str, *, server_id: int = SERVER_ID) -> None:
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO season_review_prompts "
            "(server_id, season_id, channel_id, message_id, reviewer_id, posted_at) "
            "VALUES (?, 1, ?, ?, ?, '2026-09-07T12:00:00+00:00')",
            (server_id, CHANNEL_ID, MESSAGE_ID, REVIEWER_ID),
        )
        await db.commit()


async def _rows(path: str) -> list:
    async with get_connection(path) as db:
        cursor = await db.execute("SELECT * FROM season_review_prompts")
        return await cursor.fetchall()


def _bot(db_path: str, channel=None, *, cached: bool = True):
    """*cached* False makes `get_channel` miss, so the fetch fallback is exercised."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.get_channel = MagicMock(return_value=channel if cached else None)
    if channel is None:
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "gone")
        )
    else:
        bot.fetch_channel = AsyncMock(return_value=channel)
    return bot


def _channel():
    channel = MagicMock()
    message = MagicMock()
    message.delete = AsyncMock()
    channel.fetch_message = AsyncMock(return_value=message)
    channel.send = AsyncMock()
    return channel, message


async def test_a_standing_prompt_is_deleted(db_path):
    await _store(db_path)
    channel, message = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel))

    message.delete.assert_awaited_once()


async def test_the_notice_pings_the_reviewer(db_path):
    await _store(db_path)
    channel, _ = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel))

    notice = channel.send.await_args.args[0]
    assert f"<@{REVIEWER_ID}>" in notice
    assert "/season review" in notice


async def test_the_row_is_cleared(db_path):
    """Left behind, it would be swept again on every restart for ever."""
    await _store(db_path)
    channel, _ = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel))

    assert await _rows(db_path) == []


async def test_a_message_already_gone_still_clears_its_row(db_path):
    """Deleted by hand, or with its channel, while the bot was down."""
    await _store(db_path)
    channel, _ = _channel()
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "gone")
    )

    await _recover_expired_review_prompts(_bot(db_path, channel))

    assert await _rows(db_path) == []
    channel.send.assert_awaited_once()


async def test_an_unreachable_channel_still_clears_its_row(db_path):
    """The channel itself is gone: neither the cache nor a fetch finds it."""
    await _store(db_path)

    await _recover_expired_review_prompts(_bot(db_path, None))

    assert await _rows(db_path) == []


async def test_a_channel_missing_from_the_cache_is_fetched(db_path):
    """The sweep runs during startup, where the cache may not yet hold every channel.

    A miss and a deleted channel look identical to `get_channel`, and the row is cleared
    either way — so trusting the cache alone would drop the only record of a message
    still standing, which is the whole thing this sweep exists to prevent.
    """
    await _store(db_path)
    channel, message = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel, cached=False))

    message.delete.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert await _rows(db_path) == []


async def test_a_fetch_that_fails_still_clears_its_row(db_path):
    """A refused fetch must not leave the row to be swept again on every restart."""
    await _store(db_path)
    bot = _bot(db_path, None)
    bot.fetch_channel = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(status=500), "boom")
    )

    await _recover_expired_review_prompts(bot)

    assert await _rows(db_path) == []


async def test_a_refused_delete_does_not_stop_the_sweep(db_path):
    await _store(db_path)
    channel, message = _channel()
    message.delete = AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no"))

    await _recover_expired_review_prompts(_bot(db_path, channel))

    assert await _rows(db_path) == []


async def test_every_server_is_swept(db_path):
    """One row per server, and a restart clears all of them."""
    await _store(db_path)
    await _store(db_path, server_id=SERVER_ID + 1)
    channel, _ = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel))

    assert await _rows(db_path) == []
    assert channel.send.await_count == 2


async def test_nothing_standing_does_nothing(db_path):
    channel, _ = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel))

    channel.send.assert_not_awaited()


async def test_an_unreadable_database_is_survived():
    """A sweep that raises would stop the bot finishing its startup."""
    await _recover_expired_review_prompts(_bot("/nonexistent/nowhere.db"))
