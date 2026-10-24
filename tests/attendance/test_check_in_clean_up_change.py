"""A round's check-in clean-up on the change queue (#439, slice 6b, C32): asked by its timer a
day after the round and by the restart, run by jobs of its own — `plan` reads the messages the
round's check-in left recorded, one `delete_message` takes each down from the channel it was
posted in, and `mark_cleared` marks the round's check-in over.

The league is `ongoing_league`'s with attendance on, its queue rebuilt on a real
`AttendanceService`: Pro's check-in channel is 603, and round 3's call stands there as message
7001 with its last notice 7002 and its reserve announcement 7003, all three recorded on the call.
Round 3 is moved to 25 hours ago, so its clean-up is due.

A test asks the clean-up of the queue as its timer does, as the bot (`ask_check_in_clean_up`),
and runs the queue. A refused deletion stops the queue; a Discard names the message for deletion
by hand (Q9, as a cancellation's does); a message already gone completes its deletion.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    refuse_the_job,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.season_league import (
    CALL_MESSAGES,
    DIVISIONS,
    PRO,
    SeasonLeague,
    ongoing_league,
    round_id,
)

KIND = "attendance.check_in.clean_up"
CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_3 = round_id(PRO, 3)
ROUND_4 = round_id(PRO, 4)
CALL, LAST_NOTICE, ANNOUNCEMENT = CALL_MESSAGES
#: Round 4's call and last notice, standing in Pro's check-in channel beside round 3's.
ROUND_4_CALL, ROUND_4_NOTICE = 7101, 7102


async def _league(tmp_path: Any) -> SeasonLeague:
    """The league of the module docstring: round 3 raced 25 hours ago, its call, last notice
    and announcement standing and recorded."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now - timedelta(hours=25)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)
    return league


async def _ask(league: SeasonLeague) -> None:
    """Ask the queue for round 3's clean-up as its timer does; the queue is not run."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_clean_up
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_clean_up(league.bot.change_queue, league.db_path, ROUND_3,
                                origin=ChangeOrigin.BOT)


def _deleted(league: SeasonLeague) -> list[int]:
    """The ids of the messages deleted from Pro's check-in channel, in order."""
    return [mid for kind, ch, mid in league.events if kind == "delete" and ch == CHECK_IN]


async def _call_rows(league: SeasonLeague, rid: int = ROUND_3) -> list[dict[str, Any]]:
    return await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", rid)


async def _cleared(league: SeasonLeague, rid: int = ROUND_3) -> bool:
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", rid)
    return bool(row["checkin_cleared"])


async def _clean_up_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def test_the_timer_asks_for_the_clean_up_and_the_queue_deletes_each_message(tmp_path):
    """Nothing is deleted as the clean-up is asked. The queue runs `plan`, one `delete_message`
    for each of the call (7001), the last notice (7002) and the announcement (7003), and
    `mark_cleared`: all three are gone from Pro's check-in channel, the call's record with them,
    and the queue is not stopped. The clean-up timer's callback asks the queue rather than
    running the clean-up."""
    import leaguebot.__main__ as bot_module

    league = await _league(tmp_path)

    await _ask(league)
    assert _deleted(league) == []
    await run_queue(league.bot)

    assert sorted(_deleted(league)) == sorted(CALL_MESSAGES)
    assert all(mid not in league.channel(CHECK_IN).messages for mid in CALL_MESSAGES)
    assert await _call_rows(league) == []
    [change] = await _clean_up_changes(league)
    assert [step["name"] for step in await step_rows(league.db_path, change["id"])] == [
        "plan", "delete_message", "delete_message", "delete_message", "mark_cleared",
    ]
    assert await stopped_job(league.db_path) is None
    assert "ask_check_in_clean_up(" in inspect.getsource(bot_module.main)


