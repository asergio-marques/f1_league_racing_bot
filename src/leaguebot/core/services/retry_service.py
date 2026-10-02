"""RetryService — persistent message retry queue.

When a post fails, its text is kept here with ``enqueue``: by ``OutputRouter`` for log lines
and a forecast's text, and by the forecast, calendar and attendance sheet posters for their
own. ``RetryCog`` runs ``attempt_delivery`` on a 5-minute loop until the message is
delivered.

Constitution Principle V: successful and stuck-entry retry outcomes are posted
to the calculation log channel for full observability.

FR-009: ``attempt_delivery`` NEVER calls ``enqueue`` on its own delivery
failures — the entry's ``retry_count`` is incremented instead.

**A line is sent once.** The change queue writes a log line in the save of the step it records
(``OutputRouter.queue_log_on``) and delivers it once that save is committed
(``OutputRouter.deliver_queued``), while this loop may find the same row. Both take
:func:`delivery_lock` and read the row again under it, so a line delivered by one is gone when
the other looks, and is never sent twice.

**A row with an empty ``failure_reason`` was never tried:** the bot stopped between saving the
line and sending it. Nothing failed, so its delivery here writes no "Retry delivery succeeded"
line. A row that did fail carries its reason, and its eventual delivery is recorded as the core
specification asks ("When the bot stops").
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.pending_message import PendingMessage
from leaguebot.weather.utils.message_builder import discord_ts

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

RETRY_WARN_THRESHOLD: int = 12  # ~1 hour at 5-min intervals

_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def delivery_lock() -> asyncio.Lock:
    """The lock a delivery of a pending row is made under, so that no row is sent twice.

    One lock for each running event loop: an ``asyncio.Lock`` is bound to the loop it first has to
    wait in, and a module-level one would fail in any later loop (a test's, a restart's).
    """
    loop = asyncio.get_running_loop()
    lock = _LOCKS.get(loop)
    if lock is None:
        lock = _LOCKS[loop] = asyncio.Lock()
    return lock


# ---------------------------------------------------------------------------
# T003 — enqueue
# ---------------------------------------------------------------------------

async def enqueue(
    db_path: str,
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
                (channel_id, content, failure_reason, enqueued_at,
                 retry_count, last_attempted_at)
            VALUES (?, ?, ?, ?, 0, NULL)
            """,
            (channel_id, content, failure_reason, now),
        )
        await db.commit()
    log.warning(
        "Enqueued failed message for retry: channel=%s reason=%s",
        channel_id, failure_reason,
    )


# ---------------------------------------------------------------------------
# T004 — get_all_pending
# ---------------------------------------------------------------------------

_COLUMNS = (
    "SELECT id, channel_id, content, failure_reason, enqueued_at, retry_count, last_attempted_at "
    "FROM pending_messages "
)


def _message_of(row: aiosqlite.Row) -> PendingMessage:
    last_attempted: datetime | None = None
    if row["last_attempted_at"]:
        last_attempted = datetime.fromisoformat(row["last_attempted_at"])
    return PendingMessage(
        id=row["id"],
        channel_id=row["channel_id"],
        content=row["content"],
        failure_reason=row["failure_reason"],
        enqueued_at=datetime.fromisoformat(row["enqueued_at"]),
        retry_count=row["retry_count"],
        last_attempted_at=last_attempted,
    )


async def get_all_pending(db_path: str) -> list[PendingMessage]:
    """Return all pending retry entries ordered by enqueue time (oldest first)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(_COLUMNS + "ORDER BY enqueued_at ASC")
        rows = await cursor.fetchall()
    return [_message_of(row) for row in rows]


async def get_pending(db_path: str, entry_id: int) -> PendingMessage | None:
    """The pending entry *entry_id*, or None where it has been delivered or removed."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(_COLUMNS + "WHERE id = ?", (entry_id,))
        row = await cursor.fetchone()
    return None if row is None else _message_of(row)


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

