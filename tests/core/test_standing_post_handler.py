"""The standing-post handler: the one place core posts, edits and deletes a standing post (#441).

A standing post is one the bot keeps up to date in place: a calendar, a team lineup, the hub
panel. The handler posts it and gives back its id for the caller to keep, edits it, and deletes
the one it replaces, saying each time whom the post may notify and letting whatever Discord raises
reach the caller unchanged (`docs/design/architecture.md`, "Posting to Discord").
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import http_error, in_a_job


def _channel():
    message = MagicMock(id=4242)
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel = MagicMock(id=800)
    channel.send = AsyncMock(return_value=message)
    return channel, message


def _content(call):
    return call.args[0] if call.args else call.kwargs.get("content")


async def test_post_standing_sends_text_an_embed_or_a_file_with_the_mentions_given_and_gives_the_id():
    from leaguebot.core.services.standing_post_handler import post_standing

    mentions = discord.AllowedMentions.none()
    embed, picture = discord.Embed(title="Lineup"), MagicMock(name="file")

    channel, message = _channel()
    with in_a_job():
        assert await post_standing(channel, "Round 1 — Monza", mentions=mentions) == message.id
    call = channel.send.await_args
    assert _content(call) == "Round 1 — Monza"
    assert call.kwargs["allowed_mentions"] is mentions

    channel, message = _channel()
    with in_a_job():
        assert await post_standing(channel, mentions=mentions, embed=embed) == message.id
    call = channel.send.await_args
    assert call.kwargs["embed"] is embed and not _content(call)
    assert call.kwargs["allowed_mentions"] is mentions

    channel, message = _channel()
    with in_a_job():
        assert await post_standing(channel, mentions=mentions, file=picture) == message.id
    call = channel.send.await_args
    assert call.kwargs["file"] is picture
    assert call.kwargs["allowed_mentions"] is mentions


async def test_edit_standing_edits_with_the_mentions_given():
    from leaguebot.core.services.standing_post_handler import edit_standing

    _channel_, message = _channel()
    mentions = discord.AllowedMentions.none()
    view = MagicMock(name="view")
    with in_a_job():
        result = await edit_standing(message, mentions=mentions, content="The hub", view=view)

    assert result is None
    message.edit.assert_awaited_once()
    call = message.edit.await_args
    assert call.kwargs["content"] == "The hub"
    assert call.kwargs["view"] is view
    assert call.kwargs["allowed_mentions"] is mentions


async def test_edit_standing_takes_a_part_off_when_given_as_none_and_leaves_one_not_given():
    """The hub panel loses its buttons when nothing is offered: a part given as None reaches
    the edit and takes it off, while a part not given is left out and stays as it stands."""
    from leaguebot.core.services.standing_post_handler import edit_standing

    mentions = discord.AllowedMentions.none()
    _channel_, message = _channel()
    with in_a_job():
        await edit_standing(message, mentions=mentions, content="x", view=None)
    call = message.edit.await_args
    assert call.kwargs["content"] == "x"
    assert "view" in call.kwargs and call.kwargs["view"] is None

    _channel_, message = _channel()
    with in_a_job():
        await edit_standing(message, mentions=mentions, content="x")
    call = message.edit.await_args
    assert call.kwargs["content"] == "x"
    assert "view" not in call.kwargs


@pytest.mark.parametrize("error", [http_error(), http_error(discord.NotFound, status=404)],
                         ids=["refused", "gone"])
async def test_delete_standing_deletes_and_raises_what_discord_raises(error):
    from leaguebot.core.services.standing_post_handler import (
        delete_standing,
        edit_standing,
        post_standing,
    )

    _channel_, message = _channel()
    with in_a_job():
        assert await delete_standing(message) is None
    message.delete.assert_awaited_once_with()

    channel, message = _channel()
    channel.send.side_effect = error
    message.edit.side_effect = error
    message.delete.side_effect = error
    mentions = discord.AllowedMentions.none()
    with in_a_job():
        with pytest.raises(type(error)) as posted:
            await post_standing(channel, "Round 1", mentions=mentions)
        with pytest.raises(type(error)) as edited:
            await edit_standing(message, mentions=mentions, content="Round 1")
        with pytest.raises(type(error)) as deleted:
            await delete_standing(message)

    assert posted.value is edited.value is deleted.value is error


async def test_a_standing_post_cannot_be_made_without_saying_who_it_may_notify():
    from leaguebot.core.services.standing_post_handler import edit_standing, post_standing

    channel, message = _channel()
    with in_a_job():
        with pytest.raises(TypeError):
            await post_standing(channel, "Round 1")
        with pytest.raises(TypeError):
            await edit_standing(message, content="Round 1")

    channel.send.assert_not_awaited()
    message.edit.assert_not_awaited()


async def test_a_standing_post_outside_a_job_is_refused_before_it_is_sent():
    from leaguebot.core.services.standing_post_handler import (
        delete_standing,
        edit_standing,
        post_standing,
    )
    from leaguebot.core.utils.in_a_job import PostedOutsideAJob

    channel, message = _channel()
    mentions = discord.AllowedMentions.none()
    with pytest.raises(PostedOutsideAJob):
        await post_standing(channel, "Round 1", mentions=mentions)
    with pytest.raises(PostedOutsideAJob):
        await edit_standing(message, mentions=mentions, content="Round 1")
    with pytest.raises(PostedOutsideAJob):
        await delete_standing(message)

    channel.send.assert_not_awaited()
    message.edit.assert_not_awaited()
    message.delete.assert_not_awaited()
