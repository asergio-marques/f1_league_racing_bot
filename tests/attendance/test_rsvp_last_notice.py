"""The last check-in notice — the reminder ping, and who it names.

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

Since #439 (slice 6b, C29) the last notice is a change on the queue: each test asks it as its
timer does, as the bot (`ask_check_in_last_notice`), and runs the queue; the notice is posted in
the division's check-in channel (770277) by the change's job. Where the division has no check-in
channel, or the bot cannot find it, or Discord refuses the notice, the queue stops, naming the
command that sets the channel where it is missing.

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
from tests.support.change_queue import (
    attach_queue,
    http_error,
    league_double,
    run_queue,
    stopped_job,
)
from tests.support.review_league import channel as recording_channel

SERVER_ID = 8608
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 7
RSVP_CHANNEL_ID = 770277

FULL_TIME_A = 401
FULL_TIME_B = 402
RESERVE = 403

NO_LAST_NOTICE_CHANGE = (
    "#439: the last notice is posted by `run_rsvp_last_notice` at once, off the change queue"
)


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


def _make_bot(db_path: str, *, visible: bool = True) -> MagicMock:
    """The league's bot double with its change queue and attendance on, the division's
    check-in channel (770277) on the server where *visible*, recording its sends in
    `bot.events`; a channel the bot cannot find is refused as Discord refuses it."""
    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.events = []
    bot.rsvp_channel = recording_channel(RSVP_CHANNEL_ID, bot.events)
    known = {bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    if visible:
        known[RSVP_CHANNEL_ID] = bot.rsvp_channel

    async def _fetch(cid: int) -> object:
        if int(cid) in known:
            return known[int(cid)]
        raise http_error(discord.NotFound, status=404, text="Unknown Channel")

    bot.get_channel = MagicMock(side_effect=lambda cid: known.get(int(cid)))
    bot.fetch_channel = AsyncMock(side_effect=_fetch)
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot


async def _ask(bot: MagicMock, round_id: int = ROUND_ID) -> None:
    """Ask the queue for *round_id*'s last notice as its timer does, as the bot; run the
    queue."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_last_notice
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_last_notice(bot.change_queue, bot.db_path, round_id,
                                   origin=ChangeOrigin.BOT)
    await run_queue(bot)


def _sent(bot: MagicMock) -> list[int]:
    """The ids of the messages posted in the division's check-in channel, in order."""
    return [mid for kind, cid, mid in bot.events if kind == "send" and cid == RSVP_CHANNEL_ID]


def _posted(bot: MagicMock) -> str:
    """The text of the one notice posted in the division's check-in channel."""
    [message_id] = _sent(bot)
    return str(bot.rsvp_channel.messages[message_id].content)


def _logged(bot: MagicMock) -> str:
    return "\n".join(bot.log_channel.sent)


async def _recorded_notice(db_path: str) -> str | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT last_notice_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        return (await cursor.fetchone())["last_notice_msg_id"]


# ---------------------------------------------------------------------------
# The reminder, when somebody is still silent
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_silent_full_time_driver_is_mentioned(tmp_path):
    db_path = await _make_db(tmp_path)
    await _answer(db_path, FULL_TIME_B, "ACCEPTED")
    bot = _make_bot(db_path)

    await _ask(bot)

    body = _posted(bot)
    assert "RSVP Reminder" in body
    assert f"<@{FULL_TIME_A}>" in body


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_driver_who_has_answered_is_not_chased(tmp_path):
    """Being reminded to answer a question already answered is how a league learns to
    ignore the reminder."""
    db_path = await _make_db(tmp_path)
    await _answer(db_path, FULL_TIME_B, "ACCEPTED")
    bot = _make_bot(db_path)

    await _ask(bot)

    assert f"<@{FULL_TIME_B}>" not in _posted(bot)


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
@pytest.mark.parametrize("status", ["ACCEPTED", "TENTATIVE", "DECLINED"])
async def test_any_answer_at_all_counts_as_having_responded(tmp_path, status):
    """The reminder asks for a response, not for a yes. A driver who has declined has
    answered and must not be chased."""
    db_path = await _make_db(tmp_path)
    for profile_id in (FULL_TIME_A, FULL_TIME_B):
        await _answer(db_path, profile_id, status)
    bot = _make_bot(db_path)

    await _ask(bot)

    assert "All drivers have responded" in _posted(bot)


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_every_silent_driver_is_named_not_only_the_first(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)

    await _ask(bot)

    body = _posted(bot)
    assert f"<@{FULL_TIME_A}>" in body
    assert f"<@{FULL_TIME_B}>" in body


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_silent_reserve_is_not_named(tmp_path):
    """`ti.is_reserve = 0` in the query. A reserve is asked, not required, so chasing one
    publicly for not answering would be chasing a driver who owes the division nothing."""
    db_path = await _make_db(tmp_path)
    for profile_id in (FULL_TIME_A, FULL_TIME_B):
        await _answer(db_path, profile_id, "ACCEPTED")
    bot = _make_bot(db_path)

    await _ask(bot)

    body = _posted(bot)
    assert f"<@{RESERVE}>" not in body
    assert "All drivers have responded" in body


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_test_driver_s_display_name_is_carried_into_the_mention(tmp_path):
    """Test drivers all share one Discord account, so the mention alone cannot tell them
    apart — the display name is what makes the reminder legible in test mode."""
    db_path = await _make_db(tmp_path, named=True)
    bot = _make_bot(db_path)

    await _ask(bot)

    assert f"({'Driver %d' % FULL_TIME_A})" in _posted(bot)


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_driver_with_no_display_name_is_mentioned_plainly(tmp_path):
    """The other branch of the same mention. An empty pair of brackets would read as a
    missing name rather than as a real driver."""
    db_path = await _make_db(tmp_path, named=False)
    bot = _make_bot(db_path)

    await _ask(bot)

    body = _posted(bot)
    assert f"<@{FULL_TIME_A}>" in body
    assert "()" not in body


