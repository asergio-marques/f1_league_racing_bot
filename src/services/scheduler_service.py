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

from models.round import Round, RoundFormat

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_GRACE_SECONDS = 300  # 5-minute misfire grace period

# Module-level service reference so APScheduler can pickle the job callable.
# Set in SchedulerService.start(); always non-None when jobs fire.
_GLOBAL_SERVICE: "SchedulerService | None" = None


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

    def schedule_round(self, rnd: Round) -> None:
        """Register Phase 1/2/3 DateTrigger jobs for *rnd*.

        MYSTERY rounds: no phases scheduled.
        Jobs use replace_existing=True so re-scheduling an amended round is safe.
        """
        if rnd.format == RoundFormat.MYSTERY:
            log.info("Round %s is MYSTERY — no weather phases scheduled.", rnd.id)
            return

        scheduled_at = rnd.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        horizons = {
            1: scheduled_at - timedelta(days=5),
            2: scheduled_at - timedelta(days=2),
            3: scheduled_at - timedelta(hours=2),
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

    def cancel_round(self, round_id: int) -> None:
        """Remove all three phase jobs for *round_id*."""
        for phase_num in (1, 2, 3):
            job_id = f"phase{phase_num}_r{round_id}"
            try:
                self._scheduler.remove_job(job_id)
                log.info("Removed job %s", job_id)
            except Exception:
                pass  # Job may not exist if it already fired

    def schedule_all_rounds(self, rounds: list[Round]) -> None:
        """Schedule all rounds in *rounds*."""
        for rnd in rounds:
            self.schedule_round(rnd)

    # ------------------------------------------------------------------
    # Season-end scheduling
    # ------------------------------------------------------------------

    def schedule_season_end(
        self,
        server_id: int,
        fire_at: datetime,
        callback: Callable,
    ) -> None:
        """Schedule a one-shot season-end job for *server_id* at *fire_at*.

        Uses ``replace_existing=True`` so calling this a second time (e.g.
        after a test-suite re-seed) simply moves the job forward.
        """
        job_id = f"season_end_{server_id}"
        self._scheduler.add_job(
            callback,
            trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            name=f"Season end for server {server_id}",
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
