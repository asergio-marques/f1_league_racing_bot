"""Which of a season's configured lead times have already elapsed.

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
        moment the window was due. Empty when every window is still ahead — including when
        both modules are disabled, which leaves no windows to check at all.
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


def _days(count: int) -> str:
    return f"{count} day{'' if count == 1 else 's'} before"


def _hours(count: int) -> str:
    return f"{count} hour{'' if count == 1 else 's'} before"
