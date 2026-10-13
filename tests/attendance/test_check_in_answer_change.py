"""A driver's check-in answer on the change queue (#439, slice 6b, C35): a press of Accept,
Tentative or Decline asks an `attendance.check_in.answer` change, whose jobs save the answer
(`save_answer`) and redraw the call (`redraw`) when its turn comes.

The league is `ongoing_league`'s with attendance on, its queue rebuilt on a real
`AttendanceService`: Pro has Lewis and Max seated at Ferrari, its check-in channel is 603, and
round 3's call stands there as message 7001, Lewis having accepted and Max not answered. Round 3
is moved to three days off, well before its two-hour check-in deadline, unless a test says
otherwise. A test presses a button of the call as a driver (`handle_rsvp_button`, the press
carrying the moment it was made) and runs the queue.

The press is acknowledged privately as under way and the acknowledgement updated with its
outcome (decision 8 of the slice). The answer is judged against the locks at the moment it was
pressed, not when its turn comes (Q10; Constitution XIII). A burst of answers to one call makes
one redraw; a refused redraw stops the queue with the answer saved (Q4); a call gone completes
its redraw.

Everything of the change is imported inside a test, so this file collects while it is unbuilt.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.attendance.cogs.attendance_cog import handle_rsvp_button
from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    http_error,
    member,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    updated_reply,
)
from tests.support.review_league import block_queue
from tests.support.season_league import (
    CALL_MESSAGES,
    DIVISIONS,
    LEWIS,
    MAX,
    PRO,
    SeasonLeague,
    ongoing_league,
    profile_id,
    round_id,
)

KIND = "attendance.check_in.answer"
CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_3 = round_id(PRO, 3)
CALL = CALL_MESSAGES[0]

NO_ANSWER_CHANGE = (
    "#439: a check-in answer is saved and the call redrawn at once by `handle_rsvp_button`, off "
    "the change queue"
)


async def _league(tmp_path: Any, *, race_in: timedelta = timedelta(days=3)) -> SeasonLeague:
    """The league of the module docstring, round 3 raced *race_in* from now."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + race_in
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)
    return league


async def _press(league: SeasonLeague, user_id: int, action: str, *,
                 pressed_at: Any = None) -> Any:
    """*user_id* presses *action* ("accept", "tentative" or "decline") on round 3's call at
    *pressed_at* (now where not given); the queue is not run. Gives the interaction."""
    name = {LEWIS: "Lewis", MAX: "Max"}[user_id]
    interaction = member_interaction(
        league.bot, user=member(user_id, name),
        created_at=pressed_at or league.clock.now,
    )
    await handle_rsvp_button(interaction, f"rsvp_{action}_r{ROUND_3}")
    return interaction


async def _answer(league: SeasonLeague, user_id: int) -> str | None:
    rows = await league.rows(
        "SELECT rsvp_status FROM driver_round_attendance WHERE round_id = ? "
        "AND driver_profile_id = ?", ROUND_3, profile_id(user_id),
    )
    return rows[0]["rsvp_status"] if rows else None


def _redraws(league: SeasonLeague) -> list[int]:
    """The ids of the messages edited in Pro's check-in channel, in order."""
    return [mid for kind, ch, mid in league.events if kind == "edit" and ch == CHECK_IN]


async def _answer_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_CHANGE)
async def test_an_answer_is_saved_and_the_call_redrawn_when_its_turn_comes(tmp_path):
    """Max presses Accept. Nothing is saved or redrawn as he presses; the queue saves his answer
    as accepted and then redraws the call (7001), in that order."""
    league = await _league(tmp_path)

    await _press(league, MAX, "accept")
    assert await _answer(league, MAX) == "NO_RSVP"
    assert _redraws(league) == []
    await run_queue(league.bot)

    assert await _answer(league, MAX) == "ACCEPTED"
    assert _redraws(league) == [CALL]
    [change] = await _answer_changes(league)
    assert [step["name"] for step in await step_rows(league.db_path, change["id"])] == [
        "save_answer", "redraw",
    ]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_CHANGE)