# ---------------------------------------------------------------------------
# The visibility notice, when nobody is
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_division_that_has_all_answered_still_gets_a_notice(tmp_path):
    """"Always posts". The message goes to everyone because it is the division's notice
    that the deadline is near, not merely a chase — returning early when nobody is
    outstanding would take that away."""
    db_path = await _make_db(tmp_path)
    for profile_id in (FULL_TIME_A, FULL_TIME_B, RESERVE):
        await _answer(db_path, profile_id, "ACCEPTED")
    bot = _make_bot(db_path)

    await _ask(bot)

    assert len(_sent(bot)) == 1
    assert "All drivers have responded" in _posted(bot)


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_both_shapes_of_notice_carry_the_race_time(tmp_path):
    """The relative timestamp is the whole point of the notice, and it is computed from the
    round rather than written by Discord, so it is the bot's to get right. Each shape is
    posted in a league of its own, a round having one last notice."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)
    await _ask(bot)
    reminder = _posted(bot)

    other = tmp_path / "all_answered"
    other.mkdir()
    db_path = await _make_db(other)
    for profile_id in (FULL_TIME_A, FULL_TIME_B):
        await _answer(db_path, profile_id, "ACCEPTED")
    bot = _make_bot(db_path)
    await _ask(bot)
    visibility = _posted(bot)

    assert ":R>" in reminder
    assert ":R>" in visibility


# ---------------------------------------------------------------------------
# When it cannot post
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_round_that_no_longer_exists_posts_nothing(tmp_path):
    """Work no longer due is dropped: nothing is posted and the queue does not stop."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)

    await _ask(bot, round_id=9999)

    assert _sent(bot) == []
    assert await stopped_job(db_path) is None


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_division_with_no_rsvp_channel_configured_stops_the_queue_naming_the_command(
    tmp_path,
):
    """A bot change lacking its channel stops the queue, the notice naming the command that
    sets it, rather than the host's log alone saying so."""
    db_path = await _make_db(tmp_path, with_division_config=False)
    bot = _make_bot(db_path)

    await _ask(bot)

    assert _sent(bot) == []
    assert await stopped_job(db_path) is not None
    assert "/attendance channel rsvp" in _logged(bot)


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_channel_the_bot_cannot_see_stops_the_queue_naming_the_command(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path, visible=False)

    await _ask(bot)

    assert _sent(bot) == []
    assert await stopped_job(db_path) is not None
    assert "/attendance channel rsvp" in _logged(bot)


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_a_refused_last_notice_stops_the_queue_recording_nothing(tmp_path):
    """The recorded id is what the round's cleanup, a day after it, deletes; one pointing at
    a message that was never sent would have it delete something else, or nothing. Discord
    refusing the notice stops the queue with nothing recorded."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)
    bot.rsvp_channel.send_fails = http_error(discord.Forbidden, status=403, text="Missing Access")

    await _ask(bot)

    assert await stopped_job(db_path) is not None
    assert await _recorded_notice(db_path) is None


@pytest.mark.xfail(strict=True, reason=NO_LAST_NOTICE_CHANGE)
async def test_the_posted_notice_is_recorded_for_the_cleanup(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)

    await _ask(bot)

    [posted] = _sent(bot)
    assert await _recorded_notice(db_path) == str(posted)


# ---------------------------------------------------------------------------
# No last notice where no call stands (#439, slice 6b, fault 8)
# ---------------------------------------------------------------------------
#
# A round with no check-in call standing has nobody to remind: the last notice used to post
# "All drivers have responded" for it, tracked by nothing. It is now dropped as no longer due
# when it is asked, and the host's log says so.
#
# These use `ongoing_league` with attendance on, its queue rebuilt on a real `AttendanceService`:
# Pro's round 3 is three hours off, past its last-notice time and before its two-hour deadline,
# and its call (seeded in Pro's check-in channel, 603) is taken off the record, as though it was
# never posted. The last notice is asked of the queue as its timer asks it, and the queue run.

NO_CALL_NO_NOTICE = (
    "#439: the last notice is not yet a change on the queue whose check drops it where no "
    "check-in call stands"
)


async def _no_call_league(tmp_path):
    from tests.support.change_queue import attach_queue
    from tests.support.season_league import PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + timedelta(hours=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), round_id(PRO, 3))
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", round_id(PRO, 3))
    return league


async def _ask_last_notice(league) -> None:
    from leaguebot.attendance.services.check_in_changes import ask_check_in_last_notice
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue
    from tests.support.season_league import PRO, round_id

    await ask_check_in_last_notice(league.bot.change_queue, league.db_path, round_id(PRO, 3),
                                   origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


@pytest.mark.xfail(strict=True, reason=NO_CALL_NO_NOTICE)
async def test_no_last_notice_is_posted_where_no_call_stands(tmp_path):
    from tests.support.change_queue import change_rows, stopped_job
    from tests.support.season_league import DIVISIONS, PRO

    league = await _no_call_league(tmp_path)

    await _ask_last_notice(league)

    assert [kind for kind, cid, _ in league.events
            if cid == DIVISIONS[PRO][3].checkin and kind == "send"] == []
    assert await stopped_job(league.db_path) is None
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=NO_CALL_NO_NOTICE)
async def test_a_last_notice_with_no_call_is_recorded_in_the_host_log(tmp_path, caplog):
    league = await _no_call_league(tmp_path)

    with caplog.at_level("INFO"):
        await _ask_last_notice(league)

    assert any("no longer due" in record.getMessage() for record in caplog.records)
