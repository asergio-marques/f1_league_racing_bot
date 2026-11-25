"""What an amendment to a round costs its check-in — `check_in_amendment_rules` (#287).

Core judges an amendment's own rules and asks each module for its part. Attendance's part is the
check-in: whether each of its windows (the call, the last notice, the deadline) would have run
already were the round always to have stood at its new moment, the refusal of a round brought
inside its deadline, the refusals of a round moved inside its last notice or once its last notice
is posted, the warning of a call already due, and whether the call is posted again or the check-in
stays closed. `judge_check_in` is pure: no database, no Discord.

Every test pins ``now`` alongside the dates it seeds.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from leaguebot.attendance.models.attendance import AttendanceWindows

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)

#: The packaged defaults: call 5 days out, reminder 24 hours out, deadline 2 hours out.
DEFAULT_ATTENDANCE = AttendanceWindows(notice_days=5, last_notice_hours=24, deadline_hours=2)


def _judged(
    moment: datetime, windows: AttendanceWindows | None = DEFAULT_ATTENDANCE, *,
    called: bool = False, notified: bool = False, moved: bool = True,
):
    """The check-in's part of an amendment leaving the round at *moment*, judged at NOW: by
    default a move of a round whose call and last notice have not gone out."""
    from leaguebot.attendance.services.check_in.check_in_amendment_rules import judge_check_in

    return judge_check_in(
        moment=moment, now=NOW, windows=windows, called=called, notified=notified, moved=moved
    )


#: The call warning where no call has gone out yet, and where one stands (#606).
POSTED_AT_ONCE = "The check-in call for that moment is already due, so it will be posted at once."
POSTED_AGAIN = (
    "The check-in call for that moment is already due, so the call already posted will be "
    "posted again at once, carrying what changed and keeping every answer given to it."
)
#: The refusals a move earns once its last notice has passed or been posted (#606).
LAST_NOTICE_PASSED = (
    "The check-in last notice for that moment has already passed, so drivers would have no time "
    "left to answer the check-in. Move it further out, or shorten the last notice."
)
LAST_NOTICE_POSTED = (
    "The check-in last notice for this round has already been posted, so its check-in cannot be "
    "run again this close to the race. Move it far enough out that its check-in call is still to "
    "come."
)
DEADLINE_PASSED = (
    "The check-in deadline for that moment has already passed, so the round would have no "
    "check-in at all. Move it further out, or shorten the deadline."
)
NO_LAST_NOTICE = AttendanceWindows(notice_days=5, last_notice_hours=0, deadline_hours=2)


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


@pytest.mark.parametrize("hours_out, windows, standing, refusals, warnings", [
    pytest.param(48, DEFAULT_ATTENDANCE, dict(called=False, notified=False, moved=True),
                 (), (POSTED_AT_ONCE,), id="no-call-yet-last-notice-ahead"),
    pytest.param(48, DEFAULT_ATTENDANCE, dict(called=True, notified=False, moved=True),
                 (), (POSTED_AGAIN,), id="call-standing-last-notice-ahead"),
    pytest.param(3, DEFAULT_ATTENDANCE, dict(called=False, notified=False, moved=True),
                 (LAST_NOTICE_PASSED,), (), id="no-call-yet-last-notice-passed"),
    pytest.param(3, DEFAULT_ATTENDANCE, dict(called=True, notified=False, moved=True),
                 (LAST_NOTICE_PASSED,), (), id="call-standing-last-notice-passed"),
    pytest.param(48, DEFAULT_ATTENDANCE, dict(called=True, notified=True, moved=True),
                 (LAST_NOTICE_POSTED,), (), id="last-notice-posted"),
    pytest.param(3, DEFAULT_ATTENDANCE, dict(called=True, notified=True, moved=True),
                 (LAST_NOTICE_POSTED,), (), id="last-notice-posted-and-its-moment-passed"),
    pytest.param(1, DEFAULT_ATTENDANCE, dict(called=True, notified=True, moved=True),
                 (DEADLINE_PASSED,), (), id="deadline-passed"),
    pytest.param(144, DEFAULT_ATTENDANCE, dict(called=True, notified=True, moved=True),
                 (), (), id="call-ahead-again-after-the-last-notice"),
    pytest.param(3, NO_LAST_NOTICE, dict(called=False, notified=False, moved=True),
                 (), (POSTED_AT_ONCE,), id="no-last-notice-at-all"),
    pytest.param(3, DEFAULT_ATTENDANCE, dict(called=True, notified=True, moved=False),
                 (), (POSTED_AGAIN,), id="circuit-corrected-after-the-last-notice"),
])
def test_a_round_inside_its_call_window_is_offered_or_refused_by_its_last_notice(
    hours_out, windows, standing, refusals, warnings
):
    """The owner's rule of 2026-11-24 (#606). A round left *hours_out* from now, its check-in
    call five days before it, last notice 24 hours (or none, where it is 0) and deadline 2 hours,
    so the call's moment has passed in every case but the one six days out. Whether a call has
    gone out, whether its last notice has, and whether the amendment moves the round, as given:
    - two days out, last notice still ahead and not posted: allowed, warned that the call is
      posted at once where none stands, or posted again keeping every answer where one does;
    - three hours out, the last notice's moment passed: refused, call or none;
    - the last notice already posted: refused while the call's moment is past, that refusal alone
      even where the last notice's moment has passed too; six days out, the call ahead again,
      allowed with no warning;
    - an hour out, the deadline passed: the deadline refusal alone, as today;
    - no last notice at all: three hours out is allowed, warned the call is posted at once;
    - a circuit corrected three hours out without moving the round, last notice posted: allowed,
      warned the call is posted again, as today.
    "This round will get no reminder" is never warned of."""
    judgement = _judged(NOW + timedelta(hours=hours_out), windows, **standing)
    assert judgement.part.refusals == refusals
    assert judgement.part.warnings == warnings


def test_a_move_refused_for_its_last_notice_keeps_what_an_allowed_one_keeps():
    """A round three hours out on the default windows, its call posted and its last notice not.
    Moved there, it is refused for its last notice; corrected there without moving, it is allowed.
    Both keep the same for the save and the arming (reopened), the re-arming of a round judging it
    as it stands and arming from what is kept, whatever is refused. Each window is judged on the
    new moment alone, the same either way: the call and the last notice would have gone out
    already, so they stand; only the deadline, still ahead, is armed again."""
    refused = _judged(NOW + timedelta(hours=3), called=True, notified=False, moved=True)
    allowed = _judged(NOW + timedelta(hours=3), called=True, notified=False, moved=False)
    assert refused.part.refusals and not allowed.part.refusals
    assert refused.part.kept == allowed.part.kept == {"reopened": True}
    for judgement in (refused, allowed):
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
    """A deadline of zero hours, and no last notice, the round brought to thirty minutes away:
    nothing refused, and the deadline falls at the round's own moment. Zero is the documented
    meaning of the setting, not an absent window."""
    windows = AttendanceWindows(notice_days=5, last_notice_hours=0, deadline_hours=0)
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
