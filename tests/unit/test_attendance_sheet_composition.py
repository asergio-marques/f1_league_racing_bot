"""Who a division's attendance sheet lists (issue #220).

Every driver currently seated full-time in the division, and every driver who has held a seat in
it during the season — full-time, or as a Reserve placed into a seat for a round — whether or not
they still hold it. Before #220 the sheet read the drivers still seated, so the sheet posted
after an autosack or an autoreserve left out the very drivers it had just sanctioned, and the
"reached point limit" annotation could never appear.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.attendance_service import _sheet_rows, enforce_attendance_sanctions  # noqa: E402

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
                    "'2026-06-01T14:00:00', 'FINAL')",
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


async def test_sheet_lists_autosacked_driver(db_path):
    assert (await _listed(db_path, 102))[2] == 6


async def test_sheet_lists_autoreserved_driver(db_path):
    assert (await _listed(db_path, 102))[3] == 4


async def test_a_reserve_placed_into_a_seat_is_listed_from_that_round(db_path):
    assert 4 not in await _listed(db_path, 101)
    assert (await _listed(db_path, 102))[4] == 3


async def test_a_reserve_never_placed_is_not_listed(db_path):
    assert 5 not in await _listed(db_path, 102)


async def test_a_driver_seated_but_not_yet_scored_is_listed_on_nought(db_path):
    assert (await _listed(db_path, 102))[6] == 0


async def test_the_full_sheet_after_round_two(db_path):
    assert await _listed(db_path, 102) == {1: 2, 2: 6, 3: 4, 4: 3, 6: 0}


async def _seat_driver_two_in_pro_past_the_threshold(db_path, *, am_scored: bool = True):
    """Driver 2 seated in Pro, past the autosack threshold at Pro's round 2, and holding an Am seat."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) VALUES (10, 9, 2)"
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) VALUES (2, 1, 1, ?, 1)",
            (cursor.lastrowid,),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "total_points_after) VALUES (102, 1, 2, 12)"
        )
        if am_scored:
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "total_points_after) VALUES (201, 2, 2, 0)"
            )
        await db.commit()


async def _enforce(db_path, *, sack=None, post=None):
    bot = MagicMock()
    bot.user.id = 1
    bot.output_router.post_log = AsyncMock()
    bot.placement_service.sack_driver = sack or AsyncMock()
    bot.placement_service._refresh_lineup_post = AsyncMock()

    with patch("services.attendance_service.post_attendance_sheet",
               new=post or AsyncMock()) as posted, \
            patch("services.verdict_announcement_service.banner_for_round", return_value=None), \
            patch("services.verdict_announcement_service.post_autosanction_announcement",
                  # `[] += AsyncMock()` rebinds posting_faults to a MagicMock rather
                  # than extending it, which leaves SanctionOutcome.complete false
                  # for every run in this file. It must return a list (#237).
                  new=AsyncMock(return_value=[])):
        await enforce_attendance_sanctions(bot, MagicMock(), db_path, 102, PRO, 1)
    return posted


async def test_a_division_that_has_posted_no_sheet_is_not_reposted(db_path):
    """There is no sheet in it to correct."""
    await _seat_driver_two_in_pro_past_the_threshold(db_path, am_scored=False)

    posted = await _enforce(db_path)

    assert [call.args[4] for call in posted.await_args_list] == [PRO]


async def test_a_sack_that_fails_reposts_no_other_division(db_path):
    """Only a driver actually sacked has been taken out of the other divisions."""
    await _seat_driver_two_in_pro_past_the_threshold(db_path)

    posted = await _enforce(db_path, sack=AsyncMock(side_effect=ValueError("already gone")))

    assert posted.await_count == 0


async def test_an_other_division_whose_sheet_fails_does_not_stop_the_run(db_path):
    await _seat_driver_two_in_pro_past_the_threshold(db_path)
    calls = []

    async def post(bot, guild, db_path, round_id, division_id, **kwargs):
        calls.append(division_id)
        if division_id == AM:
            raise RuntimeError("channel gone")

    await _enforce(db_path, post=post)

    assert calls == [PRO, AM]


async def test_autosack_reposts_every_division(db_path):
    """A driver sacked for their points in one division loses every seat in every division,
    so every division they sat in has its sheet posted again."""
    async with get_connection(db_path) as db:
        # Driver 2 is seated in Pro too, past the threshold at Pro's round 2, and has a sheet in Am.
        cursor = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) VALUES (10, 9, 2)"
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) VALUES (2, 1, 1, ?, 1)",
            (cursor.lastrowid,),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "total_points_after) VALUES (102, 1, 2, 12), (201, 2, 2, 0)"
        )
        await db.commit()

    bot = MagicMock()
    bot.user.id = 1
    bot.output_router.post_log = AsyncMock()
    bot.placement_service.sack_driver = AsyncMock()
    bot.placement_service._refresh_lineup_post = AsyncMock()

    with patch("services.attendance_service.post_attendance_sheet", new=AsyncMock()) as posted, \
            patch("services.verdict_announcement_service.banner_for_round", return_value=None), \
            patch("services.verdict_announcement_service.post_autosanction_announcement",
                  # `[] += AsyncMock()` rebinds posting_faults to a MagicMock rather
                  # than extending it, which leaves SanctionOutcome.complete false
                  # for every run in this file. It must return a list (#237).
                  new=AsyncMock(return_value=[])):
        await enforce_attendance_sanctions(bot, MagicMock(), db_path, 102, PRO, 1)

    posted_divisions = sorted(call.args[4] for call in posted.await_args_list)
    assert posted_divisions == [PRO, AM]
    am_call = next(c for c in posted.await_args_list if c.args[4] == AM)
    assert am_call.args[3] == 201
    assert am_call.kwargs["sanctioned_profile_ids"] == {2}
