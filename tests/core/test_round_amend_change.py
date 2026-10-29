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
call standing; round 4 (id 214) 120 days out. The weather phases are recorded rather than run
(`phases`), patched before the league is built so that the builder's hooks reach the recorder
however they bind it; the call is posted as attendance posts it, and read from what its check-in
channel was sent (`reposts`).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sqlite3
import sys
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection

from tests.support.change_queue import (
    discard_job,
    http_error,
    refuse_the_job,
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
from tests.support.mentions import NOBODY, notified

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
#: The message of a check-in call recorded as posted late at a restart.
LATE_CALL = 7101
#: What each stop notice adds where an amendment's call is refused, by what is left of the round.
EARLIER_STANDS = "the earlier call still stands, and the answers given to it count"
ANSWERS_KEPT = ("no call stands; the answers given to the earlier call are kept, and count if "
                "this call is posted before the check-in deadline")
NONE_SEEN = "no call for this round can be seen, its message or its channel gone"
NOTHING_LEFT = ("no call stands, and no attendance rows are open for this round until this call "
                "is posted")
MAY_HAVE_LANDED = ("; it may have reached Discord all the same: the bot posts it again on its "
                   "next try, and any second copy is to be deleted by hand")
NOT_POSTED = "ATTENDANCE | check-in call | NOT POSTED"


# ── Recorders, patched before the league is built ──────────────────────────────────


@pytest.fixture
def phases(monkeypatch):
    """Weather's draw of each phase, recording `(phase, round id)` in `ran` as the amendment's
    `draw_forecast` job makes it, then drawn as weather draws it; a phase in `failing` raises its
    fault instead, as a fault of the bot's own (`tests/support/weather_draws.py`)."""
    from tests.support.weather_draws import recorded_draws

    return recorded_draws(monkeypatch)


class _Reposts:
    """What the `reposts` fixture saw of the league it wrapped: the check-in calls posted, and
    the fault their sends are refused with."""

    def __init__(self) -> None:
        self.league: Any = None

    @property
    def posted(self) -> list[tuple[int, int]]:
        """`(round id, division id)` of each check-in call sent since the league was built: a
        send to a check-in channel whose message stands recorded as a round's call."""
        if self.league is None:
            return []
        checkins = {DIVISIONS[division][3].checkin for division in DIVISIONS}
        sent = [(cid, mid) for kind, cid, mid in self.league.events
                if kind == "send" and cid in checkins]
        with sqlite3.connect(self.league.db_path) as db:
            calls = {(int(channel), int(message)): (rid, division)
                     for rid, division, message, channel in db.execute(
                         "SELECT round_id, division_id, message_id, channel_id "
                         "FROM rsvp_embed_messages")}
        return [calls[each] for each in sent if each in calls]

    def refuse_sends(self, fault: BaseException | None) -> None:
        """Every send to Pro's check-in channel raises *fault* in its place."""
        self.league.channel(PRO_CH.checkin).send_fails = fault


@pytest.fixture
def reposts(monkeypatch):
    """The check-in call posted as attendance posts it, by whichever of the bot's code posts it
    (#439, slice 6b, C28: today `run_rsvp_notice` and `repost_rsvp_call` whole, then the call's
    own job), and recorded from what Discord was sent, so that no test leans on either.

    It wraps this module's `ongoing_league`: the league each test builds gets attendance's own
    service, its writers included (`_attendance_as_it_is`), and the fixture keeps it. `posted`
    is `(round id, division id)` of each call sent to a check-in channel and recorded as the
    round's call; `refuse_sends(fault)` refuses every send to Pro's check-in channel with it."""
    record = _Reposts()
    built = ongoing_league

    async def _wrapped(*args: Any, **kwargs: Any) -> Any:
        league = await built(*args, **kwargs)
        _attendance_as_it_is(league, monkeypatch)
        record.league = league
        return league

    monkeypatch.setattr(sys.modules[__name__], "ongoing_league", _wrapped)
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


def _stop_notices(league: Any) -> list[str]:
    """The log channel's notices that the queue stopped, and its lines of a Retry that failed."""
    return [line for line in _log_lines(league)
            if "The queue is stopped at job #" in line or line.startswith("❌ Retry of job #")]


def _real_call(league: Any, monkeypatch: Any) -> None:
    """Attendance's own call poster, drawing no picture (the image module's check-in graphic
    gives none) and reading the division's check-in channel as it stands. The call's record —
    its message and the round's NO_RSVP rows — is written by the call's own job in its save
    (#439, slice 6b, C28): the attendance service's writers on a handed connection (its `_on`
    forms) are attendance's own, and its committing writers are left as the league's doubles."""
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.image.services import image_rsvp_post

    monkeypatch.setattr(image_rsvp_post, "try_attach", AsyncMock(return_value=None))
    attendance = AttendanceService(league.db_path)
    league.bot.attendance_service.get_division_config = attendance.get_division_config
    for name in dir(AttendanceService):
        if name.endswith("_on") and not name.startswith("_"):
            setattr(league.bot.attendance_service, name, getattr(attendance, name))


def _attendance_as_it_is(league: Any, monkeypatch: Any) -> None:
    """`_real_call`, the attendance service's committing writers made real as well, so that the
    call's record is written whichever of them the code that posts it uses."""
    from leaguebot.attendance.services.attendance_service import AttendanceService

    _real_call(league, monkeypatch)
    attendance = AttendanceService(league.db_path)
    for name in dir(AttendanceService):
        if not name.startswith("_") and callable(getattr(AttendanceService, name)):
            setattr(league.bot.attendance_service, name, getattr(attendance, name))


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


async def test_the_invalidation_notice_notifies_nobody(tmp_path):
    """Weather on; round 3's posted phase 1 forecast is withdrawn by moving the round ninety-one
    days on. The notice in Pro's forecast channel that the forecasts no longer stand notifies
    nobody."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await posted_forecast(league, R3, 1, 8001)
    await _amended(league, scheduled_at=_at(league, days=91))

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    [call] = league.channel(PRO_CH.forecast).send.await_args_list
    assert notified(call.kwargs.get("allowed_mentions")) == NOBODY


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


async def test_a_discarded_check_in_call_take_down_is_named_and_the_call_is_still_posted_again(
    tmp_path, reposts,
):
    """Attendance on; round 3's call stands (messages 7001 to 7003) and the round is brought
    forward to three days out, inside its call's window. Discord refuses to delete the last
    notice (7002): the queue stops at the take-down, the call's record kept, and no call is
    posted again while it stands stopped. A league admin discards the take-down: the amendment
    goes on, the call is posted again beside the message left standing, and the reply names 7002
    to remove by hand."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.undeletable.add(CALL_MESSAGES[1])
    press = await _amended(league, scheduled_at=_at(league, days=3))
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    assert len(await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3)) == 1
    assert CALL_MESSAGES[1] in league.channel(PRO_CH.checkin).messages
    assert reposts.posted == []

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
    pytest.param(True, True),
    pytest.param(True, False),
    pytest.param(False, True),
    pytest.param(False, False),
], ids=["weather and attendance on", "weather on", "attendance on", "both off"])
async def test_the_jobs_run_in_today_s_order(tmp_path, phases, reposts, weather, attendance):
    """Round 3's call stands, and its phase 2 forecast (message 8002) was posted though phase 1
    never ran. The round is brought forward to three days out: phase 1 is now due and was never
    drawn, phase 2 is ahead again and withdrawn, and the call's window has passed, so the call is
    posted again. The jobs run in today's order, phase 1 drawn and its forecast posted by jobs of
    the amendment's own after its `rerun_phase`; a module off drops its own (the forecast's
    deletion runs whatever weather's state)."""
    league = await ongoing_league(tmp_path, weather=weather, attendance=attendance,
                                  horizons=HORIZONS)
    await league.write("UPDATE rounds SET track_name = 'Silverstone Circuit' WHERE id = ?", R3)
    await posted_forecast(league, R3, 2, 8002)
    await _amended(league, scheduled_at=_at(league, days=3))

    await run_queue(league.bot)

    expected = ["judge", "unarm", "apply", "arm"]
    if attendance:
        expected += ["take_down_call", "post_call"]
    expected += ["delete_forecast"]
    if weather:
        expected += ["notify_invalidation", "rerun_phase", "draw_forecast", "post_forecast"]
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
    reposts.refuse_sends(RuntimeError("embed could not be built"))
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
    assert await _stopped_at(league) == "draw_forecast"

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


