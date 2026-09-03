"""A mock roster survives the season-setup wizard rewriting its snapshot.

`save_pending_snapshot` deletes and re-creates the whole SETUP season — divisions,
teams and seats all take new row IDs — and it runs on every wizard command, not only
on the first. Until this was fixed it also deleted every mock driver seated in that
season, so a test-mode roster built during setup vanished the moment the manager ran
`/round add`, or restarted the bot and touched the wizard again.

The rule these pin: a seated mock driver is a league manager's work and outlives the
snapshot, reseated by division name, team name and seat number the same way channel
configuration is carried across the rebuild.
"""
from __future__ import annotations

import os
import sys
from datetime import date

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_service import SeasonService  # noqa: E402
from services.test_roster_service import add_test_driver, list_test_drivers  # noqa: E402

SERVER_ID = 8420
DIVISION = "Division 1"
_TEAMS = (("Redline", 2, 0), ("Reserve", 0, 1))


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "snapshot.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


async def _seed_teams(db, division_id):
    """The two teams every division here gets: a two-seat one and the reserve."""
    for name, seats, reserve in _TEAMS:
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, ?)",
            (division_id, name, seats, reserve),
        )
        team_id = cursor.lastrowid
        for seat_number in range(1, seats + 1):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                (team_id, seat_number),
            )


async def _seed_setup_season(db_path):
    """One SETUP season with one division, a two-seat team and a reserve team."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-03-01', 'SETUP', 1)",
            (SERVER_ID,),
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, ?, 1, 'ACTIVE', 1)",
            (season_id, DIVISION),
        )
        await _seed_teams(db, cursor.lastrowid)
        await db.commit()
    return season_id


async def _snapshot(svc, db_path, season_id, divisions=None):
    """Re-snapshot the way a wizard command does, re-seeding teams as the cog then does."""
    divisions = divisions or [DIVISION]
    new_season_id, _ = await svc.save_pending_snapshot(
        SERVER_ID,
        date(2026, 3, 1),
        season_id,
        [
            {"name": name, "role_id": 1, "channel_id": None, "tier": i + 1, "rounds": []}
            for i, name in enumerate(divisions)
        ],
    )
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ? ORDER BY id", (new_season_id,)
        )
        for row in await cursor.fetchall():
            await _seed_teams(db, row[0])
        await db.commit()
    await svc.restore_test_driver_seats(new_season_id)
    return new_season_id


async def _seat_map(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dp.test_display_name AS name, ts.seat_number
            FROM team_seats ts
            JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
            ORDER BY dp.test_display_name
            """
        )
        return {r["name"]: r["seat_number"] for r in await cursor.fetchall()}


async def test_seated_mock_driver_survives_a_snapshot(db_path):
    """The bug: a wizard command after seating a mock driver used to delete them."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    assert not isinstance(added, str), added

    await _snapshot(SeasonService(db_path), db_path, season_id)

    survivors = await list_test_drivers(SERVER_ID, DIVISION, db_path)
    assert [d["display_name"] for d in survivors] == ["Mock Alpha"]
    assert survivors[0]["team_name"] == "Redline"


async def test_mock_driver_keeps_its_seat_number(db_path):
    """Reseating is by seat number, not by "the next free seat"."""
    season_id = await _seed_setup_season(db_path)
    await add_test_driver(SERVER_ID, "Mock One", "Redline", DIVISION, db_path)
    second = await add_test_driver(SERVER_ID, "Mock Two", "Redline", DIVISION, db_path)
    assert not isinstance(second, str), second

    before = await _seat_map(db_path)
    await _snapshot(SeasonService(db_path), db_path, season_id)

    assert await _seat_map(db_path) == before


async def test_season_assignment_is_restored_under_the_new_season(db_path):
    """The assignment must point at the new season and division IDs, not the dead ones."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    new_season_id = await _snapshot(SeasonService(db_path), db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_id, division_id FROM driver_season_assignments "
            "WHERE driver_profile_id = ?",
            (profile_id,),
        )
        rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ?", (new_season_id,)
        )
        new_div_id = (await cursor.fetchone())[0]

    assert len(rows) == 1, "exactly one assignment, not a duplicate or an orphan"
    assert rows[0]["season_id"] == new_season_id
    assert rows[0]["division_id"] == new_div_id


async def test_repeated_snapshots_do_not_accumulate_assignments(db_path):
    """Several wizard commands in a row leave one assignment, not one per command."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    svc = SeasonService(db_path)
    for _ in range(3):
        season_id = await _snapshot(svc, db_path, season_id)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = ?",
            (profile_id,),
        )
        assignments = (await cursor.fetchone())[0]
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = ?", (profile_id,)
        )
        seats = (await cursor.fetchone())[0]

    assert assignments == 1
    assert seats == 1
    assert len(await list_test_drivers(SERVER_ID, DIVISION, db_path)) == 1


async def test_driver_in_a_removed_division_is_left_unseated_not_deleted(db_path):
    """A division dropped from the setup takes the seat with it, but not the profile.

    The driver stops being seated because there is nowhere to seat them; deleting the
    profile instead would be the very data loss this change exists to stop.
    """
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Alpha", "Redline", DIVISION, db_path)
    profile_id = added["profile_id"]

    # Re-snapshot with the division renamed — the one they sat in no longer exists.
    await _snapshot(SeasonService(db_path), db_path, season_id, divisions=["Division 2"])

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM driver_profiles WHERE id = ?", (profile_id,)
        )
        profile = await cursor.fetchone()
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = ?", (profile_id,)
        )
        seats = (await cursor.fetchone())[0]

    assert profile is not None, "the profile survives even when its division does not"
    assert seats == 0


async def test_reserve_driver_is_reseated(db_path):
    """The reserve team pre-creates no seats, so its occupant needs one made again."""
    season_id = await _seed_setup_season(db_path)
    added = await add_test_driver(SERVER_ID, "Mock Sub", "Reserve", DIVISION, db_path)
    assert not isinstance(added, str), added

    await _snapshot(SeasonService(db_path), db_path, season_id)

    survivors = await list_test_drivers(SERVER_ID, DIVISION, db_path)
    assert [(d["display_name"], d["team_name"]) for d in survivors] == [
        ("Mock Sub", "Reserve")
    ]
