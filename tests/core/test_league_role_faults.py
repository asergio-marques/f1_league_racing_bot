"""What is wrong with the league's two roles upon the server (#374).

Opening signups and confirming a season's configuration both refused only a role that was not
*stored*. A base role since deleted opened a window nobody could see and pinged nobody; a driver
role since deleted, or one the bot cannot grant, let every approval of the window grant nothing,
with only the host's log told.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.channel_guard import league_role_faults  # noqa: E402

BASE = 501
DRIVER = 502


def _role(role_id: int, *, managed: bool = False, above_the_bot: bool = False) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    role.is_default.return_value = False
    role.managed = managed
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: not above_the_bot
    return role


def _guild(*roles: MagicMock) -> MagicMock:
    by_id = {role.id: role for role in roles}
    guild = MagicMock()
    guild.get_role = MagicMock(side_effect=by_id.get)
    return guild


def test_two_roles_on_the_server_and_grantable_are_no_fault():
    assert league_role_faults(_guild(_role(BASE), _role(DRIVER)), BASE, DRIVER) == []


def test_a_base_role_gone_from_the_server_is_named_with_its_command():
    (fault,) = league_role_faults(_guild(_role(DRIVER)), BASE, DRIVER)

    assert "**base role** is no longer on the server" in fault
    assert "`/bot base-role`" in fault


def test_a_driver_role_gone_from_the_server_is_named_with_its_command():
    (fault,) = league_role_faults(_guild(_role(BASE)), BASE, DRIVER)

    assert "**driver role** is no longer on the server" in fault
    assert "`/bot driver-role`" in fault


def test_a_driver_role_the_bot_cannot_grant_is_named_with_why():
    (fault,) = league_role_faults(
        _guild(_role(BASE), _role(DRIVER, above_the_bot=True)), BASE, DRIVER
    )

    assert fault.startswith("The league's **driver role**:")
    assert "Move my role above it" in fault


def test_a_driver_role_managed_by_an_integration_names_the_driver_role_command():
    (fault,) = league_role_faults(
        _guild(_role(BASE), _role(DRIVER, managed=True)), BASE, DRIVER
    )

    assert "integration" in fault
    assert "team" not in fault
    assert "`/bot driver-role`" in fault


def test_a_base_role_the_bot_cannot_grant_is_no_fault():
    """The league grants the base role, not the bot, so where it sits is not the bot's concern."""
    assert league_role_faults(
        _guild(_role(BASE, above_the_bot=True), _role(DRIVER)), BASE, DRIVER
    ) == []


def test_every_fault_is_named_at_once():
    faults = league_role_faults(_guild(), BASE, DRIVER)

    assert len(faults) == 2
    assert "base role" in faults[0]
    assert "driver role" in faults[1]


def test_a_role_not_set_is_left_to_the_caller():
    """The caller names a role not set with the command that sets it; saying it is also "no
    longer on the server" would be a second, wrong, line about the same thing."""
    assert league_role_faults(_guild(), None, None) == []


def test_with_no_server_to_look_in_nothing_is_said():
    assert league_role_faults(None, BASE, DRIVER) == []
