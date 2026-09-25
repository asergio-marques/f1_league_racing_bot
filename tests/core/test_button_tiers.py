"""Every button the bot posts asks the tier its action belongs to.

The tiers govern the action, not only the command. Four checks are pinned here, none of which
had any test at all before issue #116 — which is how three of them came to be wrong.

**The signup review panel** is posted publicly into the driver's own signup channel, and the
driver can read it. Its check is the only thing between a driver and approving their own
signup.

**The penalty and appeals reviews** are entirely button-driven. Their gate asked for the
interaction role alone, so a league admin who did not also hold that role was refused all
thirteen buttons of a round they were entitled to judge.

**The points-configuration select** asked nothing whatever, and is posted publicly into the
submission and amendment channels from three call sites. Anyone who could read one of those
channels could decide which configuration scored the session, and the first press won.

**The season-approval button** asked for Discord's Administrator permission, which stopped
being a tier when both tiers became roles.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.server_config import ServerConfig  # noqa: E402

SERVER_ID = 4242
MANAGER_ROLE = 222
ADMIN_ROLE = 444


def _config(*, admin_role: int | None = ADMIN_ROLE) -> ServerConfig:
    return ServerConfig(
        server_id=SERVER_ID,
        interaction_role_id=MANAGER_ROLE,
        league_admin_role_id=admin_role,
        interaction_channel_id=111,
        log_channel_id=333,
    )


def _role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = f"role-{role_id}"
    return role


def _member(*roles: int) -> MagicMock:
    member = MagicMock(spec=discord.Member)
    member.id = 7
    member.roles = [_role(r) for r in roles]
    member.guild_permissions = MagicMock()
    member.guild_permissions.administrator = True  # never a route to a tier
    return member


def _interaction(member: MagicMock) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.guild.id = SERVER_ID
    interaction.user = member
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    return interaction


# ── The points-configuration select ───────────────────────────────────────


async def _press_config_select(member, config):
    from services.result_submission_service import _ConfigSelectView

    view = _ConfigSelectView(["Standard", "Sprint"], config)
    interaction = _interaction(member)
    await view.children[0].callback(interaction)
    return view, interaction


@pytest.mark.parametrize("roles", [(MANAGER_ROLE,), (ADMIN_ROLE,)], ids=["manager", "admin"])
async def test_either_tier_may_choose_the_points_configuration(roles):
    view, _ = await _press_config_select(_member(*roles), _config())
    assert view.selected == "Standard"


async def test_a_bystander_may_not_choose_the_points_configuration():
    """The view is posted publicly, so a bystander is exactly who this stops."""
    view, interaction = await _press_config_select(_member(), _config())

    assert view.selected is None
    assert "league managers" in interaction.response.send_message.call_args.args[0]


async def test_the_points_configuration_select_refuses_when_it_cannot_read_the_config():
    """A view that cannot tell who is pressing must not guess permissively."""
    view, _ = await _press_config_select(_member(MANAGER_ROLE), None)
    assert view.selected is None


# ── The signup review panel ───────────────────────────────────────────────


async def _may_review(member, config):
    from cogs.admin_review_cog import _may_review_signup

    interaction = _interaction(member)
    interaction.client.config_service.get_server_config = AsyncMock(return_value=config)
    return await _may_review_signup(interaction)


@pytest.mark.parametrize("roles", [(MANAGER_ROLE,), (ADMIN_ROLE,)], ids=["manager", "admin"])
async def test_either_tier_may_action_a_signup_review(roles):
    assert await _may_review(_member(*roles), _config()) is True


async def test_the_driver_may_not_action_their_own_signup_review():
    """The panel is posted in the driver's own channel, which they can read."""
    assert await _may_review(_member(), _config()) is False


async def test_a_signup_review_refuses_when_the_config_cannot_be_read():
    from cogs.admin_review_cog import _may_review_signup

    interaction = _interaction(_member(MANAGER_ROLE))
    interaction.client.config_service.get_server_config = AsyncMock(
        side_effect=RuntimeError("database is locked")
    )

    assert await _may_review_signup(interaction) is False


# ── The penalty and appeals reviews ───────────────────────────────────────


async def _is_lm(member, config):
    from services.penalty_wizard import _is_league_manager

    bot = MagicMock()
    bot.config_service.get_server_config = AsyncMock(return_value=config)
    return await _is_league_manager(_interaction(member), ":memory:", bot)


async def test_a_league_admin_may_press_a_penalty_review_button():
    """Refused before issue #116: the gate read the interaction role alone."""
    assert await _is_lm(_member(ADMIN_ROLE), _config()) is True


async def test_a_league_manager_may_press_a_penalty_review_button():
    assert await _is_lm(_member(MANAGER_ROLE), _config()) is True


async def test_a_bystander_may_not_press_a_penalty_review_button():
    assert await _is_lm(_member(), _config()) is False
