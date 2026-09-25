"""`/clean-bot` deletes what it was asked for, and stops there.

The command took no parameter and swept five hundred messages. It now asks how many and
refuses more than ten, and these pin the part that makes that meaningful: it stops at the
count, it counts *deletions* rather than messages looked at, and it never touches a
message somebody else wrote. Deletion on Discord has no undo, so a command that overshoots
by one is a message a league does not get back.

This is the file the command never had — which is how a five-hundred-message sweep with
no confirmation went unremarked for as long as it did.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.clean_cog import MAX_DELETIONS, CleanCog  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

BOT_USER = MagicMock(name="bot_user")
SOMEONE_ELSE = MagicMock(name="a_person")


def _message(author=BOT_USER, *, fails: Exception | None = None):
    message = MagicMock()
    message.author = author
    message.delete = AsyncMock(side_effect=fails)
    return message


def _channel(messages):
    """A TextChannel whose history yields *messages*, newest first."""
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 700

    async def history(*, limit):
        for message in messages[:limit]:
            yield message

    channel.history = history
    return channel


def _interaction(channel):
    interaction = MagicMock()
    interaction.guild_id = 55
    interaction.channel = channel
    interaction.user = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _cog():
    cog = CleanCog.__new__(CleanCog)
    cog.bot = MagicMock()
    cog.bot.user = BOT_USER
    return cog


async def _run(cog, interaction, count):
    """The command body, past whatever tier guard it wears."""
    body = undecorate(CleanCog.clean_bot)
    await body(cog, interaction, count)


def _reply(interaction) -> str:
    return interaction.followup.send.await_args.args[0]


async def test_it_deletes_exactly_the_number_asked_for():
    """The whole point of the parameter."""
    messages = [_message() for _ in range(10)]
    interaction = _interaction(_channel(messages))

    await _run(_cog(), interaction, 3)

    assert sum(m.delete.await_count for m in messages) == 3


async def test_it_deletes_the_most_recent_first():
    """`history` yields newest first, so the tail of a command just run is what goes."""
    messages = [_message() for _ in range(5)]
    interaction = _interaction(_channel(messages))

    await _run(_cog(), interaction, 2)

    messages[0].delete.assert_awaited_once()
    messages[1].delete.assert_awaited_once()
    for untouched in messages[2:]:
        untouched.delete.assert_not_awaited()


async def test_it_never_deletes_a_message_somebody_else_wrote():
    """A channel a league talks in holds other people's messages between the bot's."""
    theirs = _message(SOMEONE_ELSE)
    ours = _message()
    interaction = _interaction(_channel([theirs, ours]))

    await _run(_cog(), interaction, 5)

    theirs.delete.assert_not_awaited()
    ours.delete.assert_awaited_once()


async def test_a_person_s_messages_do_not_consume_the_count():
    """The count is of deletions, not of messages looked at."""
    messages = [_message(SOMEONE_ELSE), _message(), _message(SOMEONE_ELSE), _message()]
    interaction = _interaction(_channel(messages))

    await _run(_cog(), interaction, 2)

    assert messages[1].delete.await_count == 1
    assert messages[3].delete.await_count == 1


async def test_fewer_bot_messages_than_asked_for_is_reported_not_failed():
    messages = [_message()]
    interaction = _interaction(_channel(messages))

    await _run(_cog(), interaction, 5)

    reply = _reply(interaction)
    assert "Deleted 1" in reply
    assert "Only 1" in reply


async def test_a_message_already_gone_does_not_consume_the_count():
    """Deleted by hand between the history read and the delete."""
    gone = _message(fails=discord.NotFound(MagicMock(status=404), "gone"))
    survivor = _message()
    interaction = _interaction(_channel([gone, survivor]))

    await _run(_cog(), interaction, 1)

    survivor.delete.assert_awaited_once(), "a vanished message ate the deletion"


async def test_a_refused_delete_is_counted_and_reported():
    refused = _message(fails=discord.HTTPException(MagicMock(status=403), "no"))
    interaction = _interaction(_channel([refused]))

    await _run(_cog(), interaction, 1)

    assert "could not be deleted" in _reply(interaction)


async def test_the_reply_is_ephemeral():
    """A tidy-up that announces itself to the channel defeats the tidying."""
    interaction = _interaction(_channel([_message()]))

    await _run(_cog(), interaction, 1)

    assert interaction.followup.send.await_args.kwargs["ephemeral"] is True


async def test_a_non_text_channel_is_refused():
    interaction = _interaction(MagicMock())  # not a TextChannel

    await _run(_cog(), interaction, 1)

    assert "text channel" in _reply(interaction)


# ── The bound itself ──────────────────────────────────────────────────────


def test_the_count_is_required_and_bounded():
    """Discord enforces the range client-side, so the declaration *is* the guard.

    A parameter that acquired a default would silently restore the old behaviour of a
    command run with no thought about how much it was about to delete.
    """
    from discord import app_commands

    parameter = CleanCog.clean_bot.parameters[0]

    assert parameter.name == "count"
    assert parameter.required, "a default would let it be run without a thought"
    assert parameter.min_value == 1
    assert parameter.max_value == MAX_DELETIONS


def test_the_cap_is_ten():
    """Named rather than inlined so the describe text and the range cannot disagree."""
    assert MAX_DELETIONS == 10
