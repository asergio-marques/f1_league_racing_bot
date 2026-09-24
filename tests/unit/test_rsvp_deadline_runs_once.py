"""A round's check-in deadline runs once for each call (#429).

Two runs of one deadline can meet. The scheduler runs a job overdue by less than its five-minute
misfire grace as soon as it starts, and the restart's catch-up reaches the same deadline moments
later; two presses of `/test-mode advance` can do the same. What says a deadline has run — the
message it records on the call — is written at its very end, so neither could tell the other was
running, and the division was sent two announcements.

`run_rsvp_deadline` now runs under a lock of its round's and reads the call again once it holds
it. `test_two_deadline_runs_at_once_post_one_announcement` interleaves two runs for real: the
first to fetch the call waits for the second to arrive, so without the lock both reach the post.
A deadline whose message never posted has not run, and runs again, which is how a failed
announcement is retried.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord

from db.database import get_connection, run_migrations
from services import rsvp_service
from services.attendance_service import AttendanceService

SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 429
CHANNEL_ID = 770001
CALL_MSG_ID = 900901
POSTED_MSG_ID = 990099


async def _make_db(
    tmp_path, *, with_call: bool = True, distribution_msg_id: str | None = None
) -> str:
    """A division with its check-in channel set, and a round whose call is standing."""
    db_path = os.path.join(str(tmp_path), "deadline_once.db")
    await run_migrations(db_path)
    scheduled = datetime.now(timezone.utc) + timedelta(hours=1)
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
        if with_call:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at, "
                " distribution_msg_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ROUND_ID,
                    DIVISION_ID,
                    str(CALL_MSG_ID),
                    str(CHANNEL_ID),
                    datetime.now(timezone.utc).isoformat(),
                    distribution_msg_id,
                ),
            )
        await db.commit()
    return db_path


def _make_channel(*, wait_for_second_fetch: bool = False) -> MagicMock:
    """A check-in channel holding the call, recording what is posted to it.

    With *wait_for_second_fetch*, the first run to fetch the call waits — for up to a second —
    until a second run fetches it too. Two runs that do not exclude each other therefore both
    reach the post; two that do, the second waiting on the first, carry on after the timeout.
    """
    channel = MagicMock()
    call = MagicMock()
    call.edit = AsyncMock()
    fetches = 0
    second_arrived = asyncio.Event()

    async def _fetch(_message_id: int) -> MagicMock:
        nonlocal fetches
        fetches += 1
        if fetches >= 2:
            second_arrived.set()
        elif wait_for_second_fetch:
            try:
                await asyncio.wait_for(second_arrived.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        return call

    channel.fetch_message = AsyncMock(side_effect=_fetch)
    posted = MagicMock()
    posted.id = POSTED_MSG_ID
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


async def _recorded(db_path: str) -> str | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return row["distribution_msg_id"] if row is not None else None


async def test_two_deadline_runs_at_once_post_one_announcement(tmp_path):
    """The scheduler's late run and the restart's catch-up, side by side."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel(wait_for_second_fetch=True)
    bot = _make_bot(db_path, channel)

    await asyncio.gather(
        rsvp_service.run_rsvp_deadline(ROUND_ID, bot),
        rsvp_service.run_rsvp_deadline(ROUND_ID, bot),
    )

    assert channel.send.await_count == 1, "two runs of one deadline both posted"
    assert await _recorded(db_path) == str(POSTED_MSG_ID)


async def test_a_deadline_whose_message_is_recorded_does_nothing(tmp_path):
    """It has run. The call is not redrawn and nothing is posted."""
    db_path = await _make_db(tmp_path, distribution_msg_id=str(POSTED_MSG_ID))
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    channel.fetch_message.assert_not_awaited()
    channel.send.assert_not_awaited()


async def test_a_deadline_whose_message_never_posted_is_run_again(tmp_path):
    """A failed announcement records nothing, so the deadline has not run: the next run posts
    it. The lock must not turn a retry into a refusal."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)
    posted = channel.send.return_value
    channel.send = AsyncMock(
        side_effect=[discord.HTTPException(MagicMock(status=500), "Discord failing"), posted]
    )

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)
    assert await _recorded(db_path) is None

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    assert channel.send.await_count == 2
    assert await _recorded(db_path) == str(POSTED_MSG_ID)


async def test_a_deadline_with_no_call_standing_posts_nothing(tmp_path):
    """A deadline closes the call standing, and with none there is nothing to close. It used to
    distribute and post its notice anyway, with no call to record the message on — so nothing
    took the notice down a day after the round, and it stood in the channel for good."""
    db_path = await _make_db(tmp_path, with_call=False)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    channel.send.assert_not_awaited()
