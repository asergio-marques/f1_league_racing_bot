"""When a round's timed events fall: the lead times that place them, and the delay after the race.

`round_event_moments` is the one function that works out when each of a round's events falls,
keyed by the kind its job is stored with (`ROUND_JOB_KINDS`); `clean_up_moment` gives the clean-ups'
alone, and `round_event_moments` places them by it. The scheduler's arming, the approval's
overdue windows and the amendment's rules all read it, so the rules every copy once carried hold
in one place: a last notice of 0 hours is no last notice, a deadline of 0 hours is the round's own
moment, and a moment stored without a zone is UTC.

Pure by design: no database, no clock, no scheduler. Models import no service, so each of those
imports this one place.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: The kinds of the jobs the weather module owns: its three forecast phases and the post-race
#: clean-up that deletes what they posted.
WEATHER_JOB_KINDS = frozenset({"weather_p1", "weather_p2", "weather_p3", "cleanup"})

#: The kinds of a round's nine jobs: each is its job id's prefix, and its key the round id. A
#: job is a round's where its kind is one of these; another job's key, an amendment's lapse
#: timer's for one, is no round's, however it is numbered.
ROUND_JOB_KINDS = WEATHER_JOB_KINDS | {
    "results", "rsvp_notice", "rsvp_last_notice", "rsvp_deadline", "rsvp_cleanup",
}

#: How long after a round's scheduled start its posted messages come down: the weather module's
#: forecast, whichever of Phases 1 to 3 still stands, and the attendance module's check-in call
#: with its last notice and its distribution message (#425). One figure for both, so a round's
#: channels clear together.
POST_RACE_CLEANUP_DELAY = timedelta(hours=24)


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


def as_utc(moment: datetime) -> datetime:
    """A moment with a zone is itself; a naive one, as the database hands it out, is UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def clean_up_moment(scheduled_at: datetime) -> datetime:
    """When a round's clean-ups fall, `POST_RACE_CLEANUP_DELAY` after its moment, in UTC.

    The forecast's and the check-in's fall together and depend on no lead time, so the callers
    that need only this, the arming of a clean-up and the amendment's test of one due, ask for
    it here and `round_event_moments` places both clean-ups by it.
    """
    return as_utc(scheduled_at) + POST_RACE_CLEANUP_DELAY


def round_event_moments(
    scheduled_at: datetime,
    *,
    weather: WeatherWindows | None,
    attendance: AttendanceWindows | None,
) -> Mapping[str, datetime]:
    """When each of a round's events falls, keyed by the kind its job is stored with.

    The results submission falls at the round's moment, whatever the modules. Where *weather* is
    handed, its three phases fall at their horizons before the round and the forecast's
    clean-up `POST_RACE_CLEANUP_DELAY` after it. Where *attendance* is handed, the check-in's
    call, last notice and deadline fall at theirs before it and its clean-up
    `POST_RACE_CLEANUP_DELAY` after: a last notice of 0 hours is no last notice and is left
    out, and a deadline of 0 hours is the round's own moment. A naive *scheduled_at* is UTC, and
    every moment given is in UTC.

    No moment is judged here, neither past nor ahead: whether one still falls ahead is the
    caller's to ask of the `now` it holds.
    """
    at = as_utc(scheduled_at)
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
    moments["results"] = at
    return moments
