"""The output router's stop notice, and the one mention it lets notify (#439, 6b).

A log line names members and roles without notifying them: the router wraps every mention in code
and sends it with no mention allowed. The queue's stop notice is the one exception: it mentions
the league admins' role, which the router leaves bare and allows, and nothing else.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.services.output_router import OutputRouter
from tests.support.change_queue import text_channel

LOG_CHANNEL_ID = 101
ADMIN_ROLE_ID = 901

NO_NOTICE_ROLE = "#439: the router's stop notice takes no role to mention"


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


@pytest.mark.xfail(strict=True, reason=NO_NOTICE_ROLE)
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
