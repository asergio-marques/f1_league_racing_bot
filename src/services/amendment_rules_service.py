"""What an amendment to a round may do, and what it costs the round.

A round of an active season may have its track, its moment or its format amended. Each of those
costs the round something — a forecast drawn for a circuit it is no longer run at, a check-in
called for a date that has moved — and some of them cost so much that the amendment must not
happen at all. This module decides which, and nothing else: it computes no consequence, writes
nothing and posts nothing. `amendment_service` carries the answer out.

**Pure by design**: no database, no Discord, and ``now`` passed in rather than read from the wall
clock, so the tests can pin a date and a moment together. Modelled on `approval_window_service`,
which answers the neighbouring question at `/season approve`, and which owns the window offsets
both modules read — the two must not drift, and `test_the_window_offsets_match_the_scheduler`
already pins those offsets against the scheduler.

**One question answers almost all of it.** For every window a round carries — the check-in call,
its last notice, its deadline, and the three forecast phases — ask: *would this have run already,
were the round always to have stood at its new moment?* Where it would, what was posted stands and
nothing is posted in its place; a moment already past is never honoured retroactively. Where it
would not, what was posted is withdrawn and the work armed again for its new moment. That single
rule covers the delay, the bring-forward and the change that moves no date at all.

**An amendment is one change.** However many of the three fields it alters, it is judged once,
against the round as it will stand once every change is in, and where any rule refuses any part of
it none of it happens. That is why this takes a change set rather than a field: a track change is
judged against the new date when one is given in the same breath, and judging the fields
separately would refuse an amendment the league is entitled to make.

**The verdict is judged again at the moment the amendment is confirmed**, not only when it is
offered. A window can pass while the confirmation stands, and an amendment allowed on the strength
of a window that has since closed is exactly the silent loss these rules exist to prevent. That is
the caller's job; this module is given a ``now`` and answers for that moment alone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from models.round import ROUND_CANCELLABLE, Round, RoundFormat, RoundStatus
from services.approval_window_service import AttendanceWindows, WeatherWindows

#: The fields of a round a league may amend.
AMENDABLE_FIELDS = frozenset({"track_name", "format", "scheduled_at"})


@dataclass(frozen=True)
class WindowOutcome:
    """What becomes of one of a round's windows once it is amended.

    *stands* is the answer to the one question: the window would have run already under the
    round's new moment, so whatever was posted for it stays and nothing replaces it. *fire_at* is
    the moment it falls at under the new arrangement, which is when the work is armed for where
    it does not stand.
    """

    label: str
    fire_at: datetime
    stands: bool

    @property
    def rearm(self) -> bool:
        """The complement of *stands*: the window is still ahead and must be armed."""
        return not self.stands


@dataclass(frozen=True)
class AmendmentVerdict:
    """Whether an amendment may proceed, and what it does to the round.

    *refusals* empty means it may. Anything in it means none of the amendment happens — a change
    set is one change, and a rule refusing one field refuses the lot.
    """

    refusals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    #: The check-in windows, keyed "call" / "last_notice" / "deadline". Empty with attendance off.
    check_in: dict[str, WindowOutcome] = field(default_factory=dict)
    #: The forecast phases, keyed 1 / 2 / 3. Empty with weather off.
    phases: dict[int, WindowOutcome] = field(default_factory=dict)
    #: The round's moment once amended, which every window above is measured from.
    new_moment: datetime | None = None

    @property
    def allowed(self) -> bool:
        return not self.refusals

    @property
    def recall_check_in(self) -> bool:
        """Whether the check-in call is to be posted again, carrying what changed.

        True where the call is still to come under the new arrangement — either it never went out
        and is now armed, or it did and the round has moved far enough that it would not have.
        """
        call = self.check_in.get("call")
        return call is not None and call.rearm

    @property
    def check_in_stays_closed(self) -> bool:
        """Whether the round's check-in is beyond recall, its deadline having passed either way.

        Nothing is posted afresh for such a round and no answer may be changed: the deadline
        computed from its new moment has gone by, so there is no window left to reopen.
        """
        deadline = self.check_in.get("deadline")
        return deadline is not None and deadline.stands


def _as_utc(moment: datetime) -> datetime:
    """Naive datetimes out of the database are UTC, as they are everywhere else here."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def _amended_moment(rnd: Round, changes: dict[str, Any]) -> datetime:
    """The round's moment once *changes* are in — the new one, or its present one."""
    moment = changes.get("scheduled_at", rnd.scheduled_at)
    if not isinstance(moment, datetime):
        moment = datetime.fromisoformat(str(moment))
    return _as_utc(moment)


