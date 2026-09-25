"""Granting a replaced league role to every driver (#374).

A league role deleted from the server may be replaced while the season's configuration is fixed.
Nobody holds a deleted role, so every driver lost it with the role, and the replacement is given
to each of them: every real driver Unassigned or Assigned, which is whoever the driver role
belongs to.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.placement_service import PlacementService

ROLE_ID = 4040

#: (discord user id, state, test driver) — only the first two are drivers the role belongs to.
PROFILES = [
    ("1001", "ASSIGNED", 0),
    ("1002", "UNASSIGNED", 0),
    ("1003", "PENDING_ADMIN_APPROVAL", 0),
    ("1004", "NOT_SIGNED_UP", 0),
    ("1005", "ASSIGNED", 1),
]


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "grant_every_driver.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        for uid, state, is_test in PROFILES:
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state, is_test_driver) "
                "VALUES (?, ?, ?)",
                (uid, state, is_test),
            )
        await db.commit()
    return path


def _guild(*, members: dict | None = None, role_present: bool = True):
    role = MagicMock()
    role.id = ROLE_ID
    guild = MagicMock()
    guild.get_role = MagicMock(return_value=role if role_present else None)
    if members is None:
        members = {1001: MagicMock(add_roles=AsyncMock()), 1002: MagicMock(add_roles=AsyncMock())}
    guild.get_member = MagicMock(side_effect=lambda uid: members.get(uid))
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "Unknown Member")
    )
    guild._role = role
    guild._members = members
    return guild


async def test_every_driver_the_driver_role_belongs_to_is_given_it(db_path):
    guild = _guild()

    outcome = await PlacementService(db_path).grant_to_every_driver(guild, ROLE_ID)

    assert outcome.granted == 2
    assert outcome.not_granted == []
    for member in guild._members.values():
        member.add_roles.assert_awaited_once_with(guild._role, reason="League role replaced")


async def test_nobody_else_is_given_it(db_path):
    """Not a signup still in review, not a former driver, and not a test driver, who holds no
    role at all."""
    guild = _guild()

    await PlacementService(db_path).grant_to_every_driver(guild, ROLE_ID)

    asked = sorted(call.args[0] for call in guild.get_member.call_args_list)
    assert asked == [1001, 1002]


async def test_a_driver_discord_refuses_is_named_and_the_rest_still_get_it(db_path):
    refused = MagicMock(add_roles=AsyncMock(side_effect=discord.Forbidden(MagicMock(status=403), "no")))
    granted = MagicMock(add_roles=AsyncMock())
    guild = _guild(members={1001: refused, 1002: granted})

    outcome = await PlacementService(db_path).grant_to_every_driver(guild, ROLE_ID)

    assert outcome.not_granted == ["1001"]
    assert outcome.granted == 1
    granted.add_roles.assert_awaited_once()


async def test_a_driver_not_cached_is_fetched(db_path):
    fetched = MagicMock(add_roles=AsyncMock())
    guild = _guild(members={1002: MagicMock(add_roles=AsyncMock())})
    guild.fetch_member = AsyncMock(return_value=fetched)

    outcome = await PlacementService(db_path).grant_to_every_driver(guild, ROLE_ID)

    assert outcome.granted == 2
    fetched.add_roles.assert_awaited_once()


async def test_a_driver_who_has_left_is_passed_over(db_path):
    guild = _guild(members={1002: MagicMock(add_roles=AsyncMock())})

    outcome = await PlacementService(db_path).grant_to_every_driver(guild, ROLE_ID)

    assert outcome.granted == 1
    assert outcome.not_granted == []


async def test_a_role_not_on_the_server_is_granted_to_nobody_and_says_so(db_path):
    """The command checks the role first; this only keeps a race from reading as success."""
    guild = _guild(role_present=False)

    outcome = await PlacementService(db_path).grant_to_every_driver(guild, ROLE_ID)

    assert outcome.granted == 0
    assert outcome.not_granted == ["1001", "1002"]
