"""What an amendment to a round may do — `amendment_rules_service`.

Pure tests: no database, no Discord. Every one pins ``now`` alongside the dates it seeds, because
the module takes ``now`` for exactly that reason and a test that seeded a future date and let the
code read the wall clock would pass today and fail silently months later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from leaguebot.core.models.round import Round, RoundFormat, RoundStatus
from leaguebot.core.services.amendment_rules_service import judge_amendment
from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

#: The packaged defaults: call 5 days out, reminder 24 hours out, deadline 2 hours out.
DEFAULT_ATTENDANCE = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=2)
#: The packaged forecast horizons.
DEFAULT_WEATHER = WeatherWindows(phase_1_days=5, phase_2_days=2, phase_3_hours=2)


def _round(
    *,
    scheduled_at: datetime,
    status: str = RoundStatus.NOT_RUN.value,
    fmt: RoundFormat = RoundFormat.NORMAL,
    track: str | None = "Bahrain International Circuit",
    phase1_done: bool = False,
    phase2_done: bool = False,
    phase3_done: bool = False,
) -> Round:
    return Round(
        id=1,
        division_id=1,
        round_number=1,
        format=fmt,
        track_name=track,
        scheduled_at=scheduled_at,
        phase1_done=phase1_done,
        phase2_done=phase2_done,
        phase3_done=phase3_done,
        status=status,
    )


# ---------------------------------------------------------------------------
# Whether the round may be amended at all
# ---------------------------------------------------------------------------


def test_a_round_well_ahead_may_be_amended():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd, {"track_name": "Silverstone Circuit"}, now=NOW, attendance=DEFAULT_ATTENDANCE
    )
    assert verdict.allowed
    assert verdict.refusals == []


def test_a_round_whose_results_are_in_may_not_be_amended():
    """The same rule that governs cancellation: drivers have reports and appeals lodged."""
    rnd = _round(
        scheduled_at=NOW + timedelta(days=30),
        status=RoundStatus.AWAITING_REPORT_VERDICTS.value,
    )
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("results have been entered" in r for r in verdict.refusals)


def test_a_cancelled_round_may_not_be_amended():
    rnd = _round(scheduled_at=NOW + timedelta(days=30), status=RoundStatus.CANCELLED.value)
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("cancelled" in r for r in verdict.refusals)


def test_an_empty_change_set_is_refused():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    assert not judge_amendment(rnd, {}, now=NOW).allowed


def test_a_field_that_is_not_amendable_is_refused():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"round_number": 4}, now=NOW)
    assert not verdict.allowed
    assert any("round_number" in r for r in verdict.refusals)


# ---------------------------------------------------------------------------
# The moment, and the check-in deadline that guards it
# ---------------------------------------------------------------------------


def test_a_delay_is_allowed_however_close_the_round_stood():
    """The participation case: a league delays a round by a week to get more drivers.

    The old moment's windows have all passed — that is *why* the league is moving it — and none
    of that is judged. Only the round as it will stand counts.
    """
    rnd = _round(scheduled_at=NOW + timedelta(hours=1))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(days=7)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert verdict.allowed, verdict.refusals
    # A week out, every window is ahead again and every one of them is armed afresh.
    assert all(w.rearm for w in verdict.check_in.values())


def test_bringing_a_round_inside_its_check_in_deadline_is_refused():
    """A check-in that opens and closes in the same instant asks a question nobody can answer."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(hours=1)},  # deadline is 2h before — an hour ago
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert not verdict.allowed
    assert any("check-in deadline" in r for r in verdict.refusals)


def test_a_deadline_falling_exactly_now_counts_as_passed():
    """The same threshold `approval_window_service` holds to, so the two cannot disagree."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(hours=2)},  # deadline lands exactly on NOW
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert not verdict.allowed


def test_a_round_just_beyond_its_deadline_is_allowed_and_warns():
    """Inside the call window but not the deadline: allowed, and the league is told what it costs."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(hours=3)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert verdict.allowed, verdict.refusals
    assert verdict.warnings, "a round this close must not be moved silently"
    assert verdict.check_in["deadline"].rearm
    assert verdict.check_in["call"].stands


def test_the_deadline_is_not_judged_while_attendance_is_disabled():
    """With the module off the round has no check-in to lose, so nothing is refused for it."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"scheduled_at": NOW + timedelta(hours=1)}, now=NOW)
    assert verdict.allowed
    assert verdict.check_in == {}


def test_a_deadline_of_zero_hours_is_the_rounds_own_moment():
    """Zero is the documented meaning of the setting, not an absent window."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=0)
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd, {"scheduled_at": NOW + timedelta(minutes=30)}, now=NOW, attendance=windows
    )
    assert verdict.allowed, verdict.refusals
    assert verdict.check_in["deadline"].fire_at == NOW + timedelta(minutes=30)


