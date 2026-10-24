"""Arming a round's timed work again after a refused cancellation (#439, slice 8, F2).

A round's, a division's or a season's cancellation removes the timed work of its rounds before
its save. Where the save then refuses the cancellation (a session accepted on the round's
submission meanwhile), each round still to be run or awaiting its results is armed again by a
change of its own, `season.round.rearm`, asked by the bot: it is judged as the round stands when
it runs, arms the round's timed work as an amended round's arming does, and catches up what fell
due meanwhile; a round already raced has its next-day clean-ups armed again alone. Neither its
judging nor its arming can be discarded.

Every test drives the real queue through `tests/support/season_league.py`'s `ongoing_league`,
with "now" pinned, and asks the re-arming directly as the refused cancellation asks it. Pro's
round 3 (id 213) is NOT_RUN 90 days out unless a test moves it.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.season_league import (
    CALL_MESSAGES,
    DIVISIONS,
    PRO,
    ongoing_league,
    posted_forecast,
    round_id,
)

PRO_CH = DIVISIONS[PRO][3]
R3, R4 = round_id(PRO, 3), round_id(PRO, 4)
HORIZONS = (5, 2, 2)
REARM_KIND = "season.round.rearm"
UNBUILT = "#439: no change arms a round's timed work again after a refused cancellation yet"
ARMED_AGAIN = (
    "⏱️ The timed work of round 3 in **Pro** is armed again, `/round cancel` having been refused"
)
CANNOT_DISCARD = (
    "⛔ This job can't be discarded: without it round 3 in **Pro** would be left with no timed "
    "work, never run. Fix what stopped it and press **Retry**."
)


# ── Helpers ─────────────────────────────────────────────────────────────────────────


async def _ask_rearm(league: Any, rid: int = R3, number: int = 3, *,
                     cleanups_only: bool = False) -> int | None:
    """Ask, as the bot, for round *number* of Pro (id *rid*) to be armed again, as a refused
    `/round cancel` asks it; with *cleanups_only*, its clean-ups alone, as for a raced round."""
    from leaguebot.core.models.change import ChangeOrigin

    payload: dict[str, Any] = {
        "round_id": rid, "round_number": number, "division_id": PRO, "division_name": "Pro",
        "command": "/round cancel",
    }
    if cleanups_only:
        payload["cleanups_only"] = True
    return await league.bot.change_queue.ask(
        REARM_KIND, payload, what=f"arming the timed work of round {number} in **Pro** again",
        origin=ChangeOrigin.BOT,
    )


async def _rearmings(league: Any) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == REARM_KIND]


async def _place(league: Any, rid: int = R3, **delta: float) -> None:
    """Round *rid* stands *delta* from "now", stored as plain UTC as production stores it."""
    await league.write(
        "UPDATE rounds SET scheduled_at = ? WHERE id = ?",
        (league.clock.now + timedelta(**delta)).replace(tzinfo=None).isoformat(), rid,
    )


async def _stopped_at(league: Any) -> str | None:
    job = await stopped_job(league.db_path)
    return job["name"] if job else None


def _real_attendance(league: Any, monkeypatch: Any) -> None:
    """Attendance's own service in place of the league's double, drawing no check-in picture,
    so that a call posted is posted and recorded as attendance posts and records it."""
    from leaguebot.attendance.services.attendance_service import AttendanceService
    from leaguebot.image.services import image_rsvp_post

    monkeypatch.setattr(image_rsvp_post, "try_attach", AsyncMock(return_value=None))
    attendance = AttendanceService(league.db_path)
    for name in dir(AttendanceService):
        if not name.startswith("_") and callable(getattr(AttendanceService, name)):
            setattr(league.bot.attendance_service, name, getattr(attendance, name))


async def _calls_posted(league: Any) -> list[int]:
    """The rounds whose check-in call was sent to Pro's check-in channel and recorded."""
    sent = {mid for kind, cid, mid in league.events if kind == "send" and cid == PRO_CH.checkin}
    rows = await league.rows("SELECT round_id, message_id FROM rsvp_embed_messages")
    return [row["round_id"] for row in rows if int(row["message_id"]) in sent]


@pytest.fixture
def phases(monkeypatch):
    """Weather's draw of each phase, recorded as `(phase, round id)` in `ran`; a phase in
    `failing` raises its fault instead (`tests/support/weather_draws.py`)."""
    from tests.support.weather_draws import recorded_draws

    return recorded_draws(monkeypatch)


