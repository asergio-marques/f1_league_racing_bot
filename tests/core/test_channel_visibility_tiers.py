"""Both of the league's roles can see the channels their buttons live in.

Four channels are created with permission overwrites keyed to a role, and every one of them
keyed only the *interaction* role. That was harmless while Discord's Administrator permission
carried the league manager tier, because an administrator could read every channel on the
server anyway.

It stopped being harmless the moment both tiers became roles the league configures
(issue #116). A league admin who does not also hold the interaction role is entitled to press
the buttons in all four — approving a signup, judging a penalty, cancelling an amendment —
and would have been unable to see any of them.

The admin role is skipped where the league has not set one, which is every server configured
before it existed.

The results submission channel is created by a job of the queued opening of a round's
submission (#439, slice 6b), whose own tests pin its two roles
(`test_result_submission_wizard.py`, `test_submission_channel.py`).
"""
from __future__ import annotations

from unittest.mock import MagicMock

import discord
import pytest

from leaguebot.core.models.server_config import ServerConfig
from tests.support.change_queue import in_a_job

SERVER_ID = 4242
MANAGER_ROLE = 222
ADMIN_ROLE = 444


def _role(role_id: int) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.name = f"role-{role_id}"
    return role


def _guild() -> MagicMock:
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.default_role = _role(1)
    guild.me = _role(2)
    guild.get_role = lambda role_id: _role(role_id) if role_id in (MANAGER_ROLE, ADMIN_ROLE) else None
    guild.get_channel = MagicMock(return_value=None)
    return guild


def _overwrite_role_ids(overwrites: dict) -> set[int]:
    return {getattr(target, "id", None) for target in overwrites}


# ── The driver's signup wizard channel ────────────────────────────────────


async def _create_wizard_channel(*, league_admin_role_id):
    from leaguebot.signup.services.wizard_service import WizardService

    guild = _guild()
    created: dict = {}

    async def _create(name, **kwargs):
        created.update(kwargs)
        return MagicMock()

    guild.create_text_channel = _create
    member = MagicMock(spec=discord.Member)
    member.name = "driver"

    with in_a_job():
        await WizardService._create_wizard_channel(
            guild, member, None, MANAGER_ROLE, league_admin_role_id
        )
    return created["overwrites"]


@pytest.mark.parametrize(
    "admin_role_id,expected",
    [(ADMIN_ROLE, {MANAGER_ROLE, ADMIN_ROLE}), (None, {MANAGER_ROLE})],
    ids=["admin role set", "admin role unset"],
)
async def test_the_wizard_channel_is_open_to_both_roles(admin_role_id, expected):
    """The admin review panel is posted here, so a league admin has to be able to read it; an
    admin role that is not set is left out."""
    overwrites = await _create_wizard_channel(league_admin_role_id=admin_role_id)

    assert expected <= _overwrite_role_ids(overwrites)
    if admin_role_id is None:
        assert ADMIN_ROLE not in _overwrite_role_ids(overwrites)


# ── The signup channel ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "admin_role_id,expected",
    [(ADMIN_ROLE, {MANAGER_ROLE, ADMIN_ROLE}), (None, {MANAGER_ROLE})],
    ids=["admin role set", "admin role unset"],
)
async def test_the_signup_channel_is_open_to_both_roles(admin_role_id, expected):
    from leaguebot.signup.services.signup_module_service import signup_channel_overwrites

    overwrites = signup_channel_overwrites(
        _guild(),
        ServerConfig(
            server_id=SERVER_ID,
            interaction_role_id=MANAGER_ROLE,
            league_admin_role_id=admin_role_id,
            interaction_channel_id=111,
            log_channel_id=333,
        ),
    )

    assert expected <= _overwrite_role_ids(overwrites)
    if admin_role_id is None:
        assert ADMIN_ROLE not in _overwrite_role_ids(overwrites)
