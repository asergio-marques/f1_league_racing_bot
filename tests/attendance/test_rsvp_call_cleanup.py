"""A round's check-in call comes down 24 hours after the round, and at no other routine moment.

Issue #425. When the bot posted a division's check-in call it deleted the call, the last
notice and the distribution message of **every** other round of that division, without asking
whether that round's check-in had closed. Two rounds closer together than the notice — a
Saturday and Sunday double-header on the default five days — meant Sunday's call went out while
Saturday's was open and took it down: nobody could answer Saturday any more, its deadline could
not take the buttons off, and its distribution message was posted with no row to record it in.

The rule now (decided 2026-09-24, #425): posting a call takes down no other call, and each
round's call, last notice and distribution message are taken down 24 hours after the round's
scheduled start, as weather's Phase 3 forecast already was. The answers are kept.

A division can therefore hold more than one call at once, and anything that looked for "the
division's call" by taking whichever row it found — `/attendance test rsvp` did — has to
choose. `get_current_embed_message` does, and its tests are at the foot of this file.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import attach_queue, league_double, run_queue, stopped_job
from tests.support.review_league import channel
from tests.support.season_league import CALL_MESSAGES, DIVISIONS, PRO, ongoing_league, round_id

#: The moment every round is placed relative to.
NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)

SEASON_ID = 1
DIVISION_ID = 1
OTHER_DIVISION_ID = 2
RSVP_CHANNEL_ID = 770077

#: The round whose call is posted, due exactly at NOW on the default five-day notice.
POSTED_ROUND = 10
#: Another round of the division, whose call is already standing.
EARLIER_ROUND = 11

DEADLINE_HOURS = 2
NOTICE_DAYS = 5

DRIVER_PROFILE = 201


def _msg_ids(round_id: int) -> tuple[str, str, str]:
    """The call, last notice and distribution message ids seeded for *round_id*."""
    return f"{round_id}001", f"{round_id}002", f"{round_id}003"


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "rsvp_clearing.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours) "
            "VALUES (1, 1, ?, 0, ?)",
            (NOTICE_DAYS, DEADLINE_HOURS),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        for division_id, name in ((DIVISION_ID, "Division 1"), (OTHER_DIVISION_ID, "Division 2")):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (division_id, SEASON_ID, name, division_id, 500 + division_id),
            )
            await db.execute(
                "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
                "VALUES (?, ?)",
                (division_id, str(RSVP_CHANNEL_ID)),
            )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO driver_profiles "
            "(id, discord_user_id, current_state, is_test_driver, test_display_name) "
            "VALUES (?, ?, 'ACTIVE', 1, 'Full Timer')",
            (DRIVER_PROFILE, str(DRIVER_PROFILE)),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, 10, 1, ?)",
            (DRIVER_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, 20)",
            (DRIVER_PROFILE, SEASON_ID, DIVISION_ID),
        )
        await db.commit()
    await _add_round(db_path, POSTED_ROUND, NOW + timedelta(days=NOTICE_DAYS))
    return db_path


async def _add_round(
    db_path: str, round_id: int, scheduled_at: datetime, *, division_id: int = DIVISION_ID
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, ?, 'NORMAL', 'Silverstone Circuit', ?)",
            (round_id, division_id, round_id, scheduled_at.isoformat()),
        )
        await db.commit()


async def _add_standing_call(
    db_path: str,
    round_id: int,
    scheduled_at: datetime,
    *,
    division_id: int = DIVISION_ID,
    distributed: bool = True,
) -> None:
    """A round whose call, last notice and distribution message are all standing.

    With *distributed* false its deadline has not run, so it has no distribution message yet.
    """
    await _add_round(db_path, round_id, scheduled_at, division_id=division_id)
    await _add_call(
        db_path, round_id, scheduled_at, division_id=division_id, distributed=distributed
    )


async def _add_call(
    db_path: str,
    round_id: int,
    scheduled_at: datetime,
    *,
    division_id: int = DIVISION_ID,
    distributed: bool = True,
) -> None:
    """Record a call as standing for a round that already exists."""
    call, last_notice, distribution = _msg_ids(round_id)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at, "
            " last_notice_msg_id, distribution_msg_id, deadline_run_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                division_id,
                call,
                str(RSVP_CHANNEL_ID),
                (scheduled_at - timedelta(days=NOTICE_DAYS)).isoformat(),
                last_notice,
                distribution if distributed else None,
                scheduled_at.isoformat() if distributed else None,
            ),
        )
        await db.commit()


async def _standing_rounds(db_path: str) -> set[int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT round_id FROM rsvp_embed_messages")
        return {row["round_id"] for row in await cursor.fetchall()}




async def _post_call(db_path: str, *, round_id: int = POSTED_ROUND) -> SimpleNamespace:
    """Ask the queue for *round_id*'s call as its timer does (#439, slice 6b), and run the queue;
    hand back what it deleted from the check-in channel (`.deleted`, the ids as text).

    The check-in channel stands on the server holding every message `_msg_ids` names for both
    rounds; the call is posted there once, and nothing stops the queue."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin
    from leaguebot.core.services.module_service import ModuleService

    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service = ModuleService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    events: list = []
    checkin = channel(RSVP_CHANNEL_ID, events)
    for rid in (POSTED_ROUND, EARLIER_ROUND):
        for message_id in _msg_ids(rid):
            checkin.seed(int(message_id), "a check-in message")
    known = {RSVP_CHANNEL_ID: checkin, bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=lambda cid: known.get(int(cid)))
    attach_queue(bot, db_path, now=NOW)

    await ask_check_in_call(bot.change_queue, db_path, round_id, origin=ChangeOrigin.BOT)
    await run_queue(bot)

    assert await stopped_job(db_path) is None
    assert len([e for e in events if e[0] == "send" and e[1] == RSVP_CHANNEL_ID]) == 1
    return SimpleNamespace(deleted=[str(mid) for kind, cid, mid in events
                                    if kind == "delete" and cid == RSVP_CHANNEL_ID])


