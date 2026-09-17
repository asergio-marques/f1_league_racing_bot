"""A history entry names its driver by identifier, and outlives the profile (issue #220).

Test mode deletes its drivers when a season ends and keeps their history, so a driver created
again under the same identifier holds it as their own. Migration 057 gives every entry its
server and Discord identifier and lets its profile reference go null when the profile does.
"""
from __future__ import annotations

import os
import sys

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 22120
_MIGRATIONS = os.path.join(os.path.dirname(__file__), "..", "..", "src", "db", "migrations")
_MIGRATION_057 = os.path.join(_MIGRATIONS, "057_season_lifecycle.sql")


async def _schema_before_057(db) -> None:
    """Raise the schema as it stood before the lifecycle migration, applied by hand.

    Deliberately not `run_migrations`: the entries have to be written before 057 runs, which
    the schema template cannot stop short of.
    """
    for name in sorted(f for f in os.listdir(_MIGRATIONS) if f.endswith(".sql") and f < "057"):
        with open(os.path.join(_MIGRATIONS, name), encoding="utf-8") as fh:
            await db.executescript(fh.read())
    # Migration 001 turns foreign keys on; the rows seeded here stand alone.
    await db.execute("PRAGMA foreign_keys = OFF")


async def test_the_migration_carries_every_entry_and_its_drivers_identity(tmp_path):
    path = str(tmp_path / "pre_057.db")
    async with aiosqlite.connect(path) as db:
        await _schema_before_057(db)
        await db.executescript(
            """
            INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state)
                VALUES (1, 7, '4242', 'NOT_SIGNED_UP');
            INSERT INTO driver_history_entries
                (driver_profile_id, season_number, division_name, final_position, final_points)
                VALUES (1, 3, 'Pro', 2, 88);
            """
        )
        with open(_MIGRATION_057, encoding="utf-8") as fh:
            await db.executescript(fh.read())
        cursor = await db.execute(
            "SELECT server_id, discord_user_id, driver_profile_id, season_number, final_points "
            "FROM driver_history_entries"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]

    assert rows == [(7, "4242", 1, 3, 88)]


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "identity.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state, "
            "is_test_driver) VALUES (1, ?, '9000000000000000001', 'ASSIGNED', 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO driver_history_entries (server_id, discord_user_id, driver_profile_id, "
            "season_number, division_name) VALUES (?, '9000000000000000001', 1, 1, 'Pro')",
            (SERVER_ID,),
        )
        await db.commit()
    return path


async def test_an_entry_outlives_the_profile_it_belonged_to(db_path):
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM driver_profiles WHERE id = 1")
        await db.commit()
        cursor = await db.execute(
            "SELECT discord_user_id, driver_profile_id FROM driver_history_entries"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]

    assert rows == [("9000000000000000001", None)]


async def test_a_test_driver_created_again_holds_the_history_of_its_identifier(db_path):
    from services.test_roster_service import _reattach_history

    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM driver_profiles WHERE id = 1")
        await db.execute(
            "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state, "
            "is_test_driver) VALUES (2, ?, '9000000000000000001', 'ASSIGNED', 1)",
            (SERVER_ID,),
        )
        await _reattach_history(db, SERVER_ID, "9000000000000000001", 2)
        await db.commit()
        cursor = await db.execute("SELECT driver_profile_id FROM driver_history_entries")
        assert [r[0] for r in await cursor.fetchall()] == [2]


async def test_history_held_by_a_living_profile_is_not_taken(db_path):
    from services.test_roster_service import _reattach_history

    async with get_connection(db_path) as db:
        await _reattach_history(db, SERVER_ID, "9000000000000000001", 99)
        cursor = await db.execute("SELECT driver_profile_id FROM driver_history_entries")
        assert [r[0] for r in await cursor.fetchall()] == [1]
