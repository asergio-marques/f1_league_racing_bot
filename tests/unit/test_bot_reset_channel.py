"""`/bot-reset` is given in the interaction channel.

The one channel change in the two-tier work (issue #116), and the reason it is worth a test
of its own: the command deletes a league's seasons, divisions, rounds, results and history
entire, and until now it carried no channel guard at all. Anybody holding the Discord
permission could fire it from any channel on the server.

The old reasoning was that a full reset deletes the `server_configs` row, so the configured
channel no longer exists once it has run. That is true and beside the point — the guard
reads the configuration before the command runs, and the row is still there at that moment.

It does not share `/bot-init`'s exemption either. The setup commands run from anywhere
because they *repair* the settings the guards read. A reset destroys them, which is the
opposite errand, and a league that has reset itself runs `/bot-init` again — which is exempt.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.reset_cog import ResetCog  # noqa: E402
from models.server_config import ServerConfig  # noqa: E402
from utils.channel_guard import CHANNEL_EXEMPT_ATTRIBUTE, LEAGUE_ADMIN, TIER_ATTRIBUTE  # noqa: E402

SERVER_ID = 4242
CHANNEL = 111
ADMIN_ROLE = 444


def _config() -> ServerConfig:
    return ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=222,
        league_admin_role_id=ADMIN_ROLE,
        interaction_channel_id=CHANNEL,
        log_channel_id=333,
    )


def _cog() -> ResetCog:
    bot = MagicMock()
    bot.db_path = ":memory:"
    bot.config_service.get_server_config = AsyncMock(return_value=_config())
    bot.output_router.post_log = AsyncMock()
    return ResetCog(bot)


def _interaction(*, channel_id: int) -> MagicMock:
    role = MagicMock()
    role.id = ADMIN_ROLE
    role.name = "Owners"

    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = channel_id
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = 7
    interaction.user.display_name = "owner"
    interaction.user.roles = [role]
    interaction.guild.get_role = lambda role_id: role if role_id == ADMIN_ROLE else None
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


async def test_a_reset_from_another_channel_is_refused_and_deletes_nothing(monkeypatch):
    cog = _cog()
    interaction = _interaction(channel_id=CHANNEL + 1)

    reset = AsyncMock()
    monkeypatch.setattr("services.reset_service.reset_server_data", reset)

    await ResetCog.handle_bot_reset.callback(cog, interaction, "CONFIRM", True)

    assert "interaction channel" in interaction.response.send_message.call_args.args[0]
    reset.assert_not_awaited()


async def test_the_confirmation_word_is_still_required_in_the_right_channel(monkeypatch):
    """Proving the refusal above is the channel guard and not the confirmation gate."""
    cog = _cog()
    interaction = _interaction(channel_id=CHANNEL)

    reset = AsyncMock()
    monkeypatch.setattr("services.reset_service.reset_server_data", reset)

    await ResetCog.handle_bot_reset.callback(cog, interaction, "confirm", True)

    assert "Reset aborted" in interaction.response.send_message.call_args.args[0]
    reset.assert_not_awaited()


@pytest.mark.parametrize("roles", [[], None], ids=["no roles", "roles absent"])
async def test_a_reset_is_refused_to_a_member_without_the_league_admin_role(
    monkeypatch, roles
):
    cog = _cog()
    interaction = _interaction(channel_id=CHANNEL)
    interaction.user.roles = roles if roles is not None else []

    reset = AsyncMock()
    monkeypatch.setattr("services.reset_service.reset_server_data", reset)

    await ResetCog.handle_bot_reset.callback(cog, interaction, "CONFIRM", True)

    assert "league admin's" in interaction.response.send_message.call_args.args[0]
    reset.assert_not_awaited()


def test_the_reset_is_a_league_admin_command_bound_to_the_channel():
    callback = ResetCog.handle_bot_reset.callback
    assert getattr(callback, TIER_ATTRIBUTE) == LEAGUE_ADMIN
    assert getattr(callback, CHANNEL_EXEMPT_ATTRIBUTE) is False
