"""`run_rsvp_last_notice` — the reminder ping, and who it names.

Issue #208. `tests/attendance/test_attendance_module_gate.py` covers this job's module gate and
nothing else; the body was unexecuted. It is the bot's last word to a division before check-in
closes, and it has two quite different shapes depending on whether anyone is still silent.

`attendance_module_specification.md` gives the job its purpose — the last notice is the
reminder sent before the deadline — and the function's own docstring the rule this file holds
to:

    Always posts a visibility message to the RSVP channel with a Discord relative timestamp to
    the race. When full-time drivers still have rsvp_status = 'NO_RSVP' they are also mentioned
    with a reminder to respond.

**"Always posts" is the half that is easy to lose.** A reader tidying this would naturally
return early when nobody is outstanding — there is, after all, nobody to remind. That would
take away the division's only notice that the deadline is approaching, which is the reason the
message is sent to everyone rather than to the silent drivers alone.
`test_a_division_that_has_all_answered_still_gets_a_notice` is what holds it.

**Who is named is the other half, and the query is where it can go wrong.** Only *full-time*
drivers still at `NO_RSVP` are mentioned. A reserve is not late for not having answered —
reserves are asked, not required — so `ti.is_reserve = 0` in the query is load-bearing, and
`test_a_silent_reserve_is_not_named` sits on it. Naming reserves would publicly chase drivers
who owe the division nothing, every round.

The round's moment is rendered as a Discord relative timestamp, so the tests assert on the
`<t:...:R>` form rather than any rendered wording — what a reader sees is Discord's, computed
from the unix seconds the bot supplies, and that number is the only part the bot decides.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.attendance_service import AttendanceService
from leaguebot.attendance.services.rsvp_service import run_rsvp_last_notice

SERVER_ID = 8608
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 7
RSVP_CHANNEL_ID = 770277

FULL_TIME_A = 401
FULL_TIME_B = 402
RESERVE = 403


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    with_division_config: bool = True,
    named: bool = True,
) -> str:
    """A division of two full-time drivers and one reserve, all at `NO_RSVP`.

    *named* controls whether the drivers carry a `test_display_name`, which decides which of
    the two mention forms the notice uses.
    """
    db_path = os.path.join(str(tmp_path), "last_notice.db")
    await run_migrations(db_path)

    scheduled = datetime.now(timezone.utc) + timedelta(hours=12)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled) VALUES (?, 1)",
            (1,),
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
        if with_division_config:
            await db.execute(
                "INSERT INTO attendance_division_config "
                "(division_id, rsvp_channel_id) VALUES (?, ?)",
                (DIVISION_ID, str(RSVP_CHANNEL_ID)),
            )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, scheduled.isoformat()),
        )

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
        #: (profile, team instance, seat row id, seat number within that team)
        seats = ((FULL_TIME_A, 10, 20, 1), (FULL_TIME_B, 10, 21, 2), (RESERVE, 11, 22, 1))
        for profile_id, team_id, seat_id, seat_number in seats:
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, ?, 'ACTIVE', 1, ?)",
                (
                    profile_id,
                    str(profile_id),
                    f"Driver {profile_id}" if named else None,
                ),
            )
            await db.execute(
                "INSERT INTO team_seats "
                "(id, team_instance_id, seat_number, driver_profile_id) VALUES (?, ?, ?, ?)",
                (seat_id, team_id, seat_number, profile_id),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id) "
                "VALUES (?, ?, ?, ?)",
                (profile_id, SEASON_ID, DIVISION_ID, seat_id),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance "
                "(round_id, division_id, driver_profile_id, rsvp_status) "
                "VALUES (?, ?, ?, 'NO_RSVP')",
                (ROUND_ID, DIVISION_ID, profile_id),
            )

        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '900700', ?, ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                str(RSVP_CHANNEL_ID),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()
    return db_path


async def _answer(db_path: str, profile_id: int, status: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET rsvp_status = ? "
            "WHERE round_id = ? AND driver_profile_id = ?",
            (status, ROUND_ID, profile_id),
        )
        await db.commit()


def _make_channel() -> MagicMock:
    channel = MagicMock()
    sent = MagicMock()
    sent.id = 991199
    channel.send = AsyncMock(return_value=sent)
    return channel


def _make_bot(db_path: str, channel: MagicMock | None) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.get_channel = MagicMock(return_value=channel)
    return bot


def _posted(channel: MagicMock) -> str:
    return str(channel.send.await_args.args[0])


# ---------------------------------------------------------------------------
# The reminder, when somebody is still silent
# ---------------------------------------------------------------------------


async def test_a_silent_full_time_driver_is_mentioned(tmp_path):
    db_path = await _make_db(tmp_path)
    await _answer(db_path, FULL_TIME_B, "ACCEPTED")
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    body = _posted(channel)
    assert "RSVP Reminder" in body
    assert f"<@{FULL_TIME_A}>" in body


async def test_a_driver_who_has_answered_is_not_chased(tmp_path):
    """Being reminded to answer a question already answered is how a league learns to
    ignore the reminder."""
    db_path = await _make_db(tmp_path)
    await _answer(db_path, FULL_TIME_B, "ACCEPTED")
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    assert f"<@{FULL_TIME_B}>" not in _posted(channel)


@pytest.mark.parametrize("status", ["ACCEPTED", "TENTATIVE", "DECLINED"])
async def test_any_answer_at_all_counts_as_having_responded(tmp_path, status):
    """The reminder asks for a response, not for a yes. A driver who has declined has
    answered and must not be chased."""
    db_path = await _make_db(tmp_path)
    for profile_id in (FULL_TIME_A, FULL_TIME_B):
        await _answer(db_path, profile_id, status)
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    assert "All drivers have responded" in _posted(channel)


async def test_every_silent_driver_is_named_not_only_the_first(tmp_path):
    db_path = await _make_db(tmp_path)
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    body = _posted(channel)
    assert f"<@{FULL_TIME_A}>" in body
    assert f"<@{FULL_TIME_B}>" in body


async def test_a_silent_reserve_is_not_named(tmp_path):
    """`ti.is_reserve = 0` in the query. A reserve is asked, not required, so chasing one
    publicly for not answering would be chasing a driver who owes the division nothing."""
    db_path = await _make_db(tmp_path)
    for profile_id in (FULL_TIME_A, FULL_TIME_B):
        await _answer(db_path, profile_id, "ACCEPTED")
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    body = _posted(channel)
    assert f"<@{RESERVE}>" not in body
    assert "All drivers have responded" in body


async def test_a_test_driver_s_display_name_is_carried_into_the_mention(tmp_path):
    """Test drivers all share one Discord account, so the mention alone cannot tell them
    apart — the display name is what makes the reminder legible in test mode."""
    db_path = await _make_db(tmp_path, named=True)
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    assert f"({'Driver %d' % FULL_TIME_A})" in _posted(channel)


async def test_a_driver_with_no_display_name_is_mentioned_plainly(tmp_path):
    """The other branch of the same mention. An empty pair of brackets would read as a
    missing name rather than as a real driver."""
    db_path = await _make_db(tmp_path, named=False)
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    body = _posted(channel)
    assert f"<@{FULL_TIME_A}>" in body
    assert "()" not in body


# ---------------------------------------------------------------------------
# The visibility notice, when nobody is
# ---------------------------------------------------------------------------


async def test_a_division_that_has_all_answered_still_gets_a_notice(tmp_path):
    """"Always posts". The message goes to everyone because it is the division's notice
    that the deadline is near, not merely a chase — returning early when nobody is
    outstanding would take that away."""
    db_path = await _make_db(tmp_path)
    for profile_id in (FULL_TIME_A, FULL_TIME_B, RESERVE):
        await _answer(db_path, profile_id, "ACCEPTED")
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    channel.send.assert_awaited_once()
    assert "All drivers have responded" in _posted(channel)


async def test_both_shapes_of_notice_carry_the_race_time(tmp_path):
    """The relative timestamp is the whole point of the notice, and it is computed from the
    round rather than written by Discord, so it is the bot's to get right."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel()
    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))
    reminder = _posted(channel)

    for profile_id in (FULL_TIME_A, FULL_TIME_B):
        await _answer(db_path, profile_id, "ACCEPTED")
    channel = _make_channel()
    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))
    visibility = _posted(channel)

    assert ":R>" in reminder
    assert ":R>" in visibility


