"""ForecastCleanupService — track and delete per-phase forecast messages.

Feature 007: Forecast Message Cleanup.

Deletes are the inverse of writes: they use the channel and message ids stored when the
forecast was posted, whether it went out as text through `OutputRouter` or as a graphic sent
from here.

Public API:
  store_forecast_message_on — persist a message ID, on the save the post job's record has
  forget_forecast_on      — drop a message ID once its message is deleted
  post_phase_message      — the one send site of a weather posting
  forecast_deletes        — the delete jobs a clean-up or a flush plans, one for each
                            recorded forecast
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import PlannedStep, StepFailedOnDiscord

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: The name of a forecast's deletion job, which `forecast_changes` gives its step.
DELETE_FORECAST = "delete_forecast"


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
# T010 + T013 — the forecasts a clean-up or a flush deletes, as jobs
# ---------------------------------------------------------------------------

async def forecast_deletes(
    db_path: str,
    *,
    round_id: int | None = None,
    phases: Sequence[int] | None = None,
    clean_up: bool = False,
) -> tuple[PlannedStep, ...]:
    """One `delete_forecast` job for each recorded forecast, in round, division, phase order.

    The clean-up a day after a round (T010, SC-005) asks for whichever of Phases 1 to 3 still
    stands for one round (*round_id* and *phases* given); the flush when test mode is switched off (T013, SC-004, FR-015)
    and the season end's for all of them (neither given). Each job carries the round (and its
    number), division, phase and message it was planned for, and deletes that message alone: the
    deletion is a job of the change queue, whose refusal stops it, so nothing is deleted here.
    Under *clean_up* each job is marked (``"clean_up": True``), so that a change naming it says it
    is the race's own forecast, deleted a day after the round, not a withdrawn or superseded one.
    """
    where: list[str] = []
    params: list[int] = []
    if round_id is not None:
        where.append("fm.round_id = ?")
        params.append(round_id)
    if phases is not None:
        where.append(f"fm.phase_number IN ({', '.join('?' for _ in phases)})")
        params.extend(phases)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT fm.round_id, fm.division_id, fm.phase_number, fm.message_id, "
            "       r.round_number "
            "FROM forecast_messages fm "
            "JOIN rounds r ON r.id = fm.round_id "
            "JOIN divisions d ON d.id = fm.division_id "
            "JOIN seasons s ON s.id = d.season_id "
            + (f"WHERE {' AND '.join(where)} " if where else "")
            + "ORDER BY fm.round_id, fm.division_id, fm.phase_number",
            params,
        )
        rows = list(await cursor.fetchall())
    return tuple(
        PlannedStep(DELETE_FORECAST, {
            "round_id": row["round_id"], "division_id": row["division_id"],
            "phase": row["phase_number"], "message_id": row["message_id"],
            "round_number": row["round_number"],
            **({"clean_up": True} if clean_up else {}),
        })
        for row in rows
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
