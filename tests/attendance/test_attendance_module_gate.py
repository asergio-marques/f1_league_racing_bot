"""The attendance module produces nothing while it is switched off — issue #114.

`core_specification.md` — "A disabled module shall produce nothing. While a module is disabled
the bot shall neither compute, record nor post any of that module's output, whatever the path
arrives at it — a scheduled job, a restart, or a command that amends work arranged while the
module was still enabled."

`rsvp_service` carried no module check at all. The three RSVP jobs are booked once, by
the confirmation of placements, and switching attendance off never touched them, so a league that turned
the module off mid-season still got every remaining round's check-in call, its reminder and
its deadline — the last of which moves reserve drivers into seats. A restart made it worse:
the recovery re-armed the buttons and ran any deadline it had missed without asking whether
the module was on.

The fix is a gate at each entry point rather than a job cancellation. Cancelling looks
equivalent and is not: nothing short of the confirmation of placements recreates these jobs and it cannot
be run again on an active season, so a cancel loses the season's check-ins for good — the
mistake issue #117 made for weather, in reverse.

These tests hold the gate at all six ways in — the three job entry points, the distribution
beneath them, the restart recovery and the button a driver presses — and pin the enabled path
beside each, so the gate cannot be over-applied and quietly switch check-in off for everyone.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import AttendanceService

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
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, ?)",
            (1, int(attendance_enabled)),
        )
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
            "INSERT INTO attendance_division_config "
            "(division_id, rsvp_channel_id) VALUES (?, ?)",
            (DIVISION_ID, str(RSVP_CHANNEL_ID)),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, scheduled_at.isoformat()),
        )

        # One full-time team with a vacancy, and a reserve team with one accepted reserve.
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (11, ?, 'Reserve', 'Reserve', 6, 1)",
            (DIVISION_ID,),
        )
        for profile_id, name in ((101, "Full Timer"), (102, "Stand In")):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, discord_user_id, current_state, is_test_driver, test_display_name) "
                "VALUES (?, ?, 'ACTIVE', 1, ?)",
                (profile_id, str(profile_id), name),
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
    """Give the round the attendance rows a posted call would have created. The reserve answered
    two hours ago: three hours before a round an hour off (`_an_hour_off`), so before its
    two-hour deadline, as the deadline places reserves on the answers as they stood then."""
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
                (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
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
# The check-in call and deadline, asked of the change queue (#439, slice 6b, C26, C30)
# ---------------------------------------------------------------------------
#
# Each is a change on the queue: its timer asks it as the bot, and the change's check drops it
# as no longer due while attendance is off, or for a cancelled round, so nothing is computed,
# recorded or posted and the queue does not stop. The round's deadline passed an hour ago where
# a test asks it (the round an hour off, the deadline two hours before it).


def _an_hour_off() -> datetime:
    """The round's start an hour from now: its two-hour deadline passed an hour ago."""
    return datetime.now(timezone.utc) + timedelta(hours=1)


async def _ask(bot: MagicMock, step: str) -> list[int]:
    """Ask the queue for the round's check-in *step* (``call``, ``deadline``) as its timer does,
    as the bot; run the queue; return the ids posted in the check-in channel."""
    import importlib

    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue

    check_in_changes = importlib.import_module("leaguebot.attendance.services.check_in_changes")
    ask = getattr(check_in_changes, f"ask_check_in_{step}")
    await ask(bot.change_queue, bot.db_path, ROUND_ID, origin=ChangeOrigin.BOT)
    await run_queue(bot)
    return [mid for kind, cid, mid in bot.events if kind == "send" and cid == RSVP_CHANNEL_ID]


async def test_no_check_in_call_is_posted_while_attendance_is_disabled(tmp_path):
    """Attendance is off and the round's call is due: the call asked of the queue is dropped,
    nothing posted and no attendance row or call recorded, and the queue does not stop."""
    from tests.support.change_queue import stopped_job

    db_path = await _make_db(tmp_path, attendance_enabled=False)
    bot = _queue_bot(db_path, attendance_enabled=False)

    assert await _ask(bot, "call") == []
    attendance_rows, embed_rows, _ = await _counts(db_path)
    assert attendance_rows == 0, "a disabled module recorded the round's attendance rows"
    assert embed_rows == 0, "a disabled module recorded a posted check-in call"
    assert await stopped_job(db_path) is None


async def test_the_check_in_call_is_posted_while_attendance_is_enabled(tmp_path):
    """Attendance is on: the queue posts the call in the check-in channel, recording it and an
    attendance row for each of the two drivers."""
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    bot = _queue_bot(db_path, attendance_enabled=True)

    assert len(await _ask(bot, "call")) == 1
    attendance_rows, embed_rows, _ = await _counts(db_path)
    assert attendance_rows == 2
    assert embed_rows == 1


# ---------------------------------------------------------------------------
# The last notice — the reminder, asked of the change queue (#439, slice 6b, C29)
# ---------------------------------------------------------------------------
#
# The last notice is a change on the queue: its timer asks it as the bot, and the change's check
# drops it as no longer due while attendance is off, or for a cancelled round, so nothing is
# posted and the queue does not stop. The round's call stands (message 900900 in the check-in
# channel), so the last notice is dropped for the gate alone and not for want of a call.



