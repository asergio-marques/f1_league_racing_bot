"""ForecastCleanupService — track and delete per-phase forecast messages.

Feature 007: Forecast Message Cleanup.

Deletes are the inverse of writes: they use the channel and message ids stored when the
forecast was posted, whether it went out as text through `OutputRouter` or as a graphic sent
from here.

Public API:
  store_forecast_message_on — persist a message ID, on the save the post job's record has
  forget_forecast_on      — drop a message ID once its message is deleted
  post_phase_message      — the one send site of a weather posting
  delete_forecast_message — delete a stored message (with test-mode guard)
  run_post_race_cleanup   — delete Phase 3 message 24 h after round start
  flush_pending_deletions — on test-mode disable, delete all stored messages
                            for a server at once
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# T003 — store_forecast_message
# ---------------------------------------------------------------------------

async def store_forecast_message_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    phase_number: int,
    message_id: int,
    *,
    now: datetime,
) -> None:
    """Record *message_id* as the forecast of *phase_number* of *round_id*, on *db*, committing
    nothing: the post job's record writes it in the save that marks the post done.

    Uses ``INSERT OR REPLACE`` so re-running a phase (e.g. after an amendment)
    transparently updates the stored ID without leaving stale rows.
    """
    await db.execute(
        """
        INSERT OR REPLACE INTO forecast_messages
            (round_id, division_id, phase_number, message_id, posted_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (round_id, division_id, phase_number, message_id, now.isoformat()),
    )
    log.debug(
        "Stored forecast message: round=%s div=%s phase=%s msg=%s",
        round_id, division_id, phase_number, message_id,
    )


async def forget_forecast_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    phase_number: int,
    message_id: int | None,
) -> None:
    """Drop the record of the forecast of *phase_number* of *round_id* on *db*, committing
    nothing, once its message is deleted.

    Only the row holding *message_id* goes, so that a newer forecast recorded for the phase is
    never dropped with the one it replaced; with no *message_id* the phase's row goes whatever
    it holds.
    """
    if message_id is None:
        await db.execute(
            "DELETE FROM forecast_messages "
            "WHERE round_id = ? AND division_id = ? AND phase_number = ?",
            (round_id, division_id, phase_number),
        )
        return
    await db.execute(
        "DELETE FROM forecast_messages "
        "WHERE round_id = ? AND division_id = ? AND phase_number = ? AND message_id = ?",
        (round_id, division_id, phase_number, message_id),
    )


# ---------------------------------------------------------------------------
# T004 + T012 — delete_forecast_message (with test-mode guard)
# ---------------------------------------------------------------------------

async def delete_forecast_message(
    round_id: int,
    division_id: int,
    phase_number: int,
    bot: "LeagueBot",
    *,
    raise_on_failure: bool = False,
) -> None:
    """Delete the stored Discord message for *phase_number* of *round_id*.

    **No test-mode guard.** This once skipped the deletion while test mode was active and
    retained the row for ``flush_pending_deletions``; that was removed at an earlier
    increment and deletion now behaves identically in test mode and live. The docstring said
    otherwise until 2026-08-14.

    On Discord API failure (NotFound / Forbidden / HTTPException) the DB row is
    still removed so a stale reference does not block future clean-ups.

    *raise_on_failure* is for a caller that must stop on a failure and be tried again, as a
    round amendment on the change queue is. A message Discord refuses to delete (or whose channel
    is not one the bot can delete in) raises `StepFailedOnDiscord` whose `result` is
    `{"channel_id": ..., "message_id": ...}`, raised `from` the refusal, and **keeps the row**,
    so the next try finds the message again. A message already gone, or whose channel is gone,
    counts as gone and drops the row. A fault that is not Discord's propagates unchanged.
    """
    db_path: str = bot.db_path

    # --- Load stored message row ---
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT fm.message_id, d.forecast_channel_id
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

    message_id: int = row["message_id"]
    channel_id: int = row["forecast_channel_id"]

    # --- Attempt Discord deletion ---
    if raise_on_failure:
        _delete_ok = True
        try:
            await _discord_delete(bot, channel_id, message_id, raising=True)
        except discord.NotFound:
            pass  # Already gone, or its channel is: which is what was asked.
        except discord.HTTPException as exc:
            raise StepFailedOnDiscord(
                f"the Phase {phase_number} forecast message {message_id} in channel"
                f" {channel_id} could not be deleted",
                result={"channel_id": channel_id, "message_id": message_id},
            ) from exc
    else:
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

async def run_post_race_cleanup(round_id: int, bot: "LeagueBot") -> None:
    """Delete the Phase 3 forecast message for all divisions of *round_id*.

    Invoked 24 hours after round start by the APScheduler
    ``cleanup_s{S}_d{D}_r{N}_id{round_id}`` job registered in SchedulerService.

    Each division is processed independently so a single failure does not block
    the others.
    """
    db_path: str = bot.db_path

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

