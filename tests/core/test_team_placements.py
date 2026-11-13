"""Core's readers of who is placed and seated in a division, read on a connection the caller holds.

Attendance reads its drivers, their seats and their names through these, in place of its own joins
of core's tables (architecture.md, "The database"). Each is read here over one league: Season 1 is
active with Pro (11) and Am (12). Driver 1 (account 101) is placed and seated in Pro's Alpha and in
Am's Bravo; driver 2 (102) is placed in Pro's Alpha on a placement not yet confirmed; driver 3 (103)
in Pro's reserve team, his test name "Test Three"; driver 4 (104) in Am's reserve team; and driver 5
(105) holds a seat of Pro's Alpha with no placement row at all.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.drivers.driver_service import driver_identities_on
from leaguebot.core.services.drivers.team_service import TeamService
from tests.attendance.test_seat_join_equivalence import AM, PRO, _seed


@pytest.fixture
async def league(tmp_path):
    """The league of the module docstring, its database's path."""
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (5, '105', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (6, 2, 3, 5)"
        )
        await db.execute("UPDATE driver_profiles SET test_display_name = 'Test Three' WHERE id = 3")
        await db.commit()
    return path


async def test_placed_drivers_on_reads_each_placement_of_the_division_as_it_reads(league):
    """Pro's placements are read in the order they were made, each with its profile, account,
    state and the team of the seat it names, the unconfirmed one included and the seat holder with
    no placement left out; Am's carry driver 1 again, in Bravo; a division with none reads empty."""
    from leaguebot.core.models.team import PlacedDriver

    async with get_connection(league) as db:
        pro = await TeamService.placed_drivers_on(db, PRO)
        am = await TeamService.placed_drivers_on(db, AM)
        none = await TeamService.placed_drivers_on(db, 999)

    assert pro == [
        PlacedDriver(1, 101, "ASSIGNED", "Alpha Motorsport", "Alpha", False),
        PlacedDriver(2, 102, "ASSIGNED", "Alpha Motorsport", "Alpha", False),
        PlacedDriver(3, 103, "ASSIGNED", "Reserve Pool", "Reserve", True),
    ]
    assert am == [
        PlacedDriver(1, 101, "ASSIGNED", "Bravo Racing", "Bravo", False),
        PlacedDriver(4, 104, "ASSIGNED", "Reserve Pool", "Reserve", True),
    ]
    assert none == []


async def test_divisions_placed_in_on_gives_a_driver_s_divisions_of_the_season(league):
    """Driver 1 is placed in both divisions, read in id order; driver 2 in Pro alone; an unknown
    driver, or a season the driver is not placed in, reads empty."""
    async with get_connection(league) as db:
        assert await TeamService.divisions_placed_in_on(db, 1, 1) == [PRO, AM]
        assert await TeamService.divisions_placed_in_on(db, 2, 1) == [PRO]
        assert await TeamService.divisions_placed_in_on(db, 99, 1) == []
        assert await TeamService.divisions_placed_in_on(db, 1, 2) == []


@pytest.mark.parametrize("division_id", [PRO, AM], ids=["Pro", "Am"])
async def test_seat_holders_on_agrees_with_seated_drivers_on_and_adds_the_profile(
    league, division_id,
):
    """Each division's seat holders are the drivers `seated_drivers_on` seats, each with the same
    team and reserve flag, and with their profile: in Pro, driver 1 in Alpha, driver 3 in the
    reserve team and driver 5, whose seat has no placement row, in Alpha, while driver 2's seat,
    taken on a placement not yet confirmed, reads as empty; in Am, driver 1 in Bravo and driver 4
    in the reserve team."""
    async with get_connection(league) as db:
        holders = await TeamService.seat_holders_on(db, division_id)
        seated = await TeamService.seated_drivers_on(db, division_id)

    assert {(h.discord_user_id, h.team_name, h.is_reserve) for h in holders} == {
        (s.discord_user_id, s.team_name, s.is_reserve) for s in seated
    }
    assert sorted((h.profile_id, h.discord_user_id) for h in holders) == {
        PRO: [(1, 101), (3, 103), (5, 105)],
        AM: [(1, 101), (4, 104)],
    }[division_id]


async def test_driver_identities_on_names_each_profile_and_omits_an_unknown_one(league):
    """Drivers 1 and 3 and an unknown 99 are asked for: each known driver is given as their current
    account and test name, driver 3's being "Test Three", and 99 is left out; asking for none reads
    empty."""
    async with get_connection(league) as db:
        assert await driver_identities_on(db, [3, 1, 99]) == {
            1: ("101", None),
            3: ("103", "Test Three"),
        }
        assert await driver_identities_on(db, []) == {}


async def test_division_teams_on_reads_as_get_division_teams_and_sees_its_own_connection_s_write(
    league,
):
    """Pro's teams are read on a held connection: Alpha first and the reserve team last, each with
    its seats in seat order, as `get_division_teams` reads them on its own. Alpha then has its full
    name changed on that connection, not yet committed, and the read on it gives the new name. With
    *committed_only*, driver 2's seat, taken on a placement not yet confirmed, reads as empty, while
    driver 5's, which has no placement row, is still read as held."""
    service = TeamService(league)
    expected = await service.get_division_teams(PRO)
    alpha_seats = [
        {"seat_number": 1, "driver_profile_id": 1, "discord_user_id": "101"},
        {"seat_number": 2, "driver_profile_id": 2, "discord_user_id": "102"},
        {"seat_number": 3, "driver_profile_id": 5, "discord_user_id": "105"},
    ]
    reserve = {
        "id": 1, "name": "Reserve", "full_name": "Reserve Pool", "max_seats": 2,
        "is_reserve": True,
        "seats": [{"seat_number": 1, "driver_profile_id": 3, "discord_user_id": "103"}],
    }
    alpha = {
        "id": 2, "name": "Alpha", "full_name": "Alpha Motorsport", "max_seats": 2,
        "is_reserve": False, "seats": alpha_seats,
    }
    assert expected == [alpha, reserve]

    async with get_connection(league) as db:
        assert await TeamService.division_teams_on(db, PRO) == expected
        await db.execute("UPDATE team_instances SET full_name = 'Alpha Racing' WHERE id = 2")
        renamed = await TeamService.division_teams_on(db, PRO)
        committed = await TeamService.division_teams_on(db, PRO, committed_only=True)

    assert renamed == [{**alpha, "full_name": "Alpha Racing"}, reserve]
    assert committed[0]["seats"] == [
        alpha_seats[0],
        {"seat_number": 2, "driver_profile_id": None, "discord_user_id": None},
        alpha_seats[2],
    ]
    assert committed[1] == reserve
