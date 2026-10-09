"""Amending a round on the change queue (#439, slice 4b, amendment A).

`/round amend` offers its confirmation as today. Confirm reads the round again and asks the change
queue for the amendment, which is judged when it is asked and again when it runs, in today's
Confirm words. Its jobs: the amendment judged (`judge`), the round's timed work removed (`unarm`),
one save writing the round's fields, its record, the weather and check-in state it withdraws and
the division's renumbering together (`apply`), the round's timed work armed again against the
round as it then stands (`arm`, which a league admin cannot discard), then the check-in call taken
down and posted again, each withdrawn forecast deleted, the invalidation notice and each phase due
at once, each a job of its own, and `close`, which reads the round list for the reply.

Every test drives the real cog and the real queue through `tests/support/season_league.py`'s
`ongoing_league`, with "now" pinned. Pro's round 3 (id 213) is NOT_RUN 90 days out, its check-in
call standing; round 4 (id 214) 120 days out. The weather phases and the call's repost are
recorded rather than run (`phases`, `reposts`), patched before the league is built so that the
builder's hooks reach the recorders however they bind them. The change type is unbuilt until the
build, so each test is marked to fail until then: today Confirm amends on the spot.
"""
from __future__ import annotations

import importlib
import json
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection

from tests.support.change_queue import (
    discard_job,
    http_error,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    AM,
    CALL_MESSAGES,
    DIVISIONS,
    FERRARI,
    MAX,
    PRO,
    amend_round,
    amendment_changes,
    cancel_round,
    cancellation_changes,
    ongoing_league,
    posted_forecast,
    profile_id,
    round_id,
)

PRO_CH, AM_CH = DIVISIONS[PRO][3], DIVISIONS[AM][3]
R3, R4 = round_id(PRO, 3), round_id(PRO, 4)
HORIZONS = (5, 2, 2)
AMENDED = "✅ Round amended successfully."
ACK = (
    "⏳ Amending round 3 in **Pro**. This message will be updated when it is done; if it takes "
    "longer, the log channel will say so. It begins with job #"
)
SUCCESS = "| /round amend | Success"
NOTHING_AMENDED = (
    "Nothing was amended: round 3 in **Pro** stands as it was. Run `/round amend` again."
)
SAVE_DISCARDED = (
    "Nothing was amended: round 3 in **Pro** stands as it was, its timed work armed again. Run "
    "`/round amend` again."
)
CANNOT_DISCARD = (
    "⛔ This job can't be discarded: without it round 3 in **Pro** would never run. Fix what "
    "stopped it and press **Retry**."
)
NO_LONGER = "⛔ This round can no longer be amended:\n• "
NOTHING_CHANGED = "\n\n**Nothing has been changed.** Run `/round amend` again to start over."
NOT_DONE = "⚠️ **Not done**"
#: The message a check-in call posted by the `reposts` recorder stands as.
LATE_CALL = 7101


# ── Recorders, patched before the league is built ──────────────────────────────────


@pytest.fixture
def phases(monkeypatch):
    """Weather's three phase runners, recording `(phase, round id)` in `ran`; a phase in
    `failing` raises its fault instead, as a fault of the bot's own."""
    record = SimpleNamespace(ran=[], failing={})
    for number in (1, 2, 3):
        module = importlib.import_module(f"leaguebot.weather.services.phase{number}_service")

        async def _run(rid: int, *_args: Any, _number: int = number, **_kwargs: Any) -> None:
            if _number in record.failing:
                raise record.failing[_number]
            record.ran.append((_number, rid))

        monkeypatch.setattr(module, f"run_phase{number}", _run)
    return record


@pytest.fixture
def reposts(monkeypatch):
    """Attendance's posts of a check-in call, recording `(round id, division id)` in `posted`,
    raising `fault` instead where it is set: its repost (`repost_rsvp_call`), and its first post
    (`run_rsvp_notice`), which, as the real one does, posts nothing where a call stands for the
    round and records the call it posts as standing."""
    from leaguebot.attendance.services import rsvp_service

    record = SimpleNamespace(posted=[], fault=None)

    async def _repost(rid: int, division_id: int, *_args: Any, **_kwargs: Any) -> None:
        if record.fault is not None:
            raise record.fault
        record.posted.append((rid, division_id))

    async def _post(rid: int, bot: Any, *_args: Any, **_kwargs: Any) -> None:
        async with get_connection(bot.db_path) as db:
            cursor = await db.execute(
                "SELECT r.division_id, c.rsvp_channel_id FROM rounds r "
                "JOIN attendance_division_config c ON c.division_id = r.division_id "
                "WHERE r.id = ?",
                (rid,),
            )
            found = await cursor.fetchone()
            cursor = await db.execute(
                "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (rid,)
            )
            standing = await cursor.fetchone() is not None
        if standing:
            return
        if record.fault is not None:
            raise record.fault
        async with get_connection(bot.db_path) as db:
            await db.execute(
                "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, "
                "channel_id, posted_at) VALUES (?, ?, ?, ?, ?)",
                (rid, found["division_id"], str(LATE_CALL), str(found["rsvp_channel_id"]),
                 "2026-01-01T00:00:00"),
            )
            await db.commit()
        record.posted.append((rid, found["division_id"]))

    monkeypatch.setattr(rsvp_service, "repost_rsvp_call", _repost)
    monkeypatch.setattr(rsvp_service, "run_rsvp_notice", _post)
    return record


@pytest.fixture
def deadlines(monkeypatch):
    """Attendance's check-in deadline (the reserves' distribution), recording the round ids it
    was run for in `ran`."""
    from leaguebot.attendance.services import rsvp_service

    record = SimpleNamespace(ran=[])

    async def _run(rid: int, *_args: Any, **_kwargs: Any) -> None:
        record.ran.append(rid)

    monkeypatch.setattr(rsvp_service, "run_rsvp_deadline", _run)
    return record


# ── Helpers ─────────────────────────────────────────────────────────────────────────


def _at(league: Any, **delta: float) -> str:
    """The moment *delta* from "now", as `/round amend` takes it."""
    return (league.clock.now + timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%S")


async def _moment(league: Any, rid: int = R3) -> datetime:
    [row] = await league.rows("SELECT scheduled_at FROM rounds WHERE id = ?", rid)
    moment = datetime.fromisoformat(row["scheduled_at"])
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _when(league: Any, **delta: float) -> datetime:
    return datetime.fromisoformat(_at(league, **delta)).replace(tzinfo=timezone.utc)


async def _numbers(league: Any) -> dict[int, int]:
    rows = await league.rows("SELECT id, round_number FROM rounds WHERE division_id = ?", PRO)
    return {row["id"]: row["round_number"] for row in rows}


async def _status(league: Any, rid: int = R3) -> str:
    return (await league.rows("SELECT status FROM rounds WHERE id = ?", rid))[0]["status"]


async def _set_status(league: Any, status: str, rid: int = R3) -> None:
    await league.write("UPDATE rounds SET status = ? WHERE id = ?", status, rid)


async def _change(league: Any) -> dict[str, Any]:
    """The one amendment asked of the queue."""
    rows = await amendment_changes(league)
    assert len(rows) == 1, rows
    return rows[0]


async def _jobs(league: Any) -> list[dict[str, Any]]:
    return await step_rows(league.db_path, (await _change(league))["id"])


async def _job(league: Any, name: str) -> dict[str, Any]:
    found = [job for job in await _jobs(league) if job["name"] == name]
    assert len(found) == 1, await _jobs(league)
    return found[0]


async def _ran(league: Any) -> list[str]:
    """The amendment's jobs that were carried out, in order: those a module turned off dropped
    left out."""
    return [job["name"] for job in await _jobs(league)
            if job["done_at"] and not (job["result"] or {}).get("dropped")]


async def _run_through(league: Any, name: str) -> None:
    """Run the queue a job at a time until the amendment's job called *name* is done."""
    for _ in range(100):
        if any(job["name"] == name and job["done_at"] for job in await _jobs(league)):
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"the amendment's job {name} never finished: {await _jobs(league)}")


