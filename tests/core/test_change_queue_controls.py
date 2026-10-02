"""Retry and Discard: the buttons on the stop notice of the job the queue is stopped at (#439).

The owner's rule (2026-10-02): a job that fails stops the queue until it is cleared. A league
manager or admin may press Retry at any time, and after the hour it is the only way on; a league
admin may press Discard, which drops the job and lets the queue run on, recorded with what was not
done. The buttons sit on the one ❌ message the stop posts to the log channel, and still work after
a restart. `docs/design/architecture.md`, "How a change is carried out", designs it. The dummy
change types and the league are `test_change_queue.py`'s.
"""
from __future__ import annotations

from typing import Any

import discord
import pytest

from leaguebot.core.db.database import get_connection
from tests.core.test_change_queue import (  # noqa: F401 — `env` is the fixture
    STOPPED_AT,
    WENT_THROUGH,
    WHAT,
    _act,
    _api,
    _ask,
    _described,
    _fails_while,
    _lines,
    _queue,
    _states,
    _try_at,
    _type,
    env,
)
from tests.support.change_queue import (
    LOG_CHANNEL_WARNING,
    discard_job,
    http_error,
    maybe_await,
    member_interaction,
    queued_log_lines,
    restart_queue,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    tier_member,
)

MANAGER = {"member_id": 5151, "display_name": "Manager", "name": "Manager#0001"}
ADMIN = {"member_id": 6161, "display_name": "Boss", "name": "Boss#0001"}
#: The two pressers as a log line names them, the mention wrapped so it notifies nobody.
MANAGER_NAMED = "Manager (`<@5151>`)"
ADMIN_NAMED = "Boss (`<@6161>`)"


def _manager() -> Any:
    return tier_member("manager", **MANAGER)


def _admin() -> Any:
    return tier_member("admin", **ADMIN)


def _private(interaction: Any) -> list[str]:
    """What the presser was told, seen by them alone."""
    calls = (interaction.response.send_message.await_args_list
             + interaction.followup.send.await_args_list)
    return [
        str(call.args[0] if call.args else call.kwargs.get("content", ""))
        for call in calls if call.kwargs.get("ephemeral") is True
    ]


async def _stop(env, holder: dict, ran: list, *, later: bool = True) -> dict:
    """A request of two jobs, its first failing with `holder["fail"]` while set, and a change
    asked after it; run until the queue stops. Gives the stopped job."""
    _queue(
        env,
        _type(steps=[_fails_while(holder, ran=ran, describe=_described("posting the dummy")),
                     _act("after", ran)]),
        _type("later", steps=[_act("later", ran)]),
    )
    await _ask(env)
    if later:
        await _ask(env, "later")
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job is not None, "the queue did not stop"
    return job


async def _press_on(env, action: str, notice_id: int | None, user: Any) -> Any:
    """Press *action* on the notice *notice_id*, as *user*, then run the queue."""
    interaction = member_interaction(env.bot, user=user)
    interaction.message.id = notice_id
    await maybe_await(getattr(env.bot.change_queue, action)(notice_id, interaction))
    await run_queue(env.bot)
    return interaction


def _notice(env, job: dict) -> Any:
    [notice] = [m for m in env.bot.log_channel.posted if m.id == job["notice_message_id"]]
    return notice


def _buttons(message: Any) -> set[str]:
    return {child.custom_id for child in getattr(message.view, "children", None) or ()}


# ---------------------------------------------------------------------------
# Retry
# ---------------------------------------------------------------------------


