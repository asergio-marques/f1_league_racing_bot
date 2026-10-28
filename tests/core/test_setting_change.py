"""One settings change, built by `setting_change` from a `Setting` (#439, slice 7).

Every settings command of results, weather and images declares a `Setting`, and one factory turns
it into a change type: the value, its audit entry and its log line saved together, a value already
held answered "Nothing changed" when the change runs, and a fault that saves nothing, says so and
lets the lane go on. These tests drive a dummy setting on a table of the test's own, through the
real queue on a database built by the migrations, with "now" pinned.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.utils.interaction_errors import failure_reply
from tests.support.change_queue import (
    MEMBER_ID,
    Clock,
    acknowledgement,
    attach_queue,
    change_rows,
    league_double,
    maybe_await,
    member_interaction,
    queued_log_lines,
    restart_queue,
    run_queue,
    seed_server,
    stopped_job,
    updated_reply,
)

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
KIND = "dummy.setting.value"
#: The member's command, as the change is asked with it and as its failure line names it.
WHAT = "`/dummy set`"
#: The command as the setting's line names it.
COMMAND = "/dummy set"
#: The member as a log line names them, the mention wrapped so it notifies nobody.
NAMED = f"Admin (`<@{MEMBER_ID}>`)"
NOTHING_SAVED_HERE = "Nothing was saved. Run `/dummy set` again to retry."
PASTE_NOT_SAVED = "Nothing from the paste was saved. Paste it again to retry."
FAILED_LINE = f"❌ {WHAT} failed for {NAMED} — {{fault}}. The details are in the host's log."


def _doing(value: int) -> str:
    return f"Setting the dummy value to {value}"


# ---------------------------------------------------------------------------
# The dummy setting
# ---------------------------------------------------------------------------


@pytest.fixture
async def env(tmp_path):
    db_path = str(tmp_path / "settings.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    async with get_connection(db_path) as db:
        # The test's own tables: the setting's, and one it does not declare.
        await db.execute("CREATE TABLE dummy_setting (id INTEGER PRIMARY KEY, value INTEGER)")
        await db.execute("INSERT INTO dummy_setting (id, value) VALUES (1, 5)")
        await db.execute("CREATE TABLE undeclared (v TEXT)")
        await db.commit()
    return SimpleNamespace(
        db_path=db_path, bot=league_double(db_path), clock=Clock(NOW), ran=[],
        knobs={
            "refuse": False,        # the check refuses
            "check_faults": 0,      # how many checks to come raise
            "save_faults": 0,       # how many saves to come raise after writing
            "after_faults": 0,      # how many after-steps to come raise
            "undeclared": False,    # the save also writes a table it does not declare
        },
    )


def _setting(env, *, after: str | None = None, carries_line: bool = False, **fields) -> Any:
    """The dummy `Setting`: one integer held in `dummy_setting`, set to the payload's `value`.

    *after*, where named, is a step after the save (an `ACT`) that records it ran, notes
    "Checked." for the reply, and raises while `after_faults` lasts; *carries_line* makes the
    success line wait for it.
    """
    from leaguebot.core.models.change import AuditRecord, PlannedStep, StepKind, StepResult, Verdict
    from leaguebot.core.services.change_queue import Step
    from leaguebot.core.services.setting_change import After, Saved, Setting

    knobs = env.knobs

    async def _check(_ctx):
        if knobs["check_faults"]:
            knobs["check_faults"] -= 1
            raise RuntimeError("the check broke")
        return Verdict.refuse("⚠️ Not now.") if knobs["refuse"] else Verdict.go()

    async def _save(db, payload):
        cursor = await db.execute("SELECT value FROM dummy_setting WHERE id = 1")
        held = (await cursor.fetchone())[0]
        then = (PlannedStep(after, {}),) if after else ()
        if held == payload["value"]:
            # `then` is handed back here too: the factory, not the save, keeps it unplanned.
            return Saved(changed=False, details=(f"value: {held}",), result={"value": held},
                         then=then)
        await db.execute("UPDATE dummy_setting SET value = ? WHERE id = 1", (payload["value"],))
        if knobs["undeclared"]:
            await db.execute("INSERT INTO undeclared (v) VALUES ('x')")
        if knobs["save_faults"]:
            knobs["save_faults"] -= 1
            raise RuntimeError("the save broke")
        return Saved(
            changed=True,
            audits=(AuditRecord("DUMMY_SET", {"value": held}, {"value": payload["value"]}),),
            details=(f"value: {payload['value']}",),
            result={"value": payload["value"]},
            then=then,
        )

    def _reply(_payload, results):
        saved = results["save"]
        text = (
            f"✅ The dummy value is now **{saved['value']}**." if saved["changed"]
            else f"ℹ️ Nothing changed: the dummy value is already **{saved['value']}**."
        )
        note = (results.get(after) or {}).get("note") if after else None
        return f"{text}\n{note}" if note else text

    async def _after(_ctx):
        env.ran.append(after)
        if knobs["after_faults"]:
            knobs["after_faults"] -= 1
            raise RuntimeError("the step after broke")
        return StepResult(result={"note": "Checked."})

    afters = {} if after is None else {
        after: After(
            step=Step(after, StepKind.ACT, _after),
            fault_reply=lambda _payload, reply: f"{reply}\n⚠️ Not checked.",
            carries_line=carries_line,
        ),
    }
    return Setting(
        kind=KIND,
        command=lambda _payload: COMMAND,
        doing=lambda payload: _doing(payload["value"]),
        check=_check,
        save=_save,
        reply=_reply,
        writes=("dummy_setting",),
        after=afters,
        **fields,
    )


def _league(env, **setting) -> Any:
    """Attach the real queue, with the dummy setting's change type registered on it."""
    from leaguebot.core.services.setting_change import setting_change

    return attach_queue(
        env.bot, env.db_path, now=env.clock, types=(setting_change(_setting(env, **setting)),)
    )


