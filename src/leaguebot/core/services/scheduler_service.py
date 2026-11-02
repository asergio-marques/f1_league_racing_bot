"""SchedulerService — APScheduler wrapper for phase job management.

Uses SQLAlchemyJobStore backed by **its own** SQLite file, separate from the league
database, so jobs survive restarts. A job whose moment passed while the bot was stopped runs
on start only if that moment is less than five minutes gone (`_GRACE_SECONDS`, the misfire grace
a job has unless it is armed with none). Later than that, APScheduler skips it with a warning in
the host's log, and whatever should be caught up after a longer stop is caught up by the start-up
recovery in `__main__.py`. A one-off timer and an overdue results submission are armed with no
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
"""

from __future__ import annotations

import contextvars
import inspect
import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from collections.abc import Collection, Iterable, Iterator
from typing import TYPE_CHECKING, Any, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.base import STATE_PAUSED
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import Round, RoundFormat

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_GRACE_SECONDS = 300  # 5-minute misfire grace period

#: How long after a round's scheduled start its posted messages come down: the weather module's
#: forecast, whichever of Phases 1 to 3 still stands, and the attendance module's check-in call
#: with its last notice and its distribution message (#425). One figure for both, so a round's
#: channels clear together.
POST_RACE_CLEANUP_DELAY = timedelta(hours=24)


class _JobScheduler(AsyncIOScheduler):
    """An `AsyncIOScheduler` whose jobs run in a context of their own.

    APScheduler arms its timer, and starts each job's task, through calls that copy the context
    of whoever last woke it. A job added from inside a member's command would therefore run
    with that command's `core.utils.answering` record, and a line it writes that the log channel
    refuses would warn that member, long after, about a line that is not theirs. Waking the
    scheduler in an empty context cuts that: a job answers no interaction, and the host's log
    alone records its failure (`docs/wip-specs/core_specification.md`, "The record of what
    changed"). Pinned by
    `test_a_job_scheduled_from_an_admitted_interaction_finds_no_member_to_warn`.
    """

    def wakeup(self) -> None:
        contextvars.Context().run(super().wakeup)


# Regex that matches the ``_s{S}_d{D}_r{R}_id{round_id}`` suffix appended to every
# round-scoped job ID. Used to extract the event-type prefix for dispatch.
#
# ``_id{round_id}`` is optional because the job store is a real file that outlives a
# restart: jobs written before the id was added are still in it, and a regex that
# insisted on the id would fail to read their prefix — which is how a job's owning
# module is identified, so weather's disable would stop filtering and take the other
# modules' jobs down with it.
_JOB_SUFFIX_RE = re.compile(r"_s\d+_d\d+_r\d+(?:_id\d+)?$")

# The event-type prefixes of the jobs the **weather module** owns: its three forecast
# phases and the post-race cleanup that deletes what they posted.
#
# Ownership can only be read from the job ID. All nine of a round's jobs — these four,
# the result submission and the four attendance jobs — carry the same ``key`` kwarg (the round id), so
# the kwarg says which round a job belongs to and nothing about which module it is for.
# The prefix is the only marker there is, and ``_JOB_SUFFIX_RE`` above is what exposes it.
_WEATHER_JOB_PREFIXES = frozenset({"weather_p1", "weather_p2", "weather_p3", "cleanup"})


def _round_job_suffix(rnd: "Round", season_number: int, division_tier: int) -> str:
    """The ``_s{S}_d{D}_r{R}_id{round_id}`` tail every one of a round's job IDs carries.

    **Why the round id is in there** — a job ID must be unique to its round for the life of
    that round, and the season, tier and round number are not. `renumber_rounds` rewrites the
    numbers of a whole division whenever an amendment changes the order of its rounds, and
    nothing re-arms the jobs of the rounds that merely shifted. So a round that became number 4
    kept jobs saying ``r5``, and the next amendment of the round that became number 5 scheduled
    ``..._r5`` on top of them — and because every ``add_job`` here passes
    ``replace_existing=True``, that silently destroyed the first round's entire schedule: its
    forecasts, its result submission and its check-in, with nothing reporting it.

    The round id never changes, so the collision cannot happen. The season, tier and number stay
    in the ID because they are what makes it readable in an APScheduler admin view; they are
    decoration, and only the id is load-bearing. A number left stale by a renumbering is
    therefore cosmetic and is corrected the next time that round is scheduled.
    """
    return f"_s{season_number}_d{division_tier}_r{rnd.round_number}_id{rnd.id}"