async def test_retry_by_a_league_manager_tries_the_job_at_once(env):
    """Before the job's next try is due, a league manager's Retry tries it at once: it goes
    through, the request and the change behind it run, the manager is told privately, the log
    channel names them and says the job went through, and the notice loses its buttons."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    notice = _notice(env, job)
    holder["fail"] = None

    interaction = await retry_job(env.bot, user=_manager())

    assert ran == ["post", "post", "after", "later"]
    assert await _states(env) == ["DONE", "DONE"]
    assert _private(interaction)
    lines = await _lines(env)
    assert any(MANAGER_NAMED in line and f"job #{job['id']}" in line and "Retr" in line
               for line in lines)
    went = WENT_THROUGH.format(id=job["id"], job="posting the dummy")
    assert [line.split("\n")[0] for line in lines if "went through" in line] == [went]
    assert _buttons(notice) == set()


async def test_retry_by_a_league_admin_tries_the_job_at_once(env):
    """A league admin holds the manager's tier within theirs: their Retry tries the job at once."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    holder["fail"] = None

    interaction = await retry_job(env.bot, user=_admin())

    assert ran == ["post", "post", "after", "later"]
    assert await _states(env) == ["DONE", "DONE"]
    assert _private(interaction)
    assert any(ADMIN_NAMED in line and f"job #{job['id']}" in line for line in await _lines(env))


async def test_retry_after_the_hour_is_the_only_way_on_and_a_failed_retry_leaves_it_stopped(env):
    """Once the 60-minute try has failed, no try comes but a Retry. A Retry that fails leaves the
    queue stopped with no try scheduled; one that goes through sets the queue going."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    await _stop(env, holder, ran, later=False)
    for at in (1, 5, 10, 15, 30, 60):
        await _try_at(env, at)
    assert (await stopped_job(env.db_path))["next_try_at"] is None

    await retry_job(env.bot)

    job = await stopped_job(env.db_path)
    assert job["tries"] == 8
    assert job["next_try_at"] is None
    assert ran == ["post"] * 8

    holder["fail"] = None
    await retry_job(env.bot)

    assert ran == ["post"] * 9 + ["after"]
    assert await _states(env) == ["DONE"]


async def test_a_retry_whose_try_fails_is_recorded_naming_the_presser(env):
    """A Retry whose try fails writes one line naming the presser, the job and the kind of fault,
    and leaves the schedule as it stood."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    before = await _lines(env)

    await retry_job(env.bot, user=_manager())

    after = await stopped_job(env.db_path)
    added = [line for line in await _lines(env) if line not in before]
    assert any(MANAGER_NAMED in line and f"job #{job['id']}" in line and "RuntimeError" in line
               for line in added)
    assert after["next_try_at"] == job["next_try_at"]
    assert after["failing_since"] == job["failing_since"]
    assert ran == ["post", "post"]


# ---------------------------------------------------------------------------
# Discard
# ---------------------------------------------------------------------------


async def test_discard_by_a_league_admin_drops_the_job_and_the_queue_runs_on(env):
    """A league admin's Discard drops the stopped job alone: one line names the admin, job #N and
    what was not done for which request, an audit record keeps it, the notice loses its buttons,
    and the request's later job and the change behind it run."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    notice = _notice(env, job)

    interaction = await discard_job(env.bot, user=_admin())

    assert _buttons(notice) == set()
    assert ran == ["post", "after", "later"]
    assert await stopped_job(env.db_path) is None
    assert await _states(env) == ["DONE", "DONE"]
    assert _private(interaction)
    line = f"{ADMIN_NAMED} | Discard job #{job['id']} | Discarded"
    [said] = [text for text in await _lines(env) if line in text]
    assert f"not done: posting the dummy for {WHAT}" in said
    async with get_connection(env.db_path) as db:
        cursor = await db.execute(
            "SELECT actor_id, change_type FROM audit_entries WHERE change_type = ?",
            ("CHANGE_JOB_DISCARDED",),
        )
        audits = [dict(row) for row in await cursor.fetchall()]
    assert audits == [{"actor_id": ADMIN["member_id"], "change_type": "CHANGE_JOB_DISCARDED"}]


async def test_discard_by_a_league_manager_is_refused(env):
    """Only a league admin may discard: a league manager is told so privately, the refusal is a
    ⛔ line naming them, and the job still stops the queue."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)

    interaction = await discard_job(env.bot, user=_manager())

    assert any("league admin" in text for text in _private(interaction))
    assert any(line.startswith("⛔") and MANAGER_NAMED in line for line in await _lines(env))
    assert (await stopped_job(env.db_path))["id"] == job["id"]
    assert ran == ["post"]


