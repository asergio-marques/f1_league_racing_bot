"""Who a division's attendance sheet lists (issue #220).

Every driver currently seated full-time in the division, and every driver who has held a seat in
it during the season — full-time, or as a Reserve placed into a seat for a round — whether or not
they still hold it. Before #220 the sheet read the drivers still seated, so the sheet posted
after an autosack or an autoreserve left out the very drivers it had just sanctioned, and the
"reached point limit" annotation could never appear.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.sheet.attendance_sheet import _sheet_rows

SERVER_ID = 22170
PRO, AM = 1, 2


@pytest.fixture
async def db_path(tmp_path):
    """Pro has two scored rounds. Driver 1 seated throughout; driver 2 sacked after round 1;
    driver 3 moved to Reserve after round 1; driver 4 a Reserve placed into a seat for round 2;
    driver 5 a Reserve never placed; driver 6 seated full-time but not yet scored."""
    path = str(tmp_path / "sheet.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, autosack_threshold) VALUES (?, 10)",
            (1,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (1, '2026-01-01', 'ACTIVE', 1, 'ONGOING')"
        )
        for division_id, name in ((PRO, "Pro"), (AM, "Am")):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
                "VALUES (?, 1, ?, 1, ?, 'ACTIVE')",
                (division_id, name, division_id),
            )
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, 'Alpha', 'Alpha', 6, 0), (?, ?, 'Reserve', 'Reserve', 6, 1)",
                (division_id * 10, division_id, division_id * 10 + 1, division_id),
            )
            for number in (1, 2):
                await db.execute(
                    "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                    "scheduled_at, status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone Circuit', "
                    "'2026-06-01T14:00:00+00:00', 'FINAL')",
                    (division_id * 100 + number, division_id, number),
                )
        for pid in range(1, 7):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (pid, str(1000 + pid)),
            )

        async def seat(pid, team_id, division_id):
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (team_id, pid, pid),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
                "VALUES (?, 1, ?, ?, 1)",
                (pid, division_id, cursor.lastrowid),
            )

        await seat(1, 10, PRO)
        await seat(3, 11, PRO)   # now in Reserve
        await seat(4, 11, PRO)   # Reserve
        await seat(5, 11, PRO)   # Reserve, never placed
        await seat(6, 10, PRO)   # full-time, not yet scored
        await seat(2, 20, AM)    # sacked from Pro; still in Am

        async def attendance(round_id, pid, total, assigned=None):
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "assigned_team_id, total_points_after) VALUES (?, ?, ?, ?, ?)",
                (round_id, round_id // 100, pid, assigned, total),
            )

        await attendance(101, 1, 0)
        await attendance(101, 2, 6)
        await attendance(101, 3, 4)
        await attendance(101, 5, None)          # unplaced reserve: never scored
        await attendance(102, 1, 2)
        await attendance(102, 3, None)          # in Reserve now, not placed
        await attendance(102, 4, 3, assigned=10)
        await db.commit()
    return path


async def _listed(db_path, round_id, division_id=PRO):
    async with get_connection(db_path) as db:
        rows = await _sheet_rows(db, round_id, division_id)
    return {row["driver_profile_id"]: row["total_points_after"] for row in rows}


async def test_a_reserve_placed_into_a_seat_is_listed_from_that_round(db_path):
    assert 4 not in await _listed(db_path, 101)
    assert (await _listed(db_path, 102))[4] == 3


async def test_the_full_sheet_after_round_two(db_path):
    """Driver 1 seated throughout is listed on 2; driver 2, autosacked, is still listed on 6;
    driver 3, autoreserved, is still listed on 4; driver 4, a Reserve placed into a seat, is
    listed on 3; driver 5, a Reserve never placed, is not listed; driver 6, seated but not yet
    scored, is listed on nought."""
    assert await _listed(db_path, 102) == {1: 2, 2: 6, 3: 4, 4: 3, 6: 0}
