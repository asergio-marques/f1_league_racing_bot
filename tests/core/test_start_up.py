"""The bot's start-up, run once per process: the sweep (architecture.md, "Start-up runs once, in one
function", steps 5 to 7, and "Timed work and restarts").

`start_up` is handed the builder's `StartingHook`, the module service, the change queue, the
scheduler service and the log channel's `post_log`. It starts the scheduler paused, so the job store
is readable and no job runs; inside the queue's `sweeping()` it runs core's own steps in the order
they signed up, then turns to each module in sign-up order, reads its switch, passes it over where it
is off and otherwise runs its steps in its own order; then it starts the queue, and resumes the
scheduler last. A step, a switch read, a module's list of steps or the queue's start that raises is
logged with its traceback and reported to the log channel by one line, and the rest carry on.
discord.py may report the bot ready more than once in a run, after a network drop; the second call
runs nothing.

Driven with stub steps, a stub module service, a stub queue and a stub scheduler, each recording
what is done to it, so no Discord and no real scheduler is needed. The guard lives in the module,
once per process, so each test reloads it for a fresh one. `StartingHook` and the new call are not
built yet, so each test imports them inside itself.
"""
from __future__ import annotations

import asyncio
import contextlib
import functools
import importlib
import logging
from contextlib import asynccontextmanager
from typing import Any

import pytest

_MODULE = "leaguebot.core.services.upkeep.start_up"


def _fresh_start_up() -> Any:
    """The start-up module, reloaded so that its run-once guard has not yet been spent."""
    return importlib.reload(importlib.import_module(_MODULE))


def _starting() -> Any:
    """An empty `StartingHook`."""
    from leaguebot.core.utils.hooks import StartingHook

    return StartingHook()


class _Fault(RuntimeError):
    """The fault a failing step raises in these tests, its name the kind the log line gives."""


