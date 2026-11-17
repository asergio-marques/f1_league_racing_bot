"""Core's reader of a division's seating, as the lineup of record shows it, and of the drivers
assigned in the active season.

Every module that draws or lists who races in a division asks `division_seating` and joins none of
core's tables itself (core's text lineup, image's lineup, the standings' seats, the preview and the
portrait roster); the portrait refresh asks `active_season_driver_ids`. These tests pin both readers
directly, which the lineup, standings, preview and portrait tests reach only through their callers.
"""
from __future__ import annotations

import os

import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from leaguebot.core.services.drivers.division_seating import (
    active_season_driver_ids,
    division_seating,
)

DIVISION = 1


async def _league(tmp_path, *, season_status: str = "ACTIVE") -> str:
    """Season 1 (in *season_status*) with division Pro, seeded parents first:

    - team Ferrari (two seats) made first, team Reserve (one seat) made second as a reserve team,
      and team McLaren (two seats) made third, so the reserve team sits between the regular ones;
    - Ferrari seat 1 held by Alex (ASSIGNED, confirmed), Ferrari seat 2 by Sam, whose state is
      UNASSIGNED (confirmed);
    - McLaren seat 1 held by the test driver "Test Tom" (ASSIGNED, confirmed), McLaren seat 2 by Zara
      (ASSIGNED), placed mid-season and not yet confirmed (committed 0);
    - the reserve seat left empty.
    """
    db_path = os.path.join(str(tmp_path), "seating.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', ?, 1)",
            (season_status,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (?, 1, 'Pro', 1)",
            (DIVISION,),
        )
        for team_id, name, seats, reserve in (
            (1, "Ferrari", 2, 0), (2, "Reserve", 1, 1), (3, "McLaren", 2, 0),
        ):
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, ?, ?)",
                (team_id, DIVISION, name, f"{name} Racing", seats, reserve),
            )
            for seat in range(1, seats + 1):
                await db.execute(
                    "INSERT INTO team_seats (id, team_instance_id, seat_number) "
                    "VALUES (?, ?, ?)",
                    (team_id * 10 + seat, team_id, seat),
                )
        for profile_id, user, state, test_name in (
            (1, "1001", "ASSIGNED", None),
            (2, "1002", "UNASSIGNED", None),
            (3, "9000001", "ASSIGNED", "Test Tom"),
            (4, "1004", "ASSIGNED", None),
        ):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, ?, ?, ?, ?)",
                (profile_id, user, state, int(test_name is not None), test_name),
            )
        for profile_id, seat, committed in ((1, 11, 1), (2, 12, 1), (3, 31, 1), (4, 32, 0)):
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id, committed) VALUES (?, 1, ?, ?, ?)",
                (profile_id, DIVISION, seat, committed),
            )
        await db.commit()
    return db_path


def _held(teams) -> dict[tuple[str, int], str | None]:
    """Each seat, by team name and seat number, to the Discord id of who holds it or None."""
    return {
        (team.name, seat.seat_number):
            seat.occupant.discord_user_id if seat.occupant is not None else None
        for team in teams for seat in team.seats
    }


async def test_the_seating_lists_regular_teams_first_with_every_seat(tmp_path):
    """Asked with no filter, the reader hands back Ferrari and McLaren (in the order they were
    made) before the reserve team, each with every seat in seat order, the empty reserve seat
    among them, and the team's name, full name, seat count and reserve flag."""
    db_path = await _league(tmp_path)

    teams = await division_seating(db_path, DIVISION)

    assert [team.name for team in teams] == ["Ferrari", "McLaren", "Reserve"]
    assert [team.is_reserve for team in teams] == [False, False, True]
    assert [team.full_name for team in teams] == [
        "Ferrari Racing", "McLaren Racing", "Reserve Racing"]
    assert [team.max_seats for team in teams] == [2, 2, 1]
    assert [[seat.seat_number for seat in team.seats] for team in teams] == [[1, 2], [1, 2], [1]]
    assert _held(teams)[("Reserve", 1)] is None


@pytest.mark.parametrize("include_uncommitted", [False, True])
async def test_a_placement_not_yet_confirmed_is_an_empty_seat_unless_asked_for(
        tmp_path, include_uncommitted):
    """Zara was placed in McLaren's second seat mid-season and not yet confirmed. The lineup of
    record shows the seat empty, still listed; asked with `include_uncommitted`, as the mid-season
    placements review asks, it shows Zara in it. The confirmed seats read the same either way."""
    db_path = await _league(tmp_path)

    teams = await division_seating(db_path, DIVISION, include_uncommitted=include_uncommitted)

    held = _held(teams)
    assert held[("McLaren", 2)] == ("1004" if include_uncommitted else None)
    assert held[("Ferrari", 1)] == "1001"
    assert held[("McLaren", 1)] == "9000001"


async def test_a_placement_not_yet_confirmed_shows_once_its_season_is_no_longer_active(tmp_path):
    """The same league with season 1 completed: Zara's unconfirmed placement is no longer
    mid-season, so the lineup of record shows her in McLaren's second seat."""
    db_path = await _league(tmp_path, season_status="COMPLETED")

    teams = await division_seating(db_path, DIVISION)

    assert _held(teams)[("McLaren", 2)] == "1004"


@pytest.mark.parametrize(("states", "sam"), [(None, "1002"), ({"ASSIGNED"}, None)],
                         ids=["every-state", "assigned-only"])
async def test_a_driver_whose_state_is_not_asked_for_leaves_the_seat_empty(
        tmp_path, states, sam):
    """Sam holds Ferrari's second seat with state UNASSIGNED. Asked for every state, as the
    picture and the standings ask, the seat is Sam's; asked for ASSIGNED drivers only, as the text
    lineup asks, the seat is listed empty. Alex's seat is held both ways."""
    db_path = await _league(tmp_path)

    teams = await division_seating(db_path, DIVISION, states=states)

    held = _held(teams)
    assert held[("Ferrari", 2)] == sam
    assert held[("Ferrari", 1)] == "1001"


async def test_a_seat_carries_its_driver_s_profile_fields(tmp_path):
    """McLaren's first seat is held by the test driver "Test Tom": the occupant carries his
    profile id, Discord id, state, the test flag and his test display name."""
    db_path = await _league(tmp_path)

    teams = await division_seating(db_path, DIVISION)

    tom = next(seat.occupant for team in teams for seat in team.seats
               if team.name == "McLaren" and seat.seat_number == 1)
    assert tom is not None
    assert (tom.profile_id, tom.discord_user_id, tom.current_state) == (3, "9000001", "ASSIGNED")
    assert tom.is_test_driver is True
    assert tom.test_display_name == "Test Tom"


async def test_the_active_season_s_drivers_are_sorted_and_leave_out_test_drivers(tmp_path):
    """Season 1 is active: Alex, Sam and Zara are assigned in it (Zara not yet confirmed, who
    counts) and so is the test driver Test Tom. The roster is their three Discord ids, sorted,
    Test Tom's synthetic id left out."""
    db_path = await _league(tmp_path)

    assert await active_season_driver_ids(db_path) == ["1001", "1002", "1004"]


async def test_no_driver_is_on_the_roster_while_no_season_is_active(tmp_path):
    """The same league with season 1 completed and none active: the roster is empty."""
    db_path = await _league(tmp_path, season_status="COMPLETED")

    assert await active_season_driver_ids(db_path) == []
