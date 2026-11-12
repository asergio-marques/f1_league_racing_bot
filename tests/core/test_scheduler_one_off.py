"""One-off timers that ask a change, armed with no lateness limit (#439, 6b).

The queue has no delay. A message the bot deletes some time after posting it, an amendment's five
minutes, a correction's five minutes and a season review's five minutes are each a one-off timer,
armed through `SchedulerService.arm_once(kind, key, run_at)` by a job of its own. The builder
registers each kind's callback (`register_timer(kind, callback)`), which is handed the key, reads
its row again and asks its change. A timer is armed with no lateness limit
(`misfire_grace_time=None`, architecture.md "Timed work and restarts"), so one missed while the
bot was down fires when it starts.

These tests use the real `SchedulerService`, on job-store files the test tears down, Windows
included. `arm_once`, `register_timer` and the queue's lanes are unbuilt, so each test fails until
they are.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from leaguebot.core.db.database import run_migrations
from tests.support.change_queue import (
    Clock,
    attach_queue,
    change_rows,
    league_double,
    run_queue,
    seed_server,
    stopped_job,
)


def _dispose(service: Any) -> None:
    """Stop the scheduler and close the job store's connections, so its file can be deleted."""
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        service._scheduler._jobstores["default"].engine.dispose()
    except Exception:
        pass


@pytest.fixture
def services(tmp_path, monkeypatch):
    """Build scheduler services on one league database and one job store, as the bot does at each
    start; every one is stopped and its job store closed once the test has run."""
    from leaguebot.core.services import scheduler_service

    monkeypatch.setattr(scheduler_service, "_GLOBAL_SERVICE", None)
    made: list[Any] = []

    def build() -> Any:
        service = scheduler_service.SchedulerService(
            str(tmp_path / "bot.db"), str(tmp_path / "jobs.db")
        )
        made.append(service)
        return service

    yield build
    for service in made:
        _dispose(service)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0.01)


async def test_a_one_off_timer_is_armed_with_no_lateness_limit(services):
    """A lock warning's clear is armed for an hour from now: the scheduler holds one job for it,
    and that job has no lateness limit."""
    service = services()
    service.register_timer("review_lock_clear", lambda _key: None)
    service.start()

    service.arm_once("review_lock_clear", 77, _now() + timedelta(hours=1))

    [job] = service._scheduler.get_jobs()
    assert job.misfire_grace_time is None


async def test_a_one_off_timer_hands_its_key_to_the_callback_the_builder_registered(services):
    """Two kinds are registered, and a timer of one is armed a moment from now for the message
    77: when it fires, its kind's callback alone is called, handed 77."""
    service = services()
    handed: dict[str, list[Any]] = {"review_lock_clear": [], "amend_lapse": []}
    fired = asyncio.Event()

    async def _clear(key: Any) -> None:
        handed["review_lock_clear"].append(key)
        fired.set()

    async def _lapse(key: Any) -> None:
        handed["amend_lapse"].append(key)

    service.register_timer("review_lock_clear", _clear)
    service.register_timer("amend_lapse", _lapse)
    service.start()

    service.arm_once("review_lock_clear", 77, _now() + timedelta(milliseconds=300))
    await asyncio.wait_for(fired.wait(), 10)
    await _settle()

    assert handed == {"review_lock_clear": [77], "amend_lapse": []}


async def test_a_one_off_timer_armed_again_for_the_same_key_replaces_the_earlier(services):
    """An amendment's five minutes are armed, then armed again for a later prompt: one timer
    stands for that amendment, at the later time. A timer of another kind for the same key, and
    one of the same kind for another key, stand beside it; `disarm` takes one away alone."""
    service = services()
    for kind in ("amend_lapse", "correction_lapse"):
        service.register_timer(kind, lambda _key: None)
    service.start()
    first = _now() + timedelta(hours=1)
    later = first + timedelta(minutes=4)

    service.arm_once("amend_lapse", 5, first)
    service.arm_once("amend_lapse", 5, later)
    service.arm_once("amend_lapse", 6, first)
    service.arm_once("correction_lapse", 5, first)

    run_dates = sorted(job.trigger.run_date for job in service._scheduler.get_jobs())
    assert run_dates == [first, first, later]

    service.disarm("amend_lapse", 5)

    assert sorted(job.trigger.run_date for job in service._scheduler.get_jobs()) == [first, first]


