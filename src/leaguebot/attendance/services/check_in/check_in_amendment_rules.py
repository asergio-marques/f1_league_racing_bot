"""What an amendment to a round costs its check-in: attendance's part of the judgement.

Core judges an amendment's own rules and asks each module for its part (`amendment_judged`,
answered by `check_in_amendment`). The check-in's part is whether each of its windows (the call,
the last notice, the deadline) would have run already were the round always to have stood at its
new moment: where it would, what was posted stands and nothing is posted in its place, and where it
would not, it is armed again for the new moment. A round brought inside its deadline is refused,
for a check-in that opens and closes in the same instant asks a question nobody can answer; a call
or a reminder already due is warned of.

**Pure by design**: no database, no Discord, and ``now`` passed in rather than read from the wall
clock. The windows are the lead times the module is configured with, or none where it is off, in
which case the round has no check-in to lose and none of its windows are judged.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from leaguebot.core.models.amendment import AmendmentPart
from leaguebot.core.models.round import AttendanceWindows
from leaguebot.core.services.rounds.round_moments import round_event_moments


@dataclass(frozen=True)
class CheckInWindow:
    """What becomes of one of a round's check-in windows once it is amended.

    *stands* is the answer to the one question: the window would have run already under the
    round's new moment, so whatever was posted for it stands and nothing replaces it. *fire_at* is
    the moment it falls at under the new arrangement, which is when the work is armed for where it
    does not stand.
    """

    label: str
    fire_at: datetime
    stands: bool

    @property
    def rearm(self) -> bool:
        """The complement of *stands*: the window is still ahead and must be armed."""
        return not self.stands


@dataclass(frozen=True)
class CheckInJudgement:
    """The check-in's judgement of an amendment: its *part*, and its *windows* by key ("call",
    "last_notice", "deadline"), none where attendance is off."""

    part: AmendmentPart
    windows: dict[str, CheckInWindow] = field(default_factory=dict)

    @property
    def recall(self) -> bool:
        """Whether the check-in call is to be posted again, carrying what changed.

        True where the call is still to come under the new arrangement: either it never went out
        and is now armed, or it did and the round has moved far enough that it would not have.
        """
        call = self.windows.get("call")
        return call is not None and call.rearm

    @property
    def stays_closed(self) -> bool:
        """Whether the round's check-in is beyond recall, its deadline having passed either way.

        Nothing is posted afresh for such a round and no answer may be changed: the deadline
        computed from its new moment has gone by, so there is no window left to reopen.
        """
        deadline = self.windows.get("deadline")
        return deadline is not None and deadline.stands


def judge_check_in(
    *, moment: datetime, now: datetime, windows: AttendanceWindows | None
) -> CheckInJudgement:
    """The check-in's part of an amendment that leaves the round at *moment*, judged at *now*.

    Args:
        moment:  The round's moment once amended.
        now:     The moment to judge against. Never the wall clock.
        windows: The check-in lead times, or None where attendance is off.

    Returns:
        A `CheckInJudgement`. Its part keeps ``reopened``, whether the amendment reopens the
        check-in: with attendance on, an allowed amendment always does, one whose deadline has
        passed being refused.
    """
    judged: dict[str, CheckInWindow] = {}
    refusals: list[str] = []
    warnings: list[str] = []
    if windows is not None:
        moments = round_event_moments(moment, weather=None, attendance=windows)
        listed = [
            ("call", "Check-in call", moments["rsvp_notice"]),
            # Absent from the moments where the last notice is 0 hours: no last notice at all.
            *(
                [("last_notice", "Check-in last notice", moments["rsvp_last_notice"])]
                if "rsvp_last_notice" in moments
                else []
            ),
            # Zero means the deadline is the round's own start time, the documented meaning of
            # the setting, not an absent window.
            ("deadline", "Check-in deadline", moments["rsvp_deadline"]),
        ]
        for key, label, fire_at in listed:
            # ``<=`` rather than ``<``: a window falling exactly now counts as having passed,
            # the same threshold `approval_window_service` holds to, so the two cannot disagree
            # about a single round.
            judged[key] = CheckInWindow(label=label, fire_at=fire_at, stands=fire_at <= now)

        # A check-in that would open and close in the same instant asks a question nobody can
        # answer, and the round would be recorded afterwards as perfect attendance for the whole
        # division. This is the one refusal the moment itself earns.
        if judged["deadline"].stands:
            refusals.append(
                "The check-in deadline for that moment has already passed, so the round would "
                "have no check-in at all. Move it further out, or shorten the deadline."
            )
        elif judged["call"].stands:
            warnings.append(
                "The check-in call for that moment is already due, so no fresh call will go out "
                "— the one already posted stands."
            )
        if "last_notice" in judged and judged["last_notice"].stands and not judged["call"].stands:
            warnings.append(
                "The check-in last notice for that moment is already due, so this round will "
                "get no reminder."
            )
    deadline = judged.get("deadline")
    stays_closed = deadline is not None and deadline.stands
    part = AmendmentPart(
        refusals=tuple(refusals),
        warnings=tuple(warnings),
        kept={"reopened": bool(judged) and not stays_closed},
    )
    return CheckInJudgement(part=part, windows=judged)