async def _ask(env, value: int, interaction: Any = None) -> Any:
    """Ask the setting for *value*, as a cog asks it: the member's interaction and the command."""
    return await env.bot.change_queue.ask(
        KIND, {"value": value}, interaction=interaction or member_interaction(env.bot), what=WHAT,
    )


async def _value(env) -> int:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT value FROM dummy_setting WHERE id = 1")
        return (await cursor.fetchone())[0]


async def _audits(env) -> list[tuple[str, Any, Any, int]]:
    async with get_connection(env.db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_id FROM audit_entries ORDER BY id"
        )
        return [
            (row[0], json.loads(row[1]), json.loads(row[2]), row[3])
            for row in await cursor.fetchall()
        ]


async def _lines(env) -> list[str]:
    """Every log line written: those the log channel took, then those waiting on a retry, each
    without the separator the router puts beneath it (`OutputRouter._as_log_line`)."""
    rule = "\n" + "\u2015" * 36
    lines = list(env.bot.log_channel.sent) + await queued_log_lines(env.db_path)
    return [line.removesuffix(rule) for line in lines]


async def _states(env) -> list[str]:
    return [row["state"] for row in await change_rows(env.db_path)]


def _success_line(value: int) -> str:
    return f"{NAMED} | {COMMAND} | Success\n  value: {value}"


# ---------------------------------------------------------------------------
# Recorded twice, in one save
# ---------------------------------------------------------------------------


async def test_a_setting_s_value_audit_entry_and_line_are_saved_in_one_save(env, monkeypatch):
    """The audit entry's write raising undoes the value with it: no success line, the failure
    line queued instead, and the lane not stopped, the request behind it running."""
    from leaguebot.core.services import change_queue

    _league(env)
    real = change_queue.record_change_on
    faults = {"left": 1}

    async def _audit_once_broken(*args, **kwargs):
        if faults["left"]:
            faults["left"] -= 1
            raise sqlite3.OperationalError("disk I/O error")
        return await real(*args, **kwargs)

    monkeypatch.setattr(change_queue, "record_change_on", _audit_once_broken)

    await _ask(env, 6)
    await _ask(env, 7)
    await run_queue(env.bot)

    lines = await _lines(env)
    assert _success_line(6) not in lines
    assert FAILED_LINE.format(fault="OperationalError") in lines
    assert await stopped_job(env.db_path) is None
    assert await _value(env) == 7
    assert [(a[1], a[2]) for a in await _audits(env)] == [({"value": 5}, {"value": 7})]