# ---------------------------------------------------------------------------
# Posting a call takes down no other
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scheduled_at", "division_id"),
    [
        (NOW + timedelta(days=NOTICE_DAYS - 1), DIVISION_ID),
        (NOW - timedelta(hours=21), DIVISION_ID),
        (NOW - timedelta(days=6), DIVISION_ID),
        (NOW + timedelta(days=NOTICE_DAYS + 1), DIVISION_ID),
        (NOW - timedelta(days=6), OTHER_DIVISION_ID),
    ],
    ids=[
        "double-header-still-open",
        "closed-under-a-day-ago",
        "closed-days-ago",
        "a-later-round",
        "another-division",
    ],
)
async def test_posting_a_call_takes_down_no_other_call(tmp_path, scheduled_at, division_id):
    """The first case is the issue's own reproduction: Sunday's call going out while
    Saturday's is still open. The third is the one the old behaviour did take down, and the
    fourth is what `/attendance post-check-in` re-posting an earlier round used to cost the
    next. None of them is posting's business now."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, scheduled_at, division_id=division_id)

    channel = await _post_call(db_path)

    assert channel.deleted == []
    assert await _standing_rounds(db_path) == {EARLIER_ROUND, POSTED_ROUND}


# ---------------------------------------------------------------------------
# The cleanup, 24 hours after the round
# ---------------------------------------------------------------------------



async def _clean_up(
    db_path: str, round_id: int = EARLIER_ROUND, *, attendance_enabled: bool = True
) -> SimpleNamespace:
    """Ask the queue for *round_id*'s cleanup as its timer does (#439, slice 6b), and run the
    queue; hand back what it deleted from the check-in channel (`.deleted`, the ids as text).

    The check-in channel stands on the server holding every message `_msg_ids` names for both
    rounds; with *attendance_enabled* false the module is off, in its configuration and to the
    module service alike."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_clean_up
    from leaguebot.core.models.change import ChangeOrigin

    if not attendance_enabled:
        async with get_connection(db_path) as db:
            await db.execute("UPDATE attendance_config SET module_enabled = 0")
            await db.commit()
    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    events: list = []
    checkin = channel(RSVP_CHANNEL_ID, events)
    for rid in (POSTED_ROUND, EARLIER_ROUND):
        for message_id in _msg_ids(rid):
            checkin.seed(int(message_id), "a check-in message")
    known = {RSVP_CHANNEL_ID: checkin, bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=lambda cid: known.get(int(cid)))
    attach_queue(bot, db_path, now=NOW)

    await ask_check_in_clean_up(bot.change_queue, db_path, round_id, origin=ChangeOrigin.BOT)
    await run_queue(bot)

    assert await stopped_job(db_path) is None
    return SimpleNamespace(deleted=[str(mid) for kind, cid, mid in events
                                    if kind == "delete" and cid == RSVP_CHANNEL_ID])