@pytest.mark.parametrize("test_mode", [
    pytest.param(False, id="outside test mode"),
    pytest.param(True, id="under test mode too"),
])
async def test_a_call_that_fell_due_while_the_save_stood_stopped_is_posted_once_it_is_discarded(
    tmp_path, reposts, test_mode,
):
    """Attendance on, its call five days before the round. Pro's round 3 is five days and thirty
    minutes out, and no call stands for it yet: its call is thirty minutes ahead. It is moved a
    day later; the save stops, and forty minutes on, past the call's moment and with its deadline
    still ahead, a league admin discards it. The round stands at its old moment, its call due and
    none standing: the call is posted.

    Under test mode too: the amendment posts it all the same, unlike the start-up recovery,
    which leaves a test season's calls to `/test-mode advance` (owner, 2026-10-09: "Post it
    anyway")."""
    league = await ongoing_league(tmp_path, attendance=True)
    if test_mode:
        await league.set_test_mode(True)
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
    tmp_path, reposts,
):
    """Attendance on, its deadline two hours before the round. Pro's round 3 is two hours and
    five minutes out, its call standing (7001) and its reserves not yet distributed. It is moved
    a day later; the save stops, and ten minutes on, past the old deadline, a league admin
    discards it. The round stands at its old moment, its deadline gone by with its call
    standing: the deadline is run at once, as a restart runs it — the call's buttons taken off
    and one notice of the placements posted in Pro's check-in channel and recorded — and no
    call is posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _deadline_not_yet_run(league)
    before = await _place_and_discard_past_the_deadline(league)

    assert await stopped_job(league.db_path) is None
    assert await _moment(league) == before
    assert ("edit", PRO_CH.checkin, CALL_MESSAGES[0]) in league.events
    [announced] = _sent_in_check_in(league)
    assert await _distribution_recorded(league) == str(announced)
    assert reposts.posted == []


async def _deadline_not_yet_run(league: Any) -> None:
    """Round 3's reserves not yet distributed: no announcement recorded."""
    await league.write(
        "UPDATE rsvp_embed_messages SET distribution_msg_id = NULL WHERE round_id = ?", R3
    )


