"""The final attendance sheet a season's completion posts, where its division has no attendance
channel set (#439, slice 6b, fault 5).

A completion's `final_sheet` job used to complete having posted nothing where the division's
attendance channel was unset, where a sheet a command asked for stops the queue naming the command
that sets the channel. It stops the queue too, while a sheet no longer due, with attendance off,
is still dropped.

The league is `tests.support.season_league`'s `pending_completion_league`: season 3, every round
of Pro and Am final, Pro's rounds 1 and 2 and Am's round 1 with results, completed by admin 77
through `/season complete`.
"""

from __future__ import annotations

import json

import pytest

from tests.support.change_queue import run_queue, stopped_job
from tests.support.season_league import (
    DIVISIONS,
    PRO,
    complete_season,
    pending_completion_league,
)

NO_SHEET_CHANNEL_STOP = (
    "#439: a season end's final sheet with no attendance channel completes posting nothing, "
    "rather than stopping the queue naming /attendance channel attendance"
)

PRO_CH = DIVISIONS[PRO][3]


async def _unset_pro_attendance_channel(league) -> None:
    await league.write(
        "UPDATE attendance_division_config SET attendance_channel_id = NULL WHERE division_id = ?",
        PRO,
    )


@pytest.mark.xfail(strict=True, reason=NO_SHEET_CHANNEL_STOP)
async def test_the_final_sheet_with_no_channel_stops_the_queue(tmp_path):
    """Attendance is on, and Pro's attendance channel was never set. Admin 77 completes the
    season: the queue stops at Pro's final sheet, its stop line naming the command that sets the
    channel, and the season is not completed while it stands stopped."""
    league = await pending_completion_league(tmp_path, attendance=True)
    await _unset_pro_attendance_channel(league)
    await complete_season(league)
    assert not league.errors, league.errors

    await run_queue(league.bot)

    job = await stopped_job(league.db_path)
    assert job is not None and job["name"] == "final_sheet"
    assert json.loads(job["payload"])["division_id"] == PRO
    assert "/attendance channel attendance" in league.log()
    assert (await league.season())["status"] == "ACTIVE"


async def test_a_sheet_with_attendance_off_is_still_dropped(tmp_path):
    """Attendance is off, and Pro's attendance channel is unset. Admin 77 completes the season:
    no sheet is due, so nothing stops the queue and the season is completed."""
    league = await pending_completion_league(tmp_path, attendance=False)
    await _unset_pro_attendance_channel(league)
    await complete_season(league)
    assert not league.errors, league.errors

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await league.season())["status"] == "COMPLETED"
    assert league.texts(PRO_CH.attendance) == []
