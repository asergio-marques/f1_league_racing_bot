"""A driver whose placement is not yet confirmed stands outside check-in and attendance (#220).

Placed mid-season in Ongoing, placements, a driver receives no check-in call, holds no
attendance row and stands on no attendance sheet until placements are confirmed. A seat whose
occupant holds no placement row at all reads as it always did.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.attendance_service import _opening_attendance_rows  # noqa: E402
from services.rsvp_service import query_division_roster  # noqa: E402

SERVER_ID = 22080
DIVISION_ID = 1


@pytest.fixture
async def db_path(tmp_path):
    """An ongoing season: one committed driver, one uncommitted, one seated with no row."""
    path = str(tmp_path / "uncommitted_attendance.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number, stage) "
            "VALUES (1, ?, '2026-09-17', 'ACTIVE', 1, 'ONGOING_PLACEMENTS')",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (?, 1, 'Pro', 1, 1, 'ACTIVE')",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 3, 0)",
            (DIVISION_ID,),
        )
        for profile_id, uid, committed in ((1, "1001", 1), (2, "1002", 0), (3, "1003", None)):
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state) "
                "VALUES (?, ?, ?, 'ASSIGNED')",
                (profile_id, SERVER_ID, uid),
            )
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (10, ?, ?)",
                (profile_id, profile_id),
            )
            if committed is not None:
                await db.execute(
                    "INSERT INTO driver_season_assignments "
                    "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                    "VALUES (?, 1, ?, ?, ?)",
                    (profile_id, DIVISION_ID, cursor.lastrowid, committed),
                )
        await db.commit()
    return path


async def test_the_check_in_roster_leaves_out_an_uncommitted_driver(db_path):
    roster = await query_division_roster(db_path, DIVISION_ID)

    drivers = [d["discord_user_id"] for team in roster for d in team["drivers"]]
    assert sorted(drivers) == ["1001", "1003"]


async def test_the_opening_sheet_leaves_out_an_uncommitted_driver(db_path):
    async with get_connection(db_path) as db:
        rows = await _opening_attendance_rows(db, DIVISION_ID)

    assert sorted(row["discord_user_id"] for row in rows) == ["1001", "1003"]


async def test_a_season_in_placements_leaves_its_seats_alone(db_path):
    """Nothing is raced before placements are confirmed, so nothing is filtered."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = 'SETUP', stage = 'PLACEMENTS'")
        await db.commit()

    roster = await query_division_roster(db_path, DIVISION_ID)

    drivers = [d["discord_user_id"] for team in roster for d in team["drivers"]]
    assert sorted(drivers) == ["1001", "1002", "1003"]
