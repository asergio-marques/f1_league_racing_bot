"""A round amended, as the forecasts answer it (#287).

Weather takes part in an amendment through core's `round_armed`: when the round is armed again,
`round_armed` plans the posts the round now owes of weather's and says whether the round's
forecasts are armed, and with what. Core makes the scheduler's calls from that answer, holding no
switch and no rule of weather's.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.core.models.amendment import RoundArming
from leaguebot.core.models.change import PlannedStep
from leaguebot.core.models.round import ForecastJobs, Round, RoundFormat, WeatherWindows
from leaguebot.weather.services.draw_guard import weather_enabled_on
from leaguebot.weather.services.forecast_changes import (
    DELETE_FORECAST,
    NOTIFY_INVALIDATION,
    RERUN_PHASE,
)
from leaguebot.weather.services.round_phases import first_horizon_ahead, phases_drawn_on, phases_due


async def round_armed(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    rnd: Round,
    kept: Mapping[str, Any],
    weather: WeatherWindows,
    now: Callable[[], datetime],
    **_context: Any,
) -> RoundArming:
    """What weather owes the round armed again, read on *db* at the queue's clock *now*.

    The steps, in order: the deletion of each withdrawn forecast that was posted, the notice that
    the forecasts no longer stand, and the draw of each phase due at once. *kept* is what weather's
    part of the save kept (`phase_withdrawal.round_amended_on`) with the track, and is empty where
    the save was not made. The phases due are drawn by a job of the amendment's, so the forecasts'
    timers skip them: armed with no lateness limit, a timer would draw the phase a second time.

    The forecasts are armed only while weather is on, and not for a mystery round moved inside its
    first horizon, whose notice already told the drivers and which must not be fired
    retroactively. Subscribed to core's `round_armed`.
    """
    at = now()
    on = await weather_enabled_on(db)
    draw: list[int] = []
    if on:
        drawn = (await phases_drawn_on(db, [round_id])).get(round_id, {})
        draw = phases_due(rnd, weather, at, drawn=drawn)
    posted = kept.get("posted", [])
    planned = [PlannedStep(DELETE_FORECAST, {"phase": n}) for n in posted]
    if posted:
        planned.append(PlannedStep(NOTIFY_INVALIDATION, {"track": kept["track"]}))
    planned += [PlannedStep(RERUN_PHASE, {"phase": n}) for n in draw]
    armed = on and (rnd.format != RoundFormat.MYSTERY or first_horizon_ahead(rnd, weather, at))
    return RoundArming(
        steps=tuple(planned),
        jobs=ForecastJobs(windows=weather, skip_phases=frozenset(draw)) if armed else None,
    )
