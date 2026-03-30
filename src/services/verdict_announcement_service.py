"""verdict_announcement_service.py — Post penalty and appeal announcements.

One Discord message per penalty or appeal correction, posted to the division's
configured verdicts (penalty) channel after the respective review is approved.
"""
from __future__ import annotations

import logging
import re

import discord

from db.database import get_connection
from models.points_config import SessionType
from utils import results_formatter

log = logging.getLogger(__name__)

# +Ns or -Ns  (with optional sign, digits, optional 's')
_PENALTY_RE = re.compile(r"^([+-]?\d+)s?$", re.IGNORECASE)


def translate_penalty(penalty_str: str) -> str:
    """Convert a raw penalty magnitude to a human-readable description.

    | Input       | Output                    |
    |-------------|---------------------------|
    | ``+5s``     | ``5 seconds removed``     |
    | ``5s``      | ``5 seconds removed``     |
    | ``-3s``     | ``3 seconds added``       |
    | ``DSQ``     | ``Disqualified``          |
    """
    if penalty_str.strip().upper() == "DSQ":
        return "Disqualified"
    m = _PENALTY_RE.match(penalty_str.strip())
    if m:
        seconds = int(m.group(1))
        if seconds < 0:
            return f"{abs(seconds)} seconds added"
        return f"{seconds} seconds removed"
    return penalty_str  # fallback: return raw value unchanged


