"""`OutputRouter.post_log` returns the message it posted.

It always held one and discarded it, while its sibling `post_forecast` returned one. A
caller that has just written a block to the log can now point a reader at it — which is
what `/images test` does with its notices.

The **first** message is returned where the content had to be split across Discord's
limit: a link is meant to land a reader at the top of the block, not at its tail. That is
the one respect in which it differs from `post_forecast`, whose callers store the returned
id to edit that message later — returning a different one there would repoint those edits,
so the difference is opt-in rather than a change to the shared sender.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.output_router import OutputRouter  # noqa: E402


def _channel(sent):
    channel = MagicMock(spec=discord.TextChannel)

    async def _send(content, **_kwargs):
        message = MagicMock(name=f"msg{len(sent)}", jump_url=f"http://j/{len(sent)}")
        sent.append(content)
        return message

    channel.send = AsyncMock(side_effect=_send)
    return channel


def _bot(channel):
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=99, interaction_channel_id=98)
    )
    return bot


@pytest.mark.asyncio
async def test_post_log_returns_the_posted_message():
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    message = await router.post_log(1, "something happened")

    assert message is not None
    assert message.jump_url == "http://j/0"


@pytest.mark.asyncio
async def test_a_split_block_returns_its_first_message():
    """So a link lands a reader at the top of the block rather than its tail."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    message = await router.post_log(1, "\n".join(f"line {i}" * 40 for i in range(200)))

    assert len(sent) > 1, "this content was meant to be split"
    assert message.jump_url == "http://j/0"


@pytest.mark.asyncio
async def test_post_forecast_still_returns_its_last_message():
    """Its callers store the id to edit that message later (calendar, constructor
    standings). Returning a different one would repoint those edits."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))
    division = MagicMock(forecast_channel_id=99)

    message = await router.post_forecast(
        division, "\n".join(f"line {i}" * 40 for i in range(200))
    )

    assert len(sent) > 1, "this content was meant to be split"
    assert message.jump_url == f"http://j/{len(sent) - 1}"


@pytest.mark.asyncio
async def test_post_log_returns_none_when_the_server_has_no_config():
    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=None)

    assert await OutputRouter(bot).post_log(1, "anything") is None


@pytest.mark.asyncio
async def test_post_log_returns_none_when_the_channel_cannot_be_reached():
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=99, interaction_channel_id=98)
    )

    assert await OutputRouter(bot).post_log(1, "anything") is None
