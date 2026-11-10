"""A real driver may not be seated while the server is in test mode.

`PlacementService.guard_assign` holds the guard for `/driver assign`, which asks it at the ask and
again as its change starts. A *fake* driver must still pass: test mode exists to seat the test
roster.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations

#: Why a placement refusal test fails until #283's build raises the member's refusal by name.
PLACEMENT_REFUSAL = "#283: placement still refuses with a bare ValueError, not Refused"

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
                "INSERT INTO driver_profiles (id, discord_user_id, "
                "current_state, is_test_driver) VALUES (?, ?, 'UNASSIGNED', ?)",
                (profile_id, str(9000 + profile_id), 1 if is_test else 0),
            )
        await db.commit()
    return db_path


def _service(db_path):
    from leaguebot.core.services.placement_service import PlacementService

    return PlacementService(db_path)  # handed no drawing, it asks no capacity guard


@pytest.mark.xfail(strict=True, reason=PLACEMENT_REFUSAL)
async def test_a_real_driver_is_refused_under_test_mode(tmp_path):
    from leaguebot.core.models.refusal import Refused
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: False})

    with pytest.raises(Refused) as excinfo:
        await _service(db_path)._guard_test_mode(1)

    assert "Test mode is active" in str(excinfo.value)


async def test_a_fake_driver_is_seated_under_test_mode(tmp_path):
    """Test mode exists to seat the test roster, so its fake drivers pass."""
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: True})

    await _service(db_path)._guard_test_mode(1)


async def test_a_real_driver_is_seated_when_test_mode_is_off(tmp_path):
    db_path = await _seed(tmp_path, test_mode=False, drivers={1: False})

    await _service(db_path)._guard_test_mode(1)


async def test_an_unknown_profile_is_left_to_the_check_that_reports_it(tmp_path):
    """The assignment's own check names a missing profile in its own words; the guard stays
    quiet."""
    db_path = await _seed(tmp_path, test_mode=True)

    await _service(db_path)._guard_test_mode(404)


@pytest.mark.xfail(strict=True, reason=PLACEMENT_REFUSAL)
async def test_the_guard_runs_before_a_placement(tmp_path):
    """The refusal reaches the caller through the assignment's guards, not only in isolation."""
    from leaguebot.core.models.refusal import Refused
    db_path = await _seed(tmp_path, test_mode=True, drivers={1: False})
    service = _service(db_path)

    with pytest.raises(Refused) as excinfo:
        await service.guard_assign(1, 1, "Redline", 1)

    assert "Test mode is active" in str(excinfo.value)

