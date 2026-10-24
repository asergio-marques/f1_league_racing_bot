"""`channel.tell`: a change that tells a channel one line, on the change queue (#439, 6b).

A job or a start-up step needing a one-line notice in a channel asks it, as the bot's: its one
job, `tell`, posts the payload's `text` in the channel `channel_id`, stopping the queue where
Discord refuses it or the channel is gone. It mentions nobody but the member its payload names
(`mention`, a member's id). The mid-season confirmation's fallback and the review take-down's
restart notice use it. `channel_tell_change()` (`leaguebot.core.services.channel_tell_change`) is
the factory the builder registers.

These tests use the real change type on a database built by the migrations, with "now" pinned.
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
    league_double,
    run_queue,
    seed_server,
    stopped_job,
    text_channel,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
CHANNEL = 707
TEXT = "Placements for season 3 were confirmed, but the reviewer could not be told privately."


def _channels(bot: Any, *channels: Any) -> None:
    """Let *bot* find *channels* beside its log and interaction channels; any other is gone."""
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel}
    known.update({channel.id: channel for channel in channels})

    async def _fetch(channel_id: int) -> Any:
        if channel_id in known:
            return known[channel_id]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    bot.get_channel = MagicMock(side_effect=known.get)
    bot.fetch_channel = AsyncMock(side_effect=_fetch)


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    channel = text_channel(CHANNEL)
    _channels(bot, channel)
    return SimpleNamespace(db_path=db_path, bot=bot, clock=Clock(NOW), channel=channel)


async def _tell(env, *, mention: int | None = None) -> None:
    """Register `channel.tell` alone and ask it, as the bot's, to tell channel 707 `TEXT`."""
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.channel_tell_change import channel_tell_change

    attach_queue(env.bot, env.db_path, now=env.clock, types=(channel_tell_change(),))
    payload: dict[str, Any] = {"channel_id": CHANNEL, "text": TEXT}
    if mention is not None:
        payload["mention"] = mention
    await env.bot.change_queue.ask(
        "channel.tell", payload, what="telling the review's channel", origin=ChangeOrigin.BOT,
    )


def _states(rows: list[dict[str, Any]]) -> list[str]:
    return [row["state"] for row in rows]


def _log(env) -> str:
    return "\n".join(env.bot.log_channel.sent)


async def test_a_tell_posts_its_line_in_its_channel(env):
    """The bot asks for a line to be told in channel 707: once the queue runs, the line is
    posted there, once, and the change is done."""
    await _tell(env)

    await run_queue(env.bot)

    assert env.channel.sent == [TEXT]
    assert _states(await change_rows(env.db_path)) == ["DONE"]


async def test_a_refused_tell_stops_the_queue_and_retries(env):
    """Discord refuses the line: the queue stops at the tell, nothing posted. A minute later it is
    tried again on the schedule and, Discord taking it now, the line is posted and the change
    done."""
    await _tell(env)
    env.channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")

    await run_queue(env.bot)

    assert (await stopped_job(env.db_path))["name"] == "tell"
    assert env.channel.sent == []

    env.channel.fails = None
    env.clock.advance(minutes=1)
    await run_queue(env.bot)

    assert env.channel.sent == [TEXT]
    assert await stopped_job(env.db_path) is None
    assert _states(await change_rows(env.db_path)) == ["DONE"]


async def test_a_tell_whose_channel_is_gone_stops_the_queue_naming_it(env):
    """Channel 707 has been deleted from the server: the queue stops at the tell, and the stop
    notice in the log channel names the channel."""
    _channels(env.bot)
    await _tell(env)

    await run_queue(env.bot)

    assert (await stopped_job(env.db_path))["name"] == "tell"
    assert f"<#{CHANNEL}>" in _log(env)


async def test_a_discarded_tell_is_named_in_the_log(env):
    """A refused tell is discarded by a league admin: the queue runs on, nothing is posted, and
    the log channel's line for the Discard names the channel the line was for."""
    await _tell(env)
    env.channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    await run_queue(env.bot)
    before = len(env.bot.log_channel.sent)

    await discard_job(env.bot)

    assert await stopped_job(env.db_path) is None
    assert env.channel.sent == []
    assert f"<#{CHANNEL}>" in "\n".join(env.bot.log_channel.sent[before:])


async def test_a_tell_mentions_nobody_but_the_member_its_payload_names(env):
    """A line with no member named is posted mentioning nobody; one naming the member 4242 is
    posted allowing that member's mention alone — no role, no everyone, no other member."""
    await _tell(env)
    await run_queue(env.bot)
    await _tell(env, mention=4242)
    await run_queue(env.bot)

    plain, pinging = (call.kwargs["allowed_mentions"]
                      for call in env.channel.send.await_args_list)
    assert not plain.everyone and not plain.roles and not plain.users
    assert not pinging.everyone and not pinging.roles
    assert [user.id for user in pinging.users] == [4242]