async def test_an_answer_pressed_before_the_lock_counts_though_it_runs_after(tmp_path):
    """Round 3 races in three hours, so its check-in locks in one. Max presses Decline now; the
    queue reaches it two hours later, past the lock. Nothing is saved as he presses; his answer is
    judged at the press, so the queue saves it as declined and redraws the call."""
    league = await _league(tmp_path, race_in=timedelta(hours=3))

    await _press(league, MAX, "decline")
    assert await _answer(league, MAX) == "NO_RSVP"
    league.clock.advance(hours=2)
    await run_queue(league.bot)

    assert await _answer(league, MAX) == "DECLINED"
    assert _redraws(league) == [CALL]


async def test_an_answer_pressed_after_the_lock_is_refused(tmp_path):
    """Round 3 races in an hour, so its check-in locked an hour ago. Max presses Accept: he is
    told the RSVP deadline has passed, his answer stays unanswered, the call is not redrawn, and
    nothing is left on the queue for it."""
    league = await _league(tmp_path, race_in=timedelta(hours=1))

    interaction = await _press(league, MAX, "accept")
    await run_queue(league.bot)

    replied = acknowledgement(interaction) + "\n" + updated_reply(interaction)
    assert "The RSVP deadline has passed" in replied
    assert await _answer(league, MAX) == "NO_RSVP"
    assert _redraws(league) == []
    assert await stopped_job(league.db_path) is None
    assert all(row["state"] != "QUEUED" for row in await _answer_changes(league))


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_CHANGE)
async def test_a_burst_of_answers_makes_one_redraw(tmp_path):
    """Max presses Accept, then Decline, and Lewis presses Tentative, all before the queue runs.
    Each answer is saved — Max's last one declined, Lewis tentative — and the call is redrawn
    once, by the last of them."""
    league = await _league(tmp_path)

    await _press(league, MAX, "accept")
    await _press(league, MAX, "decline")
    await _press(league, LEWIS, "tentative")
    await run_queue(league.bot)

    assert await _answer(league, MAX) == "DECLINED"
    assert await _answer(league, LEWIS) == "TENTATIVE"
    assert _redraws(league) == [CALL]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_CHANGE)
async def test_a_refused_redraw_stops_the_queue_with_the_answer_saved(tmp_path):
    """Discord refuses every edit of the call (7001). Max presses Accept: his answer is saved,
    and the queue stops at the redraw."""
    league = await _league(tmp_path)
    league.channel(CHECK_IN).messages[CALL].edit = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )

    await _press(league, MAX, "accept")
    await run_queue(league.bot)

    assert await _answer(league, MAX) == "ACCEPTED"
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "redraw"


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_CHANGE)
async def test_a_call_gone_completes_the_redraw_with_a_line(tmp_path):
    """The call (7001) was deleted by hand. Max presses Accept: his answer is saved, the redraw
    finds the call gone and completes, and the queue does not stop."""
    league = await _league(tmp_path)
    del league.channel(CHECK_IN).messages[CALL]

    await _press(league, MAX, "accept")
    await run_queue(league.bot)

    assert await _answer(league, MAX) == "ACCEPTED"
    assert await stopped_job(league.db_path) is None
    [change] = await _answer_changes(league)
    [redraw] = [step for step in await step_rows(league.db_path, change["id"])
                if step["name"] == "redraw"]
    assert redraw["done_at"] is not None


@pytest.mark.xfail(strict=True, reason=NO_ANSWER_CHANGE)
async def test_an_answer_is_acknowledged_privately_and_updated_and_kept_while_the_queue_is_stopped(
    tmp_path,
):
    """The queue is stopped at another job. Max presses Accept: he is answered privately at once
    that his answer is under way, and nothing is saved or redrawn while the queue is stopped.
    Once the stop is cleared his answer is saved, the call redrawn, and his reply updated to say
    his RSVP is now Accepted."""
    league = await _league(tmp_path)
    holder = await block_queue(league)

    interaction = await _press(league, MAX, "accept")
    await run_queue(league.bot)

    ack = acknowledgement(interaction)
    assert ack and "Your RSVP has been updated" not in ack
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert await _answer(league, MAX) == "NO_RSVP"
    assert _redraws(league) == []

    holder["fail"] = False
    await retry_job(league.bot)

    assert await _answer(league, MAX) == "ACCEPTED"
    assert _redraws(league) == [CALL]
    assert "Accepted" in updated_reply(interaction)
