"""Attendance's two ways of finding a driver's seat read the drivers core's reader seats (#285).

Attendance's reads find the seat a driver holds in a division in one of two ways: through the seat
their placement names (`driver_season_assignments.team_seat_id`, read off the driver's attendance
row, as scoring, the sanctions and the sheet do), or through the seat holding their profile in a
team of the placement's division (`team_seats.driver_profile_id`, as the check-in answer and the
reminders do). Both are to give way to `TeamService.seated_drivers_on`, which reads the seats
directly and counts a seat whose occupant's placement is not yet confirmed as empty. This pins,
before the move, that the three agree on the cases where they could part: a driver seated in two
divisions, and a seat taken on a placement not yet confirmed (whose driver the call writes no
attendance row for).
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.drivers.team_service import TeamService
from leaguebot.core.services.seasons.season_lifecycle_service import uncommitted_seat_excluded

PRO, AM = 11, 12


async def _seed(path: str) -> None:
    """Season 1 is active, with Pro (11) and Am (12), one round each. Driver 101 is seated in
    Alpha (Pro) and in Bravo (Am); 102 in Pro's Alpha on a placement not yet confirmed; 103 in
    Pro's reserve team; 104 in Am's reserve team. Every placement names its seat, and each round
    holds an attendance row for every confirmed driver of its division, as the call writes them."""
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (1, 1, '2026-01-01', 'ACTIVE')"
        )
        for division_id, name, tier in ((PRO, "Pro", 1), (AM, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, 1, ?, ?, 555)",
                (division_id, name, tier),
            )
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (?, ?, 1, 'NORMAL', '2026-03-01T18:00:00+00:00')",
                (division_id, division_id),
            )
        for profile_id, user in ((1, "101"), (2, "102"), (3, "103"), (4, "104")):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, user),
            )
        for instance_id, division_id, name, full_name, reserve in (
            (1, PRO, "Reserve", "Reserve Pool", 1),
            (2, PRO, "Alpha", "Alpha Motorsport", 0),
            (3, AM, "Reserve", "Reserve Pool", 1),
            (4, AM, "Bravo", "Bravo Racing", 0),
        ):
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (?, ?, ?, ?, 2, ?)",
                (instance_id, division_id, name, full_name, reserve),
            )
        # (seat, instance, number, profile, division, committed)
        seats = (
            (1, 2, 1, 1, PRO, 1), (2, 4, 1, 1, AM, 1), (3, 2, 2, 2, PRO, 0),
            (4, 1, 1, 3, PRO, 1), (5, 3, 1, 4, AM, 1),
        )
        for seat_id, instance_id, number, profile, division_id, committed in seats:
            await db.execute(
                "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?, ?)",
                (seat_id, instance_id, number, profile),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id, committed) VALUES (?, 1, ?, ?, ?)",
                (profile, division_id, seat_id, committed),
            )
            if committed:
                await db.execute(
                    "INSERT INTO driver_round_attendance (round_id, division_id, "
                    "driver_profile_id) VALUES (?, ?, ?)",
                    (division_id, division_id, profile),
                )
        await db.commit()


# The seat named by the placement, read off the driver's attendance row, as scoring, the
# sanctions and the sheet read it.
BY_PLACEMENT_SEAT = """
    SELECT dp.discord_user_id, ti.full_name, ti.is_reserve
      FROM driver_round_attendance dra
      JOIN driver_season_assignments dsa ON dsa.driver_profile_id = dra.driver_profile_id
      JOIN team_seats ts ON ts.id = dsa.team_seat_id
      JOIN team_instances ti ON ti.id = ts.team_instance_id
      JOIN driver_profiles dp ON dp.id = dra.driver_profile_id
     WHERE dra.round_id = ? AND dra.division_id = ? AND ti.division_id = ?
"""

# The seat holding the driver's profile in a team of the placement's division, as the check-in
# answer and the reminders read it.
BY_SEAT_HOLDER = f"""
    SELECT dp.discord_user_id, ti.full_name, ti.is_reserve
      FROM driver_season_assignments dsa
      JOIN team_seats ts ON ts.driver_profile_id = dsa.driver_profile_id
      JOIN team_instances ti ON ti.id = ts.team_instance_id
                            AND ti.division_id = dsa.division_id
      JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
     WHERE dsa.division_id = ? AND {uncommitted_seat_excluded("ts")}
"""  # noqa: S608


@pytest.mark.parametrize("division_id", [PRO, AM], ids=["Pro", "Am"])
async def test_both_seat_joins_read_the_drivers_core_s_reader_seats(tmp_path, division_id):
    """Each division is read three ways: through the placement's seat off its round's attendance
    rows, through the seat holding the driver in a team of the division, and through
    `TeamService.seated_drivers_on`. The three give the same drivers, each with the same team and
    reserve flag: 101 once per division in that division's team, 102 (not yet confirmed) in none,
    and each reserve driver in his own division's reserve team."""
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        by_placement = {
            (int(r[0]), r[1], bool(r[2]))
            for r in await (
                await db.execute(BY_PLACEMENT_SEAT, (division_id, division_id, division_id))
            ).fetchall()
        }
        by_holder = {
            (int(r[0]), r[1], bool(r[2]))
            for r in await (await db.execute(BY_SEAT_HOLDER, (division_id,))).fetchall()
        }
        seated = {
            (s.discord_user_id, s.team_name, s.is_reserve)
            for s in await TeamService.seated_drivers_on(db, division_id)
        }
    expected = {
        PRO: {(101, "Alpha Motorsport", False), (103, "Reserve Pool", True)},
        AM: {(101, "Bravo Racing", False), (104, "Reserve Pool", True)},
    }[division_id]
    assert seated == expected
    assert by_placement == seated
    assert by_holder == seated
