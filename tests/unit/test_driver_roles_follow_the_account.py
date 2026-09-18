"""A driver's roles belong to their current account (issue #243).

`PlacementService.driver_role_ids` reads what the driver's standing entitles them to — the
signed-up role while Unassigned or Assigned, and the division and team roles of each confirmed
placement in the live season — from the league's own record, so it answers even when the
account holding the roles has left. `move_driver_roles` gives them to the new current account
and takes them from the one it replaced where that one is still in the server (E21, E22).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402

SERVER_ID = 2438
SIGNED_UP_ROLE, DIVISION_ROLE, TEAM_ROLE = 7001, 7002, 7003
OLD, NEW = "6301", "6302"


async def _league(tmp_path, *, state: str = "ASSIGNED", committed: int = 1,
                   test: bool = False) -> tuple[str, int]:
    """A driver seated in the live season's one division, in team Alpha."""
    db_path = os.path.join(str(tmp_path), "roles.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_module_config (id, signed_up_role_id) VALUES (?, ?)",
            (1, SIGNED_UP_ROLE),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-09-17', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
            "VALUES (1, 1, 'Pro', ?, 1)",
            (DIVISION_ROLE,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (1, 1, 'Alpha', 2, 0)"
        )
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES ('Alpha', ?)",
            (TEAM_ROLE,),
        )
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver) VALUES (?, ?, ?)",
            (NEW, state, int(test)),
        )
        profile_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (1, 1, 1, ?)",
            (profile_id,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (?, 1, 1, 1, ?)",
            (profile_id, committed),
        )
        await db.commit()
    return db_path, profile_id


async def test_an_assigned_driver_holds_signed_up_division_and_team_roles(tmp_path):
    db_path, pid = await _league(tmp_path)
    assert await PlacementService(db_path).driver_role_ids(pid) == {
        SIGNED_UP_ROLE, DIVISION_ROLE, TEAM_ROLE
    }


async def test_an_unconfirmed_placement_grants_no_division_or_team_role(tmp_path):
    db_path, pid = await _league(tmp_path, state="UNASSIGNED", committed=0)
    assert await PlacementService(db_path).driver_role_ids(pid) == {SIGNED_UP_ROLE}


async def test_a_driver_not_signed_up_holds_no_signed_up_role(tmp_path):
    """E22."""
    db_path, pid = await _league(tmp_path, state="NOT_SIGNED_UP", committed=0)
    assert await PlacementService(db_path).driver_role_ids(pid) == set()


async def test_a_test_mode_driver_holds_no_roles(tmp_path):
    db_path, pid = await _league(tmp_path, test=True)
    assert await PlacementService(db_path).driver_role_ids(pid) == set()


def _role(role_id: int) -> MagicMock:
    role = MagicMock(spec=discord.Role)
    role.id = role_id
    return role


def _guild(members: dict[str, MagicMock]) -> MagicMock:
    roles = {rid: _role(rid) for rid in (SIGNED_UP_ROLE, DIVISION_ROLE, TEAM_ROLE)}
    guild = MagicMock()
    guild.get_role = MagicMock(side_effect=roles.get)
    guild.get_member = MagicMock(side_effect=lambda uid: members.get(str(uid)))
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "gone")
    )
    guild.roles_by_id = roles
    return guild


def _member(roles: list) -> MagicMock:
    member = MagicMock()
    member.roles = roles
    member.add_roles = AsyncMock()
    member.remove_roles = AsyncMock()
    return member


async def test_the_roles_move_from_the_replaced_account_to_the_current_one(tmp_path):
    db_path, pid = await _league(tmp_path)
    new_member = _member([])
    guild = _guild({NEW: new_member})
    old_member = _member(list(guild.roles_by_id.values()))
    guild.get_member.side_effect = lambda uid: {NEW: new_member, OLD: old_member}.get(str(uid))

    problems = await PlacementService(db_path).move_driver_roles(guild, pid, OLD, NEW)

    assert problems == []
    granted = {r.id for r in new_member.add_roles.await_args.args}
    removed = {r.id for r in old_member.remove_roles.await_args.args}
    assert granted == removed == {SIGNED_UP_ROLE, DIVISION_ROLE, TEAM_ROLE}


async def test_a_replaced_account_that_has_left_is_skipped_quietly(tmp_path):
    """E21: granted to the current account from the league's record; nothing to remove."""
    db_path, pid = await _league(tmp_path)
    new_member = _member([])
    guild = _guild({NEW: new_member})

    problems = await PlacementService(db_path).move_driver_roles(guild, pid, OLD, NEW)

    assert problems == []
    assert {r.id for r in new_member.add_roles.await_args.args} == {
        SIGNED_UP_ROLE, DIVISION_ROLE, TEAM_ROLE
    }


async def test_a_grant_discord_refuses_is_reported(tmp_path):
    """E44: reported for the command to show; nothing raised."""
    db_path, pid = await _league(tmp_path)
    new_member = _member([])
    new_member.add_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    guild = _guild({NEW: new_member})

    problems = await PlacementService(db_path).move_driver_roles(guild, pid, OLD, NEW)

    assert len(problems) == 1 and "could not be given" in problems[0]
