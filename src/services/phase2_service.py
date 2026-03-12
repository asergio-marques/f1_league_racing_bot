"""Phase 2 service — Session-level rain/mixed/sunny assignment (T−2 days).

Reads Phase 1 Rpc, builds 1000-slot pool, draws one slot per session,
persists results, posts to forecast and log channels.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.database import get_connection
from utils.math_utils import compute_ir, compute_im, compute_is, build_slot_pool
from utils.message_builder import phase2_message, phase_log_message, session_type_label

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


async def run_phase2(round_id: int, bot: "Bot") -> None:
    """Execute Phase 2 for *round_id*."""
    # --- Load context ---
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.id, r.track_name, r.phase1_done, r.phase2_done, r.division_id, "
            "       d.forecast_channel_id, d.mention_role_id, s.server_id "
            "FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id "
            "WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        log.error("Phase 2: round_id=%s not found", round_id)
        return

    if row["phase2_done"]:
        log.info("Phase 2 already done for round %s — skipping.", round_id)
        return

    if not row["phase1_done"]:
        log.warning("Phase 2: Phase 1 not yet done for round %s — running Phase 1 first.", round_id)
        from services.phase1_service import run_phase1
        await run_phase1(round_id, bot)

    # --- Load active Phase 1 result to get Rpc ---
    async with get_connection(bot.db_path) as db:
        p1_cursor = await db.execute(
            "SELECT payload FROM phase_results "
            "WHERE round_id = ? AND phase_number = 1 AND status = 'ACTIVE' "
            "ORDER BY id DESC LIMIT 1",
            (round_id,),
        )
        p1_row = await p1_cursor.fetchone()

    if p1_row is None:
        log.error("Phase 2: no active Phase 1 PhaseResult for round %s", round_id)
        return

    p1_payload = json.loads(p1_row["payload"])
    rpc: float = p1_payload["rpc"]
    track_name: str = row["track_name"] or p1_payload.get("track", "Unknown")

    # --- Build slot pool ---
    ir = compute_ir(rpc)
    im = compute_im(rpc, ir)
    is_ = compute_is(im, ir)
    pool = build_slot_pool(ir, im, is_)

    # --- Load sessions and draw ---
    async with get_connection(bot.db_path) as db:
        s_cursor = await db.execute(
            "SELECT id, session_type FROM sessions WHERE round_id = ? ORDER BY id",
            (round_id,),
        )
        sessions = await s_cursor.fetchall()

    session_slots: list[tuple[str, str]] = []
    slot_draws: list[dict] = []

    for session_row in sessions:
        slot = random.choice(pool)
        session_slots.append((session_type_label(session_row["session_type"]), slot))
        slot_draws.append({"session_id": session_row["id"], "session_type": session_row["session_type"], "slot": slot})

    # --- Persist ---
    payload = {
        "phase": 2,
        "round_id": round_id,
        "track": track_name,
        "rpc": rpc,
        "ir": ir,
        "im": im,
        "is": is_,
        "session_draws": slot_draws,
    }
    now = datetime.now(timezone.utc)

    async with get_connection(bot.db_path) as db:
        for draw in slot_draws:
            await db.execute(
                "UPDATE sessions SET phase2_slot_type = ? WHERE id = ?",
                (draw["slot"], draw["session_id"]),
            )
        await db.execute(
            "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
            "VALUES (?, 2, ?, 'ACTIVE', ?)",
            (round_id, json.dumps(payload), now.isoformat()),
        )
        await db.execute("UPDATE rounds SET phase2_done = 1 WHERE id = ?", (round_id,))
        await db.commit()

    # --- Post output ---
    class _Div:
        forecast_channel_id = row["forecast_channel_id"]

    from services.forecast_cleanup_service import delete_forecast_message, store_forecast_message
    await delete_forecast_message(round_id, row["division_id"], phase_number=1, bot=bot)

    msg = await bot.output_router.post_forecast(
        _Div(),
        phase2_message(row["mention_role_id"], track_name, session_slots),
        server_id=row["server_id"],
    )
    if msg is not None:
        await store_forecast_message(round_id, row["division_id"], 2, msg, bot.db_path)

    await bot.output_router.post_log(
        row["server_id"],
        phase_log_message(2, round_id, track_name, payload),
    )
    log.info("Phase 2 complete for round %s", round_id)
