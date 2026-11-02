"""When a round's timed events fall: the lead times that place them, and the delay after the race.

Pure by design: no database, no clock, no scheduler. Models import no service, so the scheduler's
arming, the approval's overdue windows and the amendment's rules each import the one place.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

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
