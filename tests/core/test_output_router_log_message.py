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

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.services.output_router import OutputRouter


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

    message = await router.post_log("something happened")

    assert message is not None
    assert message.jump_url == "http://j/0"


@pytest.mark.asyncio
async def test_a_split_block_returns_its_first_message():
    """So a link lands a reader at the top of the block rather than its tail."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    message = await router.post_log("\n".join(f"line {i}" * 40 for i in range(200)))

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

    assert await OutputRouter(bot).post_log("anything") is None


@pytest.mark.asyncio
async def test_post_log_returns_none_when_the_channel_cannot_be_reached():
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=None)
    bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=99, interaction_channel_id=98)
    )

    assert await OutputRouter(bot).post_log("anything") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mention", ["<@123>", "<@!123>", "<@&123>"])
async def test_every_mention_in_a_log_line_is_written_as_code(mention):
    """Named without notifying: the log channel mentions nobody (#362 builds the forms from the
    shared ones, which also wraps `<@!123>`)."""
    sent = []
    router = OutputRouter(_bot(_channel(sent)))

    await router.post_log(f"{mention} did something")

    assert f"`{mention}`" in sent[0]


async def test_a_forecast_for_a_division_with_no_forecast_channel_posts_nothing():
    """Nothing to post to. It came to the same before — the router asked Discord for a channel
    with no id and logged the failure — less the request (#228)."""
    from leaguebot.core.services.output_router import ForecastChannel

    bot = MagicMock()
    bot.fetch_channel = AsyncMock()
    router = OutputRouter(bot)

    assert await router.post_forecast(ForecastChannel(None), "forecast") is None
    bot.get_channel.assert_not_called()
    bot.fetch_channel.assert_not_awaited()


# ── The factory reset's closing line (#482) ───────────────────────────────
#
# A factory reset posts one line once its clean-up has finished, after the wipe has taken the
# configuration and with it any way to find the log channel. The cog asks the router where the
# log goes before the wipe, and hands that back with the line. A line that cannot be posted is
# not queued: a queued row would sit in the fresh database of a bot serving no server. Nor does
# it fall back to the interaction channel, which no configuration is left to name.


def _unconfigured_bot(channel) -> MagicMock:
    """A bot just wiped: no configuration, but the channel it held is still in Discord."""
    bot = _bot(channel)
    bot.config_service.get_server_config = AsyncMock(return_value=None)
    return bot


async def test_the_router_says_where_the_log_goes():
    router = OutputRouter(_bot(_channel([])))

    assert await router.log_destination() == 99


async def test_the_router_says_there_is_no_log_before_the_bot_is_set_up():
    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=None)

    assert await OutputRouter(bot).log_destination() is None


async def test_a_line_for_a_given_channel_is_written_as_any_other():
    """Its mentions name without notifying and it carries the separator, with no configuration
    left to read."""
    sent = []
    bot = _unconfigured_bot(_channel(sent))

    message = await OutputRouter(bot).post_log("<@77> reset the league", channel=555)

    assert message is not None
    bot.get_channel.assert_called_once_with(555)
    [line] = sent
    assert line.startswith("`<@77>` reset the league")
    assert line.endswith("\n" + "―" * 36)


async def test_a_long_line_for_a_given_channel_is_split_and_returns_its_first_message():
    sent = []
    bot = _unconfigured_bot(_channel(sent))

    message = await OutputRouter(bot).post_log(
        "\n".join(f"line {i}" * 40 for i in range(200)), channel=555
    )

    assert len(sent) > 1, "this content was meant to be split"
    assert message.jump_url == "http://j/0"


def _refusing_channel() -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403, reason="Forbidden"), "no access")
    )
    return channel


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(
            "refuses",
            id="channel-refuses-the-post",
        ),
        pytest.param(
            "gone",
            id="channel-is-gone",
        ),
    ],
)
async def test_a_line_for_a_given_channel_that_fails_is_neither_queued_nor_redirected(
    tmp_path, monkeypatch, where
):
    """No retry row and no notice in the interaction channel: the caller puts the line in the
    host's log instead."""
    from leaguebot.core.services import retry_service

    enqueue = AsyncMock()
    monkeypatch.setattr(retry_service, "enqueue", enqueue)
    bot = _bot(_refusing_channel())
    if where == "gone":
        bot.get_channel = MagicMock(return_value=None)
        bot.fetch_channel = AsyncMock(side_effect=discord.NotFound(MagicMock(), "gone"))
    router = OutputRouter(bot, retry_db_path=str(tmp_path / "retry.db"))

    assert await router.post_log("the league was reset", channel=555) is None

    enqueue.assert_not_awaited()
    assert [call.args[0] for call in bot.get_channel.call_args_list] == [555]
