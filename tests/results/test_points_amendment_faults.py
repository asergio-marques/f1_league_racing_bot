"""What stops a points amendment's approval being published: results' `approval_faults`.

It composes results' own channel faults with attendance's, which it asks through the
`AttendanceAfterReview` hook it is handed (architecture.md, "Results reaches attendance through a
protocol, handed in by the builder"), never by importing attendance. Every test runs on
`tests/support/points_league.py`'s league.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.services.modules.module_service import SwitchNotSignedUp
from tests.support.points_league import BETA_ATTENDANCE, SEASON_ID, points_league

#: Why each test below fails until results holds the check.
NOT_IN_RESULTS = "#284: approval_faults is still core's and asks attendance_service directly"


def _switches(**answer: object) -> MagicMock:
    """The module switches the check is handed: attendance's reads as *answer* says."""
    switches = MagicMock()
    switches.is_attendance_enabled = AsyncMock(**answer)
    return switches


@pytest.mark.xfail(strict=True, reason=NOT_IN_RESULTS)
async def test_approval_faults_names_a_cancelled_division_s_deleted_attendance_channel(tmp_path):
    """A division cancelled mid-season, attendance on, whose attendance channel (722) has been
    deleted: the approval reposts and recalculates its raced rounds too, so the check names the
    channel, as the results specification's "every division's" channels require (owner,
    2026-10-06, "Refuse at the press"). Attendance answers through the real hook it is handed.
    Whether attendance is on is asked of the module switches the check is handed, not of the
    bot's own, which say it is off."""
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview
    from leaguebot.results.services.points.points_amendment_faults import approval_faults

    league = await points_league(tmp_path, cancelled_division=True, attendance=True)
    league.remove_channel(BETA_ATTENDANCE)
    league.bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    hook = AttendanceAfterReview(league.bot, MagicMock())

    faults = await approval_faults(
        league.db_path, SEASON_ID, league.bot,
        module_service=_switches(return_value=True), attendance=hook,
    )

    named = [fault for fault in faults if "attendance channel" in fault]
    assert len(named) == 1, faults
    assert f"**Beta** — the attendance channel (id {BETA_ATTENDANCE})" in named[0]


@pytest.mark.xfail(strict=True, reason=NOT_IN_RESULTS)
async def test_approval_faults_asks_attendance_s_faults_through_the_hook_it_is_handed(
    tmp_path, monkeypatch,
):
    """Attendance is on and every results channel stands. The hook handed in answers one fault
    of its own: it is asked once, for the season and the league's server, and its fault is in
    the result. No attendance module is imported to ask it: each is made unimportable for the
    call."""
    from leaguebot.results.services.points.points_amendment_faults import approval_faults

    league = await points_league(tmp_path)
    hook = MagicMock()
    hook.recalculation_faults = AsyncMock(return_value=["the hook's own fault"])
    for name in [name for name in sys.modules if name.startswith("leaguebot.attendance")]:
        monkeypatch.setitem(sys.modules, name, None)

    faults = await approval_faults(
        league.db_path, SEASON_ID, league.bot,
        module_service=_switches(return_value=True), attendance=hook,
    )

    assert faults == ["the hook's own fault"]
    hook.recalculation_faults.assert_awaited_once_with(SEASON_ID, league.guild)


@pytest.mark.xfail(strict=True, reason=NOT_IN_RESULTS)
async def test_an_attendance_switch_nobody_signed_up_for_is_raised_not_read_as_off(tmp_path):
    """The attendance module's switch reader was never signed up (`SwitchNotSignedUp`): that is a
    fault in the bot's assembly, not a switch that cannot be read, so `approval_faults` raises it
    rather than standing aside and reading the module as off, and attendance is never asked. The
    switch is the handed module switches'."""
    from leaguebot.results.services.points.points_amendment_faults import approval_faults

    league = await points_league(tmp_path)
    hook = MagicMock()
    hook.recalculation_faults = AsyncMock(return_value=[])

    with pytest.raises(SwitchNotSignedUp, match="attendance"):
        await approval_faults(
            league.db_path, SEASON_ID, league.bot,
            module_service=_switches(side_effect=SwitchNotSignedUp("attendance")),
            attendance=hook,
        )
    hook.recalculation_faults.assert_not_awaited()
