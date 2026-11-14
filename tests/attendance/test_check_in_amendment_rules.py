"""What an amendment to a round costs its check-in — `check_in_amendment_rules` (#287).

Core judges an amendment's own rules and asks each module for its part. Attendance's part is the
check-in: whether each of its windows (the call, the last notice, the deadline) would have run
already were the round always to have stood at its new moment, the refusal of a round brought
inside its deadline, the warnings of a call or a reminder already due, and whether the call is
posted again or the check-in stays closed. `judge_check_in` is pure: no database, no Discord.

Every test pins ``now`` alongside the dates it seeds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from leaguebot.core.models.round import AttendanceWindows

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

#: The packaged defaults: call 5 days out, reminder 24 hours out, deadline 2 hours out.
DEFAULT_ATTENDANCE = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=2)


def _judged(moment: datetime, windows: AttendanceWindows | None = DEFAULT_ATTENDANCE):
    """The check-in's part of an amendment leaving the round at *moment*, judged at NOW."""
    from leaguebot.attendance.services.check_in.check_in_amendment_rules import judge_check_in

    return judge_check_in(moment=moment, now=NOW, windows=windows)


# ---------------------------------------------------------------------------
# The moment, and the check-in deadline that guards it
# ---------------------------------------------------------------------------


def test_a_delay_is_allowed_however_close_the_round_stood():
    """The participation case: a league delays a round by a week to get more drivers.

    The check-in is judged on the amended moment alone, a week out, with the default windows:
    the round's earlier moment is no input, so however close it stood, and however many of its
    windows had passed, nothing of it is judged. The check-in refuses nothing, and every one of
    its windows is ahead again and armed afresh.
    """
    judgement = _judged(NOW + timedelta(days=7))
    assert not judgement.part.refusals
    assert all(w.rearm for w in judgement.windows.values())


def test_bringing_a_round_inside_its_check_in_deadline_is_refused():
    """A round brought to an hour away, its deadline two hours before it, an hour ago: a check-in
    that opens and closes in the same instant asks a question nobody can answer, so it is refused
    naming the check-in deadline."""
    judgement = _judged(NOW + timedelta(hours=1))
    assert any("check-in deadline" in r for r in judgement.part.refusals)


def test_a_deadline_falling_exactly_now_counts_as_passed():
    """A round brought to two hours away, its deadline landing exactly now: refused, the same
    threshold `approval_window_service` holds to, so the two cannot disagree."""
    assert _judged(NOW + timedelta(hours=2)).part.refusals


def test_a_round_just_beyond_its_deadline_is_allowed_and_warns():
    """A round brought to three hours away, inside its call window but not its deadline, as a
    round an hour away delayed by two is: nothing refused, and the league warned what it costs.
    The call and the reminder would still have gone out, so they stand; a moment already past is
    never honoured retroactively. Only the deadline, which has not yet come round under the new
    moment, is armed again."""
    judgement = _judged(NOW + timedelta(hours=3))
    assert not judgement.part.refusals
    assert judgement.part.warnings, "a round this close must not be moved silently"
    assert judgement.windows["call"].stands
    assert judgement.windows["last_notice"].stands
    assert judgement.windows["deadline"].rearm


def test_the_deadline_is_not_judged_while_attendance_is_disabled():
    """With the module off the round has no check-in to lose: a round brought to an hour away is
    refused nothing for it, and none of its windows is judged."""
    judgement = _judged(NOW + timedelta(hours=1), windows=None)
    assert not judgement.part.refusals
    assert judgement.windows == {}


def test_a_deadline_of_zero_hours_is_the_rounds_own_moment():
    """A deadline of zero hours, the round brought to thirty minutes away: nothing refused, and
    the deadline falls at the round's own moment. Zero is the documented meaning of the setting,
    not an absent window."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=0)
    judgement = _judged(NOW + timedelta(minutes=30), windows=windows)
    assert not judgement.part.refusals
    assert judgement.windows["deadline"].fire_at == NOW + timedelta(minutes=30)


def test_a_disabled_last_notice_carries_no_window():
    """A last notice of zero hours disables the reminder outright, exactly as the scheduler reads
    it: a round 30 days out carries no last-notice window."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=0, deadline_hours=2)
    assert "last_notice" not in _judged(NOW + timedelta(days=30), windows=windows).windows


# ---------------------------------------------------------------------------
# What the judgement tells the save to do
# ---------------------------------------------------------------------------


def test_a_round_moved_well_out_has_its_check_in_called_again():
    """The amended moment 30 days out, the default windows (the round's earlier moment is not
    judged): its call is posted again, and its check-in does not stay closed."""
    judgement = _judged(NOW + timedelta(days=30))
    assert judgement.recall
    assert not judgement.stays_closed


def test_a_round_nudged_within_its_closed_check_in_stays_closed():
    """The amended moment an hour away, the default windows: the same input as
    `test_bringing_a_round_inside_its_check_in_deadline_is_refused`, here asserting what the save
    is told. Its deadline has passed under that moment, so its check-in stays closed and no call
    is posted again. The round's earlier moment is not judged."""
    judgement = _judged(NOW + timedelta(hours=1))
    assert judgement.stays_closed
    assert not judgement.recall
