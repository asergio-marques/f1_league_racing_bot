"""RetryService — persistent message retry queue.

When OutputRouter fails to post a message to a Discord channel, it calls
``enqueue`` to persist the attempt.  ``RetryCog`` runs ``attempt_delivery`` on
a 5-minute loop until the message is delivered.

Constitution Principle V: successful and stuck-entry retry outcomes are posted
to the calculation log channel for full observability.

FR-009: ``attempt_delivery`` NEVER calls ``enqueue`` on its own delivery
failures — the entry's ``retry_count`` is incremented instead.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.database import get_connection
from models.pending_message import PendingMessage

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)

RETRY_WARN_THRESHOLD: int = 12  # ~1 hour at 5-min intervals


# ---------------------------------------------------------------------------
# T003 — enqueue
# ---------------------------------------------------------------------------

async def enqueue(
    db_path: str,
    server_id: int,
    channel_id: int,
    content: str,
    failure_reason: str,
) -> None:
    """Persist a failed channel message for later retry."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO pending_messages
                (server_id, channel_id, content, failure_reason, enqueued_at,
                 retry_count, last_attempted_at)
            VALUES (?, ?, ?, ?, ?, 0, NULL)
            """,
            (server_id, channel_id, content, failure_reason, now),
        )
        await db.commit()
    log.warning(
        "Enqueued failed message for retry: server=%s channel=%s reason=%s",
        server_id, channel_id, failure_reason,
    )


# ---------------------------------------------------------------------------
# T004 — get_all_pending
# ---------------------------------------------------------------------------

async def get_all_pending(db_path: str) -> list[PendingMessage]:
    """Return all pending retry entries ordered by enqueue time (oldest first)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, server_id, channel_id, content, failure_reason, "
            "       enqueued_at, retry_count, last_attempted_at "
            "FROM pending_messages "
            "ORDER BY enqueued_at ASC"
        )
        rows = await cursor.fetchall()

    result: list[PendingMessage] = []
    for row in rows:
        last_attempted: datetime | None = None
        if row["last_attempted_at"]:
            last_attempted = datetime.fromisoformat(row["last_attempted_at"])
        result.append(
            PendingMessage(
                id=row["id"],
                server_id=row["server_id"],
                channel_id=row["channel_id"],
                content=row["content"],
                failure_reason=row["failure_reason"],
                enqueued_at=datetime.fromisoformat(row["enqueued_at"]),
                retry_count=row["retry_count"],
                last_attempted_at=last_attempted,
            )
        )
    return result


# ---------------------------------------------------------------------------
# T005 — mark_delivered
# ---------------------------------------------------------------------------

async def mark_delivered(db_path: str, entry_id: int) -> None:
    """Delete the pending_messages row — message successfully delivered."""
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM pending_messages WHERE id = ?", (entry_id,)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# T006 — mark_failed
# ---------------------------------------------------------------------------

async def mark_failed(db_path: str, entry_id: int) -> None:
    """Increment retry_count and record last_attempted_at for entry_id."""
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        await db.execute(
            """
            UPDATE pending_messages
               SET retry_count = retry_count + 1,
                   last_attempted_at = ?
             WHERE id = ?
            """,
            (now, entry_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# T007 + T016–T018 — attempt_delivery
# ---------------------------------------------------------------------------

async def attempt_delivery(entry: PendingMessage, bot: "Bot") -> bool:
    """Attempt to post entry.content to entry.channel_id.

    Returns True if all chunks were delivered successfully, False otherwise.
    Never raises. Never calls enqueue() (FR-009).

    On success: deletes the DB row and posts a delivery notification to the
    calculation log channel (best-effort; swallowed on failure).

    On failure: if retry_count >= RETRY_WARN_THRESHOLD a warning is posted to
    the log channel (T017); then mark_failed is called to increment the counter.
    The warning fires every cycle once the threshold is crossed.
    """
    # Import here to avoid circular import: retry_service ← output_router
    from utils.output_router import _chunk_message

    db_path: str = bot.db_path  # type: ignore[attr-defined]

    # --- Warn before attempt if threshold already crossed (T017) ---
    if entry.retry_count >= RETRY_WARN_THRESHOLD:
        warn_msg = (
            f"⚠️ Stuck retry entry id={entry.id}: message to channel "
            f"<#{entry.channel_id}> (id={entry.channel_id}) has failed "
            f"{entry.retry_count} time(s) since {entry.enqueued_at.isoformat()}. "
            f"Original failure: {entry.failure_reason}"
        )
        _safe_post_log(bot, entry.server_id, warn_msg)

    # --- Resolve channel ---
    channel = bot.get_channel(entry.channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(entry.channel_id)
        except Exception as exc:
            log.warning(
                "attempt_delivery: cannot fetch channel id=%s for entry id=%s: %s",
                entry.channel_id, entry.id, exc,
            )
            await mark_failed(db_path, entry.id)
            return False

    import discord
    if not isinstance(channel, discord.TextChannel):
        log.warning(
            "attempt_delivery: channel id=%s is not a TextChannel for entry id=%s",
            entry.channel_id, entry.id,
        )
        await mark_failed(db_path, entry.id)
        return False

    # --- Attempt send ---
    try:
        for chunk in _chunk_message(entry.content):
            await channel.send(chunk)
    except Exception as exc:
        log.warning(
            "attempt_delivery: send failed for entry id=%s channel=%s: %s",
            entry.id, entry.channel_id, exc,
        )
        await mark_failed(db_path, entry.id)
        return False

    # --- Success (T016) ---
    await mark_delivered(db_path, entry.id)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    success_msg = (
        f"✅ Retry delivery succeeded for channel <#{entry.channel_id}> "
        f"(id={entry.channel_id}). "
        f"Original failure: {entry.failure_reason}. "
        f"Retries taken: {entry.retry_count}. "
        f"Delivered at: {now_str}."
    )
    _safe_post_log(bot, entry.server_id, success_msg)
    return True


# ---------------------------------------------------------------------------
# T018 — internal helper: best-effort log-channel post
# ---------------------------------------------------------------------------

def _safe_post_log(bot: "Bot", server_id: int, message: str) -> None:
    """Schedule a fire-and-forget post_log call.

    Uses asyncio.ensure_future so the caller does not need to await it.
    Failures are swallowed and written to the application log at WARNING level
    only (T018).
    """
    import asyncio

    async def _post() -> None:
        try:
            await bot.output_router.post_log(server_id, message)  # type: ignore[attr-defined]
        except Exception as exc:
            log.warning(
                "_safe_post_log: failed to post log notification "
                "(server=%s): %s", server_id, exc,
            )

    asyncio.ensure_future(_post())