async def _place_and_discard_past_the_deadline(league: Any) -> datetime:
    """Pro's round 3 two hours and five minutes out is moved a day later; the save stops, and ten
    minutes on, past the old deadline, a league admin discards it. The round's moment before."""
    await _place_r3(league, hours=2, minutes=5)
    before = await _moment(league)
    await _amended(league, scheduled_at=_at(league, days=1, hours=2))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=10)
        await discard_job(league.bot)
    return before


def _sent_in_check_in(league: Any) -> list[int]:
    return [mid for kind, cid, mid in league.events if kind == "send" and cid == PRO_CH.checkin]


async def _distribution_recorded(league: Any) -> Any:
    [row] = await league.rows(
        "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?", R3)
    return row["distribution_msg_id"]


async def test_an_amendment_s_late_reserve_placement_cannot_be_discarded(tmp_path, reposts):
    """Attendance on. Pro's round 3 is two hours and five minutes out, its call standing (7001)
    and its reserves not yet distributed. It is moved a day later; the save stops, and ten
    minutes on, past the old deadline, a league admin discards it, so the round is armed again
    as it stood and its deadline, gone by, is run at once. The save of that job, which places the
    reserves, fails and stops the queue at `distribute`. A league admin's Discard on it is
    refused, saying the reserves of round 3 in Pro would never be placed; the queue stays stopped
    there and nothing is announced. Once the fault is lifted, a Retry places them, takes the
    call's buttons off and posts one notice of the placements, recorded."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _deadline_not_yet_run(league)
    await refuse_the_job(league.db_path, "season.round.amend", "distribute", True)
    await _place_and_discard_past_the_deadline(league)
    assert await _stopped_at(league) == "distribute"

    discarding = await discard_job(league.bot)

    assert (
        "⛔ This job can't be discarded: without it the reserves of round 3 in **Pro** would "
        "never be placed. Fix what stopped it and press **Retry**."
    ) in _said_to(discarding)
    assert await _stopped_at(league) == "distribute"
    assert _sent_in_check_in(league) == []

    await refuse_the_job(league.db_path, "season.round.amend", "distribute", False)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert ("edit", PRO_CH.checkin, CALL_MESSAGES[0]) in league.events
    [announced] = _sent_in_check_in(league)
    assert await _distribution_recorded(league) == str(announced)


async def test_an_amendment_s_deadline_whose_announcement_is_refused_stops_the_queue(
    tmp_path, reposts,
):
    """As the deadline run once its save is discarded (Pro's round 3, its old deadline passed),
    Discord refusing every post in Pro's check-in channel: the announcement of the placements is
    not posted, so the queue stops with nothing recorded, rather than the refusal being logged
    on the host alone."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _deadline_not_yet_run(league)
    reposts.refuse_sends(http_error(discord.Forbidden, status=403, text="Missing Permissions"))

    await _place_and_discard_past_the_deadline(league)

    assert await stopped_job(league.db_path) is not None
    assert await _distribution_recorded(league) is None


async def test_an_amendment_s_deadline_whose_lock_is_refused_stops_the_queue(tmp_path, reposts):
    """As the deadline run once its save is discarded (Pro's round 3, its old deadline passed),
    Discord refusing the edit that takes the call's buttons off (7001): the queue stops there,
    nothing yet announced."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _deadline_not_yet_run(league)
    league.channel(PRO_CH.checkin).messages[CALL_MESSAGES[0]].edit = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions"))

    await _place_and_discard_past_the_deadline(league)

    assert await stopped_job(league.db_path) is not None
    assert _sent_in_check_in(league) == []


async def test_an_amendment_s_clean_up_whose_delete_is_refused_stops_the_queue(tmp_path):
    """Attendance on. Pro's round 3 is thirty minutes out, its call standing (7001 to 7003). It
    is moved a day later; the save stops, and a day and forty minutes on, past the check-in's
    clean-up due a day after the old moment, a league admin discards it. Discord refuses to
    delete the last notice (7002): the queue stops there, the notice still standing and the
    round's check-in not marked over, rather than the refusal being logged on the host alone."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _place_r3(league, minutes=30)
    league.undeletable.add(CALL_MESSAGES[1])
    await _amended(league, scheduled_at=_at(league, days=1))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(days=1, minutes=40)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert CALL_MESSAGES[1] in league.channel(PRO_CH.checkin).messages
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 0


async def test_an_amendment_s_discarded_clean_up_delete_is_named_by_its_link(tmp_path):
    """Attendance on. Pro's round 3 was raced twenty-three hours and fifty-five minutes ago, its
    call standing (7001 to 7003). It is moved to a day from now; the save stops, and ten minutes
    on, past the check-in's clean-up due a day after the old moment, a league admin discards it.
    Discord refuses to delete the last notice (7002) and the queue stops there. A league admin
    discards that too: the reply, still within Discord's fourteen minutes, names the last notice
    by its link, for the admin to open and delete by hand, not by a bare message id."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _place_r3(league, hours=-23, minutes=-55)
    league.undeletable.add(CALL_MESSAGES[1])
    press = await _amended(league, scheduled_at=_at(league, days=1))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=10)
        await discard_job(league.bot)
    assert await _stopped_at(league) == "delete_message"

    await discard_job(league.bot)

    told = _outcome(press)
    named = ("could not be deleted a day after the round, and a league admin discarded it; "
             f"delete it by hand (https://discord.test/{PRO_CH.checkin}/{CALL_MESSAGES[1]})")
    assert named in told, told


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


@pytest.mark.parametrize("standing", [
    pytest.param("phase 2", id="phase 3 never posted"),
    pytest.param("mystery notice", id="a mystery round's notice"),
])
async def test_a_late_clean_up_deletes_whichever_phase_is_still_standing(tmp_path, standing):
    """Weather on. Pro's round 3 is thirty minutes out with one forecast recorded: (phase 3 never
    posted) its Phase 2 forecast (message 8002), or (a mystery round's notice) round 3 a mystery
    round with its notice (message 8000). It is moved to a day from now; the save stops, and a
    day and forty minutes on, past the clean-up due a day after the old moment, a league admin
    discards it. The round stands at its old moment, and its clean-up deletes whichever of Phases
    1 to 3 is still recorded (owner, 2026-10-12): Phase 2 goes, its record with it. A mystery
    round's notice is no such forecast, and stands with its record."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await _place_r3(league, minutes=30)
    forecast = league.channel(PRO_CH.forecast)
    if standing == "phase 2":
        message, phase = 8002, 2
        await posted_forecast(league, R3, 2, message)
    else:
        message, phase = 8000, 0
        await league.write("UPDATE rounds SET format = 'MYSTERY', phase1_done = 1 WHERE id = ?",
                           R3)
        await league.write(
            "INSERT INTO forecast_messages (round_id, division_id, phase_number, message_id, "
            "posted_at) VALUES (?, ?, 0, ?, ?)", R3, PRO, message, league.clock.now.isoformat())
        forecast.seed(message, "Mystery Round notice")
    await _amended(league, scheduled_at=_at(league, days=1))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(days=1, minutes=40)
        await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    recorded = await league.rows(
        "SELECT * FROM forecast_messages WHERE round_id = ? AND phase_number = ?", R3, phase)
    if standing == "phase 2":
        assert message not in forecast.messages
        assert recorded == []
    else:
        assert message in forecast.messages
        assert len(recorded) == 1


async def test_a_late_clean_up_s_forecast_deletion_is_named_as_the_clean_up_names_it(tmp_path):
    """Weather on. Pro's round 3 was raced twenty-three hours and fifty-five minutes ago, its
    results not yet in and its Phase 3 forecast (message 8003) standing. It is moved to a day
    from now; the save stops, and ten minutes on, past the clean-up due a day after the old
    moment, a league admin discards it. The round stands at its old moment, so its Phase 3 forecast is
    to be deleted, as its clean-up deletes it; Discord refuses, and the queue stops, its notice
    naming the job "deleting the Phase 3 forecast of round 3 in **Pro** a day after it". A league
    admin discards that too: the reply, still within Discord's fourteen minutes, names it as the
    Phase 3 forecast that could not be deleted a day after the round, with its link to delete it
    by hand. Neither calls it withdrawn: it is the race's own forecast."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await _place_r3(league, hours=-23, minutes=-55)
    await posted_forecast(league, R3, 3, 8003)
    league.undeletable.add(8003)
    press = await _amended(league, scheduled_at=_at(league, days=1))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=10)
        await discard_job(league.bot)
    assert await _stopped_at(league) == "delete_forecast"

    notices = _stop_notices(league)
    assert any("deleting the Phase 3 forecast of round 3 in **Pro** a day after it" in line
               for line in notices), notices
    assert not any("withdrawn" in line for line in notices), notices

    await discard_job(league.bot)

    outcome = _outcome(press)
    assert NOT_DONE in outcome, outcome
    named = ("**Pro** — forecast channel: the Phase 3 forecast could not be deleted a day after "
             "the round, and a league admin discarded it; delete it by hand (")
    bullets = [line for line in outcome.splitlines() if named in line]
    assert len(bullets) == 1, outcome
    assert "8003" in bullets[0]
    assert "withdrawn" not in outcome
    assert 8003 in league.channel(PRO_CH.forecast).messages


async def test_a_late_clean_up_is_named_by_the_phase_still_standing(tmp_path, monkeypatch):
    """Weather on. Pro's round 3 was raced twenty-three hours and fifty-five minutes ago, its
    Phase 3 never posted, so its Phase 2 forecast (message 8002) stands. It is moved to a day
    from now; the save stops, and ten minutes on, past the clean-up due a day after the old
    moment, a league admin discards it. The round stands at its old moment, so its clean-up is
    due; finding the forecasts to delete fails, and the queue stops, its notice naming the job
    "deleting the Phase 2 forecast of round 3 in **Pro** a day after it". A league admin discards
    that too: the reply names the Phase 2 forecast as not deleted a day after the round. Neither
    names a phase that is not standing."""
    import leaguebot.weather.services.forecast_cleanup_service as cleanup

    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await _place_r3(league, hours=-23, minutes=-55)
    await posted_forecast(league, R3, 2, 8002)
    press = await _amended(league, scheduled_at=_at(league, days=1))
    with _renumbering_fails():
        await run_queue(league.bot)
        assert await _stopped_at(league) == "apply"
        league.clock.advance(minutes=10)
        monkeypatch.setattr(cleanup, "forecast_deletes",
                            AsyncMock(side_effect=RuntimeError("unreadable")))
        await discard_job(league.bot)
    assert await _stopped_at(league) == "clean_up_forecast"

    notices = _stop_notices(league)
    assert any("deleting the Phase 2 forecast of round 3 in **Pro** a day after it" in line
               for line in notices), notices
    assert not any("Phase 3" in line for line in notices), notices

    await discard_job(league.bot)

    outcome = _outcome(press)
    assert ("**Pro** — forecast channel: the Phase 2 forecast was not deleted a day after the "
            "round, and a league admin discarded it; it is deleted when the bot next starts"
            ) in outcome, outcome
    assert 8002 in league.channel(PRO_CH.forecast).messages


@pytest.mark.parametrize("ending", ["discarded", "refused"])
async def test_a_discarded_catch_up_is_named_whatever_became_of_the_save(
    tmp_path, phases, ending,
):
    """Weather on, horizons 5 days, 2 days, 2 hours. Pro's round 3 is two days and five minutes
    out, its Phase 1 drawn, its Phase 2 five minutes ahead. It is brought forward to ten minutes
    out and the save stops. Twelve minutes on, past the old Phase 2 horizon, (discarded) a league
    admin discards the save, or (refused) Retry is pressed and the save refuses it, its new moment
    having passed. The round is armed again at its old moment and its Phase 2, due meanwhile, is
    drawn at once; drawing it fails, stopping the queue at its `draw_forecast` job, and a league admin
    discards it. The reply names it under
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
    assert await _stopped_at(league) == "draw_forecast"

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
               and "not done: drawing the Phase 2 forecast of round 3 in **Pro** now" in line
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
    assert reposts.posted == []
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
    tmp_path, reposts,
):
    """Attendance on, its deadline two hours before the round. Pro's round 3, its call standing,
    has had its reserves distributed (its distribution announced, message 7003). It is moved a
    day later; the save stops, and ten minutes on, past the old deadline, a league admin discards
    it. The deadline has been run: it is not run again — the call is not edited and nothing is
    posted in Pro's check-in channel — and no call is posted."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _passed_while_the_save_stood_stopped(league, minutes=10)

    assert "run_deadline" not in [job["name"] for job in await _jobs(league)]
    assert ("edit", PRO_CH.checkin, CALL_MESSAGES[0]) not in league.events
    assert _sent_in_check_in(league) == []
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
    posted again. Posting it fails, Discord refusing the message or the check-in channel gone:
    the queue stops at `post_call`, and its stop notice says no call stands and that the answers
    to the earlier call are kept and count if this call is posted before the deadline (owner,
    2026-10-11: "Notice says what's left"); the log channel says nothing of a call not posted.
    The answers are kept. A league admin discards the job: the reply names the check-in call and
    `/attendance post-check-in`."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    press = await _amended(league, scheduled_at=_at(league, days=3))
    await _run_through(league, "take_down_call")
    if fault == "Discord refuses the post":
        league.channel(PRO_CH.checkin).send_fails = http_error(
            discord.Forbidden, status=403, text="Missing Permissions"
        )
    else:
        league.remove_channel(PRO_CH.checkin)

    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_call"
    assert (await _change(league))["state"] != "DONE"
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []
    assert len(await league.rows(
        "SELECT * FROM driver_round_attendance WHERE round_id = ?", R3
    )) == 2
    assert any(ANSWERS_KEPT in line for line in _stop_notices(league))
    assert not [line for line in _log_lines(league) if line.startswith(NOT_POSTED)]

    await discard_job(league.bot)

    outcome = _outcome(press)
    assert NOT_DONE in outcome
    assert "**Pro** — check-in call:" in outcome and "`/attendance post-check-in`" in outcome
    assert "no attendance rows were opened" not in outcome


async def test_a_call_not_given_up_because_results_came_in_is_recorded_as_left(
    tmp_path, reposts, caplog,
):
    """Attendance on. Pro's round 3, its call standing, is brought forward to three hours out;
    Discord refuses to delete the last notice (7002), stopping the take-down. While it stands
    stopped the round is raced and its results entered. An hour and ten minutes on, past the
    deadline, the fault is mended and Retry is pressed: there is nothing to give up for a round
    whose results are in, and the job records that it left the call as it was, not that it gave
    it up. The host's log names the give-up, not the start-up recovery, as what did nothing."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _amended(league, scheduled_at=_at(league, hours=3))
    league.undeletable.add(CALL_MESSAGES[1])
    await run_queue(league.bot)
    assert await _stopped_at(league) == "take_down_call"
    await _set_status(league, "AWAITING_REPORT_VERDICTS")
    league.clock.advance(hours=1, minutes=10)
    league.undeletable.clear()

    with caplog.at_level("INFO"):
        await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _job(league, "post_call"))["result"] == {"left": True}
    assert _given_up_lines(league) == []
    said = [record.getMessage() for record in caplog.records
            if "nothing given up" in record.getMessage()]
    assert said and all(not line.startswith("_recover_missed_check_in_calls:") for line in said)


