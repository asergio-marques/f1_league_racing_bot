"""A round carries one check-in call, however many posters reach it at once (#429).

The scheduler's job, run late inside its misfire grace when the bot starts, the restart's late
post of a call missed while the bot was down, `/attendance post-check-in` and `/test-mode advance`
can all reach one round together. The row saying a call stands is written only after the post, so
none of them could see another about to post, and two calls went out: the second overwrote the
first's id, leaving a live call in the channel that drivers could answer and nothing tracked.

`run_rsvp_notice` now checks for a standing call itself, under the round's check-in lock.
`test_two_calls_posted_at_once_for_one_round_post_one` interleaves two posters for real: the
first to reach the check-in graphic waits for the second to arrive, so without the lock both post.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import AttendanceService

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
    channel.send = AsyncMock(return_value=posted)
    return channel


def _make_bot(db_path: str, channel: MagicMock) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.get_channel = MagicMock(return_value=channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


def _graphic_waiting_for_a_second_poster():
    """Stands in for the check-in graphic, the last thing drawn before the post.

    The first poster to reach it waits — for up to a second — until a second poster reaches it
    too. Two posters that do not exclude each other therefore both post; two that do, the second
    waiting on the first, carry on after the timeout.
    """
    arrivals = 0
    second_arrived = asyncio.Event()

    async def _attachment(*_args, **_kwargs):
        nonlocal arrivals
        arrivals += 1
        if arrivals >= 2:
            second_arrived.set()
        else:
            try:
                await asyncio.wait_for(second_arrived.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        return None

    return _attachment


async def _calls_recorded(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cursor.fetchone())["n"]


async def test_two_calls_posted_at_once_for_one_round_post_one(tmp_path):
    """The scheduler's late run and the restart's late post, side by side."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    with patch.object(rsvp_service, "_checkin_attachment", _graphic_waiting_for_a_second_poster()):
        await asyncio.gather(
            rsvp_service.run_rsvp_notice(ROUND_ID, bot),
            rsvp_service.run_rsvp_notice(ROUND_ID, bot),
        )

    assert channel.send.await_count == 1, "two posters put two calls in the channel"
    assert await _calls_recorded(db_path) == 1


async def test_a_call_already_standing_is_not_posted_again(tmp_path):
    """A second call beside the first would divide the division's answers between them."""
    db_path = await _make_db(tmp_path, call_standing=True)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    with patch.object(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None)):
        await rsvp_service.run_rsvp_notice(ROUND_ID, bot)

    channel.send.assert_not_awaited()
