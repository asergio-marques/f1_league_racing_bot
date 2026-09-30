"""The season-review approve button, swept away by a restart.

The button expires five minutes after `/season placements-review` posts it, and the timer that does
so is a `discord.ui.View` timeout — held in memory, and lost with the process. A bot
restarted inside that window leaves a public message offering a button nothing is
listening to, and nothing else would ever clear it.

Every row surviving to startup is expired by definition: the bot was down, so the five
minutes cannot have been served. These pin that the sweep deletes the message, says so,
and — the part that matters most — clears the row whatever Discord did, since a row left
behind would be swept again on every restart thereafter.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest


# `__main__.py` reads the token at import time and raises without it; `tests/conftest.py` gives it
# a placeholder before collection, so every file importing `bot` can do so at module level.
from leaguebot.__main__ import _recover_expired_review_prompts
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.review_prompts import store_review_prompt

SERVER_ID = 6501
CHANNEL_ID = 700
MESSAGE_ID = 800
REVIEWER_ID = 4242


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "prompts.db")
    await run_migrations(path)
    return path


async def _store(
    path: str,
    *,
    message_id: int = MESSAGE_ID,
    review: str = "/season placements-review",
    report_message_ids: tuple[int, ...] = (),
) -> None:
    """One review prompt standing in channel 700, posted for Alex's review of season 1."""
    async with get_connection(path) as db:
        await store_review_prompt(
            db,
            season_id=1,
            channel_id=CHANNEL_ID,
            message_id=message_id,
            reviewer_id=REVIEWER_ID,
            review=review,
            report_message_ids=report_message_ids,
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
    # The log channel, and the league's server on which Alex, the reviewer, is found by id: a
    # lapse the sweep records has no interaction to name him from.
    bot.output_router.post_log = AsyncMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    alex = MagicMock()
    alex.id = REVIEWER_ID
    alex.display_name = "Alex"
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: alex if uid == REVIEWER_ID else None)
    bot.get_guild = MagicMock(return_value=guild)
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
    assert "/season placements-review" in notice


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


async def test_nothing_standing_does_nothing(db_path):
    channel, _ = _channel()

    await _recover_expired_review_prompts(_bot(db_path, channel))

    channel.send.assert_not_awaited()


async def test_an_unreadable_database_is_survived():
    """A sweep that raises would stop the bot finishing its startup."""
    await _recover_expired_review_prompts(_bot("/nonexistent/nowhere.db"))


# ── #482: what the sweep records, and one row per standing prompt ─────────────────────────




def _logged(bot) -> list[str]:
    return [call.args[0] for call in bot.output_router.post_log.await_args_list]


def _channel_holding(*message_ids: int):
    """Channel 700, holding each message by its id, each deletable."""
    channel = MagicMock()
    channel.id = CHANNEL_ID
    messages = {mid: MagicMock(id=mid, delete=AsyncMock()) for mid in message_ids}
    channel.fetch_message = AsyncMock(side_effect=lambda mid: messages[int(mid)])
    channel.send = AsyncMock()
    return channel, messages


@pytest.mark.parametrize("review", ["/season placements-review", "/season config-review"])
async def test_a_cleared_prompt_records_its_lapse_naming_the_reviewer_and_the_review(
    db_path, review
):
    """Criterion 10: a review cleared at start-up records one lapse line naming the reviewer."""
    await _store(db_path, review=review)
    channel, _ = _channel()
    bot = _bot(db_path, channel)

    await _recover_expired_review_prompts(bot)

    channel.send.assert_awaited_once()
    lines = _logged(bot)
    assert len(lines) == 1, lines
    head, _, beneath = lines[0].partition("\n")
    assert head.startswith("⌛ "), head
    assert review in head, head
    assert head.endswith("lapsed unconfirmed (started by Alex (<@4242>))"), head
    assert review in beneath and "again" in beneath, lines[0]


async def test_an_older_review_lapsing_leaves_a_newer_ones_record_for_the_restart(db_path):
    """F1. Two placements reviews stand in channel 700, Alex's older one (message 800) and his
    newer one (message 801). The older lapses; the bot then restarts with the newer still
    standing, and the sweep must find it."""
    from leaguebot.core.cogs.season_cog import _ApproveView

    channel, messages = _channel_holding(800, 801)
    bot = _bot(db_path, channel)
    cog = MagicMock()
    cog.bot = bot
    older = _ApproveView(cog, REVIEWER_ID)
    newer = _ApproveView(cog, REVIEWER_ID)
    for view, message_id in ((older, 800), (newer, 801)):
        view._season_id = 1
        prompt = messages[message_id]
        prompt.channel = channel
        await view.bind(prompt)

    await older.on_timeout()

    rows = await _rows(db_path)
    assert [int(row["message_id"]) for row in rows] == [801]

    bot.output_router.post_log.reset_mock()
    channel.send.reset_mock()
    await _recover_expired_review_prompts(bot)

    messages[801].delete.assert_awaited_once()
    notice = channel.send.await_args.args[0]
    assert f"<@{REVIEWER_ID}>" in notice
    lapses = [line for line in _logged(bot) if line.startswith("⌛ ")]
    assert len(lapses) == 1, _logged(bot)
    assert "/season placements-review" in lapses[0].partition("\n")[0]
    assert await _rows(db_path) == []


async def test_a_review_standing_at_a_restart_has_its_report_deleted_with_its_prompt(db_path):
    """F2. Alex's placements review stands in channel 700: its report (messages 901 and 902) and
    its question (message 800). The bot restarts; the report goes with the question, as it
    does when the review expires while the bot runs."""
    from leaguebot.core.cogs.season_cog import _ApproveView

    channel, messages = _channel_holding(800, 901, 902)
    bot = _bot(db_path, channel)
    cog = MagicMock()
    cog.bot = bot
    view = _ApproveView(cog, REVIEWER_ID)
    view._season_id = 1
    view.carries([messages[901], messages[902]])
    messages[800].channel = channel
    await view.bind(messages[800])

    await _recover_expired_review_prompts(bot)

    for message_id in (800, 901, 902):
        messages[message_id].delete.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert await _rows(db_path) == []


async def test_a_prompt_whose_report_list_cannot_be_read_is_still_cleared(db_path):
    """A standing prompt whose recorded report is unreadable (written by hand, say) is still
    swept: its question is deleted, the notice posted, its lapse recorded and its row cleared,
    and the start-up goes on rather than stopping on it."""
    await _store(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE season_review_prompts SET report_message_ids = 'not a list'")
        await db.commit()
    channel, message = _channel()
    bot = _bot(db_path, channel)

    await _recover_expired_review_prompts(bot)

    message.delete.assert_awaited_once()
    channel.send.assert_awaited_once()
    assert [line for line in _logged(bot) if line.startswith("⌛ ")]
    assert await _rows(db_path) == []
