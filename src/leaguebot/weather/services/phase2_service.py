"""Phase 2 service — Session-level rain/mixed/sunny assignment, at the phase 2 horizon.

The horizon is configured (``weather_pipeline_config.phase_2_days``, two days by default)
and is no fixed T−2; see ``phase1_service`` for the whole of that note.

Reads Phase 1 Rpc, builds 1000-slot pool, draws one slot per session,
persists results, on the connection a save hands it (`draw_phase2_on`); the forecast is posted
after the draw (`forecast_changes`).
"""

from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.weather.models.forecast import Drawn
from leaguebot.weather.services.draw_guard import round_if_due
from leaguebot.weather.utils.math_utils import compute_ir, compute_im, compute_is, build_slot_pool
from leaguebot.weather.utils.message_builder import phase2_message, phase_log_message, session_type_label

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


async def draw_phase2_on(
    db: aiosqlite.Connection, round_id: int, *, now: datetime
) -> Drawn:
    """Draw Phase 2 for *round_id* on *db*, committing nothing.

    Writes each session's slot type, the draw (``phase_results``, created at *now*) and
    ``phase2_done`` on the connection it is handed, and returns the `Drawn` with its calculation
    line. A round whose Phase 1 is not done gives ``Drawn(earlier=1)``, nothing written: the
    caller draws Phase 1 first. A Phase 1 recorded as done but holding no ACTIVE result is not
    guessed at (nothing written).

    Produces nothing at all, and says so by a `Drawn` with no payload and no line, while the
    weather module is disabled, for a round that is cancelled, or whose division is, and for
    a phase already done; see ``draw_guard`` for why the draw itself refuses.
    """
    row = await round_if_due(db, round_id, label="Phase 2", done_column="phase2_done")
    if row is None:
        return Drawn(2)

    if not row["phase1_done"]:
        log.warning("Phase 2: Phase 1 not yet done for round %s — it is drawn first.", round_id)
        return Drawn(2, earlier=1)

    # --- Load active Phase 1 result to get Rpc ---
    p1_cursor = await db.execute(
        "SELECT payload FROM phase_results "
        "WHERE round_id = ? AND phase_number = 1 AND status = 'ACTIVE' "
        "ORDER BY id DESC LIMIT 1",
        (round_id,),
    )
    p1_row = await p1_cursor.fetchone()

    if p1_row is None:
        log.error("Phase 2: no active Phase 1 PhaseResult for round %s", round_id)
        return Drawn(2)

    p1_payload = json.loads(p1_row["payload"])
    rpc: float = p1_payload["rpc"]
    track_name: str = row["track_name"] or p1_payload.get("track", "Unknown")

    # --- Build slot pool ---
    ir = compute_ir(rpc)
    im = compute_im(rpc, ir)
    is_ = compute_is(im, ir)
    pool = build_slot_pool(ir, im, is_)

    # --- Load sessions and draw ---
    s_cursor = await db.execute(
        "SELECT id, session_type FROM sessions WHERE round_id = ? ORDER BY id",
        (round_id,),
    )
    sessions = await s_cursor.fetchall()

    slot_draws: list[dict] = []

    for session_row in sessions:
        slot = random.choice(pool)
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
    log.info("Phase 2 drawn for round %s", round_id)
    return Drawn(
        2, payload=payload, line=phase_log_message(2, round_id, track_name, payload), written=True
    )


async def run_phase2(round_id: int, bot: "LeagueBot") -> None:
    """Draw Phase 2 for *round_id* (Phase 1 first where it is not done), then post its forecast
    and calculation line.

    Until the timers, the recovery and test mode ask the change queue for a forecast's jobs
    (`forecast_changes`), the one caller of the draw that still posts as it did.
    """
    for _attempt in (1, 2):
        async with get_connection(bot.db_path) as db:
            drawn = await draw_phase2_on(db, round_id, now=datetime.now(timezone.utc))
            await db.commit()
        if not drawn.earlier or _attempt == 2:
            break
        from leaguebot.weather.services.phase1_service import run_phase1
        await run_phase1(round_id, bot)
    if drawn.payload is None:
        return
    payload = drawn.payload

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.division_id, d.forecast_channel_id, d.mention_role_id "
            "FROM rounds r JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return
    track_name = payload["track"]
    session_slots = [
        (session_type_label(d["session_type"]), d["slot"]) for d in payload["session_draws"]
    ]

    # --- Post output ---
    from leaguebot.weather.services.forecast_cleanup_service import post_phase_message
    from leaguebot.image.services.image_weather_post import attach_forecast

    attachment = await attach_forecast(bot, round_id, 2)

    # Produce before destroy (Constitution XIV.8). The phase 1 message stands until the phase
    # 2 message replacing it exists: deleting first would leave the division with no forecast
    # at all whenever this posting fails, which is the window the rule exists to close. Both
    # manners take this one send site, so the image flow inherits the ordering rather than
    # holding a second copy of it.
    await post_phase_message(
        bot,
        round_id=round_id,
        division_id=row["division_id"],
        channel_id=row["forecast_channel_id"],
        phase_number=2,
        text=phase2_message(row["mention_role_id"], track_name, session_slots),
        attachment=attachment,
        attachment_text=f"<@&{row['mention_role_id']}>",
        supersedes=1,
    )

    await bot.output_router.post_log(drawn.line or "")
    log.info("Phase 2 complete for round %s", round_id)