# ── A round still to be run ─────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_re_arming_arms_the_round_as_it_stands_and_catches_up_the_phases_and_the_call_that_fell_due(
    tmp_path, phases, monkeypatch,
):
    """Weather on (horizons 5 days, 2 days, 2 hours) and attendance on (the call five days before
    the round, the deadline two hours before). Pro's round 3 is a day and 23 hours out, no phase
    drawn and no call standing: its Phase 1 and Phase 2 horizons and its call's moment passed
    while its timed work stood removed, its Phase 3 and its deadline still ahead. Its re-arming
    runs: its forecasts and its check-in are armed again, Phases 1 and 2 are drawn at once, the
    call is posted and recorded, and nothing stops the queue."""
    league = await ongoing_league(tmp_path, weather=True, attendance=True, horizons=HORIZONS)
    _real_attendance(league, monkeypatch)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", R3)
    await _place(league, days=1, hours=23)

    assert await _ask_rearm(league) is not None
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _rearmings(league)] == ["DONE"]
    assert ("weather", [R3]) in league.armed
    assert ("attendance", [R3]) in league.armed
    assert sorted(phases.ran) == [(1, R3), (2, R3)]
    assert await _calls_posted(league) == [R3]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_re_arming_of_a_round_whose_moment_passed_runs_its_results_submission_at_once(
    tmp_path,
):
    """Pro's round 3, still to be run, was due ten minutes ago, its timed work removed. Its
    re-arming arms its results submission and runs it at once, rather than leave it to a job
    armed for a moment already past."""
    league = await ongoing_league(tmp_path)
    await _place(league, minutes=-10)

    await _ask_rearm(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert ("results", [R3]) in league.armed
    opened = league.bot.scheduler_service.run_result_submission_now
    assert [call.args[0].id for call in opened.call_args_list] == [R3]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_re_arming_under_test_mode_runs_no_results_submission_at_once(tmp_path):
    """Under test mode, Pro's round 3, still to be run, was due ten minutes ago. Its re-arming
    arms its results submission but does not run it at once: `/test-mode advance` runs a test
    season's rounds in their turn, as for an amended round."""
    league = await ongoing_league(tmp_path, test_mode=True)
    await league.set_test_mode(True)
    await _place(league, minutes=-10)

    await _ask_rearm(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert ("results", [R3]) in league.armed
    league.bot.scheduler_service.run_result_submission_now.assert_not_called()


@pytest.mark.parametrize("case", [
    pytest.param("cancelled", marks=pytest.mark.xfail(strict=True, reason=UNBUILT)),
    pytest.param("results entered", marks=pytest.mark.xfail(strict=True, reason=UNBUILT)),
    pytest.param("gone", marks=pytest.mark.xfail(strict=True, reason=UNBUILT)),
])
async def test_a_re_arming_whose_round_can_no_longer_be_cancelled_is_dropped(tmp_path, case):
    """Pro's round 4's re-arming is asked; before it runs, (cancelled) a later cancellation
    records the round cancelled, (results entered) its results are entered and it awaits its
    report verdicts, or (gone) the round is deleted. When the queue runs, the re-arming is
    dropped as no longer due: nothing is armed and nothing stops the queue."""
    league = await ongoing_league(tmp_path)
    assert await _ask_rearm(league, R4, 4) is not None
    if case == "cancelled":
        await league.write("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", R4)
    elif case == "results entered":
        await league.write("UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = ?", R4)
    else:
        await league.write("DELETE FROM rounds WHERE id = ?", R4)

    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _rearmings(league)] == ["DROPPED"]
    assert league.armed == []


# ── It cannot be discarded ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("job", [
    pytest.param("judge", marks=pytest.mark.xfail(strict=True, reason=UNBUILT)),
    pytest.param("arm", marks=pytest.mark.xfail(strict=True, reason=UNBUILT)),
])
async def test_the_re_arming_cannot_be_discarded(tmp_path, job):
    """Pro's round 3's re-arming stops the queue: (judge) the windows it is judged by cannot be
    read, or (arm) the scheduler refuses to arm it. A league admin's Discard on the stopped job is
    refused, saying round 3 in **Pro** would be left with no timed work, never run; the queue
    stays stopped at it and nothing is armed. Once the fault is mended, a Retry arms the round."""
    league = await ongoing_league(tmp_path)
    assert await _ask_rearm(league) is not None
    windows = league.bot.amendment_windows
    if job == "judge":
        def _unreadable() -> Any:
            raise RuntimeError("database is locked")

        league.bot.amendment_windows = _unreadable
    else:
        league.arming_fails = RuntimeError("job store unavailable")
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == job

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == CANNOT_DISCARD
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert league.armed == []

    league.bot.amendment_windows = windows
    league.arming_fails = None
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _rearmings(league)] == ["DONE"]
    assert league.armed == [("results", [R3])]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_re_arming_stopped_at_its_check_cannot_be_discarded_whole(tmp_path):
    """Pro's round 3's re-arming is asked; when it comes up, the rounds cannot be read, so its
    check fails and the queue stops on it before any job has run. A league admin's Discard, which
    would drop the whole change, is refused as a Discard of its first job is: the queue stays
    stopped on it and nothing is armed. Once the rounds can be read again, a Retry arms the
    round."""
    league = await ongoing_league(tmp_path)
    assert await _ask_rearm(league) is not None
    await league.write("ALTER TABLE rounds RENAME TO rounds_unreadable")
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "judge"

    refused = await discard_job(league.bot)

    assert "can't be discarded" in acknowledgement(refused)
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert [row["state"] for row in await _rearmings(league)] != ["DISCARDED"]
    assert league.armed == []

    await league.write("ALTER TABLE rounds_unreadable RENAME TO rounds")
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _rearmings(league)] == ["DONE"]
    assert league.armed == [("results", [R3])]


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_discarded_catch_up_of_a_re_arming_is_named_in_the_log(tmp_path, phases):
    """Weather on (horizons 5 days, 2 days, 2 hours). Pro's round 3 is three days out, its Phase 1
    never drawn, so its re-arming draws Phase 1 at once; the draw fails on a fault of the bot's
    own and a league admin discards it. The log channel says, in one line, that the timed work of
    round 3 in **Pro** is armed again, `/round cancel` having been refused, and names the Phase 1
    forecast as not drawn, to be drawn when the bot next starts."""
    league = await ongoing_league(tmp_path, weather=True, horizons=HORIZONS)
    await _place(league, days=3)
    phases.failing[1] = RuntimeError("forecast could not be drawn")
    await _ask_rearm(league)
    await run_queue(league.bot)
    assert await _stopped_at(league) == "draw_forecast"

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    lines = [line for line in league.bot.log_channel.sent if ARMED_AGAIN in line]
    assert len(lines) == 1, league.bot.log_channel.sent
    assert ("**Pro** — Phase 1 forecast: not drawn, and a league admin discarded it; it is drawn "
            "when the bot next starts, while the round is still to be run") in lines[0]


