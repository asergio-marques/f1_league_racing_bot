"""A round's job identifiers, and the readers that find a round's jobs by them.

Both are core's round jobs' (`RoundJobs`, #544): the identifiers are written and read in
`leaguebot.core.services.rounds.round_jobs`, and the readers of pending jobs and queued events are
its methods, over the framework's scheduler.

Issue #208. `scheduler_service.py` was at 50.7%. Every timed thing the bot does — forecasts,
check-in calls, deadlines, result submission — is a row in a job store keyed by one of these
identifiers, so the identifier scheme is load-bearing in a way nothing else in the bot is.

**The round id in the suffix prevents a silent catastrophe**, and the function's own docstring
records it. `renumber_rounds_on` rewrites the numbers of a whole division whenever an amendment
changes the order of its rounds, and nothing re-arms the jobs of the rounds that merely
shifted — so a round that became number 4 kept jobs saying `r5`, and the next amendment of the
round that became number 5 scheduled `..._r5` on top of them. Every `add_job` passes
`replace_existing=True`, so that destroyed the first round's entire schedule: its forecasts,
its result submission and its check-in, with nothing reporting it.

`test_two_rounds_that_swapped_numbers_keep_distinct_job_ids` is the regression test for exactly
that, and it is the reason this file exists. The season, tier and number stay in the identifier
only to make it readable in an admin view — they are decoration, and a number left stale by a
renumbering is cosmetic.

The one runner every job is stored with, and what it does when the service or a kind's handler
is missing, is pinned in `test_scheduler_job_callables.py`. A round job is stored with its kind
and the round id as its key, as a one-off timer is, and the readers find a round's jobs by that
key.

`get_pending_advance_jobs` parses the identifier back apart, which is the other reason the
scheme matters: a change to the suffix format breaks the reader as well as the writer, and
these tests hold both ends against each other.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from leaguebot.framework.services.jobs.scheduler import SchedulerService

SERVER_ID = 10408


def _round_job_suffix(rnd, season_number: int, division_tier: int) -> str:
    """The suffix core's round jobs append to a round's job ids."""
    from leaguebot.core.services.rounds import round_jobs

    return round_jobs._round_job_suffix(rnd, season_number, division_tier)


def _job_event_type(job_id: str) -> str | None:
    """The event type core's round jobs read off a job id."""
    from leaguebot.core.services.rounds import round_jobs

    return round_jobs._job_event_type(job_id)


def _round(round_id: int, round_number: int):
    return SimpleNamespace(id=round_id, round_number=round_number)


# ---------------------------------------------------------------------------
# The identifier scheme
# ---------------------------------------------------------------------------


def test_the_suffix_carries_season_tier_number_and_id():
    """All four, in that order. The reader below parses this back apart."""
    assert _round_job_suffix(_round(42, 5), season_number=3, division_tier=2) == (
        "_s3_d2_r5_id42"
    )


def test_two_rounds_that_swapped_numbers_keep_distinct_job_ids():
    """The regression this scheme exists for. After a renumbering, one round says `r5`
    from before and another says `r5` now — and with `replace_existing=True` on every
    `add_job`, identical ids would destroy the first round's whole schedule silently. Season,
    tier and number are decoration: it is each round's own id that makes them differ."""
    stale = _round(42, 5)  # was number 5, is now number 4, jobs never re-armed
    current = _round(43, 5)  # has just become number 5

    assert _round_job_suffix(stale, 3, 2) != _round_job_suffix(current, 3, 2)
    assert "id42" in _round_job_suffix(stale, 3, 2)
    assert "id43" in _round_job_suffix(current, 3, 2)


def test_a_stale_round_number_is_only_cosmetic():
    """A renumbering leaves the number wrong until the round is next scheduled. That is
    tolerated deliberately — the id is what the scheduler keys on."""
    before = _round_job_suffix(_round(42, 5), 3, 2)
    after = _round_job_suffix(_round(42, 4), 3, 2)

    assert before != after
    assert before.endswith("id42")
    assert after.endswith("id42")


# ---------------------------------------------------------------------------
# Reading the identifier back
# ---------------------------------------------------------------------------

#: Every event type a round's jobs are armed under, by `schedule_round` and
#: `schedule_attendance_round`.
_ROUND_EVENT_TYPES = (
    "weather_p1",
    "weather_p2",
    "weather_p3",
    "cleanup",
    "results",
    "rsvp_notice",
    "rsvp_last_notice",
    "rsvp_deadline",
    "rsvp_cleanup",
)