async def test_a_call_posted_again_whose_send_times_out_stops_the_queue_saying_it_may_have_landed(
    tmp_path, monkeypatch,
):
    """Attendance on. Pro's round 3, its call standing, is brought forward to three days out;
    its call is taken down and posted again, and the send times out. The queue stops at
    `post_call`, the amendment not finished, its stop notice saying the call may have reached
    Discord all the same; the log channel says nothing of a call not posted (owner, 2026-10-10,
    decision 3; 2026-10-11, "Notice says what's left"). Once the send goes through, Retry posts
    the call, once, and the amendment finishes."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    await _amended(league, scheduled_at=_at(league, days=3))
    await _run_through(league, "take_down_call")
    league.channel(PRO_CH.checkin).send_fails = asyncio.TimeoutError()

    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_call"
    assert (await _change(league))["state"] != "DONE"
    assert any("it may have reached Discord all the same" in line
               for line in _stop_notices(league))
    assert not [line for line in _log_lines(league) if line.startswith(NOT_POSTED)]

    league.channel(PRO_CH.checkin).send_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    [row] = await league.rows("SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", R3)
    assert int(row["message_id"]) in league.channel(PRO_CH.checkin).messages


async def test_a_call_whose_send_times_out_stops_the_queue_saying_it_will_be_posted_again(
    tmp_path, monkeypatch,
):
    """Attendance on. Pro's round 3, its call standing and answered, is brought forward to three
    days out; its call is taken down and posted again, and the send times out, and times out
    again on Retry. The stop notice, and the line of the Retry that failed, each say that no call
    stands and the answers are kept, and end that the call may have reached Discord all the same:
    the bot posts it again on its next try, and any second copy is to be deleted by hand (owner,
    2026-10-11: "Notice says what's left")."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    await _amended(league, scheduled_at=_at(league, days=3))
    await _run_through(league, "take_down_call")
    league.channel(PRO_CH.checkin).send_fails = asyncio.TimeoutError()

    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_call"
    await retry_job(league.bot)

    assert await _stopped_at(league) == "post_call"
    [stop] = [line for line in _stop_notices(league) if "The queue is stopped at job #" in line]
    retried = [line for line in _stop_notices(league) if line.startswith("❌ Retry of job #")]
    assert retried
    for line in (stop, retried[-1]):
        assert f"{ANSWERS_KEPT}{MAY_HAVE_LANDED}" in line


