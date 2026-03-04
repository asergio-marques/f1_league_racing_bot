"""Phase 3 service — Slot-by-slot weather assignment (T−2 hours).

For each session, draws N weather labels using weighted probabilities
derived from the Phase 2 slot type and Phase 1 Rpc.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.database import get_connection
from models.session import MAX_SLOTS, SessionType
from utils.math_utils import get_phase3_weights, draw_weighted
from utils.message_builder import phase3_message, phase_log_message, session_type_label

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)

_MIN_SLOTS = 2


async def run_phase3(round_id: int, bot: "Bot") -> None:
    """Execute Phase 3 for *round_id*."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.id, r.track_name, r.phase2_done, r.phase3_done, "
            "       d.forecast_channel_id, d.mention_role_id, s.server_id "
            "FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id "
            "WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        log.error("Phase 3: round_id=%s not found", round_id)
        return

    if row["phase3_done"]:
        log.info("Phase 3 already done for round %s — skipping.", round_id)
        return

    if not row["phase2_done"]:
        log.warning("Phase 3: Phase 2 not done for round %s — running Phase 2 first.", round_id)
        from services.phase2_service import run_phase2
        await run_phase2(round_id, bot)

    # Load active Phase 1 PhaseResult for Rpc
    async with get_connection(bot.db_path) as db:
        p1_cursor = await db.execute(
            "SELECT payload FROM phase_results "
            "WHERE round_id = ? AND phase_number = 1 AND status = 'ACTIVE' "
            "ORDER BY id DESC LIMIT 1",
            (round_id,),
        )
        p1_row = await p1_cursor.fetchone()

    if p1_row is None:
        log.error("Phase 3: no active Phase 1 PhaseResult for round %s", round_id)
        return

    p1_payload = json.loads(p1_row["payload"])
    rpc: float = p1_payload["rpc"]
    track_name: str = row["track_name"] or p1_payload.get("track", "Unknown")

    # Load sessions with their Phase 2 slot assignments
    async with get_connection(bot.db_path) as db:
        s_cursor = await db.execute(
            "SELECT id, session_type, phase2_slot_type FROM sessions "
            "WHERE round_id = ? ORDER BY id",
            (round_id,),
        )
        sessions = await s_cursor.fetchall()

    session_weather: list[tuple[str, list[str]]] = []
    session_draws: list[dict] = []

    for session_row in sessions:
        session_id: int = session_row["id"]
        session_type_val: str = session_row["session_type"]
        slot_type: str = session_row["phase2_slot_type"] or "sunny"

        # Determine draw count
        try:
            st_enum = SessionType(session_type_val)
            max_s = MAX_SLOTS.get(st_enum, 4)
        except ValueError:
            max_s = 4

        min_s = _MIN_SLOTS if slot_type == "mixed" else 1
        n_slots = random.randint(min_s, max_s)

        weights = get_phase3_weights(slot_type, rpc)
        slots = [draw_weighted(weights) for _ in range(n_slots)]

        session_weather.append((session_type_label(session_type_val), slots))
        session_draws.append({
            "session_id": session_id,
            "session_type": session_type_val,
            "slot_type": slot_type,
            "n_slots": n_slots,
            "slots": slots,
        })

    # Persist
    payload = {
        "phase": 3,
        "round_id": round_id,
        "track": track_name,
        "rpc": rpc,
        "session_draws": session_draws,
    }
    now = datetime.now(timezone.utc)

    async with get_connection(bot.db_path) as db:
        for draw in session_draws:
            await db.execute(
                "UPDATE sessions SET phase3_slots = ? WHERE id = ?",
                (json.dumps(draw["slots"]), draw["session_id"]),
            )
        await db.execute(
            "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
            "VALUES (?, 3, ?, 'ACTIVE', ?)",
            (round_id, json.dumps(payload), now.isoformat()),
        )
        await db.execute("UPDATE rounds SET phase3_done = 1 WHERE id = ?", (round_id,))
        await db.commit()

    class _Div:
        forecast_channel_id = row["forecast_channel_id"]

    await bot.output_router.post_forecast(
        _Div(),
        phase3_message(row["mention_role_id"], track_name, session_weather),
    )
    await bot.output_router.post_log(
        row["server_id"],
        phase_log_message(3, round_id, track_name, payload),
    )
    log.info("Phase 3 complete for round %s", round_id)
