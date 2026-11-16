"""A round amended, as the forecasts answer it (#287).

Weather takes part in an amendment through two of core's hooks. `amendment_judged` is weather's
part of the judgement: the two refusals for a forecast already posted, the note of what the
amendment does to the forecasts, and the phases it withdraws, which weather keeps for the
amendment's save (`phase_withdrawal.round_amended_on`). `judge_forecasts` is pure: the phases
drawn are read by `amendment_judged`, on the connection core hands it. When the round is armed
again, `round_armed` plans the posts the round now owes of weather's and says whether the round's
forecasts are armed, and with what. Core makes the scheduler's calls from that answer, holding no
switch and no rule of weather's.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.framework.db.database import get_connection
from leaguebot.core.models.amendment import AmendmentPart, RoundArming
from leaguebot.framework.models.change import PlannedStep
from leaguebot.core.models.round import (
    ForecastJobs,
    Round,
    RoundFormat,
    WeatherWindows,
)
from leaguebot.core.services.rounds.round_moments import round_event_moments
from leaguebot.weather.services.draw_guard import weather_enabled_on
from leaguebot.weather.services.forecast_changes import (
    DELETE_FORECAST,
    NOTIFY_INVALIDATION,
    RERUN_PHASE,
)
from leaguebot.weather.services.round_phases import first_horizon_ahead, phases_drawn_on, phases_due
from leaguebot.weather.services.weather_config_service import (
    WINDOWS_KEPT,
    read_config_on,
    windows_kept,
    windows_of,
)


@dataclass(frozen=True)
class PhaseWindow:
    """What becomes of one forecast phase once its round is amended.

    *stands* is the answer to the one question: the phase would have run already under the
    round's new moment, so the forecast posted for it stays and none is drawn in its place.
    *fire_at* is the moment it falls at under the new arrangement.
    """

    label: str
    fire_at: datetime
    stands: bool

    @property
    def rearm(self) -> bool:
        """The complement of *stands*: the phase is still ahead, and is withdrawn and drawn
        again."""
        return not self.stands


@dataclass(frozen=True)
class ForecastJudgement:
    """The forecasts' judgement of an amendment: weather's *part*, and its *phases* by number
    (1, 2, 3), none where no horizons are configured."""

    part: AmendmentPart
    phases: dict[int, PhaseWindow] = field(default_factory=dict)


def judge_forecasts(
    changes: Mapping[str, Any],
    *,
    moment: datetime,
    now: datetime,
    windows: WeatherWindows | None,
    drawn: Mapping[int, bool],
    weather_on: bool = True,
) -> ForecastJudgement:
    """Weather's part of an amendment *changes* that leaves the round at *moment*, judged at *now*.

    For each phase it asks: would this have run already, were the round always to have stood at
    its new moment? Where it would, the forecast stands; where it would not, it is withdrawn and
    drawn again, and the part keeps those phases as ``withdrawn``. A forecast already posted was
    drawn for the round's circuit and sessions and cannot be unsaid, but only where it still
    stands once the amendment is in: a track change is refused for the first forecast, and a
    format change for the second. Judged on what was performed (*drawn*, by phase number) and not
    on the module's switch; with no *windows* nothing can be said of withdrawal, so *drawn* alone
    decides. The note names the phases withdrawn, only those drawn (*drawn*) and put back in
    question, and promises a redraw only while weather is on (*weather_on*); where some forecast
    is drawn and none is withdrawn it says the forecasts stand, and where none is drawn it says
    nothing of forecasts. A mystery round's notice is no forecast and is never named.

    Pure: nothing is read, written or posted.
    """
    phases: dict[int, PhaseWindow] = {}
    if windows is not None:
        moments = round_event_moments(moment, weather=windows, attendance=None)
        for number in (1, 2, 3):
            fire_at = moments[f"weather_p{number}"]
            phases[number] = PhaseWindow(
                label=f"Weather Phase {number}", fire_at=fire_at, stands=fire_at <= now
            )

    def still_stands(number: int) -> bool:
        outcome = phases.get(number)
        return outcome is None or outcome.stands

    refusals: list[str] = []
    if "track_name" in changes and drawn.get(1, False) and still_stands(1):
        refusals.append(
            "The first forecast for this round has already been posted, and it was drawn for "
            "its present circuit. Amend the round's moment as well to put the forecasts back "
            "in question."
        )
    if "format" in changes and drawn.get(2, False) and still_stands(2):
        refusals.append(
            "The second forecast for this round has already been posted, and it was drawn for "
            "its present sessions. Amend the round's moment as well to put the forecasts back "
            "in question."
        )
    withdrawn = sorted(number for number, outcome in phases.items() if outcome.rearm)
    notes: tuple[str, ...] = ()
    named = ", ".join(f"Phase {number}" for number in withdrawn if drawn.get(number, False))
    if named:
        notes = (
            f"This will withdraw and redraw {named} for this round."
            if weather_on
            else f"This will withdraw {named} for this round.",
        )
    elif any(drawn.get(number, False) for number in (1, 2, 3)):
        notes = ("The forecasts already posted for this round will stand.",)
    part = AmendmentPart(refusals=tuple(refusals), notes=notes, kept={"withdrawn": withdrawn})
    return ForecastJudgement(part=part, phases=phases)


async def amendment_judged(
    db: aiosqlite.Connection | None,
    rnd: Round,
    changes: Mapping[str, Any],
    *,
    moment: datetime,
    now: datetime,
    db_path: str | None = None,
    windows: WeatherWindows | None = None,
    weather_on: bool | None = None,
    **_context: Any,
) -> AmendmentPart:
    """Weather's part of judging an amendment of *rnd* that leaves it at *moment*, at *now*, by the
    league's own horizons and from the phases drawn. In a save both are read on *db*, the
    connection of the save that judges it again, which has written what it has not yet committed.
    Asked outside a save, the phases are read on a connection opened on *db_path*, and the
    horizons are *windows*, which `wiring` reads through the configuration's reader, as the
    settings read them. The horizons count whatever weather's switch, a forecast posted while
    weather was on being posted still. Whether weather is on is *weather_on*, as the module's
    switch reader answers it (`wiring`); where it is not handed, it is read on the connection
    used. The horizons are kept in the part, as ``weather_windows``, for the jobs that follow
    (`windows_kept`). Subscribed to core's `amendment_judged`."""
    if db is None:
        if db_path is None or windows is None:
            raise ValueError("no connection, or no database path and horizons, to judge on")
        async with get_connection(db_path) as own:
            return await _judged_on(own, rnd, changes, moment, now, windows, weather_on)
    return await _judged_on(
        db, rnd, changes, moment, now, windows_of(await read_config_on(db)), weather_on
    )