async def _stopped_at(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


async def _amended(league: Any, number: int = 3, **fields: str) -> Any:
    """Run `/round amend` for Pro's round *number* and press its Confirm; gives the press."""
    press = await amend_round(league, "Pro", number, **fields)
    assert press is not None, "nothing was offered"
    assert not league.errors, league.errors
    return press


def _content(call: Any) -> str:
    return str(call.args[0]) if call.args else str(call.kwargs.get("content", ""))


def _acknowledged(press: Any) -> str:
    """What Confirm was first answered with: the deferred press's first follow-up."""
    calls = press.followup.send.await_args_list
    return _content(calls[0]) if calls else ""


def _outcome(press: Any) -> str:
    """What the acknowledgement was last updated to say."""
    edits = press.followup.send.return_value.edit.await_args_list
    return _content(edits[-1]) if edits else ""


def _said_to(interaction: Any) -> list[str]:
    calls = (interaction.response.send_message.await_args_list
             + interaction.followup.send.await_args_list)
    return [_content(call) for call in calls]


def _log_lines(league: Any) -> list[str]:
    return list(league.bot.log_channel.sent)


def _success_lines(league: Any) -> list[str]:
    return [line for line in _log_lines(league) if SUCCESS in line]


def _amend_refusals(league: Any) -> list[str]:
    return [line for line in _log_lines(league)
            if line.startswith("⛔") and "/round amend" in line]


def _everywhere(name: str, new: Any) -> ExitStack:
    """Patch season_service's *name* with *new*, and the change type's own reference to it."""
    stack = ExitStack()
    for module_name in ("leaguebot.core.services.season_service",
                        "leaguebot.core.services.round_amend_change"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, name):
            stack.enter_context(patch.object(module, name, new))
    return stack


def _renumbering_fails() -> ExitStack:
    return _everywhere("renumber_rounds_on",
                       AsyncMock(side_effect=RuntimeError("disk I/O error")))


def _blind_to_the_queue() -> ExitStack:
    """Every check reading the queue for a change in hand finds none, as two asks in the same
    instant leave each other unseen."""
    from leaguebot.core.services import change_queue

    real = change_queue.in_hand

    async def _nothing(*_args: Any, **_kwargs: Any) -> list[Any]:
        return []

    stack = ExitStack()
    for name, module in list(sys.modules.items()):
        if name.startswith("leaguebot.") and getattr(module, "in_hand", None) is real:
            stack.enter_context(patch.object(module, "in_hand", _nothing))
    return stack


def _unarming_fails(league: Any) -> None:
    league.bot.scheduler_service.cancel_round = MagicMock(
        side_effect=RuntimeError("job store unavailable")
    )


def _unarming_mended(league: Any) -> None:
    league.bot.scheduler_service.cancel_round = MagicMock(side_effect=league.unarmed.append)


def _judging_fails(league: Any) -> None:
    """The amendment's windows can be read once more, by the check as the change starts, and
    not again, so that the change stops at `judge`."""
    reading = league.bot.amendment_windows
    reads = {"n": 0}

    def _read() -> Any:
        reads["n"] += 1
        if reads["n"] >= 2:
            raise RuntimeError("database is locked")
        return reading()

    league.bot.amendment_windows = _read


async def _stopped_blocker(league: Any) -> None:
    """Am's round 3 cancelled with Am's check-in channel deleted: the queue stops at its notice,
    and whatever is asked after it waits."""
    league.remove_channel(AM_CH.checkin)
    await cancel_round(league, "Am", 3)
    assert not league.errors, league.errors
    await run_queue(league.bot)
    assert await _stopped_at(league) == "notify_checkin"


async def _clear_blocker(league: Any) -> None:
    league.restore_channel(AM_CH.checkin)
    await retry_job(league.bot)


# ── The defects ─────────────────────────────────────────────────────────────────────


async def test_a_stop_after_the_amendment_is_saved_arms_the_round_again_on_restart(tmp_path):
    """Pro's round 3 moved past round 4. The queue is stopped once the save is made; after a
    restart the round is armed against its new moment, the rounds stand renumbered and one
    success line records the amendment."""
    league = await ongoing_league(tmp_path)
    await _amended(league, scheduled_at=_at(league, days=150))
    await _run_through(league, "apply")
    assert league.armed == []

    await league.restart()
    await run_queue(league.bot)

    assert league.armed == [("results", [R3])]
    assert await _moment(league) == _when(league, days=150)
    assert (await _numbers(league))[R3] == 4 and (await _numbers(league))[R4] == 3
    assert len(_success_lines(league)) == 1


async def test_the_timed_work_is_removed_before_the_save_and_armed_after_it(tmp_path):
    """The save fails: the queue stops at it with round 3's timed work already removed and
    nothing armed; once the save goes through on Retry, the round is armed."""
    league = await ongoing_league(tmp_path)
    await _amended(league, scheduled_at=_at(league, days=150))

    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        assert league.unarmed == [R3]
        assert league.armed == []

    await retry_job(league.bot)

    assert league.unarmed == [R3]
    assert league.armed == [("results", [R3])]


async def test_the_rounds_are_renumbered_in_the_amendment_s_own_save(tmp_path):
    """Round 3 moved past round 4, and the renumbering fails: nothing of the amendment is
    written — not the moment, not its record, not the numbers — and the queue stops at the
    save."""
    league = await ongoing_league(tmp_path)
    before = await _moment(league)
    await _amended(league, scheduled_at=_at(league, days=150))

    with _renumbering_fails():
        await run_queue(league.bot)

        assert await _stopped_at(league) == "apply"
    assert await _moment(league) == before
    assert await _numbers(league) == {round_id(PRO, n): n for n in (1, 2, 3, 4)}
    assert await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'round.scheduled_at'"
    ) == []
    assert _success_lines(league) == []


async def test_the_success_line_is_saved_with_the_amendment(tmp_path):
    """The arming fails after the save, stopping the queue, and the bot restarts: the success
    line, saved with the amendment, stands once in the log channel, and once only after the
    arming goes through on Retry."""
    league = await ongoing_league(tmp_path)
    await _amended(league, scheduled_at=_at(league, days=91))
    league.arming_fails = RuntimeError("job store unavailable")
    await run_queue(league.bot)
    assert await _stopped_at(league) == "arm"

    await league.restart()
    await run_queue(league.bot)

    [line] = _success_lines(league)
    assert "scheduled_at:" in line
    league.arming_fails = None
    await retry_job(league.bot)
    assert len(_success_lines(league)) == 1


async def test_the_manager_is_told_at_once_naming_the_job_and_the_reply_is_updated_with_the_round_list(
    tmp_path,
):
    from leaguebot.core.cogs.season_cog import format_round_list

    league = await ongoing_league(tmp_path)
    press = await _amended(league, scheduled_at=_at(league, days=91))

    assert _acknowledged(press).startswith(ACK)
    assert _outcome(press) == ""

    await run_queue(league.bot)

    rounds = await league.bot.season_service.get_division_rounds(PRO)
    assert _outcome(press) == AMENDED + "\n\n" + format_round_list(rounds)


@pytest.mark.parametrize("cleared", ["retried", "discarded"])
async def test_a_withdrawn_forecast_discord_will_not_delete_stops_the_queue_keeping_its_record(
    tmp_path, cleared,
):
    """Weather on; round 3's phase 1 forecast (message 8001) was posted, and the round is moved
    91 days out, withdrawing it. Discord refuses to delete the message: the queue stops at its
    deletion with the forecast's record kept. Retried once Discord allows it, the message and its
    record go; discarded, the reply names it with its link, to delete by hand."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await posted_forecast(league, R3, 1, 8001)
    league.undeletable.add(8001)
    press = await _amended(league, scheduled_at=_at(league, days=91))

    await run_queue(league.bot)

    assert await _stopped_at(league) == "delete_forecast"
    forecasts = "SELECT * FROM forecast_messages WHERE round_id = ? AND phase_number = 1"
    assert len(await league.rows(forecasts, R3)) == 1
    if cleared == "retried":
        league.undeletable.clear()
        await retry_job(league.bot)
        assert 8001 not in league.channel(PRO_CH.forecast).messages
        assert await league.rows(forecasts, R3) == []
        assert NOT_DONE not in _outcome(press)
        return
    await discard_job(league.bot)
    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    named = ("**Pro** — forecast channel: the withdrawn Phase 1 forecast could not be deleted, "
             "and a league admin discarded it; delete it by hand (")
    [bullet] = [line for line in outcome.splitlines() if named in line]
    assert "8001" in bullet


async def test_an_invalidation_notice_discord_refuses_stops_the_queue(tmp_path):
    """Weather on; round 3's posted phase 1 forecast is withdrawn by moving the round, and
    Discord refuses the notice that the forecasts no longer stand: the queue stops at the
    notice, which nothing puts on the old retry queue. Discarded, the reply names it."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await posted_forecast(league, R3, 1, 8001)
    league.channel(PRO_CH.forecast).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )
    press = await _amended(league, scheduled_at=_at(league, days=91))

    await run_queue(league.bot)

    assert await _stopped_at(league) == "notify_invalidation"
    assert league.texts(PRO_CH.forecast) == []
    assert await league.rows(
        "SELECT * FROM pending_messages WHERE channel_id = ?", PRO_CH.forecast
    ) == []
    await discard_job(league.bot)
    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    assert ("**Pro** — forecast channel: the notice that its forecasts no longer stand could not "
            "be posted, and a league admin discarded it") in outcome


