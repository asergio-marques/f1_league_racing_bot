"""The channel a division's forecast is posted in.

`/weather channel` sets it. The column is the weather module's own, on core's
`divisions` table (architecture.md, "The database").
"""

from __future__ import annotations

from leaguebot.core.db.database import get_connection


async def set_division_forecast_channel(
    db_path: str, division_id: int, channel_id: int | None
) -> int | None:
    """Update divisions.forecast_channel_id. Returns the previous value."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT forecast_channel_id FROM divisions WHERE id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
        old_id: int | None = row[0] if row else None
        await db.execute(
            "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?",
            (channel_id, division_id),
        )
        await db.commit()
    return old_id
