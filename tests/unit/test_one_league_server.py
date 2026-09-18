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
