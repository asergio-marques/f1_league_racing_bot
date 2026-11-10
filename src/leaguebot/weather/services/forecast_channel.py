"""The channel a division's forecast is posted in.

`/weather channel` sets it, through the weather setting (`weather_settings.forecast_channel_setting`)
that calls `set_division_forecast_channel` in its save. The column is the weather module's own, on
core's `divisions` table (architecture.md, "The database"). A season's confirmation asks the
module for the channel's faults through core's `division_channel_faults_read` hook
(`division_channel_faults`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection, sole_row, tuple_rows_in
from leaguebot.core.services.approval_checks import channel_fault

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService


async def set_division_forecast_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int
) -> int | None:
    """Set `divisions.forecast_channel_id` on *db*, never committing; returns the previous value.

    The save that calls it owns the transaction, so the column and the audit entry stand or fall
    together. A value already held is left unwritten, and the caller told so by the value it gets
    back.
    """
    old_id: int | None = (
        await sole_row(
            await db.execute(
                "SELECT forecast_channel_id FROM divisions WHERE id = ?", (division_id,)
            )
        )
    )["forecast_channel_id"]
    if old_id != channel_id:
        await db.execute(
            "UPDATE divisions SET forecast_channel_id = ? WHERE id = ?", (channel_id, division_id)
        )
    return old_id


async def forecast_channels_on(
    db: aiosqlite.Connection, division_ids: Sequence[int]
) -> dict[int, int | None]:
    """The forecast channel of each division in *division_ids*, by division id, `None` for one
    with no channel set; a division that does not exist is left out.

    Reads on the connection it is handed. Reads only.
    """
    rows = await tuple_rows_in(
        db, "SELECT id, forecast_channel_id FROM divisions WHERE id IN ({marks})", division_ids
    )
    return {int(row[0]): row[1] for row in rows}


async def division_lines(
    db_path: str, *, season_id: int, division_ids: Sequence[int]
) -> dict[int, list[str]]:
    """The weather channel line the placements review shows for each division in *division_ids*:
    the channel named, or shown as none where none is set, as the review always showed it. Asked
    no switch: the review shows it whether weather is on or off.

    Subscribed to core's `division_lines_read`, which hands the season (as it does every
    subscriber; the divisions named are what is read) and its divisions. Reads only.
    """
    async with get_connection(db_path) as db:
        channels = await forecast_channels_on(db, division_ids)
    return {
        division_id: [
            "  Weather channel: "
            + (f"<#{channels[division_id]}>" if channels.get(division_id) else "*(none)*")
        ]
        for division_id in division_ids
    }


async def division_channel_faults(
    modules: "ModuleService",
    db_path: str,
    *,
    divisions: Mapping[int, str],
    guild: Any = None,
) -> dict[int, list[str]]:
    """The forecast channel of each division in *divisions* (id to name) that is not set, or not
    on the *guild*, by division id; nothing where the weather module is off.

    Subscribed to core's `division_channel_faults_read`, which hands the divisions the
    confirmation judges and the server. A division with no fault is given an empty list. With no
    *guild*, only whether the channel is set is judged. Reads only.
    """
    if not await modules.is_weather_enabled():
        return {}
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id, forecast_channel_id FROM divisions")
        channels = {row["id"]: row["forecast_channel_id"] for row in await cursor.fetchall()}
    found: dict[int, list[str]] = {}
    for division_id, name in divisions.items():
        fault = await channel_fault(
            guild, name, channels.get(division_id), "weather channel", "/weather channel"
        )
        found[division_id] = [fault] if fault else []
    return found