class _Recorder:
    """A stub module service, change queue and scheduler, the log channel, and steps, writing what is
    done to them to one list; the log channel's lines go to a list of their own."""

    def __init__(self, switches: dict[str, bool] | None = None) -> None:
        self.calls: list[str] = []
        self.lines: list[str] = []
        self.queue = self._Queue(self.calls)
        self.scheduler = self._Scheduler(self.calls)
        self.modules = self._Modules(self.calls, switches or {})

    def step(self, name: str) -> tuple[str, Any]:
        async def run() -> None:
            self.calls.append(f"{name} began")
            # Give way to the loop, so a step not awaited before the next would show interleaved.
            await asyncio.sleep(0)
            self.calls.append(name)

        return name, run

    def failing_step(self, name: str, error: BaseException) -> tuple[str, Any]:
        async def run() -> None:
            self.calls.append(name)
            raise error

        return name, run

    def module_steps(self, *steps: tuple[str, Any]) -> Any:
        """A module's wiring function, bound: handed nothing, it gives *steps* in its order."""
        return lambda: list(steps)

    async def post_log(self, content: str, **_: Any) -> None:
        self.lines.append(content)

    def ran(self) -> list[str]:
        """The steps that finished, in order, without the scheduler's, the queue's or the hold's."""
        return [call for call in self.calls
                if not call.endswith(" began") and not call.startswith(("scheduler", "queue",
                                                                         "hold", "switch"))]

    def start_up(self, module: Any, starting: Any) -> Any:
        return module.start_up(starting=starting, modules=self.modules, change_queue=self.queue,
                               scheduler=self.scheduler, post_log=self.post_log)

    class _Modules:
        def __init__(self, calls: list[str], switches: dict[str, bool]) -> None:
            self._calls = calls
            self.switches = switches
            self.failing: dict[str, BaseException] = {}

        async def is_enabled(self, module: str) -> bool:
            self._calls.append(f"switch {module}")
            if module in self.failing:
                raise self.failing[module]
            return self.switches.get(module, True)

    class _Queue:
        """Held, as `main` leaves it before the bot connects. The hold is lifted on leaving
        `sweeping()` or by `release()`; lifting it again is harmless and records nothing."""

        def __init__(self, calls: list[str]) -> None:
            self._calls = calls
            self.held = True

        def release(self) -> None:
            if self.held:
                self.held = False
                self._calls.append("hold released")

        @asynccontextmanager
        async def sweeping(self) -> Any:
            self._calls.append("hold sweep began")
            try:
                yield
            finally:
                self.release()

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
    core's one step, starts the queue and resumes the scheduler (their order is
    `test_no_job_runs_until_start_up_has_ended`'s); the second runs, starts and resumes nothing
    again, and logs that start-up was skipped."""
    module = _fresh_start_up()
    record = _Recorder()
    starting = _starting()
    starting.subscribe(record.step("hub")[1], name="hub")

    await record.start_up(module, starting)
    first = list(record.calls)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await record.start_up(module, starting)

    assert {"hub", "queue started", "scheduler resumed"} <= set(first)
    assert record.calls == first
    assert [r for r in caplog.records
            if r.name == _MODULE and "skip" in r.getMessage().lower()], \
        "the second call logs no line saying start-up was skipped"


async def test_the_sweep_runs_core_s_steps_then_each_enabled_module_s_in_turn():
    """Core signs up two steps, the expired review prompts and then the hub; results then signs up
    its steps, the amendment sweep and then the orphaned amend channels; attendance then its, the
    missed check-in calls and then the RSVP views and deadlines. Results and attendance are both on.
    The sweep runs core's two in their order, then results' two in theirs, then attendance's two
    in theirs, which is not their names' order, each finished before the next begins; only after
    the last does the queue start."""
    module = _fresh_start_up()
    record = _Recorder({"results": True, "attendance": True})
    starting = _starting()
    for name in ("expired review prompts", "hub"):
        starting.subscribe(record.step(name)[1], name=name)
    starting.subscribe(record.module_steps(record.step("amendment sweep"),
                                           record.step("orphaned amend channels")),
                       module="results")
    starting.subscribe(record.module_steps(record.step("missed check-in calls"),
                                           record.step("RSVP views and deadlines")),
                       module="attendance")

    await record.start_up(module, starting)

    order = ["expired review prompts", "hub", "amendment sweep", "orphaned amend channels",
             "missed check-in calls", "RSVP views and deadlines"]
    assert record.ran() == order
    steps = [call for call in record.calls if call.removesuffix(" began") in order]
    assert steps == [call for name in order for call in (f"{name} began", name)], \
        "a step began before the one ahead of it had finished"
    assert record.calls.index("queue started") > record.calls.index("RSVP views and deadlines")


async def test_a_disabled_module_s_steps_are_passed_over(caplog):
    """Results is off and attendance on, each with two steps signed up. None of results' steps runs,
    attendance's both do, the host's log has a line naming results as passed over, and nothing is
    posted to the log channel for it."""
    module = _fresh_start_up()
    record = _Recorder({"results": False, "attendance": True})
    starting = _starting()
    starting.subscribe(record.module_steps(record.step("amendment sweep"),
                                           record.step("orphaned amend channels")),
                       module="results")
    starting.subscribe(record.module_steps(record.step("missed check-in calls"),
                                           record.step("RSVP views and deadlines")),
                       module="attendance")

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await record.start_up(module, starting)

    assert record.ran() == ["missed check-in calls", "RSVP views and deadlines"]
    assert [r for r in caplog.records
            if r.name == _MODULE and r.levelno < logging.ERROR and "results" in r.getMessage()], \
        "the host's log has no line saying results was passed over"
    assert record.lines == []


@pytest.mark.parametrize(("on", "ran"), [
    (False, ["held channels"]),
    (True, ["amendment sweep", "held channels", "orphaned amend channels"]),
], ids=["off", "on"])
async def test_a_step_a_module_marks_runs_while_the_module_is_off(on, ran):
    """Results signs up three steps: the amendment sweep, then the held channels, marked by
    `runs_while_off` as a step that runs whatever results' switch says, then the orphaned amend
    channels, unmarked. With results off, the sweep runs the held channels alone and passes over
    the two unmarked steps; with results on, it runs all three in results' own order. Nothing is
    posted to the log channel either way."""
    from leaguebot.core.utils.hooks import runs_while_off

    module = _fresh_start_up()
    record = _Recorder({"results": on})
    starting = _starting()
    starting.subscribe(record.module_steps(record.step("amendment sweep"),
                                           runs_while_off(record.step("held channels")),
                                           record.step("orphaned amend channels")),
                       module="results")

    await record.start_up(module, starting)

    assert record.ran() == ran
    assert record.lines == []


def _failing_core_step(record: _Recorder, starting: Any, error: BaseException) -> None:
    starting.subscribe(record.failing_step("expired review prompts", error)[1],
                       name="expired review prompts")
    starting.subscribe(record.step("hub")[1], name="hub")
    starting.subscribe(record.module_steps(record.step("amendment sweep")), module="results")
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")


def _failing_module_step(record: _Recorder, starting: Any, error: BaseException) -> None:
    starting.subscribe(record.step("hub")[1], name="hub")
    starting.subscribe(record.module_steps(record.step("missed check-in calls"),
                                           record.failing_step("RSVP views and deadlines", error),
                                           record.step("missed check-in clean-ups")),
                       module="attendance")
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")


#: The first start-up step of each module a switch or list case fails, which never runs.
_FIRST_STEP = {"results": "amendment sweep", "signup": "signup close timer"}


def _failing_switch(record: _Recorder, starting: Any, error: BaseException,
                    key: str = "results") -> None:
    starting.subscribe(record.step("hub")[1], name="hub")
    starting.subscribe(record.module_steps(record.step(_FIRST_STEP[key])), module=key)
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")
    record.modules.failing[key] = error


def _failing_list(record: _Recorder, starting: Any, error: BaseException,
                  key: str = "results") -> None:
    def steps() -> Any:
        raise error

    starting.subscribe(record.step("hub")[1], name="hub")
    starting.subscribe(steps, module=key)
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")


def _failing_queue(record: _Recorder, starting: Any, error: BaseException) -> None:
    async def fails() -> None:
        record.calls.append("queue start failed")
        raise error

    starting.subscribe(record.step("hub")[1], name="hub")
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")
    record.queue.start = fails  # type: ignore[method-assign]


@pytest.mark.parametrize("arrange, named, posted, ran, queue_started", [
    pytest.param(_failing_core_step, "expired review prompts",
                 "❌ The bot's start-up step `expired review prompts` failed — _Fault. "
                 "The details are in the host's log.",
                 ["expired review prompts", "hub", "amendment sweep", "missed phases"], True,
                 id="a core step"),
    pytest.param(_failing_module_step, "RSVP views and deadlines",
                 "❌ The bot's start-up step `RSVP views and deadlines` failed — _Fault. "
                 "The details are in the host's log.",
                 ["hub", "missed check-in calls", "RSVP views and deadlines",
                  "missed check-in clean-ups", "missed phases"], True,
                 id="a module's step"),
    pytest.param(_failing_switch, "results",
                 "❌ The bot's start-up step `results' switch` failed — SwitchNotSignedUp. "
                 "The details are in the host's log.",
                 ["hub", "missed phases"], True,
                 id="a module's switch read"),
    pytest.param(functools.partial(_failing_switch, key="signup"), "signup",
                 "❌ The bot's start-up step `signup's switch` failed — SwitchNotSignedUp. "
                 "The details are in the host's log.",
                 ["hub", "missed phases"], True,
                 id="a module's switch read, its name not ending in s"),
    pytest.param(_failing_list, "results",
                 "❌ The bot's start-up step `results' start-up steps` failed — _Fault. "
                 "The details are in the host's log.",
                 ["hub", "missed phases"], True,
                 id="a module's list of steps"),
    pytest.param(functools.partial(_failing_list, key="signup"), "signup",
                 "❌ The bot's start-up step `signup's start-up steps` failed — _Fault. "
                 "The details are in the host's log.",
                 ["hub", "missed phases"], True,
                 id="a module's list of steps, its name not ending in s"),
    pytest.param(_failing_queue, "queue",
                 "❌ The bot's start-up step `change queue start` failed — _Fault. "
                 "The details are in the host's log.",
                 ["hub", "missed phases"], False,
                 id="the queue's start"),
])
async def test_a_failing_sweep_step_is_reported_to_the_log_channel_and_the_rest_carry_on(
    caplog, arrange, named, posted, ran, queue_started
):
    """One part of the sweep raises: core's expired review prompts step (the hub and two modules'
    steps after it); attendance's RSVP views and deadlines, between its check-in calls and its
    clean-ups, weather after it; results' switch read, a fault in the bot's assembly, weather after
    it; results' list of steps; signup's switch read or list of steps, its name taking "'s" where
    results' takes a bare apostrophe; or the queue's start. One line goes to the log channel, naming the
    part that failed and the kind of fault and saying the detail is in the host's log; the host's log
    has the error with its traceback; every other step runs, a module whose switch or list cannot be
    read passed over; the queue starts unless its start was what failed, and the scheduler is
    resumed, last. The line is pinned word for word, its ❌ mark included: the shape of a Discord
    event's failure line, naming the step that failed."""
    from leaguebot.core.services.modules.module_service import SwitchNotSignedUp

    module = _fresh_start_up()
    record = _Recorder()
    starting = _starting()
    error = (SwitchNotSignedUp(named) if getattr(arrange, "func", arrange) is _failing_switch
             else _Fault("the database is locked"))
    arrange(record, starting, error)

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await record.start_up(module, starting)

    assert record.ran() == ran
    assert record.lines == [posted]
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    [error_record] = errors
    assert named in error_record.getMessage()
    assert error_record.exc_info is not None and error_record.exc_info[1] is error
    assert ("queue started" in record.calls) is queue_started
    assert record.calls[-1] == "scheduler resumed"
    assert record.calls.count("scheduler resumed") == 1