@pytest.mark.parametrize("fault", ["the call is refused", "the channel is gone"])
async def test_a_call_posted_again_that_discord_refuses_stops_the_queue_and_is_posted_on_retry(
    tmp_path, monkeypatch, fault,
):
    """Attendance on, its call five days before the round. Pro's round 3 has no call standing
    and no answers; an amendment's save stops and is discarded past the call's moment, so the
    amendment is to post the call at once (`_call_to_post_at_once`). Posting it fails, Discord
    refusing it or the check-in channel gone: the queue stops at `post_call`, its stop notice
    saying no call stands and no attendance rows are open until this one is posted; the log
    channel says nothing of a call not posted. Once the cause is cleared, Retry posts the call
    once and the amendment finishes (owner, 2026-10-10, decision 3: "Stop the queue")."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    await league.write("DELETE FROM driver_round_attendance WHERE round_id = ?", R3)
    await _call_to_post_at_once(league)
    checkin = league.channel(PRO_CH.checkin)
    if fault == "the call is refused":
        checkin.send_fails = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    else:
        league.remove_channel(PRO_CH.checkin)

    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_call"
    assert any(NOTHING_LEFT in line for line in _stop_notices(league))
    assert not [line for line in _log_lines(league) if line.startswith(NOT_POSTED)]
    assert await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", R3) == []

    if fault == "the call is refused":
        checkin.send_fails = None
    else:
        league.restore_channel(PRO_CH.checkin)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    [row] = await league.rows("SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", R3)
    assert int(row["message_id"]) in checkin.messages


@pytest.mark.parametrize("left", ["earlier call kept", "no call can be seen", "nothing"])
async def test_a_discarded_call_post_says_what_is_left(tmp_path, monkeypatch, left):
    """Attendance on; Pro's round 3, its call standing (7001 to 7003) and answered, is brought
    forward to three days out, so its call is taken down and posted again. (earlier call kept)
    Discord refuses to delete the call itself and a league admin discards the take-down; (no
    call can be seen) it refuses to delete the last notice, the call itself deleted, and the
    take-down is discarded; (nothing) the take-down goes through. Posting the call again is then
    refused, stopping the queue, and a league admin discards it: the reply's entry for the
    check-in call says what is left — the earlier call kept with its answers counting; none to
    be seen, to post by hand once the cause is cleared; or that the call was not posted again,
    to post with `/attendance post-check-in`.

    Where the take-down was discarded, the log channel says nothing of a call not posted, and
    the reply never bids the manager remove by hand a call that is the round's only one. (earlier
    call kept) The earlier call stands with its answers, its record kept, and the stop notice
    says so. (no call can be seen) The call itself was deleted: its record is dropped, the post
    is recorded as having vanished, the stop notice says none can be seen, and the reply never
    says it was kept (owner, 2026-10-09: "Make it"; 2026-10-11: "Notice says what's left")."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    if left == "earlier call kept":
        league.undeletable.add(CALL_MESSAGES[0])
    elif left == "no call can be seen":
        league.undeletable.add(CALL_MESSAGES[1])
    press = await _amended(league, scheduled_at=_at(league, days=3))
    if left == "nothing":
        await _run_through(league, "take_down_call")
    else:
        await run_queue(league.bot)
        assert await _stopped_at(league) == "take_down_call"
    league.channel(PRO_CH.checkin).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    if left == "nothing":
        await run_queue(league.bot)
    else:
        await discard_job(league.bot)
    assert await _stopped_at(league) == "post_call"
    if left != "nothing":
        assert not [line for line in _log_lines(league) if line.startswith(NOT_POSTED)]
    notices = _stop_notices(league)

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    outcome = _outcome(press)
    assert NOT_DONE in outcome
    rows = await league.rows("SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", R3)
    if left == "earlier call kept":
        assert any(EARLIER_STANDS in line for line in notices)
        assert [row["message_id"] for row in rows] == [str(CALL_MESSAGES[0])]
        assert "removed by hand" not in outcome
    elif left == "no call can be seen":
        assert any(NONE_SEEN in line for line in notices)
        assert rows == []
        assert ((await _job(league, "post_call"))["result"] or {}).get("vanished") is True
        assert "removed by hand" not in outcome
        assert "kept" not in outcome
    said = {
        "earlier call kept": "it could not be posted again, so the earlier call was kept and its "
                             "answers count; see the log channel",
        "no call can be seen": "it could not be posted again and none can be seen; post it by "
                               "hand with `/attendance post-check-in` once the cause is cleared "
                               "(or the check-in channel is set), and see the log channel",
        "nothing": "the call could not be posted again, and a league admin discarded it; post it "
                   "with `/attendance post-check-in`",
    }[left]
    assert f"**Pro** — check-in call: {said}" in outcome