async def _cleared(db_path: str, round_id: int = EARLIER_ROUND) -> bool:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT checkin_cleared FROM rounds WHERE id = ?", (round_id,))
        return bool((await cursor.fetchone())["checkin_cleared"])


async def test_the_cleanup_takes_down_the_call_and_its_notices(tmp_path):
    """The call, its last notice and its distribution message, and the row recording them."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(days=1))

    channel = await _clean_up(db_path)

    assert sorted(channel.deleted) == sorted(_msg_ids(EARLIER_ROUND))
    assert await _standing_rounds(db_path) == set()


async def test_the_cleanup_keeps_the_answers(tmp_path):
    """The messages go; what the drivers answered is the attendance record and stays."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(days=1))
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status) VALUES (?, ?, ?, 'ACCEPTED')",
            (EARLIER_ROUND, DIVISION_ID, DRIVER_PROFILE),
        )
        await db.commit()

    await _clean_up(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_status FROM driver_round_attendance WHERE round_id = ?",
            (EARLIER_ROUND,),
        )
        rows = await cursor.fetchall()
    assert [row["rsvp_status"] for row in rows] == ["ACCEPTED"]


@pytest.mark.parametrize("call_standing", [True, False], ids=["call-standing", "no-call"])
async def test_the_cleanup_marks_the_round_cleared(tmp_path, call_standing):
    """Test mode reads a round with no call recorded as one whose call is still to be posted.
    The mark is what stops it posting a day-old round's call again, and a round whose call never
    posted is no more due one afterwards than a round whose call did."""
    db_path = await _make_db(tmp_path)
    if call_standing:
        await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(days=1))
    else:
        await _add_round(db_path, EARLIER_ROUND, NOW - timedelta(days=1))

    await _clean_up(db_path)

    assert await _cleared(db_path)


async def test_the_cleanup_does_nothing_while_attendance_is_disabled(tmp_path):
    """A call already posted when the module is switched off stays where it is (attendance
    specification), so the cleanup leaves it, and does not mark the round either."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(days=1))

    channel = await _clean_up(db_path, attendance_enabled=False)

    assert channel.deleted == []
    assert await _standing_rounds(db_path) == {EARLIER_ROUND}
    assert not await _cleared(db_path)


async def test_the_cleanup_leaves_a_cancelled_round_alone(tmp_path):
    """A cancelled round's call came down with the cancellation; a cleanup job that survived it
    has nothing to do."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(days=1))
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET status = 'CANCELLED' WHERE id = ?", (EARLIER_ROUND,))
        await db.commit()

    channel = await _clean_up(db_path)

    assert channel.deleted == []
    assert not await _cleared(db_path)


async def test_the_cleanup_leaves_the_other_round_of_a_double_header_standing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(days=1))
    await _add_call(db_path, POSTED_ROUND, NOW + timedelta(days=NOTICE_DAYS), distributed=False)

    channel = await _clean_up(db_path)

    assert sorted(channel.deleted) == sorted(_msg_ids(EARLIER_ROUND))
    assert await _standing_rounds(db_path) == {POSTED_ROUND}
    assert not await _cleared(db_path, POSTED_ROUND)