async def test_a_value_already_held_writes_a_nothing_changed_line_and_no_audit_entry(env):
    _league(env)
    interaction = member_interaction(env.bot)

    await _ask(env, 5, interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == "ℹ️ Nothing changed: the dummy value is already **5**."
    assert (await _lines(env)).count(f"{NAMED} | {COMMAND} | Nothing changed\n  value: 5") == 1
    assert await _audits(env) == []
    assert await _states(env) == ["DONE"]


async def test_the_line_names_the_member_the_command_and_the_values_beneath(env):
    """One line, "Name (<@id>) | command | Success", the values beneath it; one audit entry naming
    the member, from the value held to the value set."""
    _league(env)
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == "✅ The dummy value is now **6**."
    assert (await _lines(env)).count(_success_line(6)) == 1
    assert await _audits(env) == [("DUMMY_SET", {"value": 5}, {"value": 6}, MEMBER_ID)]
    assert await _value(env) == 6


@pytest.mark.parametrize("value", [
    pytest.param(5, id="the value held when asked"),
    pytest.param(7, id="another value"),
])
async def test_a_value_held_when_asked_is_set_when_a_change_ahead_moved_it(env, value):
    """Held 5: 6 is asked, then *value* while 6 waits, and it is queued behind it. 5 is not
    refused for being held when asked, and once 6 has run it sets 5 again; another value, 7, is
    set the same way."""
    _league(env)
    second = member_interaction(env.bot)

    await _ask(env, 6)
    await _ask(env, value, second)
    await run_queue(env.bot)

    assert await _states(env) == ["DONE", "DONE"]
    assert updated_reply(second) == f"✅ The dummy value is now **{value}**."
    assert [(a[1], a[2]) for a in await _audits(env)] == [
        ({"value": 5}, {"value": 6}), ({"value": 6}, {"value": value}),
    ]
    assert await _value(env) == value


# ---------------------------------------------------------------------------
# Checked twice, and a request repeated
# ---------------------------------------------------------------------------


async def test_a_request_its_check_refuses_is_refused_at_once_and_nothing_is_queued(env):
    _league(env)
    env.knobs["refuse"] = True
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await run_queue(env.bot)

    assert acknowledgement(interaction) == "⚠️ Not now."
    assert await change_rows(env.db_path) == []
    assert await _value(env) == 5


async def test_a_setting_its_check_refuses_when_it_runs_is_refused_and_says_why(env):
    _league(env)
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    env.knobs["refuse"] = True
    await run_queue(env.bot)

    assert updated_reply(interaction) == "⚠️ Not now."
    assert f"⛔ {WHAT} refused for {NAMED} — Not now." in "\n".join(await _lines(env))
    assert await _states(env) == ["REFUSED"]
    assert await _value(env) == 5
    assert await _audits(env) == []


async def test_the_same_setting_to_the_same_value_while_it_waits_is_refused_as_a_repeat(env):
    _league(env)
    second = member_interaction(env.bot)

    await _ask(env, 6)
    await _ask(env, 6, second)

    assert acknowledgement(second) == (
        f"⚠️ {_doing(6)} has already been asked for and has not started yet, "
        "so it was not asked for again."
    )
    assert len(await change_rows(env.db_path)) == 1


async def test_the_acknowledgement_names_what_is_being_set(env):
    """The member is told at once what is being set; a command that deferred is told through a
    follow-up, and that message is updated with the outcome."""
    _league(env)
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)

    said = acknowledgement(interaction)
    assert said.startswith("⏳") and _doing(6) in said

    deferred = member_interaction(env.bot)
    message = SimpleNamespace(edit=AsyncMock())
    deferred.followup.send = AsyncMock(return_value=message)
    await deferred.response.defer()

    await _ask(env, 7, deferred)
    await run_queue(env.bot)

    [sent] = deferred.followup.send.await_args_list
    text = sent.args[0] if sent.args else sent.kwargs.get("content")
    assert text.startswith("⏳") and _doing(7) in text
    [edit] = message.edit.await_args_list
    assert (edit.args[0] if edit.args else edit.kwargs.get("content")) == (
        "✅ The dummy value is now **7**."
    )


# ---------------------------------------------------------------------------
# A fault never holds up the lane
# ---------------------------------------------------------------------------


async def test_a_fault_in_the_save_saves_nothing_says_to_run_it_again_and_the_lane_goes_on(env):
    from leaguebot.core.services.setting_change import NOTHING_SAVED

    assert NOTHING_SAVED == "Nothing was saved. Run `{command}` again to retry."
    _league(env)
    env.knobs["save_faults"] = 1
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await _ask(env, 7)
    await run_queue(env.bot)

    assert updated_reply(interaction) == failure_reply(WHAT, NOTHING_SAVED_HERE)
    lines = await _lines(env)
    assert FAILED_LINE.format(fault="RuntimeError") in lines
    assert _success_line(6) not in lines
    assert await stopped_job(env.db_path) is None
    assert await _value(env) == 7
    assert [(a[1], a[2]) for a in await _audits(env)] == [({"value": 5}, {"value": 7})]


