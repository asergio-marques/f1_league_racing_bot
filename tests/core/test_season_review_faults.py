"""The calendar faults `/season placements-review` reports, and the windows it judges them against.

Issue #208. `season_cog.py` is the largest file in the bot. This file takes the two helpers that
decide what a league is told is wrong with its calendar before it can approve a season.

**Both faults name the *latest* round of their kind, deliberately** — because that is the one
that bounds the remedy. Clearing the latest round in the past clears every earlier one with it,
so naming the first would have a manager fix rounds one at a time and re-run the review between
each. `test_only_the_latest_round_of_each_kind_is_named` is what holds it.

**The two faults are two different jobs and are reported separately.** A round already run is a
calendar to move wholesale; a round inside its window is one date to push out, or one window to
shorten. Telling a manager the wrong one sends them to the wrong command, so each line names its
own remedy — and a calendar with both faults gets both lines.

**The verdict is taken rather than the rounds.** `/season placements-review` and the approval reach this by
different routes and must not be able to reach different answers — a review that passed and an
approval that refused would be the worst of both.

**The module windows are read once for a whole season.** The faults are judged a division at a
time, and a league with eight of them would otherwise pay eight times over for two
configurations that cannot have changed in between. A module that is off contributes `None`,
which is how "there is no window to be inside" is expressed.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402

SERVER_ID = 11808


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _round(number: int, *, days_ago: int = 1):
    return SimpleNamespace(
        round_number=number,
        scheduled_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def _window(number: int, label: str = "RSVP Notice", lead: str = "5 days before"):
    return SimpleNamespace(
        round_number=number,
        label=label,
        lead=lead,
        fire_at=datetime.now(timezone.utc) - timedelta(days=1),
    )


def _fault(*, latest_past=None, latest_window=None):
    return SimpleNamespace(latest_past=latest_past, latest_window=latest_window)


def _make_cog(
    *,
    attendance_enabled: bool = False,
    weather_enabled: bool = False,
    attendance_config=None,
    weather_config=None,
) -> SeasonCog:
    bot = MagicMock()
    bot.db_path = "/tmp/does-not-matter.db"
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=attendance_config
        or SimpleNamespace(
            rsvp_notice_days=5, rsvp_last_notice_hours=24, rsvp_deadline_hours=2
        )
    )
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = bot
    cog._weather_config = weather_config or SimpleNamespace(
        phase_1_days=5, phase_2_days=2, phase_3_hours=2
    )
    return cog


def _weather(cog):
    return patch(
        "services.weather_config_service.get_weather_pipeline_config",
        new=AsyncMock(return_value=cog._weather_config),
    )


# ---------------------------------------------------------------------------
# The fault lines
# ---------------------------------------------------------------------------


def test_a_calendar_with_nothing_wrong_reports_nothing():
    """The caller concatenates these unconditionally, so a clean calendar has to produce
    an empty list rather than a reassuring line."""
    assert _make_cog()._calendar_fault_lines(None) == []


def test_a_verdict_with_no_faults_reports_nothing():
    assert _make_cog()._calendar_fault_lines(_fault()) == []


def test_a_round_already_run_is_reported():
    lines = _make_cog()._calendar_fault_lines(_fault(latest_past=_round(3)))

    assert len(lines) == 1
    assert "Round 3 has already run" in lines[0]


def test_the_past_round_fault_says_why_it_cannot_stand():
    """A manager who does not know that a past round never opens its submission would
    assume the bot would simply catch up."""
    lines = _make_cog()._calendar_fault_lines(_fault(latest_past=_round(3)))

    assert "never opens its result submission" in lines[0]
    assert "could never take results" in lines[0]


def test_the_past_round_fault_says_every_earlier_round_is_affected():
    """Which is why naming the latest is enough — a manager told about round 3 alone would
    fix it and be refused again on round 2."""
    lines = _make_cog()._calendar_fault_lines(_fault(latest_past=_round(3)))

    assert "every round before it" in lines[0]


def test_a_round_inside_its_window_is_reported():
    lines = _make_cog()._calendar_fault_lines(
        _fault(latest_window=_window(4, "RSVP Notice"))
    )

    assert len(lines) == 1
    assert "Round 4 is already inside its rsvp notice" in lines[0]


def test_the_window_fault_names_the_window_and_when_it_fell_due():
    """A manager has to know *which* of the five windows is the problem before they can
    decide whether to move the round or shorten the window."""
    lines = _make_cog()._calendar_fault_lines(
        _fault(latest_window=_window(4, "Phase 1", "5 days before"))
    )

    assert "phase 1" in lines[0]
    assert "5 days before" in lines[0]


def test_the_two_faults_name_different_remedies():
    """A round already run is a calendar to move wholesale; a round inside a window is one
    date to push out or one window to shorten. Sending a manager to the wrong one is the
    failure these two lines exist to prevent."""
    past = _make_cog()._calendar_fault_lines(_fault(latest_past=_round(3)))[0]
    window = _make_cog()._calendar_fault_lines(_fault(latest_window=_window(4)))[0]

    assert "Move the calendar forward" in past
    assert "Move the round out" in window
    assert "shorten the window" in window


def test_a_calendar_with_both_faults_reports_both():
    """They are independent: moving the calendar forward can leave a later round inside
    its window, and a manager needs to see both before deciding what to change."""
    lines = _make_cog()._calendar_fault_lines(
        _fault(latest_past=_round(3), latest_window=_window(6))
    )

    assert len(lines) == 2
    assert "Round 3" in lines[0]
    assert "Round 6" in lines[1]


def test_only_the_latest_round_of_each_kind_is_named():
    """The verdict carries one round per fault, and that is the whole design — naming the
    first would have a manager fix rounds one at a time, re-running the review between
    each."""
    lines = _make_cog()._calendar_fault_lines(_fault(latest_past=_round(7)))

    assert "Round 7" in lines[0]
    assert "Round 1" not in lines[0]


def test_both_faults_name_the_command_that_fixes_them():
    """`/round amend` is not obvious from a refusal that only says the calendar is wrong."""
    lines = _make_cog()._calendar_fault_lines(
        _fault(latest_past=_round(3), latest_window=_window(6))
    )

    assert all("/round amend" in line for line in lines)


# ---------------------------------------------------------------------------
# The windows the faults are judged against
# ---------------------------------------------------------------------------


async def test_a_league_running_neither_module_has_no_windows():
    """Nothing to be inside, so nothing can be refused for it — a league running only the
    results module must not be blocked by windows it does not have."""
    cog = _make_cog(attendance_enabled=False, weather_enabled=False)

    with _weather(cog):
        attendance, weather = await cog._approval_windows()

    assert attendance is None
    assert weather is None


async def test_the_attendance_windows_are_read_when_the_module_is_on():
    cog = _make_cog(attendance_enabled=True)

    with _weather(cog):
        attendance, _ = await cog._approval_windows()

    assert attendance is not None
    assert attendance.notice_days == 5
    assert attendance.last_notice_hours == 24
    assert attendance.deadline_hours == 2


async def test_the_weather_windows_are_read_when_the_module_is_on():
    cog = _make_cog(weather_enabled=True)

    with _weather(cog):
        _, weather = await cog._approval_windows()

    assert weather is not None
    assert weather.phase_1_days == 5
    assert weather.phase_2_days == 2
    assert weather.phase_3_hours == 2


async def test_one_module_on_leaves_the_other_s_windows_absent():
    """The two are independent, and a league running attendance without weather must not
    be judged against phase horizons it never configured."""
    cog = _make_cog(attendance_enabled=True, weather_enabled=False)

    with _weather(cog):
        attendance, weather = await cog._approval_windows()

    assert attendance is not None
    assert weather is None


async def test_a_disabled_module_s_configuration_is_not_even_read():
    """The read is the cost this helper exists to avoid paying per division; doing it for
    a module that is off would be the whole saving given back."""
    cog = _make_cog(attendance_enabled=False, weather_enabled=False)

    with _weather(cog) as weather_read:
        await cog._approval_windows()

    cog.bot.attendance_service.get_or_create_config.assert_not_awaited()
    weather_read.assert_not_awaited()


async def test_the_windows_reflect_what_the_league_configured():
    """A league that shortened its notice to two days is judged against two, not against
    the packaged default — which is the point of reading them at all."""
    cog = _make_cog(
        attendance_enabled=True,
        attendance_config=SimpleNamespace(
            rsvp_notice_days=2, rsvp_last_notice_hours=6, rsvp_deadline_hours=1
        ),
    )

    with _weather(cog):
        attendance, _ = await cog._approval_windows()

    assert attendance.notice_days == 2
    assert attendance.last_notice_hours == 6
    assert attendance.deadline_hours == 1