def _queue_bot(db_path: str, *, attendance_enabled: bool) -> MagicMock:
    """The league's bot double with its change queue, the module answering from the flag under
    test, the check-in channel (880011) recording its sends in `bot.events`."""
    from tests.support.change_queue import attach_queue, league_double
    from tests.support.review_league import channel as recording_channel

    bot = league_double(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.attendance_service = AttendanceService(db_path)
    bot.events = []
    rsvp = recording_channel(RSVP_CHANNEL_ID, bot.events)
    rsvp.seed(900900, "the call")
    known = {bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel, RSVP_CHANNEL_ID: rsvp}
    bot.get_channel = MagicMock(side_effect=lambda cid: known.get(int(cid)))
    bot.fetch_channel = AsyncMock(side_effect=lambda cid: known[int(cid)])
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot


async def _ask_last_notice(bot: MagicMock) -> list[int]:
    """Ask the queue for the round's last notice as its timer does; run the queue; return the
    ids posted in the check-in channel."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_last_notice
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue

    await ask_check_in_last_notice(bot.change_queue, bot.db_path, ROUND_ID,
                                   origin=ChangeOrigin.BOT)
    await run_queue(bot)
    return [mid for kind, cid, mid in bot.events if kind == "send" and cid == RSVP_CHANNEL_ID]


async def test_no_reminder_is_posted_while_attendance_is_disabled(tmp_path):
    from tests.support.change_queue import stopped_job

    db_path = await _make_db(tmp_path, attendance_enabled=False)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    await _seed_embed_message(db_path)
    bot = _queue_bot(db_path, attendance_enabled=False)

    assert await _ask_last_notice(bot) == []
    assert await stopped_job(db_path) is None


async def test_the_reminder_is_posted_while_attendance_is_enabled(tmp_path):
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    await _seed_embed_message(db_path)
    bot = _queue_bot(db_path, attendance_enabled=True)

    assert len(await _ask_last_notice(bot)) == 1


# ---------------------------------------------------------------------------
# run_rsvp_deadline and run_reserve_distribution — the seats
# ---------------------------------------------------------------------------


async def test_the_deadline_distributes_nobody_while_attendance_is_disabled(tmp_path):
    """Attendance is off, the call stands with the reserve's acceptance, and the deadline has
    passed: the deadline asked of the queue is dropped, no reserve placed and nothing posted,
    and the queue does not stop."""
    from tests.support.change_queue import stopped_job

    db_path = await _make_db(tmp_path, attendance_enabled=False, scheduled_at=_an_hour_off())
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _queue_bot(db_path, attendance_enabled=False)

    assert await _ask(bot, "deadline") == []
    _, _, distributed = await _counts(db_path)
    assert distributed == 0, "a disabled module moved a reserve driver into a seat"
    assert await stopped_job(db_path) is None


async def test_the_deadline_distributes_reserves_while_attendance_is_enabled(tmp_path):
    """Attendance is on: the queue places the reserve who accepted into the vacant seat."""
    db_path = await _make_db(tmp_path, attendance_enabled=True, scheduled_at=_an_hour_off())
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _queue_bot(db_path, attendance_enabled=True)

    await _ask(bot, "deadline")

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
    """Attendance is off and the round's deadline passed while the bot was down: the restart
    re-arms no button and asks nothing of the queue, and once the queue runs no reserve is
    placed."""
    from leaguebot.__main__ import _recover_rsvp_views_and_deadlines
    from tests.support.change_queue import change_rows, run_queue

    # A deadline already in the past, so the catch-up would fire were it not gated.
    db_path = await _make_db(tmp_path, attendance_enabled=False, scheduled_at=_an_hour_off())
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _queue_bot(db_path, attendance_enabled=False)

    await _recover_rsvp_views_and_deadlines(bot)
    assert await change_rows(db_path) == [], "a restart asked the queue for a disabled module"
    await run_queue(bot)

    bot.add_view.assert_not_called()
    _, _, distributed = await _counts(db_path)
    assert distributed == 0, "a restart ran a missed deadline for a disabled module"


async def test_a_restart_re_arms_the_buttons_while_attendance_is_enabled(tmp_path):
    """Attendance is on and the round's deadline passed while the bot was down: the restart
    re-arms the call's buttons and asks the queue for the deadline, placing no reserve itself;
    once the queue runs, the reserve who accepted is placed."""
    from leaguebot.__main__ import _recover_rsvp_views_and_deadlines
    from tests.support.change_queue import run_queue

    db_path = await _make_db(tmp_path, attendance_enabled=True, scheduled_at=_an_hour_off())
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    bot = _queue_bot(db_path, attendance_enabled=True)

    await _recover_rsvp_views_and_deadlines(bot)

    bot.add_view.assert_called()
    _, _, distributed = await _counts(db_path)
    assert distributed == 0, "the restart ran the missed deadline itself"
    await run_queue(bot)
    _, _, distributed = await _counts(db_path)
    assert distributed == 1, "the missed deadline did not run for an enabled module"


# ---------------------------------------------------------------------------
# The button on a call that outlived the module
# ---------------------------------------------------------------------------


def _make_interaction(bot: MagicMock) -> MagicMock:
    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.user.id = 101
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.is_done = MagicMock(
        side_effect=lambda: bool(interaction.response.send_message.await_count)
    )
    return interaction


async def test_a_button_press_records_nothing_while_attendance_is_disabled(tmp_path):
    from leaguebot.attendance.cogs.attendance_cog import handle_rsvp_button

    db_path = await _make_db(tmp_path, attendance_enabled=False)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    bot = _make_bot(db_path, attendance_enabled=False)
    interaction = _make_interaction(bot)

    await handle_rsvp_button(interaction, f"rsvp_accept_r{ROUND_ID}")

    interaction.response.send_message.assert_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "switched off" in reply

    async with get_connection(db_path) as db:
        cur = await db.execute(
            "SELECT rsvp_status FROM driver_round_attendance "
            " WHERE round_id = ? AND driver_profile_id = 101",
            (ROUND_ID,),
        )
        status = (await cur.fetchone())["rsvp_status"]
    assert status == "DECLINED", "a disabled module recorded a check-in answer"


# ---------------------------------------------------------------------------
# The test-mode bulk check-in
# ---------------------------------------------------------------------------


async def test_test_mode_sets_no_check_in_status_while_attendance_is_disabled(tmp_path):
    """A call posted before the module went off leaves its embed row behind.

    Without a gate the command finds that row and writes check-in answers for a module the
    league has switched off — the same defect as the button, by a maintainer's route.
    """
    from leaguebot.attendance.cogs.attendance_cog import AttendanceCog

    db_path = await _make_db(tmp_path, attendance_enabled=False)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    await _seed_embed_message(db_path)

    cog = AttendanceCog.__new__(AttendanceCog)
    cog.bot = _make_bot(db_path, attendance_enabled=False)
    config = MagicMock()
    config.test_mode_active = True
    cog.bot.config_service.get_server_config = AsyncMock(return_value=config)

    interaction = _make_interaction(cog.bot)
    interaction.response.send_modal = AsyncMock()

    # The permission tier is `league_admin_only`'s business and has its own tests; call the
    # command beneath it so this test pins the module gate and nothing else.
    await AttendanceCog.test_rsvp.callback.__wrapped__(cog, interaction, "Division 1")

    interaction.response.send_modal.assert_not_awaited()
    reply = interaction.response.send_message.await_args.args[0]
    assert "Attendance module is not enabled" in reply


# ---------------------------------------------------------------------------
# A round recorded as cancelled — the same three jobs (#175)
# ---------------------------------------------------------------------------
#
# Cancelling a round unschedules its jobs, so ordinarily none of these fires at all. The gate
# is what holds when one survives: the scheduler's removal swallows what it raises, the job
# store is durable and outlives a restart, and the cancellation now takes the call down — a
# surviving job would post a call, a reminder or a distribution for a round that is off, and
# write back the row that says a call is standing.


async def _cancel_the_round(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", (ROUND_ID,))
        await db.commit()


async def test_no_check_in_call_is_posted_for_a_cancelled_round(tmp_path):
    """The round is cancelled: the call asked of the queue is dropped, nothing posted or
    recorded."""
    db_path = await _make_db(tmp_path, attendance_enabled=True)
    await _cancel_the_round(db_path)
    bot = _queue_bot(db_path, attendance_enabled=True)

    assert await _ask(bot, "call") == []
    attendance_rows, embed_rows, _ = await _counts(db_path)
    assert attendance_rows == 0
    assert embed_rows == 0, "a cancelled round recorded a check-in call as standing"


async def test_no_reminder_is_posted_for_a_cancelled_round(tmp_path):
    from tests.support.change_queue import stopped_job

    db_path = await _make_db(tmp_path, attendance_enabled=True)
    await _seed_rsvp_rows(db_path, reserve_accepted=False)
    await _seed_embed_message(db_path)
    await _cancel_the_round(db_path)
    bot = _queue_bot(db_path, attendance_enabled=True)

    assert await _ask_last_notice(bot) == []
    assert await stopped_job(db_path) is None


async def test_the_deadline_distributes_nobody_for_a_cancelled_round(tmp_path):
    """The round is cancelled after its call: the deadline asked of the queue is dropped, no
    reserve placed and nothing posted."""
    db_path = await _make_db(tmp_path, attendance_enabled=True, scheduled_at=_an_hour_off())
    await _seed_rsvp_rows(db_path)
    await _seed_embed_message(db_path)
    await _cancel_the_round(db_path)
    bot = _queue_bot(db_path, attendance_enabled=True)

    assert await _ask(bot, "deadline") == []
    _, _, distributed = await _counts(db_path)
    assert distributed == 0, "a cancelled round moved a reserve driver into a seat"