async def test_a_setting_s_own_not_saved_words_replace_the_generic_ones(env):
    _league(env, not_saved=PASTE_NOT_SAVED)
    env.knobs["save_faults"] = 1
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == failure_reply(WHAT, PASTE_NOT_SAVED)
    assert await _value(env) == 5


async def test_the_steps_after_the_save_run_only_when_it_changed_something(env):
    """The save hands back its steps after it either way; they are planned only where it changed
    the value."""
    _league(env, after="advise")
    held, changed = member_interaction(env.bot), member_interaction(env.bot)

    await _ask(env, 5, held)
    await run_queue(env.bot)

    assert env.ran == []
    assert updated_reply(held) == "ℹ️ Nothing changed: the dummy value is already **5**."

    await _ask(env, 6, changed)
    await run_queue(env.bot)

    assert env.ran == ["advise"]
    assert updated_reply(changed) == "✅ The dummy value is now **6**.\nChecked."


async def test_a_stop_after_the_save_runs_the_steps_after_it_on_restart(env):
    _league(env, after="advise")

    await _ask(env, 6)
    await run_queue(env.bot, steps=1)
    assert env.ran == []
    assert await _value(env) == 6

    await restart_queue(env.bot)
    try:
        await run_queue(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert env.ran == ["advise"]
    assert await _states(env) == ["DONE"]
    assert (await _lines(env)).count(_success_line(6)) == 1


async def test_a_fault_in_a_step_after_the_save_keeps_the_setting_and_says_what_was_not_done(env):
    _league(env, after="advise")
    env.knobs["after_faults"] = 1
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await _ask(env, 7)
    await run_queue(env.bot)

    assert updated_reply(interaction) == "✅ The dummy value is now **6**.\n⚠️ Not checked."
    lines = await _lines(env)
    assert lines.count(_success_line(6)) == 1
    assert FAILED_LINE.format(fault="RuntimeError") in lines
    assert await stopped_job(env.db_path) is None
    assert env.ran == ["advise", "advise"]
    assert await _value(env) == 7


async def test_a_line_carried_by_a_step_after_the_save_is_written_only_when_it_succeeds(env):
    """Where the step after the save carries the line (the daily job's arming), the success line
    waits for it: written once it succeeds, and never where it fails, the failure line standing
    in its place and the value staying saved."""
    _league(env, after="arm", carries_line=True)

    await _ask(env, 6)
    await run_queue(env.bot, steps=1)

    assert await _value(env) == 6
    assert _success_line(6) not in await _lines(env)

    await run_queue(env.bot)

    assert (await _lines(env)).count(_success_line(6)) == 1

    env.knobs["after_faults"] = 1
    await _ask(env, 7)
    await run_queue(env.bot)

    lines = await _lines(env)
    assert _success_line(7) not in lines
    assert FAILED_LINE.format(fault="RuntimeError") in lines
    assert await _value(env) == 7
    assert await stopped_job(env.db_path) is None


async def test_a_check_fault_when_the_setting_runs_says_nothing_was_saved_records_a_failure_and_the_lane_goes_on(
    env,
):
    _league(env)
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await _ask(env, 7)
    env.knobs["check_faults"] = 1
    await run_queue(env.bot)

    assert updated_reply(interaction) == failure_reply(WHAT, NOTHING_SAVED_HERE)
    lines = await _lines(env)
    assert [line for line in lines if line.startswith(f"❌ {WHAT} failed for ")] == [
        FAILED_LINE.format(fault="RuntimeError")
    ]
    assert not any(line.startswith("⛔") for line in lines)
    assert await _states(env) == ["REFUSED", "DONE"]
    assert await stopped_job(env.db_path) is None
    assert await _value(env) == 7


async def test_a_setting_writing_a_table_it_does_not_declare_saves_nothing(env):
    """The queue's authoriser refuses the write to a table the setting does not declare; the save
    is ended by its fault path, nothing of it kept, and the lane goes on."""
    _league(env)
    env.knobs["undeclared"] = True
    interaction = member_interaction(env.bot)

    await _ask(env, 6, interaction)
    await run_queue(env.bot)

    assert updated_reply(interaction) == failure_reply(WHAT, NOTHING_SAVED_HERE)
    assert await _value(env) == 5
    async with get_connection(env.db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM undeclared")
        assert (await cursor.fetchone())[0] == 0
    assert await stopped_job(env.db_path) is None
    assert await _audits(env) == []
