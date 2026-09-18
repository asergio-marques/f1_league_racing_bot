"""Switching test mode off deletes its drivers and keeps their history (issue #220).

Every driver created by test mode on the server is deleted — seated or not, in whatever season —
together with what holds them, and their history entries are kept by identifier.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.test_mode_service import switch_test_mode_off  # noqa: E402
from services.test_roster_service import clear_all_test_drivers  # noqa: E402

SERVER_ID = 22140


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "test_mode_off.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number) "
            "VALUES (1, ?, '2026-09-17', 'ACTIVE', 1)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (1, 1, 'Pro', 1, 1, 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (1, 1, 1, 'NORMAL', 'Silverstone Circuit', '2026-06-01T14:00:00')"
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, 1, 'Alpha', 2, 0)"
        )
        for pid, uid, test, seated in ((1, "9000000000000000001", 1, True),
                                        (2, "9000000000000000002", 1, False),
                                        (3, "4242", 0, True)):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
                "is_test_driver) VALUES (?, ?, 'ASSIGNED', ?)",
                (pid, uid, test),
            )
            if seated:
                cursor = await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                    "VALUES (10, ?, ?)",
                    (pid, pid),
                )
                await db.execute(
                    "INSERT INTO driver_season_assignments "
                    "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, 1, 1, ?)",
                    (pid, cursor.lastrowid),
                )
                await db.execute(
                    "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id) "
                    "VALUES (1, 1, ?)",
                    (pid,),
                )
            await db.execute(
                "INSERT INTO driver_history_entries (discord_user_id, "
                "driver_profile_id, season_number, division_name) VALUES (?, ?, 1, 'Pro')",
                (uid, pid),
            )
        await db.commit()
    return path


async def _profiles(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM driver_profiles ORDER BY id")
        return [r["id"] for r in await cursor.fetchall()]


async def test_every_fake_driver_is_deleted_seated_or_not_and_the_real_one_kept(db_path):
    assert await clear_all_test_drivers(db_path) == 2

    assert await _profiles(db_path) == [3]


async def test_a_fake_driver_who_raced_is_deleted_all_the_same(db_path):
    """Attendance and placements held them: the deletion lets go of both."""
    await clear_all_test_drivers(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM driver_round_attendance WHERE driver_profile_id = 1"
        )
        assert (await cursor.fetchone())[0] == 0


async def test_their_history_is_kept_by_identifier(db_path):
    await clear_all_test_drivers(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, driver_profile_id FROM driver_history_entries ORDER BY id"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]
    assert rows == [
        ("9000000000000000001", None),
        ("9000000000000000002", None),
        ("4242", 3),
    ]


async def test_switching_off_clears_the_flag_and_the_drivers(db_path):
    bot = SimpleNamespace(db_path=db_path)
    with patch(
        "services.forecast_cleanup_service.flush_pending_deletions", new=AsyncMock()
    ) as flushed:
        assert await switch_test_mode_off(SERVER_ID, bot) == 2

    flushed.assert_awaited_once()
    assert await _profiles(db_path) == [3]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs WHERE server_id = ?", (SERVER_ID,)
        )
        assert (await cursor.fetchone())[0] == 0


async def test_a_server_not_in_test_mode_is_left_alone(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET test_mode_active = 0")
        await db.commit()

    assert await switch_test_mode_off(SERVER_ID, SimpleNamespace(db_path=db_path)) == 0
    assert await _profiles(db_path) == [1, 2, 3]


async def test_a_flush_that_fails_still_switches_test_mode_off(db_path):
    """A stale forecast is not worth staying in test mode for."""
    bot = SimpleNamespace(db_path=db_path)
    with patch(
        "services.forecast_cleanup_service.flush_pending_deletions",
        new=AsyncMock(side_effect=RuntimeError("channel gone")),
    ):
        assert await switch_test_mode_off(SERVER_ID, bot) == 2

    assert await _profiles(db_path) == [3]


async def test_clearing_one_division_deletes_its_fake_drivers_and_keeps_their_history(db_path):
    from services.test_roster_service import _delete_test_drivers_in_division

    assert await _delete_test_drivers_in_division(1, db_path) == 1

    # The seated fake driver goes; the unseated one and the real driver stay.
    assert await _profiles(db_path) == [2, 3]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, driver_profile_id FROM driver_history_entries "
            "WHERE discord_user_id = '9000000000000000001'"
        )
        assert tuple(await cursor.fetchone()) == ("9000000000000000001", None)