async def test_a_check_in_call_discord_will_not_delete_stops_the_queue_before_the_call_is_posted_again(
    tmp_path, reposts,
):
    """Attendance on; round 3's call stands (messages 7001 to 7003) and the round is brought
    forward to three days out, inside its call's window, so its call is posted again. Discord
    refuses to delete the last notice (7002): the queue stops at the take-down, the call's
    record kept, and no call is posted again."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    await _amended(league, scheduled_at=_at(league, days=3))

    await run_queue(league.bot)

    assert await _stopped_at(league) == "take_down_call"
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1
    assert CALL_MESSAGES[1] in league.channel(PRO_CH.checkin).messages
    assert reposts.posted == []


async def test_a_discarded_check_in_call_take_down_is_named_and_the_call_is_still_posted_again(
    tmp_path, reposts,
):
    """Attendance on; round 3's call stands (messages 7001 to 7003) and the round is brought
    forward to three days out, inside its call's window. Discord refuses to delete the last
    notice (7002) and a league admin discards the take-down: the amendment goes on, the call is
    posted again beside the message left standing, and the reply names 7002 to remove by hand."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    press = await _amended(league, scheduled_at=_at(league, days=3))
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert reposts.posted == [(R3, PRO)]
    assert CALL_MESSAGES[1] in league.channel(PRO_CH.checkin).messages
    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    assert (f"**Pro** — check-in call: 1 message(s) could not be deleted and must be removed by "
            f"hand (ids {CALL_MESSAGES[1]})") in outcome


@pytest.mark.parametrize("meanwhile", [
    "the check-in deadline passes",
    "results entered",
    "the round cancelled",
])
async def test_an_amendment_judged_again_when_it_runs_is_refused_in_today_s_words(
    tmp_path, meanwhile,
):
    """Attendance on; the queue is stopped at Am's round 3 cancellation. Pro's round 3 is moved
    to two hours and ten minutes out and confirmed, and waits; Am's round 4 is cancelled behind
    it. While they wait, the check-in deadline (two hours before the round) passes eleven minutes
    on, while the reply can still be updated, or round 3's results are entered, or it is
    cancelled. Once the queue goes on, the amendment is refused in today's Confirm words, nothing
    of it written and nothing unarmed, and Am's round 4 is cancelled."""
    reasons = {
        "the check-in deadline passes": (
            "The check-in deadline for that moment has already passed, so the round would have "
            "no check-in at all. Move it further out, or shorten the deadline."
        ),
        "results entered": (
            "This round's results have been entered, so it can no longer be amended. Drivers "
            "have reports and appeals to lodge against them."
        ),
        "the round cancelled": "This round has been cancelled and can no longer be amended.",
    }
    league = await ongoing_league(tmp_path, attendance=True)
    await _stopped_blocker(league)
    before = await _moment(league)
    press = await _amended(league, scheduled_at=_at(league, hours=2, minutes=10))
    behind = await cancel_round(league, "Am", 4)
    assert not league.errors, league.errors
    if meanwhile == "the check-in deadline passes":
        league.clock.advance(minutes=11)
    elif meanwhile == "results entered":
        await _set_status(league, "AWAITING_REPORT_VERDICTS")
    else:
        await _set_status(league, "CANCELLED")

    await _clear_blocker(league)

    assert _outcome(press) == NO_LONGER + reasons[meanwhile] + NOTHING_CHANGED
    assert (await _change(league))["state"] == "REFUSED"
    assert await _moment(league) == before
    assert R3 not in league.unarmed
    assert len(_amend_refusals(league)) == 1
    assert behind.edit_original_response.await_count >= 1
    am_4 = [row for row in await cancellation_changes(league)
            if json.loads(row["payload"]).get("round_id") == round_id(AM, 4)]
    assert [row["state"] for row in am_4] == ["DONE"]


async def test_a_judgement_that_stopped_and_is_retried_after_the_window_passed_is_refused_by_the_save(
    tmp_path,
):
    """Attendance on, its check-in deadline one hour before the round. Pro's round 3 is moved to
    two hours from now and confirmed; the check passes, and the judgement stops, its windows
    unreadable. Am's round 4 is cancelled behind it. The clock passes the new moment while the
    judgement stands stopped; the fault is mended and Retry is pressed. The save refuses in
    today's Confirm words, nothing of the amendment written, one refusal line, the round armed
    again as it stood, and Am's round 4 is cancelled."""
    league = await ongoing_league(tmp_path, attendance=True)
    await league.write("UPDATE attendance_config SET rsvp_deadline_hours = 1 WHERE id = 1")
    before = await _moment(league)
    reading = league.bot.amendment_windows
    await _amended(league, scheduled_at=_at(league, hours=2))
    _judging_fails(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "judge"
    await cancel_round(league, "Am", 4)
    assert not league.errors, league.errors

    league.clock.advance(hours=2, minutes=1)
    league.bot.amendment_windows = reading
    await retry_job(league.bot)

    refused = (
        NO_LONGER
        + "A round cannot be moved into the past. Its result submission would never open, and "
        "the round could never take results at all. Give it a moment still to come.\n• "
        "The check-in deadline for that moment has already passed, so the round would have no "
        "check-in at all. Move it further out, or shorten the deadline."
        + NOTHING_CHANGED
    )
    assert (await _job(league, "apply"))["result"]["refused"] == refused
    assert (await _change(league))["state"] == "DONE"
    assert len(_amend_refusals(league)) == 1
    assert await _moment(league) == before
    assert await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'round.scheduled_at'"
    ) == []
    assert _success_lines(league) == []
    assert league.unarmed.count(R3) == 1
    assert league.armed == [("results", [R3]), ("attendance", [R3])]
    assert await stopped_job(league.db_path) is None
    assert await _status(league, round_id(AM, 4)) == "CANCELLED"
    am_4 = [row for row in await cancellation_changes(league)
            if json.loads(row["payload"]).get("round_id") == round_id(AM, 4)]
    assert [row["state"] for row in am_4] == ["DONE"]


@pytest.mark.parametrize("stopped", ["apply", "unarm"])
async def test_an_amendment_stopped_after_its_judgement_and_retried_once_the_deadline_passed_is_refused_by_the_save(
    tmp_path, reposts, stopped,
):
    """Attendance on, its check-in deadline two hours before the round. Pro's round 3 is moved to
    two hours and ten minutes out and confirmed; the amendment is judged allowed, and stops at its
    save (the renumbering finds the database locked) or at the removal of its timed work. Am's
    round 4 is cancelled behind it. Eleven minutes on, while the reply can still be updated, the
    check-in deadline has passed; the fault is mended and Retry is pressed. The save refuses in
    today's Confirm words, nothing of the amendment written, one refusal line, the round armed
    again as it stood, and Am's round 4 is cancelled."""
    league = await ongoing_league(tmp_path, attendance=True)
    before = await _moment(league)
    press = await _amended(league, scheduled_at=_at(league, hours=2, minutes=10))
    if stopped == "apply":
        with _everywhere("renumber_rounds_on",
                         AsyncMock(side_effect=RuntimeError("database is locked"))):
            await run_queue(league.bot)
            assert await _stopped_at(league) == "apply"
    else:
        _unarming_fails(league)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "unarm"
    behind = await cancel_round(league, "Am", 4)
    assert not league.errors, league.errors

    league.clock.advance(minutes=11)
    if stopped == "unarm":
        _unarming_mended(league)
    await retry_job(league.bot)

    refused = (
        NO_LONGER
        + "The check-in deadline for that moment has already passed, so the round would have no "
        "check-in at all. Move it further out, or shorten the deadline."
        + NOTHING_CHANGED
    )
    assert _outcome(press) == refused
    assert (await _job(league, "apply"))["result"]["refused"] == refused
    assert await _moment(league) == before
    assert await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'round.scheduled_at'"
    ) == []
    assert _success_lines(league) == []
    assert len(_amend_refusals(league)) == 1
    assert league.unarmed.count(R3) == 1
    assert league.armed == [("results", [R3]), ("attendance", [R3])]
    assert await stopped_job(league.db_path) is None
    assert behind.edit_original_response.await_count >= 1
    am_4 = [row for row in await cancellation_changes(league)
            if json.loads(row["payload"]).get("round_id") == round_id(AM, 4)]
    assert [row["state"] for row in am_4] == ["DONE"]
    assert reposts.posted == []


