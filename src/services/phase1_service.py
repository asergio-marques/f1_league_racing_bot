"""Phase 1 service — Rain probability announcement (T−5 days).

Draws Rpc from the per-track Beta distribution (mu, sigma), persists PhaseResult,
posts to forecast and log channels.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from db.database import get_connection
from models.track import get_effective_rpc_params
from utils.math_utils import compute_rpc_beta
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

    # --- Resolve effective (mu, sigma) — server override > packaged default ---
    async with get_connection(bot.db_path) as db:
        override_cursor = await db.execute(
            "SELECT mu_rain_pct, sigma_rain_pct FROM track_rpc_params WHERE track_name = ?",
            (track_name,),
        )
        override_row = await override_cursor.fetchone()

    override_mu = override_row["mu_rain_pct"] if override_row else None
    override_sigma = override_row["sigma_rain_pct"] if override_row else None

    try:
        mu, sigma = get_effective_rpc_params(track_name, override_mu, override_sigma)
    except ValueError as exc:
        # FR-015: no packaged default AND no server override — block Phase 1
        err_msg = (
            f"\u26a0\ufe0f Phase 1 BLOCKED for round {round_id} ({track_name}): {exc} "
            "Please run `/track config` for this track before the T\u22125d window expires."
        )
        log.error(err_msg)
        await bot.output_router.post_log(row["server_id"], err_msg)
        return

    # --- Draw Rpc from Beta distribution ---
    try:
        raw_draw, rpc = compute_rpc_beta(mu, sigma)
    except ValueError as exc:
        err_msg = (
            f"\u26a0\ufe0f Phase 1 BLOCKED for round {round_id} ({track_name}): "
            f"Beta sampling failed — {exc}"
        )
        log.error(err_msg)
        await bot.output_router.post_log(row["server_id"], err_msg)
        return

    nu = mu * (1.0 - mu) / sigma ** 2 - 1.0
    alpha = mu * nu
    beta_param = (1.0 - mu) * nu

    payload = {
        "phase": 1,
        "round_id": round_id,
        "track": track_name,
        "distribution": "beta",
        "mu": mu,
        "sigma": sigma,
        "alpha": alpha,
        "beta_param": beta_param,
        "raw_draw": raw_draw,
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

    msg = await bot.output_router.post_forecast(
        _Div(),
        phase1_message(row["mention_role_id"], track_name, rpc),
        server_id=row["server_id"],
    )
    if msg is not None:
        from services.forecast_cleanup_service import store_forecast_message
        await store_forecast_message(round_id, row["division_id"], 1, msg, bot.db_path)

    await bot.output_router.post_log(
        row["server_id"],
        phase_log_message(1, round_id, track_name, payload),
    )
    log.info("Phase 1 complete for round %s — Rpc=%.2f", round_id, rpc)
