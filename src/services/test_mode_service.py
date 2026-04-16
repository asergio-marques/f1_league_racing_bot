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
                r.result_status,
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

    # Fetch module flags, RSVP embed state, and finalized status.
    # These are used by _first_pending_for_row which drives both the
    # earlier-round priority check and the all-misfired DB fallback.
    async with get_connection(db_path) as db:
        rmc_cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config WHERE server_id = ?",
            (server_id,),
        )
        rmc_row = await rmc_cursor.fetchone()
        results_module_enabled = bool(rmc_row[0]) if rmc_row else False

        wm_cursor = await db.execute(
            "SELECT weather_module_enabled FROM server_configs WHERE server_id = ?",
            (server_id,),
        )
        wm_row = await wm_cursor.fetchone()
        weather_module_enabled = bool(wm_row[0]) if wm_row else False

        att_cursor = await db.execute(
            "SELECT module_enabled FROM attendance_config WHERE server_id = ?",
            (server_id,),
        )
        att_row = await att_cursor.fetchone()
        attendance_module_enabled = bool(att_row[0]) if att_row else False

        # RSVP embed state: keyed by round_id (one row per round since each
        # round belongs to exactly one division).
        if attendance_module_enabled:
            rsvp_cursor = await db.execute(
                """
                SELECT rem.round_id, rem.last_notice_msg_id, rem.distribution_msg_id
                FROM rsvp_embed_messages rem
                JOIN rounds r ON r.id = rem.round_id
                JOIN divisions d ON d.id = r.division_id
                JOIN seasons s ON s.id = d.season_id
                WHERE s.server_id = ?
                """,
                (server_id,),
            )
            rsvp_state: dict[int, Any] = {
                r["round_id"]: r for r in await rsvp_cursor.fetchall()
            }
        else:
            rsvp_state = {}

    def _first_pending_for_row(row: Any) -> "PhaseEntry | None":
        """Return the first pending phase for *row* detected from DB state only.

        Canonical order mirrors APScheduler fire-time ordering with defaults:
          normal:  P1(1) → RSVP-notice(5) → P2(2) → RSVP-last(6) → P3(3) → RSVP-deadline(7) → results(4)
          mystery: notice(0) → RSVP-notice(5) → RSVP-last(6) → RSVP-deadline(7) → results(4)

        Used for two purposes:
          1. Priority check: detect misfired/absent work in earlier rounds before
             returning the first scheduler job.
          2. Empty-jobs fallback: iterate all rounds when the scheduler has nothing
             (all jobs auto-fired or evicted by misfire_grace_time).
        """
        rid = row["round_id"]
        is_mystery = str(row["format"]).upper() == "MYSTERY"

        # Skip rounds that are fully done or already in results processing.
        # result_status is the canonical post-submission state machine:
        #   PROVISIONAL → ACTIVE (not started) | FINAL/POST_RACE_PENALTY → done/in-review.
        # rounds.finalized is a legacy column (never set by current code); use result_status.
        if row["result_status"] in ("FINAL", "POST_RACE_PENALTY"):
            return None

        def _make(phase: int) -> PhaseEntry:
            return PhaseEntry(
                round_id=rid,
                round_number=row["round_number"],
                division_id=row["division_id"],
                phase_number=phase,
                track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
                division_name=row["division_name"],
                job_id=None,
            )

        # ── Phase 0 / Phase 1: mystery notice or weather P1 ──────────────
        if is_mystery:
            if not row["phase1_done"]:
                return _make(0)
        else:
            if weather_module_enabled and not row["phase1_done"]:
                return _make(1)

        # ── Phase 5: RSVP notice ──────────────────────────────────────────
        if attendance_module_enabled and rid not in rsvp_state:
            return _make(5)

        # ── Phase 2: weather P2 (normal rounds only) ──────────────────────
        if not is_mystery and weather_module_enabled and not row["phase2_done"]:
            return _make(2)

        # ── Phase 6: RSVP last-notice ─────────────────────────────────────
        if attendance_module_enabled:
            rsvp = rsvp_state.get(rid)
            if rsvp is not None and not rsvp["last_notice_msg_id"]:
                return _make(6)

        # ── Phase 3: weather P3 (normal rounds only) ──────────────────────
        if not is_mystery and weather_module_enabled and not row["phase3_done"]:
            return _make(3)

        # ── Phase 7: RSVP deadline ────────────────────────────────────────
        if attendance_module_enabled:
            rsvp = rsvp_state.get(rid)
            if rsvp is not None and not rsvp["distribution_msg_id"]:
                return _make(7)

        # ── Phase 4: result submission ────────────────────────────────────
        if results_module_enabled:
            if rid not in rounds_with_results and row["result_status"] == "PROVISIONAL":
                return _make(4)

        return None

    if pending_jobs:
        job = pending_jobs[0]
        # Before returning this scheduler job, check all earlier rounds (by
        # scheduled_at) for any pending work that the scheduler cannot see —
        # misfired/evicted phase jobs, result submission (excluded from
        # pending_jobs), or RSVP phases that were never created for past-dated
        # rounds.  Ensures chronological advance order is preserved even when
        # the job store is incomplete.
        first_job_idx = next(
            (i for i, r in enumerate(rows) if r["round_id"] == job["round_id"]),
            len(rows),
        )
        for row in rows[:first_job_idx]:
            entry = _first_pending_for_row(row)
            if entry is not None:
                return entry
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

    # ── DB fallback: all scheduler jobs have misfired or been evicted ─────────
    # Walk rounds in scheduled_at order and return the first pending phase
    # detected from DB state alone.  Covers the common test-mode scenario where
    # all rounds are past-dated and every job auto-fired or was evicted by
    # APScheduler's misfire_grace_time, leaving the job store empty.
    for row in rows:
        entry = _first_pending_for_row(row)
        if entry is not None:
            return entry

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

