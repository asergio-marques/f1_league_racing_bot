"""Phase 1 service — Rain probability announcement (T−5 days).

Draws rand1, rand2 in [1, 98], computes Rpc, persists PhaseResult,
posts to forecast and log channels.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.database import get_connection
from models.track import get_btrack
from utils.math_utils import compute_rpc
from utils.message_builder import phase1_message, phase_log_message

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


async def run_phase1(round_id: int, bot: "Bot") -> None:
    """Execute Phase 1 for *round_id*."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.id, r.track_name, r.phase1_done, r.division_id, "
            "       d.forecast_channel_id, d.mention_role_id, "
            "       s.server_id "
            "FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id "
            "WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        log.error("Phase 1: round_id=%s not found", round_id)
        return

    if row["phase1_done"]:
        log.info("Phase 1 already done for round %s — skipping.", round_id)
        return

    track_name = row["track_name"]
    if not track_name:
        log.error("Phase 1: round %s has no track_name", round_id)
        return

    try:
        btrack = get_btrack(track_name)
    except ValueError as exc:
        log.error("Phase 1: %s", exc)
        return

    rand1 = random.randint(1, 98)
    rand2 = random.randint(1, 98)
    rpc = compute_rpc(btrack, rand1, rand2)

    payload = {
        "phase": 1,
        "round_id": round_id,
        "track": track_name,
        "btrack": btrack,
        "rand1": rand1,
        "rand2": rand2,
        "rpc": rpc,
    }

    now = datetime.now(timezone.utc)

    async with get_connection(bot.db_path) as db:
        await db.execute(
            "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
            "VALUES (?, 1, ?, 'ACTIVE', ?)",
            (round_id, json.dumps(payload), now.isoformat()),
        )
        await db.execute(
            "UPDATE rounds SET phase1_done = 1 WHERE id = ?",
            (round_id,),
        )
        await db.commit()

    # Build division-like object for output_router
    class _Div:
        forecast_channel_id = row["forecast_channel_id"]

    await bot.output_router.post_forecast(
        _Div(),
        phase1_message(row["mention_role_id"], track_name, rpc),
    )
    await bot.output_router.post_log(
        row["server_id"],
        phase_log_message(1, round_id, track_name, payload),
    )
    log.info("Phase 1 complete for round %s — Rpc=%.2f", round_id, rpc)
