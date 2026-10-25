"""`/clean-bot` on the change queue (#439, slice 6b, C101): a member's change, `core.clean`, carried
out on the league lane whole.

Today the command reads the channel and deletes the bot's newest messages at once, off the queue,
counting a refused delete and passing over it. On the queue it is acknowledged at once; its first
job chooses, when it runs, the newest *count* of the bot's own messages among the channel's last
two hundred (decision 18, core spec :181: it may take a message the bot posted while it waited),
and plans a delete job for each; a last job closes it with today's reply as its outcome. A refused
delete stops the queue, and a Discard names the message and leaves it uncounted (core spec :183);
a message already gone by its delete completes it, uncounted. The command runs on the league lane
even in a channel the bot keeps for a conversation (decision 30).

The league: `league_double`'s, the league admin Admin (4242) running the command in channel 700,
which holds, newest first, messages of the bot and of a driver.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import run_migrations
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    Clock,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    history_channel,
    league_double,
    member_interaction,
    post_in,
    retry_job,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
)
from tests.support.review_league import block_queue
from tests.support.undecorate import undecorate

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
KIND = "core.clean"
CLEANED = 700
BOT_USER = SimpleNamespace(id=1, name="LeagueBot")
DRIVER = SimpleNamespace(id=105, name="Kimi")

@pytest.fixture
async def env(tmp_path):
    """The league's queue, with every change type the builder registers, and channel 700 found
    by the bot and by its server."""
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.user = BOT_USER
    channel = history_channel(CLEANED)
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel,
             CLEANED: channel}
    guild = MagicMock(spec=discord.Guild)
    guild.id = 12408
    guild.me = BOT_USER
    guild.get_channel = MagicMock(side_effect=known.get)
    channel.guild = guild
    bot.get_channel = MagicMock(side_effect=known.get)
    bot.get_guild = MagicMock(return_value=guild)
    attach_queue(bot, db_path, now=Clock(NOW))
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel)


async def _clean(env: Any, count: int) -> Any:
    """Admin runs `/clean-bot` with *count* in channel 700; the queue is not run."""
    from leaguebot.core.cogs.clean_cog import CleanCog

    cog = CleanCog.__new__(CleanCog)
    cog.bot = env.bot
    interaction = member_interaction(env.bot)
    interaction.channel = env.channel
    interaction.channel_id = CLEANED
    interaction.command = SimpleNamespace(qualified_name="clean-bot")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())
    await undecorate(CleanCog.clean_bot)(cog, interaction, count)
    return interaction


def _said(interaction: Any) -> list[str]:
    """Everything Admin was told, in order: the first answer, then each update of it."""
    calls = (interaction.response.send_message.await_args_list
             + interaction.followup.send.await_args_list
             + interaction.edit_original_response.await_args_list
             + interaction.followup.send.return_value.edit.await_args_list)
    return [str(c.args[0]) if c.args else str(c.kwargs.get("content", "")) for c in calls]


async def _cleans(env: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(env.db_path) if row["kind"] == KIND]


async def test_clean_bot_is_acknowledged_and_deletes_the_chosen_messages_as_jobs(env):
    """Channel 700 holds, oldest first, three of the bot's messages A, B and C, with Kimi's
    between B and C. Admin runs `/clean-bot` for 2. Before the queue runs nothing is deleted and
    Admin has been answered "⏳"; once it runs, C and B are deleted by a delete job each, after a
    job choosing them and before one closing the change, A and Kimi's message stand, and the
    acknowledgement is updated to say two were deleted."""
    a = post_in(env.channel, BOT_USER)
    b = post_in(env.channel, BOT_USER)
    kimi = post_in(env.channel, DRIVER)
    c = post_in(env.channel, BOT_USER)

    interaction = await _clean(env, 2)
    deleted_before = list(env.channel.deleted)
    await run_queue(env.bot)

    assert deleted_before == []
    said = _said(interaction)
    assert said[0].startswith("⏳")
    assert env.channel.deleted == [c, b]
    assert env.channel.messages == [kimi, a]
    [change] = await _cleans(env)
    assert change["state"] == "DONE"
    names = [job["name"] for job in await step_rows(env.db_path, change["id"])]
    assert names[0] == "choose" and names[-1] == "close"
    assert names.count("delete_message") == 2
    assert "Deleted 2" in said[-1]


async def test_the_messages_are_chosen_when_its_job_runs(env):
    """The bot's messages A, B and C stand, C newest. The queue is stopped when Admin runs
    `/clean-bot` for 2; while it waits, C and B are deleted by hand. Once the stop clears, the
    clean reads the channel as it then stands and deletes A, the newest of the bot's messages
    left, and says one was deleted: it chose when its job ran, not when it was asked."""
    a = post_in(env.channel, BOT_USER)
    b = post_in(env.channel, BOT_USER)
    c = post_in(env.channel, BOT_USER)
    holder = await block_queue(SimpleNamespace(bot=env.bot, db_path=env.db_path))

    interaction = await _clean(env, 2)
    env.channel.messages.remove(c)
    env.channel.messages.remove(b)
    holder["fail"] = False
    await retry_job(env.bot)
    await run_queue(env.bot)

    assert env.channel.deleted == [a]
    assert "Deleted 1" in _said(interaction)[-1]


async def test_clean_bot_may_delete_a_message_the_bot_posted_while_it_waited(env):
    """The bot's message A stands. The queue is stopped when Admin runs `/clean-bot` for 1; while
    it waits, the bot posts message N in the channel. Once the stop clears, N, the newest of the
    bot's messages when the job runs, is the one deleted, and A stands (decision 18)."""
    a = post_in(env.channel, BOT_USER)
    holder = await block_queue(SimpleNamespace(bot=env.bot, db_path=env.db_path))

    await _clean(env, 1)
    n = post_in(env.channel, BOT_USER)
    holder["fail"] = False
    await retry_job(env.bot)
    await run_queue(env.bot)

    assert env.channel.deleted == [n]
    assert env.channel.messages == [a]


