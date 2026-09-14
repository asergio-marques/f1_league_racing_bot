"""What a season's approval refuses on, judged against the dates alone.

Two halves. `overdue_windows` reports the configured lead times a season has already run
past; `calendar_faults` reduces one division to the two rounds that bound what is wrong with
it — the last round already run, and the last round holding an elapsed window.

The first half answers the two faults below, reduced to the arithmetic underneath them:

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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.round import Round, RoundFormat, RoundStatus  # noqa: E402
from services.approval_window_service import (  # noqa: E402
    AttendanceWindows,
    WeatherWindows,
    calendar_faults,
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


# ── `calendar_faults` — one division reduced to the rounds that bound it ──────
#
# #181: a league running neither weather nor attendance contributed no windows at all, so
# `overdue_windows` answered empty for a season every round of which was already in the past
# and the approval let it through. A round's own moment is judged here instead, and whatever
# the modules are.
#
# The reduction is the second half of this module's job. A division's fault is the last round
# already run and — where it is a later round — the last round holding an elapsed window, and
# nothing else: every earlier round is implied by them, because a manager who moves the
# calendar past the round named has moved it past all of them.


def _faults(rounds, *, attendance=None, weather=None, now=NOW):
    return calendar_faults(rounds, now=now, attendance=attendance, weather=weather)


def test_a_season_wholly_in_the_past_is_a_fault_with_no_modules_at_all():
    """#181, reduced to the arithmetic underneath it.

    Both modules off, so there is not one configured window to check — and before this the
    answer was that the season was fine. Every round would have sat at *not run* for good.
    """
    fault = _faults([_round(days_out=-90, number=1), _round(days_out=-60, number=2)])

    assert fault is not None
    assert fault.latest_past.round_number == 2
    assert fault.latest_window is None


def test_the_latest_past_round_is_named_and_not_the_first():
    """The last one bounds the calendar. Naming round 1 would understate what must move."""
    rounds = [_round(days_out=d, number=n) for n, d in ((1, -30), (2, -20), (3, -10), (4, 30))]

    fault = _faults(rounds)

    assert fault.latest_past.round_number == 3


def test_a_later_round_inside_a_window_is_named_as_well():
    """Two findings, because they are two different things to fix.

    Round 1 has gone; round 2 is still to come but its five-day check-in call has not.
    """
    rounds = [_round(days_out=-1, number=1), _round(days_out=3, number=2)]

    fault = _faults(rounds, attendance=DEFAULT_ATTENDANCE)

    assert fault.latest_past.round_number == 1
    assert fault.latest_window.round_number == 2
    assert fault.latest_window.label == "Check-in call"


def test_a_round_already_run_is_not_also_named_for_its_windows():
    """The suppression rule. A round behind us has missed all six of its windows.

    Reporting it as both the latest past round and the latest windowed round says one thing
    twice, and the remedy — move it — is the same either way.
    """
    fault = _faults(
        [_round(days_out=-1)], attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER
    )

    assert fault.latest_past.round_number == 1
    assert fault.latest_window is None


def test_the_furthest_overdue_window_of_that_round_is_the_one_named():
    """Clearing the earliest-due window clears the rest, so it sets how far the round moves.

    A round three days out has missed the five-day check-in call and the five-day Phase 1,
    but not the 24-hour last notice. The call is named, being the earliest due of them.
    """
    fault = _faults(
        [_round(days_out=3)], attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER
    )

    assert fault.latest_past is None
    assert fault.latest_window.fire_at == fault.latest_window.scheduled_at - timedelta(days=5)
    assert fault.latest_window.label in {"Check-in call", "Weather Phase 1"}


def test_a_healthy_calendar_is_no_fault_at_all():
    """The gate must not stand in the way of an ordinary season."""
    rounds = [_round(days_out=d, number=n) for n, d in ((1, 30), (2, 37), (3, 44))]

    assert _faults(rounds, attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER) is None


def test_no_rounds_at_all_is_no_fault():
    assert _faults([], attendance=DEFAULT_ATTENDANCE) is None


def test_a_round_exactly_now_has_already_run():
    """``scheduled_at <= now``, the threshold `judge_amendment` refuses a backwards move on.

    The two doors must agree about a round landing on this instant, or a moment `/round amend`
    refuses would still be approvable.
    """
    fault = _faults([_round(days_out=0)])

    assert fault.latest_past is not None
    assert fault.latest_past.scheduled_at == NOW


def test_a_cancelled_round_is_no_fault_however_far_behind():
    """Refusing a season over one would leave a league unable to approve at all until they
    deleted a round they may well want to keep a record of."""
    rounds = [
        _round(days_out=-30, number=1, status=RoundStatus.CANCELLED.value),
        _round(days_out=30, number=2),
    ]

    assert _faults(rounds, attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER) is None


def test_a_division_of_nothing_but_cancelled_rounds_is_no_fault():
    cancelled = _round(days_out=-30, status=RoundStatus.CANCELLED.value)

    assert _faults([cancelled]) is None


def test_a_naive_scheduled_at_is_read_as_utc_here_too():
    """Rounds come back from the database without a timezone, as they do everywhere here."""
    naive = Round(
        id=1,
        division_id=1,
        round_number=1,
        format=RoundFormat.NORMAL,
        track_name="Silverstone",
        scheduled_at=datetime(2026, 9, 1, 12, 0),  # no tzinfo, and behind NOW
    )

    fault = _faults([naive])

    assert fault is not None and fault.latest_past.round_number == 1


def test_two_rounds_at_one_moment_break_the_tie_on_round_number():
    """Never the order a host's database handed them back in.

    Two rounds of one division should never share a moment — approval refuses that too — but
    the answer must not depend on row order while one slips through.
    """
    same = [_round(days_out=-1, number=2), _round(days_out=-1, number=1)]

    assert _faults(same).latest_past.round_number == 2
    assert _faults(list(reversed(same))).latest_past.round_number == 2


# ── `_calendar_fault_lines` — the verdict as a manager reads it ───────────────
#
# The wording a division's calendar carries in `/season review`, and the half of the answer
# the approval quotes back when it refuses. Driven directly; that the review posts these
# beside the calendar whichever form it took is pinned in `test_season_review_images.py`.


def _lines_for(rounds, *, attendance=None, weather=None):
    from cogs.season_cog import SeasonCog

    cog = SeasonCog.__new__(SeasonCog)
    return cog._calendar_fault_lines(
        calendar_faults(rounds, now=NOW, attendance=attendance, weather=weather)
    )


def test_a_healthy_calendar_carries_no_lines():
    assert _lines_for([_round(days_out=30)], attendance=DEFAULT_ATTENDANCE) == []


def test_the_past_round_line_names_the_round_and_the_remedy():
    lines = _lines_for([_round(days_out=-5, number=3)])

    assert len(lines) == 1
    assert "Round 3 has already run" in lines[0]
    assert "could never take results" in lines[0]
    assert "/round amend" in lines[0]


def test_a_windowed_round_adds_a_second_line_naming_its_window():
    lines = _lines_for(
        [_round(days_out=-1, number=1), _round(days_out=3, number=2)],
        attendance=DEFAULT_ATTENDANCE,
    )

    assert len(lines) == 2
    assert "Round 1 has already run" in lines[0]
    assert "Round 2 is already inside its check-in call" in lines[1]
    assert "5 days before" in lines[1]


def test_a_round_already_run_yields_one_line_and_not_two():
    """The suppression rule, seen from the message rather than the verdict."""
    lines = _lines_for(
        [_round(days_out=-1)], attendance=DEFAULT_ATTENDANCE, weather=DEFAULT_WEATHER
    )

    assert len(lines) == 1
    assert "already inside" not in lines[0]


def test_a_window_alone_yields_the_window_line_alone():
    lines = _lines_for([_round(days_out=3)], attendance=DEFAULT_ATTENDANCE)

    assert len(lines) == 1
    assert "already inside its check-in call" in lines[0]


def test_no_fault_at_all_is_no_lines():
    from cogs.season_cog import SeasonCog

    assert SeasonCog.__new__(SeasonCog)._calendar_fault_lines(None) == []


def test_a_naive_now_is_read_as_utc_too():
    """`overdue_windows` normalises its ``now``; this half must not diverge from it.

    Nothing in the bot passes a naive one — both callers read `datetime.now(timezone.utc)` —
    but a comparison that raises where its sibling compares is a trap for the next caller.
    """
    fault = calendar_faults([_round(days_out=-1)], now=NOW.replace(tzinfo=None))

    assert fault is not None and fault.latest_past.round_number == 1
