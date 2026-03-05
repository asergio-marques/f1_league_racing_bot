"""test_mode_service — Test mode state management and phase queue.

Provides three async functions consumed by TestModeCog:
  - toggle_test_mode:        flip the test_mode_active flag in server_configs
  - get_next_pending_phase:  find the earliest un-executed phase across all rounds
  - build_review_summary:    format a full season/division/round status string
"""

from __future__ import annotations

import logging
from typing import TypedDict

from db.database import get_connection

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return-type hints
# ---------------------------------------------------------------------------

class PhaseEntry(TypedDict):
    round_id: int
    round_number: int
    division_id: int
    phase_number: int  # 1|2|3 for normal phases; 0 for mystery notice
    track_name: str
    division_name: str


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

async def toggle_test_mode(server_id: int, db_path: str) -> bool:
    """Flip test_mode_active for *server_id* and return the NEW value.

    Uses a single atomic UPDATE so no read-modify-write race can occur.
    Returns False if the server has no config row (bot not initialised).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs "
            "SET test_mode_active = 1 - test_mode_active "
            "WHERE server_id = ?",
            (server_id,),
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs WHERE server_id = ?",
            (server_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        log.error("toggle_test_mode: no server_config row for server_id=%s", server_id)
        return False
    return bool(row["test_mode_active"])


# ---------------------------------------------------------------------------
# Phase advancement queue
# ---------------------------------------------------------------------------

async def get_next_pending_phase(server_id: int, db_path: str) -> PhaseEntry | None:
    """Return the earliest pending action entry, or None when all are done.

    Resolution order:
      1. rounds.scheduled_at ASC   (real-world trigger time — earliest first)
      2. divisions.id ASC          (insertion order; tie-breaks same-date rounds)
      3. action type ASC           (mystery notice / Phase 1 before 2 before 3)

    Mystery rounds are included: when their mystery notice has not yet been sent
    (phase1_done = 0 for that round), phase_number=0 is returned as a sentinel
    so the cog can call run_mystery_notice instead of a phase service.  After the
    notice is sent the cog sets phase1_done = 1 on the round, which excludes it
    from future calls.

    If there is no ACTIVE season for this server, returns None.

    Note on concurrency: if the real scheduler fires a phase for a round that has
    already been advanced by this command, the phase service's own phase1_done /
    phase2_done / phase3_done idempotency guard will silently skip it — no
    duplicate output will be produced.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT
                r.id           AS round_id,
                r.round_number,
                r.division_id,
                r.format,
                r.track_name,
                r.scheduled_at,
                r.phase1_done,
                r.phase2_done,
                r.phase3_done,
                d.name         AS division_name
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.server_id = ?
              AND s.status    = 'ACTIVE'
            ORDER BY r.scheduled_at ASC, d.id ASC
            """,
            (server_id,),
        )
        rows = await cursor.fetchall()

    for row in rows:
        is_mystery = str(row["format"]).upper() == "MYSTERY"
        if is_mystery:
            # phase1_done = 1 means the mystery notice has already been posted
            if not row["phase1_done"]:
                return PhaseEntry(
                    round_id=row["round_id"],
                    round_number=row["round_number"],
                    division_id=row["division_id"],
                    phase_number=0,  # sentinel: mystery notice
                    track_name=row["track_name"] or "Mystery",
                    division_name=row["division_name"],
                )
            continue  # notice already sent — skip this round
        # Non-mystery: check each phase in order
        if not row["phase1_done"]:
            return PhaseEntry(
                round_id=row["round_id"],
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=1,
                track_name=row["track_name"] or "Unknown",
                division_name=row["division_name"],
            )
        if not row["phase2_done"]:
            return PhaseEntry(
                round_id=row["round_id"],
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=2,
                track_name=row["track_name"] or "Unknown",
                division_name=row["division_name"],
            )
        if not row["phase3_done"]:
            return PhaseEntry(
                round_id=row["round_id"],
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=3,
                track_name=row["track_name"] or "Unknown",
                division_name=row["division_name"],
            )

    # All rounds fully actioned (or no active season / no rounds)
    return None


# ---------------------------------------------------------------------------
# Review summary
# ---------------------------------------------------------------------------

async def build_review_summary(server_id: int, db_path: str) -> str:
    """Return a formatted multi-line string summarising all rounds and phase status.

    Groups results by division (insertion order), then by round (scheduled_at).
    Mystery rounds appear with 'Phases N/A' instead of P1/P2/P3 indicators.

    Returns an informative message string if no active season exists.
    """
    async with get_connection(db_path) as db:
        # Season header
        season_cursor = await db.execute(
            "SELECT start_date FROM seasons WHERE server_id = ? AND status = 'ACTIVE'",
            (server_id,),
        )
        season_row = await season_cursor.fetchone()

        if season_row is None:
            return "ℹ️ No active season found. Configure a season first."

        season_name = f"Season starting {season_row['start_date']}"

        # All rounds for the active season
        cursor = await db.execute(
            """
            SELECT
                r.id          AS round_id,
                r.format,
                r.track_name,
                r.scheduled_at,
                r.phase1_done,
                r.phase2_done,
                r.phase3_done,
                d.name        AS division_name,
                d.id          AS division_id
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.server_id = ?
              AND s.status    = 'ACTIVE'
            ORDER BY d.id ASC, r.scheduled_at ASC
            """,
            (server_id,),
        )
        rows = await cursor.fetchall()

    if not rows:
        return f"**Season: {season_name} — ACTIVE**\n\nNo rounds have been configured yet."

    # Group by division
    divisions: dict[str, list] = {}
    for row in rows:
        div_name = row["division_name"]
        if div_name not in divisions:
            divisions[div_name] = []
        divisions[div_name].append(row)

    lines: list[str] = [f"**Season: {season_name} — ACTIVE**\n"]

    for div_name, rounds in divisions.items():
        lines.append(f"**{div_name}**")
        for i, row in enumerate(rounds, start=1):
            track = row["track_name"] or "TBA"
            # Format date string
            sched = row["scheduled_at"]
            try:
                date_str = str(sched)[:10]  # YYYY-MM-DD
            except Exception:
                date_str = str(sched)

            fmt = str(row["format"]).upper()

            if fmt == "MYSTERY":
                lines.append(
                    f"  Round {i} · {track:<15} · {date_str}  *(Mystery Round — phases N/A)*"
                )
            else:
                p1 = "✅" if row["phase1_done"] else "⏳"
                p2 = "✅" if row["phase2_done"] else "⏳"
                p3 = "✅" if row["phase3_done"] else "⏳"
                lines.append(
                    f"  Round {i} · {track:<15} · {date_str}  "
                    f"P1: {p1}  P2: {p2}  P3: {p3}"
                )
        lines.append("")  # blank line between divisions

    return "\n".join(lines).rstrip()
