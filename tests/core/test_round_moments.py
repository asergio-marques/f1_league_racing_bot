"""When each of a round's timed events falls: one pure function, read by every caller.

`round_event_moments` gives each of a round's events its moment, keyed by the kind its job is
stored with. The scheduler's arming, the approval's overdue windows and the amendment's rules all
read it, so the rules every copy once carried hold in one place: a last notice of 0 hours is no
last notice, a deadline of 0 hours is the round's own moment, and a moment stored without a zone
is UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

#: Pro's round, on 14 June 2026 at 14:00 UTC.
MOMENT = datetime(2026, 6, 14, 14, 0, tzinfo=timezone.utc)

#: Forecast horizons and a check-in unlike the packaged defaults, so a default cannot pass.
WEATHER = {"phase_1_days": 6, "phase_2_days": 3, "phase_3_hours": 4}
CHECK_IN = {"notice_days": 4, "last_notice_hours": 12, "deadline_hours": 1}

_PHASES = {
    "weather_p1": -timedelta(days=6),
    "weather_p2": -timedelta(days=3),
    "weather_p3": -timedelta(hours=4),
    "cleanup": timedelta(hours=24),
}
_CALL = {
    "rsvp_notice": -timedelta(days=4),
    "rsvp_last_notice": -timedelta(hours=12),
    "rsvp_deadline": -timedelta(hours=1),
    "rsvp_cleanup": timedelta(hours=24),
}
_RESULTS = {"results": timedelta(0)}


@pytest.mark.parametrize(
    ("scheduled_at", "weather", "check_in", "offsets"),
    [
        pytest.param(MOMENT, None, None, _RESULTS, id="neither module"),
        pytest.param(MOMENT, WEATHER, None, {**_PHASES, **_RESULTS}, id="weather alone"),
        pytest.param(MOMENT, None, CHECK_IN, {**_CALL, **_RESULTS}, id="check-in alone"),
        pytest.param(MOMENT, WEATHER, CHECK_IN, {**_PHASES, **_CALL, **_RESULTS}, id="both"),
        pytest.param(
            MOMENT, None, {**CHECK_IN, "last_notice_hours": 0},
            {k: v for k, v in {**_CALL, **_RESULTS}.items() if k != "rsvp_last_notice"},
            id="a last notice of 0 is no last notice",
        ),
        pytest.param(
            MOMENT, None, {**CHECK_IN, "deadline_hours": 0},
            {**_CALL, "rsvp_deadline": timedelta(0), **_RESULTS},
            id="a deadline of 0 is the round's moment",
        ),
        pytest.param(
            MOMENT.replace(tzinfo=None), WEATHER, CHECK_IN, {**_PHASES, **_CALL, **_RESULTS},
            id="a moment with no zone is UTC",
        ),
    ],
)
def test_round_event_moments(scheduled_at, weather, check_in, offsets):
    """Each event of a round on 14 June 2026 at 14:00 falls at its own offset from it: the
    results submission at the round's moment, Phases 1 to 3 six days, three days and four hours
    before it and their clean-up a day after, only where weather is handed; the check-in's call
    four days before, its last notice twelve hours before, its deadline an hour before and its
    clean-up a day after, only where the check-in is handed. Every moment given is in UTC."""
    from leaguebot.core.models.round_moments import (
        AttendanceWindows,
        WeatherWindows,
        round_event_moments,
    )

    moments = round_event_moments(
        scheduled_at,
        weather=None if weather is None else WeatherWindows(**weather),
        attendance=None if check_in is None else AttendanceWindows(**check_in),
    )

    assert dict(moments) == {kind: MOMENT + offset for kind, offset in offsets.items()}
    assert all(each.tzinfo is not None for each in moments.values())