async def test_a_round_gone_when_the_amendment_runs_is_refused(tmp_path):
    """Pro's round 4 is moved a day later and confirmed while the queue is stopped; the round is
    deleted while the amendment waits. Once the queue goes on, the amendment is refused with
    today's words, and nothing is unarmed."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _stopped_blocker(league)
    press = await _amended(league, 4, scheduled_at=_at(league, days=121))
    await league.write("DELETE FROM rounds WHERE id = ?", R4)

    await _clear_blocker(league)

    assert _outcome(press) == "⛔ That round no longer exists. **Nothing has been changed.**"
    assert (await _change(league))["state"] == "REFUSED"
    assert R4 not in league.unarmed
    assert len(_amend_refusals(league)) == 1


# ── What it does ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("weather, attendance", [
    (True, True), (True, False), (False, True), (False, False),
], ids=["weather and attendance on", "weather on", "attendance on", "both off"])
async def test_the_jobs_run_in_today_s_order(tmp_path, phases, reposts, weather, attendance):
    """Round 3's call stands, and its phase 2 forecast (message 8002) was posted though phase 1
    never ran. The round is brought forward to three days out: phase 1 is now due and was never
    drawn, phase 2 is ahead again and withdrawn, and the call's window has passed, so the call is
    posted again. The jobs run in today's order; a module off drops its own (the forecast's
    deletion runs whatever weather's state)."""
    league = await ongoing_league(tmp_path, weather=weather, attendance=attendance,
                                  horizons=HORIZONS)
    await posted_forecast(league, R3, 2, 8002)
    await _amended(league, scheduled_at=_at(league, days=3))

    await run_queue(league.bot)

    expected = ["judge", "unarm", "apply", "arm"]
    if attendance:
        expected += ["take_down_call", "post_call"]
    expected += ["delete_forecast"]
    if weather:
        expected += ["notify_invalidation", "rerun_phase"]
    expected += ["close"]
    assert await _ran(league) == expected
    assert league.armed == [("weather" if weather else "results", [R3])] + (
        [("attendance", [R3])] if attendance else []
    )
    assert phases.ran == ([(1, R3)] if weather else [])
    assert reposts.posted == ([(R3, PRO)] if attendance else [])
    assert 8002 not in league.channel(PRO_CH.forecast).messages


