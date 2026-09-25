"""A team's role must be one the bot can grant (#381).

A team's role is granted to every driver placed in it, and `placement_service._grant_roles` only
logs a failure — so a role Discord will not let the bot grant costs a whole team their role with
nobody told. The four cases are refused where the role is chosen, which is the one moment a league
is there to choose another.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.channel_guard import role_grant_refusal  # noqa: E402


def _role(
    *,
    default: bool = False,
    managed: bool = False,
    manage_roles: bool = True,
    above_the_bot: bool = False,
) -> MagicMock:
    role = MagicMock()
    role.id = 111
    role.mention = "<@&111>"
    role.is_default.return_value = default
    role.managed = managed
    role.guild.me.guild_permissions.manage_roles = manage_roles
    role.guild.me.top_role.__gt__ = lambda _self, _other: not above_the_bot
    return role


def test_a_grantable_role_is_accepted():
    assert role_grant_refusal(_role()) is None


def test_everyone_is_refused():
    refusal = role_grant_refusal(_role(default=True))

    assert refusal is not None
    assert "@everyone" in refusal


def test_a_role_managed_by_an_integration_is_refused():
    """A bot's own role, Server Booster, a linked role: Discord grants those itself."""
    refusal = role_grant_refusal(_role(managed=True))

    assert refusal is not None
    assert "integration" in refusal


def test_a_role_above_the_bot_s_own_is_refused_saying_how_to_fix_it():
    refusal = role_grant_refusal(_role(above_the_bot=True))

    assert refusal is not None
    assert "above it" in refusal


def test_every_role_is_refused_while_the_bot_lacks_manage_roles():
    refusal = role_grant_refusal(_role(manage_roles=False))

    assert refusal is not None
    assert "Manage Roles" in refusal


@pytest.mark.parametrize("fault", [{"default": True}, {"managed": True}])
def test_a_team_s_role_is_told_to_choose_one_of_the_team_s_own(fault):
    """The default wording is a team's, for every caller that sets one."""
    assert "Choose a role of the team's own." in role_grant_refusal(_role(**fault))


@pytest.mark.parametrize("fault", [{"default": True}, {"managed": True}])
def test_a_role_standing_for_something_else_says_so_and_names_its_own_remedy(fault):
    """The league's driver role is granted on the same terms (#374), and a refusal telling a
    league to choose "a role of the team's own" for it would send them to the wrong command."""
    refusal = role_grant_refusal(
        _role(**fault),
        stands_for="the league's drivers",
        remedy="Choose another with `/bot driver-role`.",
    )

    assert "team" not in refusal
    assert refusal.endswith("Choose another with `/bot driver-role`.")
    if fault.get("default"):
        assert "cannot stand for the league's drivers" in refusal


def test_a_role_carrying_no_guild_is_judged_on_itself_alone():
    """Every caller passes a real role, but nothing here fails on a double that answers less."""
    role = MagicMock()
    role.is_default.return_value = False
    role.managed = False
    role.guild = None

    assert role_grant_refusal(role) is None


# ── The commands that set one ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "command,arguments",
    [
        ("team_reserve_role", {}),
    ],
)
async def test_a_team_command_refuses_a_role_the_bot_cannot_grant(command, arguments):
    from cogs.team_cog import TeamCog
    from tests.support.undecorate import undecorate

    bot = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.team_service.get_teams_with_roles = AsyncMock(
        return_value=[{"name": "RBR", "full_name": "Oracle Red Bull Racing",
                       "is_reserve": False, "role_id": None}]
    )
    bot.team_service.add_default_team = AsyncMock()
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    bot.placement_service.set_team_role_config = AsyncMock()
    interaction = MagicMock()
    interaction.guild_id = 1
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot

    await undecorate(getattr(TeamCog, command))(
        cog, interaction, role=_role(managed=True), **arguments
    )

    said = interaction.response.send_message.await_args.args[0]
    assert "integration" in said
    bot.team_service.add_default_team.assert_not_awaited()
    bot.placement_service.set_team_role_config.assert_not_awaited()


async def test_adding_a_team_refuses_a_role_the_bot_cannot_grant():
    """The form's own submission, which is where `/team add` does its work now (#381)."""
    from cogs.team_cog import TeamCog

    bot = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.team_service.add_default_team = AsyncMock()
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.send_message = AsyncMock()
    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot

    await cog.add_team(
        interaction,
        shorthand="RBR",
        full_name="Oracle Red Bull Racing",
        role=_role(managed=True),
    )

    assert "integration" in interaction.response.send_message.await_args.args[0]
    bot.team_service.add_default_team.assert_not_awaited()
