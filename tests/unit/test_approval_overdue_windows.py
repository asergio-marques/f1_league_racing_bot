"""`overdue_windows` reports the configured lead times a season has already run past.

These are the two reported faults, reduced to the arithmetic underneath them:

* #121 — a season approved three days before round 1, against a five-day check-in notice.
  The call was never scheduled, so the round asked nobody whether they were racing and was
  recorded as perfect attendance for the whole division.
* #122 — the same season against a five-day Phase 1 deadline, whose job was armed already
  past-dated and thrown away by the scheduler's misfire grace.

Every test pins ``now`` beside the dates it seeds. The module takes ``now`` as a required
argument for exactly this reason: a test that seeded a future date and let the code read the
wall clock would pass today and fail silently some months from now.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.round import Round, RoundFormat, RoundStatus  # noqa: E402
from services.approval_window_service import (  # noqa: E402
    AttendanceWindows,
    WeatherWindows,
    overdue_windows,
)

#: The moment every test judges against. Fixed, so the dates below mean the same thing on
#: every host and in every year.
NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)

#: The defaults a league gets without configuring anything.
DEFAULT_ATTENDANCE = AttendanceWindows(
    notice_days=5, last_notice_hours=24, deadline_hours=2
)
DEFAULT_WEATHER = WeatherWindows(phase_1_days=5, phase_2_days=2, phase_3_hours=2)


def _round(
    *,
    days_out: float,
    number: int = 1,
    status: str = RoundStatus.NOT_RUN.value,
) -> Round:
    """A round *days_out* days after `NOW`."""
    return Round(
        id=number,
        division_id=1,
        round_number=number,
        format=RoundFormat.NORMAL,
        track_name="Silverstone",
        scheduled_at=NOW + timedelta(days=days_out),
        status=status,
    )


def _labels(found) -> list[str]:
    return [w.label for w in found]


# ── The two reported faults ───────────────────────────────────────────────────


def test_an_elapsed_check_in_notice_is_reported():
    """#121: round 1 is three days away and the check-in notice is five days.

    The call was due two days ago. Before the gate this produced no job, no call, and a
    division credited with perfect attendance.
    """
    found = overdue_windows(
        [("Premier", _round(days_out=3))], now=NOW, attendance=DEFAULT_ATTENDANCE
    )

    assert "Check-in call" in _labels(found)
    overdue = next(w for w in found if w.label == "Check-in call")
    assert overdue.fire_at == NOW - timedelta(days=2)
    assert overdue.round_number == 1
    assert overdue.division_name == "Premier"
    assert overdue.lead == "5 days before"


def test_an_elapsed_phase_1_deadline_is_reported():
    """#122: the same season, against the default five-day Phase 1 horizon."""
    found = overdue_windows(
        [("Premier", _round(days_out=3))], now=NOW, weather=DEFAULT_WEATHER
    )

    assert "Weather Phase 1" in _labels(found)
    assert next(w for w in found if w.label == "Weather Phase 1").fire_at == NOW - timedelta(days=2)


def test_both_modules_are_reported_from_one_call():
    """One check covers both, which is what #121 and #122 each ask for."""
    found = overdue_windows(
        [("Premier", _round(days_out=3))],
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )

    assert _labels(found) == ["Check-in call", "Weather Phase 1"]


# ── The season that is fine ───────────────────────────────────────────────────


def test_a_round_beyond_every_window_is_clean():
    """The ordinary case. The gate must not refuse a season it has no business refusing."""
    found = overdue_windows(
        [("Premier", _round(days_out=30))],
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )

    assert found == []


def test_a_disabled_module_contributes_no_windows():
    """A module that is off has no lead times to miss, so it cannot refuse a season."""
    past_round = [("Premier", _round(days_out=3))]

    assert overdue_windows(past_round, now=NOW) == []
    assert _labels(overdue_windows(past_round, now=NOW, weather=DEFAULT_WEATHER)) == [
        "Weather Phase 1"
    ]


def test_no_rounds_at_all_is_clean():
    found = overdue_windows(
        [], now=NOW, attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER
    )

    assert found == []


# ── The settings whose zero means something particular ────────────────────────