@pytest.mark.parametrize("stopped", ["judge", "unarm"])
async def test_a_discarded_judgement_or_removal_amends_nothing_and_leaves_the_timed_work(
    tmp_path, stopped,
):
    """The amendment stops at its judgement (its windows cannot be read) or at the removal of
    the timed work, and a league admin discards it: nothing is amended, the round keeps its timed
    work, nothing is armed and no success line is written."""
    league = await ongoing_league(tmp_path)
    before = await _moment(league)
    press = await _amended(league, scheduled_at=_at(league, days=91))
    if stopped == "judge":
        _judging_fails(league)
    else:
        _unarming_fails(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == stopped

    await discard_job(league.bot)

    assert _outcome(press) == NOTHING_AMENDED
    assert await _moment(league) == before
    assert league.unarmed == []
    assert league.armed == []
    assert _success_lines(league) == []


async def test_a_discarded_save_amends_nothing_and_arms_the_round_again_as_it_was(tmp_path):
    """The save fails and a league admin discards it: the round stands as it was, its numbers
    unchanged, and its timed work is armed again against its old moment."""
    league = await ongoing_league(tmp_path)
    before = await _moment(league)
    press = await _amended(league, scheduled_at=_at(league, days=150))

    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        await discard_job(league.bot)

    assert _outcome(press) == SAVE_DISCARDED
    assert await _moment(league) == before
    assert await _numbers(league) == {round_id(PRO, n): n for n in (1, 2, 3, 4)}
    assert league.unarmed == [R3]
    assert league.armed == [("results", [R3])]
    assert _success_lines(league) == []


async def test_a_discarded_amendment_is_named_by_the_number_the_round_bears_when_the_reply_is_made(
    tmp_path, monkeypatch,
):
    """Attendance on; Pro's round 2 is still to be run, 60 days out. While the queue is stopped,
    amendment A moves round 2 after round 4 and amendment B changes round 3's track, pressed as
    round 3. The queue goes on: A renumbers the division, so B's round is now round 2. B's save
    stops and a league admin discards it: the reply names round 2, never round 3."""
    from leaguebot.weather.services import phase_withdrawal

    real = phase_withdrawal.withdraw_phases_on
    failing = {"on": False}

    async def _withdraw(db: Any, rid: int, numbers: Any) -> None:
        if failing["on"] and rid == R3:
            raise RuntimeError("database is locked")
        await real(db, rid, numbers)

    # Before the league is built, so that the builder's hooks bind it.
    monkeypatch.setattr(phase_withdrawal, "withdraw_phases_on", _withdraw)
    league = await ongoing_league(tmp_path, attendance=True)
    r2 = round_id(PRO, 2)
    await league.write(
        "UPDATE rounds SET status = 'NOT_RUN', scheduled_at = ? WHERE id = ?",
        (league.clock.now + timedelta(days=60)).replace(tzinfo=None).isoformat(), r2,
    )
    await _stopped_blocker(league)
    await _amended(league, 2, scheduled_at=_at(league, days=150))
    press = await _amended(league, 3, track="Hungaroring")
    assert len(await amendment_changes(league)) == 2

    failing["on"] = True
    await _clear_blocker(league)
    assert await _stopped_at(league) == "apply"
    assert (await _numbers(league))[R3] == 2
    await discard_job(league.bot)

    assert _outcome(press) == (
        "Nothing was amended: round 2 in **Pro** stands as it was, its timed work armed again. "
        "Run `/round amend` again."
    )
    assert "round 3" not in _outcome(press)


async def test_the_arming_cannot_be_discarded_and_once_retried_arms_the_round(tmp_path):
    """The arming fails and stops the queue. A league admin's Discard on it is refused,
    privately and with one line in the log channel; the queue stays stopped at the arming, and
    nothing is discarded. Once the scheduler is mended, Retry arms round 3 against its new moment
    and the reply names nothing left undone."""
    league = await ongoing_league(tmp_path)
    press = await _amended(league, scheduled_at=_at(league, days=91))
    league.arming_fails = RuntimeError("job store unavailable")
    await run_queue(league.bot)
    assert await _stopped_at(league) == "arm"

    discarding = await discard_job(league.bot)

    assert CANNOT_DISCARD in _said_to(discarding)
    assert len([line for line in _log_lines(league)
                if line.startswith("⛔") and "can't be discarded" in line]) == 1
    assert await _stopped_at(league) == "arm"
    assert await league.rows(
        "SELECT * FROM audit_entries WHERE change_type = 'CHANGE_JOB_DISCARDED'"
    ) == []

    league.arming_fails = None
    await retry_job(league.bot)

    assert league.armed == [("results", [R3])]
    assert await _moment(league) == _when(league, days=91)
    assert await stopped_job(league.db_path) is None
    assert _outcome(press).startswith(AMENDED)
    assert NOT_DONE not in _outcome(press)


async def test_a_discarded_call_repost_names_the_command_that_posts_it(tmp_path, reposts):
    """Attendance on; round 3 is brought forward inside its call's window, so its call is taken
    down and posted again. The repost fails on a fault of the bot's own and a league admin
    discards it: the reply names the call and the command that posts it."""
    league = await ongoing_league(tmp_path, attendance=True)
    reposts.fault = RuntimeError("embed could not be built")
    press = await _amended(league, scheduled_at=_at(league, days=3))
    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_call"

    await discard_job(league.bot)

    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    assert ("**Pro** — check-in call: the call could not be posted again, and a league admin "
            "discarded it; post it with `/attendance post-check-in`") in outcome


async def test_a_discarded_phase_run_says_it_is_drawn_when_the_bot_next_starts(tmp_path, phases):
    """Weather on; round 3 is brought forward to three days out, so its phase 1, never drawn,
    is due at once. Drawing it fails on a fault of the bot's own and a league admin discards it:
    the reply says it is drawn when the bot next starts."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    phases.failing[1] = RuntimeError("forecast could not be drawn")
    press = await _amended(league, scheduled_at=_at(league, days=3))
    await run_queue(league.bot)
    assert await _stopped_at(league) == "rerun_phase"

    await discard_job(league.bot)

    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    assert ("**Pro** — Phase 1 forecast: not drawn, and a league admin discarded it; it is drawn "
            "when the bot next starts, while the round is still to be run") in outcome


async def test_a_module_turned_off_before_its_job_drops_it(tmp_path):
    """Weather on; round 3's posted phase 1 forecast is withdrawn by moving the round. Weather is
    turned off once the round is armed again: the forecast is still deleted, but the notice that
    the forecasts no longer stand is dropped, and nothing is posted in the forecast channel."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await posted_forecast(league, R3, 1, 8001)
    press = await _amended(league, scheduled_at=_at(league, days=91))
    await _run_through(league, "arm")
    await league.switch("weather", False)

    await run_queue(league.bot)

    assert (await _change(league))["state"] == "DONE"
    assert (await _job(league, "notify_invalidation"))["result"] == {"dropped": True}
    assert 8001 not in league.channel(PRO_CH.forecast).messages
    assert league.texts(PRO_CH.forecast) == []
    assert NOT_DONE not in _outcome(press)


async def test_a_cancellation_queued_behind_an_amendment_runs_after_its_renumbering(tmp_path):
    """Attendance on; the queue is stopped. Pro's round 3 is moved past round 4 and confirmed,
    then Pro's round 4 is cancelled, each ask blind to the other as two asks in the same instant
    are. Once the queue goes on, the amendment renumbers the division first, so the cancellation
    names the round it cancels by its new number, 3, in its notice, its line and its reply."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _stopped_blocker(league)
    await _amended(league, scheduled_at=_at(league, days=150))
    with _blind_to_the_queue():
        cancelling = await cancel_round(league, "Pro", 4)
    assert not league.errors, league.errors
    assert len(await amendment_changes(league)) == 1
    assert len([row for row in await cancellation_changes(league)
                if json.loads(row["payload"]).get("round_id") == R4]) == 1

    await _clear_blocker(league)

    assert await _status(league, R4) == "CANCELLED"
    assert await _status(league) == "NOT_RUN"
    assert await _numbers(league) == {round_id(PRO, 1): 1, round_id(PRO, 2): 2, R4: 3, R3: 4}
    assert _content(cancelling.edit_original_response.await_args) == (
        "✅ Round **3** in **Pro** cancelled."
    )
    notices = [text for text in league.texts(PRO_CH.checkin) if "Cancelled" in text]
    assert len(notices) == 1 and notices[0].startswith("<@&801>\n📢 **Round 3 Cancelled: Pro**")
    [line] = [line for line in league.bot.log_channel.sent
              if "| /round cancel | Success" in line and "division: Pro" in line]
    assert "\n  round: 3" in line and "round: 4" not in line


@pytest.mark.parametrize("case", [
    pytest.param("cancelled", id="cancelled while the arming stood stopped"),
    pytest.param("results", id="results entered while the removal stood stopped"),
    pytest.param("control", id="still to be run"),
])
async def test_the_arming_is_not_due_once_the_round_can_no_longer_be_cancelled(tmp_path, case):
    """(cancelled) The arming stops; meanwhile `/season cancel`, off the queue, records round 3
    cancelled; on Retry the arming is dropped as no longer due, one line says the stopped job no
    longer stops the queue, nothing is armed and the queue goes on. (results) The removal of the
    timed work stops; meanwhile round 3's results are entered; on Retry the save refuses in
    today's Confirm words, the arming is dropped and nothing is armed. (control) The removal stops
    and the round is still to be run: on Retry the arming runs."""
    league = await ongoing_league(tmp_path)
    press = await _amended(league, scheduled_at=_at(league, days=91))
    if case == "cancelled":
        league.arming_fails = RuntimeError("job store unavailable")
        await run_queue(league.bot)
        assert await _stopped_at(league) == "arm"
        await _set_status(league, "CANCELLED")
        league.arming_fails = None
    else:
        _unarming_fails(league)
        await run_queue(league.bot)
        assert await _stopped_at(league) == "unarm"
        if case == "results":
            await _set_status(league, "AWAITING_REPORT_VERDICTS")
        _unarming_mended(league)

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    if case == "control":
        assert league.armed == [("results", [R3])]
        assert (await _change(league))["state"] == "DONE"
        return
    assert league.armed == []
    arming = await _job(league, "arm")
    assert arming["result"] == {"dropped": True}
    if case == "cancelled":
        cleared = [line.split("\n")[0] for line in _log_lines(league)
                   if "no longer stops the queue" in line]
        assert len(cleared) == 1, cleared
        assert cleared[0].startswith(f"ℹ️ Job #{arming['id']} (")
        assert cleared[0].endswith(
            ") no longer stops the queue: it is no longer due, so it was dropped. The queue runs "
            "on."
        )
    if case == "results":
        outcome = _outcome(press)
        assert outcome.startswith(NO_LONGER) and "**Nothing has been changed.**" in outcome
        assert await _moment(league) != _when(league, days=91)


async def test_a_mystery_round_moved_inside_its_first_horizon_keeps_its_results_submission(
    tmp_path,
):
    """Weather on, horizons 5 days, 2 days, 2 hours; Pro's round 3 is a mystery round. It is
    brought forward to three days out, inside its first horizon, so its forecasts are not armed
    again. Its results submission is armed all the same, so that the round still opens its
    submission and can finish."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await league.write("UPDATE rounds SET format = 'MYSTERY' WHERE id = ?", R3)
    await _amended(league, scheduled_at=_at(league, days=3))

    await run_queue(league.bot)

    assert (await _change(league))["state"] == "DONE"
    assert ("results", [R3]) in league.armed
    assert ("weather", [R3]) not in league.armed


async def test_a_mystery_round_whose_save_is_discarded_is_armed_again_with_its_mystery_notice(
    tmp_path,
):
    """Weather on, horizons 5 days, 2 days, 2 hours; Pro's round 3 is a mystery round 90 days
    out, its first horizon days ahead. It is brought forward to three days out, inside that
    horizon; the save stops and a league admin discards it. The round stands at its old moment,
    its first horizon still ahead, so it is armed again with its forecasts (`schedule_round`, which
    arms its mystery notice and its results submission), not its results submission alone, and the
    reply says its timed work was armed again."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await league.write("UPDATE rounds SET format = 'MYSTERY' WHERE id = ?", R3)
    before = await _moment(league)
    press = await _amended(league, scheduled_at=_at(league, days=3))

    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        await discard_job(league.bot)

    assert _outcome(press) == SAVE_DISCARDED
    assert await _moment(league) == before
    assert league.unarmed == [R3]
    assert league.armed == [("weather", [R3])]


# ── What fell due while the amendment stood stopped (owner, 2026-10-09) ────────────────


async def _place_r3(league: Any, **delta: float) -> None:
    """Pro's round 3 stands *delta* from "now", stored as plain UTC as production stores it."""
    await league.write(
        "UPDATE rounds SET scheduled_at = ? WHERE id = ?",
        (league.clock.now + timedelta(**delta)).replace(tzinfo=None).isoformat(), R3,
    )


async def test_a_phase_that_fell_due_while_the_save_stood_stopped_is_drawn_at_once(
    tmp_path, phases,
):
    """Weather on, horizons 5 days, 2 days, 2 hours. Pro's round 3 is two days and thirty minutes
    out, its Phase 1 drawn and its Phase 2 thirty minutes ahead. It is moved later, to two days
    and two hours out: Phase 2's new horizon, two hours ahead, is still to come when the amendment
    is judged. The save stops; two hours and ten minutes on, past Phase 2's new horizon, it is
    retried. Phase 2 is drawn at once, by a job of the amendment's, rather than left to a schedule
    whose moment has passed."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS,
                                  phases_done={R3: (1,)})
    await _place_r3(league, days=2, minutes=30)
    moved = _when(league, days=2, hours=2)
    await _amended(league, scheduled_at=_at(league, days=2, hours=2))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"

    league.clock.advance(hours=2, minutes=10)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    assert await _moment(league) == moved
    assert [json.loads(job["payload"]) for job in await _jobs(league)
            if job["name"] == "rerun_phase"] == [{"phase": 2}]
    assert phases.ran == [(2, R3)]


@pytest.mark.parametrize("stopped", [
    pytest.param("apply", id="the save stopped"),
    pytest.param("take_down_call", id="the take-down stopped after the arming"),
])
async def test_a_call_that_fell_due_while_the_amendment_stood_stopped_is_posted_again(
    tmp_path, reposts, stopped,
):
    """Attendance on, its call five days before the round and its deadline two hours before.
    Pro's round 3, its call standing (messages 7001 to 7003), is brought forward to five days and
    one hour out: the call's new moment, an hour ahead, is still to come when the amendment is
    judged, so the old call is to come down and the new one to wait for its moment. (the save
    stopped) The save stops; (the take-down stopped after the arming) the round is armed with its
    new call, and Discord refuses to delete the last notice (7002), stopping the take-down. An
    hour and ten minutes on, past the call's new moment and with its deadline still ahead, the
    fault is mended and Retry is pressed: the old call comes down and the call is posted again,
    so that one stands."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _amended(league, scheduled_at=_at(league, days=5, hours=1))
    if stopped == "apply":
        with _renumbering_fails():
            await run_queue(league.bot)
            assert await _stopped_at(league) == "apply"
    else:
        league.undeletable.add(CALL_MESSAGES[1])
        await run_queue(league.bot)
        assert await _stopped_at(league) == "take_down_call"
        assert ("attendance", [R3]) in league.armed
        league.undeletable.clear()

    league.clock.advance(hours=1, minutes=10)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    checkin = league.channel(PRO_CH.checkin)
    assert all(mid not in checkin.messages for mid in CALL_MESSAGES)
    assert reposts.posted == [(R3, PRO)]


async def test_a_call_that_fell_due_while_the_save_stood_stopped_is_posted_once_it_is_discarded(
    tmp_path, reposts,
):
    """Attendance on, its call five days before the round. Pro's round 3 is five days and thirty
    minutes out, and no call stands for it yet: its call is thirty minutes ahead. It is moved a
    day later; the save stops, and forty minutes on, past the call's moment and with its deadline
    still ahead, a league admin discards it. The round stands at its old moment, its call due and
    none standing: the call is posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", R3)
    await _place_r3(league, days=5, minutes=30)
    before = await _moment(league)
    await _amended(league, scheduled_at=_at(league, days=6, minutes=30))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=40)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _moment(league) == before
    assert reposts.posted == [(R3, PRO)]


@pytest.mark.parametrize("ending", ["discarded", "refused"])
async def test_a_round_whose_moment_passed_while_its_save_stood_stopped_opens_its_results_submission(
    tmp_path, ending,
):
    """Pro's round 3 is two hours and five minutes out (a round at 20:00, amended at 17:55). It
    is moved thirty minutes later, and the save stops. (discarded) Ten minutes past its old
    moment a league admin discards the save; (refused) ten minutes past its new moment the save
    is retried and refuses it, that moment having passed. Either way the round stands at its old
    moment, now gone by, and still to be run: its results submission is run at once, rather than
    left to a job armed for a moment already past."""
    league = await ongoing_league(tmp_path)
    await _place_r3(league, hours=2, minutes=5)
    before = await _moment(league)
    await _amended(league, scheduled_at=_at(league, hours=2, minutes=35))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        if ending == "discarded":
            league.clock.advance(hours=2, minutes=15)
            await discard_job(league.bot)
    if ending == "refused":
        league.clock.advance(hours=2, minutes=45)
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _moment(league) == before
    assert await _status(league) == "NOT_RUN"
    opened = league.bot.scheduler_service.run_result_submission_now
    assert [call.args[0].id for call in opened.call_args_list] == [R3]


async def test_a_check_in_deadline_that_passed_while_the_save_stood_stopped_is_run_once_it_is_discarded(
    tmp_path, reposts, deadlines,
):
    """Attendance on, its deadline two hours before the round. Pro's round 3 is two hours and
    five minutes out, its call standing and its reserves not yet distributed. It is moved a day
    later; the save stops, and ten minutes on, past the old deadline, a league admin discards it.
    The round stands at its old moment, its deadline gone by with its call standing: the deadline
    is run at once, as a restart runs it, and no call is posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    await league.write(
        "UPDATE rsvp_embed_messages SET distribution_msg_id = NULL WHERE round_id = ?", R3
    )
    await _place_r3(league, hours=2, minutes=5)
    before = await _moment(league)
    await _amended(league, scheduled_at=_at(league, days=1, hours=2))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=10)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _moment(league) == before
    assert deadlines.ran == [R3]
    assert reposts.posted == []


