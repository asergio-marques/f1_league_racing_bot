"""A real driver may not be seated while the server is in test mode.

`PlacementService.assign_driver` is the single choke point through which a driver enters a
division, so the guard tested here covers `/driver assign`, the signup path and attendance's
autoreserve alike. A *fake* driver must still pass: autoreserve moves the test roster through
this very call.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 1


async def _seed(tmp_path, *, test_mode: bool, drivers: dict[int, bool] | None = None) -> str:
    """A server with *test_mode* set, holding `{profile_id: is_test_driver}` drivers."""
    db_path = str(tmp_path / "placement_test_mode.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (?, 1, 2, 3, ?)",
            (SERVER_ID, 1 if test_mode else 0),
        )
        for profile_id, is_test in sorted((drivers or {}).items()):
            await db.execute(
                "INSERT INTO driver_profiles (id, server_id, discord_user_id, "
                "current_state, is_test_driver) VALUES (?, ?, ?, 'UNASSIGNED', ?)",
                (profile_id, SERVER_ID, str(9000 + profile_id), 1 if is_test else 0),
            )
        await db.commit()
    return db_path


def _service(db_path):
    from services.placement_service import PlacementService

    return PlacementService(db_path, bot=MagicMock())


async def test_a_real_driver_is_refused_under_test_mode(tmp_path):
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: False})

    with pytest.raises(ValueError) as excinfo:
        await _service(db_path)._guard_test_mode(SERVER_ID, 1)

    assert "Test mode is active" in str(excinfo.value)


async def test_a_fake_driver_is_seated_under_test_mode(tmp_path):
    """Attendance autoreserve moves the test roster through assign_driver."""
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: True})

    await _service(db_path)._guard_test_mode(SERVER_ID, 1)


async def test_a_real_driver_is_seated_when_test_mode_is_off(tmp_path):
    db_path = await _seed(tmp_path, test_mode=False, drivers={1: False})

    await _service(db_path)._guard_test_mode(SERVER_ID, 1)


async def test_an_unknown_profile_is_left_to_the_check_that_reports_it(tmp_path):
    """assign_driver names a missing profile in its own words; the guard stays quiet."""
    db_path = await _seed(tmp_path, test_mode=True)

    await _service(db_path)._guard_test_mode(SERVER_ID, 404)


async def test_a_server_with_no_config_row_is_not_in_test_mode(tmp_path):
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: False})

    await _service(db_path)._guard_test_mode(SERVER_ID + 1, 1)


async def test_the_guard_runs_before_a_placement(tmp_path):
    """The refusal reaches the caller through assign_driver, not only in isolation."""
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: False})
    service = _service(db_path)
    service._guard_image_capacity = _noop

    with pytest.raises(ValueError) as excinfo:
        await service.assign_driver(
            server_id=SERVER_ID,
            driver_profile_id=1,
            division_id=1,
            team_name="Redline",
            season_id=1,
            acting_user_id=99,
            acting_user_name="Tester",
            guild=MagicMock(),
            discord_user_id="9001",
        )

    assert "Test mode is active" in str(excinfo.value)


async def _noop(*args, **kwargs):
    return None
