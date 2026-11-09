"""The channels a division's results, standings and penalty verdicts are posted in.

`/results channel results`, `standings` and `verdicts` set them, each in its own column of
`division_results_config`, which the results module owns (`results/services/channel_settings.py`
holds the settings that call these). Three separate upserts naming one column each, so that setting
one never blanks its neighbour.

**Each writes on the connection it is handed and never commits**: the setting's save owns the
transaction, so the channel and its audit entry stand or fall together. Each gives back the channel
it replaced, and **writes nothing where the channel is already the one held**, so that a save can
tell that nothing changed. `penalty_channel_id` is a TEXT column, the other two INTEGER; each gives
its previous value back as an integer.
"""

from __future__ import annotations

import aiosqlite


async def set_division_results_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.results_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT results_channel_id FROM division_results_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO division_results_config (division_id, results_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET results_channel_id = excluded.results_channel_id",
            (division_id, channel_id),
        )
    return old_id


async def set_division_standings_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.standings_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT standings_channel_id FROM division_results_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO division_results_config (division_id, standings_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET standings_channel_id = excluded.standings_channel_id",
            (division_id, channel_id),
        )
    return old_id


async def set_division_penalty_channel(
    db: aiosqlite.Connection, division_id: int, channel_id: int | None
) -> int | None:
    """Upsert division_results_config.penalty_channel_id. Returns the previous value."""
    cursor = await db.execute(
        "SELECT penalty_channel_id FROM division_results_config WHERE division_id = ?",
        (division_id,),
    )
    row = await cursor.fetchone()
    old_id = None if row is None or row[0] is None else int(row[0])
    if old_id != channel_id:
        await db.execute(
            "INSERT INTO division_results_config (division_id, penalty_channel_id) "
            "VALUES (?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET penalty_channel_id = excluded.penalty_channel_id",
            (division_id, channel_id),
        )
    return old_id
