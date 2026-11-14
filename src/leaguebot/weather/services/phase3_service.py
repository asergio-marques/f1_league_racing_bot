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

import aiosqlite

from leaguebot.weather.models.forecast import Drawn, PhaseResultStatus
from leaguebot.weather.services.draw_guard import round_if_due
from leaguebot.core.models.session import MAX_SLOTS
from leaguebot.core.services.seasons.season_service import sessions_of_round_on
from leaguebot.weather.services.round_phases import phase2_slot_types_on
from leaguebot.weather.utils.math_utils import get_phase3_weights, draw_weighted
from leaguebot.weather.utils.message_builder import phase_log_message, format_slots_for_log

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
    row = await round_if_due(db, round_id, label="Phase 3", phase=3)
    if row is None:
        return Drawn(3)

    if not row.drawn[2]:
        log.warning("Phase 3: Phase 2 not done for round %s — it is drawn first.", round_id)
        return Drawn(3, earlier=2)

    # Load active Phase 1 PhaseResult for Rpc
    p1_cursor = await db.execute(
        "SELECT payload FROM phase_results "
        "WHERE round_id = ? AND phase_number = 1 AND status = ? "
        "ORDER BY id DESC LIMIT 1",
        (round_id, PhaseResultStatus.ACTIVE.value),
    )
    p1_row = await p1_cursor.fetchone()

    if p1_row is None:
        log.error("Phase 3: no active Phase 1 PhaseResult for round %s", round_id)
        return Drawn(3)

    p1_payload = json.loads(p1_row["payload"])
    rpc: float = p1_payload["rpc"]
    track_name: str = row.facts.track_name or p1_payload.get("track", "Unknown")

    # Load sessions with their Phase 2 slot assignments
    sessions = await sessions_of_round_on(db, round_id)
    slot_types = await phase2_slot_types_on(db, round_id)

    session_draws: list[dict] = []

    for session in sessions:
        session_id: int = session.id
        session_type_val: str = session.session_type.value
        slot_type: str = slot_types.get(session_id) or "sunny"

        # Determine draw count
        max_s = MAX_SLOTS.get(session.session_type, 4)

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
        "VALUES (?, 3, ?, ?, ?)",
        (round_id, json.dumps(payload), PhaseResultStatus.ACTIVE.value, now.isoformat()),
    )
    await db.execute("UPDATE rounds SET phase3_done = 1 WHERE id = ?", (round_id,))
    log.info("Phase 3 drawn for round %s", round_id)
    return Drawn(
        3, payload=payload, line=phase_log_message(
            3, row.facts.round_number, row.facts.division_name, track_name, payload
        ), written=True
    )