async def flush_pending_deletions(bot: "LeagueBot") -> None:
    """Delete all pending forecast messages.

    Called when test mode is disabled (FR-015).  By the time this function
    runs, test mode has already been persisted as ``False`` in the DB, so the
    test-mode guard inside ``delete_forecast_message`` will not fire.

    All stored messages for the server are iterated and deleted.  Individual
    Discord API failures are logged but do not halt the batch.
    """
    db_path: str = bot.db_path

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT fm.round_id, fm.division_id, fm.phase_number
            FROM forecast_messages fm
            JOIN divisions d ON d.id = fm.division_id
            JOIN seasons s ON s.id = d.season_id
            ORDER BY fm.round_id, fm.division_id, fm.phase_number
            """,
        )
        rows = list(await cursor.fetchall())

    if not rows:
        log.debug("flush_pending_deletions: no pending messages")
        return

    log.info(
        "flush_pending_deletions: flushing %d message(s)",
        len(rows),
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

async def _discord_delete(
    bot: "LeagueBot", channel_id: int, message_id: int, *, raising: bool = False
) -> bool:
    """Attempt to delete a Discord message.  Returns True on success.

    Under *raising* it raises what Discord said, rather than logging it and returning False, and
    a channel that is not a text channel raises `StepFailedOnDiscord`.
    """
    try:
        channel = bot.get_channel(channel_id)
        if channel is None:
            channel = await bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            if raising:
                raise StepFailedOnDiscord(
                    f"channel {channel_id} is not a text channel the forecast can be deleted"
                    " from",
                    result={"channel_id": channel_id, "message_id": message_id},
                )
            log.error(
                "_discord_delete: channel id=%s is not a TextChannel", channel_id
            )
            return False
        await channel.get_partial_message(message_id).delete()
        return True
    except discord.HTTPException as exc:
        if raising:
            raise  # Discord's word, for the caller to turn into a stop (`from` it).
        _log_refusal(exc, channel_id, message_id)
        return False


def _log_refusal(exc: discord.HTTPException, channel_id: int, message_id: int) -> None:
    """Log a refused delete on the host, as the quiet form does."""
    if isinstance(exc, discord.NotFound):
        log.debug(
            "_discord_delete: message %s in channel %s already deleted or not found",
            message_id, channel_id,
        )
    elif isinstance(exc, discord.Forbidden):
        log.error(
            "_discord_delete: missing permissions to delete message %s in channel %s: %s",
            message_id, channel_id, exc,
        )
    else:
        log.error(
            "_discord_delete: HTTP error deleting message %s in channel %s: %s",
            message_id, channel_id, exc,
        )


# ---------------------------------------------------------------------------
# 042 — the one send site every weather posting takes
# ---------------------------------------------------------------------------

async def post_phase_message(
    bot: "LeagueBot",
    *,
    division_id: int,
    channel_id: int,
    phase_number: int,
    text: str,
    attachment=None,
    attachment_text: str = "",
) -> "discord.Message":
    """Post one weather occasion, and nothing else: the job that calls it records the message and
    deletes the one it supersedes.

    The single send site for all four of the module's weather postings — the three phases and
    the notice of a mystery round — and for both manners in which each may be drawn.

    **A transport failure stops the queue at the post job**, whose next try posts the text
    (XIV.8, FR-057): a message Discord refuses, a channel the bot cannot fetch and one that is
    not a text channel raise `StepFailedOnDiscord` (from the Discord fault where there is one),
    and the text is composed again when it is finally sent, never a picture kept for later. The
    post is the only thing that may fail here: the previous forecast stands until a job after
    this one deletes it (Constitution XIV.8, produce before destroy).

    *attachment* is a ``discord.File`` where the league draws a graphic; *attachment_text* is
    the message it rides on, which for a forecast is the division role mention and nothing
    besides, and for a mystery notice is empty.
    """
    if attachment is None:
        # The textual path, unchanged: the router chunks it, and raises where Discord refuses.
        from leaguebot.core.services.output_router import ForecastChannel

        msg = await bot.output_router.post_forecast(ForecastChannel(channel_id), text)
        if msg is None:
            raise StepFailedOnDiscord(
                f"the Phase {phase_number} forecast has no channel to be posted to"
            )
        return msg

    # One `finally` around the whole graphic branch. Two of its three exits abandon the
    # picture *before* the send — an unfetchable channel and one that is not a text
    # channel — and those are the paths a per-send cleanup would miss.
    from leaguebot.image.services.image_render_service import discard_attachment

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
                raise StepFailedOnDiscord(
                    f"the forecast channel <#{channel_id}> could not be reached: {exc}"
                ) from exc
        if not isinstance(channel, discord.TextChannel):
            log.error("post_phase_message: channel id=%s is not a TextChannel", channel_id)
            raise StepFailedOnDiscord(f"<#{channel_id}> is not a text channel")

        try:
            return await channel.send(
                attachment_text,
                file=attachment,
                allowed_mentions=discord.AllowedMentions.all(),
            )
        except discord.HTTPException as exc:
            log.warning(
                "post_phase_message: failed to post phase %s for division %s: %s",
                phase_number, division_id, exc,
            )
            raise StepFailedOnDiscord(
                f"the Phase {phase_number} forecast could not be posted to <#{channel_id}>: {exc}"
            ) from exc
    finally:
        discard_attachment(attachment)
