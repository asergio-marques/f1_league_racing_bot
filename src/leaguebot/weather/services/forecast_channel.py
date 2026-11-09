"""The channel a division's forecast is posted in.

`/weather channel` sets it, through the weather setting (`weather_settings.forecast_channel_setting`)
that calls `set_division_forecast_channel` in its save. The column is the weather module's own, on
core's `divisions` table (architecture.md, "The database").
"""

from __future__ import annotations

import aiosqlite

from leaguebot.core.db.database import sole_row


async def set_division_forecast_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int
) -> int | None:
    """Set `divisions.forecast_channel_id` on *db*, never committing; returns the previous value.

    The save that calls it owns the transaction, so the column and the audit entry stand or fall
    together.
    """
    old_id: int | None = (
        await sole_row(
            await db.execute(
                "SELECT forecast_channel_id FROM divisions WHERE id = ?", (division_id,)
            )
        )
    )["forecast_channel_id"]
    await db.execute(
        "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?", (channel_id, division_id)
    )
    return old_id