def _job_event_type(job_id: str) -> str | None:
    """The event type of a round's job — its ID less the `_round_job_suffix` tail — or None.

    The one reader of the scheme `_round_job_suffix` writes. None stands for an ID carrying no
    round suffix, whose owner cannot be read: a server-scoped job such as the portrait refresh, or
    one from a version of the bot that named its jobs otherwise.

    Every caller asking what a job is for asks here, and pairs the answer with the job's
    round id the job is keyed by (`_round_of`) — never rebuilding an ID to look one up. The review summary once built
    IDs of its own, in a shape no job has ever had, and so found none of them (#426).
    """
    m = _JOB_SUFFIX_RE.search(job_id)
    return None if m is None else job_id[: m.start()]


# Module-level service reference so APScheduler can pickle the job callable.
# Set in SchedulerService.start(); always non-None when jobs fire.
_GLOBAL_SERVICE: "SchedulerService | None" = None


#: One league, so one signup window at a time and one timer to close it.
SIGNUP_CLOSE_JOB_ID = "signup_close"

#: The kinds of the standing and one-shot jobs no round owns, named for what they do. Each is
#: signed up with `SchedulerService.register_timer`, and stored on its job as ``kind``.
SIGNUP_CLOSE_KIND = "signup_close"
PORTRAIT_REFRESH_KIND = "portrait_refresh"
AMENDMENT_SWEEP_KIND = "amendment_sweep"

#: The kinds of a round's nine jobs: each is its job id's prefix (`_job_event_type`), and its key
#: the round id. A job is a round's where its kind is one of these; another job's key, an
#: amendment's lapse timer's for one, is no round's, however it is numbered.
ROUND_JOB_KINDS = frozenset({
    "weather_p1", "weather_p2", "weather_p3", "cleanup", "results",
    "rsvp_notice", "rsvp_last_notice", "rsvp_deadline", "rsvp_cleanup",
})


def _round_of(job: Any) -> int | None:
    """The round a stored job belongs to — its key, where its kind is one of a round's
    (`ROUND_JOB_KINDS`) — or None for any other job."""
    kwargs = job.kwargs
    if kwargs.get("kind") not in ROUND_JOB_KINDS:
        return None
    key = kwargs.get("key")
    return key if isinstance(key, int) else None


