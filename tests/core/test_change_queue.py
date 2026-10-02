"""The change queue: a change asked for is acknowledged, then carried out step by step (#439).

`docs/design/architecture.md`, "How a change is carried out", designs it. These tests use dummy
change types on a database built by the migrations, with "now" pinned, and a real `OutputRouter`
whose log channel records what it is sent. What Discord makes fail, and the retries, are in
`test_change_queue_retries.py`.

Everything of the queue is imported inside a test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

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
    updated_reply,
)

NOT_BUILT = "#439: the change queue is not built yet"

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
WHAT = "`/dummy`"
DOING = "Doing the dummy thing"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
ACKNOWLEDGEMENT = (
    f"⏳ {DOING}. This message will be updated when it is done; if it takes longer, "
    "the log channel will say so."
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


def _type(kind: str = "dummy", *, steps, check=None, outcome="✅ Done.",
          fault_outcome="Nothing was changed.", places=(), repeatable=False,
          overrides_waiting=False, opening=None) -> Any:
    """A change type running *steps* in the order given, each opened with no payload."""
    api = _api()

    async def _go(_ctx):
        return api.Verdict.go()

    return api.ChangeType(
        kind=kind,
        opening=(
            tuple(opening) if opening is not None
            else tuple(api.PlannedStep(s.name, {}, places=tuple(places)) for s in steps)
        ),
        steps={s.name: s for s in steps},
        check=check or _go,
        key=lambda payload: f"{kind}|{json.dumps(payload, sort_keys=True)}",
        doing=lambda _payload: DOING,
        outcome=outcome if callable(outcome) else (lambda _ctx: outcome),
        fault_outcome=lambda _ctx: fault_outcome,
        places=lambda _payload: tuple(places),
        repeatable=repeatable,
        overrides_waiting=overrides_waiting,
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
    ran: list[str] = []
    _queue(env, _type(steps=[
        _save("a", "first"), _save("b", "second", fails=RuntimeError("boom")), _act("c", ran),
    ]))

    await _ask(env)
    await run_queue(env.bot)

    assert await _scratch(env) == ["first"]
    assert [bool(s["done_at"]) for s in await step_rows(env.db_path)] == [True, False, False]
    assert ran == []
    assert await _states(env) == ["FAULTED"]


async def test_a_fault_is_reported_to_the_member_the_log_channel_and_the_host(env, caplog):
    from leaguebot.core.utils.interaction_errors import failure_reply

    caplog.set_level(logging.INFO)
    _queue(env, _type(steps=[_act("a", [], fails=RuntimeError("boom"))],
                      fault_outcome="Nothing was changed."))
    interaction = member_interaction(env.bot)

    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == failure_reply(WHAT, "Nothing was changed.")
    assert (
        f"❌ {WHAT} failed for {NAMED} — RuntimeError. The details are in the host's log."
        in "\n".join(await _lines(env))
    )
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

    assert acknowledgement(interaction) == ACKNOWLEDGEMENT
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


async def test_a_bot_change_lacking_what_the_league_can_repair_waits_and_says_so(env):
    """It waits on the retries, and one line, not one per try, says what is missing."""
    holder = {"repairable": False}
    ran: list[str] = []

    async def _check(_ctx):
        api = _api()
        if holder["repairable"]:
            return api.Verdict.repairable("the forecast channel is missing")
        return api.Verdict.go()

    _queue(env, _type(steps=[_act("a", ran)], check=_check))
    api = _api()

    await _ask(env, origin=api.ChangeOrigin.BOT)
    holder["repairable"] = True
    await run_queue(env.bot)

    assert await _states(env) == ["WAITING"]
    assert ran == []
    saying = [line for line in await _lines(env) if "the forecast channel is missing" in line]
    assert len(saying) == 1

    env.clock.advance(seconds=30)
    await run_queue(env.bot)

    saying = [line for line in await _lines(env) if "the forecast channel is missing" in line]
    assert len(saying) == 1
    assert ran == []


async def test_a_request_repeating_the_last_change_asked_for_before_it_starts_is_refused_saying_so(env):
    _queue(env, _type(steps=[_act("a", [])]))
    first, second = member_interaction(env.bot), member_interaction(env.bot)

    await _ask(env, interaction=first)
    await _ask(env, interaction=second)

    reason = (
        f"{DOING} has already been asked for and has not started yet, "
        "so it was not asked for again."
    )
    assert acknowledgement(first) == ACKNOWLEDGEMENT
    assert acknowledgement(second) == f"⚠️ {reason}"
    assert len(await change_rows(env.db_path)) == 1
    # The line drops the reply's opening mark, which its own mark replaces.
    assert f"⛔ {WHAT} refused for {NAMED} — {reason}" in "\n".join(await _lines(env))


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_change_is_queued_again_once_another_has_been_asked_for_after_it(env):
    """Whatever became of the change asked for in between: here it has already finished, ahead of
    a change held behind one waiting on a retry, and the held one may still be asked for again."""
    api = _api()
    held = {"fail": api.StepFailedOnDiscord("Missing Access")}

    async def _fails(_ctx):
        if held["fail"] is not None:
            raise held["fail"]
        return api.StepResult()

    _queue(
        env,
        _type("waiting", steps=[api.Step("post", api.StepKind.ACT, _fails)], places=("channel:1",)),
        _type("dummy", steps=[_act("a", [])], places=("channel:1",)),
        _type("elsewhere", steps=[_act("b", [])], places=("channel:2",)),
    )

    await _ask(env, "dummy")
    await _ask(env, "elsewhere")
    await _ask(env, "dummy")
    assert [r["kind"] for r in await change_rows(env.db_path)] == ["dummy", "elsewhere", "dummy"]

    await run_queue(env.bot)
    await _ask(env, "waiting")
    await run_queue(env.bot)
    await _ask(env, "dummy")
    await _ask(env, "elsewhere")
    await run_queue(env.bot)

    rows = await change_rows(env.db_path)
    assert [(r["kind"], r["state"]) for r in rows[3:]] == [
        ("waiting", "WAITING"), ("dummy", "QUEUED"), ("elsewhere", "DONE"),
    ]

    await _ask(env, "dummy")

    rows = await change_rows(env.db_path)
    assert [(r["kind"], r["state"]) for r in rows[3:]] == [
        ("waiting", "WAITING"), ("dummy", "QUEUED"), ("elsewhere", "DONE"), ("dummy", "QUEUED"),
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

    assert acknowledgement(interaction) == ACKNOWLEDGEMENT
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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
    """As a pack or a factory reset would remove it while the step runs: the mark updates no
    row, so the step's audits and lines are rolled back with it, and the host's log says so."""
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


async def test_a_change_of_a_kind_the_bot_no_longer_knows_is_faulted_not_lost(env):
    _queue(env)
    async with get_connection(env.db_path) as db:
        await db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, payload, origin, state, actor_id, "
            "actor_name, actor_display, what, places) "
            "VALUES ('vanished', 'vanished', '{}', 'MEMBER', 'QUEUED', ?, 'Admin#0001', 'Admin', "
            "'`/gone`', '[]')",
            (MEMBER_ID,),
        )
        await db.execute(
            "INSERT INTO queued_change_steps (change_id, position, name) VALUES (1, 0, 'a')"
        )
        await db.commit()

    await run_queue(env.bot)

    assert await _states(env) == ["FAULTED"]
    assert f"❌ `/gone` failed for {NAMED}" in "\n".join(await _lines(env))


async def test_the_queue_writes_its_lines_through_the_router_it_was_handed(env):
    """The bot carries no `output_router`: the queue never looks one up on it."""
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
    assert f"❌ {WHAT} failed for {NAMED} — RuntimeError." in sent
