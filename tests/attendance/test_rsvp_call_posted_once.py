"""A round carries one check-in call, however many posters reach it at once (#429).

The scheduler's job, run late inside its misfire grace when the bot starts, the restart's late
post of a call missed while the bot was down, `/attendance post-check-in` and `/test-mode advance`
can all reach one round together. The row saying a call stands is written only after the post, so
none of them could see another about to post, and two calls went out: the second overwrote the
first's id, leaving a live call in the channel that drivers could answer and nothing tracked.

Each now asks the change queue for the call (#439, slice 6b), which carries it out one change at a
time: two asks of one round's call while the first waits make one change (the queue's repeat rule),
and a call found standing when its change runs is not posted again.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    attach_queue,
    change_rows,
    league_double,
    run_queue,
    stopped_job,
)

SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 429
CHANNEL_ID = 770001
POSTED_MSG_ID = 900901


async def _make_db(tmp_path, *, call_standing: bool = False) -> str:
    """A division with its check-in channel set and one seated driver, and a round ahead."""
    db_path = os.path.join(str(tmp_path), "call_once.db")
    await run_migrations(db_path)
    scheduled = datetime.now(timezone.utc) + timedelta(days=2)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(CHANNEL_ID)),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, scheduled.isoformat()),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (201, '201', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, 10, 1, 201)"
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (201, ?, ?, 20)",
            (SEASON_ID, DIVISION_ID),
        )
        if call_standing:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at) "
                "VALUES (?, ?, '800800', ?, ?)",
                (ROUND_ID, DIVISION_ID, str(CHANNEL_ID), datetime.now(timezone.utc).isoformat()),
            )
        await db.commit()
    return db_path


def _make_channel() -> MagicMock:
    channel = MagicMock()
    posted = MagicMock()
    posted.id = POSTED_MSG_ID
    posted.channel.id = CHANNEL_ID
    channel.id = CHANNEL_ID
    channel.send = AsyncMock(return_value=posted)
    return channel


def _make_bot(db_path: str, channel: MagicMock) -> MagicMock:
    """The league's bot double with its change queue, attendance on and images off, the check-in
    channel among its channels."""
    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    known = {CHANNEL_ID: channel, bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=lambda cid: known.get(int(cid)))
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot


async def _ask(bot: MagicMock) -> None:
    """Ask the queue for the round's call as its timer and the restart do."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_call(bot.change_queue, bot.db_path, ROUND_ID, origin=ChangeOrigin.BOT)


async def _calls_recorded(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cursor.fetchone())["n"]


NO_CALL_CHANGE = (
    "#439: the check-in call is posted by `run_rsvp_notice` at once, off the change queue, and "
    "its failure reported rather than stopping the queue"
)


@pytest.mark.xfail(strict=True, reason=NO_CALL_CHANGE)
async def test_two_calls_posted_at_once_for_one_round_post_one(tmp_path):
    """The scheduler's late run and the restart's late post both ask for the round's call before
    the queue runs: one change is asked, and one call posted and recorded."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _ask(bot)
    await _ask(bot)
    await run_queue(bot)

    assert channel.send.await_count == 1, "two posters put two calls in the channel"
    assert await _calls_recorded(db_path) == 1
    assert [row["kind"] for row in await change_rows(db_path)] == ["attendance.check_in.call"]


@pytest.mark.xfail(strict=True, reason=NO_CALL_CHANGE)
async def test_a_call_already_standing_is_not_posted_again(tmp_path):
    """A second call beside the first would divide the division's answers between them: the
    change finds the call standing when it runs, posts nothing and does not stop the queue."""
    db_path = await _make_db(tmp_path, call_standing=True)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _ask(bot)
    await run_queue(bot)

    channel.send.assert_not_awaited()
    assert await _calls_recorded(db_path) == 1
    assert await stopped_job(db_path) is None
