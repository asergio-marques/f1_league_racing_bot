"""Cancelling a round on the change queue (#439, slice 4b).

`/round cancel` checks its confirmation word, the season and the names at the press, then asks the
change queue for the round's cancellation. The rest of today's gates are the change type's check,
run when it is asked and again when it runs, in today's words. The timed work is removed first;
one save records the round cancelled with the status it was cancelled from, finishes its division
and moves the season on; then each enabled module's notice, the check-in call's take-down and the
calendar's repost follow as jobs of their own, each of which stops the queue when it fails; then
one closing line, the check-in written beneath it. A second cancellation of the round, or one
while its division's is in hand, is refused at once, naming the job.

Every test drives the real cog and the real queue through `tests/support/season_league.py`'s
`ongoing_league`, with "now" pinned. The change type is unbuilt until the build, so each test that
needs it is marked to fail until then: today the command cancels on the spot. The press's refusals
and the set of states that may be cancelled are what today's command already does, and pass as
they stand.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from contextlib import ExitStack
from datetime import timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.season_service import SeasonImmutableError
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    AM,
    CALENDAR_MESSAGE,
    CALL_MESSAGES,
    DIVISION_CANCEL_KIND,
    DIVISIONS,
    PRO,
    ROUND_CANCEL_KIND,
    SEASON_ID,
    cancel_division,
    cancel_round,
    cancellation_changes,
    ongoing_league,
    open_submission,
    reply,
    round_id,
)

PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
R3 = round_id(PRO, 3)
CANCELLED = "✅ Round **3** in **Pro** cancelled."
SUCCESS = "Admin (`<@77>`) | /round cancel | Success"
REFUSAL = "⛔ `/round cancel` refused for Admin (`<@77>`) — "
ACK = (
    "⏳ Cancelling round 3 in **Pro**. This message will be updated when it is done; if it takes "
    "longer, the log channel will say so. It begins with job #"
)
CHECKIN_AUDIT = (
    "\n  check-in, Pro, Round 3 (Silverstone):"
    "\n    accepted: <@101>"
    "\n    tentative: none"
    "\n    declined: none"
    "\n    no answer: <@102>"
)
#: The same, as the log channel shows it: each mention in code marks.
CHECKIN_LOGGED = CHECKIN_AUDIT.replace("<@101>", "`<@101>`").replace("<@102>", "`<@102>`")
UNARM_DISCARDED = (
    "Nothing was cancelled: round 3 in **Pro** stands as it was. Run `/round cancel` again."
)
SAVE_DISCARDED = (
    "Nothing was cancelled, but round 3 in **Pro** no longer has its timed work: run "
    "`/round cancel` again."
)
ALL_JOBS = ["unarm", "apply", "notify_checkin", "take_down_call", "notify_forecast",
            "notify_results", "post_calendar", "close"]
WIND_DOWN = "season.wind_down"
#: Every channel a cancellation in Pro posts in: its check-in, forecast, results and calendar.
_PRO_POSTS = (PRO_CH.checkin, PRO_CH.forecast, PRO_CH.results, PRO_CH.calendar)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _changes(league: Any, kind: str = ROUND_CANCEL_KIND) -> list[dict[str, Any]]:
    return [row for row in await cancellation_changes(league) if row["kind"] == kind]


async def _changes_of(league: Any, rid: int = R3) -> list[dict[str, Any]]:
    """Every cancellation of round *rid* asked of the queue."""
    return [row for row in await _changes(league)
            if json.loads(row["payload"]).get("round_id") == rid]


async def _change(league: Any, rid: int = R3) -> dict[str, Any]:
    """The one cancellation of round *rid* asked of the queue."""
    rows = await _changes_of(league, rid)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any, change: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return await step_rows(league.db_path, (change or await _change(league))["id"])


async def _names(league: Any) -> list[str]:
    return [job["name"] for job in await _jobs(league)]


async def _job(league: Any, name: str) -> dict[str, Any]:
    found = [job for job in await _jobs(league) if job["name"] == name]
    assert len(found) == 1, await _jobs(league)
    return found[0]


async def _run_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the cancellation's job called *name* is done."""
    for _ in range(100):
        if any(job["name"] == name and job["done_at"] for job in await _jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the cancellation's job {name} never finished: {await _jobs(league)}")


async def _done(league: Any) -> None:
    """Run the queue, and see the cancellation finished on it."""
    await run_queue(league.bot)
    assert (await _change(league))["state"] == "DONE"


async def _stopped_at(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


async def _status(league: Any, rid: int = R3) -> str:
    return (await league.rows("SELECT status FROM rounds WHERE id = ?", rid))[0]["status"]


async def _set_status(league: Any, status: str, rid: int = R3) -> None:
    await league.write("UPDATE rounds SET status = ? WHERE id = ?", status, rid)


async def _awaiting_results(league: Any, rid: int = R3) -> None:
    """Round *rid*'s race time has passed and it waits for its results."""
    await league.write(
        "UPDATE rounds SET status = 'AWAITING_RESULTS', scheduled_at = ? WHERE id = ?",
        (league.clock.now - timedelta(hours=2)).isoformat(), rid,
    )


async def _round_audits(league: Any) -> list[dict[str, Any]]:
    return await league.rows(
        "SELECT old_value, new_value FROM audit_entries WHERE change_type = 'round.status' "
        "AND division_id = ? ORDER BY id", PRO,
    )


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _success_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if SUCCESS in line]


def _refusal_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if line.startswith(REFUSAL)]


