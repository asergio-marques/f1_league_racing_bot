"""`PlacementService.assign_driver` — putting a driver in a seat.

Issue #208. `placement_service.py` was at 50.4%. This method is the **single choke point
through which a driver enters a division**: the signup wizard, `/driver assign`, the bulk
roster import and attendance's autoreserve all arrive here. Its own comments say so, which is
why the capacity and test-mode guards sit at the top of it rather than at each caller.

**A race team's seats are finite; the reserve team's are not.** An ordinary team has a fixed
number of seats created when the division was seeded, and filling the last one means the next
driver is refused. The reserve team has no such bound — a division can carry as many reserves
as turn up — so when its seats run out the method creates another rather than refusing. The two
branches are near-identical SQL and are tested separately for that reason:
`test_a_full_race_team_refuses_the_next_driver` and
`test_the_reserve_team_makes_another_seat_rather_than_refusing` are the pair.

**Seats are filled in number order.** A driver placed into seat 2 while seat 1 stands empty
would draw a hole in the lineup graphic, which addresses a seat by its ordinal.

**A driver may hold one seat per division, and no more.** The duplicate check names the division
rather than the team, because a manager placing somebody twice has usually forgotten *where*
they already are, not which team.

**Only an Unassigned or Assigned driver may be placed.** Somebody mid-signup, awaiting approval
or already sacked is in a state a seat would contradict, and the refusal quotes the state back
so a manager can see which.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.driver_profile import DriverState  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402

SERVER_ID = 11008
SEASON_ID = 1
DIVISION_ID = 11
ACTOR_ID = 77
PROFILE_ID = 101


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    state: str = "UNASSIGNED",
    team_seats: int = 2,
    reserve_seats: int = 1,
    is_test_driver: bool = False,
) -> str:
    db_path = os.path.join(str(tmp_path), "assign.db")
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
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', ?, 0)",
            (DIVISION_ID, team_seats),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (11, ?, 'Reserve', 'Reserve', -1, 1)",
            (DIVISION_ID,),
        )
        for seat in range(1, team_seats + 1):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (10, ?, NULL)",
                (seat,),
            )
        for seat in range(1, reserve_seats + 1):
            await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (11, ?, NULL)",
                (seat,),
            )
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state, is_test_driver) "
            "VALUES (?, '4242', ?, ?)",
            (PROFILE_ID, state, int(is_test_driver)),
        )
        await db.commit()
    return db_path


def _service(db_path: str) -> PlacementService:
    service = PlacementService.__new__(PlacementService)
    service._db_path = db_path
    service._bot = None  # the capacity guards return early without a bot
    return service


async def _assign(service, *, team: str = "Alpha", profile_id: int = PROFILE_ID) -> dict:
    """Place a driver, with the Discord side stubbed out.

    The method falls through to `guild.fetch_member` when the member is not cached, and
    then refreshes the lineup post — both awaited, and neither the subject here. Role
    granting has its own cover in `test_placement_role_configs.py`.
    """
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=None)
    service._refresh_lineup_post = AsyncMock(return_value=None)
    with patch.object(
        PlacementService, "_guard_test_mode", new=AsyncMock(return_value=None)
    ):
        return await service.assign_driver(
            driver_profile_id=profile_id,
            division_id=DIVISION_ID,
            team_name=team,
            season_id=SEASON_ID,
            acting_user_id=ACTOR_ID,
            acting_user_name="Manager",
            guild=guild,
            discord_user_id="4242",
        )


async def _seat_of(db_path: str, profile_id: int = PROFILE_ID):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT ts.seat_number, ti.name FROM team_seats ts "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ts.driver_profile_id = ?",
            (profile_id,),
        )
        row = await cursor.fetchone()
    return (row["seat_number"], row["name"]) if row else None


async def _add_driver(db_path: str, profile_id: int, state: str = "UNASSIGNED") -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state, is_test_driver) "
            "VALUES (?, ?, ?, 0)",
            (profile_id, str(4000 + profile_id), state),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Who may be placed
# ---------------------------------------------------------------------------


async def test_an_unassigned_driver_is_placed(tmp_path):
    db_path = await _make_db(tmp_path, state="UNASSIGNED")

    result = await _assign(_service(db_path))

    assert result["team_name"] == "Alpha"
    assert result["division_name"] == "Division 1"
    assert result["was_unassigned"] is True


async def test_an_already_assigned_driver_may_take_a_second_division(tmp_path):
    """A driver can race in more than one division, so being Assigned is not a refusal —
    and `was_unassigned` is what tells the caller which of the two happened."""
    db_path = await _make_db(tmp_path, state="ASSIGNED")

    result = await _assign(_service(db_path))

    assert result["was_unassigned"] is False


@pytest.mark.parametrize(
    "state",
    [
        "NOT_SIGNED_UP",
        "PENDING_SIGNUP_COMPLETION",
        "PENDING_ADMIN_APPROVAL",
        "PENDING_DRIVER_CORRECTION",
    ],
)
async def test_a_driver_not_yet_through_signup_cannot_be_placed(tmp_path, state):
    """A seat would contradict the state, and the refusal quotes it back so a manager can
    see which."""
    db_path = await _make_db(tmp_path, state=state)

    with pytest.raises(ValueError, match="Unassigned or Assigned"):
        await _assign(_service(db_path))


async def test_a_driver_who_does_not_exist_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)

    with pytest.raises(ValueError, match="not found"):
        await _assign(_service(db_path), profile_id=9999)


async def test_a_driver_may_hold_only_one_seat_per_division(tmp_path):
    """The refusal names the division rather than the team, because a manager placing
    somebody twice has usually forgotten *where* they already are."""
    db_path = await _make_db(tmp_path)
    service = _service(db_path)
    await _assign(service)

    with pytest.raises(ValueError, match="Division 1"):
        await _assign(service)


# ---------------------------------------------------------------------------
# Finding a seat in a race team
# ---------------------------------------------------------------------------


async def test_the_lowest_numbered_free_seat_is_taken(tmp_path):
    """A driver in seat 2 with seat 1 empty would draw a hole in the lineup graphic,
    which addresses a seat by its ordinal."""
    db_path = await _make_db(tmp_path, team_seats=3)

    await _assign(_service(db_path))

    assert await _seat_of(db_path) == (1, "Alpha")


async def test_the_next_driver_takes_the_next_seat(tmp_path):
    db_path = await _make_db(tmp_path, team_seats=3)
    service = _service(db_path)
    await _add_driver(db_path, 102)

    await _assign(service, profile_id=PROFILE_ID)
    await _assign(service, profile_id=102)

    assert await _seat_of(db_path, 102) == (2, "Alpha")


async def test_a_full_race_team_refuses_the_next_driver(tmp_path):
    """The seats are finite and were created when the division was seeded; there is no
    seat to make."""
    db_path = await _make_db(tmp_path, team_seats=1)
    service = _service(db_path)
    await _add_driver(db_path, 102)
    await _assign(service, profile_id=PROFILE_ID)

    with pytest.raises(ValueError, match="no available seats"):
        await _assign(service, profile_id=102)


async def test_a_team_that_does_not_exist_is_refused_by_name(tmp_path):
    db_path = await _make_db(tmp_path)

    with pytest.raises(ValueError, match="Ferrari"):
        await _assign(_service(db_path), team="Ferrari")


# ---------------------------------------------------------------------------
# The reserve team, which has no bound
# ---------------------------------------------------------------------------


async def test_a_reserve_takes_a_free_reserve_seat(tmp_path):
    db_path = await _make_db(tmp_path, reserve_seats=2)

    await _assign(_service(db_path), team="Reserve")

    assert await _seat_of(db_path) == (1, "Reserve")


async def test_the_reserve_team_makes_another_seat_rather_than_refusing(tmp_path):
    """A division can carry as many reserves as turn up, so running out is not a refusal —
    it is a seat to create. The near-identical SQL either side of this branch is why the
    two cases are tested apart."""
    db_path = await _make_db(tmp_path, reserve_seats=1)
    service = _service(db_path)
    await _add_driver(db_path, 102)
    await _assign(service, team="Reserve", profile_id=PROFILE_ID)

    await _assign(service, team="Reserve", profile_id=102)

    assert await _seat_of(db_path, 102) == (2, "Reserve")


async def test_a_created_reserve_seat_continues_the_numbering(tmp_path):
    """Taken from the highest existing number rather than the count, so a seat removed
    earlier does not cause a collision."""
    db_path = await _make_db(tmp_path, reserve_seats=0)
    # A seat numbered 7 already taken, with the numbers below it removed — the case a
    # count-based next number would collide on. The occupant has to be a real profile,
    # because `team_seats.driver_profile_id` is a foreign key.
    await _add_driver(db_path, 199)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (11, 7, 199)"
        )
        await db.commit()

    await _assign(_service(db_path), team="Reserve")

    assert await _seat_of(db_path) == (8, "Reserve")


async def test_a_reserve_team_with_no_seats_at_all_still_takes_a_driver(tmp_path):
    """A division seeded before the reserve team existed, or one whose seats were cleared."""
    db_path = await _make_db(tmp_path, reserve_seats=0)

    await _assign(_service(db_path), team="Reserve")

    assert await _seat_of(db_path) == (1, "Reserve")


# ---------------------------------------------------------------------------
# What the placement records
# ---------------------------------------------------------------------------


async def test_the_seat_is_occupied_and_the_assignment_recorded(tmp_path):
    """Both, atomically — a seat held by a driver with no season assignment would draw on
    the lineup but score for nobody."""
    db_path = await _make_db(tmp_path)

    await _assign(_service(db_path))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_season_assignments "
            "WHERE driver_profile_id = ? AND division_id = ?",
            (PROFILE_ID, DIVISION_ID),
        )
        assert (await cursor.fetchone())["n"] == 1
    assert await _seat_of(db_path) is not None


async def test_the_assignment_points_at_the_seat_that_was_taken(tmp_path):
    """`team_seat_id` is how everything downstream finds which car a driver is in."""
    db_path = await _make_db(tmp_path)

    await _assign(_service(db_path))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT dsa.team_seat_id, ts.driver_profile_id FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "WHERE dsa.driver_profile_id = ?",
            (PROFILE_ID,),
        )
        row = await cursor.fetchone()
    assert row is not None
    assert row["driver_profile_id"] == PROFILE_ID


async def test_a_placed_driver_becomes_assigned(tmp_path):
    db_path = await _make_db(tmp_path, state="UNASSIGNED")

    await _assign(_service(db_path))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id = ?", (PROFILE_ID,)
        )
        assert (await cursor.fetchone())["current_state"] == DriverState.ASSIGNED.value


async def test_a_refused_placement_leaves_no_trace(tmp_path):
    """The guards run before anything is written, so a refusal has to leave the division
    exactly as it stood — a half-placed driver has no command to finish or undo it."""
    db_path = await _make_db(tmp_path, team_seats=1)
    service = _service(db_path)
    await _add_driver(db_path, 102)
    await _assign(service, profile_id=PROFILE_ID)

    with pytest.raises(ValueError):
        await _assign(service, profile_id=102)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_season_assignments WHERE driver_profile_id = 102"
        )
        assert (await cursor.fetchone())["n"] == 0
