"""Removing a scheduled job: "already gone" is the aim, and any other fault is kept (#442).

Every cancellation the scheduler service offers is a clean-up. A job that has already fired, or
was never armed, is the ordinary case, and APScheduler says so with `JobLookupError`: that is
passed over in silence. Anything else — a job store that cannot be read or written — is a real
fault, and it is logged as a warning with its traceback. Neither raises, because the callers are
teardown lists where one job that will not go must not stop the rest.

`cancel_all` counts only the jobs that actually went.

`cancel_rounds` takes the timed work of several rounds, and the season-end job when asked, in one
pass: it lists the store once, before it removes anything, so a store that cannot be listed
removes nothing and a division's or a season's cancellation stands as it was (#439, slice 8).

`schedule_round_cleanups` arms a raced round's two clean-ups alone, the forecast's and the
check-in's, a day after its start: what a cancellation refused after its unarming gives back to a
round already raced (#439, slice 8).
"""
from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from apscheduler.jobstores.base import JobLookupError

from leaguebot.core.services.scheduler_service import SchedulerService

LOGGER = "leaguebot.core.services.scheduler_service"

#: Each cancel method, and the arguments it is called with. A job the store lists carries the
#: round id and the season-end prefix, so every method that walks the store finds it.
CANCELS = [
    ("cancel_job", ("wizard_inactivity_101",)),
    ("cancel_all", ()),
    ("cancel_round", (5,)),
    ("cancel_season_end", ()),
    ("cancel_portrait_refresh", ()),
    ("cancel_signup_close_timer", ()),
    ("cancel_rounds", ([5],)),
    # A wizard's timer, which the signup switch-off disarms whether or not it has fired.
    ("disarm", ("signup_inactivity", "101")),
]

CANCEL_CASES = [pytest.param(name, args, id=name) for name, args in CANCELS]


def _service(jobs) -> SchedulerService:
    """A scheduler service whose APScheduler is stubbed, listing *jobs*."""
    service = SchedulerService.__new__(SchedulerService)
    service._scheduler = MagicMock()
    service._scheduler.get_jobs = MagicMock(return_value=list(jobs))
    return service


def _job(job_id: str):
    return SimpleNamespace(id=job_id, kwargs={"round_id": 5})


@pytest.mark.parametrize("cancel, args", CANCEL_CASES)
def test_a_cancel_passes_over_a_job_already_gone(caplog, cancel, args):
    """Fired or never armed: nothing raises and nothing is logged. `cancel_all`, given two
    jobs of which one is already gone, counts only the one that went."""
    jobs = [_job("season_end_gone"), _job("season_end_kept")]
    service = _service(jobs if cancel == "cancel_all" else jobs[:1])

    def _remove(job_id):
        if cancel == "cancel_all" and job_id == "season_end_kept":
            return None
        raise JobLookupError(job_id)

    service._scheduler.remove_job = MagicMock(side_effect=_remove)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = getattr(service, cancel)(*args)

    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    if cancel == "cancel_all":
        assert result == 1


@pytest.mark.parametrize("cancel, args", CANCEL_CASES)
def test_a_cancel_logs_the_traceback_of_any_other_failure(caplog, cancel, args):
    """A job store that cannot be written is a real fault: a warning carrying the error and
    its traceback, and still no raise. `cancel_all` goes on to the next job and counts it."""
    jobs = [_job("season_end_broken"), _job("season_end_kept")]
    service = _service(jobs if cancel == "cancel_all" else jobs[:1])
    error = RuntimeError("the job store is locked")

    def _remove(job_id):
        if cancel == "cancel_all" and job_id == "season_end_kept":
            return None
        raise error

    service._scheduler.remove_job = MagicMock(side_effect=_remove)

    with caplog.at_level(logging.WARNING, logger=LOGGER):
        result = getattr(service, cancel)(*args)  # must not raise

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "the fault was not logged"
    assert any(r.exc_info and r.exc_info[1] is error for r in warnings)
    if cancel == "cancel_all":
        assert result == 1


def _round_job(job_id: str, round_id: int):
    return SimpleNamespace(id=job_id, kwargs={"round_id": round_id})


def test_cancel_rounds_removes_every_job_of_every_round_handed_and_no_other():
    """Rounds 5 and 6 each carry two jobs, round 7 one, and a season-end job stands. Cancelling
    rounds 5 and 6 removes their four jobs, listing the store once, and leaves round 7's job and
    the season-end job alone."""
    jobs = [
        _round_job("results_r5", 5), _round_job("rsvp_cleanup_r5", 5),
        _round_job("results_r6", 6), _round_job("cleanup_r6", 6),
        _round_job("results_r7", 7), SimpleNamespace(id="season_end_3", kwargs={}),
    ]
    service = _service(jobs)

    service.cancel_rounds([5, 6])

    removed = [call.args[0] for call in service._scheduler.remove_job.call_args_list]
    assert sorted(removed) == ["cleanup_r6", "results_r5", "results_r6", "rsvp_cleanup_r5"]
    assert service._scheduler.get_jobs.call_count == 1


