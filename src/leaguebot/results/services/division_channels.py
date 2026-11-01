"""The channels a division's results, standings and penalty verdicts are posted in.

`/results channel results`, `standings` and `verdicts` set them, each in its own column of
`division_results_config`, which the results module owns. Three separate upserts naming one
column each, so that setting one never blanks its neighbour.
"""

from __future__ import annotations

from leaguebot.core.db.database import get_connection


async def set_division_results_channel(
    db_path: str, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.results_channel_id. Returns the previous value."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
        old_id: int | None = row[0] if row else None
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET results_channel_id = excluded.results_channel_id",
            (division_id, channel_id),
        )
        await db.commit()
    return old_id


async def set_division_standings_channel(
    db_path: str, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.standings_channel_id. Returns the previous value."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
        old_id: int | None = row[0] if row else None
        await db.execute(
            "INSERT INTO division_results_config (division_id, standings_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET standings_channel_id = excluded.standings_channel_id",
            (division_id, channel_id),
        )
        await db.commit()
    return old_id


async def set_division_penalty_channel(
    db_path: str, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.penalty_channel_id. Returns the previous value."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT penalty_channel_id FROM division_results_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()
        old_id: int | None = row[0] if row else None
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET penalty_channel_id = excluded.penalty_channel_id",
            (division_id, channel_id),
        )
        await db.commit()
    return old_id
