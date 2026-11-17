"""Core's round jobs: the timed work of a round, armed and cancelled through the framework's scheduler.

A round carries jobs of the kinds the modules sign up (`RoundJobs.sign_up_kinds`): weather's
forecast phases and clean-up, the result submission, and the check-in's call, last notice, deadline
and clean-up. Core arms them where a round is approved or amended, cancels them where it is called
off, and reads them where test mode asks what is queued. `RoundJobs` is the one place that knows the scheme their
ids are written in, and the framework's scheduler, which it holds, knows none of it: every job is
armed under the id, kind and key handed to the scheduler's generic calls
(`docs/design/architecture.md`, "Timed work and restarts"). A job's ids, kinds, names and moments
are what they have always been, so a job already stored finds its handler on a restart.

Every cancel is a clean-up, and the scheduler's `remove` never raises: a job already fired is the
ordinary case, and a job store that will not give one up is logged and left. A cancel that lists
the store first (`cancel_round`, `cancel_rounds`, `cancel_season_end`) or reads the league
database (`cancel_live_rounds`) does raise where that cannot be read, before it removes anything.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Collection, Iterable, Mapping
from types import MappingProxyType
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from leaguebot.core.models.round import (
    AttendanceWindows,
    Round,
    RoundLead,
    WeatherWindows,
)
from leaguebot.core.services.rounds.round_moments import clean_up_moment, round_event_moments
from leaguebot.framework.db.database import get_connection

if TYPE_CHECKING:
    from leaguebot.framework.services.jobs.scheduler import SchedulerService, StoredJob

log = logging.getLogger(__name__)

# Regex that matches the ``_s{S}_d{D}_r{R}_id{round_id}`` suffix appended to every
# round-scoped job ID. Used to extract the event-type prefix for dispatch.
#
# ``_id{round_id}`` is optional because the job store is a real file that outlives a
# restart: jobs written before the id was added are still in it, and a regex that
# insisted on the id would fail to read their prefix — which is how a job's owning
# module is identified, so weather's disable would stop filtering and take the other
# modules' jobs down with it.
_JOB_SUFFIX_RE = re.compile(r"_s\d+_d\d+_r\d+(?:_id\d+)?$")

# A round's job names its owner twice over: its ID prefix and the ``kind`` it is stored with are
# the same, one of the kinds the modules signed up (`RoundJobs.sign_up_kinds`). All of a round's
# jobs carry the same ``key`` kwarg (the round id), so the key says which round
# a job belongs to and nothing about which module it is for. The kind says it, and so does the
# prefix; `cancel_round(only=...)` reads the prefix, which ``_JOB_SUFFIX_RE`` above exposes,
# because that is what its callers already hold.


def _round_job_suffix(rnd: "Round", season_number: int, division_tier: int) -> str:
    """The ``_s{S}_d{D}_r{R}_id{round_id}`` tail every one of a round's job IDs carries.

    **Why the round id is in there** — a job ID must be unique to its round for the life of
    that round, and the season, tier and round number are not. `renumber_rounds_on` rewrites the
    numbers of a whole division whenever an amendment changes the order of its rounds, and
    nothing re-arms the jobs of the rounds that merely shifted. So a round that became number 4
    kept jobs saying ``r5``, and the next amendment of the round that became number 5 scheduled
    ``..._r5`` on top of them — and because the scheduler replaces a job armed
    under an id already taken, that silently destroyed the first round's entire schedule: its
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
    round id the job is keyed by (`_round_of`) — never rebuilding an ID to look one up: an ID
    built elsewhere can take a shape no job has, and so find none.
    """
    m = _JOB_SUFFIX_RE.search(job_id)
    return None if m is None else job_id[: m.start()]


class RoundJobs:
    """Arms, cancels and reads a round's jobs, over the *scheduler* the builder hands it.

    *db_path* is the league database, read only where the live season's rounds are asked for
    (`cancel_live_rounds`)."""

    def __init__(self, scheduler: "SchedulerService", db_path: str) -> None:
        self._timers = scheduler
        self._db_path = db_path
        self._kinds: dict[str, int | None] = {}

    def sign_up_kinds(self, kinds: Mapping[str, int | None]) -> None:
        """Sign up the kinds of job a module arms for a round, each with the phase number
        `/test-mode advance` numbers it by, or None for a kind the advance reads from the round's
        state instead (results' submission).

        Core's round jobs know a job is a round's, and which phase it is, by this register and by
        nothing of their own: a job of a kind no module signed up is no round's, whatever key it
        carries. Each module signs its own kinds up at build.
        """
        self._kinds.update(kinds)

    @property
    def kinds(self) -> Mapping[str, int | None]:
        """Every kind of a round's job the modules signed up, with its advance phase."""
        return MappingProxyType(self._kinds)

    def _round_of(self, job: "StoredJob") -> int | None:
        """The round a stored job belongs to: its key, where its kind is one a module signed up as
        a round's (`sign_up_kinds`), or None for any other job. Another job's key, an amendment's
        lapse timer's for one, is no round's, however it is numbered."""
        if job.kind not in self._kinds:
            return None
        return job.key if isinstance(job.key, int) else None

    def arm_leads(
        self,
        rnd: Round,
        leads: Iterable[RoundLead],
        season_number: int,
        division_tier: int,
        now: datetime,
    ) -> None:
        """Arm each of *leads* for *rnd*, under the id, moment, name and key a round's job carries.

        A lead's timer is stored as ``<kind>_s{season_number}_d{division_tier}_r{round_number}_id{round_id}``
        (`_round_job_suffix`), fires at the round's moment plus the lead's offset, is named
        ``<label> s{season_number} d{division_tier} r{round_number}`` and is handed its kind and the
        round id as its key. It replaces a job of the same id, so arming an amended round again is
        safe. A lead marked to be armed only while ahead is left out once its moment is not ahead of
        *now* (the queue's clock, never the wall's); any other is armed whatever the hour.

        No timer has a lateness limit (`misfire_grace_time=None`): one whose moment passed while the
        bot was down fires once it is back, and its handler judges what late means (a last notice is
        sent late while the deadline is ahead, a result submission decides what becomes of a late
        round), so the arming never has to.

        The result submission is armed through its lead whatever the modules: results answers it
        for every round, since it is the round's one way off Not run and its handler moves a round
        on where results is off.

        This only arms. Running a lead at once (`run_lead_now`), taking down what a round's earlier
        arming left, and dropping a lead test mode leaves to the advance are the caller's, and the
        marks on a lead are how it chooses them.
        """
        suffix = _round_job_suffix(rnd, season_number, division_tier)
        at = rnd.scheduled_at.astimezone(timezone.utc)
        for lead in leads:
            fire_at = at + lead.offset
            job_id = f"{lead.kind}{suffix}"
            if lead.ahead_only and fire_at <= now:
                log.info("Skipping %s — fire time %s is in the past", job_id, fire_at.isoformat())
                continue
            self._timers.arm_at(
                job_id=job_id, kind=lead.kind, key=rnd.id, run_at=fire_at,
                name=f"{lead.label} s{season_number} d{division_tier} r{rnd.round_number}",
            )

    def run_lead_now(
        self, rnd: Round, lead: RoundLead, season_number: int, division_tier: int
    ) -> None:
        """Run *lead*'s timer for *rnd* at once, as its own job would have at its moment.

        For a round whose moment passed while its timed work stood removed, as an amendment
        stopped on the change queue leaves it: the job armed again against that moment lies in the
        past, and where the lead is the round's one way off Not run it is run now rather than left
        to the scheduler's next wake. It replaces that job under the same id, run at once by the
        scheduler, and with no lateness limit, so that a restart before it runs still runs it.
        """
        job_id = f"{lead.kind}{_round_job_suffix(rnd, season_number, division_tier)}"
        self._timers.run_now(
            job_id=job_id, kind=lead.kind, key=rnd.id,
            name=f"{lead.label} s{season_number} d{division_tier} r{rnd.round_number}",
        )

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
        ``weather_p3``) and a cleanup job.  Every job runs through
        the scheduler's one runner, handed its kind (its job id's prefix) and the round id as its key.
        Whether a MYSTERY round's phase asks for a notice or for nothing is weather's rule,
        decided by the handler the builder signs up for ``weather_p1`` to ``weather_p3``, never
        here.

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
                            amendment's catch-up (`round_amend_change`). A job armed for a
                            horizon already passed has no lateness limit and would fire at
                            once, asking for the phase a second time.
        """
        _suffix = _round_job_suffix(rnd, season_number, division_tier)

        moments = round_event_moments(
            rnd.scheduled_at,
            weather=WeatherWindows(phase_1_days, phase_2_days, phase_3_hours),
            attendance=None,
        )

        for phase_num in (1, 2, 3):
            if phase_num in skip_phases:
                continue
            fire_at = moments[f"weather_p{phase_num}"]
            job_id = f"weather_p{phase_num}{_suffix}"
            self._timers.arm_at(
                job_id=job_id, kind=f"weather_p{phase_num}", key=rnd.id,
                run_at=fire_at, name=f"Weather P{phase_num} s{season_number} d{division_tier} r{rnd.round_number}",
            )

        # Post-race Phase 3 cleanup, a day after round start
        self._arm_forecast_cleanup(rnd, season_number=season_number, division_tier=division_tier)

    def schedule_attendance_round(
        self,
        rnd: Round,
        *,
        season_number: int,
        division_tier: int,
        notice_days: int,
        last_notice_hours: int,
        deadline_hours: int,
        now: datetime,
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
            now:                 The moment the jobs are judged against: one whose moment is not
                                 ahead of it is skipped (the queue's clock, never the wall's).

        A fourth job, ``rsvp_cleanup``, takes the round's call, last notice and distribution
        message down `POST_RACE_CLEANUP_DELAY` after the round's start.

        Each of the four is armed with no lateness limit (`misfire_grace_time=None`), so one whose
        moment passed while the bot was down fires when it is back, and its handler, the
        check-in's change, judges what late means: a last notice is sent late while the deadline
        is ahead, and a call or a deadline is asked as it would be on time.
        """
        moments = round_event_moments(
            rnd.scheduled_at,
            weather=None,
            attendance=AttendanceWindows(notice_days, last_notice_hours, deadline_hours),
        )
        _suffix = _round_job_suffix(rnd, season_number, division_tier)

        # The call, the last notice (absent where its hours are 0) and the deadline, each only
        # while its moment is still ahead of *now*.
        for kind, label in (
            ("rsvp_notice", "RSVP notice"),
            ("rsvp_last_notice", "RSVP last-notice"),
            ("rsvp_deadline", "RSVP deadline"),
        ):
            fire_at = moments.get(kind)
            if fire_at is None:
                continue
            job_id = f"{kind}{_suffix}"
            if fire_at > now:
                self._timers.arm_at(
                    job_id=job_id, kind=kind, key=rnd.id,
                    run_at=fire_at, name=f"{label} s{season_number} d{division_tier} r{rnd.round_number}",
                )
            else:
                log.info("Skipping %s — fire time %s is in the past", job_id, fire_at.isoformat())

        # Cleanup job, a day after the round
        self.schedule_round_cleanups(
            rnd, season_number=season_number, division_tier=division_tier, forecast=False, now=now
        )

    def schedule_round_cleanups(
        self,
        rnd: Round,
        *,
        season_number: int,
        division_tier: int,
        now: datetime,
        forecast: bool = True,
        check_in: bool = True,
    ) -> None:
        """Register the clean-ups that take a round's posts down `POST_RACE_CLEANUP_DELAY` after its start.

        ``cleanup`` deletes the round's weather forecasts and ``rsvp_cleanup`` its check-in call,
        last notice and distribution message; *forecast* and *check_in* choose which are armed
        (``schedule_round`` arms the first through `_arm_forecast_cleanup`,
        ``schedule_attendance_round`` calls this for the second alone, and a refused
        cancellation arms both for a round raced, or ended cancelled as not held). The
        forecast's is armed whatever the hour; the check-in's only while its moment is still
        ahead of *now*, the restart's catch-up taking one already past (weather's
        `recover_missed_forecast_clean_ups`, attendance's `recover_missed_check_in_clean_ups`).
        Both replace a job of the same id.
        """
        if forecast:
            self._arm_forecast_cleanup(
                rnd, season_number=season_number, division_tier=division_tier
            )

        if check_in:
            suffix = _round_job_suffix(rnd, season_number, division_tier)
            cleanup_fire_at = clean_up_moment(rnd.scheduled_at)
            rsvp_job_id = f"rsvp_cleanup{suffix}"
            if cleanup_fire_at > now:
                self._timers.arm_at(
                    job_id=rsvp_job_id, kind="rsvp_cleanup", key=rnd.id,
                    run_at=cleanup_fire_at, name=f"RSVP cleanup s{season_number} d{division_tier} r{rnd.round_number}",
                )
            else:
                log.info("Skipping %s — fire time %s is in the past", rsvp_job_id, cleanup_fire_at.isoformat())

    def _arm_forecast_cleanup(
        self, rnd: Round, *, season_number: int, division_tier: int
    ) -> None:
        """Arm the ``cleanup`` job that deletes a round's forecasts, whatever the hour.

        Armed by `schedule_round` and, on its own, by `schedule_round_cleanups`. No clock is
        read: the job is armed past-dated too.
        """
        suffix = _round_job_suffix(rnd, season_number, division_tier)
        cleanup_fire_at = clean_up_moment(rnd.scheduled_at)
        cleanup_job_id = f"cleanup{suffix}"
        self._timers.arm_at(
            job_id=cleanup_job_id, kind="cleanup", key=rnd.id,
            run_at=cleanup_fire_at, name=f"Forecast cleanup s{season_number} d{division_tier} r{rnd.round_number}",
        )

    def cancel_round(self, round_id: int, *, only: frozenset[str] | None = None) -> list[str]:
        """Remove scheduler jobs belonging to *round_id*, and give the ids of those that went.

        The ids let a cancellation refused afterwards know what it took and arm it again.

        Iterates the live jobstore and removes every job of one of a round's kinds
        (`sign_up_kinds`) whose ``key`` is *round_id*.  This is format-agnostic, and leaves
        alone a one-off timer of another kind that happens to be keyed with the same number.

        Args:
            only: When given, restricts the removal to jobs whose event type — as
                `_job_event_type` reads it off the job ID — is in the set.  Callers
                cancelling a *round* want the default, which takes all nine of its jobs; a
                caller switching **one module** off wants that module's prefixes and nothing
                else, or it takes the other modules' work down with it. The filter reads the ID's
                prefix, which is the kind the job is stored with, so it never has to guess a job's
                owner; a job whose ID carries no round suffix has no event type to match and is left
                alone while *only* is in force.
        """
        removed: list[str] = []
        for job in self._timers.jobs():
            if self._round_of(job) != round_id:
                continue
            if only is not None:
                event_type = _job_event_type(job.job_id)
                if event_type is None or event_type not in only:
                    continue
            if self._timers.remove(job.job_id):
                log.info("Removed job %s", job.job_id)
                removed.append(job.job_id)
        return removed

    def cancel_rounds(self, round_ids: Iterable[int], *, season_end: bool = False) -> dict[int, list[str]]:
        """Remove the scheduler jobs of every round in *round_ids* in one pass.

        Gives the ids of the jobs removed, by round; a round that had none is left out, and so
        is a ``season_end*`` job taken with *season_end*.

        The store is listed once, before anything is removed, and that listing is the only call
        that can raise: a store that cannot be read removes nothing, so a division's or a
        season's cancellation that unarms its rounds first stands as it was. Each removal then goes
        through the scheduler's `remove`, which never raises. With *season_end*, any stale ``season_end*`` job
        the store still carries goes in the same pass.
        """
        wanted = set(round_ids)
        removed: dict[int, list[str]] = {}
        jobs = self._timers.jobs()
        for job in jobs:
            round_id = self._round_of(job)
            if round_id in wanted or (season_end and job.job_id.startswith("season_end")):
                if self._timers.remove(job.job_id):
                    log.info("Removed job %s", job.job_id)
                    if round_id in wanted:
                        removed.setdefault(round_id, []).append(job.job_id)
        return removed

    async def cancel_live_rounds(self, *, only: frozenset[str] | None = None) -> None:
        """Cancel the jobs of every round in a season being set up or running, those of *only*'s kinds
        where it is given (`cancel_round`).

        It is for a module's switch-off, which takes its own kinds from every round that has them
        to take and nothing else: weather's passes `WEATHER_JOB_KINDS`. A completed or cancelled
        season's rounds are left alone.
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
            self.cancel_round(row[0], only=only)

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
            after the round. A mystery round's notice is armed as ``weather_p1`` and so
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
        result: list[dict] = []
        for job in self._timers.jobs():
            if job.next_run is None:
                continue
            round_id = self._round_of(job)
            if round_id is None or round_id not in round_ids:
                continue
            # A job carrying no round suffix has no event type to read, and is left alone
            if _job_event_type(job.job_id) is None:
                continue
            phase = self._kinds.get(job.kind) if job.kind is not None else None
            if phase is None:
                continue  # a kind signed up with no phase (results')
            result.append(
                {
                    "job_id": job.job_id,
                    "round_id": round_id,
                    "kind": job.kind,
                    "phase_number": phase,
                    "next_run_time": job.next_run,
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

        A job is found by its round and its event type, the way ``cancel_round`` finds one: the
        round id the job is keyed by, which is the round's own key whatever numbers its ID
        carries (`_round_of`), and the type `_job_event_type` reads off the ID. A caller asks
        ``(round_id, "weather_p1") in queued`` and never rebuilds an ID to look one up: an ID
        built elsewhere can take a shape no job has, and so find none. A mystery round's notice
        is armed as ``weather_p1`` and is found under that type.

        A paused job (``next_run_time is None``) will not fire and is left out, as is one whose
        ID carries no round suffix.
        """
        result: set[tuple[int, str]] = set()
        for job in self._timers.jobs():
            if job.next_run is None:
                continue
            round_id = self._round_of(job)
            if round_id is None or round_id not in round_ids:
                continue
            event_type = _job_event_type(job.job_id)
            if event_type is not None:
                result.add((round_id, event_type))
        return result

    def cancel_season_end(self) -> None:
        """Remove any season-end job a scheduler store still carries.

        Nothing schedules one: a season ends when a league manager runs `/season complete`.
        This is kept because a `scheduler.db` written by an older version of the bot may still
        carry a `season_end_*` job, and because `/season cancel` should go on saying so plainly.
        A stale job whose callable no longer exists is dropped by APScheduler on load, with a
        warning, rather than failing start-up.
        """
        for job in self._timers.jobs():
            if job.job_id.startswith("season_end"):
                if self._timers.remove(job.job_id):
                    log.info("Removed %s job", job.job_id)
