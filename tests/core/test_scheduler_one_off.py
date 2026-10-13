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

NO_ONE_OFF_TIMER = "#439: the scheduler service arms no one-off timer that asks a change"


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


@pytest.mark.xfail(strict=True, reason=NO_ONE_OFF_TIMER)
async def test_a_one_off_timer_is_armed_with_no_lateness_limit(services):
    """A lock warning's clear is armed for an hour from now: the scheduler holds one job for it,
    and that job has no lateness limit."""
    service = services()
    service.register_timer("review_lock_clear", lambda _key: None)
    service.start()

    service.arm_once("review_lock_clear", 77, _now() + timedelta(hours=1))

    [job] = service._scheduler.get_jobs()
    assert job.misfire_grace_time is None


@pytest.mark.xfail(strict=True, reason=NO_ONE_OFF_TIMER)
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


@pytest.mark.xfail(strict=True, reason=NO_ONE_OFF_TIMER)
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


@pytest.mark.xfail(strict=True, reason=NO_ONE_OFF_TIMER)
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


@pytest.mark.xfail(strict=True, reason=NO_ONE_OFF_TIMER)
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
    from leaguebot.core.services.change_queue import ChangeType, Step

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
