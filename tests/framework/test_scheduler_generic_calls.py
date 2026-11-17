"""The scheduler's generic calls: a job armed under the id, kind and key its caller hands (#544).

The scheduler is the bot's machinery, and names no round, module or kind of job. Core's round jobs
and each module's own jobs arm through these calls, handing the id the job is stored under, the kind
whose handler it runs and the key that handler is handed. Every job runs through the one runner a
one-off timer runs through, and is armed with no lateness limit (`misfire_grace_time=None`,
architecture.md "Timed work and restarts"), as each job the scheduler armed before them was.

These tests use the real `SchedulerService`, on job-store files the test tears down, Windows
included.
"""
from __future__ import annotations

import ast
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

import leaguebot.framework.services.jobs.scheduler as scheduler_module
from leaguebot.framework.services.jobs.scheduler import SchedulerService


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
async def service(tmp_path, monkeypatch):
    """A scheduler started paused on its own job store, so that nothing it holds fires; it is
    stopped and its job store closed once the test has run."""
    monkeypatch.setattr(scheduler_module, "_GLOBAL_SERVICE", None)
    built = SchedulerService(
        str(tmp_path / "bot.db"), str(tmp_path / "jobs.db"), report=AsyncMock()
    )
    built.start(paused=True)
    yield built
    _dispose(built)


def _now() -> datetime:
    return datetime.now(timezone.utc)


AT = datetime(2031, 3, 9, 14, 30, tzinfo=timezone.utc)


def _arm_at(service: Any) -> None:
    service.arm_at(job_id="a_job_s1_d1_r1_id9", kind="a_kind", key=9, run_at=AT, name="A job")


def _run_now(service: Any) -> None:
    service.run_now(job_id="a_job_s1_d1_r1_id9", kind="a_kind", key=9, name="A job")


def _arm_daily(service: Any) -> None:
    service.arm_daily(job_id="a_job", kind="a_kind", hour=6, minute=15, name="A job")


def _arm_every(service: Any) -> None:
    service.arm_every(job_id="a_job", kind="a_kind", minutes=5, name="A job")


def _at_the_moment(trigger: Any) -> None:
    from apscheduler.triggers.date import DateTrigger

    assert isinstance(trigger, DateTrigger)
    assert trigger.run_date == AT


def _at_once(trigger: Any) -> None:
    from apscheduler.triggers.date import DateTrigger

    assert isinstance(trigger, DateTrigger)
    assert abs(trigger.run_date - _now()) < timedelta(minutes=1)


def _daily(trigger: Any) -> None:
    from apscheduler.triggers.cron import CronTrigger

    assert isinstance(trigger, CronTrigger)
    fields = {field.name: str(field) for field in trigger.fields}
    assert (fields["hour"], fields["minute"]) == ("6", "15")
    assert str(trigger.timezone) == "UTC"


def _every_five_minutes(trigger: Any) -> None:
    from apscheduler.triggers.interval import IntervalTrigger

    assert isinstance(trigger, IntervalTrigger)
    assert trigger.interval == timedelta(minutes=5)


#: Each generic call that arms a job, the id it hands, the key its handler is handed (None: a
#: standing job, whose handler is handed nothing), and the check of the trigger it arms.
EVERY_ARMING_CALL = [
    pytest.param(_arm_at, "a_job_s1_d1_r1_id9", 9, _at_the_moment, id="arm_at"),
    pytest.param(_run_now, "a_job_s1_d1_r1_id9", 9, _at_once, id="run_now"),
    pytest.param(_arm_daily, "a_job", None, _daily, id="arm_daily"),
    pytest.param(_arm_every, "a_job", None, _every_five_minutes, id="arm_every"),
]


