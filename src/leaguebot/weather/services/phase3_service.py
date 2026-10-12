"""Phase 3 service — Slot-by-slot weather assignment, at the phase 3 horizon.

The horizon is configured (``weather_pipeline_config.phase_3_hours``, two hours by default)
and is no fixed T−2; see ``phase1_service`` for the whole of that note.

For each session, draws N weather labels using weighted probabilities
derived from the Phase 2 slot type and Phase 1 Rpc, on the connection a save hands it
(`draw_phase3_on`); the forecast is posted after the draw (`forecast_changes`).
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.weather.models.forecast import Drawn
from leaguebot.weather.services.draw_guard import round_if_due
from leaguebot.core.models.session import MAX_SLOTS, SessionType
from leaguebot.weather.utils.math_utils import get_phase3_weights, draw_weighted
from leaguebot.weather.utils.message_builder import phase_log_message, format_slots_for_log

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

_MIN_SLOTS = 2


async def draw_phase3_on(
    db: aiosqlite.Connection, round_id: int, *, now: datetime
) -> Drawn:
    """Draw Phase 3 for *round_id* on *db*, committing nothing.

    Writes each session's slots, the draw (``phase_results``, created at *now*) and
    ``phase3_done`` on the connection it is handed, and returns the `Drawn` with its calculation
    line. A round whose Phase 2 is not done gives ``Drawn(earlier=2)``, nothing written: the
    caller draws Phase 2 first. A Phase 1 holding no ACTIVE result is not guessed at (nothing
    written).

    Produces nothing at all, and says so by a `Drawn` with no payload and no line, while the
    weather module is disabled, for a round that is cancelled, or whose division is, and for
    a phase already done; see ``draw_guard`` for why the draw itself refuses.
    """
    row = await round_if_due(db, round_id, label="Phase 3", done_column="phase3_done")
    if row is None:
        return Drawn(3)

    if not row["phase2_done"]:
        log.warning("Phase 3: Phase 2 not done for round %s — it is drawn first.", round_id)
        return Drawn(3, earlier=2)

    # Load active Phase 1 PhaseResult for Rpc
    p1_cursor = await db.execute(
        "SELECT payload FROM phase_results "
        "WHERE round_id = ? AND phase_number = 1 AND status = 'ACTIVE' "
        "ORDER BY id DESC LIMIT 1",
        (round_id,),
    )
    p1_row = await p1_cursor.fetchone()

    if p1_row is None:
        log.error("Phase 3: no active Phase 1 PhaseResult for round %s", round_id)
        return Drawn(3)

    p1_payload = json.loads(p1_row["payload"])
    rpc: float = p1_payload["rpc"]
    track_name: str = row["track_name"] or p1_payload.get("track", "Unknown")

    # Load sessions with their Phase 2 slot assignments
    s_cursor = await db.execute(
        "SELECT id, session_type, phase2_slot_type FROM sessions "
        "WHERE round_id = ? ORDER BY id",
        (round_id,),
    )
    sessions = await s_cursor.fetchall()

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
        min_s = min(min_s, max_s)  # guard: min cannot exceed session-type cap
        n_slots = random.randint(min_s, max_s)

        weights = get_phase3_weights(slot_type, rpc)
        slots = [draw_weighted(weights) for _ in range(n_slots)]

        session_draws.append({
            "session_id": session_id,
            "session_type": session_type_val,
            "slot_type": slot_type,
            "n_slots": n_slots,
            "slots": slots,
            "slots_display": format_slots_for_log(slots),
        })

    # Persist
    payload = {
        "phase": 3,
        "round_id": round_id,
        "track": track_name,
        "rpc": rpc,
        "session_draws": session_draws,
    }

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
    log.info("Phase 3 drawn for round %s", round_id)
    return Drawn(
        3, payload=payload, line=phase_log_message(3, round_id, track_name, payload), written=True
    )


async def run_phase3(round_id: int, bot: "LeagueBot") -> None:
    """Ask the change queue to draw Phase 3 of *round_id* (Phase 2 first where it is not done) and post its forecast.

    Until the timers, the recovery and test mode ask the queue themselves (`forecast_changes`),
    the one thing that still calls this: it asks the change queue for the phase's draw, its post
    and the deletion of the forecast it supersedes, and posts nothing itself.
    """
    from leaguebot.weather.services.forecast_changes import ask_forecast

    await ask_forecast(bot.change_queue, bot.db_path, round_id, 3)