def test_a_last_notice_of_zero_is_not_a_window():
    """Zero disables the last notice, exactly as `schedule_attendance_round` reads it."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=0, deadline_hours=2)

    found = overdue_windows(
        [("Premier", _round(days_out=-1))], now=NOW, attendance=windows
    )

    assert "Check-in last notice" not in _labels(found)


def test_a_deadline_of_zero_is_the_round_itself():
    """Zero does not disable the deadline — it puts it at the round's own start time."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=0)

    # A round one hour away: its start time is still ahead, so the deadline is not overdue.
    ahead = overdue_windows(
        [("Premier", _round(days_out=1 / 24))], now=NOW, attendance=windows
    )
    assert "Check-in deadline" not in _labels(ahead)

    # A round one hour ago: its start time has passed, and so has the deadline.
    behind = overdue_windows(
        [("Premier", _round(days_out=-1 / 24))], now=NOW, attendance=windows
    )
    overdue = next(w for w in behind if w.label == "Check-in deadline")
    assert overdue.fire_at == overdue.scheduled_at
    assert overdue.lead == "at the round"


# ── The rounds and the boundary ───────────────────────────────────────────────


def test_a_cancelled_round_is_not_reported():
    """A cancelled round has no scheduled work left to lose.

    Refusing a season because of one would leave a league unable to approve at all until
    they deleted a round they may well want to keep a record of.
    """
    cancelled = _round(days_out=-10, status=RoundStatus.CANCELLED.value)

    found = overdue_windows(
        [("Premier", cancelled)],
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )

    assert found == []


def test_a_window_exactly_now_is_overdue():
    """The ``fire_at <= now`` boundary the module docstring pins.

    It is the exact complement of `schedule_attendance_round`'s ``if fire_at > now``: a
    window landing on this instant would not have been scheduled, so it must be reported.
    """
    exactly = _round(days_out=5)  # notice window falls precisely on NOW

    found = overdue_windows([("Premier", exactly)], now=NOW, attendance=DEFAULT_ATTENDANCE)

    call = next(w for w in found if w.label == "Check-in call")
    assert call.fire_at == NOW


def test_a_past_round_reports_every_window():
    """A round wholly behind us has missed all six, and the manager is told all six."""
    found = overdue_windows(
        [("Premier", _round(days_out=-1))],
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )

    assert len(found) == 6


# ── Determinism and drift ─────────────────────────────────────────────────────


def test_the_report_is_ordered_deterministically():
    """Never the order the divisions came back in.

    The refusal is read by a person working down it, and the suite runs on three hosts whose
    databases need not agree about row order.
    """
    rounds = [
        ("Rookie", _round(days_out=-1, number=2)),
        ("Premier", _round(days_out=-1, number=2)),
        ("Premier", _round(days_out=-1, number=1)),
    ]

    found = overdue_windows(rounds, now=NOW, attendance=DEFAULT_ATTENDANCE)

    seen = [(w.division_name, w.round_number) for w in found]
    assert seen == sorted(seen)
    assert seen[0] == ("Premier", 1)
    # Within one round, the earliest window is named first.
    premier_1 = [
        w.fire_at
        for w in found
        if w.division_name == "Premier" and w.round_number == 1
    ]
    assert len(premier_1) == 3
    assert premier_1 == sorted(premier_1)


def test_the_window_offsets_match_the_scheduler():
    """The drift guard.

    These offsets are computed here *and* in `scheduler_service`. Nothing but this test
    stops the two drifting apart — and a gate that disagreed with the scheduler would either
    refuse a season whose jobs were fine or pass one whose jobs were silently dropped. The
    literals below are `schedule_attendance_round`'s and `schedule_round`'s own arithmetic,
    written out so that changing one place without the other fails by name.
    """
    rnd = _round(days_out=-1)
    scheduled_at = rnd.scheduled_at

    found = overdue_windows(
        [("Premier", rnd)],
        now=NOW,
        attendance=AttendanceWindows(
            notice_days=7, last_notice_hours=36, deadline_hours=3
        ),
        weather=WeatherWindows(phase_1_days=6, phase_2_days=3, phase_3_hours=4),
    )
    by_label = {w.label: w.fire_at for w in found}

    assert by_label["Check-in call"] == scheduled_at - timedelta(days=7)
    assert by_label["Check-in last notice"] == scheduled_at - timedelta(hours=36)
    assert by_label["Check-in deadline"] == scheduled_at - timedelta(hours=3)
    assert by_label["Weather Phase 1"] == scheduled_at - timedelta(days=6)
    assert by_label["Weather Phase 2"] == scheduled_at - timedelta(days=3)
    assert by_label["Weather Phase 3"] == scheduled_at - timedelta(hours=4)