async def test_the_clean_ups_that_fell_due_while_the_save_stood_stopped_are_run_once_it_is_discarded(
    tmp_path,
):
    """Weather and attendance on. Pro's round 3 is thirty minutes out, its Phase 3 forecast
    (message 8003) posted and its call standing (7001 to 7003). It is moved a day later; the save
    stops, and a day and forty minutes on, past the clean-ups due a day after the old moment, a
    league admin discards it. The round stands at its old moment: its Phase 3 forecast is deleted
    and its check-in taken down, as a restart takes them down."""
    league = await ongoing_league(tmp_path, weather=True, attendance=True, horizons=HORIZONS)
    await _place_r3(league, minutes=30)
    await posted_forecast(league, R3, 3, 8003)
    before = await _moment(league)
    await _amended(league, scheduled_at=_at(league, days=1))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(days=1, minutes=40)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _moment(league) == before
    assert 8003 not in league.channel(PRO_CH.forecast).messages
    checkin = league.channel(PRO_CH.checkin)
    assert all(mid not in checkin.messages for mid in CALL_MESSAGES)
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 1


@pytest.mark.parametrize("ending", ["discarded", "refused"])
async def test_a_discarded_catch_up_is_named_whatever_became_of_the_save(
    tmp_path, phases, ending,
):
    """Weather on, horizons 5 days, 2 days, 2 hours. Pro's round 3 is two days and five minutes
    out, its Phase 1 drawn, its Phase 2 five minutes ahead. It is brought forward to ten minutes
    out and the save stops. Twelve minutes on, past the old Phase 2 horizon, (discarded) a league
    admin discards the save, or (refused) Retry is pressed and the save refuses it, its new moment
    having passed. The round is armed again at its old moment and its Phase 2, due meanwhile, is
    drawn at once; drawing it fails and a league admin discards it. The reply names it under
    "Not done" beneath what became of the save, and the log channel names the job discarded."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS,
                                  phases_done={R3: (1,)})
    await _place_r3(league, days=2, minutes=5)
    press = await _amended(league, scheduled_at=_at(league, minutes=10))
    phases.failing[2] = RuntimeError("forecast could not be drawn")
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=12)
        if ending == "discarded":
            await discard_job(league.bot)
    if ending == "refused":
        await retry_job(league.bot)
    assert await _stopped_at(league) == "rerun_phase"

    await discard_job(league.bot)

    head = SAVE_DISCARDED if ending == "discarded" else (
        NO_LONGER
        + "A round cannot be moved into the past. Its result submission would never open, and "
        "the round could never take results at all. Give it a moment still to come."
        + NOTHING_CHANGED
    )
    outcome = _outcome(press)
    assert outcome.startswith(head) and NOT_DONE in outcome
    assert ("**Pro** — Phase 2 forecast: not drawn, and a league admin discarded it; it is drawn "
            "when the bot next starts, while the round is still to be run") in outcome
    assert any("| Discard job #" in line
               and "not done: drawing a forecast of round 3 in **Pro** now" in line
               for line in _log_lines(league))


async def test_a_call_whose_deadline_passed_while_its_take_down_stood_stopped_is_given_up_as_a_restart_does(
    tmp_path, reposts,
):
    """Attendance on, its deadline two hours before the round. Pro's round 3, its call standing,
    is brought forward to three hours out, so its call is to be taken down and posted again.
    Discord refuses to delete the last notice (7002), stopping the take-down. An hour and ten
    minutes on, past the new deadline, the fault is mended and Retry is pressed: the call comes
    down and is not posted again, the log channel says so in the words a restart uses, and the
    round's check-in is marked closed."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _amended(league, scheduled_at=_at(league, hours=3))
    league.undeletable.add(CALL_MESSAGES[1])
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"

    league.clock.advance(hours=1, minutes=10)
    league.undeletable.clear()
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert reposts.posted == []
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 1
    [line] = [line for line in _log_lines(league)
              if line.startswith("ATTENDANCE | check-in call | NOT POSTED")]
    assert ("reason: the round's check-in deadline passed before its call could be posted"
            in line)
    assert "round: 3" in line


