"""ForecastCleanupService — track and delete per-phase forecast messages.

Feature 007: Forecast Message Cleanup.

Constitution Principle VII: Every channel write goes through OutputRouter.
Deletes are the inverse of writes; they use the same channel references stored
at write time.

Public API:
  store_forecast_message  — persist a message ID after posting
  delete_forecast_message — delete a stored message (with test-mode guard)
  run_post_race_cleanup   — delete Phase 3 message 24 h after round start
  flush_pending_deletions — on test-mode disable, delete all stored messages
                            for a server at once
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from db.database import get_connection

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# T003 — store_forecast_message
# ---------------------------------------------------------------------------

async def store_forecast_message(
    round_id: int,
    division_id: int,
    phase_number: int,
    message: "discord.Message",
    db_path: str,
) -> None:
    """Persist the Discord message snowflake for *phase_number* of *round_id*.

    Uses ``INSERT OR REPLACE`` so re-running a phase (e.g. after an amendment)
    transparently updates the stored ID without leaving stale rows.
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO forecast_messages
                (round_id, division_id, phase_number, message_id, posted_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (round_id, division_id, phase_number, message.id, now),
        )
        await db.commit()
    log.debug(
        "Stored forecast message: round=%s div=%s phase=%s msg=%s",
        round_id, division_id, phase_number, message.id,
    )


# ---------------------------------------------------------------------------
# T004 + T012 — delete_forecast_message (with test-mode guard)
# ---------------------------------------------------------------------------

async def delete_forecast_message(
    round_id: int,
    division_id: int,
    phase_number: int,
    bot: "Bot",
) -> None:
    """Delete the stored Discord message for *phase_number* of *round_id*.

    Test-mode guard (FR-012): if test mode is active for the server that owns
    this round, the deletion is silently skipped.  The row is retained so that
    ``flush_pending_deletions`` can action it later when test mode is disabled.

    On Discord API failure (NotFound / Forbidden / HTTPException) the DB row is
    still removed so a stale reference does not block future clean-ups.
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    # --- Load stored message row ---
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT fm.message_id, d.forecast_channel_id, s.server_id
            FROM forecast_messages fm
            JOIN divisions d ON d.id = fm.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE fm.round_id = ? AND fm.division_id = ? AND fm.phase_number = ?
            """,
            (round_id, division_id, phase_number),
        )
        row = await cursor.fetchone()

    if row is None:
        log.debug(
            "delete_forecast_message: no row for round=%s div=%s phase=%s — nothing to delete",
            round_id, division_id, phase_number,
        )
        return

    server_id: int = row["server_id"]
    message_id: int = row["message_id"]
    channel_id: int = row["forecast_channel_id"]

    # --- Test-mode guard (T012 / FR-012) ---
    config = await bot.config_service.get_server_config(server_id)  # type: ignore[attr-defined]
    if config is not None and config.test_mode_active:
        log.debug(
            "delete_forecast_message: test mode active for server=%s — skipping deletion "
            "(round=%s div=%s phase=%s)",
            server_id, round_id, division_id, phase_number,
        )
        return

    # --- Attempt Discord deletion ---
    _delete_ok = await _discord_delete(bot, channel_id, message_id)

    # --- Remove DB row (even on Discord error to avoid stale references) ---
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM forecast_messages "
            "WHERE round_id = ? AND division_id = ? AND phase_number = ?",
            (round_id, division_id, phase_number),
        )
        await db.commit()

    if _delete_ok:
        log.info(
            "Deleted forecast message: round=%s div=%s phase=%s msg=%s",
            round_id, division_id, phase_number, message_id,
        )
    else:
        log.warning(
            "forecast message Discord delete failed but DB row removed: "
            "round=%s div=%s phase=%s msg=%s",
            round_id, division_id, phase_number, message_id,
        )


# ---------------------------------------------------------------------------
# T010 — run_post_race_cleanup (Phase 3 message, 24 h after round start)
# ---------------------------------------------------------------------------

async def run_post_race_cleanup(round_id: int, bot: "Bot") -> None:
    """Delete the Phase 3 forecast message for all divisions of *round_id*.

    Invoked 24 hours after round start by the APScheduler ``cleanup_r{round_id}``
    job registered in SchedulerService.

    Each division is processed independently so a single failure does not block
    the others.
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id FROM forecast_messages WHERE round_id = ? AND phase_number = 3",
            (round_id,),
        )
        rows = await cursor.fetchall()

    for row in rows:
        await delete_forecast_message(round_id, row["division_id"], phase_number=3, bot=bot)


# ---------------------------------------------------------------------------
# T013 — flush_pending_deletions (test-mode disable hook)
# ---------------------------------------------------------------------------

async def flush_pending_deletions(server_id: int, bot: "Bot") -> None:
    """Delete all pending forecast messages for *server_id*.

    Called when test mode is disabled (FR-015).  By the time this function
    runs, test mode has already been persisted as ``False`` in the DB, so the
    test-mode guard inside ``delete_forecast_message`` will not fire.

    All stored messages for the server are iterated and deleted.  Individual
    Discord API failures are logged but do not halt the batch.
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT fm.round_id, fm.division_id, fm.phase_number
            FROM forecast_messages fm
            JOIN divisions d ON d.id = fm.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE s.server_id = ?
            ORDER BY fm.round_id, fm.division_id, fm.phase_number
            """,
            (server_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        log.debug("flush_pending_deletions: no pending messages for server=%s", server_id)
        return

    log.info(
        "flush_pending_deletions: flushing %d message(s) for server=%s",
        len(rows), server_id,
    )
    for row in rows:
        await delete_forecast_message(
            row["round_id"],
            row["division_id"],
            row["phase_number"],
            bot,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _discord_delete(bot: "Bot", channel_id: int, message_id: int) -> bool:
    """Attempt to delete a Discord message.  Returns True on success."""
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            log.error(
                "_discord_delete: channel id=%s is not a TextChannel", channel_id
            )
            return False
        await channel.get_partial_message(message_id).delete()
        return True
    except discord.NotFound:
        log.debug(
            "_discord_delete: message %s in channel %s already deleted or not found",
            message_id, channel_id,
        )
        return False
    except discord.Forbidden as exc:
        log.error(
            "_discord_delete: missing permissions to delete message %s in channel %s: %s",
            message_id, channel_id, exc,
        )
        return False
    except discord.HTTPException as exc:
        log.error(
            "_discord_delete: HTTP error deleting message %s in channel %s: %s",
            message_id, channel_id, exc,
        )
        return False
