"""When a round's timed events fall, and the delay after the race that places its clean-ups.

`round_event_moments` works out when each of a round's events falls, keyed by the kind its job is
stored with (`ROUND_JOB_KINDS`); `clean_up_moment` gives the clean-ups' moment alone, and
`round_event_moments` places them by it. The scheduler's arming, the approval's overdue windows
and the amendment's rules all read `round_event_moments`, so for them the rules hold in one place:
a last notice of 0 hours is no last notice, a deadline of 0 hours is the round's own moment, and a
moment stored without a zone is UTC.

Pure by design: no database, no clock, no scheduler; `read_round_windows` only gathers what the
modules' subscribers to the windows hook hand back. The two window types `round_event_moments`
reads, and the kind names its keys match (`ROUND_JOB_KINDS`), are plain data and live in
`core.models.round`, which imports no service.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

from leaguebot.core.models.round import AttendanceWindows, RoundLead, WeatherWindows
from leaguebot.framework.utils.hooks import AwaitedHook

#: How long after a round's scheduled start its posted messages come down: the weather module's
#: forecast, whichever of Phases 1 to 3 still stands, and the attendance module's check-in call
#: with its last notice and its distribution message. One figure for both, so a round's
#: channels clear together.
POST_RACE_CLEANUP_DELAY = timedelta(hours=24)


async def read_round_windows(
    windows_read: AwaitedHook,
) -> tuple[AttendanceWindows | None, WeatherWindows | None]:
    """The lead times the modules configure, as `(attendance, weather)`, the way
    `round_event_moments` and the judges take them: each hands back its window or nothing.

    Every module signed up to the hook checks its own switch and hands nothing where it is off,
    so that a window is there only for a module that is on.
    """
    handed = await windows_read.call()
    attendance = next((each for each in handed if isinstance(each, AttendanceWindows)), None)
    weather = next((each for each in handed if isinstance(each, WeatherWindows)), None)
    return attendance, weather


async def read_round_leads(leads_read: AwaitedHook) -> tuple[RoundLead, ...]:
    """Every timer the modules want set for a round, in the order the modules signed up to
    *leads_read*: each hands back its own leads, plain records core arms under its own naming
    (`RoundJobs.arm_leads`), and a module that is off hands back none, results alone answering
    whatever its switch."""
    return tuple(lead for part in await leads_read.call() for lead in part or ())


async def read_phases_drawn(
    phases_drawn_read: AwaitedHook, round_ids: Sequence[int]
) -> dict[int, dict[int, bool]]:
    """Whether each of Phases 1 to 3 has been drawn, by phase number, for each round in
    *round_ids*, by round id, as the module that draws them says: they are its own record, not a
    column of core's round. A round nobody answers for is absent, and its phases count as not
    drawn.
    """
    drawn: dict[int, dict[int, bool]] = {}
    for part in await phases_drawn_read.call(round_ids):
        drawn.update(part or {})
    return drawn


def results_left_to_advance(*, test_mode: bool, results_on: bool, weather_on: bool) -> bool:
    """Whether a round's results submission is left to `/test-mode advance`, armed by no job.

    So where results is on and weather off under test mode (core specification, "What confirming
    placements does"). The season's approval and a round's amendment both read it here, so that an
    amended round is left to the advance as its neighbours are, whatever its moment."""
    return test_mode and results_on and not weather_on


def as_utc(moment: datetime) -> datetime:
    """A moment with a zone is itself; a naive one is UTC. The queue's clock is read through it: a
    round's moment is aware already, and goes through no such conversion."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def clean_up_moment(scheduled_at: datetime) -> datetime:
    """When a round's clean-ups fall, `POST_RACE_CLEANUP_DELAY` after its moment, in UTC.

    The forecast's and the check-in's fall together and depend on no lead time, so the callers
    that need only this, the arming of a clean-up and the amendment's test of one due, ask for
    it here and `round_event_moments` places both clean-ups by it.
    """
    return scheduled_at + POST_RACE_CLEANUP_DELAY


def round_event_moments(
    scheduled_at: datetime,
    *,
    weather: WeatherWindows | None,
    attendance: AttendanceWindows | None,
) -> Mapping[str, datetime]:
    """When each of a round's events falls, keyed by the kind its job is stored with.

    Where *weather* is handed, its three phases fall at their horizons before the round and the forecast's
    clean-up `POST_RACE_CLEANUP_DELAY` after it. Where *attendance* is handed, the check-in's
    call, last notice and deadline fall at theirs before it and its clean-up
    `POST_RACE_CLEANUP_DELAY` after: a last notice of 0 hours is no last notice and is left
    out, and a deadline of 0 hours is the round's own moment. A round's moment is aware, and
    every moment given is in UTC.

    No moment is judged here, neither past nor ahead: whether one still falls ahead is the
    caller's to ask of the `now` it holds.
    """
    at = scheduled_at.astimezone(timezone.utc)
    moments: dict[str, datetime] = {}
    if weather is not None:
        moments["weather_p1"] = at - timedelta(days=weather.phase_1_days)
        moments["weather_p2"] = at - timedelta(days=weather.phase_2_days)
        moments["weather_p3"] = at - timedelta(hours=weather.phase_3_hours)
        moments["cleanup"] = clean_up_moment(at)
    if attendance is not None:
        moments["rsvp_notice"] = at - timedelta(days=attendance.notice_days)
        if attendance.last_notice_hours > 0:
            moments["rsvp_last_notice"] = at - timedelta(hours=attendance.last_notice_hours)
        moments["rsvp_deadline"] = at - timedelta(hours=max(attendance.deadline_hours, 0))
        moments["rsvp_cleanup"] = clean_up_moment(at)
    return moments
