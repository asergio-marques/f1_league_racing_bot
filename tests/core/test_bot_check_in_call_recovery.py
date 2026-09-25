"""What a restart does about a check-in call that fell due while the bot was down (#429).

The call's job is one of those the scheduler discards when the bot comes back more than its
five-minute misfire grace late, and nothing else posted it: the round asked nobody whether they
were racing, opened no attendance rows, and counted nothing against anyone — while the core
specification said a restart recovered it.

Decided 2026-09-24: the call is posted late, provided the round's check-in deadline is still
ahead, and the drivers answer in the time left; the late post is written to the log channel.
Nothing is posted while test mode is on, `/test-mode advance` posting a test season's calls in
their turn.

`_recover_missed_check_in_calls` takes *now*, so every test here pins the moment alongside the
round it seeds.
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import leaguebot.__main__ as bot_module  # noqa: E402
from leaguebot.__main__ import _recover_missed_check_in_calls  # noqa: E402
from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.attendance.services import rsvp_service  # noqa: E402
from leaguebot.attendance.services.attendance_service import AttendanceService  # noqa: E402

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 429
CHANNEL_ID = 770001
POSTED_MSG_ID = 900901


async def _make_db(
    tmp_path,
    *,
    until_round: timedelta = timedelta(days=2),
    deadline_hours: int = 2,
    attendance_enabled: bool = True,
    season_status: str = "ACTIVE",
    round_status: str = "NOT_RUN",
    checkin_cleared: bool = False,
    call_standing: bool = False,
    test_mode: bool = False,
) -> str:
    """A division with one seated driver and its check-in channel set, and one round.

    The call is due five days before the round, its deadline *deadline_hours* before it. At the
    default two days out, the call's moment has passed and the deadline is still ahead.
    """
    db_path = os.path.join(str(tmp_path), "late_call.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) "
            "VALUES (1, 900, 100, 101, ?)",
            (int(test_mode),),
        )
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours) "
            "VALUES (1, ?, 5, 0, ?)",
            (int(attendance_enabled), deadline_hours),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 3, '2026-09-01', ?)",
            (SEASON_ID, season_status),
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
            "scheduled_at, status, checkin_cleared) "
            "VALUES (?, ?, 4, 'NORMAL', 'Silverstone Circuit', ?, ?, ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                (NOW + until_round).isoformat(),
                round_status,
                int(checkin_cleared),
            ),
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
                (ROUND_ID, DIVISION_ID, str(CHANNEL_ID), NOW.isoformat()),
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


def _make_bot(db_path: str, channel: MagicMock | None = None) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.get_channel = MagicMock(return_value=channel if channel is not None else _make_channel())
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _recover_with_the_real_call(bot) -> None:
    """Run the recovery, posting the call as the bot does, without drawing its graphic."""
    with patch.object(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None)):
        await _recover_missed_check_in_calls(bot, now=NOW)


async def _posted(db_path: str, **kwargs) -> list[int]:
    """Run the recovery with the call itself stubbed, returning the rounds it was asked for."""
    asked: list[int] = []

    async def _notice(round_id, _bot):
        asked.append(round_id)

    bot = _make_bot(db_path)
    with patch("leaguebot.attendance.services.rsvp_service.run_rsvp_notice", new=AsyncMock(side_effect=_notice)):
        await _recover_missed_check_in_calls(bot, now=NOW, **kwargs)
    return asked


async def _call_stands(db_path: str) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        return await cursor.fetchone() is not None


def _logged(bot) -> list[str]:
    return [c.args[0] for c in bot.output_router.post_log.await_args_list]


# ---------------------------------------------------------------------------
# A call whose deadline is still ahead is posted late
# ---------------------------------------------------------------------------


async def test_a_call_that_came_due_while_the_bot_was_down_is_posted_late(tmp_path):
    """Its moment went by while the bot was stopped, and the deadline has not: the division
    is asked now and answers in the time left."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _recover_with_the_real_call(bot)

    channel.send.assert_awaited_once()
    assert await _call_stands(db_path)


async def test_a_call_posted_late_is_written_to_the_log(tmp_path):
    """Staff learn the call went out late, and until when it can be answered."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)

    await _recover_with_the_real_call(bot)

    deadline = NOW + timedelta(days=2) - timedelta(hours=2)
    assert _logged(bot) == [
        "ATTENDANCE | check-in call | POSTED LATE\n"
        "  season: 3\n"
        f"  division: Division 1 (id={DIVISION_ID})\n"
        "  round: 4\n"
        "  reason: the call was not posted when it fell due\n"
        f"  deadline: <t:{int(deadline.timestamp())}:F>"
    ]


async def test_a_zero_deadline_lets_a_call_post_late_until_the_round(tmp_path):
    """A deadline of zero closes the check-in at the round's own moment, as
    `/attendance post-check-in` reads it, so an hour before the round is still in time."""
    db_path = await _make_db(tmp_path, until_round=timedelta(hours=1), deadline_hours=0)

    assert await _posted(db_path) == [ROUND_ID]


# ---------------------------------------------------------------------------
# Rounds left alone
# ---------------------------------------------------------------------------


async def test_no_late_call_is_posted_before_the_call_is_due(tmp_path):
    """Its job is still to come, and posting early would override the league's lead time."""
    db_path = await _make_db(tmp_path, until_round=timedelta(days=6))

    assert await _posted(db_path) == []