@pytest.mark.parametrize("fails", [None, "step", "scheduler"],
                         ids=["every step done", "a step raised", "the scheduler's start raised"])
async def test_the_sweep_releases_held_requests_before_the_queue_starts(fails):
    """Core's hub step and weather's missed phases run, the hub raising in the second case. Every
    step runs inside the queue's sweep, so the members' requests held meanwhile wait; the hold is
    released once, after the last step and before the queue starts, also where a step raised. In the
    third case the scheduler cannot even be started paused, so the catch-up never begins; the hold
    is still released, once, so the members' requests are not left waiting for the rest of the
    run."""
    module = _fresh_start_up()
    record = _Recorder()
    starting = _starting()
    hub = (record.failing_step("hub", _Fault("hub panel unreadable")) if fails == "step"
           else record.step("hub"))
    starting.subscribe(hub[1], name="hub")
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")
    if fails == "scheduler":
        def cannot_start(paused: bool = False) -> None:
            record.calls.append("scheduler start failed")
            raise _Fault("the job store is unreadable")

        record.scheduler.start = cannot_start  # type: ignore[method-assign]

    with contextlib.suppress(_Fault):
        await record.start_up(module, starting)

    calls = record.calls
    assert calls.count("hold released") == 1
    if fails == "scheduler":
        assert calls.index("scheduler start failed") < calls.index("hold released")
        return
    assert calls.index("hold sweep began") < calls.index("hub")
    assert calls.index("missed phases") < calls.index("hold released") < calls.index(
        "queue started")


