"""Weather's own configuration, handed to the season's fingerprint.

Core's `fingerprint_areas_read` hook asks each module for the rows of its own configuration, on
the connection the fingerprint holds open, whatever the module's switch: a change to what the
module is set to is a change to what the review reported, off or on. Weather hands two areas: its
forecast channel for each division of the season, which is a column of its own on the divisions
row, and its pipeline's three deadlines. Reads only.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

from leaguebot.core.db.database import tuple_rows


async def fingerprint_areas(db: aiosqlite.Connection, season_id: int) -> dict[str, Any]:
    """Weather's areas of the fingerprint of *season_id*: its forecast channels, by division, and
    its pipeline's deadlines."""
    return {
        "channels": await tuple_rows(
            db,
            "SELECT id, forecast_channel_id FROM divisions WHERE season_id = ? ORDER BY id",
            season_id,
        ),
        "weather": await tuple_rows(
            db,
            "SELECT phase_1_days, phase_2_days, phase_3_hours FROM weather_pipeline_config",
        ),
    }
