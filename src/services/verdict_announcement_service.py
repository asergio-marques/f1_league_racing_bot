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
            return f"{abs(seconds)} seconds removed"
        return f"{seconds} seconds added"
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


async def _get_result_context(db_path: str, race_result_id: int | None, qual_result_id: int | None) -> dict:
    """Return round_number, session_type, format for a race or qualifying result row."""
    async with get_connection(db_path) as db:
        if race_result_id is not None:
            cursor = await db.execute(
                """
                SELECT r.round_number, sr.session_type, r.format, r.id AS round_id,
                       r.division_id
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE rsr.id = ?
                """,
                (race_result_id,),
            )
        elif qual_result_id is not None:
            cursor = await db.execute(
                """
                SELECT r.round_number, sr.session_type, r.format, r.id AS round_id,
                       r.division_id
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                JOIN rounds r ON r.id = sr.round_id
                WHERE qsr.id = ?
                """,
                (qual_result_id,),
            )
        else:
            return {}
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
    driver_display_name: str | None = None,
) -> str:
    """Build the full announcement message per contract."""
    season_prefix = f"Season {season_number} " if season_number is not None else ""
    heading = f"**{season_prefix}{division_name} Round {round_number} \u2014 {session_label}**"
    separator = "\u2501" * 35
    driver_ref = f"<@{driver_discord_id}>"
    if driver_display_name:
        driver_ref += f" ({driver_display_name})"
    return (
        f"{heading}\n"
        f"{separator}\n"
        f"**Driver**: {driver_ref}\n"
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
            race_result_id = record.get("race_result_id") if hasattr(record, "get") else getattr(record, "race_result_id", None)
            qual_result_id = record.get("qual_result_id") if hasattr(record, "get") else getattr(record, "qual_result_id", None)
            driver_discord_id: int = record.get("driver_user_id") if hasattr(record, "get") else getattr(record, "driver_user_id", 0)

            result_ctx = await _get_result_context(db_path, race_result_id, qual_result_id)
            if not result_ctx:
                log.warning("post_penalty_announcements: no result context for record %r", record)
                continue

            round_number: int = result_ctx["round_number"]
            session_type_str: str = result_ctx["session_type"]
            is_sprint: bool = str(result_ctx["format"]).upper() == "SPRINT"
            st = SessionType(session_type_str)
            session_label = results_formatter.format_session_label(st, is_sprint=is_sprint)

            # Resolve test display name
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT test_display_name FROM driver_profiles WHERE CAST(discord_user_id AS INTEGER) = ?",
                    (driver_discord_id,),
                )
                dp_row = await cursor.fetchone()
            test_display_name: str | None = dp_row["test_display_name"] if dp_row else None

            penalty_type = record.get("penalty_type") if hasattr(record, "get") else getattr(record, "penalty_type", "")
            time_seconds = record.get("time_seconds") if hasattr(record, "get") else getattr(record, "time_seconds", None)
            description_text = record.get("description") if hasattr(record, "get") else getattr(record, "description", "")
            justification_text = record.get("justification") if hasattr(record, "get") else getattr(record, "justification", "")

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
                driver_display_name=test_display_name,
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
            race_result_id = record.get("race_result_id") if hasattr(record, "get") else getattr(record, "race_result_id", None)
            qual_result_id = record.get("qual_result_id") if hasattr(record, "get") else getattr(record, "qual_result_id", None)
            driver_discord_id: int = record.get("driver_user_id") if hasattr(record, "get") else getattr(record, "driver_user_id", 0)

            result_ctx = await _get_result_context(db_path, race_result_id, qual_result_id)
            if not result_ctx:
                log.warning("post_appeal_announcements: no result context for record %r", record)
                continue

            round_number: int = result_ctx["round_number"]
            session_type_str: str = result_ctx["session_type"]
            is_sprint: bool = str(result_ctx["format"]).upper() == "SPRINT"
            st = SessionType(session_type_str)
            session_label = results_formatter.format_session_label(st, is_sprint=is_sprint)

            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT test_display_name FROM driver_profiles WHERE CAST(discord_user_id AS INTEGER) = ?",
                    (driver_discord_id,),
                )
                dp_row = await cursor.fetchone()
            test_display_name: str | None = dp_row["test_display_name"] if dp_row else None

            penalty_type = record.get("penalty_type") if hasattr(record, "get") else getattr(record, "penalty_type", "")
            time_seconds = record.get("time_seconds") if hasattr(record, "get") else getattr(record, "time_seconds", None)
            description_text = record.get("description") if hasattr(record, "get") else getattr(record, "description", "")
            justification_text = record.get("justification") if hasattr(record, "get") else getattr(record, "justification", "")

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
                driver_display_name=test_display_name,
            )
            await target_channel.send(content)

        except Exception:
            log.exception(
                "post_appeal_announcements: error posting announcement for record %r", record
            )


async def post_autosanction_announcement(
    bot,
    db_path: str,
    round_id: int,
    driver_discord_id: int,
    driver_display_name: str | None,
    sanction_type: str,  # "AUTOSACK" or "AUTORESERVE"
    threshold: int,
) -> None:
    """Post a verdict-channel announcement for an autosack or autoreserve action.

    Skips silently if the verdicts channel is not configured or inaccessible.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT s.season_number, d.name AS division_name,
                   drc.penalty_channel_id, r.round_number
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
        log.warning("post_autosanction_announcement: could not load context for round %s", round_id)
        return

    penalty_channel_id_raw = row["penalty_channel_id"]
    if penalty_channel_id_raw is None:
        return  # no verdicts channel configured — skip silently

    target_channel = bot.get_channel(int(penalty_channel_id_raw))
    if target_channel is None:
        log.error(
            "post_autosanction_announcement: verdicts channel %s inaccessible — skipping",
            penalty_channel_id_raw,
        )
        return

    season_number: int | None = row["season_number"]
    division_name: str = row["division_name"]
    round_number: int = row["round_number"]

    driver_ref = f"<@{driver_discord_id}>"
    if driver_display_name:
        driver_ref += f" ({driver_display_name})"

    if sanction_type == "AUTOSACK":
        penalty_label = "Sacked"
        description_text = "Sacked due to accumulation of attendance points."
        justification_text = (
            f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
            "removed from their full-time seat. Therefore, they have been removed from all "
            "driving seats effective immediately, and their current full-time seat will be "
            "offered to another driver."
        )
    else:  # AUTORESERVE
        penalty_label = "Moved to Reserve"
        description_text = "Moved to Reserve due to accumulation of attendance points."
        justification_text = (
            f"{driver_ref} has reached the {threshold} attendance point limit in order to be "
            "removed from their full-time seat. Therefore, they have been demoted to a reserve "
            "driver effective immediately, and their current full-time seat will be offered to "
            "another driver."
        )

    content = _build_announcement_message(
        season_number,
        division_name,
        round_number,
        "Attendance Sanction",
        driver_discord_id,
        penalty_label,
        description_text,
        justification_text,
        driver_display_name=driver_display_name,
    )
    try:
        await target_channel.send(content)
    except Exception:
        log.exception(
            "post_autosanction_announcement: error posting announcement for driver %s",
            driver_discord_id,
        )
