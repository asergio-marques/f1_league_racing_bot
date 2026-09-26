"""A round's check-in call comes down 24 hours after the round, and at no other routine moment.

Issue #425. When `run_rsvp_notice` posted a division's call it deleted the call, the last
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
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import AttendanceService

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
            " last_notice_msg_id, distribution_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                round_id,
                division_id,
                call,
                str(RSVP_CHANNEL_ID),
                (scheduled_at - timedelta(days=NOTICE_DAYS)).isoformat(),
                last_notice,
                distribution if distributed else None,
            ),
        )
        await db.commit()


async def _standing_rounds(db_path: str) -> set[int]:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT round_id FROM rsvp_embed_messages")
        return {row["round_id"] for row in await cursor.fetchall()}


def _make_channel() -> MagicMock:
    """A check-in channel recording every message deleted from it."""
    channel = MagicMock()
    channel.id = RSVP_CHANNEL_ID
    channel.deleted = []

    async def _fetch(message_id: int) -> MagicMock:
        message = MagicMock()
        message.delete = AsyncMock(side_effect=lambda: channel.deleted.append(str(message_id)))
        return message

    channel.fetch_message = AsyncMock(side_effect=_fetch)
    posted = MagicMock()
    posted.id = 990099
    posted.channel.id = RSVP_CHANNEL_ID
    channel.send = AsyncMock(return_value=posted)
    return channel


def _make_bot(db_path: str, channel: MagicMock, *, attendance_enabled: bool = True) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    bot.get_channel = MagicMock(return_value=channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _post_call(db_path: str, *, round_id: int = POSTED_ROUND) -> MagicMock:
    """Post *round_id*'s call, and hand back the channel it was posted to."""
    channel = _make_channel()
    bot = _make_bot(db_path, channel)
    with patch.object(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None)):
        await rsvp_service.run_rsvp_notice(round_id, bot)
    channel.send.assert_awaited_once()
    return channel


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
) -> MagicMock:
    """Run *round_id*'s cleanup, and hand back the channel it deleted from."""
    channel = _make_channel()
    await rsvp_service.run_rsvp_cleanup(
        round_id, _make_bot(db_path, channel, attendance_enabled=attendance_enabled)
    )
    return channel


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