@pytest.mark.parametrize(
    "job_id,event_type",
    [
        ("weather_p1_s3_d2_r5_id42", "weather_p1"),
        ("rsvp_last_notice_s2_d3_r11_id97", "rsvp_last_notice"),
        ("weather_p1_s1_d1_r4", "weather_p1"),
        ("portrait_refresh", None),
        ("something_odd", None),
    ],
    ids=["current", "check-in", "written-before-the-round-id", "no-round", "unparseable"],
)
def test_the_event_type_is_the_id_less_its_round_suffix(job_id, event_type):
    """One reader for the whole scheme (#426). Every caller asking what a job is for reads it
    here — the review once built IDs of its own instead, in a shape no job has ever had, and
    found none of them."""
    assert _job_event_type(job_id) == event_type


@pytest.mark.parametrize("event_type", _ROUND_EVENT_TYPES)
def test_the_reader_undoes_the_writer(event_type):
    """Whatever `_round_job_suffix` appends, the reader takes off again, for all nine."""
    job_id = f"{event_type}{_round_job_suffix(_round(42, 5), 3, 2)}"

    assert _job_event_type(job_id) == event_type


def _job(job_id: str, round_id: int | None, *, minutes: int = 5, paused: bool = False):
    """A job as the store holds it: its kind (its id less any round suffix) and, for a round's
    job, the round id as its key, as the one runner is handed them."""
    kind = re.sub(r"_s\d+_d\d+_r\d+(_id\d+)?$", "", job_id)
    job = MagicMock()
    job.id = job_id
    job.kwargs = {"kind": kind, "key": round_id} if round_id is not None else {"kind": kind}
    job.next_run_time = (
        None if paused else datetime.now(timezone.utc) + timedelta(minutes=minutes)
    )
    return job


def _service(jobs: list) -> SchedulerService:
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = MagicMock()
    service._scheduler.get_jobs = MagicMock(return_value=jobs)
    return service


def _round_jobs(jobs: list):
    """Core's round jobs over a scheduler whose store lists *jobs*, built as the builder builds
    them."""
    from tests.support.round_jobs import forecast_leads, round_jobs_over, submission_lead

    return round_jobs_over(_service(jobs), "/tmp/not-read.db")