async def test_the_sweep_s_off_queue_commands_wait_for_the_catch_up():
    """The bot has restarted and holds its real change queue, as `main` leaves it. Core's hub step
    is mid-way when a league admin invokes a command answered off the queue (as `/bot pack` is),
    worn with `after_the_catch_up`; weather's missed phases come after the hub. The command does
    not act while the sweep runs: its interaction is deferred, privately and thinking, so Discord
    shows "thinking…". Once the last step has run and start-up has released the hold, it acts, and
    its reply goes as a follow-up, never a first response."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.core.utils.catch_up_gate import after_the_catch_up
    from tests.support.change_queue import member_interaction, response_in_its_slot

    module = _fresh_start_up()
    record = _Recorder()
    queue = ChangeQueue("unused.db", MagicMock(), MagicMock(), MagicMock())

    async def start() -> None:
        record.calls.append("queue started")

    queue.start = start  # type: ignore[method-assign]
    queue.hold()
    record.queue = queue  # type: ignore[assignment]
    mid_way, go_on = asyncio.Event(), asyncio.Event()

    async def hub() -> None:
        mid_way.set()
        await go_on.wait()
        record.calls.append("hub")

    class _Cog:
        bot = SimpleNamespace(change_queue=queue)

        @after_the_catch_up(lambda cog: cog.bot.change_queue)
        async def pack(self, interaction: Any) -> None:
            record.calls.append("command acted")
            await interaction.response.send_message("Packed.", ephemeral=True)

    starting = _starting()
    starting.subscribe(hub, name="hub")
    starting.subscribe(record.module_steps(record.step("missed phases")), module="weather")

    sweep = asyncio.create_task(record.start_up(module, starting))
    await asyncio.wait_for(mid_way.wait(), timeout=5)
    press = member_interaction(MagicMock())
    command = asyncio.create_task(_Cog().pack(response_in_its_slot(press)))
    for _ in range(20):
        await asyncio.sleep(0)

    assert not command.done()
    assert "command acted" not in record.calls
    [deferred] = press.response.defer.await_args_list
    assert deferred.kwargs.get("ephemeral") is True
    assert deferred.kwargs.get("thinking") is True

    go_on.set()
    await asyncio.wait_for(sweep, timeout=5)
    await asyncio.wait_for(command, timeout=5)

    assert record.calls.count("command acted") == 1
    assert record.calls.index("command acted") > record.calls.index("missed phases")
    press.followup.send.assert_awaited_once_with("Packed.", ephemeral=True)
    assert press.response.send_message.await_count == 0


async def test_no_job_runs_until_start_up_has_ended():
    """Two steps run at start-up. The scheduler is started paused before the first of them, so its
    job store is readable and no job runs, and it is resumed only once the queue has started, as
    the last thing start-up does; it is never started unpaused."""
    module = _fresh_start_up()
    record = _Recorder()
    starting = _starting()
    starting.subscribe(record.step("hub")[1], name="hub")
    starting.subscribe(record.module_steps(record.step("amendment sweep")), module="results")

    await record.start_up(module, starting)

    assert record.calls[0] == "scheduler started paused"
    assert "scheduler started" not in record.calls
    assert record.calls[-2:] == ["queue started", "scheduler resumed"]
    assert record.calls.count("scheduler resumed") == 1


async def test_a_review_prompt_recovery_that_cannot_read_the_database_fails_its_step(
    tmp_path, caplog, monkeypatch
):
    """The real bot's 'expired review prompts' step, run by start-up from the builder's
    `bot.starting` over a database whose review prompts cannot be read (their table is gone), every
    module off, is logged as a failed step naming it, with the database error and its traceback,
    and the next of core's steps, the hub (its recovery stubbed), still runs."""
    import sqlite3
    from datetime import datetime, timezone

    from leaguebot.core.db.database import get_connection
    from tests.support.change_queue import built_bot

    bot = await built_bot(tmp_path, now=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))
    async with get_connection(bot.db_path) as db:
        await db.execute("DROP TABLE season_review_prompts")
        await db.commit()
    record = _Recorder()
    record.modules.switches.update(
        {key: False for key in ("signup", "results", "attendance", "weather", "images")})

    async def _recover_hub(*_: Any, **__: Any) -> None:
        record.calls.append("hub")

    monkeypatch.setattr("leaguebot.core.services.modules.hub_service.recover_hub", _recover_hub)
    module = _fresh_start_up()

    with caplog.at_level(logging.DEBUG, logger=_MODULE):
        await record.start_up(module, bot.starting)

    assert "hub" in record.calls
    errors = [r for r in caplog.records if r.name == _MODULE and r.levelno >= logging.ERROR]
    assert [r.getMessage() for r in errors] == ["Start-up step 'expired review prompts' failed"]
    [error] = errors
    assert error.exc_info is not None and issubclass(error.exc_info[0], sqlite3.Error)