async def test_a_press_by_a_member_without_a_tier_is_refused_and_logged(env):
    """A member holding neither tier is refused Retry and Discard alike, privately, each refusal a
    ⛔ line; the job is not tried and still stops the queue."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    nobody = tier_member(None, member_id=7171, display_name="Driver", name="Driver#0001")

    retried = await retry_job(env.bot, user=nobody)
    discarded = await discard_job(env.bot, user=nobody)

    assert _private(retried) and _private(discarded)
    refusals = [line for line in await _lines(env)
                if line.startswith("⛔") and "Driver (`<@7171>`)" in line]
    assert len(refusals) == 2
    assert (await stopped_job(env.db_path))["id"] == job["id"]
    assert ran == ["post"]


async def test_a_press_on_a_notice_whose_job_is_gone_is_refused_and_logged(env):
    """A notice whose job no longer stops the queue (it went through, a league admin discarded
    it, or its job and change are gone from the database, as a factory reset erases them) is
    refused, privately and with a ⛔ line, and nothing is tried. The rows are deleted by hand,
    leaving the server's configuration in place: a pack is refused while the queue holds a job,
    and a factory reset would erase the configuration too."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    holder["fail"] = None
    await _try_at(env, 1)
    assert await stopped_job(env.db_path) is None

    retried = await _press_on(env, "retry", job["notice_message_id"], _manager())
    discarded = await _press_on(env, "discard", job["notice_message_id"], _admin())

    assert _private(retried) and _private(discarded)
    lines = await _lines(env)
    assert any(line.startswith("⛔") and MANAGER_NAMED in line for line in lines)
    assert any(line.startswith("⛔") and ADMIN_NAMED in line for line in lines)
    assert ran == ["post", "post", "after", "later"]

    holder["fail"] = RuntimeError("boom")
    await _ask(env)
    await run_queue(env.bot)
    dropped = await stopped_job(env.db_path)
    await discard_job(env.bot, user=_admin())
    assert await stopped_job(env.db_path) is None
    tried = list(ran)

    retried = await _press_on(env, "retry", dropped["notice_message_id"], _manager())
    discarded = await _press_on(env, "discard", dropped["notice_message_id"], _admin())

    assert _private(retried) and _private(discarded)
    lines = await _lines(env)
    assert len([line for line in lines if line.startswith("⛔") and MANAGER_NAMED in line]) == 2
    assert len([line for line in lines if line.startswith("⛔") and ADMIN_NAMED in line]) == 2
    assert ran == tried

    await _ask(env)
    await run_queue(env.bot)
    packed = await stopped_job(env.db_path)
    # Every change and job is deleted, finished ones too, as a factory reset would erase them.
    async with get_connection(env.db_path) as db:
        await db.execute("DELETE FROM queued_change_steps")
        await db.execute("DELETE FROM queued_changes")
        await db.commit()

    after_pack = await _press_on(env, "retry", packed["notice_message_id"], _manager())

    assert _private(after_pack)
    assert len([line for line in await _lines(env)
                if line.startswith("⛔") and MANAGER_NAMED in line]) == 3


# ---------------------------------------------------------------------------
# The notice
# ---------------------------------------------------------------------------


async def test_the_stop_notice_s_buttons_work_after_a_restart(env):
    """At start-up the queue registers the notice's view, persistent, with its fixed ids: Retry
    pressed on a notice posted before the restart tries the job."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    holder["fail"] = None

    await restart_queue(env.bot)
    try:
        [view] = [call.args[0] for call in env.bot.add_view.call_args_list
                  if {getattr(c, "custom_id", None) for c in call.args[0].children}
                  == {"queue:retry", "queue:discard"}]
        assert view.timeout is None
        [retry] = [c for c in view.children if c.custom_id == "queue:retry"]
        interaction = member_interaction(env.bot, user=_manager())
        interaction.message.id = job["notice_message_id"]
        await retry.callback(interaction)
        await run_queue(env.bot)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert ran == ["post", "post", "after", "later"]
    assert await _states(env) == ["DONE", "DONE"]


async def test_the_stop_notice_loses_its_buttons_once_the_job_clears(env):
    """A job that goes through takes the buttons off its notice; a notice already deleted by the
    time its job is discarded is no failure, and the queue runs on."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    job = await _stop(env, holder, ran)
    notice = _notice(env, job)
    assert _buttons(notice) == {"queue:retry", "queue:discard"}

    holder["fail"] = None
    await _try_at(env, 1)

    assert _buttons(notice) == set()

    holder["fail"] = RuntimeError("boom")
    await _ask(env)
    await run_queue(env.bot)
    second = await stopped_job(env.db_path)
    env.bot.log_channel.posted.remove(_notice(env, second))

    await discard_job(env.bot)

    assert await stopped_job(env.db_path) is None
    assert await _states(env) == ["DONE", "DONE", "DONE"]


