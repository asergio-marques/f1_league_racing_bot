"""The guards a weather draw holds to before it writes, read on the connection it is handed.

A phase computes, records *and* posts, and the core specification's rule — a disabled module
produces nothing, "whatever the path arrives at it" — can only hold for every route in if the
draw itself refuses (issue #113). Nor does a draw do anything for a round that is cancelled, or
whose division is: the cancellation removes the round's timed work before it records the round
cancelled, but a phase that fell due in between, or one fired late, can still arrive here. The
round is read again, on the connection the draw writes on, and acted on only while it is still
due (architecture.md, "The database says when something is due"); the skip is written to the
host's log alone, the league having been told of the cancellation itself.

A module of its own because the guards are shared: each of the four draws (`draw_phase1_on`,
`draw_phase2_on`, `draw_phase3_on` and the mystery notice's `record_mystery_notice_on`) holds to
`round_if_due`, and the forecast change's check reads the module's switch through
`weather_enabled_on`, so that the draw and the queue read the switch the same way. It reads core's
`server_configs` and writes nothing.

The round is read once, here (`weather_round_on`): core's half through its readers (the round, its
division and its season) beside weather's own (the phases drawn, the division's forecast channel),
so that no service of the module reads core's tables of its own. What it judges of the round,
whether the work is still due, is judged once as well (`lapse`), for the draw's guard and for the
forecast change's check and its post alike.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

import aiosqlite

from leaguebot.core.models.division import Division, DivisionStatus
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.seasons.season_service import RoundFacts, division_on, round_facts_on
from leaguebot.weather.services.forecast_channel import forecast_channels_on
from leaguebot.weather.services.round_phases import phases_drawn_on

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeatherRound:
    """A round as weather judges it: core's facts and division beside the module's own record.

    *drawn* is whether each of Phases 1 to 3 has been drawn, by phase number, and
    *forecast_channel_id* the division's forecast channel, `None` for none set.
    """

    facts: RoundFacts
    division: Division
    drawn: dict[int, bool]
    forecast_channel_id: int | None


async def weather_round_on(db: aiosqlite.Connection, round_id: int) -> WeatherRound | None:
    """*round_id* as weather judges it, or None where the round is no longer there, read on *db*.

    The single weather reader of a round: it asks core for the round and its division, and weather's
    own readers for the phases drawn and the forecast channel. Reads only.
    """
    facts = await round_facts_on(db, round_id)
    if facts is None:
        return None
    division = await division_on(db, facts.division_id)
    if division is None:
        return None
    drawn = (await phases_drawn_on(db, [round_id]))[round_id]
    channel = (await forecast_channels_on(db, [facts.division_id]))[facts.division_id]
    return WeatherRound(facts, division, drawn, channel)


class Lapse(Enum):
    """Why a round is no longer owed weather's work: gone, called off (it or its division), or the
    module switched off."""

    GONE = "the round is gone"
    CANCELLED = "the round or its division is cancelled"
    OFF = "the weather module is off"


def lapse(rnd: WeatherRound | None, weather_on: bool) -> Lapse | None:
    """Why the work for *rnd* is no longer due, else None: the judgement a draw's guard
    (`round_if_due`) and the forecast change (`forecast_changes`) share, in the order they judge it.

    The race time is no part of it: the change judges that as it starts and again as it posts, and
    the draw does not.
    """
    if rnd is None:
        return Lapse.GONE
    if (
        rnd.facts.status == RoundStatus.CANCELLED
        or rnd.facts.division_status == DivisionStatus.CANCELLED
    ):
        return Lapse.CANCELLED
    if not weather_on:
        return Lapse.OFF
    return None


async def weather_enabled_on(db: aiosqlite.Connection) -> bool:
    """Whether the weather module is on, read on *db*: the module's switch reader, which the builder
    signs up with `ModuleService` and the module's own work asks. A league that has never
    switched it on has no row, and reads as off."""
    cursor = await db.execute("SELECT weather_module_enabled FROM server_configs")
    row = await cursor.fetchone()
    return row is not None and bool(row["weather_module_enabled"])


async def round_if_due(
    db: aiosqlite.Connection, round_id: int, *, label: str, phase: int | None
) -> WeatherRound | None:
    """The round where a draw may go on, else None, the reason written to the host's log.

    Not due: the round is not found, it or its division is cancelled, the weather module is off,
    or *phase* (``None`` for none to check) is already drawn.
    """
    rnd = await weather_round_on(db, round_id)
    why = lapse(rnd, await weather_enabled_on(db))
    if rnd is None:
        log.error("%s: round_id=%s not found", label, round_id)
        return None
    if why is Lapse.CANCELLED:
        log.info("%s: round %s or its division is cancelled — round left untouched.", label, round_id)
        return None
    if why is Lapse.OFF:
        log.info("%s: weather module disabled — round %s left untouched.", label, round_id)
        return None
    if phase is not None and rnd.drawn[phase]:
        log.info("%s already done for round %s — skipping.", label, round_id)
        return None
    return rnd
