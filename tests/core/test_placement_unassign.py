"""`PlacementService.unassign_driver` — taking a driver out of one division.

Issue #208, the companion to `test_placement_assign.py`. Unassigning is where a driver racing
in more than one division gets complicated, and every rule in it turns on that.

**A driver may hold a seat in several divisions at once**, so removing one seat does not
necessarily mean they stop being a driver. The state only falls back to `UNASSIGNED` when the
*last* assignment goes — `test_a_driver_with_another_division_stays_assigned` and its pair are
the two halves, and collapsing them would either strip a driver mid-season from a division they
are still racing in, or leave a driver marked Assigned with no seat anywhere.

**The team role is revoked only when no other seat claims it.** Two divisions can both run a
team called "Alpha", the one team of the server's list mapped to one Discord role, and a driver
leaving one of them keeps the role by virtue of the other. A role belongs to one team only
(#375), so no differently named team can hold it too.

**The seat is freed before the assignment is deleted**, and both happen before the state
changes. The seat is what the lineup graphic draws from, so a deleted assignment with an
occupied seat would draw a driver who is no longer of the division.

**A refusal names the division.** A manager unassigning somebody from the wrong division needs
to know which one the bot looked in.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.models.driver_profile import DriverState  # noqa: E402
from leaguebot.core.services.placement_service import PlacementService  # noqa: E402

SERVER_ID = 11108
SEASON_ID = 1
DIVISION_A = 11
DIVISION_B = 12
ACTOR_ID = 77
PROFILE_ID = 101
ROLE_ID = 5001


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, state: str = "ASSIGNED") -> str:
    """Two divisions, each with an Alpha team and a seat, and one driver."""
    db_path = os.path.join(str(tmp_path), "unassign.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        for div_id, name in ((DIVISION_A, "Division 1"), (DIVISION_B, "Division 2")):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (div_id, SEASON_ID, name, div_id, 500 + div_id),
            )
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, 'Alpha', 'Alpha', 2, 0)",
                (div_id * 10, div_id),
            )
            await db.execute(
                "INSERT INTO team_seats "
                "(id, team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, 1, NULL)",
                (div_id * 100, div_id * 10),
            )
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state, is_test_driver) "
            "VALUES (?, '4242', ?, 0)",
            (PROFILE_ID, state),
        )
        await db.commit()
    return db_path


async def _seat(db_path: str, division_id: int, *, profile_id: int = PROFILE_ID) -> None:
    """Put the driver in that division's Alpha seat."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?",
            (profile_id, division_id * 100),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, ?)",
            (profile_id, SEASON_ID, division_id, division_id * 100),
        )
        await db.commit()


async def _map_role(db_path: str, team_name: str, role_id: int = ROLE_ID) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
            (team_name, role_id),
        )
        await db.commit()


def _service(db_path: str) -> PlacementService:
    service = PlacementService.__new__(PlacementService)
    service._db_path = db_path
    service._bot = None
    service._refresh_lineup_post = AsyncMock(return_value=None)
    service._revoke_roles = AsyncMock(return_value=None)
    return service


async def _unassign(service, division_id: int = DIVISION_A, *, member=None) -> dict:
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    return await service.unassign_driver(
        driver_profile_id=PROFILE_ID,
        division_id=division_id,
        season_id=SEASON_ID,
        acting_user_id=ACTOR_ID,
        acting_user_name="Manager",
        guild=guild,
        discord_user_id="4242",
    )


async def _state(db_path: str) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id = ?", (PROFILE_ID,)
        )
        return (await cursor.fetchone())["current_state"]


async def _assignments(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE driver_profile_id = ?",
            (PROFILE_ID,),
        )
        return (await cursor.fetchone())["n"]


async def _seat_occupant(db_path: str, division_id: int):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id FROM team_seats WHERE id = ?", (division_id * 100,)
        )
        return (await cursor.fetchone())["driver_profile_id"]


# ---------------------------------------------------------------------------
# The refusals
# ---------------------------------------------------------------------------


async def test_a_driver_who_does_not_exist_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM driver_profiles WHERE id = ?", (PROFILE_ID,))
        await db.commit()

    with pytest.raises(ValueError, match="not found"):
        await _unassign(service)


@pytest.mark.parametrize("state", ["UNASSIGNED", "NOT_SIGNED_UP", "PENDING_ADMIN_APPROVAL"])
async def test_a_driver_not_currently_assigned_is_refused(tmp_path, state):
    """The refusal quotes the state back, so a manager can see why."""
    db_path = await _make_db(tmp_path, state=state)

    with pytest.raises(ValueError, match="Assigned state"):
        await _unassign(_service(db_path))


