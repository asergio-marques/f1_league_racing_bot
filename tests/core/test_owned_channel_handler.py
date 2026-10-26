"""The bot-owned channel handler: posts in a channel the bot made, and the channel's deletion (#441).

A bot-owned channel is one the bot creates for a piece of work and deletes when the work is done,
a round's results submission channel among them. The handler posts in it, saying whom the post may
notify and giving back the message's id, and deletes the channel whole; whatever Discord raises
reaches the caller unchanged (`docs/design/architecture.md`, "Posting to Discord").
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import http_error, in_a_job

NOT_BUILT = "#441: the bot-owned channel handler is not built yet"


def _channel():
    message = MagicMock(id=5150)
    channel = MagicMock(id=900)
    channel.send = AsyncMock(return_value=message)
    channel.delete = AsyncMock()
    return channel, message


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_post_in_an_owned_channel_gives_the_id_and_notifies_only_whom_it_names():
    from leaguebot.core.services.owned_channel_handler import post_in_owned_channel

    channel, message = _channel()
    driver = MagicMock(id=123)
    mentions = discord.AllowedMentions(users=[driver], roles=False, everyone=False)
    with in_a_job():
        posted = await post_in_owned_channel(channel, "<@123>, paste the results", mentions=mentions)

    assert posted == message.id
    call = channel.send.await_args
    assert (call.args[0] if call.args else call.kwargs["content"]) == "<@123>, paste the results"
    assert call.kwargs["allowed_mentions"] is mentions


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_post_in_an_owned_channel_carries_its_buttons():
    from leaguebot.core.services.owned_channel_handler import post_in_owned_channel

    channel, _message = _channel()
    view = MagicMock(name="view")
    mentions = discord.AllowedMentions.none()
    with in_a_job():
        await post_in_owned_channel(channel, "Approve these results?", mentions=mentions, view=view)

    assert channel.send.await_args.kwargs["view"] is view
    assert channel.send.await_args.kwargs["allowed_mentions"] is mentions


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_owned_channel_is_deleted_whole_with_its_reason():
    from leaguebot.core.services.owned_channel_handler import delete_owned_channel

    channel, _message = _channel()
    with in_a_job():
        assert await delete_owned_channel(channel, reason="Round 3 results approved") is None

    channel.delete.assert_awaited_once_with(reason="Round 3 results approved")


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
@pytest.mark.parametrize("error", [http_error(), http_error(discord.NotFound, status=404)],
                         ids=["refused", "gone"])
async def test_the_owned_channel_handler_raises_what_discord_raises(error):
    from leaguebot.core.services.owned_channel_handler import (
        delete_owned_channel,
        post_in_owned_channel,
    )

    channel, _message = _channel()
    channel.send.side_effect = error
    channel.delete.side_effect = error
    with in_a_job():
        with pytest.raises(type(error)) as posted:
            await post_in_owned_channel(channel, "Paste the results",
                                        mentions=discord.AllowedMentions.none())
        with pytest.raises(type(error)) as deleted:
            await delete_owned_channel(channel, reason="done")

    assert posted.value is deleted.value is error


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_owned_channel_post_outside_a_job_is_refused_before_it_is_sent():
    from leaguebot.core.services.owned_channel_handler import (
        delete_owned_channel,
        post_in_owned_channel,
    )
    from leaguebot.core.utils.in_a_job import PostedOutsideAJob

    channel, _message = _channel()
    with pytest.raises(PostedOutsideAJob):
        await post_in_owned_channel(channel, "Paste the results",
                                    mentions=discord.AllowedMentions.none())
    with pytest.raises(PostedOutsideAJob):
        await delete_owned_channel(channel, reason="done")

    channel.send.assert_not_awaited()
    channel.delete.assert_not_awaited()