def test_a_disabled_last_notice_carries_no_window():
    """Zero disables the reminder outright, exactly as the scheduler reads it."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=0, deadline_hours=2)
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW, attendance=windows)
    assert "last_notice" not in verdict.check_in


# ---------------------------------------------------------------------------
# "Would it have run, were the round always to have stood at its new moment?"
# ---------------------------------------------------------------------------


def test_a_small_delay_leaves_the_early_windows_standing():
    """Round an hour away, delayed by two: the call and the reminder would still have gone out.

    What stands, stands — a moment already past is never honoured retroactively. Only the
    deadline, which has not yet come round under the new moment, is armed again.
    """
    rnd = _round(scheduled_at=NOW + timedelta(hours=1))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(hours=3)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert verdict.allowed, verdict.refusals
    assert verdict.check_in["call"].stands
    assert verdict.check_in["last_notice"].stands
    assert verdict.check_in["deadline"].rearm


def test_a_long_delay_puts_every_phase_back_in_question():
    rnd = _round(scheduled_at=NOW + timedelta(days=1), phase1_done=True)
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(days=30)},
        now=NOW,
        weather=DEFAULT_WEATHER,
    )
    assert all(p.rearm for p in verdict.phases.values())


def test_a_delay_can_leave_phase_one_standing_while_the_later_two_are_rearmed():
    """The middle case the rule exists for: one phase keeps, two go back.

    Round three days out, delayed to four. Phase 1 falls five days before, which is behind us
    either way, so the forecast already drawn stands. Phases 2 and 3 have not come round under
    the new moment, so they are armed again.
    """
    rnd = _round(scheduled_at=NOW + timedelta(days=3), phase1_done=True)
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(days=4)},
        now=NOW,
        weather=DEFAULT_WEATHER,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert verdict.allowed, verdict.refusals
    assert verdict.phases[1].stands
    assert verdict.phases[2].rearm
    assert verdict.phases[3].rearm


def test_a_phase_falling_exactly_now_counts_as_having_run():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(days=5)},  # phase 1 lands exactly on NOW
        now=NOW,
        weather=DEFAULT_WEATHER,
    )
    assert verdict.phases[1].stands
    assert verdict.phases[2].rearm


def test_no_phases_are_judged_without_configured_horizons():
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    assert judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW).phases == {}


# ---------------------------------------------------------------------------
# What the track and the format may no longer be
# ---------------------------------------------------------------------------


def test_the_track_may_not_be_amended_once_phase_one_has_run():
    rnd = _round(scheduled_at=NOW + timedelta(days=3), phase1_done=True)
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("first forecast" in r for r in verdict.refusals)


def test_the_format_may_not_be_amended_once_phase_two_has_run():
    rnd = _round(scheduled_at=NOW + timedelta(days=1), phase1_done=True, phase2_done=True)
    verdict = judge_amendment(rnd, {"format": RoundFormat.SPRINT}, now=NOW)
    assert not verdict.allowed
    assert any("second forecast" in r for r in verdict.refusals)


def test_the_format_may_still_be_amended_after_phase_one_alone():
    rnd = _round(scheduled_at=NOW + timedelta(days=3), phase1_done=True)
    assert judge_amendment(rnd, {"format": RoundFormat.SPRINT}, now=NOW).allowed


def test_neither_may_be_amended_once_the_round_has_started():
    rnd = _round(scheduled_at=NOW - timedelta(hours=1))
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("already started" in r for r in verdict.refusals)


def test_moving_a_round_forward_lets_its_track_be_amended_in_the_same_change():
    """The league's remedy, and the reason a change set is judged as one change.

    The round has started and its first forecast is out, so a bare track change is refused twice
    over. Moving it puts both rules back in question, and they read the round as it will stand.
    """
    rnd = _round(scheduled_at=NOW - timedelta(hours=1), phase1_done=True)
    verdict = judge_amendment(
        rnd,
        {"track_name": "Silverstone Circuit", "scheduled_at": NOW + timedelta(days=30)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )
    assert verdict.allowed, verdict.refusals


# ---------------------------------------------------------------------------
# Where a round may be moved to
# ---------------------------------------------------------------------------


def test_a_round_may_not_be_moved_into_the_past():
    """The rule on its own, with no module enabled to refuse it for another reason.

    Driven with both modules off deliberately: the check-in deadline rule refuses a backwards
    move too, and a test that let it answer would pass whether or not this rule existed.
    """
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"scheduled_at": NOW - timedelta(hours=1)}, now=NOW)
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


@pytest.mark.parametrize(
    "attendance,weather",
    [
        (None, None),
        (DEFAULT_ATTENDANCE, None),
        (None, DEFAULT_WEATHER),
        (DEFAULT_ATTENDANCE, DEFAULT_WEATHER),
    ],
)
def test_moving_a_round_into_the_past_is_refused_whatever_the_modules(attendance, weather):
    """Decided 2026-09-14: the answer shall not turn on which modules a league runs.

    The round's result submission is armed against its moment whatever the modules, so a
    backwards move costs the same round the same thing in every one of these combinations.
    """
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW - timedelta(hours=1)},
        now=NOW,
        attendance=attendance,
        weather=weather,
    )
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_moment_falling_exactly_now_counts_as_the_past():
    """``<=``, the threshold every other rule here holds to, so the two cannot disagree."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30))
    verdict = judge_amendment(rnd, {"scheduled_at": NOW}, now=NOW)
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_round_already_in_the_past_may_still_be_moved_forward():
    """The escape, and the guard against over-tightening: forward is always still open."""
    rnd = _round(scheduled_at=NOW - timedelta(days=2))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(days=30)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
        weather=DEFAULT_WEATHER,
    )
    assert verdict.allowed, verdict.refusals