def test_a_store_that_cannot_be_listed_removes_nothing():
    """The job store cannot be read when round 5's and round 6's timed work is to be removed:
    the fault is raised, so that the cancellation stops, and no job is removed."""
    service = _service([])
    service._scheduler.get_jobs = MagicMock(side_effect=RuntimeError("the job store is locked"))

    with pytest.raises(RuntimeError):
        service.cancel_rounds([5, 6])

    service._scheduler.remove_job.assert_not_called()


def test_cancel_round_gives_the_jobs_it_removed():
    """Round 5 carries its results submission and its clean-up, and round 6 its results
    submission. Cancelling round 5 gives the two jobs it removed, so that a refused cancellation
    knows what to arm again, and nothing of round 6."""
    jobs = [_round_job("results_r5", 5), _round_job("cleanup_r5", 5), _round_job("results_r6", 6)]
    service = _service(jobs)

    removed = service.cancel_round(5)

    assert sorted(removed) == ["cleanup_r5", "results_r5"]


def test_cancel_rounds_gives_the_jobs_it_removed_of_each_round_and_leaves_out_the_season_end():
    """Rounds 5 and 6 each carry two jobs, round 7 one, and a stale season-end job stands. A
    season's cancellation of rounds 5 and 6, the season-end job with them, gives the jobs it
    removed of each round, by round: round 5's two and round 6's two. The season-end job is
    removed but left out of what it gives, in the same pass, listing the store once, and round 7
    is not named."""
    jobs = [
        _round_job("results_r5", 5), _round_job("rsvp_cleanup_r5", 5),
        _round_job("results_r6", 6), _round_job("cleanup_r6", 6),
        _round_job("results_r7", 7), SimpleNamespace(id="season_end_3", kwargs={}),
    ]
    service = _service(jobs)

    removed = service.cancel_rounds([5, 6], season_end=True)

    assert {round_id: sorted(ids) for round_id, ids in removed.items()} == {
        5: ["results_r5", "rsvp_cleanup_r5"],
        6: ["cleanup_r6", "results_r6"],
    }
    assert sorted(call.args[0] for call in service._scheduler.remove_job.call_args_list) == [
        "cleanup_r6", "results_r5", "results_r6", "rsvp_cleanup_r5", "season_end_3",
    ]
    assert service._scheduler.get_jobs.call_count == 1


def test_schedule_round_cleanups_arms_both_clean_ups_from_the_post_race_delay():
    """Round 2 of division tier 1 in season 3, round id 5, starts on 1 June 2099 at 14:00. Arming
    its clean-ups alone adds two jobs and no other: the forecast's clean-up and the check-in's,
    each named for the round, each firing a day after the start, and each carrying the round."""
    from datetime import datetime, timezone

    from leaguebot.core.models.round import Round, RoundFormat
    from leaguebot.core.services.scheduler_service import POST_RACE_CLEANUP_DELAY

    start = datetime(2099, 6, 1, 14, 0, tzinfo=timezone.utc)
    rnd = Round(
        id=5, division_id=1, round_number=2, format=RoundFormat.NORMAL,
        track_name="Bahrain International Circuit", scheduled_at=start,
    )
    service = _service([])

    service.schedule_round_cleanups(rnd, season_number=3, division_tier=1)

    armed = {
        call.kwargs["id"]: call for call in service._scheduler.add_job.call_args_list
    }
    assert sorted(armed) == ["cleanup_s3_d1_r2_id5", "rsvp_cleanup_s3_d1_r2_id5"]
    for call in armed.values():
        assert call.kwargs["trigger"].run_date == start + POST_RACE_CLEANUP_DELAY
        assert call.kwargs["kwargs"] == {"round_id": 5}
        assert call.kwargs["replace_existing"] is True


async def test_a_job_stands_while_the_store_holds_it(tmp_path, monkeypatch):
    """`has_job` answers whether a job is armed and not yet fired, read of a real job store: the
    signup close timer, armed a day ahead, stands until it is cancelled, and a job never armed
    does not. The finish of a signup window's close a stop cut off reads it to pass over a
    channel already held."""
    from datetime import datetime, timedelta, timezone

    from leaguebot.core.db.database import run_migrations
    from leaguebot.core.services import scheduler_service
    from leaguebot.core.services.scheduler_service import SIGNUP_CLOSE_JOB_ID

    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    monkeypatch.setattr(scheduler_service, "_GLOBAL_SERVICE", None)
    service = SchedulerService(db_path, str(tmp_path / "jobs.db"))
    service.start()
    try:
        fire_at = datetime.now(timezone.utc) + timedelta(days=1)
        service.schedule_signup_close_timer(fire_at.replace(tzinfo=None).isoformat())

        assert service.has_job(SIGNUP_CLOSE_JOB_ID) is True
        assert service.has_job("wizard_channel_delete_102") is False

        service.cancel_signup_close_timer()
        assert service.has_job(SIGNUP_CLOSE_JOB_ID) is False
    finally:
        service._scheduler.shutdown(wait=False)
        service._scheduler._jobstores["default"].engine.dispose()
