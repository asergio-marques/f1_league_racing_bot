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
    PRO,
    amend_round,
    amendment_changes,
    cancel_round,
    cancellation_changes,
    ongoing_league,
    posted_forecast,
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
    """Attendance's repost of a check-in call, recording `(round id, division id)` in `posted`;
    raising `fault` instead where it is set."""
    from leaguebot.attendance.services import rsvp_service

    record = SimpleNamespace(posted=[], fault=None)

    async def _repost(rid: int, division_id: int, *_args: Any, **_kwargs: Any) -> None:
        if record.fault is not None:
            raise record.fault
        record.posted.append((rid, division_id))

    monkeypatch.setattr(rsvp_service, "repost_rsvp_call", _repost)
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