async def test_a_call_whose_check_in_channel_is_unset_stops_the_queue_naming_the_command(
    tmp_path, monkeypatch,
):
    """Attendance on. Pro's round 3, its call standing, is brought forward to three days out;
    its call is taken down, and then Pro's check-in channel is unset before the call is posted
    again. The queue stops at `post_call`, its stop notice naming the command that sets the
    channel; nothing is skipped quietly. Once the channel is set again, Retry posts the call and
    the amendment finishes."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    await _amended(league, scheduled_at=_at(league, days=3))
    await _run_through(league, "take_down_call")
    await league.write(
        "UPDATE attendance_division_config SET rsvp_channel_id = NULL WHERE division_id = ?", PRO
    )

    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_call"
    assert ("**Pro** has no check-in channel set: set one with `/attendance channel rsvp`, then "
            "press Retry. No call stands") in league.log()
    assert "Retry.; " not in league.log()
    assert "run_rsvp_notice | SKIP" not in league.log()

    await league.write(
        "UPDATE attendance_division_config SET rsvp_channel_id = ? WHERE division_id = ?",
        str(PRO_CH.checkin), PRO,
    )
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert (await _change(league))["state"] == "DONE"
    [row] = await league.rows("SELECT message_id FROM rsvp_embed_messages WHERE round_id = ?", R3)
    assert int(row["message_id"]) in league.channel(PRO_CH.checkin).messages


# ── The forecasts an amendment draws at once, as jobs of its own (slice 6) ─────────────


async def _drawn_at_once(tmp_path: Any, *, days: float) -> Any:
    """Weather on, horizons 5 days, 2 days, 2 hours; Pro's round 3 at Silverstone Circuit, with
    a short qualifying and a long race, nothing drawn. It is brought forward to *days* out: at
    three days phase 1 falls due at once, at one day phases 1 and 2. Gives the league and the
    press; the queue is not run."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await league.write("UPDATE rounds SET track_name = 'Silverstone Circuit' WHERE id = ?", R3)
    for session in ("SHORT_QUALIFYING", "LONG_RACE"):
        await league.write("INSERT INTO sessions (round_id, session_type) VALUES (?, ?)",
                           R3, session)
    press = await _amended(league, scheduled_at=_at(league, days=days))
    return league, press


