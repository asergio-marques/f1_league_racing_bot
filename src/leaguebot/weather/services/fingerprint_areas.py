"""Weather's own configuration, handed to the season's fingerprint.

Core's `fingerprint_areas_read` hook asks each module for the rows of its own configuration, on
the connection the fingerprint holds open, whatever the module's switch: a change to what the
module is set to is a change to what the review reported, off or on. Weather hands two areas: its
forecast channel for each division of the season, a column of its own on the divisions row, which
it has the season service read the divisions for, and its pipeline's three deadlines. Reads only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import tuple_rows

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService


async def fingerprint_areas(
    seasons: "SeasonService", db: aiosqlite.Connection, season_id: int
) -> dict[str, Any]:
    """Weather's areas of the fingerprint of *season_id*: its forecast channels, by division, as
    the season service reads the divisions, and its pipeline's deadlines."""
    divisions = sorted(await seasons.get_divisions_on(db, season_id), key=lambda d: d.id)
    return {
        "channels": [(division.id, division.forecast_channel_id) for division in divisions],
        "weather": await tuple_rows(
            db,
            "SELECT phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config",
        ),
    }