def judge_amendment(
    rnd: Round,
    changes: dict[str, Any],
    *,
    now: datetime,
    attendance: AttendanceWindows | None = None,
    weather: WeatherWindows | None = None,
) -> AmendmentVerdict:
    """Judge *changes* against *rnd* as it will stand once they are in.

    Args:
        rnd:        The round as it stands, carrying its status and its phase-done flags.
        changes:    ``{field: new value}`` over `AMENDABLE_FIELDS`. An empty set is refused.
        now:        The moment to judge against. Required, and never the wall clock.
        attendance: The check-in lead times, or None where the module is disabled — with it off
                    the round has no check-in to lose and none of its windows are judged.
        weather:    The forecast horizons, or None where the module is disabled.

    Returns:
        An `AmendmentVerdict`. Where `refusals` is non-empty the rest describes the amendment
        that was refused and must not be acted upon.
    """
    now = _as_utc(now)
    refusals: list[str] = []
    warnings: list[str] = []

    unknown = sorted(set(changes) - AMENDABLE_FIELDS)
    if unknown:
        refusals.append(f"A round's {', '.join(unknown)} cannot be amended.")
    if not changes:
        refusals.append("An amendment must change at least one of the track, the moment or the format.")

    # ── Is the round still one that may be amended at all? ──────────────────────────────────
    # The same set that governs cancellation, and for the same reason: once results are entered
    # the drivers have reports and appeals lodged against them, and an amendment would take that
    # from them. It blocks a cancelled round for free, that being outside the set too.
    if rnd.status not in ROUND_CANCELLABLE:
        if rnd.status == RoundStatus.CANCELLED.value:
            refusals.append("This round has been cancelled and can no longer be amended.")
        else:
            refusals.append(
                "This round's results have been entered, so it can no longer be amended. "
                "Drivers have reports and appeals to lodge against them."
            )

    new_moment = _amended_moment(rnd, changes) if not unknown else _as_utc(rnd.scheduled_at)
    moment_moved = "scheduled_at" in changes

    # ── The check-in windows ───────────────────────────────────────────────────────────────
    check_in: dict[str, WindowOutcome] = {}
    if attendance is not None:
        windows = [
            ("call", "Check-in call", new_moment - timedelta(days=attendance.notice_days)),
            # Zero disables the last notice outright, exactly as the scheduler reads it.
            *(
                [(
                    "last_notice",
                    "Check-in last notice",
                    new_moment - timedelta(hours=attendance.last_notice_hours),
                )]
                if attendance.last_notice_hours > 0
                else []
            ),
            # Zero means the deadline is the round's own start time — the documented meaning of
            # the setting, not an absent window.
            ("deadline", "Check-in deadline", new_moment - timedelta(hours=attendance.deadline_hours)),
        ]
        for key, label, fire_at in windows:
            # ``<=`` rather than ``<``: a window falling exactly now counts as having passed,
            # the same threshold `approval_window_service` holds to, so the two cannot disagree
            # about a single round.
            check_in[key] = WindowOutcome(label=label, fire_at=fire_at, stands=fire_at <= now)

        # A check-in that would open and close in the same instant asks a question nobody can
        # answer, and the round would be recorded afterwards as perfect attendance for the whole
        # division. This is the one refusal the moment itself earns.
        if check_in["deadline"].stands:
            refusals.append(
                "The check-in deadline for that moment has already passed, so the round would "
                "have no check-in at all. Move it further out, or shorten the deadline."
            )
        elif check_in["call"].stands:
            warnings.append(
                "The check-in call for that moment is already due, so no fresh call will go out "
                "— the one already posted stands."
            )
        if "last_notice" in check_in and check_in["last_notice"].stands and not check_in["call"].stands:
            warnings.append(
                "The check-in last notice for that moment is already due, so this round will "
                "get no reminder."
            )

    # ── The forecast phases ────────────────────────────────────────────────────────────────
    phases: dict[int, WindowOutcome] = {}
    if weather is not None:
        for number, offset in (
            (1, timedelta(days=weather.phase_1_days)),
            (2, timedelta(days=weather.phase_2_days)),
            (3, timedelta(hours=weather.phase_3_hours)),
        ):
            fire_at = new_moment - offset
            phases[number] = WindowOutcome(
                label=f"Weather Phase {number}", fire_at=fire_at, stands=fire_at <= now
            )

    # ── What the track and the format may no longer be ─────────────────────────────────────
    # A forecast already posted was drawn for this round's circuit and sessions and cannot be
    # unsaid — but only where it is still standing once the amendment is in. Where the round has
    # moved far enough that the phase would not have run under its new moment, that forecast is
    # withdrawn and drawn again, so there is nothing left that was drawn for the old circuit and
    # nothing to refuse. Moving a round is therefore the league's remedy for a circuit or a
    # format that must be corrected late, and it is why a change set is judged as one change.
    #
    # Judged on what was actually performed rather than on the module's present state: a forecast
    # posted while weather was on was still posted, whatever the module says now. Without
    # configured horizons nothing can be said about withdrawal, so the flag alone decides.
    def _still_stands(number: int) -> bool:
        outcome = phases.get(number)
        return outcome is None or outcome.stands

    if "track_name" in changes and rnd.phase1_done and _still_stands(1):
        refusals.append(
            "The first forecast for this round has already been posted, and it was drawn for "
            "its present circuit. Amend the round's moment as well to put the forecasts back "
            "in question."
        )
    if "format" in changes and rnd.phase2_done and _still_stands(2):
        refusals.append(
            "The second forecast for this round has already been posted, and it was drawn for "
            "its present sessions. Amend the round's moment as well to put the forecasts back "
            "in question."
        )
    if ("track_name" in changes or "format" in changes) and new_moment <= now:
        refusals.append(
            "This round has already started, so its track and format can no longer be amended."
        )

    # A mystery round names no track, and naming one is how a league reveals it — which is the
    # format change, not a track change slipped in beside it.
    if (
        "track_name" in changes
        and "format" not in changes
        and rnd.format == RoundFormat.MYSTERY
    ):
        refusals.append(
            "A mystery round names no circuit. Amend its format as well to reveal it."
        )

    if moment_moved and new_moment <= now:
        warnings.append(
            "That moment is already in the past, so the round will be treated as having started."
        )

    return AmendmentVerdict(
        refusals=refusals,
        warnings=warnings,
        check_in=check_in,
        phases=phases,
        new_moment=new_moment,
    )
