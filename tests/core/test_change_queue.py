"""The change queue: a change asked for is acknowledged, then carried out step by step (#439).

`docs/design/architecture.md`, "How a change is carried out", designs it. These tests use dummy
change types on a database built by the migrations, with "now" pinned, and a real `OutputRouter`
whose log channel records what it is sent. What Discord makes fail, and the retries, are in
`test_change_queue_retries.py`.

Everything of the queue is imported inside a test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    LOG_CHANNEL_WARNING,
    MEMBER_ID,
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    league_double,
    maybe_await,
    member,
    member_interaction,
    queued_log_lines,
    register,
    restart_queue,
    run_queue,
    seed_server,
    step_rows,
    stopped_job,
    updated_reply,
)


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
WHAT = "`/dummy`"
DOING = "Doing the dummy thing"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
#: The stop line's opening, the job and the request it names filled in.
STOPPED_AT = "❌ The queue is stopped at job #{id}: {job} for {request} ({asker}) failed ({fault})."
#: The lines of a stopped job, its number and what it was filled in.
WENT_THROUGH = "✅ Job #{id} ({job}) went through. The queue runs on."
RESTART_LINE = (
    "❌ The queue is still stopped at job #{id} ({job}). After the restart the bot no longer "
    "tries it on its own: press Retry or Discard on its notice."
)
HOUR_LINE = (
    "❌ Job #{id} ({job}) still fails after an hour. The bot has stopped trying on its own: "
    "press Retry on its notice, or Discard."
)


def _acknowledges(text: str) -> bool:
    """Whether *text* is the member's acknowledgement: the change under way, to be updated when it
    is done. The job number it also names is pinned by its own test."""
    return (
        text.startswith("⏳")
        and DOING in text
        and "This message will be updated when it is done" in text
        and "the log channel will say so." in text
    )


# ---------------------------------------------------------------------------
# Dummy change types
# ---------------------------------------------------------------------------


def _api() -> SimpleNamespace:
    from leaguebot.core.models import change
    from leaguebot.core.services import change_queue

    return SimpleNamespace(
        ChangeOrigin=change.ChangeOrigin,
        StepKind=change.StepKind,
        Verdict=change.Verdict,
        AuditRecord=change.AuditRecord,
        PlannedStep=change.PlannedStep,
        FollowOn=change.FollowOn,
        StepResult=change.StepResult,
        StepFailedOnDiscord=change.StepFailedOnDiscord,
        ChangeType=change_queue.ChangeType,
        Step=change_queue.Step,
    )


def _type(kind: str = "dummy", *, steps, check=None, outcome="✅ Done.", repeatable=False,
          opening=None) -> Any:
    """A change type running *steps* in the order given, each opened with no payload.

    A change type has no outcome for a fault, since a failure stops the queue rather than ending
    the change; the queue built before that rule still demands one, so it is handed one only
    where `ChangeType` declares it.
    """
    api = _api()

    async def _go(_ctx):
        return api.Verdict.go()

    declared = {f.name for f in dataclasses.fields(api.ChangeType)}
    withdrawn: dict[str, Any] = (
        {"fault_outcome": lambda _ctx: "Nothing was changed."}
        if "fault_outcome" in declared else {}
    )
    return api.ChangeType(
        kind=kind,
        opening=(
            tuple(opening) if opening is not None
            else tuple(api.PlannedStep(s.name, {}) for s in steps)
        ),
        steps={s.name: s for s in steps},
        check=check or _go,
        key=lambda payload: f"{kind}|{json.dumps(payload, sort_keys=True)}",
        doing=lambda _payload: DOING,
        outcome=outcome if callable(outcome) else (lambda _ctx: outcome),
        repeatable=repeatable,
        **withdrawn,
    )


def _act(name: str, record: list, *, fails: BaseException | None = None, result=None,
         lines=(), audits=()) -> Any:
    """An `ACT` step recording that it ran in *record*."""
    api = _api()

    async def _run(_ctx):
        record.append(name)
        if fails is not None:
            raise fails
        return api.StepResult(result=result or {}, lines=tuple(lines), audits=tuple(audits))

    return api.Step(name, api.StepKind.ACT, _run)


def _save(name: str, value: str, *, fails: BaseException | None = None, **returned) -> Any:
    """A `SAVE` step writing *value* into the test's own scratch table on the handed connection."""
    api = _api()

    async def _run(db, _ctx):
        await db.execute("INSERT INTO scratch (v) VALUES (?)", (value,))
        if fails is not None:
            raise fails
        return api.StepResult(**returned)

    return api.Step(name, api.StepKind.SAVE, _run)


# ---------------------------------------------------------------------------
# The league
# ---------------------------------------------------------------------------


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "queue.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        await db.execute("CREATE TABLE scratch (v TEXT)")  # the test's own table
        await db.commit()
    return SimpleNamespace(db_path=db_path, bot=league_double(db_path), clock=Clock(NOW))


def _queue(env, *types) -> Any:
    queue = attach_queue(env.bot, env.db_path, now=env.clock, types=types)
    return queue


async def _ask(env, kind: str = "dummy", payload=None, *, interaction=None, origin=None,
               what: str = WHAT) -> Any:
    """Ask for a change: a member's through *interaction*, or the bot's where *origin* is BOT."""
    kwargs: dict[str, Any] = {"what": what}
    if origin is not None:
        kwargs["origin"] = origin
        kwargs["actor"] = member()
    else:
        kwargs["interaction"] = interaction or member_interaction(env.bot)
        kwargs["refusal_what"] = what
    return await env.bot.change_queue.ask(kind, payload or {}, **kwargs)


async def _lines(env) -> list[str]:
    """Every log line written: those the log channel took, then those waiting on a retry."""
    return list(env.bot.log_channel.sent) + await queued_log_lines(env.db_path)


async def _scratch(env) -> list[str]:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT v FROM scratch ORDER BY rowid")
        return [row["v"] for row in await cursor.fetchall()]


async def _states(env) -> list[str]:
    return [row["state"] for row in await change_rows(env.db_path)]


def _host_errors(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno >= logging.ERROR and r.exc_info]


# ---------------------------------------------------------------------------
# Steps and their saves
# ---------------------------------------------------------------------------


async def test_a_change_runs_its_steps_in_order_and_marks_each_done(env):
    ran: list[str] = []
    _queue(env, _type(steps=[_act("a", ran), _act("b", ran), _act("c", ran)]))

    await _ask(env)
    await run_queue(env.bot)

    assert ran == ["a", "b", "c"]
    assert [s["name"] for s in await step_rows(env.db_path)] == ["a", "b", "c"]
    assert all(s["done_at"] for s in await step_rows(env.db_path))
    assert await _states(env) == ["DONE"]


async def test_a_step_s_writes_and_its_mark_commit_together(env):
    """A step that raises after writing leaves neither its write nor its mark."""
    _queue(env, _type(steps=[_save("a", "written", fails=RuntimeError("boom"))]))

    await _ask(env)
    await run_queue(env.bot)

    assert await _scratch(env) == []
    assert [s["done_at"] for s in await step_rows(env.db_path)] == [None]



