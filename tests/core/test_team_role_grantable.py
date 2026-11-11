"""A team's role must be one the bot can grant (#381).

A team's role is granted to every driver placed in it, and `team_role_service._grant_roles` only
logs a failure — so a role Discord will not let the bot grant costs a whole team their role with
nobody told. The four cases are refused where the role is chosen, which is the one moment a league
is there to choose another.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from leaguebot.core.utils.channel_guard import role_grant_refusal


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


def test_a_role_above_the_bot_s_own_is_refused_saying_how_to_fix_it():
    refusal = role_grant_refusal(_role(above_the_bot=True))

    assert refusal is not None
    assert "above it" in refusal


def test_every_role_is_refused_while_the_bot_lacks_manage_roles():
    refusal = role_grant_refusal(_role(manage_roles=False))

    assert refusal is not None
    assert "Manage Roles" in refusal


@pytest.mark.parametrize(
    "fault, said",
    [
        pytest.param({"default": True}, "@everyone", id="everyone"),
        # A bot's own role, Server Booster, a linked role: Discord grants those itself.
        pytest.param({"managed": True}, "integration", id="managed by an integration"),
    ],
)
def test_a_team_s_role_is_told_to_choose_one_of_the_team_s_own(fault, said):
    """`@everyone` and a role an integration manages are refused, each named as what it is. The
    default wording is a team's, for every caller that sets one."""
    refusal = role_grant_refusal(_role(**fault))

    assert said in refusal
    assert "Choose a role of the team's own." in refusal


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

