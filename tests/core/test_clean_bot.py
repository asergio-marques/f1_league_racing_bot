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

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.clean_cog import MAX_DELETIONS, CleanCog
from tests.support.undecorate import undecorate

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


# ── What the log channel records (#482) ─────────────────────────────────

ADMIN_ID = 4242


def _run_by_alex(cog, interaction):
    """*interaction* as the league admin Alex runs it, reaching *cog*'s log channel.

    The command defers before anything else, so the interaction reads as answered and a
    refusal's reply goes by the follow-up.
    """
    cog.bot.output_router.post_log = AsyncMock()
    interaction.client = cog.bot
    interaction.command.qualified_name = "clean-bot"
    interaction.user.id = ADMIN_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(return_value=True)
    return interaction


@pytest.mark.xfail(strict=True, reason="#482: /clean-bot's refusal is not yet recorded")
async def test_a_non_text_channel_refusal_is_recorded():
    """The core specification's "The record of what changed": a refusal is one line naming
    the member, what was refused and why. The reply is today's."""
    cog = _cog()
    interaction = _run_by_alex(cog, _interaction(MagicMock()))  # not a TextChannel

    await _run(cog, interaction, 1)

    interaction.followup.send.assert_awaited_once_with(
        "⛔ This command can only be used in a text channel.", ephemeral=True
    )
    cog.bot.output_router.post_log.assert_awaited_once_with(
        f"⛔ `/clean-bot` refused for Alex (<@{ADMIN_ID}>) — "
        "This command can only be used in a text channel."
    )


@pytest.mark.parametrize(
    ("messages", "count", "deleted", "failed"),
    [
        pytest.param(
            lambda: [
                _message(),
                _message(fails=discord.HTTPException(MagicMock(status=403), "no")),
                _message(),
                _message(),
            ],
            3,
            3,
            1,
            marks=pytest.mark.xfail(
                strict=True, reason="#482: /clean-bot's success is not yet recorded"
            ),
            id="three_deleted_one_would_not",
        ),
        pytest.param(
            lambda: [_message(SOMEONE_ELSE), _message(SOMEONE_ELSE)],
            5,
            0,
            0,
            marks=pytest.mark.xfail(
                strict=True, reason="#482: /clean-bot's success is not yet recorded"
            ),
            id="none_deleted",
        ),
    ],
)
async def test_a_clean_writes_one_line_naming_the_channel_and_the_counts(
    messages, count, deleted, failed
):
    """One success line naming the member, the channel, how many messages were deleted —
    none included — and how many would not delete."""
    cog = _cog()
    interaction = _run_by_alex(cog, _interaction(_channel(messages())))

    await _run(cog, interaction, count)

    cog.bot.output_router.post_log.assert_awaited_once()
    line = cog.bot.output_router.post_log.await_args.args[0]
    lines = [text.strip() for text in line.splitlines()]
    assert lines[0] == f"Alex (<@{ADMIN_ID}>) | /clean-bot | Success"
    assert "channel: <#700>" in lines
    assert f"deleted: {deleted}" in lines
    assert f"could not delete: {failed}" in lines


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
