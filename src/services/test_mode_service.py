"""test_mode_service — Test mode state management and phase queue.

Provides four async functions consumed by TestModeCog:
  - toggle_test_mode:             flip the test_mode_active flag in server_configs
  - toggle_test_mode_nationality: flip whether mock drivers carry a nationality
  - count_live_real_drivers:      how many real drivers stand in the way of enabling test mode
  - get_next_pending_phase:       find the earliest un-executed phase across all rounds
  - build_review_summary:         format a full season/division/round status string
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, TypedDict

from db.database import get_connection
from utils.league_bot import LeagueBot

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
    #: 0 = mystery notice — from the job store as well, where it is armed as `weather_p1` and
    #: `get_next_pending_phase` reads the round's format (#426), 1|2|3 = weather phases of a
    #: round of any other format, 4 = result submission, 5|6|7 = the check-in
    #: call, its last notice and its deadline, 8|9 = the forecast and the check-in cleanups a
    #: day after the round (#425).
    phase_number: int
    track_name: str
    division_name: str
    #: The APScheduler job the entry came from; None wherever it was found from database
    #: state instead — result submission always, and any phase the job store has lost.
    job_id: str | None


# ---------------------------------------------------------------------------
# Toggle
# ---------------------------------------------------------------------------

async def toggle_test_mode(db_path: str) -> bool:
    """Flip test_mode_active and return the NEW value.

    Uses a single atomic UPDATE so no read-modify-write race can occur.
    Returns False if the server has no config row (bot not initialised).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs "
            "SET test_mode_active = 1 - test_mode_active "
            "",
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs",
        )
        row = await cursor.fetchone()

    if row is None:
        log.error("toggle_test_mode: no server_config row")
        return False
    return bool(row["test_mode_active"])


async def switch_test_mode_off(bot: LeagueBot, *, discard_backup: bool = False) -> int:
    """Switch test mode off, deleting every driver it created.

    The one way test mode is left, by the toggle in Configuration or by the season it was chosen
    for ending (issue #220). Pending forecast deletions are flushed first, as they were while a
    season ran under test. Every fake driver is deleted and their history kept. A server not in
    test mode is left as it is. Returns the count of fake drivers removed.

    *discard_backup* deletes the saved test-mode backup as well, lock and all (decided
    2026-09-17). The toggle and a season being **completed** pass it: nothing could restore
    that state afterwards, the backup commands running in test mode alone. A season
    **cancelled or aborted** does not — it was abandoned rather than run to its end, and the
    state saved along the way is what a maintainer goes back to.
    """
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT test_mode_active FROM server_configs"
        )
        row = await cursor.fetchone()
    if row is None or not row["test_mode_active"]:
        return 0

    from services.forecast_cleanup_service import flush_pending_deletions
    from services.test_roster_service import clear_all_test_drivers

    try:
        await flush_pending_deletions(bot)
    except Exception:  # noqa: BLE001 — a stale forecast is not worth staying in test mode
        log.exception("switch_test_mode_off: could not flush pending deletions")
    removed = await clear_all_test_drivers(bot.db_path)
    if discard_backup:
        from services import backup_service

        try:
            backup_service.discard(bot.db_path, backup_service.jobstore_path_of(bot))
        except Exception:  # noqa: BLE001 — a backup left behind is not worth the switch
            log.exception("switch_test_mode_off: could not discard the saved backup")
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "UPDATE server_configs SET test_mode_active = 0"
        )
        await db.commit()
    return removed


