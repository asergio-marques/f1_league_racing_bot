"""The notice handler: the one place core sends, edits and deletes a notice (#441).

A notice is a message posted to say something to whoever is waiting in a channel, the "drawing
graphics" notice among them (`docs/design/architecture.md`, "Posting to Discord"). The handler
sends and nothing else: the caller says whom the notice may notify, the handler gives back the
message, and whatever Discord raises reaches the caller unchanged, so the change queue's own
handling of a refusal goes on as before.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from tests.support.change_queue import http_error, in_a_job

NOT_BUILT = "#441: the notice handler is not built yet"


def _channel():
    message = MagicMock(id=9001)
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel = MagicMock(id=700)
    channel.send = AsyncMock(return_value=message)
    return channel, message


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_send_notice_sends_the_text_with_the_mentions_given_and_gives_the_message():
    from leaguebot.core.services.notice_handler import send_notice

    channel, message = _channel()
    mentions = discord.AllowedMentions.none()
    with in_a_job():
        sent = await send_notice(channel, "Drawing the graphics…", mentions=mentions)

    assert sent is message
    channel.send.assert_awaited_once_with("Drawing the graphics…", allowed_mentions=mentions)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_send_notice_passes_on_a_file_a_view_and_silent():
    from leaguebot.core.services.notice_handler import send_notice

    channel, _message = _channel()
    mentions = discord.AllowedMentions.none()
    picture, view = MagicMock(name="file"), MagicMock(name="view")
    with in_a_job():
        await send_notice(channel, "The lineup", mentions=mentions, file=picture, view=view,
                          silent=True)

    channel.send.assert_awaited_once_with(
        "The lineup", allowed_mentions=mentions, file=picture, view=view, silent=True
    )


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_send_notice_cannot_be_called_without_saying_who_it_may_notify():
    from leaguebot.core.services.notice_handler import send_notice

    channel, _message = _channel()
    with in_a_job(), pytest.raises(TypeError):
        await send_notice(channel, "Drawing the graphics…")
    channel.send.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_edit_notice_edits_the_message_with_the_mentions_given():
    from leaguebot.core.services.notice_handler import edit_notice

    _channel_, message = _channel()
    mentions = discord.AllowedMentions(roles=[MagicMock(id=555)], users=False, everyone=False)
    with in_a_job():
        result = await edit_notice(message, mentions=mentions, content="Check-in closes soon")

    assert result is None
    message.edit.assert_awaited_once_with(content="Check-in closes soon", allowed_mentions=mentions)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
@pytest.mark.parametrize("error", [http_error(), http_error(discord.NotFound, status=404)],
                         ids=["refused", "gone"])
async def test_a_notice_handler_raises_what_discord_raises(error):
    from leaguebot.core.services.notice_handler import delete_notice, edit_notice, send_notice

    channel, message = _channel()
    channel.send.side_effect = error
    message.edit.side_effect = error
    message.delete.side_effect = error
    mentions = discord.AllowedMentions.none()
    with in_a_job():
        with pytest.raises(type(error)) as sent:
            await send_notice(channel, "Drawing the graphics…", mentions=mentions)
        with pytest.raises(type(error)) as edited:
            await edit_notice(message, mentions=mentions, content="…")
        with pytest.raises(type(error)) as deleted:
            await delete_notice(message)

    assert sent.value is edited.value is deleted.value is error


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_notice_outside_a_job_is_refused_before_it_is_sent():
    from leaguebot.core.services.notice_handler import delete_notice, edit_notice, send_notice
    from leaguebot.core.utils.in_a_job import PostedOutsideAJob

    channel, message = _channel()
    mentions = discord.AllowedMentions.none()
    with pytest.raises(PostedOutsideAJob):
        await send_notice(channel, "Drawing the graphics…", mentions=mentions)
    with pytest.raises(PostedOutsideAJob):
        await edit_notice(message, mentions=mentions, content="…")
    with pytest.raises(PostedOutsideAJob):
        await delete_notice(message)

    channel.send.assert_not_awaited()
    message.edit.assert_not_awaited()
    message.delete.assert_not_awaited()
