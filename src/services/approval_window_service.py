"""What is already behind a season on the day it is approved.

Two faults, and a season can hold either. A round's **configured lead times** may have
elapsed, and the round's **own moment** may have.

A season's scheduled work is armed at approval, and every piece of it fires at some
configured distance *before* the round: the check-in call five days out, the last notice a
day out, the check-in deadline two hours out, the three weather phases at their own
horizons. Approve a season late enough and some of those moments are already behind you.

What used to happen then was silent and different for each module. `schedule_attendance_round`
skipped a past-dated job and logged a line nobody reads, so the round asked nobody whether
they were racing, distributed no reserves, and was recorded afterwards as perfect attendance
for the whole division. `schedule_round` armed its weather jobs regardless, and the scheduler
discarded them for being past its five-minute misfire grace — recoverable, but only if the
host happened to restart before the race.

This module is the check that makes both unreachable from approval. It computes nothing and
decides nothing about what to do; it reports which windows have passed, and `_do_approve`
refuses on the answer. The league's remedy is to move the round or shorten the window, and
that is a choice only they can make.

**A round's own moment is judged too, and whatever the modules are** (#181, decided
2026-09-14). The windows above are contributed by the modules that configure them, so a
league running neither weather nor attendance offered none at all — and a season every round
of which was already in the past was approved in silence. Nothing recovered it: a round's
result submission is armed against the round's own moment, a job that far behind is thrown
away by the scheduler's 300-second misfire grace rather than run, `run_result_submission_job`
is the only route to a submission channel, and that job is the round's one clock-driven
transition. Every round stayed at *not run* for good. `/round amend` refuses a backwards move
on the same reasoning and in the same words (#182), so the two doors agree.

**A division's faults are reduced to the latest round of each kind** (decided 2026-09-14).
`calendar_faults` names the last round already run and, where it is a later round, the last
round holding an elapsed window — and no more. Every earlier round is implied by them: a
manager who moves the calendar past the round named has moved it past all of them. Naming
each round against each window instead told a manager sixty things where two would do, and
ran a season built wholly in the past past Discord's 2000-character limit.

**The threshold is ``fire_at <= now``** — a window whose moment is exactly now counts as
elapsed. That is precisely the complement of `schedule_attendance_round`'s own
``if fire_at > now``, so the gate and the attendance scheduler cannot disagree about a single
round: nothing this module reports would have been scheduled, and nothing it passes over would
have been skipped. For the weather phases it is very slightly stricter than the scheduler's
300-second misfire grace, which is the safe direction — better to refuse a job that might
just have squeezed in than to admit one the scheduler will throw away. `test_a_window_exactly
_now_is_overdue` and `test_the_window_offsets_match_the_scheduler` pin both halves of that,
because the offsets live here *and* in `scheduler_service` and would otherwise drift apart
without a failing test to say so.

Pure by design: no database, no Discord, and ``now`` passed in rather than read from the
wall clock, so the tests can pin a date and a moment together.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from models.round import Round, RoundStatus


@dataclass(frozen=True)
class AttendanceWindows:
    """The three check-in lead times, as `attendance_config` stores them."""

    notice_days: int
    last_notice_hours: int
    deadline_hours: int


@dataclass(frozen=True)
class WeatherWindows:
    """The three forecast phase horizons, as `weather_pipeline_config` stores them."""

    phase_1_days: int
    phase_2_days: int
    phase_3_hours: int


@dataclass(frozen=True)
class OverdueWindow:
    """One configured moment that has already passed for one round.

    *label* names the window as a league manager knows it, and *lead* says how far before the
    round it sits — the refusal quotes both, because "the check-in call was due" means little
    without "five days before" beside it.
    """

    division_name: str
    round_number: int
    scheduled_at: datetime
    label: str
    lead: str
    fire_at: datetime


def _as_utc(moment: datetime) -> datetime:
    """Naive datetimes out of the database are UTC, as they are everywhere else here."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def overdue_windows(
    rounds: list[tuple[str, Round]],
    *,
    now: datetime,
    attendance: AttendanceWindows | None = None,
    weather: WeatherWindows | None = None,
) -> list[OverdueWindow]:
    """Report every configured window of *rounds* that has already elapsed at *now*.

    Args:
        rounds:     ``(division_name, round)`` pairs, in any order.
        now:        The moment to judge against. Required, and never the wall clock.
        attendance: The check-in lead times, or None where the module is disabled.
        weather:    The forecast horizons, or None where the module is disabled.

    Returns:
        One entry per elapsed window, sorted by division name, then round number, then the
        moment the window was due. Empty when every window is still ahead, and empty when
        both modules are disabled, which leaves no windows to check at all.

        That second emptiness is not a verdict that the season is fine. A round's own moment
        is not a window and is not judged here — `calendar_faults` judges it, whatever the
        modules. Reading this answer alone as "the season may be approved" is #181.
    """
    found: list[OverdueWindow] = []
    now = _as_utc(now)

    for division_name, rnd in rounds:
        # A cancelled round has no work left to lose, and refusing a season because of one
        # would leave a league unable to approve until they deleted history they may want.
        if rnd.status == RoundStatus.CANCELLED.value:
            continue

        scheduled_at = _as_utc(rnd.scheduled_at)
        windows: list[tuple[str, str, datetime]] = []

        if attendance is not None:
            windows.append((
                "Check-in call",
                _days(attendance.notice_days),
                scheduled_at - timedelta(days=attendance.notice_days),
            ))
            # Zero disables the last notice outright, exactly as the scheduler reads it.
            if attendance.last_notice_hours > 0:
                windows.append((
                    "Check-in last notice",
                    _hours(attendance.last_notice_hours),
                    scheduled_at - timedelta(hours=attendance.last_notice_hours),
                ))
            # Zero means the deadline is the round's own start time — the documented
            # meaning of the setting, not an absent window.
            windows.append((
                "Check-in deadline",
                _hours(attendance.deadline_hours) if attendance.deadline_hours > 0
                else "at the round",
                scheduled_at - timedelta(hours=attendance.deadline_hours),
            ))

        if weather is not None:
            windows.append((
                "Weather Phase 1",
                _days(weather.phase_1_days),
                scheduled_at - timedelta(days=weather.phase_1_days),
            ))
            windows.append((
                "Weather Phase 2",
                _days(weather.phase_2_days),
                scheduled_at - timedelta(days=weather.phase_2_days),
            ))
            windows.append((
                "Weather Phase 3",
                _hours(weather.phase_3_hours),
                scheduled_at - timedelta(hours=weather.phase_3_hours),
            ))

        for label, lead, fire_at in windows:
            if fire_at <= now:
                found.append(
                    OverdueWindow(
                        division_name=division_name,
                        round_number=rnd.round_number,
                        scheduled_at=scheduled_at,
                        label=label,
                        lead=lead,
                        fire_at=fire_at,
                    )
                )

    # Sorted rather than left in the order the divisions were queried in: the refusal is read
    # by a person working down it, and the suite must not depend on what order a host's
    # database happened to hand the rounds back in.
    found.sort(key=lambda w: (w.division_name, w.round_number, w.fire_at))
    return found


