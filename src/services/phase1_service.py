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
from utils.math_utils import compute_rpc_beta
from utils.message_builder import phase1_message, phase_log_message

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


async def run_phase1(round_id: int, bot: "Bot") -> None:
    """Execute Phase 1 for *round_id*.

    Produces nothing at all while the weather module is disabled for the round's server: no
    draw, no ``phase_results`` row, no ``phase1_done``, nothing posted. The check lives in the
    runner rather than only at its call sites because a phase computes, records *and* posts,
    and the core specification's rule — a disabled module produces nothing, "whatever the path
    arrives at it" — can only hold for every route in if the runner itself refuses. Guarding the
    callers alone is what let issue #113 through.
    """
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

    # The module gate — see the docstring for why it sits here.
    if not await bot.module_service.is_weather_enabled(row["server_id"]):  # type: ignore[attr-defined]
        log.info(
            "Phase 1: weather module disabled for server %s — round %s left untouched.",
            row["server_id"], round_id,
        )
        return

    if row["phase1_done"]:
        log.info("Phase 1 already done for round %s — skipping.", round_id)
        return

    track_name = row["track_name"]
    if not track_name:
        log.error("Phase 1: round %s has no track_name", round_id)
        return

    # --- Resolve (mu, sigma) from the tracks table (migration 029+) ---
    async with get_connection(bot.db_path) as db:
        track_cursor = await db.execute(
            "SELECT mu, sigma FROM tracks WHERE name = ?",
            (track_name,),
        )
        track_row = await track_cursor.fetchone()

    if track_row is None:
        err_msg = (
            f"\u26a0\ufe0f Phase 1 BLOCKED for round {round_id} ({track_name}): "
            "no track row found in the database — check that migration 029 has run."
        )
        log.error(err_msg)
        await bot.output_router.post_log(row["server_id"], err_msg)
        return

    mu = track_row["mu"]
    sigma = track_row["sigma"]

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

    # The graphic, where the league draws one. Reached only now — after the draw, after the
    # persistence — so that it can gate nothing (XIV.7). A failure leaves ``attachment`` None
    # and the textual forecast below is posted exactly as it always was.
    from services.forecast_cleanup_service import post_phase_message
    from services.image_weather_post import attach_forecast

    attachment = await attach_forecast(bot, round_id, 1, row["server_id"])

    await post_phase_message(
        bot,
        round_id=round_id,
        division_id=row["division_id"],
        server_id=row["server_id"],
        channel_id=row["forecast_channel_id"],
        phase_number=1,
        text=phase1_message(row["mention_role_id"], track_name, rpc),
        attachment=attachment,
        attachment_text=f"<@&{row['mention_role_id']}>",
    )

    await bot.output_router.post_log(
        row["server_id"],
        phase_log_message(1, round_id, track_name, payload),
    )
    log.info("Phase 1 complete for round %s — Rpc=%.2f", round_id, rpc)
