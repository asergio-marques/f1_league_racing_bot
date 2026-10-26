"""The output router's stop notice, and the one mention it lets notify (#439, 6b).

The router's forecast post is made in the forecast's job, and refuses to run outside one.

A log line names members and roles without notifying them: the router wraps every mention in code
and sends it with no mention allowed. The queue's stop notice is the one exception: it mentions
the league admins' role, which the router leaves bare and allows, and nothing else.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.services.output_router import OutputRouter
from tests.support.change_queue import in_a_job, text_channel

LOG_CHANNEL_ID = 101
ADMIN_ROLE_ID = 901

DIVISION_ROLE_ID = 801


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


async def test_the_router_s_forecast_post_is_made_in_a_job():
    """The router is asked, outside every job, to post a forecast to division channel 701: it
    raises `PostedOutsideAJob` and sends nothing. A forecast is posted by its change's job, and
    only there."""
    from leaguebot.core.utils.in_a_job import PostedOutsideAJob

    router, channel = _router()

    with pytest.raises(PostedOutsideAJob, match="post_forecast"):
        await router.post_forecast(MagicMock(forecast_channel_id=701), "☀️ Sunny")

    channel.send.assert_not_awaited()


async def test_a_forecast_s_allowed_mentions_are_passed_to_every_part():
    """The router is asked, in a job, to post a forecast too long for one message to division
    channel 701, letting the division's role 801 notify. It is sent in more than one part, and
    every part is sent with those mentions: role 801 allowed, no member and not everyone."""
    router, channel = _router()
    mentions = discord.AllowedMentions(
        everyone=False, users=False, roles=[discord.Object(DIVISION_ROLE_ID)]
    )
    content = "\n".join(f"line {i}" * 40 for i in range(200))

    with in_a_job():
        await router.post_forecast(MagicMock(forecast_channel_id=701), content, mentions=mentions)

    sends = channel.send.await_args_list
    assert len(sends) > 1, "this content was meant to be split"
    for send in sends:
        allowed = send.kwargs["allowed_mentions"]
        assert _role_ids(allowed) == {DIVISION_ROLE_ID}
        assert not getattr(allowed, "users", False)
        assert not getattr(allowed, "everyone", False)


@pytest.mark.xfail(strict=True, reason="#441: the router has no send_log_text for the retry to send through")
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
