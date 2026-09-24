"""A new check-in call clears only the division's calls whose check-in closed a day before.

Issue #425. When `run_rsvp_notice` posted a division's call it deleted the call, the last
notice and the distribution message of **every** other round of that division, and forgot
their rows, without asking whether that round's check-in had closed. Two rounds closer together
than the notice — a Saturday and Sunday double-header on the default five days — meant
Sunday's call went out while Saturday's was open and took it down: nobody could answer
Saturday any more, its deadline could not take the buttons off, and its distribution message
was posted with no row to record it in.

The rule is the attendance specification's (decided 2026-09-24, #274): a call deletes an
earlier round's messages only once that round's check-in closed at least 24 hours before.
Those of a round still open, or closed less recently, stay until a later call is posted.

Nothing tested the clearing at all before this file, which is how the whole-division delete
passed the suite. The first two tests are the issue's own reproduction and its near neighbour;
`test_an_earlier_call_closed_a_day_ago_goes_with_its_notices` is the other side of the line,
so the rule cannot be satisfied by clearing nothing.
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

#: The moment the call under test is posted. Every round is placed relative to it.
NOW = datetime(2026, 10, 5, 18, 0, tzinfo=timezone.utc)

SEASON_ID = 1
DIVISION_ID = 1
OTHER_DIVISION_ID = 2
RSVP_CHANNEL_ID = 770077

#: The round whose call is being posted, due exactly at NOW on the default five-day notice.
POSTED_ROUND = 10
#: The round whose earlier call may or may not be cleared.
EARLIER_ROUND = 11

DEADLINE_HOURS = 2
NOTICE_DAYS = 5

DRIVER_PROFILE = 201


def _msg_ids(round_id: int) -> tuple[str, str, str]:
    """The call, last notice and distribution message ids seeded for *round_id*."""
    return f"{round_id}001", f"{round_id}002", f"{round_id}003"


async def _make_db(tmp_path, *, deadline_hours: int = DEADLINE_HOURS) -> str:
    db_path = os.path.join(str(tmp_path), "rsvp_clearing.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, rsvp_deadline_hours) "
            "VALUES (1, 1, ?, 0, ?)",
            (NOTICE_DAYS, deadline_hours),
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
) -> None:
    """A round whose call, last notice and distribution message are all standing."""
    await _add_round(db_path, round_id, scheduled_at, division_id=division_id)
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
                distribution,
            ),
        )
        await db.commit()


def _closing(hours_ago: float, *, deadline_hours: int = DEADLINE_HOURS) -> datetime:
    """The moment a round is scheduled for whose check-in closed *hours_ago* before NOW."""
    return NOW - timedelta(hours=hours_ago) + timedelta(hours=deadline_hours)


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


def _make_bot(db_path: str, channel: MagicMock) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.get_channel = MagicMock(return_value=channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _post_call(db_path: str, *, round_id: int = POSTED_ROUND, now: datetime = NOW) -> MagicMock:
    """Post *round_id*'s call as at *now*, and hand back the channel it was posted to."""
    channel = _make_channel()
    bot = _make_bot(db_path, channel)
    with patch.object(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None)):
        await rsvp_service.run_rsvp_notice(round_id, bot, now=now)
    channel.send.assert_awaited_once()
    return channel


# ---------------------------------------------------------------------------
# Which earlier calls stay
# ---------------------------------------------------------------------------


async def test_an_earlier_call_still_open_is_left_standing(tmp_path):
    """The issue's reproduction: Sunday's call goes out while Saturday's is still open."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW + timedelta(days=NOTICE_DAYS - 1))

    channel = await _post_call(db_path)

    assert channel.deleted == []
    assert await _standing_rounds(db_path) == {EARLIER_ROUND, POSTED_ROUND}


async def test_an_earlier_call_closed_under_a_day_ago_is_left_standing(tmp_path):
    """Closed, but not yet a day: the division may still want to read who is racing."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, _closing(hours_ago=23))

    channel = await _post_call(db_path)

    assert channel.deleted == []
    assert EARLIER_ROUND in await _standing_rounds(db_path)


async def test_a_later_round_s_call_is_left_standing(tmp_path):
    """`/attendance post-check-in` putting up an earlier round's missing call must not take down
    the next round's, which is still open. Before #425 it deleted it by the same route."""
    db_path = await _make_db(tmp_path)
    later_round = 12
    await _add_standing_call(db_path, later_round, NOW + timedelta(days=NOTICE_DAYS + 1))

    channel = await _post_call(db_path)

    assert channel.deleted == []
    assert later_round in await _standing_rounds(db_path)