async def test_a_stop_notice_the_log_channel_refuses_is_posted_again_with_its_buttons(env):
    """A notice the log channel refuses is kept, never put on the log lines' retry queue, and the
    member is warned; it is posted again, with its buttons, at a later try. So also after the hour,
    where the post raised after the stop was saved, and at start-up."""
    holder = {"fail": RuntimeError("boom")}
    ran: list[str] = []
    forbidden = http_error(discord.Forbidden, status=403, text="Missing Access")
    _queue(env, _type(steps=[_fails_while(holder, ran=ran,
                                          describe=_described("posting the dummy"))]))

    # Refused at the stop, posted at a later try.
    env.bot.log_channel.fails = forbidden
    interaction = member_interaction(env.bot)
    await _ask(env, interaction=interaction)
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    assert job["tries"] == 1 and job["notice_message_id"] is None
    assert any(call.args and call.args[0] == LOG_CHANNEL_WARNING
               and call.kwargs.get("ephemeral") is True
               for call in interaction.followup.send.await_args_list)
    assert not any("The queue is stopped at job #" in line
                   for line in await queued_log_lines(env.db_path))
    await _try_at(env, 1)
    env.bot.log_channel.fails = None
    await _try_at(env, 5)

    job = await stopped_job(env.db_path)
    stop = STOPPED_AT.format(id=job["id"], job="posting the dummy", request=WHAT,
                             asker="Admin (`<@4242>`)", fault="RuntimeError")
    [notice] = [m for m in env.bot.log_channel.posted if m.content.startswith(stop)]
    assert job["notice_message_id"] == notice.id
    assert _buttons(notice) == {"queue:retry", "queue:discard"}
    await discard_job(env.bot)

    # The post raised after the stop was saved, and the channel refused it the whole hour.
    env.bot.log_channel.fails = RuntimeError("the connection dropped")
    started = env.clock.now
    await _ask(env)
    await run_queue(env.bot)
    job = await stopped_job(env.db_path)
    assert job["tries"] == 1 and job["notice_message_id"] is None
    for at in (1, 5, 10, 15, 30, 60):
        env.clock.now = started
        env.clock.advance(minutes=at)
        await run_queue(env.bot)
    assert (await stopped_job(env.db_path))["next_try_at"] is None
    env.bot.log_channel.fails = None
    env.clock.now = started
    env.clock.advance(hours=3)
    await run_queue(env.bot)

    job = await stopped_job(env.db_path)
    assert job["notice_message_id"] is not None
    assert _buttons(_notice(env, job)) == {"queue:retry", "queue:discard"}
    await discard_job(env.bot)

    # Refused at the stop, posted as the bot starts again.
    env.bot.log_channel.fails = forbidden
    await _ask(env)
    await run_queue(env.bot)
    assert (await stopped_job(env.db_path))["notice_message_id"] is None
    env.bot.log_channel.fails = None

    await restart_queue(env.bot)
    try:
        job = await stopped_job(env.db_path)
    finally:
        await maybe_await(env.bot.change_queue.stop())

    assert job["notice_message_id"] is not None
    assert _buttons(_notice(env, job)) == {"queue:retry", "queue:discard"}
    assert [row["notice_message_id"] is not None for row in await step_rows(env.db_path)
            if row["name"] == "post"] == [True, True, True]