@pytest.mark.xfail(strict=True, reason="#544: the pending read hands back no job's kind, and core's round jobs keep no register of round kinds yet")
def test_each_event_type_maps_to_its_phase_number():
    """The advance command fires events by phase number, so a prefix mapped wrongly would
    fire the wrong thing — a check-in deadline in place of a forecast. Each job comes back with
    its kind too, so test mode needs no map from phase to kind (#544)."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"weather_p1{suffix}", 42, minutes=1),
        _job(f"weather_p2{suffix}", 42, minutes=2),
        _job(f"weather_p3{suffix}", 42, minutes=3),
        _job(f"rsvp_notice{suffix}", 42, minutes=4),
        _job(f"rsvp_last_notice{suffix}", 42, minutes=5),
        _job(f"rsvp_deadline{suffix}", 42, minutes=6),
        _job(f"cleanup{suffix}", 42, minutes=7),
        _job(f"rsvp_cleanup{suffix}", 42, minutes=8),
    ]

    pending = _round_jobs(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [1, 2, 3, 5, 6, 7, 8, 9]
    assert [p["kind"] for p in pending] == [
        "weather_p1", "weather_p2", "weather_p3", "rsvp_notice", "rsvp_last_notice",
        "rsvp_deadline", "cleanup", "rsvp_cleanup",
    ]


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_jobs_are_returned_in_the_order_they_will_fire():
    """Advance replays a season in order; out of sequence it would post a deadline before
    the call it closes."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"weather_p3{suffix}", 42, minutes=30),
        _job(f"weather_p1{suffix}", 42, minutes=10),
        _job(f"weather_p2{suffix}", 42, minutes=20),
    ]

    pending = _round_jobs(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [1, 2, 3]


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_paused_job_is_not_pending():
    """`next_run_time is None` means it will not fire, and advance must not claim it will."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"weather_p1{suffix}", 42, paused=True)]

    assert _round_jobs(jobs).get_pending_advance_jobs({42}) == []


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_another_round_s_jobs_are_not_returned():
    suffix_mine = _round_job_suffix(_round(42, 5), 3, 2)
    suffix_theirs = _round_job_suffix(_round(43, 6), 3, 2)
    jobs = [
        _job(f"weather_p1{suffix_mine}", 42),
        _job(f"weather_p1{suffix_theirs}", 43),
    ]

    pending = _round_jobs(jobs).get_pending_advance_jobs({42})

    assert [p["round_id"] for p in pending] == [42]


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_season_end_jobs_are_excluded():
    """Not an event of any round, so advance must not replay it."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [
        _job(f"season_end{suffix}", 42),
        _job(f"weather_p1{suffix}", 42),
    ]

    pending = _round_jobs(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [1]


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_both_cleanups_are_replayed_as_events_of_their_own():
    """A round's forecast and check-in come down a day after it, and advance fires both in
    their turn (decided 2026-09-24, #425), the forecast's first where they fall together."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"rsvp_cleanup{suffix}", 42), _job(f"cleanup{suffix}", 42)]
    for job in jobs:
        job.next_run_time = jobs[0].next_run_time

    pending = _round_jobs(jobs).get_pending_advance_jobs({42})

    assert [p["phase_number"] for p in pending] == [8, 9]


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
@pytest.mark.parametrize("minutes", [5, -5], ids=["ahead", "past"])
def test_a_results_job_is_never_returned(minutes):
    """Result submission is found from database state instead, so that a past-dated job
    which already auto-fired can neither block the wizard nor open it twice. The docstring
    once listed it as phase 4 (issue #149). Results signs its kind up as a round's with no phase
    (#544), so neither a submission still ahead nor one whose moment has passed comes back."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"results{suffix}", 42, minutes=minutes), _job(f"weather_p1{suffix}", 42)]

    pending = _round_jobs(jobs).get_pending_advance_jobs({42})

    assert [p["job_id"] for p in pending] == [f"weather_p1{suffix}"]


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_job_of_a_kind_no_module_signed_up_is_no_round_s():
    """Core's round jobs know a round's job by the kinds the modules signed up (#544), and by
    nothing of their own. With weather's four signed up alone, round 42's first forecast phase
    and check-in call are both queued: the phase is pending and queued, and the call, its kind
    signed up by no module, is neither."""
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"weather_p1{suffix}", 42, minutes=1), _job(f"rsvp_notice{suffix}", 42, minutes=2)]
    round_jobs = RoundJobs(_service(jobs), "/tmp/not-read.db")
    round_jobs.sign_up_kinds({"weather_p1": 1, "weather_p2": 2, "weather_p3": 3, "cleanup": 8})

    assert [p["job_id"] for p in round_jobs.get_pending_advance_jobs({42})] == [
        f"weather_p1{suffix}"
    ]
    assert round_jobs.get_queued_events_for_rounds({42}) == {(42, "weather_p1")}


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_job_with_no_round_is_ignored():
    """The portrait refresh and the signup close timer are server-scoped and carry no
    round."""
    jobs = [_job("portrait_refresh", None)]

    assert _round_jobs(jobs).get_pending_advance_jobs({42}) == []


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_job_whose_id_does_not_parse_is_ignored():
    """Rather than raising. A job from an older version of the bot must not stop the
    advance command working."""
    jobs = [_job("something_odd", 42)]

    assert _round_jobs(jobs).get_pending_advance_jobs({42}) == []


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_every_queued_job_is_listed_by_its_round_and_event_type():
    """`get_queued_events_for_rounds` is the review summary's view and deliberately keeps the
    ones advance excludes — it answers "is a job queued", not "what will advance fire" (#426)."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"{event_type}{suffix}", 42) for event_type in _ROUND_EVENT_TYPES]

    queued = _round_jobs(jobs).get_queued_events_for_rounds({42})

    assert queued == {(42, event_type) for event_type in _ROUND_EVENT_TYPES}


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_paused_job_is_not_queued():
    """A paused job will not fire, and is no more worth reporting as queued than a missing one."""
    suffix = _round_job_suffix(_round(42, 5), 3, 2)
    jobs = [_job(f"weather_p1{suffix}", 42, paused=True)]

    assert _round_jobs(jobs).get_queued_events_for_rounds({42}) == set()


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_queued_job_is_found_by_its_round_not_the_numbers_in_its_id():
    """The season, tier and number in an ID are decoration, and two rounds may share them after a
    renumbering. The round a job belongs to is its `round_id` kwarg, which an ID written before
    the round id was added to it carries as well."""
    jobs = [
        _job("weather_p1_s1_d1_r5_id42", 42),
        _job("weather_p1_s1_d1_r5_id43", 43),
        _job("results_s1_d1_r5", 42),
        _job("weather_p2_s1_d1_r6_id44", 44),
    ]

    queued = _round_jobs(jobs).get_queued_events_for_rounds({42, 43})

    assert queued == {(42, "weather_p1"), (43, "weather_p1"), (42, "results")}


@pytest.mark.xfail(strict=True, reason="#544: core's round jobs keep no register of the modules' round kinds yet (sign_up_kinds)")
def test_a_job_whose_id_does_not_parse_is_not_listed():
    """Its type cannot be read, so it cannot stand for any step of the round."""
    jobs = [_job("something_odd", 42), _job("portrait_refresh", None)]

    assert _round_jobs(jobs).get_queued_events_for_rounds({42}) == set()


# ---------------------------------------------------------------------------
# A renumbering must not let one round's jobs destroy another's
# ---------------------------------------------------------------------------


def _round_at(round_id: int, round_number: int):
    from datetime import datetime, timezone

    from leaguebot.core.models.round import Round, RoundFormat

    return Round(
        id=round_id,
        division_id=1,
        round_number=round_number,
        format=RoundFormat.NORMAL,
        track_name="Bahrain International Circuit",
        scheduled_at=datetime(2099, 6, 1, 14, 0, tzinfo=timezone.utc),
    )


@pytest.mark.xfail(strict=True, reason="#544: core has no RoundLead nor RoundJobs.arm_leads yet")
def test_renumbering_does_not_destroy_a_sibling_rounds_jobs():
    """Two rounds that swap numbers must never share a job ID.

    `renumber_rounds_on` rewrites the numbers of a whole division whenever an amendment changes
    the order of its rounds, and only the amended round is re-armed — so the sibling kept jobs
    named for a number it no longer held. The next amendment of *that* sibling then scheduled
    onto the same ID, and because every `add_job` passes ``replace_existing=True`` it silently
    took over the other round's entire schedule: forecasts, result submission and check-in,
    with nothing reporting it.
    """
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    svc = SchedulerService.__new__(SchedulerService)
    svc._scheduler = MagicMock()
    svc._scheduler.add_job = MagicMock()
    round_jobs = RoundJobs(svc, ":memory:")

    # Round 105 was fifth and is amended to fall fourth; the renumbering then makes round 104
    # the fifth. Both are scheduled as number 5, at different times in their lives.
    leads = (*forecast_leads(), submission_lead())
    first_round = _round_at(105, 5)
    round_jobs.arm_leads(first_round, leads, 1, 1, first_round.scheduled_at - timedelta(days=30))
    first = {c.kwargs["id"] for c in svc._scheduler.add_job.call_args_list}

    svc._scheduler.add_job.reset_mock()
    second_round = _round_at(104, 5)
    round_jobs.arm_leads(second_round, leads, 1, 1, second_round.scheduled_at - timedelta(days=30))
    second = {c.kwargs["id"] for c in svc._scheduler.add_job.call_args_list}

    assert first and second
    assert not (first & second), (
        "two rounds scheduled under the same number share job IDs, so the second silently "
        f"replaces the first: {sorted(first & second)}"
    )


def test_a_job_id_written_before_the_round_id_existed_still_reports_its_module():
    """The job store outlives a restart, so the old ID format is still in it.

    Ownership is read by stripping the round suffix off the job ID, and that is what tells a
    module's disable which jobs are its own. A suffix pattern that insisted on the round id
    would fail to parse the jobs already written without one — and weather's disable would stop
    filtering and take results and the check-in down with it, which is issue #117 over again.
    """
    from leaguebot.core.services.rounds.round_jobs import _JOB_SUFFIX_RE

    for job_id, prefix in (
        ("weather_p1_s1_d1_r4", "weather_p1"),          # written before the id was added
        ("weather_p1_s1_d1_r4_id104", "weather_p1"),    # written after
        ("rsvp_deadline_s2_d3_r11_id97", "rsvp_deadline"),
    ):
        match = _JOB_SUFFIX_RE.search(job_id)
        assert match is not None, job_id
        assert job_id[: match.start()] == prefix
