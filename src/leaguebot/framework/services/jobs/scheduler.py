"""SchedulerService — APScheduler wrapper for the bot's timed jobs.

Uses SQLAlchemyJobStore backed by **its own** SQLite file, separate from the league
database, so jobs survive restarts. A job whose moment passed while the bot was stopped runs
on start only if that moment is less than five minutes gone (`_GRACE_SECONDS`, the misfire grace
a job has unless it is armed with none). Later than that, APScheduler skips it with a warning in
the host's log, and whatever should be caught up after a longer stop is caught up by each
module's start-up steps, signed up by its wiring. A one-off timer, an overdue results submission,
a round's forecast phases and clean-up, and a round's four check-in timers are armed with no
lateness limit, and fire at once on start whatever the stop. What becomes of a job missed in a
longer stop is its kind's handler's to decide (`docs/design/architecture.md`, "Timed work and
restarts").

The separation is deliberate and is not tidiness. `SQLAlchemyJobStore` is synchronous, and
it is attached to an `AsyncIOScheduler`, so every job it adds, updates or removes writes to
SQLite **on the event-loop thread** — blocking every other coroutine for the duration,
including the HTTP send that answers a Discord interaction. Pointed at the league database
that also carries hundreds of `aiosqlite` readers, those writes contended with the very
traffic they were stalling. Given its own file the contention is gone, and the remaining
stall is one small write to an uncontended database.

**This file's module path is pinned.** The job store pickles each timed job with the path of the
runner it calls (`leaguebot.framework.services.jobs.scheduler`), so moving the file would leave every job stored
before the move, and the state test mode keeps, unable to load.
`test_every_stored_job_names_the_runner_by_its_pinned_module_path` holds it.
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Iterator
from typing import Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_PAUSED
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

log = logging.getLogger(__name__)

_GRACE_SECONDS = 300  # 5-minute misfire grace period

class _JobScheduler(AsyncIOScheduler):
    """An `AsyncIOScheduler` whose jobs run in a context of their own.

    APScheduler arms its timer, and starts each job's task, through calls that copy the context
    of whoever last woke it. A job added from inside a member's command would therefore run
    with that command's `framework.utils.answering` record, and a line it writes that the log channel
    refuses would warn that member, long after, about a line that is not theirs. Waking the
    scheduler in an empty context cuts that: a job answers no interaction, and the host's log
    alone records its failure (`docs/wip-specs/core_specification.md`, "The record of what
    changed"). Pinned by
    `test_a_job_scheduled_from_an_admitted_interaction_finds_no_member_to_warn`.
    """

    def wakeup(self) -> None:
        contextvars.Context().run(super().wakeup)


# Module-level service reference so APScheduler can pickle the job callable.
# Set in SchedulerService.start(); always non-None when jobs fire.
_GLOBAL_SERVICE: "SchedulerService | None" = None


async def _one_off_timer_job(kind: str, key: int | str | None = None) -> None:
    """The one module-level APScheduler callable every job the scheduler arms runs through —
    picklable for the job store, and named for the one-off timer it first ran.

    Looks up the handler the builder registered for *kind* (`SchedulerService.register_timer`)
    and awaits it, handed *key* (the round id of a round's job, the key of a one-off timer), which
    reads its row again and asks its change. A standing job (the amendment sweep) is armed with no
    key, and its handler is handed nothing. A kind with no handler is passed over with a warning, and a handler that raises is logged with
    its traceback, naming the kind and the key: a job's fault belongs to its host's log, and
    nothing reaches APScheduler, so a standing job outlives one bad run. Core holds no module's
    rule about what a job does, only which handler is asked.
    """
    if _GLOBAL_SERVICE is None:
        log.warning("_one_off_timer_job %s fired but _GLOBAL_SERVICE is None — skipping", kind)
        return
    handler = _GLOBAL_SERVICE._timer_callbacks.get(kind)
    if handler is None:
        log.warning("_one_off_timer_job: no handler registered for %s — skipping", kind)
        return
    try:
        outcome = handler() if key is None else handler(key)
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:  # noqa: BLE001 — one job's fault must not reach the scheduler
        log.exception("_one_off_timer_job: the %s job for %s failed", kind, key)


@dataclass(frozen=True)
class StoredJob:
    """One job the store holds, as `SchedulerService.jobs` lists it: its id, the kind whose handler
    it runs, the key that handler is handed (None for a standing job) and when it next runs (None
    for a job that is paused)."""

    job_id: str
    kind: str | None
    key: int | str | None
    next_run: datetime | None


def default_jobstore_path(db_path: str) -> str:
    """Where the job store lives when nothing names it: `scheduler.db` beside *db_path*.

    Derived from the league database rather than fixed, so a relative `DB_PATH=bot.db`
    keeps working and an absolute one puts the pair together.
    """
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "scheduler.db")


def prepare_jobstore(jobstore_path: str) -> None:
    """Put the job store into WAL before SQLAlchemy opens it.

    `run_migrations` puts the *league* database into WAL; nothing reaches this file, because
    APScheduler brings its own SQLAlchemy connections. Without this the job store would keep
    the default rollback journal, which on this host's SD card costs ~11 ms a commit against
    ~6 ms — and those milliseconds are spent on the event-loop thread, which is the whole
    reason the job store was moved out in the first place.

    `journal_mode` persists in the file header, so this runs once at startup and every later
    SQLAlchemy connection inherits it. `synchronous` is left at FULL here for the same
    reason it is on the league database: a scheduled job lost to a power cut is worse than a
    slower commit. A file that will not take WAL is not fatal.
    """
    os.makedirs(os.path.dirname(os.path.abspath(jobstore_path)), exist_ok=True)
    conn = sqlite3.connect(jobstore_path)
    try:
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
        mode = str(mode[0]).lower() if mode else "unknown"
        if mode != "wal":
            log.warning(
                "Could not enable WAL on the job store — it reports journal_mode=%s.", mode
            )
    finally:
        conn.close()


class SchedulerService:
    """The one place a job is made, found or removed (`docs/design/architecture.md`, "Timed
    work and restarts").

    Every cancel is a clean-up, and removing a job never raises. A job that has already fired, or
    was never armed, is the ordinary case and what the caller wanted: APScheduler says so with
    `JobLookupError`, which is caught by name and passed over in silence. Any other failure — a job
    store that cannot be read or written — is a real fault, and is logged as a warning with its
    traceback and then swallowed. That broad handler is a stated exception in architecture.md's
    "Errors and failures": every cancel is a clean-up, so a job that will not go is logged and left,
    whether the caller is a teardown list (a season cancelled, a module switched off, signups
    closed, a pack), where it must not stop the rest, or a single cancel. `cancel_all` counts only
    the jobs that went. A cancel that first lists the store (`cancel_all`) does raise where that
    cannot be read, before it removes anything."""

    def __init__(self, db_path: str, jobstore_path: str | None = None) -> None:
        self._db_path = db_path
        self._jobstore_path = jobstore_path or default_jobstore_path(db_path)
        prepare_jobstore(self._jobstore_path)
        jobstore_url = f"sqlite:///{self._jobstore_path}"
        jobstore = SQLAlchemyJobStore(url=jobstore_url)
        self._scheduler = _JobScheduler(
            jobstores={"default": jobstore},
            job_defaults={"misfire_grace_time": _GRACE_SECONDS},
            timezone="UTC",
        )
        # Every job's handler, by kind, registered by the builder before the scheduler starts
        self._timer_callbacks: dict[str, Callable] = {}

    def register_timer(self, kind: str, callback: Callable) -> None:
        """Register the callable invoked when a job of *kind* fires: the one way to sign up a kind.

        It is handed the job's key (a round's id, a one-off timer's key), or nothing where the job
        is a standing one with no key. The builder registers each kind before the scheduler
        starts, so that a job missed while the bot was down finds its handler when it fires on
        start.
        """
        self._timer_callbacks[kind] = callback

    @staticmethod
    def _timer_id(kind: str, key: int | str) -> str:
        return f"once:{kind}:{key}"

    def arm_once(self, kind: str, key: int | str, run_at: datetime) -> None:
        """Arm the one-off timer of *kind* for *key* to fire at *run_at* (aware, UTC).

        A timer armed again for the same kind and key replaces the earlier. It is armed with no
        lateness limit (`misfire_grace_time=None`), so one missed while the bot was down fires
        when it starts (architecture.md, "Timed work and restarts"). It is armed by a job of its
        own, after the job whose record saved its row, never inside a record.
        """
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=DateTrigger(run_date=run_at, timezone="UTC"),
            id=self._timer_id(kind, key),
            replace_existing=True,
            misfire_grace_time=None,
            name=f"One-off {kind} {key}",
            kwargs={"kind": kind, "key": key},
        )
        log.info("Armed the %s timer for %s at %s", kind, key, run_at.isoformat())

    def arm_at(
        self, job_id: str, kind: str, key: int | str, run_at: datetime, name: str
    ) -> None:
        """Arm the job *job_id* to run the handler of *kind* with *key* at *run_at* (aware, UTC).

        The scheduler names no kind of job: its caller hands the id, the kind and the key. A job
        armed again under the same id replaces the earlier, and is armed with no lateness limit
        (`misfire_grace_time=None`), as `arm_once` is.
        """
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=DateTrigger(run_date=run_at, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=None,
            name=name,
            kwargs={"kind": kind, "key": key},
        )
        log.info("Scheduled %s at %s", job_id, run_at.isoformat())

    def run_now(self, job_id: str, kind: str, key: int | str, name: str) -> None:
        """Run the handler of *kind* with *key* at once, as the job *job_id* would at its moment.

        The job replaces one of the same id, and is armed with no trigger, which APScheduler runs
        at once, and with no lateness limit, so that a restart before it runs still runs it.
        """
        self._scheduler.add_job(
            _one_off_timer_job,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=None,
            name=name,
            kwargs={"kind": kind, "key": key},
        )
        log.info("Running %s at once", job_id)

    def arm_daily(self, job_id: str, kind: str, hour: int, minute: int, name: str) -> None:
        """Arm the standing job *job_id* to run the handler of *kind*, handed nothing, every day
        at *hour*:*minute* UTC.

        **A recurring trigger, and deliberately so.** A standing instruction is not an event, and
        re-arming it from inside its own callback would put the survival of the schedule at the
        mercy of the job body: a run that raised would silently be the last. A ``CronTrigger`` is
        re-armed by APScheduler itself and outlives a failing run. The time is read as UTC: a
        stored local time would need a zone stored with it and would drift against daylight saving.
        It is armed with no lateness limit (``misfire_grace_time=None``), so that what becomes of a
        run missed while the bot was down is the handler's to judge. A job armed again under the
        same id replaces the earlier.
        """
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=None,
            name=name,
            kwargs={"kind": kind},
        )
        log.info("Scheduled %s at %02d:%02d UTC daily", job_id, hour, minute)

    def arm_every(self, job_id: str, kind: str, minutes: int, name: str) -> None:
        """Arm the standing job *job_id* to run the handler of *kind*, handed nothing, every
        *minutes* minutes.

        An ``IntervalTrigger`` for the reason `arm_daily` gives for its cron one. It is armed
        with no lateness limit (``misfire_grace_time=None``), so a run due while the bot was down
        is made when it is back. A job armed again under the same id replaces the earlier, so a
        restart re-arms rather than duplicates it.
        """
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=IntervalTrigger(minutes=minutes),
            id=job_id,
            replace_existing=True,
            misfire_grace_time=None,
            name=name,
            kwargs={"kind": kind},
        )
        log.info("Scheduled %s every %d minutes", job_id, minutes)

    def jobs(self) -> list[StoredJob]:
        """Every job the store holds, by its id, kind, key and next run: the one way to look at
        what stands without reaching past the scheduler. Raises where the store cannot be read."""
        return [
            StoredJob(
                job_id=job.id,
                kind=job.kwargs.get("kind"),
                key=job.kwargs.get("key"),
                next_run=getattr(job, "next_run_time", None),
            )
            for job in self._scheduler.get_jobs()
        ]

    def remove(self, job_id: str) -> bool:
        """Remove *job_id* and say whether it went; never raises (see the class docstring).

        A job already gone is passed over in silence, and a store fault is logged and passed over,
        both answering False, as `_remove_job` does: this is its public form.
        """
        return self._remove_job(job_id)

    def disarm(self, kind: str, key: int | str) -> None:
        """Take away the one-off timer of *kind* for *key*, if it stands."""
        self._remove_job(self._timer_id(kind, key))

    def timer_due(self, kind: str, key: int | str) -> datetime | None:
        """When the one-off timer of *kind* for *key* is next due, or None where none stands."""
        return self.job_due(self._timer_id(kind, key))

    def job_due(self, job_id: str) -> datetime | None:
        """When the job *job_id* is next due, or None where none stands or it will not fire."""
        job = self._scheduler.get_job(job_id)
        return None if job is None else getattr(job, "next_run_time", None)

    @property
    def jobstore_path(self) -> str:
        """The file the job store lives in, which is never the league database."""
        return self._jobstore_path

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Hold the scheduler still for the block, resuming it however the block ends.

        A scheduler that is not running is left as it is, neither paused nor resumed: resuming
        one nobody paused would start one the bot deliberately left down. So is one that is
        paused already: the scheduler start-up holds paused while its steps run is left paused,
        since only start-up's last act may let the jobs run. It is for the copy of
        the job store (a backup, a factory reset's, the approval's), where the pause keeps
        every job from firing while the copy is taken. It is not what keeps the copy whole:
        a copy is synchronous on the event-loop thread, which also keeps ``add_job`` out, and
        the copy goes through SQLite's backup API besides.
        """
        # APScheduler 3 reports a paused scheduler as running, so the state is read besides.
        holds = self._scheduler.running and self._scheduler.state != STATE_PAUSED
        if holds:
            self._scheduler.pause()
        try:
            yield
        finally:
            if holds:
                self._scheduler.resume()

    def start(self, *, paused: bool = False) -> None:
        """Start the scheduler, and with *paused* start it holding every job still.

        A paused scheduler reads the job store (``has_job``, ``get_jobs`` and ``remove_job`` see
        what is stored) and fires nothing until :meth:`resume`, which is how start-up keeps a
        job from running before the start-up sweep and the change queue are done."""
        global _GLOBAL_SERVICE
        _GLOBAL_SERVICE = self
        if not self._scheduler.running:
            self._scheduler.start(paused=paused)
            log.info(
                "APScheduler started%s with SQLAlchemyJobStore at %s (league data: %s)",
                " paused" if paused else "",
                self._jobstore_path,
                self._db_path,
            )

    def resume(self) -> None:
        """Let a scheduler started paused run its jobs, those that fell due while it was held
        included."""
        self._scheduler.resume()
        log.info("APScheduler resumed")

    def shutdown(self, wait: bool = True) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=wait)

    def _remove_job(self, job_id: str) -> bool:
        """Remove *job_id*, and say whether it went; never raises (see the class docstring).

        A job already gone is passed over in silence. Any other failure is logged as a warning
        with its traceback, so that a real job-store fault can be found, and a teardown goes on
        past one job that will not go.
        """
        try:
            self._scheduler.remove_job(job_id)
        except JobLookupError:
            return False  # Already fired, or never scheduled
        except Exception:  # noqa: BLE001 — a teardown list goes on past one job that will not go
            log.warning("Could not remove scheduled job %s", job_id, exc_info=True)
            return False
        return True

    def has_job(self, job_id: str) -> bool:
        """Whether the job *job_id* stands, armed and not yet fired."""
        return self._scheduler.get_job(job_id) is not None

    def cancel_job(self, job_id: str) -> None:
        """Remove a single job from the scheduler by ID (no-op if not found)."""
        if self._remove_job(job_id):
            log.info("Removed job %s", job_id)

    def cancel_all(self, *, keep: frozenset[str] = frozenset()) -> int:
        """Remove every job but those whose ids are in *keep*, and return how many went.

        For `/bot pack` and `/bot factory-reset`, which clear all of the league's scheduled work at
        once rather than round by round, and so reach the jobs no round owns — a wizard's inactivity
        and channel-delete jobs, a stale season-end job. Pack keeps the jobs the modules hand it to
        keep.
        """
        removed = 0
        for job in list(self._scheduler.get_jobs()):
            if job.id in keep:
                continue
            if self._remove_job(job.id):
                removed += 1
        log.info("Removed %d scheduled job(s), keeping %s", removed, sorted(keep) or "none")
        return removed