def _phase_status(done: bool, job_id: str, live_ids: set[str] | None) -> str:
    """Return a status emoji for a single phase slot.

    ✅ — phase complete (DB flag set)
    ⏳ — pending; scheduler job is present (will fire automatically)
    ⚠️  — pending; scheduler job is absent (misfired, never created, or
          already auto-fired — must use /test-mode advance)

    When *live_ids* is None (no scheduler available) pending phases show ⏳.
    """
    if done:
        return "✅"
    if live_ids is None:
        return "⏳"
    return "⏳" if job_id in live_ids else "⚠️"


async def build_review_summary(
    server_id: int,
    db_path: str,
    scheduler_service: Any = None,
) -> str:
    """Return a formatted multi-line string summarising all rounds and phase status.

    Groups results by division (insertion order), then by round (scheduled_at).
    Covers weather phases (P1-P3), result submission, and RSVP phases (notice /
    last-notice / deadline) based on DB state — so it reflects actual progress
    regardless of whether scheduler jobs have fired or been evicted.

    When *scheduler_service* is supplied, each pending phase is annotated:
      ⏳ = job is present in APScheduler (will fire automatically)
      ⚠️  = job is absent (misfired, evicted, or never created — needs /advance)

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

        # Module-enabled flags
        rmc_cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config WHERE server_id = ?",
            (server_id,),
        )
        rmc_row = await rmc_cursor.fetchone()
        results_module_enabled = bool(rmc_row[0]) if rmc_row else False

        att_cursor = await db.execute(
            "SELECT module_enabled FROM attendance_config WHERE server_id = ?",
            (server_id,),
        )
        att_row = await att_cursor.fetchone()
        attendance_module_enabled = bool(att_row[0]) if att_row else False

        # All non-cancelled rounds for the active season
        cursor = await db.execute(
            """
            SELECT
                r.id          AS round_id,
                r.round_number,
                r.format,
                r.track_name,
                r.scheduled_at,
                r.phase1_done,
                r.phase2_done,
                r.phase3_done,
                r.result_status,
                d.name        AS division_name,
                d.id          AS division_id
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.server_id = ?
              AND s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND r.status   != 'CANCELLED'
            ORDER BY d.id ASC, r.scheduled_at ASC
            """,
            (server_id,),
        )
        rows = await cursor.fetchall()

        # Rounds that have at least one ACTIVE session_result
        sr_cursor = await db.execute(
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
        rounds_with_results: set[int] = {r["round_id"] for r in await sr_cursor.fetchall()}

        # RSVP embed message rows: keyed by (round_id, division_id)
        rsvp_cursor = await db.execute(
            """
            SELECT rem.round_id, rem.division_id,
                   rem.message_id, rem.last_notice_msg_id, rem.distribution_msg_id
            FROM rsvp_embed_messages rem
            JOIN rounds r ON r.id = rem.round_id
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            WHERE s.server_id = ?
            """,
            (server_id,),
        )
        rsvp_rows = {
            (r["round_id"], r["division_id"]): r
            for r in await rsvp_cursor.fetchall()
        }

    if not rows:
        return f"**Season: {season_name} — ACTIVE**\n\nNo rounds have been configured yet."

    # Live scheduler job IDs — populated when a scheduler_service is available.
    round_ids: set[int] = {r["round_id"] for r in rows}
    live_ids: set[str] | None = (
        scheduler_service.get_job_ids_for_rounds(round_ids)
        if scheduler_service is not None
        else None
    )

    # Group by division
    divisions: dict[str, list] = {}
    for row in rows:
        div_name = row["division_name"]
        if div_name not in divisions:
            divisions[div_name] = []
        divisions[div_name].append(row)

    lines: list[str] = [f"**Season: {season_name} — ACTIVE**\n"]

    for div_name, div_rounds in divisions.items():
        lines.append(f"**{div_name}**")
        for row in div_rounds:
            rid = row["round_id"]
            rnum = row["round_number"]
            track = row["track_name"] or "TBA"
            try:
                date_str = str(row["scheduled_at"])[:10]
            except Exception:
                date_str = str(row["scheduled_at"])

            fmt = str(row["format"]).upper()
            is_mystery = fmt == "MYSTERY"

            parts: list[str] = []

            # ── Weather / mystery notice phases ───────────────────────────
            if is_mystery:
                notice = _phase_status(bool(row["phase1_done"]), f"mystery_r{rid}", live_ids)
                parts.append(f"Notice: {notice}")
            else:
                p1 = _phase_status(bool(row["phase1_done"]), f"phase1_r{rid}", live_ids)
                p2 = _phase_status(bool(row["phase2_done"]), f"phase2_r{rid}", live_ids)
                p3 = _phase_status(bool(row["phase3_done"]), f"phase3_r{rid}", live_ids)
                parts.append(f"P1: {p1}  P2: {p2}  P3: {p3}")

            # ── Result submission ─────────────────────────────────────────
            if results_module_enabled:
                if row["result_status"] == "FINAL":
                    res = "✅ finalized"
                elif rid in rounds_with_results:
                    res = "⏸️ pending review"
                else:
                    res = _phase_status(False, f"results_r{rid}", live_ids)
                parts.append(f"Results: {res}")

            # ── RSVP / attendance phases ──────────────────────────────────
            if attendance_module_enabled:
                rsvp = rsvp_rows.get((rid, row["division_id"]))
                notice_s = (
                    "✅" if rsvp is not None
                    else _phase_status(False, f"rsvp_notice_r{rid}", live_ids)
                )
                last_notice_s = (
                    "✅" if (rsvp and rsvp["last_notice_msg_id"])
                    else _phase_status(False, f"rsvp_last_notice_r{rid}", live_ids)
                )
                deadline_s = (
                    "✅" if (rsvp and rsvp["distribution_msg_id"])
                    else _phase_status(False, f"rsvp_deadline_r{rid}", live_ids)
                )
                parts.append(f"RSVP: {notice_s}  Last: {last_notice_s}  Deadline: {deadline_s}")

            line = f"  Round {rnum} · {track:<15} · {date_str}  " + "  |  ".join(parts)
            lines.append(line)

        lines.append("")  # blank line between divisions

    if live_ids is not None:
        lines.append("*Legend: ✅ done  ⏳ pending (job scheduled)  ⚠️ pending (no job — use /test-mode advance)*")

    return "\n".join(lines).rstrip()