async def test_a_driver_not_in_that_division_is_refused_by_division_name(tmp_path):
    """A manager unassigning from the wrong division needs to know which one was looked
    in — "not assigned" alone would leave them checking the driver instead."""
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)

    with pytest.raises(ValueError, match="Division 2"):
        await _unassign(_service(db_path), DIVISION_B)


async def test_a_refused_unassignment_leaves_the_seat_alone(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)

    with pytest.raises(ValueError):
        await _unassign(_service(db_path), DIVISION_B)

    assert await _seat_occupant(db_path, DIVISION_A) == PROFILE_ID


# ---------------------------------------------------------------------------
# What it removes
# ---------------------------------------------------------------------------


async def test_the_seat_is_freed_and_the_assignment_deleted(tmp_path):
    """Both. The seat is what the lineup graphic draws from, so a deleted assignment with
    an occupied seat would draw a driver no longer of the division."""
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)

    await _unassign(_service(db_path))

    assert await _seat_occupant(db_path, DIVISION_A) is None
    assert await _assignments(db_path) == 0


async def test_the_division_is_named_in_the_result(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)

    result = await _unassign(_service(db_path))

    assert result["division_name"] == "Division 1"


# ---------------------------------------------------------------------------
# A driver racing in more than one division
# ---------------------------------------------------------------------------


async def test_a_driver_with_no_other_division_becomes_unassigned(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)

    result = await _unassign(_service(db_path))

    assert result["has_remaining_assignments"] is False
    assert await _state(db_path) == DriverState.UNASSIGNED.value


async def test_a_driver_with_another_division_stays_assigned(tmp_path):
    """They are still racing. Falling back to UNASSIGNED would strip a driver mid-season
    from a division they hold a seat in."""
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)
    await _seat(db_path, DIVISION_B)

    result = await _unassign(_service(db_path), DIVISION_A)

    assert result["has_remaining_assignments"] is True
    assert await _state(db_path) == DriverState.ASSIGNED.value


async def test_only_the_named_division_s_seat_is_freed(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)
    await _seat(db_path, DIVISION_B)

    await _unassign(_service(db_path), DIVISION_A)

    assert await _seat_occupant(db_path, DIVISION_A) is None
    assert await _seat_occupant(db_path, DIVISION_B) == PROFILE_ID


# ---------------------------------------------------------------------------
# The team role
# ---------------------------------------------------------------------------


async def test_the_team_role_is_revoked_when_no_other_seat_claims_it(tmp_path):
    db_path = await _make_db(tmp_path)
    await _map_role(db_path, "Alpha")
    await _seat(db_path, DIVISION_A)
    service = _service(db_path)
    member = MagicMock()

    await _unassign(service, DIVISION_A, member=member)

    service._revoke_roles.assert_awaited_once()
    assert ROLE_ID in service._revoke_roles.await_args.args


async def test_a_role_held_through_another_division_survives(tmp_path):
    """Two divisions can both run an "Alpha" mapped to one role, and a driver leaving one
    keeps the role by virtue of the other — revoking it would strip a role they are
    entitled to in a division they are still racing in."""
    db_path = await _make_db(tmp_path)
    await _map_role(db_path, "Alpha")
    await _seat(db_path, DIVISION_A)
    await _seat(db_path, DIVISION_B)
    service = _service(db_path)
    member = MagicMock()

    await _unassign(service, DIVISION_A, member=member)

    revoked = service._revoke_roles.await_args.args if service._revoke_roles.await_args else ()
    assert ROLE_ID not in revoked


async def test_a_team_with_no_role_mapping_revokes_only_the_division_role(tmp_path):
    """Most leagues map no team roles at all, and the division role still goes."""
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)
    service = _service(db_path)
    member = MagicMock()

    await _unassign(service, DIVISION_A, member=member)

    service._revoke_roles.assert_awaited_once()
    assert ROLE_ID not in service._revoke_roles.await_args.args


async def test_a_driver_who_has_left_the_server_is_still_unassigned(tmp_path):
    """No member to revoke roles from, but the seat and the assignment are the league's
    record and must still be put right."""
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)
    service = _service(db_path)

    await _unassign(service, DIVISION_A, member=None)

    assert await _assignments(db_path) == 0
    service._revoke_roles.assert_not_awaited()


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


async def test_the_unassignment_is_audited_against_its_division(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seat(db_path, DIVISION_A)

    await _unassign(_service(db_path))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, division_id FROM audit_entries",
        )
        row = await cursor.fetchone()
    assert row["change_type"] == "DRIVER_UNASSIGN"
    assert row["division_id"] == DIVISION_A
