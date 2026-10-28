"""`/clean-bot` deletes what it was asked for, and stops there.

The command took no parameter and swept five hundred messages. It now asks how many and
refuses more than ten, and these pin the part that makes that meaningful: it never touches a
message somebody else wrote, and the count is required and bounded. That it stops at the count,
newest first, and counts *deletions* rather than messages looked at is pinned through the queue
in `test_clean_change.py`. Deletion on Discord has no undo, so a command that overshoots by one
is a message a league does not get back.

This is the file the command never had — which is how a five-hundred-message sweep with
no confirmation went unremarked for as long as it did.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.cogs.clean_cog import MAX_DELETIONS, CleanCog
from leaguebot.core.db.database import run_migrations
from tests.support.change_queue import (
    INTERACTION_CHANNEL_ID,
    LOG_CHANNEL_ID,
    Clock,
    attach_queue,
    history_channel,
    league_double,
    member_interaction,
    post_in,
    run_queue,
    seed_server,
)
from tests.support.undecorate import undecorate

BOT_USER = MagicMock(name="bot_user")
SOMEONE_ELSE = SimpleNamespace(id=105, name="Kimi")
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)

@pytest.fixture
async def league(tmp_path):
    """The league's queue, with every change type the builder registers, and channel 700, which
    holds messages `post_in` puts there, newest first, found by the bot and by its server."""
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.user = BOT_USER
    channel = history_channel(700)
    known = {LOG_CHANNEL_ID: bot.log_channel, INTERACTION_CHANNEL_ID: bot.interaction_channel,
             700: channel}
    guild = MagicMock(spec=discord.Guild)
    guild.id = 12408
    guild.me = BOT_USER
    guild.get_channel = MagicMock(side_effect=known.get)
    channel.guild = guild
    bot.get_channel = MagicMock(side_effect=known.get)
    bot.get_guild = MagicMock(return_value=guild)
    attach_queue(bot, db_path, now=Clock(NOW))
    return SimpleNamespace(db_path=db_path, bot=bot, channel=channel)


async def _clean(league, count) -> Any:
    """The league admin Admin (4242) runs `/clean-bot` for *count* in channel 700, and the queue
    runs. Gives the interaction."""
    cog = CleanCog.__new__(CleanCog)
    cog.bot = league.bot
    interaction = member_interaction(league.bot)
    interaction.channel = league.channel
    interaction.channel_id = 700
    interaction.command = SimpleNamespace(qualified_name="clean-bot")
    interaction.followup.send.return_value = MagicMock(edit=AsyncMock())
    await undecorate(CleanCog.clean_bot)(cog, interaction, count)
    await run_queue(league.bot)
    return interaction


def _said(interaction) -> list[Any]:
    """Every answer Admin was given, in order: the first, then each update of it."""
    return (interaction.response.send_message.await_args_list
            + interaction.followup.send.await_args_list
            + interaction.edit_original_response.await_args_list
            + interaction.followup.send.return_value.edit.await_args_list)


def _last_said(interaction) -> str:
    call = _said(interaction)[-1]
    return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))


def _interaction(channel):
    """The admin's run of the command. Its client's log channel takes a line, as a real one
    does, so a command that records its outcome is not stopped by the test's own stand-in."""
    interaction = MagicMock()
    interaction.guild_id = 55
    interaction.channel = channel
    interaction.user = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    interaction.client.output_router.post_log = AsyncMock()
    return interaction


def _cog():
    """The cog, its bot's log channel taking a line as a real one does."""
    cog = CleanCog.__new__(CleanCog)
    cog.bot = MagicMock()
    cog.bot.user = BOT_USER
    cog.bot.output_router.post_log = AsyncMock()
    return cog


async def _run(cog, interaction, count):
    """The command body, past whatever tier guard it wears."""
    body = undecorate(CleanCog.clean_bot)
    await body(cog, interaction, count)


def _reply(interaction) -> str:
    return interaction.followup.send.await_args.args[0]


async def test_it_never_deletes_a_message_somebody_else_wrote(league):
    """The bot's message, then Kimi's, newest. Admin cleans 5: the bot's goes, Kimi's stands."""
    ours = post_in(league.channel, BOT_USER)
    theirs = post_in(league.channel, SOMEONE_ELSE)

    await _clean(league, 5)

    assert league.channel.deleted == [ours]
    assert league.channel.messages == [theirs]


async def test_fewer_bot_messages_than_asked_for_is_reported_not_failed(league):
    """One of the bot's messages stands; Admin cleans 5. The reply says one was deleted, and that
    only one of the messages looked at was the bot's."""
    post_in(league.channel, BOT_USER)

    interaction = await _clean(league, 5)

    reply = _last_said(interaction)
    assert "Deleted 1" in reply
    assert "Only 1" in reply


async def test_the_reply_is_ephemeral(league):
    """A tidy-up that announces itself to the channel defeats the tidying: the first answer Admin
    is given, the acknowledgement, is seen by Admin alone."""
    post_in(league.channel, BOT_USER)

    interaction = await _clean(league, 1)

    assert _said(interaction)[0].kwargs["ephemeral"] is True


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
    ("authors", "count", "deleted"),
    [
        pytest.param((BOT_USER, BOT_USER, BOT_USER), 3, 3, id="three_deleted"),
        pytest.param((SOMEONE_ELSE, SOMEONE_ELSE), 5, 0, id="none_deleted"),
    ],
)
async def test_a_clean_writes_one_line_naming_the_channel_and_the_counts(
    league, authors, count, deleted
):
    """Channel 700 holds the messages of *authors*; Admin cleans *count*. One success line in the
    log channel names Admin, the channel and how many messages were deleted, none included, and
    that none would not delete."""
    for author in authors:
        post_in(league.channel, author)

    await _clean(league, count)

    [line] = [sent for sent in league.bot.log_channel.sent if "| /clean-bot |" in sent]
    lines = [text.strip() for text in line.splitlines()]
    assert lines[0] == "Admin (`<@4242>`) | /clean-bot | Success"
    assert "channel: <#700>" in lines
    assert f"deleted: {deleted}" in lines
    assert "could not delete: 0" in lines


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