@dataclass(frozen=True)
class CalendarFault:
    """What one division's calendar is refused for, reduced to the rounds that bound it.

    *latest_past* is the last round whose own moment has gone by, and *latest_window* the
    last round holding an elapsed window — given only where that is a **later** round than
    *latest_past*, because a round already run has every one of its windows elapsed too and
    saying so twice tells a manager nothing new.

    At least one of the two is set; a division with neither yields no fault at all.
    """

    latest_past: Round | None
    latest_window: OverdueWindow | None


def calendar_faults(
    rounds: list[Round],
    *,
    now: datetime,
    attendance: AttendanceWindows | None = None,
    weather: WeatherWindows | None = None,
) -> CalendarFault | None:
    """Reduce one division's calendar to the two rounds that bound what is wrong with it.

    Args:
        rounds:     One division's rounds, in any order.
        now:        The moment to judge against. Required, and never the wall clock.
        attendance: The check-in lead times, or None where the module is disabled.
        weather:    The forecast horizons, or None where the module is disabled.

    Returns:
        The division's fault, or None where its calendar is clean.

    **The threshold is ``scheduled_at <= now``**, the same one `judge_amendment` refuses a
    backwards move on, so the approval and `/round amend` cannot disagree about a round.

    Cancelled rounds are skipped, exactly as `overdue_windows` skips them and for the same
    reason: one has no scheduled work left to lose, and refusing a season because of one
    would leave a league unable to approve until they deleted history they may want.
    """
    live = [rnd for rnd in rounds if rnd.status != RoundStatus.CANCELLED.value]
    if not live:
        return None

    # Normalised exactly as `overdue_windows` normalises it, so a naive ``now`` compares
    # here rather than raising, and the two halves of this module cannot disagree about
    # what moment they were asked about.
    now = _as_utc(now)

    # Ordered by moment, and by round number where two share one, so that three hosts whose
    # databases need not agree about row order still reach the same answer.
    def _order(rnd: Round) -> tuple[datetime, int]:
        return (_as_utc(rnd.scheduled_at), rnd.round_number)

    past = [rnd for rnd in live if _as_utc(rnd.scheduled_at) <= now]
    latest_past = max(past, key=_order) if past else None

    # The windows are the same evaluation `overdue_windows` performs for the report — asked
    # of this one division, under a name it never shows, because only the reduction below is
    # shown. Sharing it is what keeps the offsets in one place and under one drift guard.
    windows = overdue_windows(
        [("", rnd) for rnd in live], now=now, attendance=attendance, weather=weather
    )
    latest_window = None
    if windows:
        by_number = {rnd.round_number: rnd for rnd in live}
        # The last round holding one, and of its elapsed windows the one due earliest:
        # clearing the furthest-overdue window clears the rest, so that is the window that
        # sets how far the round has to move.
        latest = max(windows, key=lambda w: _order(by_number[w.round_number]))
        latest_round = by_number[latest.round_number]
        if latest_past is None or _order(latest_round) > _order(latest_past):
            latest_window = min(
                (w for w in windows if w.round_number == latest_round.round_number),
                key=lambda w: w.fire_at,
            )

    if latest_past is None and latest_window is None:
        return None
    return CalendarFault(latest_past=latest_past, latest_window=latest_window)


def _days(count: int) -> str:
    return f"{count} day{'' if count == 1 else 's'} before"


def _hours(count: int) -> str:
    return f"{count} hour{'' if count == 1 else 's'} before"
