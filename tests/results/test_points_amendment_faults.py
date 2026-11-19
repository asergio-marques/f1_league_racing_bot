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

from leaguebot.framework.services.switches.module_service import SwitchNotSignedUp
from tests.support.points_league import BETA_ATTENDANCE, SEASON_ID, points_league
from tests.support.switches import answer_switch

#: Why the tests of the faults' kind fail until `approval_faults` gives one.
NO_KIND = "#513: approval_faults returns a bare list of lines, with no kind"


def _switches(answer: object) -> MagicMock:
    """The module switches the check is handed: attendance's reads as *answer* says."""
    switches = MagicMock()
    answer_switch(switches, "attendance", answer)
    return switches


@pytest.mark.xfail(strict=True, reason=NO_KIND)
async def test_approval_faults_names_a_cancelled_division_s_deleted_attendance_channel(tmp_path):
    """A division cancelled mid-season, attendance on, whose attendance channel (722) has been
    deleted: the approval reposts and recalculates its raced rounds too, so the check names the
    channel, as the results specification's "every division's" channels require (owner,
    2026-10-06, "Refuse at the press"). Attendance answers through the real hook it is handed.
    Whether attendance is on is asked of the module switches the check is handed, not of the
    bot's own, which say it is off. The faults are marked as channel faults."""
    from leaguebot.attendance.services.hooks.attendance_after_review import AttendanceAfterReview
    from leaguebot.results.services.points.points_amendment_faults import (
        ApprovalFaultKind,
        approval_faults,
    )

    league = await points_league(tmp_path, cancelled_division=True, attendance=True)
    league.remove_channel(BETA_ATTENDANCE)
    answer_switch(league.bot.module_service, "attendance", False)
    hook = AttendanceAfterReview(league.bot, MagicMock())

    faults = await approval_faults(
        league.db_path, SEASON_ID, league.bot,
        module_service=_switches(True), attendance=hook, drawing=league.bot.image_drawing,
    )

    assert faults.kind is ApprovalFaultKind.CHANNELS
    named = [fault for fault in faults.lines if "attendance channel" in fault]
    assert len(named) == 1, faults
    assert f"**Beta** — the attendance channel (id {BETA_ATTENDANCE})" in named[0]


@pytest.mark.xfail(strict=True, reason=NO_KIND)
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
        module_service=_switches(True), attendance=hook, drawing=league.bot.image_drawing,
    )

    assert faults.lines == ("the hook's own fault",)
    hook.recalculation_faults.assert_awaited_once_with(SEASON_ID, league.guild)


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
            module_service=_switches(SwitchNotSignedUp("attendance")),
            attendance=hook, drawing=league.bot.image_drawing,
        )
    hook.recalculation_faults.assert_not_awaited()


async def _season_unread(league) -> int:
    """No season by the id asked: the one the approval names has gone."""
    return SEASON_ID + 99


async def _not_in_server(league) -> int:
    league.bot.get_guild.return_value = None
    return SEASON_ID


async def _member_unresolved(league) -> int:
    league.guild.get_member = MagicMock(return_value=None)
    return SEASON_ID


@pytest.mark.xfail(strict=True, reason="#513: approval_faults returns a bare list of lines, with "
                   "no kind, and its season line says nothing was changed")
@pytest.mark.parametrize("setup, line, kind", [
    (_season_unread, "The season could not be read.", "SEASON"),
    (_not_in_server, "The bot is not in this server, so nothing can be reposted. "
                     "Check that it is still a member and try again.", "REACH"),
    (_member_unresolved, "The bot cannot read its own permissions in this server, so it cannot "
                         "tell whether the reposting would succeed. Try again in a moment.",
     "REACH"),
], ids=["season-unread", "bot-not-in-server", "bot-member-unresolved"])
async def test_a_fault_that_is_not_a_channel_is_marked_so(tmp_path, setup, line, kind):
    """Attendance on and every channel standing, but the approval cannot be checked channel by
    channel: the season it names cannot be read, the bot is no longer in the league's server, or
    the bot's own member there cannot be resolved. `approval_faults` gives that one line alone,
    marked with its kind (the season's, or the bot's reach of the server), and attendance is never
    asked."""
    from leaguebot.results.services.points.points_amendment_faults import (
        ApprovalFaultKind,
        approval_faults,
    )

    league = await points_league(tmp_path, attendance=True)
    season_id = await setup(league)
    hook = MagicMock()
    hook.recalculation_faults = AsyncMock(return_value=["the hook's own fault"])

    faults = await approval_faults(
        league.db_path, season_id, league.bot,
        module_service=_switches(True), attendance=hook, drawing=league.bot.image_drawing,
    )

    assert faults.lines == (line,)
    assert faults.kind is ApprovalFaultKind[kind]
    hook.recalculation_faults.assert_not_awaited()
