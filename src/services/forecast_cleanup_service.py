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

    **No test-mode guard.** This once skipped the deletion while test mode was active and
    retained the row for ``flush_pending_deletions``; that was removed at an earlier
    increment and deletion now behaves identically in test mode and live. The docstring said
    otherwise until 2026-08-14.

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


# ---------------------------------------------------------------------------
# 042 — the one send site every weather posting takes
# ---------------------------------------------------------------------------

async def post_phase_message(
    bot: "Bot",
    *,
    round_id: int,
    division_id: int,
    server_id: int,
    channel_id: int,
    phase_number: int,
    text: str,
    attachment=None,
    attachment_text: str = "",
    supersedes: int | None = None,
) -> "discord.Message | None":
    """Post one weather occasion, and only then destroy the one it supersedes.

    The single send site for all four of the module's weather postings — the three phases and
    the notice of a mystery round — and for both manners in which each may be drawn. Weather
    is the module's only aspect with four occasions, so a per-occasion implementation of this
    ordering would be four chances to get it wrong.

    **Produce before destroy** (Constitution XIV.8). *supersedes* names the phase whose message
    this one replaces, and it is deleted only once this message exists. A failure here leaves
    the previous forecast standing, which is the whole point: the fallback path is the one
    reached because something has already gone wrong.

    **The manner of a message is no part of the chain** (XIV.8, v4.7.0). *supersedes* names a
    phase, not a drawing: a message posted as text is deleted by an occasion posted as a
    graphic and the reverse, each occasion reading only which message stands.

    **A transport failure retries as text** (XIV.8, FR-057). *text* is the textual forecast and
    is what is enqueued, never the rendered image — a queue is durable and outlives the state
    that filled it, so a picture retried an hour from now is a picture of a round that has
    moved on.

    *attachment* is a ``discord.File`` where the league draws a graphic; *attachment_text* is
    the message it rides on, which for a forecast is the division role mention and nothing
    besides, and for a mystery notice is empty.
    """
    db_path: str = bot.db_path  # type: ignore[attr-defined]

    if attachment is None:
        # The textual path, unchanged: the router chunks it and owns its own retry.
        class _Div:
            forecast_channel_id = channel_id

        msg = await bot.output_router.post_forecast(  # type: ignore[attr-defined]
            _Div(), text, server_id=server_id
        )
    else:
        # One `finally` around the whole graphic branch. Two of its three exits abandon the
        # picture *before* the send — an unfetchable channel and one that is not a text
        # channel — and those are the paths a per-send cleanup would miss.
        from services.image_render_service import discard_attachment

        try:
            channel = bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await bot.fetch_channel(channel_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException) as exc:
                    log.error(
                        "post_phase_message: cannot fetch forecast channel id=%s: %s",
                        channel_id, exc,
                    )
                    return None
            if not isinstance(channel, discord.TextChannel):
                log.error("post_phase_message: channel id=%s is not a TextChannel", channel_id)
                return None

            try:
                msg = await channel.send(
                    attachment_text,
                    file=attachment,
                    allowed_mentions=discord.AllowedMentions.all(),
                )
            except discord.HTTPException as exc:
                log.warning(
                    "post_phase_message: failed to post phase %s for division %s: %s",
                    phase_number, division_id, exc,
                )
                try:
                    from services import retry_service

                    await retry_service.enqueue(
                        db_path,
                        server_id=server_id,
                        channel_id=channel_id,
                        content=text,
                        failure_reason=(
                            f"weather phase {phase_number} for division {division_id}: {exc}"
                        ),
                    )
                except Exception:  # noqa: BLE001 — the queue must never mask the original failure
                    log.exception("post_phase_message: could not enqueue the textual forecast")
                return None
        finally:
            discard_attachment(attachment)

    if msg is None:
        # Nothing was posted, so nothing may be destroyed.
        return None

    await store_forecast_message(round_id, division_id, phase_number, msg, db_path)
    if supersedes is not None:
        await delete_forecast_message(round_id, division_id, supersedes, bot)
    return msg