async def _get_announcement_context(db_path: str, round_id: int) -> dict:
    """Return season_number, division_name, penalty_channel_id for the round."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.season_number, d.name AS division_name,
                   drc.penalty_channel_id
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return {}
    return {
        "season_number": row["season_number"],
        "division_name": row["division_name"],
        "penalty_channel_id": row["penalty_channel_id"],
    }


async def _get_result_context(db_path: str, driver_session_result_id: int) -> dict:
    """Return round_number, session_type, format for the given driver result."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT r.round_number, sr.session_type, r.format, r.id AS round_id,
                   r.division_id
            FROM driver_session_results dsr
            JOIN session_results sr ON sr.id = dsr.session_result_id
            JOIN rounds r ON r.id = sr.round_id
            WHERE dsr.id = ?
            """,
            (driver_session_result_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return {}
    return {
        "round_number": row["round_number"],
        "session_type": row["session_type"],
        "format": row["format"],
        "round_id": row["round_id"],
        "division_id": row["division_id"],
    }


def _build_announcement_message(
    season_number: int | None,
    division_name: str,
    round_number: int,
    session_label: str,
    driver_discord_id: int,
    penalty_description: str,
    description_text: str,
    justification_text: str,
) -> str:
    """Build the full announcement message per contract."""
    season_prefix = f"Season {season_number} " if season_number is not None else ""
    heading = f"**{season_prefix}{division_name} Round {round_number} \u2014 {session_label}**"
    separator = "\u2501" * 35
    return (
        f"{heading}\n"
        f"{separator}\n"
        f"**Driver**: <@{driver_discord_id}>\n"
        f"**Penalty**: {penalty_description}\n"
        f"**Description**: {description_text}\n"
        f"**Justification**: {justification_text}"
    )


async def post_penalty_announcements(
    bot,
    state,  # PenaltyReviewState
    applied_penalties: list,
) -> None:
    """Post one announcement per applied penalty to the verdicts channel.

    Skips silently if the verdicts channel is not configured or inaccessible.
    Does not block finalization on any error.
    """
    if not applied_penalties:
        return

    db_path: str = state.db_path
    round_id: int = state.round_id

    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        log.warning("post_penalty_announcements: could not load context for round %s", round_id)
        return

    penalty_channel_id_raw = ctx.get("penalty_channel_id")
    if penalty_channel_id_raw is None:
        return  # no verdicts channel configured — skip silently

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_penalty_announcements: verdicts channel %s inaccessible for round %s — skipping",
            penalty_channel_id_raw,
            round_id,
        )
        return

    season_number = ctx["season_number"]
    division_name = ctx["division_name"]

    for record in applied_penalties:
        try:
            # record may be a penalty_record DB row dict or a StagedPenalty-like object
            driver_session_result_id = (
                record.get("driver_session_result_id")
                if hasattr(record, "get")
                else getattr(record, "driver_session_result_id", None)
            )
            if driver_session_result_id is None:
                continue

            result_ctx = await _get_result_context(db_path, driver_session_result_id)
            if not result_ctx:
                continue

            round_number: int = result_ctx["round_number"]
            session_type_str: str = result_ctx["session_type"]
            is_sprint: bool = str(result_ctx["format"]).upper() == "SPRINT"
            st = SessionType(session_type_str)
            session_label = results_formatter.format_session_label(st, is_sprint=is_sprint)

            # Resolve driver Discord ID from driver_session_results
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT driver_user_id FROM driver_session_results WHERE id = ?",
                    (driver_session_result_id,),
                )
                dsr_row = await cursor.fetchone()
            driver_discord_id: int = dsr_row["driver_user_id"] if dsr_row else 0

            penalty_type = (
                record.get("penalty_type") if hasattr(record, "get") else getattr(record, "penalty_type", "")
            )
            time_seconds = (
                record.get("time_seconds") if hasattr(record, "get") else getattr(record, "time_seconds", None)
            )
            description_text = (
                record.get("description") if hasattr(record, "get") else getattr(record, "description", "")
            )
            justification_text = (
                record.get("justification") if hasattr(record, "get") else getattr(record, "justification", "")
            )

            # Translate penalty magnitude
            if penalty_type == "DSQ":
                pen_str = "DSQ"
            elif time_seconds is not None:
                pen_str = f"+{time_seconds}s" if time_seconds >= 0 else f"{time_seconds}s"
            else:
                pen_str = "DSQ"
            penalty_description = translate_penalty(pen_str)

            content = _build_announcement_message(
                season_number,
                division_name,
                round_number,
                session_label,
                driver_discord_id,
                penalty_description,
                description_text or "*(not provided)*",
                justification_text or "*(not provided)*",
            )
            await target_channel.send(content)

        except Exception:
            log.exception(
                "post_penalty_announcements: error posting announcement for record %r", record
            )


async def post_appeal_announcements(
    bot,
    state,  # PenaltyReviewState
    applied_corrections: list,
) -> None:
    """Post one announcement per applied appeal correction to the verdicts channel.

    Identical contract to :func:`post_penalty_announcements`.
    Skips silently if the verdicts channel is not configured or inaccessible.
    """
    if not applied_corrections:
        return

    db_path: str = state.db_path
    round_id: int = state.round_id

    ctx = await _get_announcement_context(db_path, round_id)
    if not ctx:
        log.warning("post_appeal_announcements: could not load context for round %s", round_id)
        return

    penalty_channel_id_raw = ctx.get("penalty_channel_id")
    if penalty_channel_id_raw is None:
        return  # no verdicts channel configured — skip silently

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_appeal_announcements: verdicts channel %s inaccessible for round %s — skipping",
            penalty_channel_id_raw,
            round_id,
        )
        return

    season_number = ctx["season_number"]
    division_name = ctx["division_name"]

    for record in applied_corrections:
        try:
            driver_session_result_id = (
                record.get("driver_session_result_id")
                if hasattr(record, "get")
                else getattr(record, "driver_session_result_id", None)
            )
            if driver_session_result_id is None:
                continue

            result_ctx = await _get_result_context(db_path, driver_session_result_id)
            if not result_ctx:
                continue

            round_number: int = result_ctx["round_number"]
            session_type_str: str = result_ctx["session_type"]
            is_sprint: bool = str(result_ctx["format"]).upper() == "SPRINT"
            st = SessionType(session_type_str)
            session_label = results_formatter.format_session_label(st, is_sprint=is_sprint)

            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT driver_user_id FROM driver_session_results WHERE id = ?",
                    (driver_session_result_id,),
                )
                dsr_row = await cursor.fetchone()
            driver_discord_id: int = dsr_row["driver_user_id"] if dsr_row else 0

            penalty_type = (
                record.get("penalty_type") if hasattr(record, "get") else getattr(record, "penalty_type", "")
            )
            time_seconds = (
                record.get("time_seconds") if hasattr(record, "get") else getattr(record, "time_seconds", None)
            )
            description_text = (
                record.get("description") if hasattr(record, "get") else getattr(record, "description", "")
            )
            justification_text = (
                record.get("justification") if hasattr(record, "get") else getattr(record, "justification", "")
            )

            if penalty_type == "DSQ":
                pen_str = "DSQ"
            elif time_seconds is not None:
                pen_str = f"+{time_seconds}s" if time_seconds >= 0 else f"{time_seconds}s"
            else:
                pen_str = "DSQ"
            penalty_description = translate_penalty(pen_str)

            content = _build_announcement_message(
                season_number,
                division_name,
                round_number,
                session_label,
                driver_discord_id,
                penalty_description,
                description_text or "*(not provided)*",
                justification_text or "*(not provided)*",
            )
            await target_channel.send(content)

        except Exception:
            log.exception(
                "post_appeal_announcements: error posting announcement for record %r", record
            )