# ── A round already raced ───────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_raced_round_s_re_arming_arms_its_clean_ups_alone(tmp_path):
    """Weather and attendance on. Pro's round 3 was raced two hours ago and awaits its report
    verdicts, its timed work removed. Its re-arming, asked for its clean-ups alone, arms the
    forecast's and the check-in's clean-ups due a day after its start, and nothing else: no
    forecast, no check-in and no results submission is armed or run."""
    league = await ongoing_league(tmp_path, weather=True, attendance=True, horizons=HORIZONS)
    await league.write("UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = ?", R3)
    await _place(league, hours=-2)

    assert await _ask_rearm(league, cleanups_only=True) is not None
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert [row["state"] for row in await _rearmings(league)] == ["DONE"]
    assert league.armed == [("cleanups", [R3])]
    league.bot.scheduler_service.run_result_submission_now.assert_not_called()


@pytest.mark.xfail(strict=True, reason=UNBUILT)
async def test_a_raced_round_s_clean_ups_already_due_run_at_once(tmp_path):
    """Weather and attendance on. Pro's round 3 was raced a day and an hour ago and awaits its
    report verdicts, its Phase 3 forecast (message 8003) posted and its check-in call standing
    (7001 to 7003): its clean-ups fell due an hour ago while its timed work stood removed. Its
    re-arming, asked for its clean-ups alone, runs them at once, as a restart does: the forecast
    is deleted, the call taken down and the round's check-in marked over."""
    league = await ongoing_league(tmp_path, weather=True, attendance=True, horizons=HORIZONS)
    await league.write("UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = ?", R3)
    await _place(league, days=-1, hours=-1)
    await posted_forecast(league, R3, 3, 8003)

    await _ask_rearm(league, cleanups_only=True)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert 8003 not in league.channel(PRO_CH.forecast).messages
    checkin = league.channel(PRO_CH.checkin)
    assert all(mid not in checkin.messages for mid in CALL_MESSAGES)
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", R3)
    assert row["checkin_cleared"] == 1
