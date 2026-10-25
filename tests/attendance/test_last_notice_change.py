"""A round's last check-in notice on the change queue (#439, slice 6b, C29): asked by its timer,
posted and recorded by a job of its own.

The league is `ongoing_league`'s with attendance on, its queue rebuilt on a real
`AttendanceService`: Pro (role 801) has Lewis and Max seated at Ferrari, its check-in channel is
603, and round 3's call stands there as message 7001, Lewis having accepted and Max not answered.
Round 3 is moved to three hours off, past the moment its last notice falls due and an hour before
its check-in deadline (two hours before the race), and no last notice or reserve announcement is
recorded for it yet. A test asks the last notice of the queue as its timer does, as the bot
(`ask_check_in_last_notice`), and runs the queue.

The last notice goes to the division's check-in channel as it stands when the job runs
(attendance spec :106; decision 13 of the slice), and is recorded with the channel it was posted
in, so that the round's clean-up deletes it from there. One reached late is posted while the
deadline is still ahead and dropped after it (Q6); one whose round has no call standing when it
runs is dropped (fault 8). Discord refusing it stops the queue.
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any

import discord
import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    attach_queue,
    change_rows,
    http_error,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import block_queue, channel
from tests.support.season_league import (
    DIVISIONS,
    PRO,
    SeasonLeague,
    ongoing_league,
    round_id,
)

KIND = "attendance.check_in.last_notice"
CHECK_IN = DIVISIONS[PRO][3].checkin
#: The check-in channel Pro's is moved to.
MOVED = 608
ROUND_3 = round_id(PRO, 3)
LEWIS_MENTION, MAX_MENTION = "<@101>", "<@102>"


async def _league(tmp_path: Any) -> SeasonLeague:
    """The league of the module docstring: round 3 three hours off, its call standing and no
    last notice recorded."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + timedelta(hours=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)
    await league.write(
        "UPDATE rsvp_embed_messages SET last_notice_msg_id = NULL, distribution_msg_id = NULL "
        "WHERE round_id = ?", ROUND_3,
    )
    return league


async def _ask(league: SeasonLeague) -> None:
    """Ask the queue for round 3's last notice as its timer does; the queue is not run."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_last_notice
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_last_notice(league.bot.change_queue, league.db_path, ROUND_3,
                                   origin=ChangeOrigin.BOT)


def _sent(league: SeasonLeague, cid: int = CHECK_IN) -> list[int]:
    """The ids of the messages posted in channel *cid*, in order."""
    return [mid for kind, ch, mid in league.events if kind == "send" and ch == cid]


async def _call_row(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3)
    return row


async def _notice_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


async def _move_check_in_channel(league: SeasonLeague) -> None:
    """`/attendance channel rsvp` has moved Pro's check-in channel to `MOVED`."""
    moved = league._recording(channel(MOVED, league.events))
    moved.guild = league.guild
    league.channels[MOVED] = moved
    await league.write(
        "UPDATE attendance_division_config SET rsvp_channel_id = ? WHERE division_id = ?",
        str(MOVED), PRO,
    )


async def test_the_timer_asks_for_the_last_notice_and_the_queue_posts_it_in_the_division_s_check_in_channel(
    tmp_path,
):
    """Nothing is posted as the last notice is asked; the queue posts it in Pro's check-in
    channel, naming Max, who has not answered, and not Lewis, who has. The last-notice timer's
    callback asks the queue rather than posting."""
    import leaguebot.__main__ as bot_module

    league = await _league(tmp_path)

    await _ask(league)
    assert _sent(league) == []
    await run_queue(league.bot)

    [posted] = _sent(league)
    text = league.texts(CHECK_IN)[-1]
    assert MAX_MENTION in text and LEWIS_MENTION not in text
    assert posted in league.channel(CHECK_IN).messages
    assert [row["kind"] for row in await _notice_changes(league)] == [KIND]
    assert await stopped_job(league.db_path) is None
    assert "ask_check_in_last_notice(" in inspect.getsource(bot_module.main)