async def test_a_timer_and_a_job_say_when_they_are_due(services):
    """A held channel's deletion is armed for an hour from now: the scheduler says when the timer
    is due by its kind and key, and when the job under its id is, and says nothing of a timer
    never armed, a job that does not stand, or a timer taken away."""
    service = services()
    service.register_timer("signup_channel_delete", lambda _key: None)
    service.start()
    due = _now() + timedelta(hours=1)

    service.arm_once("signup_channel_delete", "6101", due)

    assert service.timer_due("signup_channel_delete", "6101") == due
    assert service.job_due("once:signup_channel_delete:6101") == due
    assert service.timer_due("signup_channel_delete", "6102") is None
    assert service.job_due("no_such_job") is None

    service.disarm("signup_channel_delete", "6101")

    assert service.timer_due("signup_channel_delete", "6101") is None


async def test_a_one_off_timer_missed_while_the_bot_was_down_fires_at_start(services):
    """A correction's five minutes were armed, and the bot went down before they ran out and
    stayed down two hours past them. When the bot starts again and the builder registers the
    kind's callback, the timer fires, handed its key: it was armed with no lateness limit."""
    before = services()
    before._scheduler.start(paused=True)
    before.arm_once("correction_lapse", 4343, _now() - timedelta(hours=2))
    _dispose(before)

    after = services()
    handed: list[Any] = []
    fired = asyncio.Event()

    async def _lapse(key: Any) -> None:
        handed.append(key)
        fired.set()

    after.register_timer("correction_lapse", _lapse)
    after.start()
    await asyncio.wait_for(fired.wait(), 10)
    await _settle()

    assert handed == [4343]


async def test_a_timer_missed_while_down_waits_until_the_scheduler_is_resumed(services):
    """A correction's five minutes ran out two hours ago, while the bot was down. Started again
    paused, as start-up starts it, the scheduler can read the timer from its job store but does
    not fire it; once resumed, as the last thing start-up does, it fires, handed its key."""
    before = services()
    before._scheduler.start(paused=True)
    before.arm_once("correction_lapse", 4343, _now() - timedelta(hours=2))
    _dispose(before)

    after = services()
    handed: list[Any] = []
    fired = asyncio.Event()

    async def _lapse(key: Any) -> None:
        handed.append(key)
        fired.set()

    after.register_timer("correction_lapse", _lapse)
    after.start(paused=True)
    await _settle()

    assert handed == []
    assert after.timer_due("correction_lapse", 4343) is not None

    after.resume()
    await asyncio.wait_for(fired.wait(), 10)
    await _settle()

    assert handed == [4343]


@pytest.mark.xfail(
    strict=True,
    reason="#285: the last check-in notice's timer is still armed with the scheduler's default "
    "lateness limit, so one missed while the bot was down is dropped at start",
)
async def test_a_last_notice_missed_while_the_bot_was_down_is_sent_when_it_is_back(services):
    """Round 3 of division tier 1 in season 7 (id 21) starts 20 hours from now, its last notice
    24 hours before the race and its deadline 2 hours before. Its check-in timers were armed six
    days before the race, and the bot went down; its last notice fell due four hours ago while
    it was down, the deadline still ahead. When the bot starts again and attendance registers
    its timers, the last notice's timer fires, handed round 21, so its notice is asked for and
    sent late rather than dropped."""
    from types import SimpleNamespace

    start = _now() + timedelta(hours=20)
    before = services()
    before._scheduler.start(paused=True)
    before.schedule_attendance_round(
        SimpleNamespace(id=21, division_id=11, round_number=3, scheduled_at=start),
        season_number=7, division_tier=1, notice_days=5, last_notice_hours=24,
        deadline_hours=2, now=start - timedelta(days=6),
    )
    _dispose(before)

    after = services()
    handed: list[tuple[str, Any]] = []
    fired = asyncio.Event()

    def _timer(kind: str) -> Any:
        async def _fire(key: Any) -> None:
            handed.append((kind, key))
            if kind == "rsvp_last_notice":
                fired.set()
        return _fire

    for kind in ("rsvp_notice", "rsvp_last_notice", "rsvp_deadline", "rsvp_cleanup"):
        after.register_timer(kind, _timer(kind))
    after.start()
    await asyncio.wait_for(fired.wait(), 5)
    await _settle()

    assert handed.count(("rsvp_last_notice", 21)) == 1
    assert ("rsvp_deadline", 21) not in handed


