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
"""

from __future__ import annotations

import logging

import aiosqlite

from leaguebot.core.models.round import RoundStatus

log = logging.getLogger(__name__)

_ROUND_COLUMNS = (
    "SELECT r.id, r.track_name, r.format, r.phase1_done, r.phase2_done, r.phase3_done, "
    "       r.division_id, r.status AS round_status, d.status AS division_status, "
    "       d.forecast_channel_id, d.mention_role_id "
    "FROM rounds r "
    "JOIN divisions d ON d.id = r.division_id "
    "JOIN seasons s ON s.id = d.season_id "
    "WHERE r.id = ?"
)


async def weather_enabled_on(db: aiosqlite.Connection) -> bool:
    """Whether the weather module is on, as `ModuleService.is_weather_enabled` reads it."""
    cursor = await db.execute("SELECT weather_module_enabled FROM server_configs")
    row = await cursor.fetchone()
    return row is not None and bool(row["weather_module_enabled"])


async def round_if_due(
    db: aiosqlite.Connection, round_id: int, *, label: str, done_column: str | None
) -> aiosqlite.Row | None:
    """The round's row where a draw may go on, else None, the reason written to the host's log.

    Not due: the round is not found, it or its division is cancelled, the weather module is off,
    or the phase's *done_column* is already set (``None`` for none to check).
    """
    cursor = await db.execute(_ROUND_COLUMNS, (round_id,))
    row = await cursor.fetchone()
    if row is None:
        log.error("%s: round_id=%s not found", label, round_id)
        return None
    if (
        row["round_status"] == RoundStatus.CANCELLED.value
        or row["division_status"] == "CANCELLED"
    ):
        log.info("%s: round %s or its division is cancelled — round left untouched.", label, round_id)
        return None
    if not await weather_enabled_on(db):
        log.info("%s: weather module disabled — round %s left untouched.", label, round_id)
        return None
    if done_column is not None and row[done_column]:
        log.info("%s already done for round %s — skipping.", label, round_id)
        return None
    return row