def test_a_naive_scheduled_at_is_read_as_utc():
    """Rounds come back from the database without a timezone, as they do everywhere here."""
    naive = Round(
        id=1,
        division_id=1,
        round_number=1,
        format=RoundFormat.NORMAL,
        track_name="Silverstone",
        scheduled_at=datetime(2026, 9, 13, 12, 0),  # no tzinfo
    )

    found = overdue_windows([("Premier", naive)], now=NOW, attendance=DEFAULT_ATTENDANCE)

    assert "Check-in call" in _labels(found)


# ── The review reports it before the button is pressed ───────────────────────
#
# `_overdue_window_problems` is the one evaluation `/season review` and the approval both
# read, so the two cannot disagree about whether a season may be approved — the pattern the
# portrait settings already follow. These drive that helper directly; the review's own
# structure (that the button is withheld on it) is pinned in `test_season_review_images.py`.


def _cog_for_problems(*, attendance=True, weather=False, rounds=None):
    from unittest.mock import AsyncMock

    from cogs.season_cog import SeasonCog
    from models.attendance import AttendanceConfig

    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = AsyncMock()
    cog.bot.db_path = ":memory:"
    cog.bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    cog.bot.module_service.is_weather_enabled = AsyncMock(return_value=weather)
    cog.bot.attendance_service.get_or_create_config = AsyncMock(
        return_value=AttendanceConfig(
            server_id=1,
            module_enabled=True,
            rsvp_notice_days=5,
            rsvp_last_notice_hours=24,
            rsvp_deadline_hours=2,
            no_rsvp_penalty=1,
            absent_penalty=1,
            no_show_penalty=1,
            autoreserve_threshold=None,
            autosack_threshold=None,
        )
    )
    if rounds is not None:
        divisions = []
        for name, div_id in {(n, r.division_id) for n, r in rounds}:
            divisions.append(SimpleNamespace(id=div_id, name=name))
        cog.bot.season_service.get_divisions = AsyncMock(return_value=divisions)
        cog.bot.season_service.get_division_rounds = AsyncMock(
            return_value=[r for _, r in rounds]
        )
    return cog


def _now_relative(days_out: float, *, number: int = 1) -> Round:
    """A round *days_out* days from the wall clock, which the helper judges against."""
    return Round(
        id=number,
        division_id=1,
        round_number=number,
        format=RoundFormat.NORMAL,
        track_name="Silverstone",
        scheduled_at=datetime.now(timezone.utc) + timedelta(days=days_out),
    )


async def test_the_review_helper_names_an_overdue_check_in():
    cog = _cog_for_problems()

    lines = await cog._overdue_window_problems(1, 7, rounds=[("Premier", _now_relative(3))])

    assert any("Check-in call" in line for line in lines)
    assert any("Round 1" in line for line in lines)


async def test_the_review_helper_is_silent_on_a_healthy_season():
    cog = _cog_for_problems()

    lines = await cog._overdue_window_problems(1, 7, rounds=[("Premier", _now_relative(30))])

    assert lines == []


async def test_the_review_helper_reads_no_config_without_rounds():
    """No rounds, no windows — and so nothing to pay for."""
    cog = _cog_for_problems()

    assert await cog._overdue_window_problems(1, 7, rounds=[]) == []
    cog.bot.attendance_service.get_or_create_config.assert_not_awaited()


async def test_the_review_helper_loads_the_rounds_when_none_are_handed_to_it():
    """`/season review` hands over what it already read; other callers need not."""
    rounds = [("Premier", _now_relative(3))]
    cog = _cog_for_problems(rounds=rounds)

    lines = await cog._overdue_window_problems(1, 7)

    assert any("Check-in call" in line for line in lines)
    cog.bot.season_service.get_divisions.assert_awaited_once()


async def test_the_report_is_capped_so_discord_keeps_it():
    """A season built wholly in the past would otherwise run past the 2000-character limit."""
    cog = _cog_for_problems()
    many = [("Premier", _now_relative(-1, number=n)) for n in range(1, 21)]

    lines = await cog._overdue_window_problems(1, 7, rounds=many)

    assert any("further round(s) in the same state" in line for line in lines)
    assert len("\n".join(lines)) < 2000