async def test_a_backup_taken_during_start_up_leaves_the_scheduler_paused(services):
    """A correction's timer fell due while the bot was down, and start-up holds the scheduler
    paused while its steps run. A backup taken meanwhile pauses the scheduler around its copy
    (`paused()`); once the copy is done the scheduler is still paused and the timer has not fired,
    since only start-up's last act resumes it. Then it fires."""
    before = services()
    before._scheduler.start(paused=True)
    before.arm_once("correction_lapse", 4343, _now() - timedelta(hours=2))
    _dispose(before)

    after = services()
    handed: list[Any] = []
    fired = asyncio.Event()

    async def _lapse(key: Any) -> None:
        handed.append(key)
        fired.set()

    after.register_timer("correction_lapse", _lapse)
    after.start(paused=True)

    with after.paused():
        pass
    await _settle()

    assert handed == []

    after.resume()
    await asyncio.wait_for(fired.wait(), 10)
    assert handed == [4343]


async def test_a_lane_armed_timer_asks_on_its_conversation_s_lane(services, tmp_path):
    """A round's review channel, 707, holds a lock warning whose clear is armed. The league lane
    is stopped at a change of its own. When the timer fires, the callback the builder registered
    asks the clear as the bot's: it is saved on the channel's lane, not the league's, and runs
    there while the league stays stopped."""
    from leaguebot.core.models.change import (
        ChangeOrigin,
        PlannedStep,
        StepFailedOnDiscord,
        StepKind,
        StepResult,
        Verdict,
    )
    from leaguebot.core.services.queue.change_types import ChangeType, Step

    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    cleared: list[int] = []

    async def _go(_ctx):
        return Verdict.go()

    async def _refused(_ctx):
        raise StepFailedOnDiscord("Missing Access")

    async def _clear(ctx):
        cleared.append(ctx.payload["message_id"])
        return StepResult()

    def _type(kind: str, run: Any, **lane: Any) -> Any:
        return ChangeType(
            kind=kind, opening=(PlannedStep("go"),), steps={"go": Step("go", StepKind.ACT, run)},
            check=_go, key=lambda payload: f"{kind}|{json.dumps(payload, sort_keys=True)}",
            doing=lambda _payload: "Doing it", outcome=lambda _ctx: "", **lane,
        )

    attach_queue(bot, db_path, now=Clock(_now()), types=(
        _type("league_work", _refused),
        _type("review_lock_clear", _clear,
              lane=lambda payload: f"channel:{payload['channel_id']}"),
    ))
    await bot.change_queue.ask("league_work", {}, what="`/league`", origin=ChangeOrigin.BOT)
    await run_queue(bot)
    assert await stopped_job(db_path, "league") is not None

    service = services()
    fired = asyncio.Event()

    async def _on_timer(key: Any) -> None:
        await bot.change_queue.ask(
            "review_lock_clear", {"channel_id": 707, "message_id": key},
            what="clearing the lock warning", origin=ChangeOrigin.BOT,
        )
        fired.set()

    service.register_timer("review_lock_clear", _on_timer)
    service.start()
    service.arm_once("review_lock_clear", 77, _now() + timedelta(milliseconds=300))
    await asyncio.wait_for(fired.wait(), 10)
    await _settle()
    await run_queue(bot)

    lanes = {row["kind"]: (row["lane"], row["state"]) for row in await change_rows(db_path)}
    assert lanes["review_lock_clear"] == ("channel:707", "DONE")
    assert cleared == [77]
    assert await stopped_job(db_path, "league") is not None


# ---------------------------------------------------------------------------
# Every job the scheduler arms runs through the one runner (#283, S7a)
# ---------------------------------------------------------------------------

#: Pro's round 3 (id 21) of season 7, tier 1, a month away, so that no job of it is due.
_ROUND_ID = 21
_ROUND_SUFFIX = "_s7_d1_r3_id21"


def _a_round() -> Any:
    from types import SimpleNamespace

    start = _now().replace(microsecond=0) + timedelta(days=30)
    return SimpleNamespace(id=_ROUND_ID, division_id=11, round_number=3, scheduled_at=start)