# ---------------------------------------------------------------------------
# Which of a division's calls is current
# ---------------------------------------------------------------------------
#
# A division held one call at most while every new call took down all the others, so anything
# looking for "the division's call" could take whichever row it found. `/attendance test rsvp`
# did exactly that. A double-header now leaves two standing, and the command must
# still reach the one whose check-in is open.


async def _current_round(db_path: str, division_id: int = DIVISION_ID) -> int | None:
    call = await AttendanceService(db_path).get_current_embed_message(division_id)
    return call.round_id if call is not None else None


async def test_the_current_call_of_a_double_header_is_the_earlier_one(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_call(db_path, POSTED_ROUND, NOW + timedelta(days=NOTICE_DAYS), distributed=False)
    await _add_standing_call(
        db_path, EARLIER_ROUND, NOW + timedelta(days=NOTICE_DAYS - 1), distributed=False
    )

    assert await _current_round(db_path) == EARLIER_ROUND


async def test_the_current_call_moves_on_once_the_earlier_deadline_has_run(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_call(db_path, POSTED_ROUND, NOW + timedelta(days=NOTICE_DAYS), distributed=False)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW + timedelta(days=NOTICE_DAYS - 1))

    assert await _current_round(db_path) == POSTED_ROUND


async def test_the_current_call_is_the_latest_once_every_deadline_has_run(tmp_path):
    """What the division's one row gave before #425, whether its deadline had run or not."""
    db_path = await _make_db(tmp_path)
    await _add_call(db_path, POSTED_ROUND, NOW + timedelta(days=NOTICE_DAYS))
    await _add_standing_call(db_path, EARLIER_ROUND, NOW + timedelta(days=NOTICE_DAYS - 1))

    assert await _current_round(db_path) == POSTED_ROUND


async def test_another_division_s_call_is_never_current(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_standing_call(
        db_path, 20, NOW + timedelta(days=1), division_id=OTHER_DIVISION_ID, distributed=False
    )

    assert await _current_round(db_path) is None
    assert await _current_round(db_path, OTHER_DIVISION_ID) == 20


# ---------------------------------------------------------------------------
# Each message taken down from the channel it was posted in (#439, slice 6b, fault 9)
# ---------------------------------------------------------------------------
#
# The last notice and the reserve announcement go to the division's check-in channel as it is
# when they are posted (attendance specification, decision 13 of #439's slice 6b), so after
# `/attendance channel rsvp` they stand in a channel the call does not. Each is recorded with the
# channel it was posted in, and the clean-up a day after the round deletes it from there.
#
# These use `ongoing_league` with attendance on: Pro's round 3 call stands in Pro's check-in
# channel (603) as message 7001, its last notice 7002 and its announcement 7003; Pro's check-in
# channel is then moved to 608. Each step is asked of the change queue as its timer asks it,
# through attendance's own ask functions, and the queue run.

#: The channel Pro's check-in moves to after its round 3 call was posted.
MOVED = 608
CHECK_IN = DIVISIONS[PRO][3].checkin
ROUND_3 = round_id(PRO, 3)
CALL, LAST_NOTICE, ANNOUNCEMENT = CALL_MESSAGES


async def _moved_league(tmp_path) -> object:
    """The league above, with Pro's check-in channel moved to `MOVED`, and the queue rebuilt
    on a real `AttendanceService` so the check-in's change types read the database."""
    league = await ongoing_league(tmp_path, attendance=True)
    moved = league._recording(channel(MOVED, league.events))
    moved.guild = league.guild
    league.channels[MOVED] = moved
    await league.write(
        "UPDATE attendance_division_config SET rsvp_channel_id = ? WHERE division_id = ?",
        str(MOVED), PRO,
    )
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    return league


async def _ask(league, step: str) -> None:
    """Ask the queue for round 3's *step* (`last_notice`, `deadline` or `clean_up`), as its
    timer does, and run the queue to its end."""
    from leaguebot.attendance.services import check_in_changes
    from leaguebot.core.models.change import ChangeOrigin

    ask = getattr(check_in_changes, f"ask_check_in_{step}")
    await ask(league.bot.change_queue, league.db_path, ROUND_3, origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


async def _round_3_raced_a_day_ago(league) -> None:
    when = league.clock.now - timedelta(hours=25)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)


def _moved_into(league, message_id: int, content: str) -> None:
    """*message_id* stands in `MOVED` rather than in Pro's check-in channel."""
    del league.channel(CHECK_IN).messages[message_id]
    league.channel(MOVED).seed(message_id, content)


def _deletes(league) -> set[tuple[int, int]]:
    return {(cid, mid) for kind, cid, mid in league.events if kind == "delete"}


async def _call_row(league) -> list[dict]:
    return await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3)


@pytest.mark.parametrize(
    ("moved", "content", "last_notice_channel", "distribution_channel", "deleted"),
    [
        (LAST_NOTICE, "Round 3 last notice", MOVED, CHECK_IN,
         {(CHECK_IN, CALL), (MOVED, LAST_NOTICE), (CHECK_IN, ANNOUNCEMENT)}),
        (ANNOUNCEMENT, "Round 3 reserves", CHECK_IN, MOVED,
         {(CHECK_IN, CALL), (CHECK_IN, LAST_NOTICE), (MOVED, ANNOUNCEMENT)}),
    ],
    ids=["last-notice", "reserve-announcement"],
)
async def test_a_message_is_deleted_from_the_channel_it_was_posted_in_after_the_channel_moves(
    tmp_path, moved, content, last_notice_channel, distribution_channel, deleted,
):
    """The issue's fault (b): the last notice went to the new channel, the clean-up looked for
    it in the call's, and it stood for good. A reserve announcement posted in the new channel is
    deleted from there in the same way."""
    league = await _moved_league(tmp_path)
    _moved_into(league, moved, content)
    await league.write(
        "UPDATE rsvp_embed_messages SET last_notice_channel_id = ?, distribution_channel_id = ? "
        "WHERE round_id = ?", str(last_notice_channel), str(distribution_channel), ROUND_3,
    )
    await _round_3_raced_a_day_ago(league)

    await _ask(league, "clean_up")

    assert await stopped_job(league.db_path) is None
    assert _deletes(league) == deleted
    assert moved not in league.channel(MOVED).messages
    assert await _call_row(league) == []


async def test_each_check_in_message_s_channel_is_saved_with_it(tmp_path):
    """Round 3 is three hours off, its call standing in 603 with no last notice or announcement
    yet. The last notice, late but before the two-hour deadline, and then the deadline's
    announcement once it has passed, each go to 608 and are recorded with it; the call keeps
    its own channel."""
    league = await _moved_league(tmp_path)
    for message_id in (LAST_NOTICE, ANNOUNCEMENT):
        del league.channel(CHECK_IN).messages[message_id]
    await league.write(
        "UPDATE rsvp_embed_messages SET last_notice_msg_id = NULL, distribution_msg_id = NULL "
        "WHERE round_id = ?", ROUND_3,
    )
    when = league.clock.now + timedelta(hours=3)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)

    await _ask(league, "last_notice")
    league.clock.advance(hours=2)
    await _ask(league, "deadline")

    assert await stopped_job(league.db_path) is None
    sent = [mid for kind, cid, mid in league.events if kind == "send" and cid == MOVED]
    assert len(sent) == 2
    assert not [mid for kind, cid, mid in league.events if kind == "send" and cid == CHECK_IN]
    [row] = await _call_row(league)
    assert (row["message_id"], row["channel_id"]) == (str(CALL), str(CHECK_IN))
    assert (row["last_notice_msg_id"], row["last_notice_channel_id"]) == (str(sent[0]), str(MOVED))
    assert (row["distribution_msg_id"], row["distribution_channel_id"]) == (
        str(sent[1]), str(MOVED))