def _forecast_events(league: Any) -> list[tuple[str, int]]:
    """What Pro's forecast channel saw, in order: each send and delete, with its message."""
    return [(kind, mid) for kind, cid, mid in league.events if cid == PRO_CH.forecast]


async def _forecasts(league: Any) -> dict[int, int]:
    """Round 3's forecasts recorded: each phase's message id."""
    return {row["phase_number"]: int(row["message_id"]) for row in await league.rows(
        "SELECT phase_number, message_id FROM forecast_messages WHERE round_id = ?", R3)}


async def test_a_phase_drawn_at_once_whose_forecast_discord_refuses_stops_the_queue(tmp_path):
    """Round 3 brought forward to three days out, so its phase 1 is drawn at once, and Discord
    refuses its forecast: the queue stops at the amendment's `post_forecast` job, the draw
    recorded and nothing put on the old retry queue. Discord accepting, Retry posts it and
    records its id."""
    league, _press = await _drawn_at_once(tmp_path, days=3)
    league.channel(PRO_CH.forecast).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )

    await run_queue(league.bot)

    assert await _stopped_at(league) == "post_forecast"
    [row] = await league.rows("SELECT phase1_done FROM rounds WHERE id = ?", R3)
    assert row["phase1_done"] == 1
    assert _forecast_events(league) == []
    assert await league.rows(
        "SELECT * FROM pending_messages WHERE channel_id = ?", PRO_CH.forecast
    ) == []

    league.channel(PRO_CH.forecast).send_fails = None
    await retry_job(league.bot)

    assert await _stopped_at(league) is None
    [(kind, new)] = _forecast_events(league)
    assert kind == "send"
    assert await _forecasts(league) == {1: new}