async def test_a_last_notice_after_a_channel_move_goes_to_the_new_channel_and_is_recorded_there(
    tmp_path,
):
    """Pro's check-in channel moved to 608 after round 3's call was posted in 603: the last
    notice goes to 608 and is recorded there, and the round's clean-up, a day after the race,
    deletes it from 608, the call from 603."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_clean_up
    from leaguebot.core.models.change import ChangeOrigin

    league = await _league(tmp_path)
    await _move_check_in_channel(league)

    await _ask(league)
    await run_queue(league.bot)

    assert _sent(league) == []
    [posted] = _sent(league, MOVED)
    row = await _call_row(league)
    assert (row["last_notice_msg_id"], row["last_notice_channel_id"]) == (str(posted), str(MOVED))

    when = league.clock.now - timedelta(hours=25)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)
    await ask_check_in_clean_up(league.bot.change_queue, league.db_path, ROUND_3,
                                origin=ChangeOrigin.BOT)
    await run_queue(league.bot)

    deletes = {(cid, mid) for kind, cid, mid in league.events if kind == "delete"}
    assert (MOVED, posted) in deletes
    assert (CHECK_IN, posted) not in deletes
    assert posted not in league.channel(MOVED).messages


async def test_the_last_notice_s_id_and_channel_are_saved_with_its_mark(tmp_path):
    """The notice's message id and the channel it was posted in are saved on round 3's call, and
    the posting job is marked done."""
    league = await _league(tmp_path)

    await _ask(league)
    await run_queue(league.bot)

    [posted] = _sent(league)
    row = await _call_row(league)
    assert (row["last_notice_msg_id"], row["last_notice_channel_id"]) == (
        str(posted), str(CHECK_IN),
    )
    [change] = await _notice_changes(league)
    [post] = [step for step in await step_rows(league.db_path, change["id"])
              if step["name"] == "post_last_notice"]
    assert post["done_at"] is not None


async def test_a_refused_last_notice_stops_the_queue(tmp_path):
    """Discord refuses the notice: the queue stops at it and nothing is recorded. Once Discord
    takes it, the queue's next try posts and records it."""
    league = await _league(tmp_path)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")

    await _ask(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "post_last_notice"
    assert (await _call_row(league))["last_notice_msg_id"] is None

    league.channel(CHECK_IN).send_fails = None
    league.clock.advance(minutes=1)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [posted] = _sent(league)
    assert (await _call_row(league))["last_notice_msg_id"] == str(posted)


async def test_a_last_notice_reached_after_the_deadline_is_dropped(tmp_path):
    """Q6: the notice is asked an hour before the deadline, but the queue reaches it only 70
    minutes on, past the deadline: nothing is posted or recorded, and the queue does not stop."""
    league = await _league(tmp_path)

    await _ask(league)
    assert len(await _notice_changes(league)) == 1
    league.clock.advance(minutes=70)
    await run_queue(league.bot)

    assert _sent(league) == []
    assert (await _call_row(league))["last_notice_msg_id"] is None
    assert await stopped_job(league.db_path) is None


async def test_a_last_notice_reached_late_before_the_deadline_is_posted(tmp_path):
    """Q6: the queue is stopped at another job when the notice is asked, and cleared 30 minutes
    on, with the deadline still half an hour ahead: the notice is posted late and recorded."""
    from tests.support.change_queue import retry_job

    league = await _league(tmp_path)
    holder = await block_queue(league)

    await _ask(league)
    league.clock.advance(minutes=30)
    holder["fail"] = False
    await retry_job(league.bot)

    [posted] = _sent(league)
    assert (await _call_row(league))["last_notice_msg_id"] == str(posted)
    assert await stopped_job(league.db_path) is None


async def test_a_last_notice_with_no_call_standing_is_dropped(tmp_path):
    """Fault 8, held when the job runs: the notice is asked while round 3's call stands, and the
    call is taken off the record before the queue reaches it. Nothing is posted, and the queue
    does not stop."""
    league = await _league(tmp_path)

    await _ask(league)
    assert len(await _notice_changes(league)) == 1
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3)
    await run_queue(league.bot)

    assert _sent(league) == []
    assert await stopped_job(league.db_path) is None
