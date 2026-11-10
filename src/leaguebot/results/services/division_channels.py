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

The module hands core its channels' faults, too, through core's `division_channel_faults_read`
hook (`division_channel_faults`), asking the module's switch of itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.approval_checks import channel_fault

if TYPE_CHECKING:
    from leaguebot.core.services.module_service import ModuleService

#: Results' three channels, in the order a division's lines read: the column that holds it, what a
#: league calls it, the command that sets it, and what the review shows where it is not set.
_CHANNELS = (
    ("results_channel_id", "results channel", "/results channel results", "*(none)*"),
    ("standings_channel_id", "standings channel", "/results channel standings", "*(none)*"),
    ("penalty_channel_id", "verdicts channel", "/results channel verdicts", "*(not configured)*"),
)


async def _channels_of(db_path: str) -> dict[int, Any]:
    """Each division's row of results' channel configuration, by division id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id, results_channel_id, standings_channel_id, penalty_channel_id "
            "FROM division_results_config"
        )
        return {row["division_id"]: row for row in await cursor.fetchall()}


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


async def division_channel_faults(
    modules: "ModuleService",
    db_path: str,
    *,
    divisions: Mapping[int, str],
    guild: Any = None,
) -> dict[int, list[str]]:
    """Each division's results, standings and verdicts channels, in that order, that are not set
    or not on the *guild*, by division id; nothing where the results module is off.

    Subscribed to core's `division_channel_faults_read`, which hands the divisions the
    confirmation judges (id to name) and the server. A division with no fault is given an empty
    list. With no *guild*, only whether each channel is set is judged. Reads only.
    """
    if not await modules.is_results_enabled():
        return {}
    channels = await _channels_of(db_path)
    found: dict[int, list[str]] = {}
    for division_id, name in divisions.items():
        row = channels.get(division_id)
        lines: list[str] = []
        for column, label, command, _unset in _CHANNELS:
            fault = await channel_fault(
                guild, name, row[column] if row is not None else None, label, command
            )
            if fault:
                lines.append(fault)
        found[division_id] = lines
    return found