async def test_no_late_call_while_a_call_stands(tmp_path):
    """The call went out; a second beside it would divide the division's answers."""
    db_path = await _make_db(tmp_path, call_standing=True)

    assert await _posted(db_path) == []


async def test_no_late_call_for_a_round_whose_check_in_is_over(tmp_path):
    """A round whose check-in has been taken down has no call owed (#425)."""
    db_path = await _make_db(tmp_path, checkin_cleared=True)

    assert await _posted(db_path) == []


async def test_no_late_call_for_a_cancelled_round(tmp_path):
    db_path = await _make_db(tmp_path, round_status="CANCELLED")

    assert await _posted(db_path) == []


async def test_no_late_call_while_attendance_is_disabled(tmp_path):
    """The module-output rule, by the restart path (#114)."""
    db_path = await _make_db(tmp_path, attendance_enabled=False)

    assert await _posted(db_path) == []


async def test_no_late_call_outside_an_ongoing_season(tmp_path):
    """A season still in setup has no check-ins owed."""
    db_path = await _make_db(tmp_path, season_status="SETUP")

    assert await _posted(db_path) == []


async def test_no_late_call_while_test_mode_is_on(tmp_path):
    """Decided 2026-09-24. `/test-mode advance` posts a test season's calls in their turn, and a
    restored save's rounds have all gone by."""
    db_path = await _make_db(tmp_path, test_mode=True)

    assert await _posted(db_path) == []


# ---------------------------------------------------------------------------
# Where it runs
# ---------------------------------------------------------------------------


def test_missed_calls_are_posted_before_the_deadlines_are_caught_up():
    """A call comes before its deadline. Read from the source, because both run inside
    `on_ready` among a dozen start-up steps no test drives whole."""
    source = inspect.getsource(bot_module.main)
    calls = source.index("await _recover_missed_check_in_calls(bot)")
    deadlines = source.index("await _recover_rsvp_views_and_deadlines(bot)")
    assert calls < deadlines


# ---------------------------------------------------------------------------
# A call whose deadline has passed as well is given up, and reported once
# ---------------------------------------------------------------------------


_GIVEN_UP = (
    "ATTENDANCE | check-in call | NOT POSTED\n"
    "  season: 3\n"
    f"  division: Division 1 (id={DIVISION_ID})\n"
    "  round: 4\n"
    "  reason: the round's check-in deadline passed before its call could be posted\n"
    "  note: a call posted now could not be answered, so none was posted. No attendance rows "
    "were opened, and this round will count nothing against anyone."
)


async def _checkin_cleared(db_path: str) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT checkin_cleared FROM rounds WHERE id = ?", (ROUND_ID,))
        return bool((await cursor.fetchone())["checkin_cleared"])


async def test_a_call_whose_deadline_has_passed_is_not_posted_and_is_reported(tmp_path):
    """Decided 2026-09-24. A call nobody could answer would record every driver as not having
    answered, so none is posted — and the log channel says the round has no check-in."""
    db_path = await _make_db(tmp_path, until_round=timedelta(hours=1))
    bot = _make_bot(db_path)
    notice = AsyncMock()

    with patch("leaguebot.attendance.services.rsvp_service.run_rsvp_notice", new=notice):
        await _recover_missed_check_in_calls(bot, now=NOW)

    notice.assert_not_awaited()
    assert _logged(bot) == [_GIVEN_UP]
    assert await _checkin_cleared(db_path), "a call given up is still owed"


async def test_a_call_given_up_is_reported_once_across_restarts(tmp_path):
    """The round's check-in is over once given up, so the next start has nothing to say."""
    db_path = await _make_db(tmp_path, until_round=timedelta(hours=1))
    bot = _make_bot(db_path)

    with patch("leaguebot.attendance.services.rsvp_service.run_rsvp_notice", new=AsyncMock()):
        await _recover_missed_check_in_calls(bot, now=NOW)
        await _recover_missed_check_in_calls(bot, now=NOW + timedelta(minutes=10))

    assert _logged(bot) == [_GIVEN_UP]