# ---------------------------------------------------------------------------
# When it cannot post
# ---------------------------------------------------------------------------


async def test_a_round_that_no_longer_exists_posts_nothing(tmp_path, caplog):
    db_path = await _make_db(tmp_path)
    channel = _make_channel()

    with caplog.at_level("ERROR"):
        await run_rsvp_last_notice(9999, _make_bot(db_path, channel))

    channel.send.assert_not_awaited()


async def test_a_division_with_no_rsvp_channel_configured_posts_nothing(tmp_path, caplog):
    db_path = await _make_db(tmp_path, with_division_config=False)
    channel = _make_channel()

    with caplog.at_level("WARNING"):
        await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    channel.send.assert_not_awaited()
    assert "no RSVP channel" in caplog.text


async def test_a_channel_the_bot_cannot_see_posts_nothing(tmp_path, caplog):
    db_path = await _make_db(tmp_path)

    with caplog.at_level("ERROR"):
        await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel=None))

    assert "channel not found" in caplog.text


async def test_a_failed_send_records_no_message(tmp_path, caplog):
    """The recorded id is what the round's cleanup, a day after it, deletes; one pointing at
    a message that was never sent would have it delete something else, or nothing."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "forbidden"))

    with caplog.at_level("ERROR"):
        await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT last_notice_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        assert (await cursor.fetchone())["last_notice_msg_id"] is None


async def test_the_posted_notice_is_recorded_for_the_cleanup(tmp_path):
    db_path = await _make_db(tmp_path)
    channel = _make_channel()

    await run_rsvp_last_notice(ROUND_ID, _make_bot(db_path, channel))

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT last_notice_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        assert (await cursor.fetchone())["last_notice_msg_id"] == "991199"