async def _judged_on(
    db: aiosqlite.Connection,
    rnd: Round,
    changes: Mapping[str, Any],
    moment: datetime,
    now: datetime,
    windows: WeatherWindows,
    weather_on: bool | None,
) -> AmendmentPart:
    """`amendment_judged` of the phases drawn as read on *db*, by the horizons *windows*."""
    on = await weather_enabled_on(db) if weather_on is None else weather_on
    drawn = await phases_drawn_on(db, [rnd.id])
    part = judge_forecasts(
        changes, moment=moment, now=now, windows=windows, drawn=drawn.get(rnd.id, {}),
        weather_on=on,
    ).part
    return dataclasses.replace(
        part, kept={**part.kept, WINDOWS_KEPT: dataclasses.asdict(windows)}
    )


async def _notice_stands_beside(db: aiosqlite.Connection, rnd: Round) -> bool:
    """Whether a mystery round's notice is posted for a round that is no mystery round now: it
    came down with the type, and the round's forecasts follow."""
    if rnd.format == RoundFormat.MYSTERY:
        return False
    cursor = await db.execute(
        "SELECT 1 FROM forecast_messages WHERE round_id = ? AND phase_number = 0", (rnd.id,)
    )
    return await cursor.fetchone() is not None


async def round_armed(
    db: aiosqlite.Connection,
    round_id: int,
    *,
    rnd: Round,
    kept: Mapping[str, Any],
    now: Callable[[], datetime],
    **_context: Any,
) -> RoundArming:
    """What weather owes the round armed again, read on *db* at the queue's clock *now*.

    The steps, in order: the deletion of a mystery round's notice where the round is none now,
    whatever weather's switch (it is no forecast, and no invalidation notice is posted for it), the
    deletion of each withdrawn forecast that was posted, the notice that
    the forecasts no longer stand, and the draw of each phase due at once. *kept* is what weather's
    part of the save kept (`phase_withdrawal.round_amended_on`) with the track, and is empty where
    the save was not made, with the horizons the judgement kept (`windows_kept`), read afresh where
    it kept none. The phases due are drawn by a job of the amendment's, so the forecasts'
    timers skip them: armed with no lateness limit, a timer would draw the phase a second time.

    The forecasts are armed only while weather is on, and not for a mystery round moved inside its
    first horizon, whose notice already told the drivers and which must not be fired
    retroactively. Subscribed to core's `round_armed`.
    """
    at = now()
    weather = windows_kept(kept) or windows_of(await read_config_on(db))
    on = await weather_enabled_on(db)
    draw: list[int] = []
    if on:
        drawn: Mapping[int, bool] = (await phases_drawn_on(db, [round_id])).get(round_id, {})
        draw = phases_due(rnd, weather, at, drawn=drawn)
    posted = kept.get("posted", [])
    planned = [PlannedStep(DELETE_FORECAST, {"phase": 0})] if await _notice_stands_beside(
        db, rnd
    ) else []
    planned += [PlannedStep(DELETE_FORECAST, {"phase": n}) for n in posted]
    if posted:
        planned.append(PlannedStep(NOTIFY_INVALIDATION, {"track": kept["track"]}))
    planned += [PlannedStep(RERUN_PHASE, {"phase": n}) for n in draw]
    armed = on and (rnd.format != RoundFormat.MYSTERY or first_horizon_ahead(rnd, weather, at))
    return RoundArming(
        steps=tuple(planned),
        jobs=ForecastJobs(windows=weather, skip_phases=frozenset(draw)) if armed else None,
    )
