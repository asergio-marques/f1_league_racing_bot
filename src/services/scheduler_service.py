"""SchedulerService — APScheduler wrapper for phase job management.

Uses SQLAlchemyJobStore backed by the same SQLite file so jobs survive restarts.
Jobs that missed their fire time are executed immediately (APScheduler default with
past DateTrigger + replace_existing=True).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.date import DateTrigger

from db.database import get_connection
from models.round import Round, RoundFormat

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_GRACE_SECONDS = 300  # 5-minute misfire grace period

# Module-level service reference so APScheduler can pickle the job callable.
# Set in SchedulerService.start(); always non-None when jobs fire.
_GLOBAL_SERVICE: "SchedulerService | None" = None


async def _signup_close_timer_job(server_id: int) -> None:
    """Module-level APScheduler callable for signup auto-close — picklable for
    SQLAlchemyJobStore. Delegates to the registered signup-close callback."""
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_signup_close_timer_job fired but _GLOBAL_SERVICE is None "
            "(server_id=%s) — skipping",
            server_id,
        )
        return
    cb = _GLOBAL_SERVICE._signup_close_callback
    if cb is None:
        log.warning(
            "_signup_close_timer_job: no callback registered (server_id=%s) — skipping",
            server_id,
        )
        return
    await cb(server_id)


async def _season_end_job(server_id: int, season_id: int) -> None:
    """Module-level APScheduler callable for season-end jobs — avoids closure
    pickling issues with SQLAlchemyJobStore.

    Mirrors the ``_phase_job`` pattern: looks up the running service instance
    via ``_GLOBAL_SERVICE`` and delegates to the registered season-end callback.
    """
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_season_end_job fired but _GLOBAL_SERVICE is None "
            "(server_id=%s, season_id=%s) — skipping",
            server_id, season_id,
        )
        return
    cb = _GLOBAL_SERVICE._season_end_callback
    if cb is None:
        log.warning(
            "_season_end_job: no callback registered (server_id=%s) — skipping",
            server_id,
        )
        return
    await cb(server_id, season_id)


async def _phase_job(phase_num: int, round_id: int) -> None:
    """Top-level APScheduler callable — avoids closure pickling issues.

    APScheduler with SQLAlchemyJobStore requires picklable callables.  Inner
    closures are not picklable, so we use a module-level function that finds
    the running service instance via the module-level sentinel.
    """
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_phase_job fired but _GLOBAL_SERVICE is None "
            "(phase=%s, round=%s) — skipping",
            phase_num, round_id,
        )
        return
    cb = _GLOBAL_SERVICE._phase_callbacks.get(phase_num)
    if cb is None:
        log.warning("No callback registered for phase %s; skipping.", phase_num)
        return
    await cb(round_id)


async def _mystery_notice_job(round_id: int) -> None:
    """Top-level APScheduler callable for Mystery round notices.

    Follows the same module-level pattern as ``_phase_job`` to avoid closure
    pickling issues with SQLAlchemyJobStore.
    """
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_mystery_notice_job fired but _GLOBAL_SERVICE is None "
            "(round=%s) — skipping",
            round_id,
        )
        return
    cb = _GLOBAL_SERVICE._mystery_notice_callback
    if cb is None:
        log.warning("No mystery notice callback registered; skipping round %s.", round_id)
        return
    await cb(round_id)


async def _forecast_cleanup_job(round_id: int) -> None:
    """Top-level APScheduler callable for post-race Phase 3 message cleanup.

    Fires 24 hours after round start.  Follows the same module-level pattern as
    ``_mystery_notice_job`` to avoid closure pickling issues with
    SQLAlchemyJobStore.
    """
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_forecast_cleanup_job fired but _GLOBAL_SERVICE is None "
            "(round=%s) — skipping",
            round_id,
        )
        return
    cb = _GLOBAL_SERVICE._forecast_cleanup_callback
    if cb is None:
        log.warning(
            "No forecast cleanup callback registered; skipping round %s.", round_id
        )
        return
    await cb(round_id)


async def _result_submission_job_wrapper(round_id: int) -> None:
    """Top-level APScheduler callable for result submission job."""
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_result_submission_job_wrapper fired but _GLOBAL_SERVICE is None "
            "(round=%s) — skipping",
            round_id,
        )
        return
    cb = _GLOBAL_SERVICE._result_submission_callback
    if cb is None:
        log.warning(
            "No result submission callback registered; skipping round %s.", round_id
        )
        return
    await cb(round_id)


async def _rsvp_notice_job(round_id: int) -> None:
    """Top-level APScheduler callable for RSVP notice jobs.

    Follows the same module-level pattern as ``_mystery_notice_job`` to avoid
    closure pickling issues with SQLAlchemyJobStore.
    """
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_rsvp_notice_job fired but _GLOBAL_SERVICE is None "
            "(round=%s) — skipping",
            round_id,
        )
        return
    cb = _GLOBAL_SERVICE._rsvp_notice_callback
    if cb is None:
        log.warning("No RSVP notice callback registered; skipping round %s.", round_id)
        return
    await cb(round_id)


async def _rsvp_last_notice_job(round_id: int) -> None:
    """Top-level APScheduler callable for RSVP last-notice jobs."""
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_rsvp_last_notice_job fired but _GLOBAL_SERVICE is None "
            "(round=%s) — skipping",
            round_id,
        )
        return
    cb = _GLOBAL_SERVICE._rsvp_last_notice_callback
    if cb is None:
        log.warning("No RSVP last-notice callback registered; skipping round %s.", round_id)
        return
    await cb(round_id)


async def _rsvp_deadline_job(round_id: int) -> None:
    """Top-level APScheduler callable for RSVP deadline jobs."""
    if _GLOBAL_SERVICE is None:
        log.warning(
            "_rsvp_deadline_job fired but _GLOBAL_SERVICE is None "
            "(round=%s) — skipping",
            round_id,
        )
        return
    cb = _GLOBAL_SERVICE._rsvp_deadline_callback
    if cb is None:
        log.warning("No RSVP deadline callback registered; skipping round %s.", round_id)
        return
    await cb(round_id)


class SchedulerService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        jobstore_url = f"sqlite:///{db_path}"
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=jobstore_url)},
            job_defaults={"misfire_grace_time": _GRACE_SECONDS},
            timezone="UTC",
        )
        # Phase callbacks injected after bot starts (to avoid circular imports)
        self._phase_callbacks: dict[int, Callable] = {}
        # Season-end callback injected after bot starts
        self._season_end_callback: "Callable | None" = None
        # Signup auto-close callback injected after bot starts
        self._signup_close_callback: "Callable | None" = None
        # Mystery notice callback injected after bot starts
        self._mystery_notice_callback: "Callable | None" = None
        # Post-race forecast cleanup callback injected after bot starts
        self._forecast_cleanup_callback: "Callable | None" = None
        # Result submission callback injected after bot starts
        self._result_submission_callback: "Callable | None" = None
        # RSVP callbacks injected after bot starts
        self._rsvp_notice_callback: "Callable | None" = None
        self._rsvp_last_notice_callback: "Callable | None" = None
        self._rsvp_deadline_callback: "Callable | None" = None

    def register_callbacks(
        self,
        phase1_cb: Callable,
        phase2_cb: Callable,
        phase3_cb: Callable,
    ) -> None:
        """Register async callables for each phase. Called from bot.py on_ready."""
        self._phase_callbacks[1] = phase1_cb
        self._phase_callbacks[2] = phase2_cb
        self._phase_callbacks[3] = phase3_cb

    def register_season_end_callback(self, callback: Callable) -> None:
        """Register the async callable invoked by season-end APScheduler jobs.

        The callable must accept ``(server_id: int, season_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._season_end_callback = callback

    def register_mystery_notice_callback(self, callback: Callable) -> None:
        """Register the async callable invoked when a Mystery round notice fires.

        The callable must accept ``(round_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._mystery_notice_callback = callback

    def register_forecast_cleanup_callback(self, callback: Callable) -> None:
        """Register the async callable invoked 24 h after a round start.

        The callable must accept ``(round_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._forecast_cleanup_callback = callback

    def register_result_submission_callback(self, callback: Callable) -> None:
        """Register the async callable invoked at round start time for result submission.

        The callable must accept ``(round_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._result_submission_callback = callback

    def register_rsvp_notice_callback(self, callback: Callable) -> None:
        """Register the async callable invoked when an RSVP notice job fires.

        The callable must accept ``(round_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._rsvp_notice_callback = callback

    def register_rsvp_last_notice_callback(self, callback: Callable) -> None:
        """Register the async callable invoked when an RSVP last-notice job fires.

        The callable must accept ``(round_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._rsvp_last_notice_callback = callback

    def register_rsvp_deadline_callback(self, callback: Callable) -> None:
        """Register the async callable invoked when an RSVP deadline job fires.

        The callable must accept ``(round_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._rsvp_deadline_callback = callback

    def start(self) -> None:
        global _GLOBAL_SERVICE
        _GLOBAL_SERVICE = self
        if not self._scheduler.running:
            self._scheduler.start()
            log.info("APScheduler started with SQLAlchemyJobStore at %s", self._db_path)

    def shutdown(self, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    # ------------------------------------------------------------------
    # Round scheduling
    # ------------------------------------------------------------------

    def schedule_round(
        self,
        rnd: Round,
        *,
        phase_1_days: int = 5,
        phase_2_days: int = 2,
        phase_3_hours: int = 2,
    ) -> None:
        """Register DateTrigger jobs for *rnd*.

        MYSTERY rounds: schedule the notice job (T−5 days) and the result
        submission job (round start time), but no weather phase jobs.
        Jobs use replace_existing=True so re-scheduling an amended round is safe.

        Args:
            phase_1_days: Days before the round to fire Phase 1 (default 5).
            phase_2_days: Days before the round to fire Phase 2 (default 2).
            phase_3_hours: Hours before the round to fire Phase 3 (default 2).
        """
        if rnd.format == RoundFormat.MYSTERY:
            scheduled_at = rnd.scheduled_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            fire_at = scheduled_at - timedelta(days=5)
            notice_job_id = f"mystery_r{rnd.id}"
            self._scheduler.add_job(
                _mystery_notice_job,
                trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
                id=notice_job_id,
                replace_existing=True,
                name=f"Mystery notice for round {rnd.id}",
                kwargs={"round_id": rnd.id},
            )
            log.info("Scheduled %s at %s", notice_job_id, fire_at.isoformat())
            # Also schedule result submission at round start time
            results_job_id = f"results_r{rnd.id}"
            self._scheduler.add_job(
                _result_submission_job_wrapper,
                trigger=DateTrigger(run_date=scheduled_at, timezone="UTC"),
                id=results_job_id,
                replace_existing=True,
                name=f"Result submission for round {rnd.id}",
                kwargs={"round_id": rnd.id},
            )
            log.info("Scheduled %s at %s", results_job_id, scheduled_at.isoformat())
            return

        scheduled_at = rnd.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        horizons = {
            1: scheduled_at - timedelta(days=phase_1_days),
            2: scheduled_at - timedelta(days=phase_2_days),
            3: scheduled_at - timedelta(hours=phase_3_hours),
        }

        for phase_num, fire_at in horizons.items():
            job_id = f"phase{phase_num}_r{rnd.id}"
            if self._phase_callbacks.get(phase_num) is None:
                log.warning("No callback registered for phase %s; skipping job.", phase_num)
                continue

            self._scheduler.add_job(
                _phase_job,
                trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
                id=job_id,
                replace_existing=True,
                name=f"Phase {phase_num} for round {rnd.id}",
                kwargs={"phase_num": phase_num, "round_id": rnd.id},
            )
            log.info("Scheduled %s at %s", job_id, fire_at.isoformat())

        # Schedule post-race Phase 3 cleanup: +24 h after round start
        cleanup_fire_at = scheduled_at + timedelta(hours=24)
        cleanup_job_id = f"cleanup_r{rnd.id}"
        self._scheduler.add_job(
            _forecast_cleanup_job,
            trigger=DateTrigger(run_date=cleanup_fire_at, timezone="UTC"),
            id=cleanup_job_id,
            replace_existing=True,
            name=f"Forecast cleanup for round {rnd.id}",
            kwargs={"round_id": rnd.id},
        )
        log.info("Scheduled %s at %s", cleanup_job_id, cleanup_fire_at.isoformat())

        # Schedule result submission job at round start time (FR-010)
        results_job_id = f"results_r{rnd.id}"
        self._scheduler.add_job(
            _result_submission_job_wrapper,
            trigger=DateTrigger(run_date=scheduled_at, timezone="UTC"),
            id=results_job_id,
            replace_existing=True,
            name=f"Result submission for round {rnd.id}",
            kwargs={"round_id": rnd.id},
        )
        log.info("Scheduled %s at %s", results_job_id, scheduled_at.isoformat())

    def schedule_attendance_round(
        self,
        rnd: Round,
        *,
        notice_days: int,
        last_notice_hours: int,
        deadline_hours: int,
    ) -> None:
        """Register RSVP DateTrigger jobs for *rnd* (attendance module).

        Jobs are only created for rounds whose fire time is in the future.
        ``replace_existing=True`` makes re-scheduling amended rounds safe.

        Args:
            notice_days:       Days before round to post RSVP embed.
            last_notice_hours: Hours before round for last-notice ping (0 = disabled, FR-030).
            deadline_hours:    Hours before round for distribution deadline.
        """
        scheduled_at = rnd.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)

        # Notice job
        notice_fire_at = scheduled_at - timedelta(days=notice_days)
        notice_job_id = f"rsvp_notice_r{rnd.id}"
        if notice_fire_at > now:
            self._scheduler.add_job(
                _rsvp_notice_job,
                trigger=DateTrigger(run_date=notice_fire_at, timezone="UTC"),
                id=notice_job_id,
                replace_existing=True,
                name=f"RSVP notice for round {rnd.id}",
                kwargs={"round_id": rnd.id},
            )
            log.info("Scheduled %s at %s", notice_job_id, notice_fire_at.isoformat())
        else:
            log.info("Skipping %s — fire time %s is in the past", notice_job_id, notice_fire_at.isoformat())

        # Last-notice job (FR-030: only when last_notice_hours > 0)
        if last_notice_hours > 0:
            last_notice_fire_at = scheduled_at - timedelta(hours=last_notice_hours)
            last_notice_job_id = f"rsvp_last_notice_r{rnd.id}"
            if last_notice_fire_at > now:
                self._scheduler.add_job(
                    _rsvp_last_notice_job,
                    trigger=DateTrigger(run_date=last_notice_fire_at, timezone="UTC"),
                    id=last_notice_job_id,
                    replace_existing=True,
                    name=f"RSVP last-notice for round {rnd.id}",
                    kwargs={"round_id": rnd.id},
                )
                log.info("Scheduled %s at %s", last_notice_job_id, last_notice_fire_at.isoformat())
            else:
                log.info(
                    "Skipping %s — fire time %s is in the past",
                    last_notice_job_id, last_notice_fire_at.isoformat(),
                )

        # Deadline job
        deadline_hours_actual = deadline_hours if deadline_hours > 0 else 0
        deadline_fire_at = scheduled_at - timedelta(hours=deadline_hours_actual)
        deadline_job_id = f"rsvp_deadline_r{rnd.id}"
        if deadline_fire_at > now:
            self._scheduler.add_job(
                _rsvp_deadline_job,
                trigger=DateTrigger(run_date=deadline_fire_at, timezone="UTC"),
                id=deadline_job_id,
                replace_existing=True,
                name=f"RSVP deadline for round {rnd.id}",
                kwargs={"round_id": rnd.id},
            )
            log.info("Scheduled %s at %s", deadline_job_id, deadline_fire_at.isoformat())
        else:
            log.info("Skipping %s — fire time %s is in the past", deadline_job_id, deadline_fire_at.isoformat())

    def cancel_round(self, round_id: int) -> None:
        """Remove all phase jobs, the mystery-notice job, the cleanup job, and RSVP jobs for *round_id*."""
        for job_id in (
            f"phase1_r{round_id}",
            f"phase2_r{round_id}",
            f"phase3_r{round_id}",
            f"mystery_r{round_id}",
            f"cleanup_r{round_id}",
            f"results_r{round_id}",
            f"rsvp_notice_r{round_id}",
            f"rsvp_last_notice_r{round_id}",
            f"rsvp_deadline_r{round_id}",
        ):
            try:
                self._scheduler.remove_job(job_id)
                log.info("Removed job %s", job_id)
            except Exception:
                pass  # Job may not exist if it already fired or was never scheduled

    async def cancel_all_weather_for_server(self, server_id: int) -> None:
        """Cancel all weather (phase) jobs for every round in active/setup seasons of *server_id*."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT r.id FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id "
                "WHERE s.server_id = ? AND s.status IN ('ACTIVE', 'SETUP')",
                (server_id,),
            )
            rows = await cursor.fetchall()
        for row in rows:
            self.cancel_round(row[0])

    def schedule_all_rounds(
        self,
        rounds: list[Round],
        *,
        phase_1_days: int = 5,
        phase_2_days: int = 2,
        phase_3_hours: int = 2,
    ) -> None:
        """Schedule all rounds in *rounds*."""
        for rnd in rounds:
            self.schedule_round(
                rnd,
                phase_1_days=phase_1_days,
                phase_2_days=phase_2_days,
                phase_3_hours=phase_3_hours,
            )

    def schedule_result_submission_jobs(self, rounds: list[Round]) -> None:
        """Schedule only the result-submission job for each round.

        Used when the results module is enabled but the weather module is not,
        so ``schedule_round`` (which creates all weather + results jobs together)
        was not called at approval time.
        """
        for rnd in rounds:
            scheduled_at = rnd.scheduled_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            job_id = f"results_r{rnd.id}"
            self._scheduler.add_job(
                _result_submission_job_wrapper,
                trigger=DateTrigger(run_date=scheduled_at, timezone="UTC"),
                id=job_id,
                replace_existing=True,
                name=f"Result submission for round {rnd.id}",
                kwargs={"round_id": rnd.id},
            )
            log.info("Scheduled %s at %s", job_id, scheduled_at.isoformat())

    def cancel_job(self, job_id: str) -> None:
        """Remove a single job from the scheduler by ID (no-op if not found)."""
        try:
            self._scheduler.remove_job(job_id)
            log.info("Removed job %s", job_id)
        except Exception:
            pass  # Already fired or never scheduled

    def get_pending_advance_jobs(self, round_ids: set[int]) -> list[dict]:
        """Return un-fired phase/results/mystery jobs for the given round IDs.

        Used by the test-mode advance command to determine what the scheduler has
        actually queued — so advance only fires events that a live season would
        fire (respecting which modules are enabled).

        Returns a list of dicts sorted by ``(next_run_time, round_id, phase_number)``:
          - ``job_id``       — APScheduler job ID string
          - ``round_id``     — round this job belongs to
          - ``phase_number`` — 0=mystery notice, 1/2/3=weather, 4=result submission
          - ``next_run_time``— datetime when the job is scheduled to fire

        Cleanup and season-end jobs are excluded.
        Jobs that are paused (``next_run_time is None``) are excluded.
        """
        # result submission (results_r) is intentionally excluded here.
        # For test-mode advance, result submission is detected via DB state
        # (rounds_with_results fallback) so that past-dated results_r jobs
        # that already auto-fired don't block or double-trigger the wizard.
        _PHASE_PREFIX_MAP = {
            "mystery":          0,
            "phase1":           1,
            "phase2":           2,
            "phase3":           3,
            "rsvp_notice":      5,
            "rsvp_last_notice": 6,
            "rsvp_deadline":    7,
        }
        result: list[dict] = []
        for job in self._scheduler.get_jobs():
            if job.next_run_time is None:
                continue
            job_id: str = job.id
            if "_r" not in job_id:
                continue
            try:
                prefix, round_str = job_id.rsplit("_r", 1)
                round_id = int(round_str)
            except ValueError:
                continue
            if round_id not in round_ids:
                continue
            phase = _PHASE_PREFIX_MAP.get(prefix)
            if phase is None:
                continue  # cleanup, season_end, etc.
            result.append(
                {
                    "job_id": job_id,
                    "round_id": round_id,
                    "phase_number": phase,
                    "next_run_time": job.next_run_time,
                }
            )
        result.sort(key=lambda x: (x["next_run_time"], x["round_id"], x["phase_number"]))
        return result

    def get_job_ids_for_rounds(self, round_ids: set[int]) -> set[str]:
        """Return all currently-scheduled (non-paused) job IDs that belong to
        the given round IDs.  Unlike ``get_pending_advance_jobs``, no prefix
        filtering is applied — ``results_r``, ``cleanup_r``, etc. are all
        included.  Used by the review summary to distinguish "job queued" from
        "job absent" for each pending phase.
        """
        result: set[str] = set()
        for job in self._scheduler.get_jobs():
            if job.next_run_time is None:
                continue
            if "_r" not in job.id:
                continue
            try:
                _, round_str = job.id.rsplit("_r", 1)
                if int(round_str) in round_ids:
                    result.add(job.id)
            except ValueError:
                continue
        return result

    # ------------------------------------------------------------------
    # Season-end scheduling
    # ------------------------------------------------------------------

    def schedule_season_end(
        self,
        server_id: int,
        fire_at: datetime,
        season_id: int,
    ) -> None:
        """Schedule a one-shot season-end job for *server_id* at *fire_at*.

        Uses the module-level ``_season_end_job`` callable (picklable by
        SQLAlchemyJobStore) with ``server_id`` and ``season_id`` as kwargs.
        Uses ``replace_existing=True`` so calling this a second time simply
        moves the job forward.
        """
        job_id = f"season_end_{server_id}"
        self._scheduler.add_job(
            _season_end_job,
            trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            name=f"Season end for server {server_id}",
            kwargs={"server_id": server_id, "season_id": season_id},
        )
        log.info("Scheduled season_end_%s at %s", server_id, fire_at.isoformat())

    def cancel_season_end(self, server_id: int) -> None:
        """Remove the season-end job for *server_id* if it exists."""
        job_id = f"season_end_{server_id}"
        try:
            self._scheduler.remove_job(job_id)
            log.info("Removed season_end job for server %s", server_id)
        except Exception:
            pass  # Already fired or never scheduled

    # ------------------------------------------------------------------
    # Signup auto-close scheduling
    # ------------------------------------------------------------------

    def register_signup_close_callback(self, callback: Callable) -> None:
        """Register the async callable invoked when the signup close timer fires.

        The callable must accept ``(server_id: int)``.
        Called from bot.py on_ready after the scheduler is started.
        """
        self._signup_close_callback = callback

    def schedule_signup_close_timer(self, server_id: int, close_at_iso: str) -> None:
        """Schedule a one-shot signup auto-close job for *server_id* at the
        given ISO 8601 UTC timestamp string.

        Uses ``replace_existing=True`` so calling this again re-arms the timer.
        """
        fire_at = datetime.fromisoformat(close_at_iso).replace(tzinfo=timezone.utc)
        job_id = f"signup_close_{server_id}"
        self._scheduler.add_job(
            _signup_close_timer_job,
            trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            name=f"Signup auto-close for server {server_id}",
            kwargs={"server_id": server_id},
        )
        log.info("Scheduled signup_close_%s at %s", server_id, fire_at.isoformat())

    def cancel_signup_close_timer(self, server_id: int) -> None:
        """Remove the signup close timer for *server_id* if it exists."""
        job_id = f"signup_close_{server_id}"
        try:
            self._scheduler.remove_job(job_id)
            log.info("Removed signup_close job for server %s", server_id)
        except Exception:
            pass  # Already fired or never scheduled