async def test_a_call_that_fell_due_while_the_save_stood_stopped_is_posted_under_test_mode_too(
    tmp_path, reposts,
):
    """As the call caught up once a stopped save is discarded, under test mode: the amendment
    posts it all the same, unlike the start-up recovery, which leaves a test season's calls to
    `/test-mode advance` (owner, 2026-10-09: "Post it anyway")."""
    league = await ongoing_league(tmp_path, attendance=True)
    await league.set_test_mode(True)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", R3)
    await _place_r3(league, days=5, minutes=30)
    await _amended(league, scheduled_at=_at(league, days=6, minutes=30))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=40)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert reposts.posted == [(R3, PRO)]


# ── Each catching-up job judged again when it runs (owner, 2026-10-09) ────────────────


def _given_up_lines(league: Any, number: int = 3) -> list[str]:
    """The log channel's lines giving up a call of Pro's round *number*."""
    return [line for line in _log_lines(league)
            if line.startswith("ATTENDANCE | check-in call | NOT POSTED")
            and "division: Pro" in line and f"round: {number}\n" in line]


async def _call_to_post_at_once(league: Any) -> None:
    """Attendance on, its call five days before the round. Pro's round 3 is five days and thirty
    minutes out, and no call stands for it. It is moved a day later; the save stops, and forty
    minutes on, past the call's moment, a league admin discards it. The round is armed again at
    its old moment with its call to post at once, a job not yet run."""
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", R3)
    await _place_r3(league, days=5, minutes=30)
    await _amended(league, scheduled_at=_at(league, days=6, minutes=30))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
    league.clock.advance(minutes=40)
    await discard_job(league.bot, run=False)
    await _run_through(league, "arm")
    assert "post_call" in [job["name"] for job in await _jobs(league)]


@pytest.mark.parametrize("path", ["first post", "after the take-down"])
async def test_a_call_posted_late_at_a_restart_before_the_amendment_posts_it_is_posted_once(
    tmp_path, reposts, path,
):
    """(first post) As `_call_to_post_at_once`; (after the take-down) attendance on, Pro's round
    3, its call standing, is brought forward to three days out, and the old call is taken down,
    the new one to post. The bot restarts before the amendment's job posts the call, and the
    start-up recovery posts it late. When the job runs, the call stands: it is neither taken down
    nor posted again, and the division is called once."""
    from leaguebot.__main__ import _recover_missed_check_in_calls

    league = await ongoing_league(tmp_path, attendance=True)
    if path == "first post":
        await _call_to_post_at_once(league)
    else:
        await _amended(league, scheduled_at=_at(league, days=3))
        await _run_through(league, "take_down_call")

    await _recover_missed_check_in_calls(league.bot, now=league.clock.now)
    assert reposts.posted == [(R3, PRO)]
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    assert reposts.posted == [(R3, PRO)]
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1


@pytest.mark.parametrize("path", ["first post", "after the take-down"])
async def test_a_call_standing_when_the_amendment_s_post_runs_past_its_deadline_is_not_given_up(
    tmp_path, reposts, path,
):
    """Attendance on, its call five days before the round and its deadline two hours before.
    (first post) As `_call_to_post_at_once`; (after the take-down) Pro's round 3, its call
    standing, is brought forward to five days and one hour out, and the old call is taken down,
    the new one to post. Before the amendment's post runs, a call is posted for the round all the
    same (by `/attendance post-check-in`, or its timer), and the round's deadline passes. When the
    job runs, the call standing is live: it is not given up, no line says it was not posted, and
    the round's check-in is not closed."""
    league = await ongoing_league(tmp_path, attendance=True)
    if path == "first post":
        await _call_to_post_at_once(league)
        league.clock.advance(hours=119)
    else:
        await _amended(league, scheduled_at=_at(league, days=5, hours=1))
        await _run_through(league, "take_down_call")
        league.clock.advance(hours=120)
    await league.write(
        "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
        "posted_at) VALUES (?, ?, ?, ?, ?)",
        R3, PRO, str(LATE_CALL), str(PRO_CH.checkin), league.clock.now.isoformat(),
    )

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    assert _given_up_lines(league) == []
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 0
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1
    assert reposts.posted == []


async def test_a_phase_whose_race_passed_while_a_job_before_it_stood_stopped_is_not_drawn(
    tmp_path, phases, reposts,
):
    """Weather and attendance on, horizons 5 days, 2 days, 2 hours. Pro's round 3, its call
    standing, is brought forward to a day out: its Phase 1 and Phase 2, never drawn, are to be
    drawn at once, and its call taken down and posted again. Discord refuses to delete the last
    notice (7002), stopping the take-down ahead of them. A day and an hour on, past the race, the
    fault is mended and Retry is pressed: each phase is judged again as its job runs, and no
    forecast is drawn for a round already raced."""
    league = await ongoing_league(tmp_path, weather=True, attendance=True, horizons=HORIZONS)
    league.undeletable.add(CALL_MESSAGES[1])
    await _amended(league, scheduled_at=_at(league, days=1))
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    assert [json.loads(job["payload"]) for job in await _jobs(league)
            if job["name"] == "rerun_phase"] == [{"phase": 1}, {"phase": 2}]

    league.clock.advance(days=1, hours=1)
    league.undeletable.clear()
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    assert phases.ran == []