async def test_a_refused_delete_stops_the_queue_and_a_discard_names_it(env):
    """The bot's messages A and B stand, B newest; Discord refuses to delete A. Admin runs
    `/clean-bot` for 2: B is deleted and the queue stops at A's delete job. Admin discards it:
    the change completes, A stands, and Admin's reply says one was deleted, naming A as not
    deleted."""
    a = post_in(env.channel, BOT_USER,
              fails=http_error(discord.Forbidden, status=403, text="Missing Permissions"))
    b = post_in(env.channel, BOT_USER)

    interaction = await _clean(env, 2)
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    assert job is not None and job["name"] == "delete_message"
    assert env.channel.deleted == [b]

    await discard_job(env.bot)
    await run_queue(env.bot)

    [change] = await _cleans(env)
    assert change["state"] == "DONE"
    assert env.channel.messages == [a]
    said = _said(interaction)[-1]
    assert "Deleted 1" in said
    assert str(a.id) in said


async def test_a_message_already_gone_is_not_counted(env):
    """The bot's messages A, B and C stand, C newest. Admin runs `/clean-bot` for 2; its first
    job chooses C and B, then B is deleted by hand before its delete runs. B's delete completes
    without a stop, C is deleted, A is not taken in B's place, and Admin is told one was
    deleted."""
    a = post_in(env.channel, BOT_USER)
    b = post_in(env.channel, BOT_USER)
    c = post_in(env.channel, BOT_USER)

    interaction = await _clean(env, 2)
    await run_queue(env.bot, steps=1)
    env.channel.messages.remove(b)
    await run_queue(env.bot)

    assert await stopped_job(env.db_path) is None
    assert env.channel.deleted == [c]
    assert env.channel.messages == [a]
    assert "Deleted 1" in _said(interaction)[-1]


async def test_clean_bot_in_a_conversation_s_channel_runs_on_the_league_lane(env):
    """Channel 700 is a channel the bot keeps for a conversation, and that conversation's turns
    are stopped on a failed job. The bot's message A stands there. Admin runs `/clean-bot` for 1:
    the change is on the league lane, so the conversation's stop does not hold it, and A is
    deleted (decision 30: one command, one lane)."""
    a = post_in(env.channel, BOT_USER)
    await block_queue(SimpleNamespace(bot=env.bot, db_path=env.db_path),
                      lane=f"channel:{CLEANED}")

    await _clean(env, 1)
    await run_queue(env.bot)

    [change] = await _cleans(env)
    assert change["lane"] == "league"
    assert change["state"] == "DONE"
    assert env.channel.deleted == [a]


async def test_the_change_is_refused_where_the_bot_may_not_manage_messages(env):
    """The bot's message A stands in channel 700, where the bot has no Manage Messages. Admin runs
    `/clean-bot` for 1: the change is refused with the permission named, nothing is saved, and A
    stands."""
    a = post_in(env.channel, BOT_USER)
    env.channel.permissions_for = MagicMock(return_value=MagicMock(manage_messages=False))

    interaction = await _clean(env, 1)
    await run_queue(env.bot)

    assert await _cleans(env) == []
    assert any("Manage Messages" in line for line in _said(interaction))
    assert env.channel.messages == [a]


async def test_a_channel_gone_before_the_change_runs_refuses_it_and_deletes_nothing(env):
    """The bot's message A stands in channel 700. Admin runs `/clean-bot` for 1; before the queue
    runs the channel is gone from the server. The change is refused as it comes up to run, the
    queue does not stop, and nothing is deleted."""
    post_in(env.channel, BOT_USER)

    await _clean(env, 1)
    env.bot.get_channel = MagicMock(return_value=None)
    env.bot.get_guild = MagicMock(return_value=None)
    await run_queue(env.bot)

    assert await stopped_job(env.db_path) is None
    [change] = await _cleans(env)
    assert change["state"] == "REFUSED"
    assert env.channel.deleted == []
