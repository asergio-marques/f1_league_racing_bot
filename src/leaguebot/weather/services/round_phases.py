"""Which forecasts a round has had drawn: weather's own record of it.

`rounds.phase1_done` to `phase3_done` and `rounds.mystery_notice_given` are the weather module's
own columns on core's `rounds` table (architecture.md, "Each table is read and written by its
owner alone"). Core does not carry them on its `Round`: where it must judge by them, as an
amendment does of the round it changes, it asks the weather module through its `phases_drawn_read`
hook, and this is the reader the module answers with. It is the one place the module reads what
has been drawn: a phase's flag says a draw of that phase stands, and a mystery round's notice,
which is no forecast and no draw, is recorded apart in its own flag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

import aiosqlite

from leaguebot.framework.db.database import get_connection, tuple_rows_in
from leaguebot.core.models.round import Round, RoundFormat
from leaguebot.weather.models.weather_config import WeatherWindows
from leaguebot.weather.services.forecast_leads import forecast_moments


class PhasesDrawn(dict[int, bool]):
    """Whether each of Phases 1 to 3 has been drawn, by phase number, with whether the round's
    mystery notice has been given beside it (`notice_given`): the notice is no phase, and a round
    with its notice given has had no forecast drawn for that."""

    def __init__(self, phases: Mapping[int, bool], *, notice_given: bool = False) -> None:
        super().__init__(phases)
        self.notice_given = notice_given

    def first_done(self, *, mystery: bool) -> bool:
        """Whether the round's first forecast is done: its notice where it is a mystery round,
        Phase 1 otherwise."""
        return self.notice_given if mystery else self[1]


async def phases_drawn_on(
    db: aiosqlite.Connection, round_ids: Sequence[int]
) -> dict[int, PhasesDrawn]:
    """Whether each of Phases 1 to 3 has been drawn, by phase number, and whether the mystery
    notice has been given, for each round in *round_ids*, by round id; a round that does not
    exist is left out.

    Reads on the connection it is handed, so that an amendment's save sees what that save has
    written and not yet committed. Reads only.
    """
    rows = await tuple_rows_in(
        db,
        "SELECT id, phase1_done, phase2_done, phase3_done, mystery_notice_given "
        "FROM rounds WHERE id IN ({marks})",
        round_ids,
    )
    return {
        int(row[0]): PhasesDrawn(
            {1: bool(row[1]), 2: bool(row[2]), 3: bool(row[3])}, notice_given=bool(row[4])
        )
        for row in rows
    }


async def phase2_slot_types_on(db: aiosqlite.Connection, round_id: int) -> dict[int, str | None]:
    """The slot type Phase 2 drew for each session of *round_id*, by session id, `None` for a
    session it has drawn none for.

    ``sessions.phase2_slot_type`` is the weather module's own column on core's `sessions` table,
    which Phase 3 draws from; the session itself, its id and its type, it asks core for
    (`sessions_of_round_on`). Reads on the connection it is handed. Reads only.
    """
    cursor = await db.execute("SELECT id, phase2_slot_type FROM sessions WHERE round_id = ?", (round_id,))
    return {int(row["id"]): row["phase2_slot_type"] for row in await cursor.fetchall()}


async def phases_drawn(db_path: str, round_ids: Sequence[int]) -> dict[int, dict[int, bool]]:
    """`phases_drawn_on`, as core's `phases_drawn_read` hook asks it: on a connection of its own
    opened on *db_path*. Whatever the module's switch: a forecast posted while weather was on is
    still posted. Reads only."""
    async with get_connection(db_path) as own:
        return {
            round_id: dict(drawn) for round_id, drawn in (await phases_drawn_on(own, round_ids)).items()
        }


def phases_due(
    rnd: Round, weather: WeatherWindows, at: datetime, *, drawn: Mapping[int, bool]
) -> list[int]:
    """The forecast phases to draw at once at *at*: each not yet drawn (*drawn*, by phase number,
    as `phases_drawn_on` gives it for the round) whose horizon has passed, for a round whose
    moment is still ahead and which is no mystery round."""
    moment = rnd.scheduled_at
    if rnd.format == RoundFormat.MYSTERY or moment <= at:
        return []
    moments = forecast_moments(moment, weather)
    horizons = {n: moments[f"weather_p{n}"] for n in (1, 2, 3)}
    return [n for n, horizon in horizons.items() if not drawn.get(n, False) and horizon <= at]


def first_horizon_ahead(rnd: Round, weather: WeatherWindows, at: datetime) -> bool:
    """Whether the round as it stands has its first forecast horizon still to come at *at*: a
    mystery round moved inside it has had its notice, which must not be fired retroactively."""
    moments = forecast_moments(rnd.scheduled_at, weather)
    return at < moments["weather_p1"]