def _refused(league: Any, cid: int) -> None:
    """Channel *cid* refuses every send, as Discord refusing it would."""
    league.channel(cid).send_fails = http_error(discord.Forbidden, status=403,
                                                text="Missing Access")


def _not_notified(text: str) -> list[str]:
    """The bullets of the reply's "Not notified" section, in order."""
    assert "⚠️ **Not notified**" in text, text
    section = text.split("⚠️ **Not notified**", 1)[1]
    return [line.strip()[2:] for line in section.splitlines() if line.strip().startswith("• ")]


def _everywhere(name: str, new: Any) -> ExitStack:
    """Patch season_service's *name* with *new*, and the change type's own reference to it."""
    stack = ExitStack()
    for module_name in ("leaguebot.core.services.season_service",
                        "leaguebot.core.services.cancellation_changes"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _failing(name: str) -> ExitStack:
    """season_service's *name* raises, as a fault of the database would."""
    return _everywhere(name, AsyncMock(side_effect=RuntimeError("disk I/O error")))


async def _asked(league: Any, division: str = "Pro", number: int = 3, **kwargs: Any) -> Any:
    interaction = await cancel_round(league, division, number, **kwargs)
    assert not league.errors, league.errors
    return interaction


async def _stopped_blocker(league: Any) -> None:
    """Am's round 3 cancelled with Am's check-in channel deleted: the queue stops at its notice,
    and whatever is asked after it waits."""
    league.remove_channel(AM_CH.checkin)
    await _asked(league, "Am", 3)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"


async def _clear_blocker(league: Any) -> None:
    league.restore_channel(AM_CH.checkin)
    await retry_job(league.bot)


# ── The defects ─────────────────────────────────────────────────────────────────────


async def test_a_stop_after_the_round_is_cancelled_still_tells_the_modules_takes_its_call_down_and_reposts_its_calendar_on_restart(
    tmp_path,
):
    league = await ongoing_league(tmp_path, weather=True, results=True, attendance=True)
    await _asked(league)
    await _run_through(league, "apply")
    assert [job["name"] for job in await _jobs(league) if job["done_at"]] == ["unarm", "apply"]

    await league.restart()
    await run_queue(league.bot)

    checkin = league.texts(PRO_CH.checkin)
    assert len(checkin) == 1 and checkin[0].startswith("<@&801>\n📢 **Round 3 Cancelled: Pro**")
    assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    for cid in (PRO_CH.forecast, PRO_CH.results):
        assert len(league.texts(cid)) == 1
        assert league.channel(cid).send.await_args.kwargs.get("silent") is True
    calendar = league.texts(PRO_CH.calendar)
    assert len(calendar) == 1 and calendar[0].count("— Cancelled") == 1
    lines = _success_lines(league)
    assert len(lines) == 1 and CHECKIN_LOGGED in lines[0]


async def test_a_stop_after_the_timed_work_is_removed_cancels_the_round_on_restart(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    await _asked(league)
    await _run_through(league, "unarm")
    assert league.unarmed == [R3]
    assert await _status(league) == "NOT_RUN"

    await league.restart()
    await run_queue(league.bot)

    assert await _status(league) == "CANCELLED"
    assert await _round_audits(league) == [{"old_value": "NOT_RUN", "new_value": "CANCELLED"}]
    assert len(league.texts(PRO_CH.checkin)) == 1
    assert len(league.texts(PRO_CH.results)) == 1


async def test_cancelling_the_last_outstanding_round_winds_the_season_down_as_a_change_of_its_own_even_across_a_restart(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await league.write("UPDATE seasons SET stage = 'ONGOING_PLACEMENTS' WHERE id = ?", SEASON_ID)
    for number in (2, 4):
        await _set_status(league, "FINAL", round_id(PRO, number))
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", AM)
    await _asked(league)
    await _run_through(league, "apply")

    wind_downs = [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN]
    assert len(wind_downs) == 1
    assert (await league.season())["stage"] == "ONGOING_PLACEMENTS"

    await league.restart()
    await run_queue(league.bot)

    assert (await league.season())["stage"] == "PENDING_COMPLETION"
    assert all(row["state"] == "DONE" for row in await change_rows(league.db_path))


@pytest.mark.parametrize("status", [
    "NOT_RUN",
    "AWAITING_RESULTS",
])
async def test_the_round_s_audit_records_the_status_it_was_cancelled_from(tmp_path, status):
    league = await ongoing_league(tmp_path)
    if status == "AWAITING_RESULTS":
        await _awaiting_results(league)
    await _asked(league)

    await _done(league)

    assert await _round_audits(league) == [{"old_value": status, "new_value": "CANCELLED"}]


async def test_a_notice_discord_refuses_stops_the_queue_and_once_discarded_is_named_not_notified(
    tmp_path,
):
    league = await ongoing_league(tmp_path, weather=True)
    _refused(league, PRO_CH.forecast)
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_forecast"
    assert league.texts(PRO_CH.calendar) == []
    [stop] = [line for line in _log_lines(league) if "The queue is stopped at job #" in line]
    assert "posting the forecast note for **Pro**" in stop
    assert "`/round cancel`" in stop
    assert "Admin (`<@77>`)" in stop
    assert "failed (Forbidden)" in stop

    await discard_job(league.bot)

    named = "**Pro** — forecast channel: the notice could not be posted, and a league admin discarded it"
    assert CANCELLED in reply(interaction)
    assert named in _not_notified(reply(interaction))
    assert len(league.texts(PRO_CH.results)) == 1
    assert len(league.texts(PRO_CH.calendar)) == 1
    lines = _success_lines(league)
    assert len(lines) == 1 and f"\n  not notified: {named}" in lines[0]


async def test_a_notice_channel_deleted_stops_the_queue_and_goes_through_once_it_is_set_again(
    tmp_path,
):
    league = await ongoing_league(tmp_path, attendance=True)
    league.remove_channel(PRO_CH.checkin)
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"

    league.restore_channel(PRO_CH.checkin)
    await retry_job(league.bot)

    assert len(league.texts(PRO_CH.checkin)) == 1
    assert CANCELLED in reply(interaction)
    assert "Not notified" not in reply(interaction)
    assert "not notified" not in _success_lines(league)[0]


@pytest.mark.parametrize("cleared", ["retried", "discarded"])
async def test_a_call_message_discord_will_not_delete_stops_the_queue_keeping_the_call_s_record(
    tmp_path, cleared,
):
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1

    if cleared == "retried":
        league.undeletable.clear()
        await retry_job(league.bot)
        assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
        assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
        assert "Not notified" not in reply(interaction)
    else:
        await discard_job(league.bot)
        named = ("**Pro** — check-in call: 1 message(s) could not be deleted and must be removed "
                 "by hand (ids 7002)")
        assert named in _not_notified(reply(interaction))
        assert f"\n  not notified: {named}" in _success_lines(league)[0]
    assert CHECKIN_LOGGED in _success_lines(league)[0]


async def test_a_discarded_check_in_notice_still_has_the_call_taken_down(tmp_path):
    """Discord refuses the check-in notice for Pro's round 3, so the queue stops at it, and a
    league admin discards it. The call still comes down whether or not the notice could be posted
    (attendance spec, Cancellation): messages 7001-7003 are deleted and the call's record is gone,
    its check-in is still written beneath the success line, and the discarded notice is named."""
    league = await ongoing_league(tmp_path, attendance=True)
    _refused(league, PRO_CH.checkin)
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"
    await discard_job(league.bot)

    assert not set(CALL_MESSAGES) & set(league.channel(PRO_CH.checkin).messages)
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    named = "**Pro** — check-in channel: the notice could not be posted, and a league admin discarded it"
    assert CANCELLED in reply(interaction)
    assert named in _not_notified(reply(interaction))
    [line] = _success_lines(league)
    assert CHECKIN_LOGGED in line
    assert f"\n  not notified: {named}" in line


async def test_a_calendar_discord_refuses_stops_the_queue_and_puts_nothing_on_the_old_retry_queue(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    _refused(league, PRO_CH.calendar)
    interaction = await _asked(league)

    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_calendar"
    assert await league.rows("SELECT * FROM pending_messages") == []
    assert _success_lines(league) == []

    league.channel(PRO_CH.calendar).send_fails = None
    await retry_job(league.bot)
    assert len(league.texts(PRO_CH.calendar)) == 1
    assert CANCELLED in reply(interaction)


async def test_the_admin_is_told_at_once_naming_the_job_and_the_reply_is_updated_with_the_outcome(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    interaction = await _asked(league)

    assert acknowledgement(interaction).startswith(ACK)
    interaction.response.defer.assert_not_awaited()
    assert await _status(league) == "NOT_RUN"

    await _done(league)

    assert CANCELLED in reply(interaction)


async def _first_stopped_at_apply(league: Any) -> int:
    await _asked(league)
    with _failing("cancel_round_on"):
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"
    return (await _job(league, "apply"))["id"]


async def _first_waiting_behind_a_blocker(league: Any) -> int:
    await _stopped_blocker(league)
    await _asked(league)
    await _asked(league, "Pro", 4)
    return (await _job(league, "unarm"))["id"]


@pytest.mark.parametrize("first", [
    pytest.param(_first_waiting_behind_a_blocker, id="waiting behind a stopped blocker"),
    pytest.param(_first_stopped_at_apply, id="stopped at its save"),
])
async def test_a_second_cancel_of_the_round_is_refused_at_once_naming_the_job(tmp_path, first):
    league = await ongoing_league(tmp_path, attendance=True)
    job = await first(league)
    asked = len(await change_rows(league.db_path))

    second = await _asked(league)

    assert reply(second) == (
        f"⏳ Round 3 in **Pro** is already being cancelled (job #{job}). If it has stopped, "
        "press Retry or Discard on its notice in the log channel."
    )
    assert len(_refusal_lines(league)) == 1
    assert len(await change_rows(league.db_path)) == asked


async def test_a_round_cancel_while_its_division_s_cancellation_is_in_hand_is_refused_naming_the_job(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    await cancel_division(league, "Pro")
    with _failing("cancel_division_on"):
        await run_queue(league.bot)
    assert await _stopped_at(league) == "apply"
    division_change = (await _changes(league, DIVISION_CANCEL_KIND))[0]
    job = [j for j in await _jobs(league, division_change) if j["name"] == "apply"][0]["id"]

    interaction = await _asked(league)

    assert reply(interaction).startswith(
        f"⏳ **Pro** is being cancelled (job #{job}), and its rounds with it."
    )
    assert len(_refusal_lines(league)) == 1
    assert await _changes_of(league) == []
    assert await _status(league) == "NOT_RUN"


# ── The checks ──────────────────────────────────────────────────────────────────────


async def _no_season(league: Any) -> None:
    await league.write("UPDATE seasons SET status = 'SETUP', stage = 'PLACEMENTS' WHERE id = ?",
                       SEASON_ID)


async def _archived(league: Any) -> None:
    league.bot.season_service.assert_season_mutable = AsyncMock(
        side_effect=SeasonImmutableError("Season 3 is archived and cannot be modified.")
    )


async def _already_cancelled(league: Any) -> None:
    await _set_status(league, "CANCELLED")


async def _submission_open(league: Any) -> None:
    await _awaiting_results(league)
    await open_submission(league, R3)


async def _nothing(_league: Any) -> None:
    return None


#: Each refusal at the press: what sets it up, the division and round typed, the confirmation
#: word, and today's reply.
_PRESS_REFUSALS = {
    "confirm": (_nothing, "Pro", 3, "confirm",
                "❌ Type exactly `CONFIRM` in the `confirm` field to proceed."),
    "no season being raced": (_no_season, "Pro", 3, "CONFIRM",
                              "❌ `/round cancel` is available only while the season is ongoing."),
    "archived": (_archived, "Pro", 3, "CONFIRM",
                 "❌ This season is archived (COMPLETED) and cannot be modified."),
    "unknown division": (_nothing, "Nope", 3, "CONFIRM", "❌ Division `Nope` not found."),
    "unknown round": (_nothing, "Pro", 9, "CONFIRM", "❌ Round 9 not found in division `Pro`."),
    "already cancelled": (_already_cancelled, "pro", 3, "CONFIRM",
                          "❌ Round 3 in **Pro** is already cancelled."),
    "results entered": (_nothing, "Pro", 2, "CONFIRM",
                        "❌ Cannot cancel Round 2 — its results have already been entered, and "
                        "the drivers' reports and appeals depend on it."),
    "submission open": (_submission_open, "Pro", 3, "CONFIRM",
                        "❌ Cannot cancel Round 3 — a results submission channel is currently "
                        "open. Close the submission first."),
}


@pytest.mark.parametrize("case", sorted(_PRESS_REFUSALS))
async def test_the_command_is_refused_at_once_in_today_s_words(tmp_path, case):
    setup, division, number, word, text = _PRESS_REFUSALS[case]
    league = await ongoing_league(tmp_path, attendance=True, weather=True)
    await setup(league)
    before = await league.rows("SELECT id, status FROM rounds ORDER BY id")

    interaction = await _asked(league, division, number, confirm=word)

    assert reply(interaction) == text
    assert len(_refusal_lines(league)) == 1
    assert await cancellation_changes(league) == []
    await run_queue(league.bot)
    assert league.unarmed == []
    assert await league.rows("SELECT id, status FROM rounds ORDER BY id") == before
    assert [league.texts(cid) for cid in _PRO_POSTS] == [[], [], [], []]


@pytest.mark.parametrize("status", sorted(s.value for s in RoundStatus))
async def test_every_cancellable_state_is_cancelled_and_no_other(tmp_path, status):
    league = await ongoing_league(tmp_path)
    if status == "AWAITING_RESULTS":
        await _awaiting_results(league)
    else:
        await _set_status(league, status)

    await _asked(league)
    await run_queue(league.bot)

    if status in ("NOT_RUN", "AWAITING_RESULTS"):
        assert await _status(league) == "CANCELLED"
    else:
        assert await _status(league) == status
        assert len(_refusal_lines(league)) == 1


async def _results_entered(league: Any) -> None:
    await _set_status(league, "AWAITING_REPORT_VERDICTS")


async def _pending_completion(league: Any) -> None:
    await league.write("UPDATE seasons SET stage = 'PENDING_COMPLETION' WHERE id = ?", SEASON_ID)


#: Each way the round or its season can change while its cancellation waits, and the reply's
#: words for it when the cancellation comes up to run.
_RUN_REFUSALS = {
    "results entered": (_results_entered,
                        "❌ Cannot cancel Round 3 — its results have already been entered, and "
                        "the drivers' reports and appeals depend on it."),
    "a submission opened": (_submission_open,
                            "❌ Cannot cancel Round 3 — a results submission channel is "
                            "currently open. Close the submission first."),
    "the round written cancelled": (_already_cancelled,
                                    "❌ Round 3 in **Pro** is already cancelled."),
    "the season moved to pending completion": (
        _pending_completion, "❌ `/round cancel` is available only while the season is ongoing."
    ),
}


@pytest.mark.parametrize("case", sorted(_RUN_REFUSALS))
async def test_a_refusal_found_when_the_cancel_runs_updates_the_reply_and_the_queue_goes_on(
    tmp_path, case,
):
    """Pro's round 3 cancellation waits behind a stopped one and something changes meanwhile; it
    is refused when it runs, and Am's round 4 cancellation behind it still runs. Where the season
    itself moved to Pending completion, Am's round 4 is refused for the same reason."""
    change, text = _RUN_REFUSALS[case]
    season_moved_on = case == "the season moved to pending completion"
    league = await ongoing_league(tmp_path, attendance=True)
    await _stopped_blocker(league)
    interaction = await _asked(league)
    await _asked(league, "Am", 4)
    await change(league)

    await _clear_blocker(league)

    assert text in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert len(_refusal_lines(league)) == (2 if season_moved_on else 1)
    assert R3 not in league.unarmed
    assert (round_id(AM, 4) in league.unarmed) is not season_moved_on
    assert await _stopped_at(league) is None
    states = {row["dedup_key"]: row["state"] for row in await change_rows(league.db_path)}
    assert states.pop(f"{ROUND_CANCEL_KIND}:{R3}") == "REFUSED"
    if season_moved_on:
        assert states.pop(f"{ROUND_CANCEL_KIND}:{round_id(AM, 4)}") == "REFUSED"
    assert set(states.values()) == {"DONE"}


async def test_a_cancel_waiting_behind_its_division_s_cancellation_is_refused_as_already_cancelled_when_it_runs(
    tmp_path, monkeypatch,
):
    league = await ongoing_league(tmp_path, attendance=True)
    await _stopped_blocker(league)
    await cancel_division(league, "Pro")
    from leaguebot.core.services import cancellation_changes as module

    # Asked as though the in-hand refusal had not seen the division's cancellation.
    with monkeypatch.context() as patched:
        patched.setattr(module, "cancellation_in_hand", AsyncMock(return_value=None))
        interaction = await _asked(league)
    assert len(await _changes_of(league)) == 1

    await _clear_blocker(league)

    assert await _status(league) == "CANCELLED"
    assert "❌ Round 3 in **Pro** is already cancelled." in reply(interaction)
    assert len(_refusal_lines(league)) == 1
    # Am's round 3, the blocker, has its own success line; Pro's round 3 has none.
    assert [line for line in _success_lines(league) if "division: Pro" in line] == []


async def _renumbered(league: Any) -> None:
    """Pro's round 2's date moved past round 4's, and the division renumbered by date, as an
    amended date renumbers it: the round asked for as round 3 is now round 2."""
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       (league.clock.now + timedelta(days=200)).isoformat(), round_id(PRO, 2))
    await league.bot.season_service.renumber_rounds(PRO)
    assert (await league.rows("SELECT round_number FROM rounds WHERE id = ?", R3)) == [
        {"round_number": 2}
    ]


def _outcome(interaction: Any) -> str:
    """What the acknowledgement was last updated to say."""
    call = interaction.edit_original_response.await_args
    return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))


@pytest.mark.parametrize("submission", [
    pytest.param(False, id="cancelled"),
    pytest.param(True, id="refused, its submission opened", marks=pytest.mark.xfail(
        strict=True,
        reason="#439: the refusal when the cancellation runs names the round by its number at "
               "the press",
    )),
])
async def test_a_round_renumbered_while_its_cancellation_waits_is_announced_by_its_number_when_it_runs(
    tmp_path, submission,
):
    league = await ongoing_league(tmp_path, attendance=True)
    await _stopped_blocker(league)
    interaction = await _asked(league)
    await _renumbered(league)
    if submission:
        await open_submission(league, R3)

    await _clear_blocker(league)

    outcome = _outcome(interaction)
    if submission:
        assert outcome == ("❌ Cannot cancel Round 2 — a results submission channel is currently "
                           "open. Close the submission first.")
        assert await _status(league) == "NOT_RUN"
        refusals = _refusal_lines(league)
        assert len(refusals) == 1 and "Round 2" in refusals[0] and "Round 3" not in refusals[0]
        return
    assert outcome == "✅ Round **2** in **Pro** cancelled."
    checkin = league.texts(PRO_CH.checkin)
    assert len(checkin) == 1 and checkin[0].startswith("<@&801>\n📢 **Round 2 Cancelled: Pro**")
    assert "Round 3" not in checkin[0]
    lines = [line for line in _success_lines(league) if "division: Pro" in line]
    assert len(lines) == 1
    assert "\n  round: 2" in lines[0] and "round: 3" not in lines[0]
    assert "Round 3" not in lines[0]


async def test_the_save_refuses_writing_nothing_where_the_round_moved_on_after_the_check(
    tmp_path,
):
    league = await ongoing_league(tmp_path)
    interaction = await _asked(league)
    await _run_through(league, "unarm")
    await _results_entered(league)

    await run_queue(league.bot)

    assert ("❌ Cannot cancel Round 3 — its results have already been entered, and the drivers' "
            "reports and appeals depend on it.") in reply(interaction)
    assert await _status(league) == "AWAITING_REPORT_VERDICTS"
    assert await _round_audits(league) == []
    assert len(_refusal_lines(league)) == 1
    assert _success_lines(league) == []
    assert league.texts(PRO_CH.calendar) == []


# ── What it does ────────────────────────────────────────────────────────────────────


async def test_the_timed_work_is_removed_before_the_round_is_recorded_cancelled(tmp_path):
    league = await ongoing_league(tmp_path)
    seen: list[str] = []

    def _unarm(rid: int) -> None:
        with sqlite3.connect(league.db_path) as db:
            seen.append(db.execute("SELECT status FROM rounds WHERE id = ?", (rid,)).fetchone()[0])
        league.unarmed.append(rid)

    league.bot.scheduler_service.cancel_round = MagicMock(side_effect=_unarm)
    await _asked(league)

    await _done(league)

    assert (await _names(league))[:2] == ["unarm", "apply"]
    assert league.unarmed == [R3] and seen == ["NOT_RUN"]
    assert await _status(league) == "CANCELLED"


#: Each set of modules on: the channels told, in order, after the round is cancelled.
_MODULES = {
    "every module": ({"attendance": True, "weather": True, "results": True},
                     [PRO_CH.checkin, PRO_CH.forecast, PRO_CH.results, PRO_CH.calendar]),
    "attendance alone": ({"attendance": True, "results": False},
                         [PRO_CH.checkin, PRO_CH.calendar]),
    "weather alone": ({"weather": True, "results": False}, [PRO_CH.forecast, PRO_CH.calendar]),
    "results alone": ({}, [PRO_CH.results, PRO_CH.calendar]),
    "none": ({"results": False}, [PRO_CH.calendar]),
}


@pytest.mark.parametrize("modules", sorted(_MODULES))
async def test_each_enabled_module_says_its_piece_in_its_own_channel_as_a_job_of_its_own(
    tmp_path, modules,
):
    switched, told = _MODULES[modules]
    league = await ongoing_league(tmp_path, **switched)
    await _asked(league)

    await _done(league)

    assert await _names(league) == ALL_JOBS
    dropped = {job["name"] for job in await _jobs(league)
               if (job["result"] or {}).get("dropped")}
    on = {"attendance": switched.get("attendance", False),
          "weather": switched.get("weather", False),
          "results": switched.get("results", True)}
    assert dropped == ({"notify_checkin", "take_down_call"} if not on["attendance"] else set()) \
        | ({"notify_forecast"} if not on["weather"] else set()) \
        | ({"notify_results"} if not on["results"] else set())
    sent = [cid for kind, cid, _mid in league.events if kind == "send"]
    assert sent == told


async def test_a_channel_never_set_is_named_not_notified_and_stops_nothing(tmp_path):
    league = await ongoing_league(tmp_path, weather=True)
    await league.write("UPDATE divisions SET forecast_channel_id = NULL WHERE id = ?", PRO)
    interaction = await _asked(league)

    await _done(league)

    named = "**Pro** — forecast channel: no channel is set"
    assert (await _job(league, "notify_forecast"))["result"] == {"unset": True}
    assert named in _not_notified(reply(interaction))
    assert f"\n  not notified: {named}" in _success_lines(league)[0]
    assert len(league.texts(PRO_CH.calendar)) == 1


async def test_the_call_comes_down_after_the_check_in_notice_and_its_answers_are_kept(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    await _asked(league)

    await _done(league)

    events = [(kind, cid, mid) for kind, cid, mid in league.events if cid == PRO_CH.checkin]
    assert events[0][0] == "send"
    assert sorted(mid for kind, _cid, mid in events[1:] if kind == "delete") == list(CALL_MESSAGES)
    answers = await league.rows(
        "SELECT driver_profile_id, rsvp_status FROM driver_round_attendance WHERE round_id = ? "
        "ORDER BY driver_profile_id", R3,
    )
    assert [row["rsvp_status"] for row in answers] == ["ACCEPTED", "NO_RSVP"]
    taken = (await _job(league, "take_down_call"))["result"]
    assert taken["audit"] == CHECKIN_AUDIT
    assert taken["taken_down"]


async def test_the_check_in_is_written_beneath_the_success_line(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    await _asked(league)

    await run_queue(league.bot)

    lines = _success_lines(league)
    assert len(lines) == 1
    entry = lines[0].split("\n――", 1)[0]
    assert entry == SUCCESS + "\n  division: Pro\n  round: 3" + CHECKIN_LOGGED


async def test_a_calendar_never_posted_is_left_alone(tmp_path):
    league = await ongoing_league(tmp_path)
    await league.write("UPDATE divisions SET calendar_message_id = NULL WHERE id = ?", PRO)
    interaction = await _asked(league)

    await _done(league)

    assert league.texts(PRO_CH.calendar) == []
    assert CALENDAR_MESSAGE in league.channel(PRO_CH.calendar).messages
    assert "calendar" not in reply(interaction)


async def test_the_calendar_is_posted_again_with_the_round_shown_cancelled(tmp_path):
    league = await ongoing_league(tmp_path)
    seasons = league.bot.season_service
    seasons.get_divisions = AsyncMock(side_effect=seasons.get_divisions)
    seasons.get_division_rounds = AsyncMock(side_effect=seasons.get_division_rounds)
    await _asked(league)
    seasons.get_divisions.reset_mock()
    seasons.get_division_rounds.reset_mock()

    await _done(league)

    seasons.get_divisions.assert_awaited_with(SEASON_ID)
    seasons.get_division_rounds.assert_awaited_with(PRO)
    calendar = league.texts(PRO_CH.calendar)
    assert len(calendar) == 1
    struck = [line for line in calendar[0].splitlines() if "— Cancelled" in line]
    assert len(struck) == 1 and "3" in struck[0]
    assert CALENDAR_MESSAGE not in league.channel(PRO_CH.calendar).messages
    held = await league.rows("SELECT calendar_message_id FROM divisions WHERE id = ?", PRO)
    assert held[0]["calendar_message_id"] != str(CALENDAR_MESSAGE)


async def test_a_retried_calendar_is_posted_as_text(tmp_path, monkeypatch):
    from leaguebot.core.services import calendar_post_service

    league = await ongoing_league(tmp_path, images=True)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(return_value=True)
    league.channel(PRO_CH.calendar).send_fails = http_error(status=503,
                                                            text="Service Unavailable")
    draw = AsyncMock(return_value=SimpleNamespace(problem=None, notices=[], png_paths=[]))
    monkeypatch.setattr(calendar_post_service, "render_calendar_image", draw)
    await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_calendar"
    assert draw.await_count == 1

    league.channel(PRO_CH.calendar).send_fails = None
    await retry_job(league.bot)

    assert len(league.texts(PRO_CH.calendar)) == 1
    assert league.channel(PRO_CH.calendar).files[-1] is None
    assert draw.await_count == 1, "the retry reached for the picture"


async def test_a_calendar_that_falls_back_to_text_is_named(tmp_path, monkeypatch):
    from leaguebot.core.services import calendar_post_service

    league = await ongoing_league(tmp_path, images=True)
    league.bot.image_config_service.is_aspect_enabled = AsyncMock(
        side_effect=lambda aspect: aspect == "calendar"
    )
    monkeypatch.setattr(calendar_post_service, "render_calendar_image",
                        AsyncMock(side_effect=RuntimeError("the template has no rows")))
    interaction = await _asked(league)

    await _done(league)

    named = ("**Pro** — calendar: posted as text, as the picture could not be drawn (the "
             "template has no rows)")
    assert named in _not_notified(reply(interaction))
    assert f"\n  not notified: {named}" in _success_lines(league)[0]
    result = (await _job(league, "post_calendar"))["result"]
    assert result["fell_back"] and result["problem"] == "the template has no rows"


async def test_a_discarded_save_cancels_nothing_and_says_the_timed_work_is_gone(tmp_path):
    league = await ongoing_league(tmp_path, attendance=True)
    interaction = await _asked(league)
    with _failing("cancel_round_on"):
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        await discard_job(league.bot)

    assert SAVE_DISCARDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _status(league) == "NOT_RUN"
    assert league.unarmed == [R3]
    assert _success_lines(league) == []
    assert [league.texts(cid) for cid in _PRO_POSTS] == [[], [], [], []]


async def test_a_discarded_unarming_cancels_nothing(tmp_path):
    league = await ongoing_league(tmp_path)
    league.bot.scheduler_service.cancel_round = MagicMock(
        side_effect=RuntimeError("the job store is locked")
    )
    interaction = await _asked(league)

    await run_queue(league.bot)
    assert await _stopped_at(league) == "unarm"
    [stop] = [line for line in _log_lines(league) if "The queue is stopped at job #" in line]
    assert "removing the timed work of round 3 in **Pro**" in stop
    assert "`/round cancel`" in stop
    await discard_job(league.bot)

    assert UNARM_DISCARDED in reply(interaction)
    assert CANCELLED not in reply(interaction)
    assert await _status(league) == "NOT_RUN"
    assert await _round_audits(league) == []
    assert _success_lines(league) == []


async def test_a_module_turned_off_before_its_notice_drops_the_notice(tmp_path):
    league = await ongoing_league(tmp_path, weather=True)
    interaction = await _asked(league)
    await _run_through(league, "apply")
    await league.switch("weather", False)

    await _done(league)

    assert league.texts(PRO_CH.forecast) == []
    assert (await _job(league, "notify_forecast"))["result"] == {"dropped": True}
    assert "forecast" not in reply(interaction)
    assert len(league.texts(PRO_CH.results)) == 1


async def test_one_success_line_records_the_cancellation_after_the_last_job(tmp_path):
    league = await ongoing_league(tmp_path)
    _refused(league, PRO_CH.calendar)
    await _asked(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_calendar"
    assert _success_lines(league) == []

    league.channel(PRO_CH.calendar).send_fails = None
    await retry_job(league.bot)

    assert len(_success_lines(league)) == 1
    jobs = await _jobs(league)
    assert jobs[-1]["name"] == "close" and all(job["done_at"] for job in jobs)


@pytest.mark.parametrize("finishes", [
    pytest.param(True, id="the round finishes its division"),
    pytest.param(False, id="the division goes on"),
])
async def test_the_wind_down_is_asked_only_where_the_round_finished_its_division(
    tmp_path, finishes,
):
    league = await ongoing_league(tmp_path)
    await league.write("UPDATE seasons SET stage = 'ONGOING_PLACEMENTS' WHERE id = ?", SEASON_ID)
    if finishes:
        for number in (2, 4):
            await _set_status(league, "FINAL", round_id(PRO, number))
    await _asked(league)

    await _run_through(league, "apply")

    wind_downs = [row for row in await change_rows(league.db_path) if row["kind"] == WIND_DOWN]
    assert len(wind_downs) == (1 if finishes else 0)


async def test_a_wind_down_that_stops_stops_at_its_own_job_and_the_cancellation_reports_success(
    tmp_path,
):
    from leaguebot.core.services import season_lifecycle_service

    league = await ongoing_league(tmp_path)
    await league.write("UPDATE seasons SET stage = 'ONGOING_PLACEMENTS' WHERE id = ?", SEASON_ID)
    for number in (2, 4):
        await _set_status(league, "FINAL", round_id(PRO, number))
    await league.write("UPDATE rounds SET status = 'FINAL' WHERE division_id = ?", AM)
    await league.write("UPDATE divisions SET status = 'FINISHED' WHERE id = ?", AM)
    interaction = await _asked(league)

    with patch.object(season_lifecycle_service, "wind_down_ongoing",
                      AsyncMock(side_effect=RuntimeError("the signup window would not close"))):
        await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "wind_down"
    assert stopped["change_id"] != (await _change(league))["id"]
    assert (await _change(league))["state"] == "DONE"
    assert CANCELLED in reply(interaction)
    assert "pending completion" not in reply(interaction)
    assert len(_success_lines(league)) == 1
    assert "not done" not in _success_lines(league)[0]


async def test_a_cancelled_round_keeps_its_number(tmp_path):
    league = await ongoing_league(tmp_path)
    await _asked(league)

    await _done(league)

    rows = await league.rows(
        "SELECT id, round_number, status FROM rounds WHERE division_id = ? ORDER BY id", PRO
    )
    assert [(row["round_number"], row["status"]) for row in rows] == [
        (1, "FINAL"), (2, "AWAITING_REPORT_VERDICTS"), (3, "CANCELLED"), (4, "NOT_RUN"),
    ]
