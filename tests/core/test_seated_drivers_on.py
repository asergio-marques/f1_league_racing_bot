"""The drivers seated in a division, read on a handed connection (#284, S7).

`TeamService.seated_drivers_on` is the core half of what a module's query joining `team_seats`,
`team_instances` and `driver_profiles` asked when it ordered a division's standings or opened its
grid.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.team import SeatedDriver
from leaguebot.core.services.drivers.team_service import TeamService


async def _seed(path: str) -> None:
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        for division_id, name, tier in ((11, "Pro", 1), (12, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, 1, ?, ?, 555)",
                (division_id, name, tier),
            )
        for profile_id, user in ((1, "101"), (2, "102"), (3, "103"), (4, "104")):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, user),
            )
        # (instance, division, name, full name, is_reserve): Pro's reserve team and its
        # front-runner, and Am's reserve team.
        for instance_id, division_id, name, full_name, reserve in (
            (1, 11, "Reserve", "Reserve Pool", 1),
            (2, 11, "Alpha", "Alpha Motorsport", 0),
            (3, 12, "Reserve", "Reserve Pool", 1),
        ):
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, 3, ?)",
                (instance_id, division_id, name, full_name, reserve),
            )
        # (seat, instance, number, profile): Pro's reserve seats hold 101, nobody and 102;
        # Alpha's holds 103; Am's reserve holds 104.
        for seat_id, instance_id, number, profile in (
            (1, 1, 1, 1), (2, 1, 2, None), (3, 1, 3, 2), (4, 2, 1, 3), (5, 3, 1, 4),
        ):
            await db.execute(
                "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?, ?)",
                (seat_id, instance_id, number, profile),
            )
        # Profile 102 sits in seat 3 on a placement not yet confirmed.
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id, committed) VALUES (2, 1, 11, 3, 0)"
        )
        await db.commit()


async def test_a_divisions_seated_drivers_are_read_with_their_team_and_no_others(tmp_path):
    """Pro seats 101 in its reserve team, 103 in Alpha, and 102 on a placement not yet confirmed:
    101 and 103 come back with the team's full name and the reserve flag, and not 102, the empty
    seat or Am's 104."""
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        seated = await TeamService.seated_drivers_on(db, 11)
    assert sorted(seated, key=lambda s: s.discord_user_id) == [
        SeatedDriver(101, "Reserve Pool", True),
        SeatedDriver(103, "Alpha Motorsport", False),
    ]


async def test_a_division_with_no_seated_driver_or_none_reads_an_empty_list(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL WHERE team_instance_id = 3")
        assert await TeamService.seated_drivers_on(db, 12) == []
        assert await TeamService.seated_drivers_on(db, 99) == []