def _arm_round(service: Any, rnd: Any) -> None:
    service.schedule_round(rnd, season_number=7, division_tier=1)


def _arm_check_in(service: Any, rnd: Any) -> None:
    service.schedule_attendance_round(
        rnd, season_number=7, division_tier=1, notice_days=5, last_notice_hours=24,
        deadline_hours=2, now=_now(),
    )


def _arm_cleanups(service: Any, rnd: Any) -> None:
    service.schedule_round_cleanups(rnd, season_number=7, division_tier=1, now=_now())


def _arm_results_only(service: Any, rnd: Any) -> None:
    service.schedule_result_submission_jobs([rnd], division_meta={11: (7, 1)})


def _arm_results_now(service: Any, rnd: Any) -> None:
    service.run_result_submission_now(rnd, season_number=7, division_tier=1)


def _arm_signup_close(service: Any, _rnd: Any) -> None:
    service.schedule_signup_close_timer((_now() + timedelta(days=3)).isoformat())


def _arm_portrait_refresh(service: Any, _rnd: Any) -> None:
    service.schedule_portrait_refresh("06:00")


def _arm_amendment_sweep(service: Any, _rnd: Any) -> None:
    service.schedule_amendment_sweep()


#: Every kind the builder signs up for the scheduler's own jobs, with the method that arms it,
#: the id it is stored under, and the key its handler is handed (None: a standing job, whose
#: handler is handed nothing). A round's kind is its job id's prefix and its key the round id.
EVERY_ARMED_KIND = [
    pytest.param("weather_p1", _arm_round, f"weather_p1{_ROUND_SUFFIX}", _ROUND_ID,
                 id="weather_p1-schedule_round"),
    pytest.param("weather_p2", _arm_round, f"weather_p2{_ROUND_SUFFIX}", _ROUND_ID,
                 id="weather_p2-schedule_round"),
    pytest.param("weather_p3", _arm_round, f"weather_p3{_ROUND_SUFFIX}", _ROUND_ID,
                 id="weather_p3-schedule_round"),
    pytest.param("cleanup", _arm_round, f"cleanup{_ROUND_SUFFIX}", _ROUND_ID,
                 id="cleanup-schedule_round"),
    pytest.param("results", _arm_round, f"results{_ROUND_SUFFIX}", _ROUND_ID,
                 id="results-schedule_round"),
    pytest.param("results", _arm_results_only, f"results{_ROUND_SUFFIX}", _ROUND_ID,
                 id="results-schedule_result_submission_jobs"),
    pytest.param("results", _arm_results_now, f"results{_ROUND_SUFFIX}", _ROUND_ID,
                 id="results-run_result_submission_now"),
    pytest.param("rsvp_notice", _arm_check_in, f"rsvp_notice{_ROUND_SUFFIX}", _ROUND_ID,
                 id="rsvp_notice-schedule_attendance_round"),
    pytest.param("rsvp_last_notice", _arm_check_in, f"rsvp_last_notice{_ROUND_SUFFIX}",
                 _ROUND_ID, id="rsvp_last_notice-schedule_attendance_round"),
    pytest.param("rsvp_deadline", _arm_check_in, f"rsvp_deadline{_ROUND_SUFFIX}", _ROUND_ID,
                 id="rsvp_deadline-schedule_attendance_round"),
    pytest.param("rsvp_cleanup", _arm_check_in, f"rsvp_cleanup{_ROUND_SUFFIX}", _ROUND_ID,
                 id="rsvp_cleanup-schedule_attendance_round"),
    pytest.param("cleanup", _arm_cleanups, f"cleanup{_ROUND_SUFFIX}", _ROUND_ID,
                 id="cleanup-schedule_round_cleanups"),
    pytest.param("rsvp_cleanup", _arm_cleanups, f"rsvp_cleanup{_ROUND_SUFFIX}", _ROUND_ID,
                 id="rsvp_cleanup-schedule_round_cleanups"),
    pytest.param("signup_close", _arm_signup_close, "signup_close", None, id="signup_close"),
    pytest.param("portrait_refresh", _arm_portrait_refresh, "pfp_daily", None,
                 id="portrait_refresh"),
    pytest.param("amendment_sweep", _arm_amendment_sweep, "amend_sweep", None,
                 id="amendment_sweep"),
]


