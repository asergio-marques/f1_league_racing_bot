"""The driver pass that ends a season (issue #220).

Every driver Unassigned, Assigned, mid-signup or in review returns to Not Signed Up. Every real
driver then at Not Signed Up without the former-driver flag is deleted, with their placements and
history entries; their signups remain. A former driver is kept. A driver created by test mode is
left for test mode to delete. A banned driver is left untouched.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_lifecycle_service import run_driver_pass  # noqa: E402

SERVER_ID = 22130


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "driver_pass.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number, stage) "
            "VALUES (1, ?, '2026-09-17', 'ACTIVE', 1, 'PENDING_COMPLETION')",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (1, 1, 'Pro', 1, 1, 'FINISHED')"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at, status) "
            "VALUES (1, 1, 1, 'NORMAL', 'Silverstone Circuit', '2026-06-01T14:00:00', 'FINAL')"
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, 1, 'Alpha', 6, 0)"
        )
        drivers = [
            # id, uid, state, former, test
            (1, "1001", "ASSIGNED", 1, 0),      # a former driver: kept
            (2, "1002", "ASSIGNED", 0, 0),      # placed, never raced: deleted
            (3, "1003", "UNASSIGNED", 0, 0),    # approved, never placed: deleted
            (4, "1004", "PENDING_ADMIN_APPROVAL", 0, 0),  # in review: deleted
            (5, "1005", "NOT_SIGNED_UP", 0, 0),  # pending deletion already: deleted
            (6, "1006", "SEASON_BANNED", 0, 0),  # banned: untouched
            (7, "9000000000000000007", "ASSIGNED", 0, 1),  # test driver: reset, not deleted
        ]
        for pid, uid, state, former, test in drivers:
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state, "
                "former_driver, is_test_driver) VALUES (?, ?, ?, ?, ?, ?)",
                (pid, SERVER_ID, uid, state, former, test),
            )
        for pid in (1, 2, 7):
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (10, ?, ?)",
                (pid, pid),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                "VALUES (?, 1, 1, ?, 1)",
                (pid, cursor.lastrowid),
            )
            await db.execute(
                "INSERT INTO driver_history_entries (server_id, discord_user_id, "
                "driver_profile_id, season_number, division_name) VALUES (?, ?, ?, 1, 'Pro')",
                (SERVER_ID, str(1000 + pid), pid),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id) "
                "VALUES (1, 1, ?)",
                (pid,),
            )
        await db.execute(
            "INSERT INTO signup_records (server_id, season_id, discord_user_id) "
            "VALUES (?, 1, '1002')",
            (SERVER_ID,),
        )
        await db.commit()
    return path


async def _states(db_path) -> dict[int, str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id, current_state FROM driver_profiles ORDER BY id")
        return {r["id"]: r["current_state"] for r in await cursor.fetchall()}


async def test_the_pass_resets_and_deletes_as_the_rules_say(db_path):
    result = await run_driver_pass(db_path, SERVER_ID)

    assert await _states(db_path) == {
        1: "NOT_SIGNED_UP",
        6: "SEASON_BANNED",
        7: "NOT_SIGNED_UP",
    }
    assert result == {"reset": 5, "deleted": 4}


async def test_a_deleted_driver_leaves_no_placement_or_history_but_keeps_their_signup(db_path):
    await run_driver_pass(db_path, SERVER_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = 2"
        )
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_history_entries WHERE discord_user_id = '1002'"
        )
        assert (await cursor.fetchone())[0] == 0
        cursor = await db.execute(
            "SELECT COUNT(*) FROM signup_records WHERE discord_user_id = '1002'"
        )
        assert (await cursor.fetchone())[0] == 1


async def test_a_former_driver_keeps_their_placement_and_history(db_path):
    await run_driver_pass(db_path, SERVER_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = 1"
        )
        assert (await cursor.fetchone())[0] == 1
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_history_entries WHERE driver_profile_id = 1"
        )
        assert (await cursor.fetchone())[0] == 1


async def test_a_signup_in_review_has_its_channel_closed(db_path):
    bot = MagicMock()
    bot.wizard_service._trigger_channel_hold = AsyncMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)

    await run_driver_pass(db_path, SERVER_ID, bot=bot, guild=guild)

    held = [c.args[1] for c in bot.wizard_service._trigger_channel_hold.await_args_list]
    assert held == ["1004"]


async def test_the_signed_up_role_is_revoked_from_a_real_driver(db_path):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_module_config (server_id, signed_up_role_id) VALUES (?, 555)",
            (SERVER_ID,),
        )
        await db.commit()
    role = MagicMock()
    member = MagicMock()
    member.roles = [role]
    member.remove_roles = AsyncMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.get_role = MagicMock(return_value=role)

    await run_driver_pass(db_path, SERVER_ID, guild=guild)

    # The two Assigned real drivers and the Unassigned one; not the test driver.
    assert member.remove_roles.await_count == 3
