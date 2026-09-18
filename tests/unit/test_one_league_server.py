"""One bot serves one league: the entry-point check, and the warning to the host.

Issue #244. The league's server is the one `server_configs` row; every command from any other
server is refused before its body runs, and the host is warned when the bot sits in more than
one. The claim itself — that a second server cannot be set up — is pinned in
`test_init_cog.py`, where `/bot-init` and `save_server_config` are.
"""
from __future__ import annotations

import logging
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from bot import create_bot  # noqa: E402
from utils.league_server import (  # noqa: E402
    REFUSAL,
    LeagueCommandTree,
    is_foreign_guild,
    warn_if_serving_several,
)

LEAGUE = 5550
ELSEWHERE = 5551


def _bot(league: int | None):
    bot = create_bot()
    bot.config_service = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=league)
    return bot


def _interaction(guild_id: int | None, *, kind=discord.InteractionType.application_command):
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.type = kind
    interaction.user.id = 7
    interaction.response.send_message = AsyncMock()
    return interaction


# ── The tree ──────────────────────────────────────────────────────────────


async def test_the_bot_is_built_with_the_league_tree():
    assert isinstance(create_bot().tree, LeagueCommandTree)


async def test_a_command_in_the_league_s_server_proceeds():
    bot = _bot(LEAGUE)
    interaction = _interaction(LEAGUE)

    assert await bot.tree.interaction_check(interaction) is True
    interaction.response.send_message.assert_not_awaited()


async def test_a_command_in_another_server_is_refused():
    bot = _bot(LEAGUE)
    interaction = _interaction(ELSEWHERE)

    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)


async def test_a_refused_command_never_runs():
    """The check is the tree's own, so a refusal stops the dispatch before the command.

    `_call` is the tree's entry for every application command. Past the check it would look
    the command up from `interaction.data`, which this interaction does not carry.
    """
    bot = _bot(LEAGUE)
    interaction = _interaction(ELSEWHERE)

    await bot.tree._call(interaction)

    assert interaction.command_failed is True
    interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)


async def test_autocomplete_in_another_server_offers_nothing_and_sends_nothing():
    bot = _bot(LEAGUE)
    interaction = _interaction(ELSEWHERE, kind=discord.InteractionType.autocomplete)

    assert await bot.tree.interaction_check(interaction) is False
    interaction.response.send_message.assert_not_awaited()


async def test_before_any_server_is_set_up_every_server_proceeds():
    """So that `/bot-init` can reach the server that will become the league's."""
    bot = _bot(None)

    assert await bot.tree.interaction_check(_interaction(ELSEWHERE)) is True


async def test_a_direct_message_is_left_to_the_tier_guards():
    bot = _bot(LEAGUE)

    assert await bot.tree.interaction_check(_interaction(None)) is True


# ── The predicate the listeners share ─────────────────────────────────────


async def test_only_another_server_is_foreign():
    bot = _bot(LEAGUE)

    assert await is_foreign_guild(bot, ELSEWHERE) is True
    assert await is_foreign_guild(bot, LEAGUE) is False
    assert await is_foreign_guild(bot, None) is False
    assert await is_foreign_guild(_bot(None), ELSEWHERE) is False


# ── The warning to the host ───────────────────────────────────────────────


def _guild(guild_id: int, name: str):
    return SimpleNamespace(id=guild_id, name=name)


def test_the_host_is_warned_when_the_bot_sits_in_two_servers(caplog):
    bot = SimpleNamespace(guilds=[_guild(ELSEWHERE, "Test"), _guild(LEAGUE, "League")])

    with caplog.at_level(logging.WARNING, logger="utils.league_server"):
        warn_if_serving_several(bot)

    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "2 servers" in record.getMessage()
    # Named in id order, whatever order Discord listed them in.
    assert record.getMessage().index(str(LEAGUE)) < record.getMessage().index(str(ELSEWHERE))


def test_one_server_raises_no_warning(caplog):
    bot = SimpleNamespace(guilds=[_guild(LEAGUE, "League")])

    with caplog.at_level(logging.WARNING, logger="utils.league_server"):
        warn_if_serving_several(bot)

    assert caplog.records == []


# ── The event listeners, which the tree does not see ──────────────────────


def _listener_bot():
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=LEAGUE)
    bot.wizard_service.get_wizard_by_channel = AsyncMock(return_value=None)
    bot.wizard_service.handle_member_remove = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    return bot


def _message_elsewhere():
    message = MagicMock()
    message.author.bot = False
    message.author.id = 7
    message.guild.id = ELSEWHERE
    message.channel.id = 70
    message.delete = AsyncMock()
    return message


async def test_the_reason_listener_ignores_another_server():
    from cogs.admin_review_cog import _PENDING_REASONS, AdminReviewCog

    bot = _listener_bot()
    _PENDING_REASONS[(70, 7)] = {"action": "reject"}
    try:
        await AdminReviewCog(bot).on_message(_message_elsewhere())
        assert (70, 7) in _PENDING_REASONS
    finally:
        _PENDING_REASONS.pop((70, 7), None)


async def test_the_wizard_listener_ignores_another_server():
    from cogs.signup_cog import SignupCog

    cog = SignupCog.__new__(SignupCog)
    cog.bot = _listener_bot()

    await cog.on_message(_message_elsewhere())

    cog.bot.wizard_service.get_wizard_by_channel.assert_not_awaited()


async def test_a_member_leaving_another_server_is_nothing_to_the_league():
    from cogs.signup_cog import SignupCog

    cog = SignupCog.__new__(SignupCog)
    cog.bot = _listener_bot()
    member = MagicMock()
    member.id = 7
    member.guild.id = ELSEWHERE

    await cog.on_member_remove(member)

    cog.bot.wizard_service.handle_member_remove.assert_not_awaited()
    cog.bot.output_router.post_log.assert_not_awaited()


async def test_the_penalty_review_lock_ignores_another_server(monkeypatch):
    from cogs.season_cog import SeasonCog
    from services import result_submission_service

    asked = AsyncMock(return_value=True)
    monkeypatch.setattr(result_submission_service, "is_channel_in_penalty_review", asked)
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = _listener_bot()
    message = _message_elsewhere()

    await cog.on_message(message)

    asked.assert_not_awaited()
    message.delete.assert_not_awaited()
