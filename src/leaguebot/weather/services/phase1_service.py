"""Phase 1 service — Rain probability announcement, at the league's phase 1 horizon.

The horizon is configured (``weather_pipeline_config.phase_1_days``, five days by default)
and is no fixed T−5: see ``PHASE_DESCRIPTIONS`` in ``leaguebot.weather.utils.message_builder`` for what naming
one as fixed cost a league, in issue #112.

Draws Rpc from the per-track Beta distribution (mu, sigma) and persists PhaseResult, on the
connection a save hands it (`draw_phase1_on`); the forecast is posted after the draw
(`forecast_changes`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.weather.models.forecast import Drawn
from leaguebot.weather.services.draw_guard import round_if_due
from leaguebot.weather.utils.math_utils import compute_rpc_beta
from leaguebot.weather.utils.message_builder import phase_log_message

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


async def draw_phase1_on(
    db: aiosqlite.Connection, round_id: int, *, now: datetime
) -> Drawn:
    """Draw Phase 1 for *round_id* on *db*, committing nothing.

    Writes the draw (``phase_results``, created at *now*) and ``phase1_done`` on the connection
    it is handed, so they stand or fall with the save that made them. Returns the `Drawn`: the
    payload and its calculation line, which the save writes beside the draw, or the BLOCKED line
    where the circuit's parameters cannot be resolved or sampled (nothing written).

    Produces nothing at all, and says so by a `Drawn` with no payload and no line, while the
    weather module is disabled, for a round that is cancelled, or whose division is, and for
    a phase already done; see ``draw_guard`` for why the draw itself refuses.
    """
    row = await round_if_due(db, round_id, label="Phase 1", done_column="phase1_done")
    if row is None:
        return Drawn(1)

    track_name = row["track_name"]
    if not track_name:
        log.error("Phase 1: round %s has no track_name", round_id)
        return Drawn(1)

    # --- Resolve (mu, sigma) from the tracks table ---
    track_cursor = await db.execute(
        "SELECT mu, sigma FROM tracks WHERE name = ?",
        (track_name,),
    )
    track_row = await track_cursor.fetchone()

    if track_row is None:
        err_msg = (
            f"\u26a0\ufe0f Phase 1 BLOCKED for round {round_id} ({track_name}): "
            "no track row found in the database for this circuit."
        )
        log.error(err_msg)
        return Drawn(1, line=err_msg, blocked=True)

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
        return Drawn(1, line=err_msg, blocked=True)

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

    await db.execute(
        "INSERT INTO phase_results (round_id, phase_number, payload, status, created_at) "
        "VALUES (?, 1, ?, 'ACTIVE', ?)",
        (round_id, json.dumps(payload), now.isoformat()),
    )
    await db.execute(
        "UPDATE rounds SET phase1_done = 1 WHERE id = ?",
        (round_id,),
    )
    log.info("Phase 1 drawn for round %s — Rpc=%.2f", round_id, rpc)
    return Drawn(
        1, payload=payload, line=phase_log_message(1, round_id, track_name, payload), written=True
    )


async def run_phase1(round_id: int, bot: "LeagueBot") -> None:
    """Ask the change queue to draw Phase 1 of *round_id* and post its forecast.

    Until the timers, the recovery and test mode ask the queue themselves (`forecast_changes`),
    the one thing that still calls this: it asks the change queue for the phase's draw, its post
    and the deletion of the forecast it supersedes, and posts nothing itself.
    """
    from leaguebot.weather.services.forecast_changes import ask_forecast

    await ask_forecast(bot.change_queue, bot.db_path, round_id, 1)