@pytest.mark.xfail(strict=True, reason="#453: the scheduler takes no report to the log channel")
@pytest.mark.parametrize("arm, job_id, key, trigger_is", EVERY_ARMING_CALL)
async def test_a_generic_call_arms_one_job_under_the_id_kind_and_key_handed(
    service, arm, job_id, key, trigger_is
):
    """A job is armed twice through the call, under one id: the scheduler holds that one job,
    named as handed, run through the one runner with the kind and the key as keywords, armed
    with no lateness limit and on the trigger the call stands for; fired, it calls the kind's
    handler alone, once, with the key (a standing job's with nothing)."""
    calls: list[tuple[Any, ...]] = []

    async def _handler(*args: Any) -> None:
        calls.append(args)

    service.register_timer("a_kind", _handler)

    arm(service)
    arm(service)

    [job] = service._scheduler.get_jobs()
    assert job.id == job_id
    assert job.name == "A job"
    assert job.func is scheduler_module._one_off_timer_job
    assert job.misfire_grace_time is None
    if key is None:
        assert job.kwargs in ({"kind": "a_kind"}, {"kind": "a_kind", "key": None})
    else:
        assert job.kwargs == {"kind": "a_kind", "key": key}
    trigger_is(job.trigger)

    await job.func(*job.args, **job.kwargs)

    assert calls == [() if key is None else (key,)]


@pytest.mark.xfail(strict=True, reason="#453: the scheduler takes no report to the log channel")
async def test_jobs_lists_each_stored_job_s_id_kind_key_and_next_run(service):
    """One job is armed at a moment for key 9, and one standing daily job: `jobs()` lists both,
    each by its id, its kind, its key (None for the standing job) and when it next runs."""
    service.arm_at(job_id="a_job_s1_d1_r1_id9", kind="a_kind", key=9, run_at=AT, name="A job")
    service.arm_daily(job_id="b_job", kind="b_kind", hour=6, minute=15, name="B job")

    listed = {(job.job_id, job.kind, job.key): job.next_run for job in service.jobs()}

    assert set(listed) == {("a_job_s1_d1_r1_id9", "a_kind", 9), ("b_job", "b_kind", None)}
    assert listed[("a_job_s1_d1_r1_id9", "a_kind", 9)] == AT
    assert listed[("b_job", "b_kind", None)] is not None


@pytest.mark.xfail(strict=True, reason="#453: the scheduler takes no report to the log channel")
async def test_remove_takes_the_job_away_and_says_whether_it_went(service):
    """A job armed under an id is removed: `remove` says it went and the job is gone; asked
    again for that id, and for an id nothing was armed under, it says nothing went."""
    service.arm_at(job_id="a_job", kind="a_kind", key=9, run_at=AT, name="A job")

    assert service.remove("a_job") is True
    assert not service.has_job("a_job")
    assert service.remove("a_job") is False
    assert service.remove("never_armed") is False


@pytest.mark.xfail(strict=True, reason="#453: the scheduler takes no report to the log channel")
async def test_remove_logs_a_store_fault_and_says_nothing_went(service, caplog, monkeypatch):
    """The job store fails, other than for a job already gone, as a job is removed: `remove`
    raises nothing, says nothing went, and logs a warning with the fault's traceback."""
    def _broken(_job_id: str) -> None:
        raise RuntimeError("the store broke")

    monkeypatch.setattr(service._scheduler, "remove_job", _broken)

    with caplog.at_level(logging.WARNING):
        assert service.remove("a_job") is False

    [record] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert record.exc_info is not None
    assert str(record.exc_info[1]) == "the store broke"


#: Words naming a round, a module or a kind of job, which no name or text in the scheduler's code
#: may carry: the scheduler is handed every id, kind and key by its caller.
LEAGUE_WORDS = (
    "round", "season", "weather", "forecast", "rsvp", "attendance", "result", "portrait", "pfp",
    "amend", "signup", "image",
)


def _names_and_texts(tree: ast.AST) -> set[str]:
    """Every name the code defines or reads and every string it holds, docstrings left out."""
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.add(node.name)
        elif isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.arg):
            found.add(node.arg)
        elif isinstance(node, ast.alias):
            found.add(node.asname or node.name)
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            found.add(node.value)
    return found


def test_the_scheduler_names_no_round_module_or_kind_of_job():
    """The scheduler's code is read: no name it defines or reads, and no string it holds outside
    a docstring, carries a word naming a round, a module or a kind of job."""
    source = Path(scheduler_module.__file__).read_text(encoding="utf-8")

    named = sorted(
        text for text in _names_and_texts(ast.parse(source))
        if any(word in text.lower() for word in LEAGUE_WORDS)
    )

    assert named == []
