"""The attendance module produces nothing while it is switched off — issue #114.

`core_specification.md` — "A disabled module shall produce nothing. While a module is disabled
the bot shall neither compute, record nor post any of that module's output, whatever the path
arrives at it — a scheduled job, a restart, or a command that amends work arranged while the
module was still enabled."

`rsvp_service` carried no module check at all. The three RSVP jobs are booked once, by
`/season approve`, and switching attendance off never touched them, so a league that turned
the module off mid-season still got every remaining round's check-in call, its reminder and
its deadline — the last of which moves reserve drivers into seats. A restart made it worse:
the recovery re-armed the buttons and ran any deadline it had missed without asking whether
the module was on.

The fix is a gate at each entry point rather than a job cancellation. Cancelling looks
equivalent and is not: nothing short of `/season approve` recreates these jobs and it cannot
be run again on an active season, so a cancel loses the season's check-ins for good — the
mistake issue #117 made for weather, in reverse.

These tests hold the gate at all six ways in — the three job entry points, the distribution
beneath them, the restart recovery and the button a driver presses — and pin the enabled path
beside each, so the gate cannot be over-applied and quietly switch check-in off for everyone.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import rsvp_service  # noqa: E402
from services.attendance_service import AttendanceService  # noqa: E402

SERVER_ID = 7714
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 1
RSVP_CHANNEL_ID = 880011
ACTOR_ID = 4242


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, attendance_enabled: bool, scheduled_at: datetime | None = None) -> str:
    """A migrated DB holding one server, season, division, round and a two-team roster.

    *scheduled_at* defaults to a time far enough ahead that the notice window has opened but
    the deadline has not passed. It is computed from the real clock rather than pinned to a
    date, so the test cannot rot into passing.
    """
    db_path = os.path.join(str(tmp_path), "attendance_gate.db")
    await run_migrations(db_path)

    if scheduled_at is None:
        scheduled_at = datetime.now(timezone.utc) + timedelta(days=3)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (server_id, module_enabled) VALUES (?, ?)",
            (SERVER_ID, int(attendance_enabled)),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO attendance_division_config "
            "(division_id, server_id, rsvp_channel_id) VALUES (?, ?, ?)",
            (DIVISION_ID, SERVER_ID, str(RSVP_CHANNEL_ID)),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, scheduled_at.isoformat()),
        )

        # One full-time team with a vacancy, and a reserve team with one accepted reserve.
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, max_seats, is_reserve) "
            "VALUES (11, ?, 'Reserve', 6, 1)",
            (DIVISION_ID,),
        )
        for profile_id, name in ((101, "Full Timer"), (102, "Stand In")):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, server_id, discord_user_id, current_state, is_test_driver, test_display_name) "
                "VALUES (?, ?, ?, 'ACTIVE', 1, ?)",
                (profile_id, SERVER_ID, str(profile_id), name),
            )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, 10, 1, 101)"
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (21, 11, 1, 102)"
        )
        for profile_id in (101, 102):
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id) VALUES (?, ?, ?)",
                (profile_id, SEASON_ID, DIVISION_ID),
            )
        await db.commit()
    return db_path


async def _seed_rsvp_rows(db_path: str, *, reserve_accepted: bool = True) -> None:
    """Give the round the attendance rows a posted call would have created."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status) VALUES (?, ?, 101, 'DECLINED')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status, accepted_at) "
            "VALUES (?, ?, 102, ?, ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                "ACCEPTED" if reserve_accepted else "NO_RSVP",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def _seed_embed_message(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '900900', ?, ?)",
            (ROUND_ID, DIVISION_ID, str(RSVP_CHANNEL_ID), datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()


def _make_channel() -> MagicMock:
    channel = MagicMock()
    message = MagicMock()
    message.id = 900900
    message.channel.id = RSVP_CHANNEL_ID
    message.edit = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    channel.fetch_message = AsyncMock(return_value=message)
    return channel


def _make_bot(db_path: str, *, attendance_enabled: bool) -> MagicMock:
    """A bot double whose module_service answers from the flag under test."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.attendance_service = AttendanceService(db_path)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.get_channel = MagicMock(return_value=_make_channel())
    bot.add_view = MagicMock()
    return bot


async def _counts(db_path: str) -> tuple[int, int, int]:
    """Return ``(attendance_rows, embed_rows, distributed_rows)``."""
    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_round_attendance WHERE round_id = ?", (ROUND_ID,)
        )
        attendance_rows = (await cur.fetchone())["n"]
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        embed_rows = (await cur.fetchone())["n"]
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_round_attendance "
            " WHERE round_id = ? AND (assigned_team_id IS NOT NULL OR is_standby = 1)",
            (ROUND_ID,),
        )
        distributed = (await cur.fetchone())["n"]
    return attendance_rows, embed_rows, distributed


# ---------------------------------------------------------------------------
# run_rsvp_notice — the check-in call
# ---------------------------------------------------------------------------


async def test_no_check_in_call_is_posted_while_attendance_is_disabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    bot = _make_bot(db_path, attendance_enabled=False)

    await rsvp_service.run_rsvp_notice(ROUND_ID, bot)

    bot.get_channel.assert_not_called()
    attendance_rows, embed_rows, _ = await _counts(db_path)
    assert attendance_rows == 0, "a disabled module recorded the round's attendance rows"
    assert embed_rows == 0, "a disabled module recorded a posted check-in call"


async def test_the_check_in_call_is_posted_while_attendance_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    bot = _make_bot(db_path, attendance_enabled=True)
    channel = bot.get_channel.return_value

    with patch.object(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None)):
        await rsvp_service.run_rsvp_notice(ROUND_ID, bot)

    channel.send.assert_awaited()
    attendance_rows, embed_rows, _ = await _counts(db_path)
    assert attendance_rows == 2
    assert embed_rows == 1


# ---------------------------------------------------------------------------
# run_rsvp_last_notice — the reminder
# ---------------------------------------------------------------------------


async def test_no_reminder_is_posted_while_attendance_is_disabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    bot = _make_bot(db_path, attendance_enabled=False)

    await rsvp_service.run_rsvp_last_notice(ROUND_ID, bot)

    bot.get_channel.assert_not_called()


async def test_the_reminder_is_posted_while_attendance_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    bot = _make_bot(db_path, attendance_enabled=True)
    channel = bot.get_channel.return_value

    await rsvp_service.run_rsvp_last_notice(ROUND_ID, bot)

    channel.send.assert_awaited()


# ---------------------------------------------------------------------------
# run_rsvp_deadline and run_reserve_distribution — the seats
# ---------------------------------------------------------------------------


async def test_the_deadline_distributes_nobody_while_attendance_is_disabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _make_bot(db_path, attendance_enabled=False)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    bot.get_channel.assert_not_called()
    _, _, distributed = await _counts(db_path)
    assert distributed == 0, "a disabled module moved a reserve driver into a seat"


async def test_the_deadline_distributes_reserves_while_attendance_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _make_bot(db_path, attendance_enabled=True)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    _, _, distributed = await _counts(db_path)
    assert distributed == 1


async def test_distribution_writes_no_seats_while_attendance_is_disabled(tmp_path):
    """The gate is repeated on the distribution itself, beneath its only caller."""
    db_path = await _make_db(tmp_path, attendance_enabled=False)
    await _seed_rsvp_rows(db_path)
    bot = _make_bot(db_path, attendance_enabled=False)

    placed = await rsvp_service.run_reserve_distribution(ROUND_ID, DIVISION_ID, bot)

    assert placed is False
    _, _, distributed = await _counts(db_path)
    assert distributed == 0


async def test_distribution_writes_seats_while_attendance_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    await _seed_rsvp_rows(db_path)
    bot = _make_bot(db_path, attendance_enabled=True)

    placed = await rsvp_service.run_reserve_distribution(ROUND_ID, DIVISION_ID, bot)

    assert placed is True
    _, _, distributed = await _counts(db_path)
    assert distributed == 1


# ---------------------------------------------------------------------------
# The restart recovery
# ---------------------------------------------------------------------------
#
# async def throughout — these construct a discord.ui.View, and apt's discord.py 2.5.0 calls
# asyncio.get_running_loop() in View.__init__ where the pinned 2.7.1 defers it.


async def test_a_restart_re_arms_no_buttons_while_attendance_is_disabled(tmp_path):
    from bot import _recover_rsvp_views_and_deadlines

    # A deadline already in the past, so the catch-up would fire were it not gated.
    db_path = await _make_db(
        tmp_path,
        attendance_enabled=False,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _make_bot(db_path, attendance_enabled=False)

    await _recover_rsvp_views_and_deadlines(bot)

    bot.add_view.assert_not_called()
    _, _, distributed = await _counts(db_path)
    assert distributed == 0, "a restart ran a missed deadline for a disabled module"


async def test_a_restart_re_arms_the_buttons_while_attendance_is_enabled(tmp_path):
    from bot import _recover_rsvp_views_and_deadlines

    db_path = await _make_db(
        tmp_path,
        attendance_enabled=True,
        scheduled_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _make_bot(db_path, attendance_enabled=True)

    await _recover_rsvp_views_and_deadlines(bot)

    bot.add_view.assert_called()
    _, _, distributed = await _counts(db_path)
    assert distributed == 1, "the missed deadline did not run for an enabled module"