async def toggle_test_mode_nationality(db_path: str) -> bool:
    """Flip test_mode_nationality_required and return the NEW value.

    The test-mode counterpart of the signup nationality switch: while test mode is active
    it stands in for it, so the graphics of a server under test may be seen with flags and
    without them without the real signup setting being touched.

    Same atomic UPDATE as toggle_test_mode, for the same reason. Returns False if the
    server has no config row (bot not initialised).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs "
            "SET test_mode_nationality_required = 1 - test_mode_nationality_required "
            "",
        )
        await db.commit()
        cursor = await db.execute(
            "SELECT test_mode_nationality_required FROM server_configs",
        )
        row = await cursor.fetchone()

    if row is None:
        log.error(
            "toggle_test_mode_nationality: no server_config row"
        )
        return False
    return bool(row["test_mode_nationality_required"])


async def count_live_real_drivers(db_path: str) -> int:
    """Return how many *live* real drivers this server holds.

    A live real driver is a driver_profiles row with is_test_driver = 0 whose state is
    anything but NOT_SIGNED_UP: someone mid-signup, unassigned or assigned. A row
    sitting at NOT_SIGNED_UP is a retained former driver — the profile of someone who has
    left, kept only for their history — and is not in the league, so it does not count.

    Test mode and a real league may not share a server: toggling test mode off deletes
    every fake driver on it, unscoped and unconfirmed, and a mixed roster corrupts the
    standings, the attendance sheets and the lineup graphics alike. This count is what
    /test-mode toggle refuses on.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_profiles "
            "WHERE is_test_driver = 0 AND current_state != 'NOT_SIGNED_UP'",
        )
        row = await cursor.fetchone()

    return int(row["n"]) if row is not None else 0


# ---------------------------------------------------------------------------
# Phase advancement queue
# ---------------------------------------------------------------------------

