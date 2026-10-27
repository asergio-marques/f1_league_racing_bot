"""The output router's stop notice, and the one mention it lets notify (#439, 6b).

The router posts no forecast: a forecast is a standing post, sent through the standing-post
handler (#441).

A log line names members and roles without notifying them: the router wraps every mention in code
and sends it with no mention allowed. The queue's stop notice is the one exception: it mentions
the league admins' role, which the router leaves bare and allows, and nothing else.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.services.output_router import OutputRouter
from tests.support.change_queue import text_channel

LOG_CHANNEL_ID = 101
ADMIN_ROLE_ID = 901


def _router() -> tuple[OutputRouter, MagicMock]:
    channel = text_channel(LOG_CHANNEL_ID)
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    bot.config_service.get_server_config = AsyncMock(
        return_value=MagicMock(log_channel_id=LOG_CHANNEL_ID)
    )
    return OutputRouter(bot, bot.config_service), channel


def _role_ids(mentions) -> set[int]:
    roles = getattr(mentions, "roles", False)
    return {getattr(role, "id", role) for role in roles} if isinstance(roles, list) else set()


async def test_a_notice_s_role_mention_is_the_only_one_left_unwrapped():
    """The router posts a stop notice naming member 4242, the role 555 and the league admins' role
    901, which it is given to mention. In the message sent, the admins' role stands bare and is the
    one mention allowed to notify; the member and the other role are wrapped in code and notify
    nobody. Posted with no role to mention, as before, every mention is wrapped and none notifies."""
    router, channel = _router()
    content = f"❌ Stopped <@&{ADMIN_ROLE_ID}> — asked by <@4242>; see <@&555>"

    await router.post_notice(content, MagicMock(), mention_role=ADMIN_ROLE_ID)
    await router.post_notice(content, MagicMock())

    [pinged, silent] = channel.send.await_args_list
    sent = str(pinged.args[0])
    assert f"<@&{ADMIN_ROLE_ID}>" in sent.replace(f"`<@&{ADMIN_ROLE_ID}>`", "")
    assert "`<@4242>`" in sent and "`<@&555>`" in sent
    assert _role_ids(pinged.kwargs["allowed_mentions"]) == {ADMIN_ROLE_ID}
    assert not getattr(pinged.kwargs["allowed_mentions"], "users", False)
    assert not getattr(pinged.kwargs["allowed_mentions"], "everyone", False)

    quiet = str(silent.args[0])
    assert f"`<@&{ADMIN_ROLE_ID}>`" in quiet and "`<@4242>`" in quiet
    assert _role_ids(silent.kwargs["allowed_mentions"]) == set()


def test_the_router_posts_no_forecast():
    """The router writes log lines, the stop notice and the turn-away, and nothing else: it has
    no forecast post, nor the stand-ins for a division's forecast channel that only it read."""
    from leaguebot.core.services import output_router

    assert not hasattr(OutputRouter, "post_forecast")
    assert not hasattr(output_router, "ForecastTarget")
    assert not hasattr(output_router, "ForecastChannel")


async def test_send_log_text_divides_a_long_line_and_notifies_nobody():
    """A log line too long for one message, mentioning member 4242, is handed to the log writer
    for channel 555, as the retry worker hands it, outside any job of a change. It is sent in the
    parts `chunk_message` divides it into, in order, each notifying nobody."""
    from leaguebot.core.services.output_router import send_log_text
    from leaguebot.core.utils.messages import chunk_message

    channel = MagicMock(id=555)
    channel.send = AsyncMock()
    content = "<@4242> did something\n" + "\n".join(f"line {i}" * 40 for i in range(200))

    await send_log_text(channel, content)

    sends = channel.send.await_args_list
    assert len(sends) > 1, "this content was meant to be split"
    assert [send.args[0] for send in sends] == chunk_message(content)
    for send in sends:
        assert send.kwargs["allowed_mentions"].to_dict() == discord.AllowedMentions.none().to_dict()