async def test_a_phase_drawn_at_once_supersedes_the_earlier_forecast_by_a_delete_job(tmp_path):
    """Round 3 brought forward to one day out, so phases 1 and 2 are drawn at once: phase 1's
    forecast is posted, then phase 2's, and phase 1's is deleted by a `delete_forecast` job after
    it. Only phase 2's forecast is left recorded."""
    league, _press = await _drawn_at_once(tmp_path, days=1)

    await run_queue(league.bot)

    assert await _stopped_at(league) is None
    first, second, deleted = _forecast_events(league)
    assert (first[0], second[0], deleted[0]) == ("send", "send", "delete")
    assert deleted[1] == first[1]
    assert await _forecasts(league) == {2: second[1]}
    weather_jobs = [name for name in await _ran(league)
                    if name in ("rerun_phase", "draw_forecast", "post_forecast", "delete_forecast")]
    assert weather_jobs[-4:] == ["rerun_phase", "draw_forecast", "post_forecast",
                                 "delete_forecast"]


async def test_a_discarded_forecast_post_of_an_amendment_is_named_in_its_reply(tmp_path):
    """Round 3 brought forward to three days out; Discord refuses phase 1's forecast and a
    league admin discards the post: the reply names it under "Not done", the draw standing."""
    league, press = await _drawn_at_once(tmp_path, days=3)
    league.channel(PRO_CH.forecast).send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access"
    )
    await run_queue(league.bot)
    assert await _stopped_at(league) == "post_forecast"

    await discard_job(league.bot)

    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    assert ("**Pro** — Phase 1 forecast: drawn but not posted, and a league admin discarded it; "
            "the draw stands in the log channel, and nothing posts it again") in outcome


async def test_a_superseded_forecast_that_will_not_delete_is_named_as_superseded_once_discarded(
    tmp_path,
):
    """Round 3 brought forward to one day out; phase 1's forecast is posted, and Discord refuses
    to delete it when phase 2's supersedes it: the queue stops at `delete_forecast`. A league
    admin discards it: the reply names the superseded forecast, with its link, to delete by
    hand."""
    league, press = await _drawn_at_once(tmp_path, days=1)
    await _run_through(league, "post_forecast")
    [(_, earlier)] = _forecast_events(league)
    league.channel(PRO_CH.forecast).messages[earlier].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await run_queue(league.bot)
    assert await _stopped_at(league) == "delete_forecast"
    await discard_job(league.bot)

    outcome = _outcome(press)
    assert outcome.startswith(AMENDED) and NOT_DONE in outcome
    named = ("**Pro** — forecast channel: the superseded Phase 1 forecast could not be deleted, "
             "and a league admin discarded it; delete it by hand (")
    [bullet] = [line for line in outcome.splitlines() if named in line]
    assert str(earlier) in bullet


async def test_an_amendment_s_call_saves_its_record_in_the_job(tmp_path, monkeypatch):
    """Attendance on. Pro's round 3, its call standing and answered (Lewis accepted, Max not), is
    brought forward to three days out, so its call is taken down and posted again. The attendance
    service's own committing writers are left as the league's doubles, which write nothing: the
    call posted again is recorded all the same, by the job that posted it, its message the one
    standing in Pro's check-in channel and the round's attendance rows kept, and the job is done
    with its record in one save (slice 6's A21, deferred until now)."""
    league = await ongoing_league(tmp_path, attendance=True)
    _real_call(league, monkeypatch)
    await _amended(league, scheduled_at=_at(league, days=3))

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    sent = [mid for kind, cid, mid in league.events if kind == "send" and cid == PRO_CH.checkin]
    [row] = await league.rows("SELECT message_id, channel_id FROM rsvp_embed_messages "
                              "WHERE round_id = ?", R3)
    assert sent and row["message_id"] == str(sent[-1])
    assert row["message_id"] in {str(mid) for mid in league.channel(PRO_CH.checkin).messages}
    assert row["channel_id"] == str(PRO_CH.checkin)
    assert len(await league.rows(
        "SELECT * FROM driver_round_attendance WHERE round_id = ?", R3
    )) == 2
    league.bot.attendance_service.insert_embed_message.assert_not_called()
    league.bot.attendance_service.bulk_insert_attendance_rows.assert_not_called()


async def test_an_amendment_s_call_posted_again_writes_no_post_check_in_line(tmp_path, reposts):
    """Attendance on. Pro's round 3, its call standing, is brought forward to three days out, so
    its call is taken down and posted again by the amendment's own jobs. The log channel records
    the amendment, and no line saying the amending manager ran `/attendance post-check-in`, a
    command nobody ran (core spec, the record of what changed)."""
    league = await ongoing_league(tmp_path, attendance=True)
    await _amended(league, scheduled_at=_at(league, days=3))

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert reposts.posted == [(R3, PRO)]
    assert len(_success_lines(league)) == 1
    assert [line for line in _log_lines(league) if "/attendance post-check-in" in line] == []