async def get_next_pending_phase(
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

    Result submission never comes from the scheduler. ``get_pending_advance_jobs``
    excludes results jobs, for every round format, so that a past-dated job which
    already auto-fired can neither block the wizard nor open it twice. A round is found
    due for submission from database state instead — no ACTIVE session results, and
    standing at NOT_RUN or AWAITING_RESULTS — whatever its format, mystery included: in
    the check of earlier rounds made before each scheduler job, and in the fallback once
    the job store is exhausted.

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
                r.checkin_cleared,
                r.status,
                d.name         AS division_name
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND r.status   != 'CANCELLED'
            ORDER BY r.scheduled_at ASC, d.id ASC
            """,
        )
        rows = list(await cursor.fetchall())
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
            WHERE sr.status = 'ACTIVE'
            """,
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
            "SELECT module_enabled FROM results_module_config",
        )
        rmc_row = await rmc_cursor.fetchone()
        results_module_enabled = bool(rmc_row[0]) if rmc_row else False

        wm_cursor = await db.execute(
            "SELECT weather_module_enabled FROM server_configs",
        )
        wm_row = await wm_cursor.fetchone()
        weather_module_enabled = bool(wm_row[0]) if wm_row else False

        att_cursor = await db.execute("SELECT module_enabled FROM attendance_config")
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
                """,
            )
            rsvp_state: dict[int, Any] = {
                r["round_id"]: r for r in await rsvp_cursor.fetchall()
            }
        else:
            rsvp_state = {}

        # Rounds whose Phase 3 forecast is still standing, for the forecast cleanup (8).
        if weather_module_enabled:
            forecast_cursor = await db.execute(
                "SELECT DISTINCT round_id FROM forecast_messages WHERE phase_number = 3",
            )
            standing_forecasts: set[int] = {
                r["round_id"] for r in await forecast_cursor.fetchall()
            }
        else:
            standing_forecasts = set()

    def _make_entry(row: Any, phase: int, job_id: str | None = None) -> PhaseEntry:
        is_mystery = str(row["format"]).upper() == "MYSTERY"
        return PhaseEntry(
            round_id=row["round_id"],
            round_number=row["round_number"],
            division_id=row["division_id"],
            phase_number=phase,
            track_name=row["track_name"] or ("Mystery" if is_mystery else "Unknown"),
            division_name=row["division_name"],
            job_id=job_id,
        )

    def _cleanup_pending(row: Any, phase: int | None = None) -> "PhaseEntry | None":
        """Return *row*'s cleanup still to run — *phase* alone where given — or None.

        Kept apart from `_first_pending_for_row` for two reasons (#425). A cleanup falls a day
        after its round, so offering one while checking the rounds before a scheduler job would
        pull it ahead of a later round's earlier work — the first of a double-header's cleanup
        before the second's deadline. And a finished round still has its cleanups to run, where
        `_first_pending_for_row` offers nothing for one, which would discard its cleanup job as
        stale.

        Pending means there is still something to take down: a Phase 3 forecast (8), or a
        check-in call recorded as standing (9). Once taken down there is nothing, and a cleanup
        with nothing to take down is not offered either.
        """
        rid = row["round_id"]
        if phase in (None, 8) and rid in standing_forecasts:
            return _make_entry(row, 8)
        if phase in (None, 9) and attendance_module_enabled and rid in rsvp_state:
            return _make_entry(row, 9)
        return None

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
        # A round that has ended, or whose appeals are being judged, offers no more phases.
        if row["status"] in ("FINAL", "AWAITING_APPEAL_VERDICTS"):
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
        # A round whose check-in has been taken down after it has no call recorded either, and
        # is not owed one (#425).
        if attendance_module_enabled and rid not in rsvp_state and not row["checkin_cleared"]:
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
            if rid not in rounds_with_results and row["status"] in (
                "NOT_RUN", "AWAITING_RESULTS"
            ):
                return _make(4)

        return None

    if pending_jobs:
        for job in pending_jobs:
            rnd = round_info[job["round_id"]]
            phase = job["phase_number"]
            # A mystery round's notice is armed as `weather_p1`, and the job store knows nothing
            # of formats: a live season's `_weather_phase_job` reads the format as the job fires,
            # and this is where advance reads it, once. The job is the notice (0) — and stale once
            # the notice is up, since advancing it would post the notice a second time. Handed
            # on as phase 1 it reached `run_phase1`, which reads no format (#426).
            if phase == 1 and str(rnd["format"]).upper() == "MYSTERY":
                if rnd["phase1_done"]:
                    continue
                phase = 0
            is_cleanup = phase in (8, 9)
            # Before returning this scheduler job, check all earlier rounds (by
            # scheduled_at) for any pending work that the scheduler cannot see —
            # misfired/evicted phase jobs, result submission (excluded from
            # pending_jobs), or RSVP phases that were never created for past-dated
            # rounds.  Ensures chronological advance order is preserved even when
            # the job store is incomplete.
            #
            # A cleanup checks its own round as well: it falls a day after the round, so the
            # round's own result submission, which never comes from the job store, is due first.
            first_job_idx = next(
                (i for i, r in enumerate(rows) if r["round_id"] == job["round_id"]),
                len(rows),
            )
            for row in rows[: first_job_idx + 1 if is_cleanup else first_job_idx]:
                entry = _first_pending_for_row(row)
                if entry is not None:
                    return entry
            # Validate this job against DB state before trusting it.  A scheduler
            # job can become stale when phases were already completed (e.g. run via
            # a previous advance invocation) but APScheduler still holds the job
            # because cancel_job was never called or the job fired-and-was-missed.
            # A cleanup is judged by its own work alone — see `_cleanup_pending`.
            if is_cleanup:
                if _cleanup_pending(rnd, phase) is None:
                    continue
            elif _first_pending_for_row(rnd) is None:
                # Round is fully done — stale scheduler job; try the next one.
                continue
            return _make_entry(rnd, phase, job["job_id"])

    # ── DB fallback: all scheduler jobs have misfired or been evicted ─────────
    # Walk rounds in scheduled_at order and return the first pending phase
    # detected from DB state alone.  Covers the common test-mode scenario where
    # all rounds are past-dated and every job auto-fired or was evicted by
    # APScheduler's misfire_grace_time, leaving the job store empty.
    for row in rows:
        entry = _first_pending_for_row(row)
        if entry is not None:
            return entry

    # The cleanups come last: each falls a day after its round, so with nothing else pending
    # anywhere, whatever is still standing is what remains to take down.
    for row in rows:
        entry = _cleanup_pending(row)
        if entry is not None:
            return entry

    return None


async def round_result_status(db_path: str, round_id: int) -> str | None:
    """Return a round's lifecycle state, or None if there is no such round.

    NOT_RUN -> AWAITING_RESULTS -> AWAITING_REPORT_VERDICTS -> AWAITING_APPEAL_VERDICTS -> FINAL,
    with CANCELLED as the other ending. Only FINAL and CANCELLED are terminal — a round awaiting
    appeal verdicts still has its results open to change.

    This replaced `is_round_finalized`, which read `rounds.finalized`: a column nothing has ever
    written, so it returned False for every round however completely it was scored and
    `/test-mode advance` treated a finished round as one still in penalty review (issue #154).
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    return row["status"] if row else None


# ---------------------------------------------------------------------------
# Review summary
# ---------------------------------------------------------------------------

def _phase_status(
    done: bool, event: tuple[int, str], queued: set[tuple[int, str]] | None
) -> str:
    """Return a status emoji for a single phase slot.

    ✅ — phase complete (DB flag set)
    ⏳ — pending; scheduler job is present (will fire automatically)
    ⚠️  — pending; scheduler job is absent (misfired, never created, or
          already auto-fired — must use /test-mode advance)

    *event* is the ``(round_id, event_type)`` the slot's job is armed under, and *queued* the
    scheduler's own answer from ``get_queued_events_for_rounds`` — a job is found by its round
    and its type, never by an ID rebuilt here, which is how every slot once read ⚠️ (#426).
    When *queued* is None (no scheduler available) pending phases show ⏳.
    """
    if done:
        return "✅"
    if queued is None:
        return "⏳"
    return "⏳" if event in queued else "⚠️"


async def build_review_summary(
    db_path: str,
    scheduler_service: Any = None,
) -> str:
    """Return a formatted multi-line string summarising all rounds and phase status.

    Groups results by division (insertion order), then by round (scheduled_at).
    Covers weather phases (P1-P3) and the forecast cleanup, result submission, and RSVP
    phases (notice / last-notice / deadline) and the check-in cleanup, based on DB state —
    so it reflects actual progress regardless of whether scheduler jobs have fired or been
    evicted. A module's steps are shown only while it is on — nothing of one that is off is
    armed, and advance runs none of it (#426).

    When *scheduler_service* is supplied, each pending phase is annotated:
      ⏳ = job is present in APScheduler (will fire automatically)
      ⚠️  = job is absent (misfired, evicted, or never created — needs /advance)

    Returns an informative message string if no active season exists.
    """
    async with get_connection(db_path) as db:
        # Season header
        season_cursor = await db.execute(
            "SELECT start_date FROM seasons WHERE status = 'ACTIVE'",
        )
        season_row = await season_cursor.fetchone()

        if season_row is None:
            return "ℹ️ No active season found. Configure a season first."

        season_name = f"Season starting {season_row['start_date']}"

        # Module-enabled flags
        rmc_cursor = await db.execute(
            "SELECT module_enabled FROM results_module_config",
        )
        rmc_row = await rmc_cursor.fetchone()
        results_module_enabled = bool(rmc_row[0]) if rmc_row else False

        att_cursor = await db.execute("SELECT module_enabled FROM attendance_config")
        att_row = await att_cursor.fetchone()
        attendance_module_enabled = bool(att_row[0]) if att_row else False

        # The weather module's steps are shown only while it is on, as the others' are:
        # nothing of it is armed while it is off, and advance runs none of it (#426).
        wm_cursor = await db.execute("SELECT weather_module_enabled FROM server_configs")
        wm_row = await wm_cursor.fetchone()
        weather_module_enabled = bool(wm_row[0]) if wm_row else False

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
                r.checkin_cleared,
                r.status,
                d.name        AS division_name,
                d.id          AS division_id
            FROM rounds r
            JOIN divisions d ON d.id  = r.division_id
            JOIN seasons   s ON s.id  = d.season_id
            WHERE s.status    = 'ACTIVE'
              AND d.status   != 'CANCELLED'
              AND r.status   != 'CANCELLED'
            ORDER BY d.id ASC, r.scheduled_at ASC
            """,
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
            WHERE sr.status = 'ACTIVE'
            """,
        )
        rounds_with_results: set[int] = {r["round_id"] for r in await sr_cursor.fetchall()}

        # Rounds whose Phase 3 forecast is still standing, for the forecast cleanup (#425)
        forecast_cursor = await db.execute(
            "SELECT DISTINCT round_id FROM forecast_messages WHERE phase_number = 3",
        )
        standing_forecasts: set[int] = {r["round_id"] for r in await forecast_cursor.fetchall()}

        # RSVP embed message rows: keyed by (round_id, division_id)
        rsvp_cursor = await db.execute(
            """
            SELECT rem.round_id, rem.division_id,
                   rem.message_id, rem.last_notice_msg_id, rem.distribution_msg_id
            FROM rsvp_embed_messages rem
            JOIN rounds r ON r.id = rem.round_id
            JOIN divisions d ON d.id = r.division_id
            JOIN seasons s ON s.id = d.season_id
            """,
        )
        rsvp_rows = {
            (r["round_id"], r["division_id"]): r
            for r in await rsvp_cursor.fetchall()
        }

    if not rows:
        return f"**Season: {season_name} — ACTIVE**\n\nNo rounds have been configured yet."

    # The jobs the scheduler holds, as (round_id, event_type) — when one is available.
    round_ids: set[int] = {r["round_id"] for r in rows}
    queued: set[tuple[int, str]] | None = (
        scheduler_service.get_queued_events_for_rounds(round_ids)
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
            if weather_module_enabled and is_mystery:
                # A mystery round's notice is armed as `weather_p1`: `_weather_phase_job` reads
                # the round's format as it fires.
                notice = _phase_status(bool(row["phase1_done"]), (rid, "weather_p1"), queued)
                parts.append(f"Notice: {notice}")
            elif weather_module_enabled:
                p1 = _phase_status(bool(row["phase1_done"]), (rid, "weather_p1"), queued)
                p2 = _phase_status(bool(row["phase2_done"]), (rid, "weather_p2"), queued)
                p3 = _phase_status(bool(row["phase3_done"]), (rid, "weather_p3"), queued)
                # Done once Phase 3 has run and nothing of it is left standing (#425).
                cleanup = _phase_status(
                    bool(row["phase3_done"]) and rid not in standing_forecasts,
                    (rid, "cleanup"),
                    queued,
                )
                parts.append(f"P1: {p1}  P2: {p2}  P3: {p3}  Cleanup: {cleanup}")

            # ── Result submission ─────────────────────────────────────────
            if results_module_enabled:
                if row["status"] == "FINAL":
                    res = "✅ finalized"
                elif rid in rounds_with_results:
                    res = "⏸️ pending review"
                else:
                    res = _phase_status(False, (rid, "results"), queued)
                parts.append(f"Results: {res}")

            # ── RSVP / attendance phases ──────────────────────────────────
            if attendance_module_enabled and row["checkin_cleared"]:
                # Taken down a day after the round, and its record with it (#425): every step
                # before the cleanup had run by then.
                parts.append("RSVP: ✅  Last: ✅  Deadline: ✅  Cleared: ✅")
            elif attendance_module_enabled:
                rsvp = rsvp_rows.get((rid, row["division_id"]))
                notice_s = (
                    "✅" if rsvp is not None
                    else _phase_status(False, (rid, "rsvp_notice"), queued)
                )
                last_notice_s = (
                    "✅" if (rsvp and rsvp["last_notice_msg_id"])
                    else _phase_status(False, (rid, "rsvp_last_notice"), queued)
                )
                deadline_s = (
                    "✅" if (rsvp and rsvp["distribution_msg_id"])
                    else _phase_status(False, (rid, "rsvp_deadline"), queued)
                )
                cleared_s = _phase_status(False, (rid, "rsvp_cleanup"), queued)
                parts.append(
                    f"RSVP: {notice_s}  Last: {last_notice_s}  Deadline: {deadline_s}  "
                    f"Cleared: {cleared_s}"
                )

            line = f"  Round {rnum} · {track:<15} · {date_str}  " + "  |  ".join(parts)
            lines.append(line)

        lines.append("")  # blank line between divisions

    if queued is not None:
        lines.append("*Legend: ✅ done  ⏳ pending (job scheduled)  ⚠️ pending (no job — use /test-mode advance)*")

    return "\n".join(lines).rstrip()
