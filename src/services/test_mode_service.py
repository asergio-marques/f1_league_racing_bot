"""test_mode_service — Test mode state management and phase queue.

Provides three async functions consumed by TestModeCog:
  - toggle_test_mode:        flip the test_mode_active flag in server_configs
  - get_next_pending_phase:  find the earliest un-executed phase across all rounds
  - build_review_summary:    format a full season/division/round status string
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from db.database import get_connection

if TYPE_CHECKING:
    from services.scheduler_service import SchedulerService

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Return-type hints
# ---------------------------------------------------------------------------

class PhaseEntry(TypedDict):
    round_id: int
    round_number: int
    division_id: int
    phase_number: int  # 0=mystery notice, 1|2|3=weather phases, 4=result submission
    track_name: str
    division_name: str
    job_id: str | None  # APScheduler job ID; None for mystery-round result fallback


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

async def get_next_pending_phase(
    server_id: int,
    db_path: str,
    scheduler_service: Any = None,
) -> PhaseEntry | None:
    """Return the earliest pending action entry based on the APScheduler job store.

    Uses the scheduler as the single source of truth for what is actually pending,
    so only events that the system has genuinely scheduled (i.e. the relevant module
    was enabled when the season was approved or the module was later enabled) are
    returned.  This means, for example, that weather phases are never advanced when
    the weather module is disabled — because no phase jobs would have been created.

    Resolution order (matches APScheduler fire-time ordering):
      1. next_run_time ASC  — earliest scheduled fire time first
      2. round_id ASC       — tie-break for same-fire-time jobs
      3. phase_number ASC   — e.g. phase 1 before phase 2 on same round

    Special case — Mystery rounds whose notice has already been sent (phase1_done=1)
    but have no ACTIVE session results yet: these never get a ``results_r{id}``
    APScheduler job (``schedule_round`` skips it for MYSTERY format), so they are
    handled via a DB-state fallback after all scheduler-backed jobs are exhausted.

    If there is no ACTIVE season for this server, returns None.
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
                r.phase1_done,
                r.phase2_done,
                r.phase3_done,
                d.name         AS division_name
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.server_id = ?
              AND s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND r.status   != 'CANCELLED'
            ORDER BY r.scheduled_at ASC, d.id ASC
            """,
            (server_id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None

        round_ids: set[int] = {r["round_id"] for r in rows}
        round_info = {r["round_id"]: r for r in rows}

        # Rounds that already have at least one ACTIVE session_result (for mystery fallback)
        results_cursor = await db.execute(
            """
            SELECT DISTINCT sr.round_id
            FROM session_results sr
            JOIN rounds r ON r.id = sr.round_id
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE s.server_id = ? AND sr.status = 'ACTIVE'
            """,
            (server_id,),
        )
        rounds_with_results: set[int] = {r["round_id"] for r in await results_cursor.fetchall()}

    # ── DB-based path (no scheduler) ────────────────────────────────────────
    # Used by unit tests and any caller that does not have a scheduler_service.
    # Implements the original spec ordering: scheduled_at ASC, division_id ASC;
    # Mystery rounds with phase1_done=0 return phase_number=0 (notice pending);
    # Mystery rounds with phase1_done=1 are skipped; Normal rounds return the
    # first incomplete phase (1, 2, or 3) based on the phase flag columns.
    if scheduler_service is None:
        for row in rows:
            is_mystery = str(row["format"]).upper() == "MYSTERY"
            if is_mystery:
                if not row["phase1_done"]:
                    return PhaseEntry(
                        round_id=row["round_id"],
                        round_number=row["round_number"],
                        division_id=row["division_id"],
                        phase_number=0,
                        track_name=row["track_name"] or "Mystery",
                        division_name=row["division_name"],
                        job_id=None,
                    )
                continue  # noticed mystery → skip
            # Normal round: find first incomplete phase
            if not row["phase1_done"]:
                phase = 1
            elif not row["phase2_done"]:
                phase = 2
            elif not row["phase3_done"]:
                phase = 3
            else:
                continue  # all weather phases done
            return PhaseEntry(
                round_id=row["round_id"],
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=phase,
                track_name=row["track_name"] or "Unknown",
                division_name=row["division_name"],
                job_id=None,
            )
        return None

    # ── Primary: scheduler job store ────────────────────────────────────────
    pending_jobs = scheduler_service.get_pending_advance_jobs(round_ids)

    # Fetch results_module_enabled early — needed in both the scheduler and fallback paths.
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config WHERE server_id = ?",
            (server_id,),
        )
        rmc_row = await cursor.fetchone()
    results_module_enabled = bool(rmc_row[0]) if rmc_row else False

    if pending_jobs:
        job = pending_jobs[0]
        # Before returning this scheduler job, check if an earlier round (by
        # scheduled_at) has a pending result submission.  Result submission is
        # excluded from the scheduler job list, so without this check the RSVP
        # phases of round N+1 would be returned before round N's results are done.
        if results_module_enabled:
            first_job_idx = next(
                (i for i, r in enumerate(rows) if r["round_id"] == job["round_id"]),
                len(rows),
            )
            for row in rows[:first_job_idx]:
                is_mystery = str(row["format"]).upper() == "MYSTERY"
                if is_mystery and not row["phase1_done"]:
                    continue
                if row["round_id"] in rounds_with_results:
                    continue
                if await is_round_finalized(db_path, row["round_id"]):
                    continue
                return PhaseEntry(
                    round_id=row["round_id"],
                    round_number=row["round_number"],
                    division_id=row["division_id"],
                    phase_number=4,
                    track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
                    division_name=row["division_name"],
                    job_id=None,
                )
        rnd = round_info[job["round_id"]]
        is_mystery = str(rnd["format"]).upper() == "MYSTERY"
        return PhaseEntry(
            round_id=job["round_id"],
            round_number=rnd["round_number"],
            division_id=rnd["division_id"],
            phase_number=job["phase_number"],
            track_name=rnd["track_name"] or ("Mystery" if is_mystery else "Unknown"),
            division_name=rnd["division_name"],
            job_id=job["job_id"],
        )

    # ── Fallback: any round whose result submission is still pending ───────────
    # Result submission is always detected via DB state, not via the APScheduler
    # job store, because results_r{id} jobs for past dates auto-fire immediately
    # when added in test mode — using the job store would cause double-triggering.
    # MYSTERY rounds gate result submission on phase1_done=1 (notice sent first).
    # A round needs results when the results module is enabled, all applicable
    # scheduler-backed work is done (no pending jobs above), and no ACTIVE
    # session_result exists for it.
    if results_module_enabled:
        for row in rows:
            is_mystery = str(row["format"]).upper() == "MYSTERY"
            if is_mystery and not row["phase1_done"]:
                continue  # mystery notice not yet sent
            if row["round_id"] in rounds_with_results:
                continue
            # Skip rounds that are finalized (penalty review approved) — their
            # results submission is fully complete, not a pending phase.
            if await is_round_finalized(db_path, row["round_id"]):
                continue
            return PhaseEntry(
                round_id=row["round_id"],
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=4,
                track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
                division_name=row["division_name"],
                job_id=None,
            )

    return None


async def is_round_finalized(db_path: str, round_id: int) -> bool:
    """Return True if the round's penalty review has been approved (``finalized = 1``)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT finalized FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    return bool(row["finalized"]) if row else False


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