async def _one_off_timer_job(kind: str, key: int | str | None = None) -> None:
    """The one module-level APScheduler callable every job the scheduler arms runs through —
    picklable for the job store, and named for the one-off timer it first ran.

    Looks up the handler the builder registered for *kind* (`SchedulerService.register_timer`)
    and awaits it, handed *key* (the round id of a round's job, the key of a one-off timer), which
    reads its row again and asks its change. A standing job (the signup close, the portrait
    refresh, the amendment sweep) is armed with no key, and its handler is handed nothing. A
    kind with no handler is passed over with a warning, and a handler that raises is logged with
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


#: One league, so one daily refresh: the job is named by what it does, not by a server.
PORTRAIT_REFRESH_JOB_ID = "pfp_daily"

#: The standing sweep that undoes an amendment nobody approved (#345).
AMENDMENT_SWEEP_JOB_ID = "amend_sweep"

#: How often it looks. An amendment lapses after
#: ``result_submission_service.AMENDMENT_STAGE_TIMEOUT_SECONDS``; sweeping every five minutes
#: means a lapsed one is undone within five minutes of its deadline rather than at the deadline
#: exactly, which is close enough for a round nobody is working on and cheap — the sweep reads
#: one indexed table and almost always finds nothing.
AMENDMENT_SWEEP_MINUTES = 5


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
    `JobLookupError`, which is caught by name and passed over in silence. Any other failure —
    a job store that cannot be read or written — is a real fault, and is logged as a warning
    with its traceback and then swallowed. That broad handler is the second place
    architecture.md's "Errors and failures" allows one: the callers are teardown lists
    (a season cancelled, a module switched off, signups closed, a pack), where one job that
    will not go must not stop the rest. `cancel_all` counts only the jobs that went. A cancel that
    first lists the store (`cancel_round`, `cancel_rounds`, `cancel_all`, `cancel_season_end`)
    or reads the league database (`cancel_all_weather`) does raise where that cannot be read,
    before it removes anything.
    """

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
        """Register the callable invoked when a job of *kind* fires: the one way a kind is signed up.

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

    # ------------------------------------------------------------------
    # Round scheduling
    # ------------------------------------------------------------------

    def schedule_round(
        self,
        rnd: Round,
        *,
        season_number: int,
        division_tier: int,
        phase_1_days: int = 5,
        phase_2_days: int = 2,
        phase_3_hours: int = 2,
        skip_phases: Collection[int] = frozenset(),
    ) -> None:
        """Register DateTrigger jobs for *rnd*.

        Schedules three weather-phase jobs (``weather_p1`` / ``weather_p2`` /
        ``weather_p3``), a cleanup job, and a result-submission job for every
        round regardless of format.  Every job runs through `_one_off_timer_job`, handed its kind
        (its job id's prefix) and the round id as its key.  Whether a MYSTERY round's phase asks
        for a notice or for nothing is weather's rule, decided by the handler the builder signs
        up for ``weather_p1`` to ``weather_p3``, never here.

        Job IDs follow the convention
        ``<event_type>_s{season_number}_d{division_tier}_r{round_number}_id{round_id}``
        written by `_round_job_suffix` and read back by `_job_event_type`. The season,
        tier and number make them readable in APScheduler admin views; the round id is
        what makes them unique. The round id is always stored as the job's ``key``
        for programmatic lookups (`_round_of`).

        Jobs use ``replace_existing=True`` so re-scheduling an amended round
        is safe.

        Args:
            season_number:  Season number (for human-readable job IDs).
            division_tier:  Division tier (for human-readable job IDs).
            phase_1_days:   Days before round to fire Phase 1 (default 5).
            phase_2_days:   Days before round to fire Phase 2 (default 2).
            phase_3_hours:  Hours before round to fire Phase 3 (default 2).
            skip_phases:    Phases armed no job, for the caller draws them at once itself: an
                            amendment's catch-up (`round_amend_change`). A job for a horizon
                            passed by less than the misfire grace would run at once and draw
                            the phase a second time.
        """
        scheduled_at = rnd.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        _suffix = _round_job_suffix(rnd, season_number, division_tier)

        horizons = {
            1: scheduled_at - timedelta(days=phase_1_days),
            2: scheduled_at - timedelta(days=phase_2_days),
            3: scheduled_at - timedelta(hours=phase_3_hours),
        }

        for phase_num, fire_at in horizons.items():
            if phase_num in skip_phases:
                continue
            job_id = f"weather_p{phase_num}{_suffix}"
            self._scheduler.add_job(
                _one_off_timer_job,
                trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
                id=job_id,
                replace_existing=True,
                name=f"Weather P{phase_num} s{season_number} d{division_tier} r{rnd.round_number}",
                kwargs={"kind": f"weather_p{phase_num}", "key": rnd.id},
            )
            log.info("Scheduled %s at %s", job_id, fire_at.isoformat())

        # Post-race Phase 3 cleanup, a day after round start
        self.schedule_round_cleanups(
            rnd, season_number=season_number, division_tier=division_tier, check_in=False
        )

        # Result submission at round start time
        results_job_id = f"results{_suffix}"
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=DateTrigger(run_date=scheduled_at, timezone="UTC"),
            id=results_job_id,
            replace_existing=True,
            name=f"Result submission s{season_number} d{division_tier} r{rnd.round_number}",
            kwargs={"kind": "results", "key": rnd.id},
        )
        log.info("Scheduled %s at %s", results_job_id, scheduled_at.isoformat())

    def schedule_attendance_round(
        self,
        rnd: Round,
        *,
        season_number: int,
        division_tier: int,
        notice_days: int,
        last_notice_hours: int,
        deadline_hours: int,
    ) -> None:
        """Register RSVP DateTrigger jobs for *rnd* (attendance module).

        Job IDs follow the same ``<event_type>_s{S}_d{D}_r{R}_id{round_id}`` convention
        as ``schedule_round``.  Jobs are only created when their fire time is in
        the future.  ``replace_existing=True`` makes re-scheduling amended
        rounds safe.

        Args:
            season_number:       Season number (for human-readable job IDs).
            division_tier:       Division tier (for human-readable job IDs).
            notice_days:         Days before round to post RSVP embed.
            last_notice_hours:   Hours before round for last-notice ping (0 = disabled).
            deadline_hours:      Hours before round for distribution deadline.

        A fourth job, ``rsvp_cleanup``, takes the round's call, last notice and distribution
        message down `POST_RACE_CLEANUP_DELAY` after the round's start (#425).
        """
        scheduled_at = rnd.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        _suffix = _round_job_suffix(rnd, season_number, division_tier)

        # Notice job
        notice_fire_at = scheduled_at - timedelta(days=notice_days)
        notice_job_id = f"rsvp_notice{_suffix}"
        if notice_fire_at > now:
            self._scheduler.add_job(
                _one_off_timer_job,
                trigger=DateTrigger(run_date=notice_fire_at, timezone="UTC"),
                id=notice_job_id,
                replace_existing=True,
                name=f"RSVP notice s{season_number} d{division_tier} r{rnd.round_number}",
                kwargs={"kind": "rsvp_notice", "key": rnd.id},
            )
            log.info("Scheduled %s at %s", notice_job_id, notice_fire_at.isoformat())
        else:
            log.info("Skipping %s — fire time %s is in the past", notice_job_id, notice_fire_at.isoformat())

        # Last-notice job (only when last_notice_hours > 0)
        if last_notice_hours > 0:
            last_notice_fire_at = scheduled_at - timedelta(hours=last_notice_hours)
            last_notice_job_id = f"rsvp_last_notice{_suffix}"
            if last_notice_fire_at > now:
                self._scheduler.add_job(
                    _one_off_timer_job,
                    trigger=DateTrigger(run_date=last_notice_fire_at, timezone="UTC"),
                    id=last_notice_job_id,
                    replace_existing=True,
                    name=f"RSVP last-notice s{season_number} d{division_tier} r{rnd.round_number}",
                    kwargs={"kind": "rsvp_last_notice", "key": rnd.id},
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
        deadline_job_id = f"rsvp_deadline{_suffix}"
        if deadline_fire_at > now:
            self._scheduler.add_job(
                _one_off_timer_job,
                trigger=DateTrigger(run_date=deadline_fire_at, timezone="UTC"),
                id=deadline_job_id,
                replace_existing=True,
                name=f"RSVP deadline s{season_number} d{division_tier} r{rnd.round_number}",
                kwargs={"kind": "rsvp_deadline", "key": rnd.id},
            )
            log.info("Scheduled %s at %s", deadline_job_id, deadline_fire_at.isoformat())
        else:
            log.info("Skipping %s — fire time %s is in the past", deadline_job_id, deadline_fire_at.isoformat())

        # Cleanup job, a day after the round
        self.schedule_round_cleanups(
            rnd, season_number=season_number, division_tier=division_tier, forecast=False
        )

    def schedule_round_cleanups(
        self,
        rnd: Round,
        *,
        season_number: int,
        division_tier: int,
        forecast: bool = True,
        check_in: bool = True,
    ) -> None:
        """Register the clean-ups that take a round's posts down `POST_RACE_CLEANUP_DELAY` after its start.

        ``cleanup`` deletes the round's weather forecasts and ``rsvp_cleanup`` its check-in call,
        last notice and distribution message; *forecast* and *check_in* choose which are armed
        (``schedule_round`` arms the first alone, ``schedule_attendance_round`` the second, and
        a refused cancellation arms both for a round raced, or ended cancelled as not held,
        #439). The forecast's is armed whatever the hour, as ever; the check-in's only while its
        moment is still ahead, the restart's catch-up taking one already past
        (`_recover_missed_cleanups`). Both replace a job of the same id.
        """
        scheduled_at = rnd.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        suffix = _round_job_suffix(rnd, season_number, division_tier)
        cleanup_fire_at = scheduled_at + POST_RACE_CLEANUP_DELAY

        if forecast:
            cleanup_job_id = f"cleanup{suffix}"
            self._scheduler.add_job(
                _one_off_timer_job,
                trigger=DateTrigger(run_date=cleanup_fire_at, timezone="UTC"),
                id=cleanup_job_id,
                replace_existing=True,
                name=f"Forecast cleanup s{season_number} d{division_tier} r{rnd.round_number}",
                kwargs={"kind": "cleanup", "key": rnd.id},
            )
            log.info("Scheduled %s at %s", cleanup_job_id, cleanup_fire_at.isoformat())

        if check_in:
            rsvp_job_id = f"rsvp_cleanup{suffix}"
            if cleanup_fire_at > datetime.now(timezone.utc):
                self._scheduler.add_job(
                    _one_off_timer_job,
                    trigger=DateTrigger(run_date=cleanup_fire_at, timezone="UTC"),
                    id=rsvp_job_id,
                    replace_existing=True,
                    name=f"RSVP cleanup s{season_number} d{division_tier} r{rnd.round_number}",
                    kwargs={"kind": "rsvp_cleanup", "key": rnd.id},
                )
                log.info("Scheduled %s at %s", rsvp_job_id, cleanup_fire_at.isoformat())
            else:
                log.info("Skipping %s — fire time %s is in the past", rsvp_job_id, cleanup_fire_at.isoformat())

    def cancel_round(self, round_id: int, *, only: frozenset[str] | None = None) -> list[str]:
        """Remove scheduler jobs belonging to *round_id*, and give the ids of those that went.

        The ids let a cancellation refused afterwards know what it took and arm it again (#439).

        Iterates the live jobstore and removes every job of one of a round's kinds
        (`ROUND_JOB_KINDS`) whose ``key`` is *round_id*.  This is format-agnostic, and leaves
        alone a one-off timer of another kind that happens to be keyed with the same number.

        Args:
            only: When given, restricts the removal to jobs whose event type — as
                `_job_event_type` reads it off the job ID — is in the set.  Callers cancelling a *round* want the default,
                which takes all nine of its jobs; a caller switching **one
                module** off wants that module's prefixes and nothing else, or
                it takes the other modules' work down with it (issue #117).
                A job whose ID does not carry the round suffix is left alone
                while *only* is in force, since its owner cannot be read.
        """
        removed: list[str] = []
        for job in self._scheduler.get_jobs():
            if _round_of(job) != round_id:
                continue
            if only is not None:
                event_type = _job_event_type(job.id)
                if event_type is None or event_type not in only:
                    continue
            if self._remove_job(job.id):
                log.info("Removed job %s", job.id)
                removed.append(job.id)
        return removed

    def cancel_rounds(self, round_ids: Iterable[int], *, season_end: bool = False) -> dict[int, list[str]]:
        """Remove the scheduler jobs of every round in *round_ids* in one pass.

        Gives the ids of the jobs removed, by round; a round that had none is left out, and so
        is a ``season_end*`` job taken with *season_end*.

        The store is listed once, before anything is removed, and that listing is the only call
        that can raise: a store that cannot be read removes nothing, so a division's or a
        season's cancellation that unarms its rounds first stands as it was (#439, slice 8).
        Each removal then goes through `_remove_job`, which never raises. With *season_end*,
        any stale ``season_end*`` job the store still carries goes in the same pass.
        """
        wanted = set(round_ids)
        removed: dict[int, list[str]] = {}
        jobs = list(self._scheduler.get_jobs())
        for job in jobs:
            round_id = _round_of(job)
            if round_id in wanted or (season_end and job.id.startswith("season_end")):
                if self._remove_job(job.id):
                    log.info("Removed job %s", job.id)
                    if round_id in wanted:
                        removed.setdefault(round_id, []).append(job.id)
        return removed

    async def cancel_all_weather(self) -> None:
        """Cancel the weather module's jobs for every round in active/setup seasons.

        Only the four jobs in ``_WEATHER_JOB_PREFIXES`` are taken. The other five a round
        carries belong to modules that are still switched on and must survive:

        * ``results`` is not only the results module's. the round's start (``results.submission.open``) is
          the round's one clock-driven status transition, and it runs for every round
          whatever the modules — with results off it simply closes the round as FINAL.
          Cancelled, the round never leaves NOT_RUN, its division never finishes and its
          season can never be completed.
        * ``rsvp_notice``, ``rsvp_last_notice``, ``rsvp_deadline`` and ``rsvp_cleanup`` are
          attendance's, and nothing short of the confirmation of placements recreates them — which
          cannot be run again on an active season, so cancelling them loses the season's check-ins
          for good.

        Do not widen this back to a whole-round cancel: that was issue #117, in which
        switching weather off silently stopped results collection and check-ins for the
        rest of the season while reporting that only weather jobs had been cancelled.

        Disabling the weather module (`ModuleCog._disable_weather`) is the caller. Leaving the
        ``results`` job in place is deliberate: removing it would destroy the round-arrival
        transition of a league whose results module is on, which is the defect above by
        another route.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT r.id FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id "
                "WHERE s.status IN ('ACTIVE', 'SETUP')",
            )
            rows = await cursor.fetchall()
        for row in rows:
            self.cancel_round(row[0], only=_WEATHER_JOB_PREFIXES)

    def schedule_all_rounds(
        self,
        rounds: list[Round],
        *,
        division_meta: dict[int, tuple[int, int]],
        phase_1_days: int = 5,
        phase_2_days: int = 2,
        phase_3_hours: int = 2,
    ) -> None:
        """Schedule all rounds in *rounds*.

        Args:
            division_meta: Mapping of ``division_id`` →
                ``(season_number, division_tier)`` used to build human-readable
                job IDs.  Every round's ``division_id`` must have an entry.
        """
        for rnd in rounds:
            season_number, division_tier = division_meta[rnd.division_id]
            self.schedule_round(
                rnd,
                season_number=season_number,
                division_tier=division_tier,
                phase_1_days=phase_1_days,
                phase_2_days=phase_2_days,
                phase_3_hours=phase_3_hours,
            )

    def schedule_result_submission_jobs(
        self,
        rounds: list[Round],
        *,
        division_meta: dict[int, tuple[int, int]],
    ) -> None:
        """Schedule only the result-submission job for each round.

        Used where ``schedule_round`` (which creates all weather + results jobs together) is
        not called for a round: the weather module is off (at approval or at an
        amendment), or a mystery round was amended inside its first horizon. The round still
        needs this job to leave Not run.

        Args:
            division_meta: Mapping of ``division_id`` →
                ``(season_number, division_tier)`` used to build human-readable
                job IDs.  Every round's ``division_id`` must have an entry.
        """
        for rnd in rounds:
            scheduled_at = rnd.scheduled_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
            season_number, division_tier = division_meta[rnd.division_id]
            job_id = f"results{_round_job_suffix(rnd, season_number, division_tier)}"
            self._scheduler.add_job(
                _one_off_timer_job,
                trigger=DateTrigger(run_date=scheduled_at, timezone="UTC"),
                id=job_id,
                replace_existing=True,
                name=f"Result submission s{season_number} d{division_tier} r{rnd.round_number}",
                kwargs={"kind": "results", "key": rnd.id},
            )
            log.info("Scheduled %s at %s", job_id, scheduled_at.isoformat())

    def run_result_submission_now(
        self, rnd: Round, *, season_number: int, division_tier: int
    ) -> None:
        """Run *rnd*'s results submission at once, as its own job would have at its moment.

        For a round whose moment passed while its timed work stood removed, as an amendment
        stopped on the change queue leaves it: the job armed again against that moment is past
        the misfire grace and would be skipped, and it is the round's one way off Not run. It
        replaces that job under the same id, armed with no trigger, which APScheduler runs at
        once, and with no lateness limit, so that a restart before it runs still runs it.
        """
        job_id = f"results{_round_job_suffix(rnd, season_number, division_tier)}"
        self._scheduler.add_job(
            _one_off_timer_job,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=None,
            name=f"Result submission s{season_number} d{division_tier} r{rnd.round_number}",
            kwargs={"kind": "results", "key": rnd.id},
        )
        log.info("Running %s at once: the round's moment has passed", job_id)

    def _remove_job(self, job_id: str) -> bool:
        """Remove *job_id*, and say whether it went; never raises (see the class docstring).

        A job already gone is passed over in silence. Any other failure is logged as a warning
        with its traceback, so that a real job-store fault can be found.
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

        For `/bot pack` and `/bot factory-reset` (issue #247), which clear all of the
        league's scheduled work at once rather than round by round, and so reach the jobs no
        round owns — the signup close timer, a wizard's inactivity and channel-delete jobs, a
        stale season-end job. Pack keeps the daily portrait refresh: it is a standing
        instruction from a setting pack keeps, and does nothing while no server is claimed.
        """
        removed = 0
        for job in list(self._scheduler.get_jobs()):
            if job.id in keep:
                continue
            if self._remove_job(job.id):
                removed += 1
        log.info("Removed %d scheduled job(s), keeping %s", removed, sorted(keep) or "none")
        return removed

    def get_pending_advance_jobs(self, round_ids: set[int]) -> list[dict]:
        """Return the un-fired weather and check-in jobs for the given round IDs.

        Used by the test-mode advance command to determine what the scheduler has
        actually queued — so advance only fires events that a live season would
        fire (respecting which modules are enabled).

        Returns a list of dicts sorted by ``(next_run_time, round_id, phase_number)``:
          - ``job_id``       — APScheduler job ID string
          - ``round_id``     — round this job belongs to
          - ``phase_number`` — 1/2/3 = the weather phases, 5/6/7 = the check-in call, its
            last notice and its deadline, 8/9 = the forecast and the check-in cleanups a day
            after the round (#425). A mystery round's notice is armed as ``weather_p1`` and so
            comes back as 1, which ``get_next_pending_phase`` then reads as the notice — this
            method knows nothing of formats. 0 never occurs here.
          - ``next_run_time``— datetime when the job is scheduled to fire

        Result submission and season-end jobs are excluded, so 4 never occurs either. Jobs
        that are paused (``next_run_time is None``) are excluded.
        """
        # result submission (results) is intentionally excluded here.
        # For test-mode advance, result submission is detected via DB state
        # (rounds_with_results fallback) so that past-dated results jobs
        # that already auto-fired don't block or double-trigger the wizard.
        _PHASE_PREFIX_MAP = {
            "weather_p1":       1,
            "weather_p2":       2,
            "weather_p3":       3,
            "rsvp_notice":      5,
            "rsvp_last_notice": 6,
            "rsvp_deadline":    7,
            "cleanup":          8,
            "rsvp_cleanup":     9,
        }
        result: list[dict] = []
        for job in self._scheduler.get_jobs():
            if job.next_run_time is None:
                continue
            round_id = _round_of(job)
            if round_id is None or round_id not in round_ids:
                continue
            # The event type, read off the ID by the scheme's one reader
            event_type = _job_event_type(job.id)
            if event_type is None:
                continue
            phase = _PHASE_PREFIX_MAP.get(event_type)
            if phase is None:
                continue  # results, season_end, etc.
            result.append(
                {
                    "job_id": job.id,
                    "round_id": round_id,
                    "phase_number": phase,
                    "next_run_time": job.next_run_time,
                }
            )
        result.sort(key=lambda x: (x["next_run_time"], x["round_id"], x["phase_number"]))
        return result

    def get_queued_events_for_rounds(self, round_ids: set[int]) -> set[tuple[int, str]]:
        """Return ``(round_id, event_type)`` for every queued job of the given rounds.

        The review summary's view of the job store: it tells "job queued" from "job absent"
        for each pending step. Unlike ``get_pending_advance_jobs`` nothing is filtered out by
        type — ``results`` and both cleanups are included — so it answers "is a job queued",
        not "what will advance fire".

        A job is found by its round and its event type, the way ``cancel_round`` finds one
        (issue #139): the round id the job is keyed by, which is the round's own key whatever numbers
        its ID carries (`_round_of`), and the type `_job_event_type` reads off the ID. A caller asks
        ``(round_id, "weather_p1") in queued`` and never rebuilds an ID to look one up — the
        review once did, in a shape no job has ever had, and so found none (#426). A mystery
        round's notice is armed as ``weather_p1`` and is found under that type.

        A paused job (``next_run_time is None``) will not fire and is left out, as is one whose
        ID carries no round suffix.
        """
        result: set[tuple[int, str]] = set()
        for job in self._scheduler.get_jobs():
            if job.next_run_time is None:
                continue
            round_id = _round_of(job)
            if round_id is None or round_id not in round_ids:
                continue
            event_type = _job_event_type(job.id)
            if event_type is not None:
                result.add((round_id, event_type))
        return result

    # ------------------------------------------------------------------
    # Season-end scheduling
    # ------------------------------------------------------------------

    def cancel_season_end(self) -> None:
        """Remove any season-end job a scheduler store still carries.

        Nothing schedules one any more. A season ends when a league manager runs
        `/season complete`, and `schedule_season_end` — along with the timer that armed it seven
        days after the last round — was deleted with issue #154. This is kept because a
        `scheduler.db` written by an older version may still carry a `season_end_*` job, and
        because `/season cancel` should go on saying so plainly. A stale job whose
        callable no longer exists is dropped by APScheduler on load, with a warning, rather than
        failing start-up.
        """
        for job in list(self._scheduler.get_jobs()):
            if job.id.startswith("season_end"):
                if self._remove_job(job.id):
                    log.info("Removed %s job", job.id)

    # ------------------------------------------------------------------
    # Signup auto-close scheduling
    # ------------------------------------------------------------------

    def schedule_portrait_refresh(self, time_of_day: str) -> None:
        """Schedule the league's daily driver-portrait refresh at *time_of_day* UTC.

        **A recurring trigger, and deliberately so** (`schedule_amendment_sweep` is the other).
        Every other job here is a one-shot ``DateTrigger`` arming a single event of a round or a
        season, and that shape is right for those: each fires once and is done. This one is a
        standing instruction rather than an event, and re-arming it from inside its own callback
        would put the survival of the schedule at the mercy of the job body -- a refresh that
        raised would silently be the last one. A ``CronTrigger`` is re-armed by APScheduler itself
        and outlives a failing run.

        *time_of_day* is ``HH:MM`` read as UTC, matching every other trigger in this service.
        A stored local time would need a zone stored with it and would drift against daylight
        saving twice a year; the league is told the zone when it names the time.

        ``replace_existing=True`` so that naming a new time re-arms rather than duplicates.
        """
        hour, _, minute = time_of_day.partition(":")
        job_id = PORTRAIT_REFRESH_JOB_ID
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone="UTC"),
            id=job_id,
            replace_existing=True,
            name="Daily driver portrait refresh",
            kwargs={"kind": PORTRAIT_REFRESH_KIND},
        )
        log.info("Scheduled %s at %s UTC daily", job_id, time_of_day)

    def schedule_amendment_sweep(self) -> None:
        """Arm the standing sweep that undoes an amendment nobody carried through (#345).

        An ``IntervalTrigger`` for the reason ``schedule_portrait_refresh`` gives for its cron
        one: this is a standing instruction rather than an event, and re-arming it from inside
        its own callback would put the survival of the schedule at the mercy of the job body —
        a sweep that raised would silently be the last.

        ``replace_existing=True`` so a restart re-arms rather than duplicates it.
        """
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=IntervalTrigger(minutes=AMENDMENT_SWEEP_MINUTES),
            id=AMENDMENT_SWEEP_JOB_ID,
            replace_existing=True,
            name="Revert abandoned round amendments",
            kwargs={"kind": AMENDMENT_SWEEP_KIND},
        )
        log.info(
            "Scheduled %s every %d minutes", AMENDMENT_SWEEP_JOB_ID, AMENDMENT_SWEEP_MINUTES
        )

    def cancel_portrait_refresh(self) -> None:
        """Remove the daily portrait refresh if it exists."""
        job_id = PORTRAIT_REFRESH_JOB_ID
        if self._remove_job(job_id):
            log.info("Removed %s", job_id)

    def schedule_signup_close_timer(self, close_at_iso: str) -> None:
        """Schedule the one-shot signup auto-close job at the given ISO 8601 UTC timestamp.

        Uses ``replace_existing=True`` so calling this again re-arms the timer.
        """
        fire_at = datetime.fromisoformat(close_at_iso).replace(tzinfo=timezone.utc)
        self._scheduler.add_job(
            _one_off_timer_job,
            trigger=DateTrigger(run_date=fire_at, timezone="UTC"),
            id=SIGNUP_CLOSE_JOB_ID,
            replace_existing=True,
            name="Signup auto-close",
            kwargs={"kind": SIGNUP_CLOSE_KIND},
        )
        log.info("Scheduled %s at %s", SIGNUP_CLOSE_JOB_ID, fire_at.isoformat())

    def cancel_signup_close_timer(self) -> None:
        """Remove the signup close timer if it exists."""
        if self._remove_job(SIGNUP_CLOSE_JOB_ID):
            log.info("Removed the %s job", SIGNUP_CLOSE_JOB_ID)
