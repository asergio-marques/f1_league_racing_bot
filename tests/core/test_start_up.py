"""The bot's start-up, run once per process (architecture.md, "Start-up runs once, in one function",
steps 5 to 7, and "One sweep picks up the timed events that were missed").

`start_up` is handed the builder's ordered steps, each a name and an awaitable callable, the change
queue and the scheduler service. It starts the scheduler paused, so the job store is readable and
no job runs, runs each step in turn, a step that raises being logged by name and the next one run,
then starts the queue, and resumes the scheduler last. discord.py may report the bot ready more
than once in a run, after a network drop; the second call runs nothing.

Driven with stub steps, a stub queue and a stub scheduler, each recording what is done to it, so no
Discord and no real scheduler is needed. The guard lives in the module, once per process, so each
test reloads it for a fresh one.
"""
from __future__ import annotations

import importlib
import logging
from typing import Any

_MODULE = "leaguebot.core.services.start_up"


def _fresh_start_up() -> Any:
    """The start-up module, reloaded so that its run-once guard has not yet been spent."""
    return importlib.reload(importlib.import_module(_MODULE))


class _Recorder:
    """A stub change queue and scheduler, and steps, writing what is done to them to one list."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.queue = self._Queue(self.calls)
        self.scheduler = self._Scheduler(self.calls)

    def step(self, name: str) -> tuple[str, Any]:
        async def run() -> None:
            self.calls.append(name)

        return name, run

    def failing_step(self, name: str) -> tuple[str, Any]:
        async def run() -> None:
            self.calls.append(name)
            raise RuntimeError("the portrait job could not be read")

        return name, run

    class _Queue:
        def __init__(self, calls: list[str]) -> None:
            self._calls = calls

        async def start(self) -> None:
            self._calls.append("queue started")

    class _Scheduler:
        def __init__(self, calls: list[str]) -> None:
            self._calls = calls

        def start(self, paused: bool = False) -> None:
            self._calls.append("scheduler started paused" if paused else "scheduler started")

        def resume(self) -> None:
            self._calls.append("scheduler resumed")


async def test_start_up_runs_once_on_a_second_ready(caplog):
    """The bot is reported ready twice in one run, as after a network drop. The first call runs
    the one step, starts the queue and resumes the scheduler (their order is
    `test_no_job_runs_until_start_up_has_ended`'s); the second runs, starts and resumes nothing
    again, and logs that start-up was skipped."""
    module = _fresh_start_up()
    record = _Recorder()
    steps = (record.step("hub"),)

    await module.start_up(steps=steps, change_queue=record.queue, scheduler=record.scheduler)
    first = list(record.calls)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await module.start_up(steps=steps, change_queue=record.queue, scheduler=record.scheduler)

    assert {"hub", "queue started", "scheduler resumed"} <= set(first)
    assert record.calls == first
    assert [r for r in caplog.records
            if r.name == _MODULE and "skip" in r.getMessage().lower()], \
        "the second call logs no line saying start-up was skipped"


async def test_start_up_runs_its_steps_in_order():
    """Three steps are given as missed phases, then orphaned amend channels, then the hub: they run
    in that order, which is not their names' order, and only after the last does the queue
    start."""
    module = _fresh_start_up()
    record = _Recorder()
    steps = (record.step("missed phases"), record.step("orphaned amend channels"),
             record.step("hub"))

    await module.start_up(steps=steps, change_queue=record.queue, scheduler=record.scheduler)

    assert [call for call in record.calls if not call.startswith("scheduler")] == [
        "missed phases", "orphaned amend channels", "hub", "queue started",
    ]


async def test_a_failing_step_does_not_stop_the_rest(caplog):
    """Of three steps, the second (the portrait refresh job) raises. It is logged as an error
    naming the step, with its traceback; the third step still runs, the queue still starts and the
    scheduler is still resumed."""
    module = _fresh_start_up()
    record = _Recorder()
    steps = (record.step("amendment sweep"), record.failing_step("portrait refresh job"),
             record.step("signup close timer"))

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await module.start_up(steps=steps, change_queue=record.queue, scheduler=record.scheduler)

    assert record.calls[1:] == ["amendment sweep", "portrait refresh job", "signup close timer",
                                "queue started", "scheduler resumed"]
    [error] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert "portrait refresh job" in error.getMessage()
    assert error.exc_info is not None and error.exc_info[0] is RuntimeError


async def test_no_job_runs_until_start_up_has_ended():
    """Two steps run at start-up. The scheduler is started paused before the first of them, so its
    job store is readable and no job runs, and it is resumed only once the queue has started, as
    the last thing start-up does; it is never started unpaused."""
    module = _fresh_start_up()
    record = _Recorder()
    steps = (record.step("amendment sweep"), record.step("hub"))

    await module.start_up(steps=steps, change_queue=record.queue, scheduler=record.scheduler)

    assert record.calls[0] == "scheduler started paused"
    assert "scheduler started" not in record.calls
    assert record.calls[-2:] == ["queue started", "scheduler resumed"]
    assert record.calls.count("scheduler resumed") == 1


async def test_a_queue_that_fails_to_start_still_resumes_the_scheduler(caplog):
    """The change queue fails as it starts (the database is locked). Start-up logs the failure as
    an error, with its traceback, and still resumes the scheduler, so scheduled jobs are not held
    paused for the rest of the run; the failure goes no further, as a failed step's does not."""
    module = _fresh_start_up()
    record = _Recorder()

    async def _fails() -> None:
        record.calls.append("queue start failed")
        raise RuntimeError("database is locked")

    record.queue.start = _fails
    steps = (record.step("hub"),)

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await module.start_up(steps=steps, change_queue=record.queue, scheduler=record.scheduler)

    assert record.calls[-2:] == ["queue start failed", "scheduler resumed"]
    [error] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert error.exc_info is not None and error.exc_info[0] is RuntimeError


async def test_a_review_prompt_recovery_that_cannot_read_the_database_fails_its_step(
    tmp_path, caplog
):
    """The real bot's 'expired review prompts' step, run by start-up over a database whose review
    prompts cannot be read (their table is gone), is logged as a failed step naming it, with the
    database error and its traceback, and the next step, the missed phases, still runs."""
    import sqlite3
    from datetime import datetime, timezone

    from leaguebot.core.db.database import get_connection
    from tests.support.change_queue import built_bot

    bot = await built_bot(tmp_path, now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))
    [step] = [step for step in bot.start_up_steps if step[0] == "expired review prompts"]
    async with get_connection(bot.db_path) as db:
        await db.execute("DROP TABLE season_review_prompts")
        await db.commit()
    module = _fresh_start_up()
    record = _Recorder()

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await module.start_up(steps=(step, record.step("missed phases")),
                              change_queue=record.queue, scheduler=record.scheduler)

    assert "missed phases" in record.calls
    errors = [r for r in caplog.records if r.name == _MODULE and r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["Start-up step 'expired review prompts' failed"]
    [error] = errors
    assert error.exc_info is not None and issubclass(error.exc_info[0], sqlite3.Error)