def _stored(service: Any, arm: Any, job_id: str) -> Any:
    """Arm the round's jobs (or the standing job) on a paused scheduler, and read back the job
    stored under *job_id*, which nothing has fired."""
    service.start(paused=True)
    arm(service, _a_round())
    job = service._scheduler.get_job(job_id)
    assert job is not None, f"nothing is stored under {job_id}"
    return job


@pytest.mark.parametrize("kind, arm, job_id, key", EVERY_ARMED_KIND)
async def test_every_kind_runs_through_one_runner(services, kind, arm, job_id, key):
    """Every kind is signed up with a handler of its own, and the job is armed by its method:
    the job stands under the id it has always had, its callable is the one runner a one-off
    timer runs through, and it is stored as a one-off timer is, with its kind and its key as
    keywords. Fired with them, the runner
    calls that kind's handler alone, once, with the key (a standing job's with nothing)."""
    from leaguebot.core.services import scheduler_service

    service = services()
    calls: dict[str, list[tuple[Any, ...]]] = {}

    def _handler_of(name: str) -> Any:
        async def _handler(*args: Any) -> None:
            calls.setdefault(name, []).append(args)
        return _handler

    for each in {param.values[0] for param in EVERY_ARMED_KIND}:
        service.register_timer(each, _handler_of(each))

    job = _stored(service, arm, job_id)

    assert job.func is scheduler_service._one_off_timer_job
    assert job.args == ()
    if key is None:
        assert job.kwargs in ({"kind": kind}, {"kind": kind, "key": None})
    else:
        assert job.kwargs == {"kind": kind, "key": key}

    await job.func(*job.args, **job.kwargs)

    assert calls == {kind: [() if key is None else (key,)]}


@pytest.mark.parametrize("kind, arm, job_id, key", EVERY_ARMED_KIND)
async def test_a_failing_job_keeps_its_traceback(services, caplog, kind, arm, job_id, key):
    """The kind's handler raises when its job fires: the fault is logged at ERROR with its
    traceback, naming the kind and the key, and nothing is raised to the scheduler."""
    service = services()

    async def _fails(*_args: Any) -> None:
        raise RuntimeError("the handler broke")

    service.register_timer(kind, _fails)
    job = _stored(service, arm, job_id)

    with caplog.at_level("ERROR"):
        await job.func(*job.args, **job.kwargs)  # must not raise

    [record] = [r for r in caplog.records if r.levelname == "ERROR"]
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "the handler broke"
    assert kind in record.getMessage()
    if key is not None:
        assert str(key) in record.getMessage()


#: The one runner, as the job store saves it for every job: its module's path and its name.
RUNNER_REFERENCE = "leaguebot.core.services.scheduler_service:_one_off_timer_job"


async def test_every_stored_job_names_the_runner_by_its_pinned_module_path(services):
    """Every way the scheduler arms a job is used, a one-off timer's included, on a paused
    scheduler; every job is then read back as the job store's file holds it, and each names the
    one runner by `leaguebot.core.services.scheduler_service:_one_off_timer_job`.

    **The path is pinned, and `scheduler_service.py` stays where it is.** `SQLAlchemyJobStore`
    saves a job's callable as text, its module's path and its name, and imports that path again
    when it loads the job. Moving the file, or renaming the runner, would leave every job a saved
    job store holds unloadable: the host's own after an update, and the scheduler's database in
    every saved test-mode state (core_specification.md, "Saving a state and returning to it"). So
    while core's other services are grouped into topic folders, this one stays at
    `core/services/scheduler_service.py` (#283), and a move fails here before it lands."""
    import pickle
    import sqlite3

    service = services()
    service.start(paused=True)
    rnd = _a_round()
    for arm in dict.fromkeys(param.values[1] for param in EVERY_ARMED_KIND):
        arm(service, rnd)
    service.arm_once("review_lock_clear", 77, _now() + timedelta(hours=1))

    connection = sqlite3.connect(service.jobstore_path)
    try:
        stored = {
            job_id: pickle.loads(state)["func"]
            for job_id, state in connection.execute("SELECT id, job_state FROM apscheduler_jobs")
        }
    finally:
        connection.close()

    expected = {param.values[2] for param in EVERY_ARMED_KIND} | {"once:review_lock_clear:77"}
    assert expected <= set(stored)
    assert set(stored.values()) == {RUNNER_REFERENCE}