async def test_a_fault_stops_the_change_and_keeps_what_earlier_steps_saved(env):
    """A job the bot faults on stops the queue there: the jobs before it keep what they saved,
    and neither the change's later jobs nor a change asked after it run."""
    ran: list[str] = []
    _queue(
        env,
        _type(steps=[
            _save("a", "first"), _save("b", "second", fails=RuntimeError("boom")), _act("c", ran),
        ]),
        _type("later", steps=[_act("later", ran)]),
    )

    await _ask(env)
    await _ask(env, "later")
    await run_queue(env.bot)

    assert await _scratch(env) == ["first"]
    assert [bool(s["done_at"]) for s in await step_rows(env.db_path)] == [
        True, False, False, False,
    ]
    assert ran == []
    assert await _states(env) == ["RUNNING", "QUEUED"]
    assert (await stopped_job(env.db_path))["name"] == "b"


async def test_a_fault_is_reported_to_the_member_the_log_channel_and_the_host(env, caplog):
    """The stop notice is one ❌ message in the log channel naming job #N, what it was, the
    request, who asked and the kind of fault, carrying the Retry and Discard buttons; the member's
    acknowledgement says the request is stopped at that job; the traceback is in the host's log."""
    caplog.set_level(logging.INFO)
    api = _api()

    async def _boom(_ctx):
        raise RuntimeError("boom")

    async def _describe(_ctx):
        return "doing the dummy step"

    _queue(env, _type(steps=[api.Step("a", api.StepKind.ACT, _boom, describe=_describe)]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    stop = STOPPED_AT.format(
        id=job["id"], job="doing the dummy step", request=WHAT, asker=NAMED, fault="RuntimeError",
    )
    [notice] = [m for m in env.bot.log_channel.posted if m.content.startswith(stop)]
    assert "a league manager or admin may press Retry" in notice.content
    assert "a league admin may press Discard" in notice.content
    assert {child.custom_id for child in notice.view.children} == {"queue:retry", "queue:discard"}
    assert job["notice_message_id"] == notice.id
    reply = updated_reply(interaction)
    assert reply.startswith("❌")
    assert f"job #{job['id']}" in reply
    assert any("boom" in str(r.exc_info[1]) for r in _host_errors(caplog))


async def test_changes_run_one_at_a_time_in_the_order_asked(env):
    ran: list[str] = []

    def _slow(name: str) -> Any:
        api = _api()

        async def _run(_ctx):
            ran.append(f"{name} starts")
            await asyncio.sleep(0)
            ran.append(f"{name} ends")
            return api.StepResult()

        return api.Step("work", api.StepKind.ACT, _run)

    _queue(env, _type("one", steps=[_slow("one")]), _type("two", steps=[_slow("two")]),
           _type("three", steps=[_slow("three")]))

    await _ask(env, "one")
    await _ask(env, "two")
    await _ask(env, "three")
    await run_queue(env.bot)

    assert ran == ["one starts", "one ends", "two starts", "two ends", "three starts", "three ends"]


# ---------------------------------------------------------------------------
# The checks, and a request repeated
# ---------------------------------------------------------------------------


def _check_refusing_when(holder: dict, reply: str = "⚠️ Not now.") -> Any:
    async def _check(_ctx):
        api = _api()
        return api.Verdict.refuse(reply) if holder["refuse"] else api.Verdict.go()

    return _check


async def test_a_member_s_request_failing_its_check_is_refused_at_once_and_nothing_is_queued(env):
    ran: list[str] = []
    _queue(env, _type(steps=[_act("a", ran)], check=_check_refusing_when({"refuse": True})))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    assert acknowledgement(interaction) == "⚠️ Not now."
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert await change_rows(env.db_path) == []
    assert ran == []


async def test_a_change_whose_check_fails_when_it_runs_is_refused_and_says_why(env):
    """The acknowledgement is updated with the refusal, and the line is `refusal_line`'s."""
    holder = {"refuse": False}
    ran: list[str] = []
    _queue(env, _type(steps=[_act("a", ran)], check=_check_refusing_when(holder)))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    holder["refuse"] = True
    await run_queue(env.bot)

    assert _acknowledges(acknowledgement(interaction))
    assert updated_reply(interaction) == "⚠️ Not now."
    assert f"⛔ {WHAT} refused for {NAMED} — Not now." in "\n".join(await _lines(env))
    assert await _states(env) == ["REFUSED"]
    assert ran == []


async def test_a_bot_change_no_longer_due_is_dropped(env, caplog):
    """A bot change found no longer due when it runs is dropped, with a line in the host's log only."""
    caplog.set_level(logging.INFO)
    holder = {"due": True}
    ran: list[str] = []

    async def _check(_ctx):
        api = _api()
        return api.Verdict.go() if holder["due"] else api.Verdict.not_due("the season has ended")

    _queue(env, _type(steps=[_act("a", ran)], check=_check))
    api = _api()

    await _ask(env, origin=api.ChangeOrigin.BOT)
    holder["due"] = False
    await run_queue(env.bot)

    assert await _states(env) == ["DROPPED"]
    assert ran == []
    assert await _lines(env) == []
    assert any("the season has ended" in r.getMessage() for r in caplog.records)



async def test_a_bot_change_its_check_refuses_stops_the_queue_and_is_checked_at_each_try(env):
    """A bot change its check refuses stops the queue before it starts, the stop line giving the
    check's reason once, not once per try; the check runs again at each try, and once it lets the
    change go, the change and those behind it run."""
    holder = {"refused": False}
    checks: list[str] = []
    ran: list[str] = []

    async def _check(_ctx):
        api = _api()
        checks.append("checked")
        if holder["refused"]:
            return api.Verdict.refuse(
                "The forecast channel is missing.", "the forecast channel is missing",
            )
        return api.Verdict.go()

    _queue(env, _type(steps=[_act("a", ran)], check=_check),
           _type("later", steps=[_act("later", ran)]))
    api = _api()

    await _ask(env, origin=api.ChangeOrigin.BOT)
    await _ask(env, "later")
    holder["refused"] = True
    await run_queue(env.bot)

    assert await _states(env) == ["QUEUED", "QUEUED"]
    assert ran == []
    assert (await stopped_job(env.db_path))["name"] == "a"
    saying = [line for line in await _lines(env) if "the forecast channel is missing" in line]
    assert len(saying) == 1
    assert "The queue is stopped at job #" in saying[0]

    tried = len(checks)
    env.clock.advance(minutes=1)
    await run_queue(env.bot)

    assert len(checks) == tried + 1
    saying = [line for line in await _lines(env) if "the forecast channel is missing" in line]
    assert len(saying) == 1
    assert ran == []

    holder["refused"] = False
    env.clock.advance(minutes=4)
    await run_queue(env.bot)

    assert ran == ["a", "later"]
    assert await _states(env) == ["DONE", "DONE"]


async def test_a_request_repeating_the_last_change_asked_for_before_it_starts_is_refused_saying_so(env):
    _queue(env, _type(steps=[_act("a", [])]))
    first, second = member_interaction(env.bot), member_interaction(env.bot)

    await _ask(env, interaction=first)
    await _ask(env, interaction=second)

    reason = (
        f"{DOING} has already been asked for and has not started yet, "
        "so it was not asked for again."
    )
    assert _acknowledges(acknowledgement(first))
    assert acknowledgement(second) == f"⚠️ {reason}"
    assert len(await change_rows(env.db_path)) == 1
    # The line drops the reply's opening mark, which its own mark replaces.
    assert f"⛔ {WHAT} refused for {NAMED} — {reason}" in "\n".join(await _lines(env))



async def test_a_change_is_queued_again_once_another_has_been_asked_for_after_it(env):
    """Whatever became of the change asked for in between: here it has already finished, ahead of
    changes held behind a stopped job, and the held one may still be asked for again."""
    api = _api()
    held = {"fail": api.StepFailedOnDiscord("Missing Access")}

    async def _fails(_ctx):
        if held["fail"] is not None:
            raise held["fail"]
        return api.StepResult()

    _queue(
        env,
        _type("stopping", steps=[api.Step("post", api.StepKind.ACT, _fails)]),
        _type("dummy", steps=[_act("a", [])]),
        _type("elsewhere", steps=[_act("b", [])]),
    )

    await _ask(env, "dummy")
    await _ask(env, "elsewhere")
    await _ask(env, "dummy")
    assert [r["kind"] for r in await change_rows(env.db_path)] == ["dummy", "elsewhere", "dummy"]

    await run_queue(env.bot)
    await _ask(env, "stopping")
    await run_queue(env.bot)
    await _ask(env, "dummy")
    await _ask(env, "elsewhere")
    await run_queue(env.bot)

    rows = await change_rows(env.db_path)
    assert [(r["kind"], r["state"]) for r in rows[3:]] == [
        ("stopping", "RUNNING"), ("dummy", "QUEUED"), ("elsewhere", "QUEUED"),
    ]

    await _ask(env, "dummy")

    rows = await change_rows(env.db_path)
    assert [(r["kind"], r["state"]) for r in rows[3:]] == [
        ("stopping", "RUNNING"), ("dummy", "QUEUED"), ("elsewhere", "QUEUED"), ("dummy", "QUEUED"),
    ]


async def test_a_repeatable_change_is_queued_again_once_it_is_running(env):
    _queue(env, _type(steps=[_act("a", []), _act("b", [])], repeatable=True))

    await _ask(env)
    await run_queue(env.bot, steps=1)
    assert await _states(env) == ["RUNNING"]

    await _ask(env)

    assert await _states(env) == ["RUNNING", "QUEUED"]


async def test_a_once_only_change_already_done_is_refused_by_its_check(env):
    done = {"refuse": False}
    api = _api()

    async def _run(_ctx):
        done["refuse"] = True
        return api.StepResult()

    _queue(env, _type(steps=[api.Step("a", api.StepKind.ACT, _run)],
                      check=_check_refusing_when(done, "⚠️ That is already done.")))
    again = member_interaction(env.bot)

    await _ask(env)
    await run_queue(env.bot)
    await _ask(env, interaction=again)

    assert acknowledgement(again) == "⚠️ That is already done."
    assert await _states(env) == ["DONE"]


# ---------------------------------------------------------------------------
# The acknowledgement and its update
# ---------------------------------------------------------------------------


async def test_the_member_is_told_at_once_that_the_change_is_under_way(env):
    _queue(env, _type(steps=[_act("a", [])]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)

    assert _acknowledges(acknowledgement(interaction))
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert interaction.edit_original_response.await_count == 0
    [row] = await change_rows(env.db_path)
    assert row["state"] == "QUEUED"
    assert row["acknowledged_at"] == NOW.isoformat()


async def test_the_acknowledgement_is_updated_with_the_outcome(env):
    _queue(env, _type(steps=[_act("a", [])], outcome="✅ The dummy thing is done."))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == "✅ The dummy thing is done."


async def test_an_outcome_after_fourteen_minutes_is_left_to_the_log_channel(env):
    """No update is attempted: the outcome stands in the log channel alone."""
    line = "Admin (<@4242>) | /dummy | Success"
    _queue(env, _type(steps=[_act("a", [], lines=[line])]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    env.clock.advance(minutes=15)
    await run_queue(env.bot)

    assert interaction.edit_original_response.await_count == 0
    assert interaction.followup.send.await_count == 0
    assert "Admin (`<@4242>`) | /dummy | Success" in "\n".join(await _lines(env))
    assert await _states(env) == ["DONE"]


async def test_an_acknowledgement_that_cannot_be_updated_does_not_fail_the_change(env, caplog):
    caplog.set_level(logging.INFO)
    _queue(env, _type(steps=[_act("a", [])]))
    interaction = member_interaction(env.bot)
    interaction.edit_original_response.side_effect = http_error(text="Unknown Webhook")

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    assert await _states(env) == ["DONE"]
    assert not any("failed for" in line for line in await _lines(env))
    assert any(r.exc_info for r in caplog.records)


async def test_a_long_outcome_is_sent_in_parts(env):
    links = [f"https://discord.com/channels/12408/300/{n:019d}" for n in range(150)]
    _queue(env, _type(steps=[_act("a", [])], outcome="\n".join(links)))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    first = interaction.edit_original_response.await_args_list
    rest = interaction.followup.send.await_args_list
    assert len(first) == 1 and rest
    assert all(call.kwargs.get("ephemeral") is True for call in rest)
    parts = [str(first[0].kwargs.get("content", first[0].args[0] if first[0].args else ""))]
    parts += [str(call.args[0] if call.args else call.kwargs["content"]) for call in rest]
    assert all(len(part) <= 2000 for part in parts)
    assert "\n".join(parts).split() == links


# ---------------------------------------------------------------------------
# Log lines and audit records
# ---------------------------------------------------------------------------


async def test_the_audit_record_and_the_log_line_are_saved_with_the_step(env):
    api = _api()
    _queue(env, _type(steps=[
        _save(
            "a", "written",
            audits=(api.AuditRecord("DUMMY_CHANGED", {"value": 1}, {"value": 2}),),
            lines=("Admin (<@4242>) | /dummy | Success",),
        ),
    ]))

    await _ask(env)
    await run_queue(env.bot)

    async with get_connection(env.db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, actor_name, change_type, old_value, new_value, timestamp "
            "FROM audit_entries"
        )
        [audit] = [dict(row) for row in await cursor.fetchall()]
    assert audit == {
        "actor_id": MEMBER_ID,
        "actor_name": "Admin#0001",
        "change_type": "DUMMY_CHANGED",
        "old_value": json.dumps({"value": 1}),
        "new_value": json.dumps({"value": 2}),
        "timestamp": NOW.isoformat(),
    }
    assert await _scratch(env) == ["written"]
    assert "Admin (`<@4242>`) | /dummy | Success" in "\n".join(await _lines(env))


async def test_a_log_line_is_delivered_after_its_save_and_leaves_the_retry_queue(env):
    seen_done: list[bool] = []
    channel = env.bot.log_channel
    send = channel.send.side_effect

    async def _observe(content="", **kwargs):
        with sqlite3.connect(env.db_path) as plain:
            [(done_at,)] = plain.execute("SELECT done_at FROM queued_change_steps").fetchall()
        seen_done.append(done_at is not None)
        return await send(content, **kwargs)

    channel.send.side_effect = _observe
    _queue(env, _type(steps=[_act("a", [], lines=["Admin (<@4242>) | /dummy | Success"])]))

    await _ask(env)
    await run_queue(env.bot)

    assert any("/dummy | Success" in sent for sent in channel.sent)
    assert seen_done and all(seen_done)
    assert await queued_log_lines(env.db_path) == []
    assert all(call.kwargs.get("allowed_mentions") is not None for call in channel.send.await_args_list)


async def test_a_log_line_that_cannot_be_delivered_is_left_for_the_retry_loop(env):
    env.bot.log_channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    _queue(env, _type(steps=[_act("a", [], lines=["Admin (<@4242>) | /dummy | Success"])]))

    await _ask(env)
    await run_queue(env.bot)

    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT channel_id, content, failure_reason FROM pending_messages")
        rows = [dict(row) for row in await cursor.fetchall()]
    [row] = [r for r in rows if "/dummy | Success" in r["content"]]
    assert row["channel_id"] == env.bot.log_channel.id
    assert "Missing Access" in row["failure_reason"]
    assert await _states(env) == ["DONE"]


async def test_a_log_line_too_long_for_one_message_is_delivered_in_parts(env):
    """A record too long for one message is divided across as many as it needs, as
    `chunk_message` divides it, and nothing is left waiting on a retry."""
    links = [f"https://discord.com/channels/12408/300/{n:019d}" for n in range(100)]
    line = "Admin (<@4242>) | /dummy | Messages removed\n" + "\n".join(f"  {link}" for link in links)
    _queue(env, _type(steps=[_act("a", [], lines=[line])]))

    await _ask(env)
    await run_queue(env.bot)

    parts = [sent for sent in env.bot.log_channel.sent if "/dummy | Messages removed" in sent
             or "https://discord.com/channels/12408/300/" in sent]
    assert len(parts) > 1
    assert all(len(part) <= 2000 for part in parts)
    assert parts[0].startswith("Admin (`<@4242>`) | /dummy | Messages removed")
    assert [word for part in parts for word in part.split() if word.startswith("https://")] == links
    assert await queued_log_lines(env.db_path) == []


async def test_a_queued_line_the_log_channel_refuses_is_told_to_the_member_alone(env):
    """Through the held interaction, seen by the member alone; the interaction channel is sent
    nothing, and the line still waits on the retry queue."""
    env.bot.log_channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    _queue(env, _type(steps=[_act("a", [], lines=["Admin (<@4242>) | /dummy | Success"])]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    warnings = [
        call for call in interaction.followup.send.await_args_list
        if call.args and call.args[0] == LOG_CHANNEL_WARNING
    ]
    assert len(warnings) == 1
    assert warnings[0].kwargs.get("ephemeral") is True
    assert env.bot.interaction_channel.sent == []
    assert any("/dummy | Success" in line for line in await queued_log_lines(env.db_path))


async def test_a_queued_line_refused_once_the_reply_has_lapsed_goes_to_the_host_log_alone(env, caplog):
    caplog.set_level(logging.INFO)
    env.bot.log_channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    _queue(env, _type(steps=[_act("a", [], lines=["Admin (<@4242>) | /dummy | Success"])]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    env.clock.advance(minutes=15)
    await run_queue(env.bot)

    assert interaction.followup.send.await_count == 0
    assert env.bot.interaction_channel.sent == []
    assert any("Missing Access" in r.getMessage() for r in caplog.records
               if r.levelno >= logging.WARNING)
    assert any("/dummy | Success" in line for line in await queued_log_lines(env.db_path))


async def test_a_queued_log_line_is_delivered_once_with_the_retry_loop_beside_the_worker(env):
    """The retry loop finds the line while the worker is delivering it, and leaves it."""
    from leaguebot.core.services.retry_service import attempt_delivery, get_all_pending

    channel = env.bot.log_channel
    send = channel.send.side_effect
    beside: dict[str, Any] = {}

    async def _with_the_loop_beside(content="", **kwargs):
        if "loop" not in beside:
            pending = await get_all_pending(env.db_path)
            beside["found"] = len(pending)
            beside["loop"] = asyncio.ensure_future(
                asyncio.gather(*(attempt_delivery(entry, env.bot) for entry in pending))
            )
            for _ in range(5):
                await asyncio.sleep(0)
        return await send(content, **kwargs)

    channel.send.side_effect = _with_the_loop_beside
    _queue(env, _type(steps=[_act("a", [], lines=["Admin (<@4242>) | /dummy | Success"])]))

    await _ask(env)
    await run_queue(env.bot)
    await beside["loop"]

    assert beside["found"] == 1
    assert sum("/dummy | Success" in sent for sent in channel.sent) == 1
    assert await queued_log_lines(env.db_path) == []


# ---------------------------------------------------------------------------
# Steps planning more work
# ---------------------------------------------------------------------------


async def test_a_step_may_plan_the_steps_after_it_in_its_own_save(env):
    api = _api()
    ran: list[str] = []

    async def _plan(db, _ctx):
        ran.append("plan")
        return api.StepResult(then=(api.PlannedStep("each", {"n": 1}),
                                    api.PlannedStep("each", {"n": 2})))

    async def _each(ctx):
        ran.append(f"each {ctx.step_payload['n']}")
        return api.StepResult()

    plan = api.Step("plan", api.StepKind.SAVE, _plan)
    each = api.Step("each", api.StepKind.ACT, _each)
    _queue(env, _type(steps=[plan, each, _act("last", ran)],
                      opening=[api.PlannedStep("plan", {}), api.PlannedStep("last", {})]))

    await _ask(env)
    await run_queue(env.bot)

    assert ran == ["plan", "each 1", "each 2", "last"]
    steps = await step_rows(env.db_path)
    assert [s["name"] for s in steps] == ["plan", "each", "each", "last"]
    assert [json.loads(s["payload"]) for s in steps][1:3] == [{"n": 1}, {"n": 2}]


async def test_a_step_may_ask_for_a_change_of_its_own_in_its_own_save(env):
    api = _api()
    ran: list[str] = []
    what = "Doing the other thing after `/dummy`"

    async def _ask_for_more(db, _ctx):
        return api.StepResult(follow_ons=(api.FollowOn("other", {"x": 1}, what),))

    _queue(env, _type(steps=[api.Step("a", api.StepKind.SAVE, _ask_for_more)]),
           _type("other", steps=[_act("o", ran)]))

    await _ask(env)
    await run_queue(env.bot)

    rows = await change_rows(env.db_path)
    assert [(r["kind"], r["origin"], r["state"]) for r in rows] == [
        ("dummy", "MEMBER", "DONE"), ("other", "BOT", "DONE"),
    ]
    assert rows[1]["actor_id"] == MEMBER_ID
    assert rows[1]["what"] == what
    assert json.loads(rows[1]["payload"]) == {"x": 1}
    assert ran == ["o"]


# ---------------------------------------------------------------------------
# Stops and restarts
# ---------------------------------------------------------------------------


async def test_a_change_cut_off_by_a_stop_carries_on_from_the_first_step_not_done(env):
    ran: list[str] = []
    _queue(env, _type(steps=[_act("a", ran), _act("b", ran), _act("c", ran)]))

    await _ask(env)
    await run_queue(env.bot, steps=1)
    assert await _states(env) == ["RUNNING"]

    await restart_queue(env.bot)
    try:
        await run_queue(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert ran == ["a", "b", "c"]
    assert await _states(env) == ["DONE"]


async def test_a_step_marked_done_is_not_run_again_after_a_restart(env):
    _queue(env, _type(steps=[_save("a", "once"), _save("b", "after")]))

    await _ask(env)
    await run_queue(env.bot, steps=1)
    await restart_queue(env.bot)
    try:
        await run_queue(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert await _scratch(env) == ["once", "after"]


async def test_the_worker_starts_once_though_the_bot_is_ready_twice(env):
    ran: list[str] = []
    queue = _queue(env, _type(steps=[_act("a", ran)]))

    await maybe_await(queue.start())
    worker = queue._task
    await maybe_await(queue.start())
    try:
        assert queue._task is worker
        await _ask(env)
        await run_queue(env.bot)
    finally:
        await maybe_await(queue.stop())

    assert ran == ["a"]


async def test_a_step_whose_change_was_removed_under_it_saves_nothing(env, caplog):
    """As a factory reset would remove it while the step runs: the mark updates no row, so the
    step's audits and lines are rolled back with it, and the host's log says so."""
    caplog.set_level(logging.INFO)
    api = _api()

    async def _removed_meanwhile(_ctx):
        async with get_connection(env.db_path) as db:
            await db.execute("DELETE FROM queued_changes")
            await db.commit()
        return api.StepResult(
            audits=(api.AuditRecord("DUMMY_CHANGED", {"value": 1}, {"value": 2}),),
            lines=("Admin (<@4242>) | /dummy | Success",),
        )

    _queue(env, _type(steps=[api.Step("a", api.StepKind.ACT, _removed_meanwhile)]))

    await _ask(env)
    await run_queue(env.bot)

    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM audit_entries")
        assert (await cursor.fetchone())["n"] == 0
    assert not any("/dummy | Success" in line for line in await _lines(env))
    assert await change_rows(env.db_path) == []
    assert await step_rows(env.db_path) == []
    assert any(r.levelno >= logging.WARNING for r in caplog.records
               if r.name.endswith("change_queue"))



async def test_a_change_of_a_kind_the_bot_no_longer_knows_stops_the_queue_until_discarded(env):
    """A change of a kind no longer registered stops the queue, named with its asker, and is
    kept until a league admin discards it."""
    _queue(env)
    async with get_connection(env.db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, actor_id, "
            "actor_name, actor_display, what) "
            "VALUES ('vanished', 'vanished', '{}', 'MEMBER', 'QUEUED', ?, 'Admin#0001', 'Admin', "
            "'`/gone`')",
            (MEMBER_ID,),
        )
        await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name) VALUES (1, 0, 'a')"
        )
        await db.commit()

    await run_queue(env.bot)

    assert await _states(env) == ["QUEUED"]
    stop = [line for line in await _lines(env) if "The queue is stopped at job #" in line]
    assert len(stop) == 1
    assert f"`/gone` ({NAMED})" in stop[0]

    await discard_job(env.bot)

    assert await _states(env) == ["DISCARDED"]


async def test_the_queue_writes_its_lines_through_the_router_it_was_handed(env):
    """The bot carries no `output_router`: the queue never looks one up on it. Its success line
    and the stop notice of the job that faults both reach the log channel through the router."""
    router = env.bot.output_router
    del env.bot.output_router
    attach_queue(env.bot, env.db_path, now=env.clock, router=router, types=())
    register(env.bot, _type(steps=[
        _act("a", [], lines=["Admin (<@4242>) | /dummy | Success"]),
        _act("b", [], fails=RuntimeError("boom")),
    ]))

    await _ask(env)
    await run_queue(env.bot)

    sent = "\n".join(env.bot.log_channel.sent)
    assert "Admin (`<@4242>`) | /dummy | Success" in sent
    job = await stopped_job(env.db_path)
    assert STOPPED_AT.format(
        id=job["id"], job=WHAT, request=WHAT, asker=NAMED, fault="RuntimeError",
    ) in sent


# ---------------------------------------------------------------------------
# What goes wrong around a change: its acknowledgement, its check, its outcome, the worker
# ---------------------------------------------------------------------------


async def _eventually(predicate, *, within: float = 5.0) -> None:
    """Wait, on the event loop, until *predicate()* (awaited) is true, failing after *within* s."""
    async def _poll() -> None:
        while not await predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), within)


async def test_a_change_whose_acknowledgement_fails_still_runs_and_holds_nothing(env):
    """The change is saved before the member is answered, so an answer Discord refuses does not
    undo it: it runs, and with no acknowledgement to update its outcome stands in the log channel."""
    line = "Admin (<@4242>) | /dummy | Success"
    queue = _queue(env, _type(steps=[_act("a", [], lines=[line])]))
    interaction = member_interaction(env.bot)
    interaction.response.send_message.side_effect = http_error(
        discord.NotFound, status=404, text="Unknown interaction"
    )

    await maybe_await(queue.start())
    try:
        await _ask(env, interaction=interaction)

        async def _done() -> bool:
            return await _states(env) == ["DONE"]

        await _eventually(_done)
    finally:
        await maybe_await(queue.stop())

    [row] = await change_rows(env.db_path)
    assert row["acknowledged_at"] is None
    assert interaction.edit_original_response.await_count == 0
    assert interaction.followup.send.await_count == 0
    assert "Admin (`<@4242>`) | /dummy | Success" in "\n".join(await _lines(env))


async def test_the_worker_does_not_take_a_change_before_its_acknowledgement_is_recorded(env):
    """The worker, busy with another change, finishes it while the member's answer is still being
    sent: it takes the new change only once that answer is done, and then updates it."""
    api = _api()
    ran: list[str] = []
    first_running, first_may_end = asyncio.Event(), asyncio.Event()

    async def _first(_ctx):
        first_running.set()
        await first_may_end.wait()
        return api.StepResult()

    _queue(
        env,
        _type("first", steps=[api.Step("f", api.StepKind.ACT, _first)]),
        _type("dummy", steps=[_act("a", ran)], outcome="✅ The dummy thing is done."),
    )
    await _ask(env, "first")
    worker = asyncio.create_task(run_queue(env.bot))
    await asyncio.wait_for(first_running.wait(), 5)

    interaction = member_interaction(env.bot)
    answering, may_answer = asyncio.Event(), asyncio.Event()
    answer = interaction.response.send_message.side_effect

    async def _slow_answer(*args, **kwargs):
        answering.set()
        await may_answer.wait()
        return await answer(*args, **kwargs)

    interaction.response.send_message.side_effect = _slow_answer
    asking = asyncio.create_task(_ask(env, "dummy", interaction=interaction))
    try:
        await asyncio.wait_for(answering.wait(), 5)
        first_may_end.set()
        for _ in range(20):
            await asyncio.sleep(0.01)

        assert ran == []
        assert [r["state"] for r in await change_rows(env.db_path)][1] == "QUEUED"
    finally:
        first_may_end.set()
        may_answer.set()
        await asyncio.wait_for(asking, 5)
        await asyncio.wait_for(worker, 5)

    await run_queue(env.bot)
    assert ran == ["a"]
    assert updated_reply(interaction) == "✅ The dummy thing is done."



async def test_a_check_that_raises_stops_the_queue_and_later_changes_run_once_it_is_discarded(env):
    """A check raising as its change starts stops the queue on that change, and the changes
    behind it wait; once a league admin discards it they run. An outcome that cannot be worded
    still leaves its change done."""
    holder = {"raise": False}
    ran: list[str] = []

    async def _check(_ctx):
        if holder["raise"]:
            raise RuntimeError("the check broke")
        return _api().Verdict.go()

    def _unwordable(_ctx):
        raise RuntimeError("the outcome broke")

    _queue(
        env,
        _type("checked", steps=[_act("c", ran)], check=_check),
        _type("worded", steps=[_act("w", ran)], outcome=_unwordable),
        _type("dummy", steps=[_act("a", ran)]),
    )

    await _ask(env, "checked", what="`/checked`")
    await _ask(env, "worded", what="`/worded`")
    await _ask(env, "dummy")
    holder["raise"] = True
    await run_queue(env.bot)

    assert await _states(env) == ["QUEUED", "QUEUED", "QUEUED"]
    assert ran == []
    stop = [line for line in await _lines(env) if "The queue is stopped at job #" in line]
    assert len(stop) == 1
    assert "`/checked`" in stop[0] and "(RuntimeError)" in stop[0]

    await discard_job(env.bot)

    assert await _states(env) == ["DISCARDED", "DONE", "DONE"]
    assert ran == ["w", "a"]


async def test_the_worker_carries_on_after_a_fault_outside_any_change(env, monkeypatch):
    """A fault outside any change, such as the database locked while the worker chooses, ends
    neither the worker nor the queue: a change asked for afterwards is carried out."""
    from leaguebot.core.services import change_queue

    monkeypatch.setattr(change_queue, "WORKER_PAUSE_AFTER_FAULT", 0.01)
    ran: list[str] = []
    queue = _queue(env, _type(steps=[_act("a", ran)]))
    rounds = {"n": 0}
    run_until_idle = queue.run_until_idle

    async def _locked_once(**kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return await run_until_idle(**kwargs)

    monkeypatch.setattr(queue, "run_until_idle", _locked_once)

    await maybe_await(queue.start())
    try:
        async def _faulted_once() -> bool:
            return rounds["n"] >= 1

        await _eventually(_faulted_once)
        for _ in range(5):
            await asyncio.sleep(0.01)
        assert not queue._task.done()

        await _ask(env)

        async def _done() -> bool:
            return await _states(env) == ["DONE"]

        await _eventually(_done)
        assert not queue._task.done()
    finally:
        await maybe_await(queue.stop())

    assert ran == ["a"]


async def test_the_worker_looks_again_after_a_fault_with_no_signal_to_wake_it(env, monkeypatch):
    """A change saved before a fault outside any change is not left QUEUED until the next ask, wake
    or restart: once it has paused, the worker looks at the queue again of its own accord."""
    from leaguebot.core.services import change_queue

    monkeypatch.setattr(change_queue, "WORKER_PAUSE_AFTER_FAULT", 0.01)
    ran: list[str] = []
    queue = _queue(env, _type(steps=[_act("a", ran)]))
    rounds = {"n": 0}
    run_until_idle = queue.run_until_idle

    async def _locked_before_it_starts(**kwargs):
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return await run_until_idle(**kwargs)

    monkeypatch.setattr(queue, "run_until_idle", _locked_before_it_starts)

    await _ask(env)
    assert await _states(env) == ["QUEUED"]

    await maybe_await(queue.start())
    try:
        async def _done() -> bool:
            return await _states(env) == ["DONE"]

        await _eventually(_done)
    finally:
        await maybe_await(queue.stop())

    assert rounds["n"] >= 2
    assert ran == ["a"]


async def test_an_interaction_already_answered_is_acknowledged_through_a_followup_and_that_message_is_updated(env):
    """A command that deferred, or a form already answered, has no response left to take: the
    acknowledgement is a follow-up, and it is that message the outcome updates, within the same
    14 minutes."""
    _queue(env, _type(steps=[_act("a", [])], outcome="✅ The dummy thing is done."))

    def _deferred() -> tuple[Any, Any]:
        interaction = member_interaction(env.bot)
        message = SimpleNamespace(edit=AsyncMock())
        interaction.followup.send = AsyncMock(return_value=message)
        return interaction, message

    interaction, message = _deferred()
    await interaction.response.defer()
    interaction.response.defer.reset_mock()

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    assert interaction.response.send_message.await_count == 0
    [sent] = interaction.followup.send.await_args_list
    assert _acknowledges(sent.args[0] if sent.args else sent.kwargs.get("content"))
    assert sent.kwargs.get("ephemeral") is True and sent.kwargs.get("wait") is True
    assert interaction.edit_original_response.await_count == 0
    [edit] = message.edit.await_args_list
    assert (edit.args[0] if edit.args else edit.kwargs.get("content")) == (
        "✅ The dummy thing is done."
    )

    late, late_message = _deferred()
    await late.response.defer()
    await _ask(env, interaction=late)
    env.clock.advance(minutes=15)
    await run_queue(env.bot)

    assert late.followup.send.await_count == 1
    assert late_message.edit.await_count == 0
    assert late.edit_original_response.await_count == 0


async def test_a_refusal_s_reason_is_written_beneath_the_refusal_line(env):
    """A check may give the log channel a reason beside the member's reply: the line keeps the
    reply's first line as its reason, with the check's beneath it, and the member is told the
    reply alone. So at the first check and at the second alike."""
    detail = "The season ended on 3 October."
    holder = {"refuse": False}

    async def _check(_ctx):
        api = _api()
        return api.Verdict.refuse("⚠️ Not now.", reason=detail)

    async def _later(_ctx):
        api = _api()
        if holder["refuse"]:
            return api.Verdict.refuse("⚠️ Not now.", reason=detail)
        return api.Verdict.go()

    _queue(
        env,
        _type("at_once", steps=[_act("a", [])], check=_check),
        _type("later", steps=[_act("b", [])], check=_later),
    )
    refused_at_once = member_interaction(env.bot)
    refused_later = member_interaction(env.bot)

    await _ask(env, "at_once", interaction=refused_at_once, what="`/at-once`")
    await _ask(env, "later", interaction=refused_later, what="`/later`")
    holder["refuse"] = True
    await run_queue(env.bot)

    assert acknowledgement(refused_at_once) == "⚠️ Not now."
    assert updated_reply(refused_later) == "⚠️ Not now."
    lines = "\n".join(await _lines(env))
    assert f"⛔ `/at-once` refused for {NAMED} — Not now.\n{detail}" in lines
    assert f"⛔ `/later` refused for {NAMED} — Not now.\n{detail}" in lines


# ---------------------------------------------------------------------------
# Jobs, and a queue stopped at one
# ---------------------------------------------------------------------------




def _fails_while(holder: dict, name: str = "post", *, ran: list | None = None, **step) -> Any:
    """An `ACT` job failing with `holder["fail"]` while it is set, and going through once cleared."""
    api = _api()

    async def _run(_ctx):
        if ran is not None:
            ran.append(name)
        if holder.get("fail") is not None:
            raise holder["fail"]
        return api.StepResult()

    return api.Step(name, api.StepKind.ACT, _run, **step)


def _described(text: str) -> Any:
    async def _describe(_ctx):
        return text

    return _describe


async def _try_at(env, minutes: float) -> None:
    """Set the clock to *minutes* after NOW, and run the queue."""
    env.clock.now = NOW + timedelta(minutes=minutes)
    await run_queue(env.bot)


async def test_every_job_has_its_own_id_that_planning_later_jobs_leaves_alone(env):
    """Each job saved gets a number of its own, never shared with another job of any request, and
    a job planning more jobs before the last one renumbers neither itself nor the last."""
    api = _api()

    async def _plan(db, _ctx):
        return api.StepResult(then=(api.PlannedStep("each", {"n": 1}),
                                    api.PlannedStep("each", {"n": 2})))

    async def _each(_ctx):
        return api.StepResult()

    _queue(
        env,
        _type(steps=[api.Step("plan", api.StepKind.SAVE, _plan),
                     api.Step("each", api.StepKind.ACT, _each), _act("last", [])],
              opening=[api.PlannedStep("plan", {}), api.PlannedStep("last", {})]),
        _type("other", steps=[_act("o", [])]),
    )

    await _ask(env)
    await _ask(env, "other")
    before = {s["name"]: s["id"] for s in await step_rows(env.db_path) if s["change_id"] == 1}
    await run_queue(env.bot)

    steps = await step_rows(env.db_path)
    ids = [s["id"] for s in steps]
    assert len(set(ids)) == len(ids) == 5
    assert {s["name"]: s["id"] for s in steps if s["name"] in ("plan", "last")} == before
    assert await _states(env) == ["DONE", "DONE"]


async def test_the_acknowledgement_names_the_request_s_first_job(env):
    """The member's acknowledgement names the job number of the request's first job."""
    _queue(env, _type(steps=[_act("a", []), _act("b", [])]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)

    first, _second = await step_rows(env.db_path)
    reply = acknowledgement(interaction)
    assert _acknowledges(reply)
    assert f"job #{first['id']}" in reply


async def test_a_change_asked_while_the_queue_is_stopped_joins_the_back_and_says_so(env):
    """A request made while the queue is stopped is saved and acknowledged as under way, the
    acknowledgement adding that the queue is stopped at job #N; it runs only once that job is
    cleared."""
    api = _api()
    holder = {"fail": api.StepFailedOnDiscord("Missing Access")}
    ran: list[str] = []
    _queue(env, _type("stopping", steps=[_fails_while(holder)]),
           _type("dummy", steps=[_act("a", ran)]))

    await _ask(env, "stopping")
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    interaction = member_interaction(env.bot)
    await _ask(env, "dummy", interaction=interaction)
    await run_queue(env.bot)

    reply = acknowledgement(interaction)
    assert _acknowledges(reply)
    assert f"stopped at job #{job['id']}" in reply
    assert [(r["kind"], r["state"]) for r in await change_rows(env.db_path)] == [
        ("stopping", "RUNNING"), ("dummy", "QUEUED"),
    ]
    assert ran == []

    await discard_job(env.bot)

    assert ran == ["a"]
    assert await _states(env) == ["DONE", "DONE"]


async def test_a_switch_off_asked_while_the_queue_is_stopped_waits_behind_it(env):
    """A switch-off is no exception: asked while the queue is stopped, it waits behind the stopped
    job like any change, and runs once that job is cleared."""
    api = _api()
    holder = {"fail": api.StepFailedOnDiscord("Missing Access")}
    ran: list[str] = []
    switch_off = _type("switch_off", steps=[_act("off", ran)])
    if "overrides_waiting" in {f.name for f in dataclasses.fields(api.ChangeType)}:
        # The queue built before the rule let a switch-off overtake a waiting change.
        switch_off = dataclasses.replace(switch_off, overrides_waiting=True)
    _queue(env, _type("stopping", steps=[_fails_while(holder)]), switch_off)

    await _ask(env, "stopping")
    await run_queue(env.bot)
    await _ask(env, "switch_off")
    await run_queue(env.bot)

    assert ran == []
    assert await _states(env) == ["RUNNING", "QUEUED"]

    await discard_job(env.bot)

    assert ran == ["off"]
    assert await _states(env) == ["DONE", "DONE"]


async def test_a_stop_is_retried_after_1_5_10_15_30_and_60_minutes_counted_from_the_first_failure(env):
    """The bot's own tries fall 1, 5, 10, 15, 30 and 60 minutes after the job first failed, however
    late a try before them ran: a try made at 3 minutes leaves the next at 5."""
    ran: list[str] = []
    holder = {"fail": RuntimeError("boom")}
    _queue(env, _type(steps=[_fails_while(holder, ran=ran)]))

    await _ask(env)
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job["failing_since"] == NOW.isoformat()
    marks = [datetime.fromisoformat(job["next_try_at"]) - NOW]

    await _try_at(env, 3)
    for at in (5, 10, 15, 30):
        job = await stopped_job(env.db_path)
        marks.append(datetime.fromisoformat(job["next_try_at"]) - NOW)
        await _try_at(env, at - 0.5)
        assert (await stopped_job(env.db_path))["tries"] == job["tries"]
        await _try_at(env, at)
    job = await stopped_job(env.db_path)
    marks.append(datetime.fromisoformat(job["next_try_at"]) - NOW)

    assert [mark.total_seconds() / 60 for mark in marks] == [1, 5, 10, 15, 30, 60]
    assert len(ran) == 6
    assert job["tries"] == 6
    assert job["failing_since"] == NOW.isoformat()


async def test_after_the_sixty_minute_try_fails_the_log_says_only_retry_continues_and_no_try_is_made(env):
    """The 60-minute try that fails writes one ❌ line naming the job, by the change's own words
    where the job has none; no try follows, and the worker is set to wake for none."""
    ran: list[str] = []
    _queue(env, _type(steps=[_fails_while({"fail": RuntimeError("boom")}, ran=ran)]))

    await _ask(env)
    await run_queue(env.bot)
    for at in (1, 5, 10, 15, 30, 60):
        await _try_at(env, at)

    job = await stopped_job(env.db_path)
    hour = HOUR_LINE.format(id=job["id"], job=WHAT)
    assert [line.split("\n")[0] for line in await _lines(env)
            if "still fails after an hour" in line] == [hour]
    assert job["next_try_at"] is None
    assert len(ran) == 7

    await _try_at(env, 60 * 24)

    assert len(ran) == 7
    assert await env.bot.change_queue._seconds_to_next_try() is None


async def test_a_failed_automatic_try_writes_no_line_of_its_own(env):
    """Only the stop is told: the tries at 1, 5, 10, 15 and 30 minutes that fail add nothing to the
    log channel and leave the member's reply as the stop left it."""
    _queue(env, _type(steps=[_fails_while({"fail": RuntimeError("boom")})]))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)
    lines = await _lines(env)
    edits = interaction.edit_original_response.await_count
    assert any("The queue is stopped at job #" in line for line in lines)

    for at in (1, 5, 10, 15, 30):
        await _try_at(env, at)

    assert (await stopped_job(env.db_path))["tries"] == 6
    assert await _lines(env) == lines
    assert interaction.edit_original_response.await_count == edits


async def test_a_stopped_job_that_goes_through_says_so_and_the_queue_runs_on(env):
    """A stopped job going through at a try writes one ✅ line naming it, and the jobs and
    changes behind it run."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    _queue(
        env,
        _type(steps=[_fails_while(holder, describe=_described("posting the dummy")),
                     _act("after", ran)]),
        _type("later", steps=[_act("later", ran)]),
    )

    await _ask(env)
    await _ask(env, "later")
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    holder["fail"] = None
    await _try_at(env, 1)

    went = WENT_THROUGH.format(id=job["id"], job="posting the dummy")
    assert [line.split("\n")[0] for line in await _lines(env) if "went through" in line] == [went]
    assert ran == ["after", "later"]
    assert await stopped_job(env.db_path) is None
    assert await _states(env) == ["DONE", "DONE"]


async def test_a_queue_stopped_at_a_restart_stays_stopped_until_retry_or_discard(env):
    """A restart within the hour makes no try of the stopped job, even when its try falls due: one
    ❌ line says the bot no longer tries it on its own, and a Retry sets the queue going."""
    from tests.support.change_queue import retry_job

    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    _queue(env, _type(steps=[_fails_while(holder, ran=ran)]),
           _type("later", steps=[_act("later", ran)]))

    await _ask(env)
    await _ask(env, "later")
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    env.clock.advance(minutes=2)

    await restart_queue(env.bot)
    try:
        await run_queue(env.bot)
        assert (await stopped_job(env.db_path))["next_try_at"] is None
        env.clock.advance(hours=2)
        await run_queue(env.bot)

        assert ran == ["post"]
        restart = RESTART_LINE.format(id=job["id"], job=WHAT)
        assert [line.split("\n")[0] for line in await _lines(env)
                if "After the restart" in line] == [restart]

        holder["fail"] = None
        await retry_job(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert ran == ["post", "post", "later"]
    assert await _states(env) == ["DONE", "DONE"]


async def test_a_fault_while_recording_a_stop_is_logged_and_the_job_is_tried_again(env, caplog,
                                                                                   monkeypatch):
    """Where the stop's own save raises, the worker's catch-all logs it to the host and looks again
    after its pause: the job, not marked stopped, runs again, and its failure is recorded then."""
    from leaguebot.core.services import change_queue

    caplog.set_level(logging.INFO)
    monkeypatch.setattr(change_queue, "WORKER_PAUSE_AFTER_FAULT", 0.01)
    ran: list[str] = []
    queue = _queue(env, _type(steps=[_fails_while({"fail": RuntimeError("boom")}, ran=ran)]))
    record_stop = queue._stop
    calls = {"n": 0}

    async def _locked_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return await record_stop(*args, **kwargs)

    monkeypatch.setattr(queue, "_stop", _locked_once)

    await _ask(env)
    await maybe_await(queue.start())
    try:
        async def _stopped() -> bool:
            return await stopped_job(env.db_path) is not None

        await _eventually(_stopped)
    finally:
        await maybe_await(queue.stop())

    assert ran == ["post", "post"]
    assert (await stopped_job(env.db_path))["tries"] == 1
    assert any("database is locked" in str(r.exc_info[1]) for r in _host_errors(caplog))


async def test_the_member_s_reply_says_what_a_discard_left_undone(env):
    """Discarding a request's stopped job drops that job alone: it is kept done, with who
    discarded it and when, the request's later jobs run, and its outcome, reading the discard,
    tells the member what was not done."""
    ran: list[str] = []

    def _outcome(ctx):
        undone = [s.name for s in ctx.steps if (s.result or {}).get("discarded")]
        return f"⚠️ Done, but not: {', '.join(undone)}." if undone else "✅ Done."

    _queue(env, _type(steps=[_fails_while({"fail": RuntimeError("boom")}, ran=ran),
                             _act("after", ran)], outcome=_outcome))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)
    await discard_job(env.bot)

    assert ran == ["post", "after"]
    post, after = await step_rows(env.db_path)
    assert post["done_at"] is not None and after["done_at"] is not None
    assert post["result"]["discarded"]["at"] == NOW.isoformat()
    assert "by" in post["result"]["discarded"]
    assert "⚠️ Done, but not: post." in updated_reply(interaction)
    assert await _states(env) == ["DONE"]


async def test_discarding_a_change_whose_check_failed_drops_the_whole_change(env):
    """A bot change stopped at its check before it started is dropped whole by Discard: none of
    its jobs runs, it ends DISCARDED, and the change behind it runs."""
    ran: list[str] = []

    async def _refused(_ctx):
        return _api().Verdict.refuse("The forecast channel is missing.", "it is missing")

    _queue(env, _type(steps=[_act("a", ran), _act("b", ran)], check=_refused),
           _type("later", steps=[_act("later", ran)]))

    await _ask(env, origin=_api().ChangeOrigin.BOT)
    await _ask(env, "later")
    await run_queue(env.bot)
    assert (await stopped_job(env.db_path))["name"] == "a"

    await discard_job(env.bot)

    assert ran == ["later"]
    assert await _states(env) == ["DISCARDED", "DONE"]


async def test_a_change_ended_at_its_check_leaves_no_timer_behind(env):
    """A change stopped at its check that then ends without any of its jobs running leaves the
    worker no try to wake for, once the time its next try was set for has passed: whether a league
    admin discards it, a try finds the bot's change no longer due, or a try refuses a member's."""
    api = _api()
    mode = {"verdict": "go"}
    ran: list[str] = []

    async def _check(_ctx):
        if mode["verdict"] == "raise":
            raise RuntimeError("the check broke")
        if mode["verdict"] == "refuse":
            return api.Verdict.refuse("The forecast channel is missing.", "it is missing")
        if mode["verdict"] == "not_due":
            return api.Verdict.not_due("the season has ended")
        return api.Verdict.go()

    queue = _queue(env, _type(steps=[_act("a", ran)], check=_check))

    async def _stopped_at_check(verdict: str, **ask) -> None:
        mode["verdict"] = "go"
        await _ask(env, **ask)
        mode["verdict"] = verdict
        await run_queue(env.bot)
        assert await stopped_job(env.db_path) is not None, "the change did not stop"

    await _stopped_at_check("refuse", origin=api.ChangeOrigin.BOT)
    await discard_job(env.bot)
    env.clock.advance(minutes=2)

    assert await queue._seconds_to_next_try() is None

    await _stopped_at_check("raise", payload={"n": 2}, origin=api.ChangeOrigin.BOT)
    mode["verdict"] = "not_due"
    env.clock.advance(minutes=1)
    await run_queue(env.bot)
    env.clock.advance(minutes=2)

    assert await queue._seconds_to_next_try() is None

    await _stopped_at_check("raise", payload={"n": 3})
    mode["verdict"] = "refuse"
    env.clock.advance(minutes=1)
    await run_queue(env.bot)
    env.clock.advance(minutes=2)

    assert await queue._seconds_to_next_try() is None
    assert await _states(env) == ["DISCARDED", "DROPPED", "REFUSED"]
    assert ran == []