async def mark_failed(db_path: str, entry_id: int, *, reason: str | None = None) -> None:
    """Increment retry_count and record last_attempted_at for entry_id.

    *reason* replaces the row's ``failure_reason``: a line never tried has none, and once it has
    failed its eventual delivery is recorded (see the module docstring).
    """
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(db_path) as db:
        await db.execute(
            """
            UPDATE pending_messages
               SET retry_count = retry_count + 1,
                   last_attempted_at = ?,
                   failure_reason = COALESCE(?, failure_reason)
             WHERE id = ?
            """,
            (now, reason, entry_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# T007 + T016–T018 — attempt_delivery
# ---------------------------------------------------------------------------

async def attempt_delivery(entry: PendingMessage, bot: "LeagueBot") -> bool:
    """Attempt to post entry.content to entry.channel_id, under :func:`delivery_lock`.

    The row is read again under the lock, and a row already delivered is left alone (True).

    Returns True if all chunks were delivered successfully, False otherwise.
    Never raises. Never calls enqueue() (FR-009).

    On success: deletes the DB row and posts a delivery notification to the
    calculation log channel (best-effort; swallowed on failure), unless the row
    was never tried (see the module docstring).

    On failure: if retry_count >= RETRY_WARN_THRESHOLD a warning is posted to
    the log channel (T017); then mark_failed is called to increment the counter.
    The warning fires every cycle once the threshold is crossed.
    """
    import discord

    from leaguebot.core.utils.messages import chunk_message

    db_path: str = bot.db_path
    async with delivery_lock():
        current = await get_pending(db_path, entry.id)
        if current is None:
            return True
        entry = current
        # A row never tried has no reason, and gains the one of its first failure here.
        first_failure = not entry.failure_reason

        # --- Warn before attempt if threshold already crossed (T017) ---
        if entry.retry_count >= RETRY_WARN_THRESHOLD:
            warn_msg = (
                f"⚠️ Stuck retry entry id={entry.id}: message to channel "
                f"<#{entry.channel_id}> (id={entry.channel_id}) has failed "
                f"{entry.retry_count} time(s) since {discord_ts(entry.enqueued_at)}. "
                f"Original failure: {entry.failure_reason}"
            )
            _safe_post_log(bot, warn_msg)

        # --- Resolve channel ---
        channel = bot.get_channel(entry.channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(entry.channel_id)
            except Exception as exc:
                log.warning(
                    "attempt_delivery: cannot fetch channel id=%s for entry id=%s: %s",
                    entry.channel_id, entry.id, exc, exc_info=True,
                )
                await mark_failed(db_path, entry.id, reason=str(exc) if first_failure else None)
                return False

        if not isinstance(channel, discord.TextChannel):
            log.warning(
                "attempt_delivery: channel id=%s is not a TextChannel for entry id=%s",
                entry.channel_id, entry.id,
            )
            await mark_failed(
                db_path, entry.id,
                reason="the channel is not a text channel" if first_failure else None,
            )
            return False

        # --- Attempt send ---
        try:
            for chunk in chunk_message(entry.content):
                await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
        except Exception as exc:
            log.warning(
                "attempt_delivery: send failed for entry id=%s channel=%s: %s",
                entry.id, entry.channel_id, exc, exc_info=True,
            )
            await mark_failed(db_path, entry.id, reason=str(exc) if first_failure else None)
            return False

        # --- Success (T016) ---
        await mark_delivered(db_path, entry.id)

    if first_failure:
        return True
    now_str = datetime.now(timezone.utc)
    success_msg = (
        f"\u2705 Retry delivery succeeded for channel <#{entry.channel_id}> "
        f"(id={entry.channel_id}). "
        f"Original failure: {entry.failure_reason}. "
        f"Retries taken: {entry.retry_count}. "
        f"Delivered at: {discord_ts(now_str)}."
    )
    _safe_post_log(bot, success_msg)
    return True


# ---------------------------------------------------------------------------
# T018 — internal helper: best-effort log-channel post
# ---------------------------------------------------------------------------

def _safe_post_log(bot: "LeagueBot", message: str) -> None:
    """Schedule a fire-and-forget post_log call.

    Uses asyncio.ensure_future so the caller does not need to await it.
    Failures are swallowed and written to the application log at WARNING level
    only (T018).
    """
    import asyncio

    async def _post() -> None:
        try:
            await bot.output_router.post_log(message)
        except Exception as exc:
            log.warning("_safe_post_log: failed to post log notification: %s", exc, exc_info=True)

    asyncio.ensure_future(_post())