# ---------------------------------------------------------------------------
# Which earlier calls go
# ---------------------------------------------------------------------------


async def test_an_earlier_call_closed_a_day_ago_goes_with_its_notices(tmp_path):
    """Exactly 24 hours is "at least 24 hours". The call, its last notice and its distribution
    message all go, and so does the row that recorded them."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, _closing(hours_ago=24))

    channel = await _post_call(db_path)

    assert sorted(channel.deleted) == sorted(_msg_ids(EARLIER_ROUND))
    assert await _standing_rounds(db_path) == {POSTED_ROUND}


async def test_the_answers_to_a_cleared_call_are_kept(tmp_path):
    """The messages go; what the drivers answered is the attendance record and stays."""
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, _closing(hours_ago=48))
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status) VALUES (?, ?, ?, 'ACCEPTED')",
            (EARLIER_ROUND, DIVISION_ID, DRIVER_PROFILE),
        )
        await db.commit()

    await _post_call(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rsvp_status FROM driver_round_attendance WHERE round_id = ?",
            (EARLIER_ROUND,),
        )
        rows = await cursor.fetchall()
    assert [row["rsvp_status"] for row in rows] == ["ACCEPTED"]
    assert EARLIER_ROUND not in await _standing_rounds(db_path)


async def test_another_division_s_calls_are_untouched(tmp_path):
    db_path = await _make_db(tmp_path)
    other_round = 20
    await _add_standing_call(
        db_path, other_round, _closing(hours_ago=48), division_id=OTHER_DIVISION_ID
    )

    channel = await _post_call(db_path)

    assert channel.deleted == []
    assert other_round in await _standing_rounds(db_path)


@pytest.mark.parametrize(
    ("deadline_hours", "cleared"),
    [(0, False), (DEADLINE_HOURS, True)],
    ids=["deadline-off-closes-at-the-race", "deadline-two-hours-before"],
)
async def test_a_deadline_of_zero_closes_at_the_round_itself(tmp_path, deadline_hours, cleared):
    """With the deadline switched off, check-in closes when the race starts. A round that
    started 23 hours ago is then still inside its day; with a two-hour deadline the same round
    closed 25 hours ago and goes."""
    db_path = await _make_db(tmp_path, deadline_hours=deadline_hours)
    await _add_standing_call(db_path, EARLIER_ROUND, NOW - timedelta(hours=23))

    await _post_call(db_path)

    assert (EARLIER_ROUND not in await _standing_rounds(db_path)) is cleared


# ---------------------------------------------------------------------------
# The moment a call is judged as at
# ---------------------------------------------------------------------------


async def test_a_call_fired_early_judges_as_at_the_moment_it_was_due(tmp_path):
    """`/test-mode advance` fires a call days before it falls due and does not move the clock.

    The earlier round here closed a day before this call's due moment, which is still in the
    real future, and it goes exactly as it would in the season test mode stands in for. Judged
    by the wall clock instead, a test season would never take down a call at all, since every
    deadline it holds lies ahead of the real one.
    """
    db_path = await _make_db(tmp_path)
    await _add_standing_call(db_path, EARLIER_ROUND, _closing(hours_ago=24))

    channel = await _post_call(db_path, now=NOW - timedelta(days=3))

    assert sorted(channel.deleted) == sorted(_msg_ids(EARLIER_ROUND))
    assert await _standing_rounds(db_path) == {POSTED_ROUND}


async def test_a_call_posted_late_judges_as_at_the_moment_it_is_posted(tmp_path):
    """The counterpart: `/attendance post-check-in` puts a call up after it fell due. The earlier
    round had not even closed at the due moment, but had been closed 30 hours when the call went
    out, and goes. The due moment is a floor for a call fired early, never a ceiling."""
    db_path = await _make_db(tmp_path)
    posted_at = NOW + timedelta(days=2)
    closed_at = posted_at - timedelta(hours=30)
    await _add_standing_call(
        db_path, EARLIER_ROUND, closed_at + timedelta(hours=DEADLINE_HOURS)
    )

    channel = await _post_call(db_path, now=posted_at)

    assert sorted(channel.deleted) == sorted(_msg_ids(EARLIER_ROUND))