async def test_a_refused_delete_stops_the_queue_and_a_discard_names_it_for_deletion_by_hand(
    tmp_path,
):
    """Q9: Discord refuses to delete the last notice (7002). The queue stops at its
    `delete_message`, the notice still standing. A league admin discards that job: the queue
    runs on, the log channel's line names message 7002 to be deleted by hand, by its link, the
    announcement (7003) is deleted, and the round's check-in is marked over."""
    league = await _league(tmp_path)
    league.undeletable.add(LAST_NOTICE)

    await _ask(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "delete_message"
    assert LAST_NOTICE in league.channel(CHECK_IN).messages
    told_before = league.log()

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    told = league.log()[len(told_before):]
    assert f"https://discord.test/{CHECK_IN}/{LAST_NOTICE}" in told, told
    assert ANNOUNCEMENT not in league.channel(CHECK_IN).messages
    assert await _cleared(league)


async def test_a_message_already_gone_completes_its_delete(tmp_path):
    """The announcement (7003) was deleted by hand before the queue reached the clean-up: its
    deletion finds it gone and completes, the queue does not stop, and the call and last notice
    are deleted and the record dropped all the same."""
    league = await _league(tmp_path)
    del league.channel(CHECK_IN).messages[ANNOUNCEMENT]

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert sorted(_deleted(league)) == sorted((CALL, LAST_NOTICE))
    assert await _call_rows(league) == []
    assert await _cleared(league)


async def test_the_round_is_marked_cleared_after_its_deletes(tmp_path):
    """Discord refuses to delete the call (7001): the queue stops there with round 3's check-in
    not yet marked over. Discord then allows it and a league admin presses Retry: the call, the
    last notice and the announcement are deleted, and only then is the round marked cleared."""
    league = await _league(tmp_path)
    league.undeletable.add(CALL)

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is not None
    assert not await _cleared(league)

    league.undeletable.discard(CALL)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert all(mid not in league.channel(CHECK_IN).messages for mid in CALL_MESSAGES)
    assert await _cleared(league)


MARK_CLEARED_UNDISCARDABLE = "#439: marking a round's check-in over may still be discarded"


@pytest.mark.xfail(strict=True, reason=MARK_CLEARED_UNDISCARDABLE)
async def test_marking_the_check_in_over_cannot_be_discarded(tmp_path):
    """Round 3's clean-up in Pro deletes its call (7001), last notice (7002) and announcement
    (7003), then the save of its last job, which marks the round's check-in over, fails and stops
    the queue at `mark_cleared`. A league admin's Discard on it is refused, saying the check-in of
    round 3 in Pro would never be marked over, and the next restart would delete every attendance
    answer of the round; the queue stays stopped there and the round is not marked. Once the fault
    is lifted, a Retry marks it over."""
    league = await _league(tmp_path)
    await refuse_the_job(league.db_path, KIND, "mark_cleared", True)
    await _ask(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "mark_cleared"

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == (
        "⛔ This job can't be discarded: without it the check-in of round 3 in **Pro** would never "
        "be marked over, and the next restart would delete every attendance answer of the round. "
        "Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert not await _cleared(league)

    await refuse_the_job(league.db_path, KIND, "mark_cleared", False)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _cleared(league)


async def test_the_start_up_asks_for_each_clean_up_missed(tmp_path):
    """The bot starts again with round 3's clean-up a day past and round 4's call standing for a
    race tomorrow. The restart asks the queue for round 3's clean-up and not round 4's, and
    deletes nothing itself."""
    from leaguebot.__main__ import _recover_missed_cleanups

    league = await _league(tmp_path)
    tomorrow = league.clock.now + timedelta(days=1)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       tomorrow.replace(tzinfo=None).isoformat(), ROUND_4)
    await league.write(
        "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
        "posted_at) VALUES (?, ?, ?, ?, ?)",
        ROUND_4, PRO, str(ROUND_4_CALL), str(CHECK_IN), league.clock.now.isoformat(),
    )
    asked: list[int] = []

    async def _record(_queue: Any, _db_path: Any, rid: int, **_kwargs: Any) -> None:
        asked.append(rid)

    with patch("leaguebot.attendance.services.check_in_changes.ask_check_in_clean_up",
               new=AsyncMock(side_effect=_record)):
        await _recover_missed_cleanups(league.bot)

    assert asked == [ROUND_3]
    assert _deleted(league) == []


async def test_the_clean_up_leaves_the_other_round_of_a_double_header_standing(tmp_path):
    """Round 4 races tomorrow, its call (7101) and last notice (7102) standing in Pro's check-in
    channel beside round 3's. Round 3's clean-up deletes round 3's three messages only: round 4's
    stand, its record stands, and round 4 is not marked cleared."""
    league = await _league(tmp_path)
    tomorrow = league.clock.now + timedelta(days=1)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       tomorrow.replace(tzinfo=None).isoformat(), ROUND_4)
    await league.write(
        "INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, "
        "posted_at, last_notice_msg_id) VALUES (?, ?, ?, ?, ?, ?)",
        ROUND_4, PRO, str(ROUND_4_CALL), str(CHECK_IN), league.clock.now.isoformat(),
        str(ROUND_4_NOTICE),
    )
    for message_id in (ROUND_4_CALL, ROUND_4_NOTICE):
        league.channel(CHECK_IN).seed(message_id, "Round 4 check-in")

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    assert sorted(_deleted(league)) == sorted(CALL_MESSAGES)
    assert {ROUND_4_CALL, ROUND_4_NOTICE} <= set(league.channel(CHECK_IN).messages)
    assert len(await _call_rows(league, ROUND_4)) == 1
    assert not await _cleared(league, ROUND_4)