async def _closing_the_check_in_fails(league: Any) -> None:
    """The database refuses to mark any round's check-in closed, as a full disk would."""
    await league.write(
        "CREATE TRIGGER closing_fails BEFORE UPDATE OF checkin_cleared ON rounds "
        "WHEN NEW.checkin_cleared = 1 BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )


async def test_a_call_given_up_whose_save_fails_stops_the_queue_and_is_given_up_once_retried(
    tmp_path, reposts,
):
    """As a call given up once its take-down stood stopped past the deadline (Pro's round 3
    brought forward to three hours out, the last notice 7002 undeletable, an hour and ten minutes
    on), the database refusing to close the round's check-in: a fault of the bot's own, which
    stops the queue at the call's job, the log channel not yet told. Once the fault is mended and
    Retry pressed, the call is given up, and the log channel told once."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _amended(league, scheduled_at=_at(league, hours=3))
    league.undeletable.add(CALL_MESSAGES[1])
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    await _closing_the_check_in_fails(league)
    league.clock.advance(hours=1, minutes=10)
    league.undeletable.clear()
    await retry_job(league.bot)

    assert await _stopped_at(league) == "post_call"
    assert _given_up_lines(league) == []

    await league.write("DROP TRIGGER closing_fails")
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    assert len(_given_up_lines(league)) == 1
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 1


async def test_a_call_given_up_after_its_take_down_clears_the_round_s_answers_and_placements(
    tmp_path, reposts,
):
    """As a call given up once its take-down stood stopped past the deadline (Pro's round 3
    brought forward to three hours out, the last notice 7002 undeletable, an hour and ten minutes
    on), Lewis having accepted the old call and Max not answered it, and the deadline's timer,
    armed with the round, having placed Max in Ferrari meanwhile. The round's answers and its
    placement go in the save that closes its check-in, so that the log channel's line, written
    once, holds true: no attendance rows stand for the round, and it counts nothing against
    anyone (owner, 2026-10-09: "Clear the answers, keep the line true")."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _amended(league, scheduled_at=_at(league, hours=3))
    league.undeletable.add(CALL_MESSAGES[1])
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    await league.write(
        "UPDATE driver_round_attendance SET assigned_team_id = ? "
        "WHERE round_id = ? AND driver_profile_id = ?",
        FERRARI, R3, profile_id(MAX),
    )
    league.clock.advance(hours=1, minutes=10)
    league.undeletable.clear()

    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    [line] = _given_up_lines(league)
    assert "this round will count nothing against anyone" in line
    assert await league.rows("SELECT * FROM driver_round_attendance WHERE round_id = ?", R3) == []
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 1


async def test_under_test_mode_a_round_whose_moment_passed_while_its_save_stood_stopped_is_left_to_test_mode_advance(
    tmp_path,
):
    """Test mode on. Pro's round 3 is two hours and five minutes out; it is moved thirty minutes
    later and the save stops. Ten minutes past its old moment a league admin discards the save:
    the round stands at its old moment, gone by, and still to be run. Its results submission is
    armed with it, but not run at once, `/test-mode advance` running a test season's rounds in
    their turn (owner, 2026-10-09: "Leave it to /test-mode advance")."""
    league = await ongoing_league(tmp_path)
    await league.set_test_mode(True)
    await _place_r3(league, hours=2, minutes=5)
    await _amended(league, scheduled_at=_at(league, hours=2, minutes=35))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(hours=2, minutes=15)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _status(league) == "NOT_RUN"
    assert league.armed == [("results", [R3])]
    assert league.bot.scheduler_service.run_result_submission_now.call_args_list == []


async def test_a_phase_drawn_at_once_is_not_armed_as_well(tmp_path, phases):
    """Weather on, horizons 5 days, 2 days, 2 hours. Pro's round 3, its Phase 1 never drawn, is
    brought forward to four days, twenty-three hours and fifty-eight minutes out: its Phase 1
    horizon passed two minutes ago, so a job of the amendment's draws it at once. The round is
    armed again without a Phase 1 timer, which, its moment passed by less than the scheduler's
    five minutes' grace, would run at once and draw the phase a second time."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await _amended(league, scheduled_at=_at(league, days=4, hours=23, minutes=58))

    await run_queue(league.bot)

    assert (await _change(league))["state"] == "DONE"
    [armed] = league.bot.scheduler_service.schedule_round.call_args_list
    assert armed.kwargs.get("skip_phases") == frozenset({1})
    assert phases.ran == [(1, R3)]


async def _passed_while_the_save_stood_stopped(league: Any, **after: float) -> None:
    """Pro's round 3 is two hours and five minutes out; it is moved a day later and the save
    stops. *after* on, a league admin discards it: the round is armed again at its old moment."""
    await _place_r3(league, hours=2, minutes=5)
    await _amended(league, scheduled_at=_at(league, days=1, hours=2))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(**after)
        await discard_job(league.bot)
    assert await stopped_job(league.db_path) is None


async def test_a_passed_round_s_results_submission_is_run_at_once_after_it_is_armed(tmp_path):
    """As a round whose moment passed while its save stood stopped (ten minutes past it, the save
    discarded): its results submission is armed with the round first and run at once after,
    the run replacing the job armed for a moment already gone. The other way round, the job armed
    would replace the run, and be skipped by the scheduler."""
    league = await ongoing_league(tmp_path)
    await _passed_while_the_save_stood_stopped(league, hours=2, minutes=15)

    names = [call[0] for call in league.bot.scheduler_service.method_calls
             if call[0] in ("schedule_round", "schedule_result_submission_jobs",
                            "run_result_submission_now")]
    assert names == ["schedule_result_submission_jobs", "run_result_submission_now"]


async def test_a_check_in_deadline_whose_reserves_were_distributed_is_not_run_again(
    tmp_path, reposts, deadlines,
):
    """Attendance on, its deadline two hours before the round. Pro's round 3, its call standing,
    has had its reserves distributed (its distribution announced, message 7003). It is moved a
    day later; the save stops, and ten minutes on, past the old deadline, a league admin discards
    it. The deadline has been run: it is not run again, and no call is posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _passed_while_the_save_stood_stopped(league, minutes=10)

    assert "run_deadline" not in [job["name"] for job in await _jobs(league)]
    assert deadlines.ran == []
    assert reposts.posted == []


async def test_a_call_whose_check_in_was_closed_is_not_posted(tmp_path, reposts):
    """As `_call_to_post_at_once`'s round, its check-in closed (`checkin_cleared`) and no call
    standing, as a call given up leaves it: once the stopped save is discarded past the call's
    moment, the call is not posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", R3)
    await league.write("UPDATE rounds SET checkin_cleared = 1 WHERE id = ?", R3)
    await _place_r3(league, days=5, minutes=30)
    await _amended(league, scheduled_at=_at(league, days=6, minutes=30))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=40)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "post_call" not in [job["name"] for job in await _jobs(league)]
    assert reposts.posted == []


async def test_no_phase_is_drawn_for_a_round_whose_race_passed_while_its_save_stood_stopped(
    tmp_path, phases,
):
    """Weather on, horizons 5 days, 2 days, 2 hours; no phase of Pro's round 3 drawn. As a round
    whose moment passed while its save stood stopped (ten minutes past it, the save discarded):
    every horizon has passed, but so has the race, and no phase is drawn for it."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await _passed_while_the_save_stood_stopped(league, hours=2, minutes=15)

    assert "rerun_phase" not in [job["name"] for job in await _jobs(league)]
    assert phases.ran == []


async def test_a_check_in_taken_down_after_its_round_is_not_given_up_once_a_take_down_is_discarded(
    tmp_path, reposts,
):
    """Attendance on. Pro's round 3, its call standing and answered (Lewis accepted, Max not),
    is brought forward to three hours out, so its call is to be taken down and posted again.
    Discord refuses to delete the last notice (7002), stopping the take-down. While it stands
    stopped, the round is run and its check-in taken down a day after it, as its clean-up does
    (the call's record gone, the check-in marked over). A day and four hours on, a league admin
    discards the take-down: the check-in is over, so the call is neither posted nor given up, no
    line says it was not posted, and the answers stand."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    await _amended(league, scheduled_at=_at(league, hours=3))
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", R3)
    await league.write("UPDATE rounds SET checkin_cleared = 1 WHERE id = ?", R3)
    league.clock.advance(days=1, hours=4)

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _job(league, "post_call"))["result"] == {"dropped": True}
    assert _given_up_lines(league) == []
    assert reposts.posted == []
    assert len(await league.rows(
        "SELECT * FROM driver_round_attendance WHERE round_id = ?", R3
    )) == 2


@pytest.mark.parametrize("fault", ["Discord refuses the post", "the channel is gone"])
async def test_a_call_posted_again_after_its_take_down_that_fails_says_its_answers_are_kept(
    tmp_path, monkeypatch, fault,
):
    """Attendance on. Pro's round 3, its call standing and answered (Lewis accepted, Max not),
    is brought forward to three days out, so its call is taken down, which goes through, and
    posted again. Posting it fails, Discord refusing the message or the check-in channel gone,
    as a call's own timer fails, and the queue goes on. The answers to the earlier call are kept,
    and the log channel says so, and that they count, naming `/attendance post-check-in` to post
    the call by hand, never that no attendance rows were opened (owner, 2026-10-09: "Make the
    line tell the truth")."""
    from leaguebot.attendance.services import rsvp_service
    from leaguebot.attendance.services.attendance_service import AttendanceService

    monkeypatch.setattr(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None))
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service.get_division_config = AttendanceService(
        league.db_path
    ).get_division_config
    await _amended(league, scheduled_at=_at(league, days=3))
    await _run_through(league, "take_down_call")
    if fault == "Discord refuses the post":
        league.channel(PRO_CH.checkin).send_fails = http_error(
            discord.Forbidden, status=403, text="Missing Permissions"
        )
    else:
        league.remove_channel(PRO_CH.checkin)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    assert len(await league.rows(
        "SELECT * FROM driver_round_attendance WHERE round_id = ?", R3
    )) == 2
    [line] = _given_up_lines(league)
    assert "answers given to an earlier call of this round are kept, and count" in line
    assert "`/attendance post-check-in division: Pro round: 3`" in line
    assert "no attendance rows were opened" not in line