def test_the_past_refusal_is_silent_when_the_moment_is_not_amended():
    """A track change on a round already past is refused by the started gate, not by this one.

    The round is not being *moved* anywhere, so a refusal telling the manager not to move it
    into the past would name a thing they did not ask for.
    """
    rnd = _round(scheduled_at=NOW - timedelta(hours=1))
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("already started" in r for r in verdict.refusals)
    assert not any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_round_may_not_be_moved_from_one_past_moment_to_another():
    """Already past is not a licence to stay there — the round still loses its submission."""
    rnd = _round(scheduled_at=NOW - timedelta(days=2))
    verdict = judge_amendment(rnd, {"scheduled_at": NOW - timedelta(hours=1)}, now=NOW)
    assert not verdict.allowed
    assert any("cannot be moved into the past" in r for r in verdict.refusals)


def test_a_mystery_round_may_not_be_given_a_track_without_a_format():
    """A mystery round names no circuit, so naming one alone would leave it in a forbidden state."""
    rnd = _round(scheduled_at=NOW + timedelta(days=30), fmt=RoundFormat.MYSTERY, track=None)
    verdict = judge_amendment(rnd, {"track_name": "Silverstone Circuit"}, now=NOW)
    assert not verdict.allowed
    assert any("mystery" in r.lower() for r in verdict.refusals)


def test_revealing_a_mystery_round_amends_both_at_once():
    rnd = _round(scheduled_at=NOW + timedelta(days=30), fmt=RoundFormat.MYSTERY, track=None)
    verdict = judge_amendment(
        rnd,
        {"track_name": "Silverstone Circuit", "format": RoundFormat.NORMAL},
        now=NOW,
    )
    assert verdict.allowed, verdict.refusals


# ---------------------------------------------------------------------------
# A change set is one change
# ---------------------------------------------------------------------------


def test_one_refused_field_refuses_the_whole_change_set():
    """Track and format together, with phase 2 out: the format alone is what refuses it, and the
    track goes with it. None of the amendment happens."""
    rnd = _round(scheduled_at=NOW + timedelta(days=1), phase2_done=True)
    verdict = judge_amendment(
        rnd,
        {"track_name": "Silverstone Circuit", "format": RoundFormat.SPRINT},
        now=NOW,
    )
    assert not verdict.allowed


# ---------------------------------------------------------------------------
# What the verdict tells the caller to do
# ---------------------------------------------------------------------------


def test_a_round_moved_well_out_has_its_check_in_called_again():
    rnd = _round(scheduled_at=NOW + timedelta(hours=3))
    verdict = judge_amendment(
        rnd,
        {"scheduled_at": NOW + timedelta(days=30)},
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert verdict.recall_check_in
    assert not verdict.check_in_stays_closed


def test_a_round_nudged_within_its_closed_check_in_stays_closed():
    """Deadline passed on the old moment and on the new one alike: nothing reopens."""
    rnd = _round(scheduled_at=NOW + timedelta(hours=1))
    verdict = judge_amendment(
        rnd,
        {"track_name": "Silverstone Circuit"},  # the moment does not move
        now=NOW,
        attendance=DEFAULT_ATTENDANCE,
    )
    assert verdict.check_in_stays_closed
    assert not verdict.recall_check_in
