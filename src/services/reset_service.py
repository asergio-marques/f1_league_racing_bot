"""reset_service — purge all (or all + config) data for a Discord server.

Caller is responsible for validating the ``confirm`` string before invoking
``reset_server_data``.  This module handles:

1. Collecting intermediate FK-chain IDs (seasons → divisions → rounds).
2. Cancelling any live APScheduler jobs for those rounds.
3. Executing all DELETEs in FK-safe order inside a single transaction.
4. Returning row-count summaries for the caller to display.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.database import get_connection

if TYPE_CHECKING:
    from services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)


async def reset_server_data(
    server_id: int,
    db_path: str,
    scheduler_service: "SchedulerService",
    *,
    full: bool = False,
) -> dict[str, int]:
    """Delete season data (and optionally server config) for *server_id*.

    Parameters
    ----------
    server_id:
        Discord guild ID whose data is to be deleted.
    db_path:
        Path to the SQLite database file.
    scheduler_service:
        The bot's ``SchedulerService`` instance; used to cancel pending jobs
        *before* rows are deleted.
    full:
        When ``True``, also deletes the ``server_configs`` row, effectively
        factory-resetting the bot for this server.

    Returns
    -------
    dict with keys ``seasons_deleted``, ``divisions_deleted``, ``rounds_deleted``
    (all ``int``).
    """
    async with get_connection(db_path) as db:
        # ── 1.  Collect IDs up-front (read-only; outside the write transaction) ──

        cursor = await db.execute(
            "SELECT id FROM seasons WHERE server_id = ?",
            (server_id,),
        )
        season_ids: list[int] = [row[0] for row in await cursor.fetchall()]

        division_ids: list[int] = []
        round_ids: list[int] = []

        if season_ids:
            ph = _ph(season_ids)
            cursor = await db.execute(
                f"SELECT id FROM divisions WHERE season_id IN ({ph})",
                season_ids,
            )
            division_ids = [row[0] for row in await cursor.fetchall()]

        if division_ids:
            ph = _ph(division_ids)
            cursor = await db.execute(
                f"SELECT id FROM rounds WHERE division_id IN ({ph})",
                division_ids,
            )
            round_ids = [row[0] for row in await cursor.fetchall()]

        # ── 2.  Cancel APScheduler jobs BEFORE touching the DB ──
        for rid in round_ids:
            scheduler_service.cancel_round(rid)

        # ── 3.  FK-safe deletes inside a single transaction ──
        #        Order: leaf → root
        #        sessions / phase_results → rounds → divisions → seasons
        #                                → audit_entries → [server_configs]

        if round_ids:
            ph = _ph(round_ids)
            await db.execute(
                f"DELETE FROM sessions WHERE round_id IN ({ph})",
                round_ids,
            )
            await db.execute(
                f"DELETE FROM phase_results WHERE round_id IN ({ph})",
                round_ids,
            )
            await db.execute(
                f"DELETE FROM forecast_messages WHERE round_id IN ({ph})",
                round_ids,
            )

        if division_ids:
            ph = _ph(division_ids)
            await db.execute(
                f"DELETE FROM rounds WHERE division_id IN ({ph})",
                division_ids,
            )

        if season_ids:
            ph = _ph(season_ids)
            await db.execute(
                f"DELETE FROM divisions WHERE season_id IN ({ph})",
                season_ids,
            )

        seasons_cur = await db.execute(
            "DELETE FROM seasons WHERE server_id = ?",
            (server_id,),
        )
        seasons_deleted: int = seasons_cur.rowcount  # type: ignore[assignment]

        await db.execute(
            "DELETE FROM audit_entries WHERE server_id = ?",
            (server_id,),
        )

        if full:
            await db.execute(
                "DELETE FROM server_configs WHERE server_id = ?",
                (server_id,),
            )

        await db.commit()

    log.info(
        "Reset server %s: %d season(s), %d division(s), %d round(s) deleted "
        "(full=%s)",
        server_id,
        seasons_deleted,
        len(division_ids),
        len(round_ids),
        full,
    )

    return {
        "seasons_deleted": seasons_deleted,
        "divisions_deleted": len(division_ids),
        "rounds_deleted": len(round_ids),
    }


# ── helpers ───────────────────────────────────────────────────────────────────

def _ph(values: list) -> str:
    """Return a SQL ``?``-placeholder string for *values*, e.g. ``?,?,?``."""
    return ",".join("?" * len(values))
