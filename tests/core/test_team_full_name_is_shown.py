"""A team is shown by its full name, and found by its shorthand (#381).

Every fixture in the suite gives a team the same two names, so nothing else here would notice
the two being confused. These build a team whose names differ — "RBR" and "Oracle Red Bull
Racing" — and hold each reader to the one it is supposed to use: what a league reads is the full
name; what keys artwork, a seat or a role mapping is the shorthand.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.attendance.services.attendance_service import _seat_team_keys, _seat_team_names  # noqa: E402
from leaguebot.results.services.standings_service import opening_team_standings  # noqa: E402
from leaguebot.core.services.team_service import (  # noqa: E402
    team_artwork_keys_for_instances,
    team_names_for_instances,
)

DIVISION_ID = 5
DRIVER = 900000000000000001

#: Shorthand, full name — deliberately in the reverse of alphabetical order against each
#: other, so a reader ordering by the wrong one is caught.
TEAMS = [("RBR", "Oracle Red Bull Racing"), ("ZEN", "Alpha Zenith Racing")]


async def _league(tmp_path) -> tuple[str, dict[str, int]]:
    db_path = str(tmp_path / "league.db")
    await run_migrations(db_path)
    ids: dict[str, int] = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, 1, 'Pro', 1, 555)",
            (DIVISION_ID,),
        )
        for shorthand, full_name in TEAMS:
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, 2, 0)",
                (DIVISION_ID, shorthand, full_name),
            )
            ids[shorthand] = cursor.lastrowid
        profile = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES (?, 'ASSIGNED')",
            (str(DRIVER),),
        )
        seat = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, 1, ?)",
            (ids["RBR"], profile.lastrowid),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
            "VALUES (?, 1, ?, ?, 1)",
            (profile.lastrowid, DIVISION_ID, seat.lastrowid),
        )
        await db.commit()
    return db_path, ids


async def test_a_recorded_team_is_named_by_its_full_name(tmp_path):
    """What every post and graphic prints for a team a result recorded."""
    db_path, ids = await _league(tmp_path)

    names = await team_names_for_instances(db_path, ids.values())

    assert names[ids["RBR"]] == "Oracle Red Bull Racing"


async def test_a_recorded_team_s_artwork_is_found_by_its_shorthand(tmp_path):
    db_path, ids = await _league(tmp_path)

    keys = await team_artwork_keys_for_instances(db_path, ids.values())

    assert keys[ids["RBR"]] == "RBR"


async def test_the_opening_constructors_are_ordered_by_their_full_names(tmp_path):
    """Alphabetically by the name the reader sees, not by the shorthand beside it."""
    db_path, ids = await _league(tmp_path)

    standings = await opening_team_standings(db_path, DIVISION_ID)

    assert [s.team_instance_id for s in standings] == [ids["ZEN"], ids["RBR"]]


async def test_the_attendance_sheet_names_the_full_name_and_keys_the_badge_by_the_shorthand(
    tmp_path,
):
    db_path, _ids = await _league(tmp_path)

    names = await _seat_team_names(db_path, DIVISION_ID, [DRIVER])
    keys = await _seat_team_keys(db_path, DIVISION_ID, [DRIVER])

    assert names == {DRIVER: "Oracle Red Bull Racing"}
    assert keys == {DRIVER: "RBR"}
