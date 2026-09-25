"""Taking a check-in call down, putting it back, and the notices posted beside it.

Issue #208. `withdraw_rsvp_call`, `repost_rsvp_call`, `_post_no_reserve_notice` and
`_post_distribution_announcement` were unexecuted by any test. Between them they carry the
whole amendment path for attendance: what a league sees when a round moves after its
check-in call has already gone out.

The rule that matters most, and the one with no cover at all before this file, is that
**a repost carries the answers over**. `repost_rsvp_call`'s docstring is explicit —

    The recorded answers are **not** touched. They are what a repost carries over — a driver
    who said they were racing has not unsaid it because the round moved, and asking the
    division to answer again from nothing is how an amendment comes to look like nobody
    replied.

— and `test_a_repost_keeps_every_answer_already_given` is what holds it. A later reader
clearing `driver_round_attendance` alongside `rsvp_embed_messages` would look tidy, pass every
other test in the suite, and quietly wipe a division's answers every time a round was amended.

Its counterpart is `test_a_repost_discards_an_answer_from_a_driver_who_has_left`: the delete
is deliberately *not* "delete nothing", it is "delete the ones no longer of the division", and
the two tests sit either side of that distinction.

`withdraw_rsvp_call` removes three messages — the call, its last notice and its distribution
announcement — and must survive any of them being gone already, because Discord is where they
live and a league can delete one by hand. The row goes either way; a call that failed to
delete but kept its row is a round that can never be reposted.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.attendance.services import rsvp_service  # noqa: E402
from leaguebot.attendance.services.attendance_service import AttendanceService  # noqa: E402
from leaguebot.attendance.services.rsvp_service import (  # noqa: E402
    _post_distribution_announcement,
    _post_no_reserve_notice,
    repost_rsvp_call,
    withdraw_rsvp_call,
)

SERVER_ID = 8408
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 1
RSVP_CHANNEL_ID = 770077

CALL_MSG_ID = "900001"
LAST_NOTICE_MSG_ID = "900002"
DISTRIBUTION_MSG_ID = "900003"

#: Seated in the full-time team, and so of the division for as long as the seat holds.
FULL_TIME_PROFILE = 201
#: Seated in the reserve team.
RESERVE_PROFILE = 202


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, with_division_config: bool = True) -> str:
    db_path = os.path.join(str(tmp_path), "rsvp_lifecycle.db")
    await run_migrations(db_path)

    scheduled = datetime.now(timezone.utc) + timedelta(days=3)
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
            "VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?)",
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
        for profile_id, name in (
            (FULL_TIME_PROFILE, "Full Timer"),
            (RESERVE_PROFILE, "Stand In"),
        ):
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, discord_user_id, current_state, is_test_driver, "
                "test_display_name) VALUES (?, ?, 'ACTIVE', 1, ?)",
                (profile_id, str(profile_id), name),
            )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (20, 10, 1, ?)",
            (FULL_TIME_PROFILE,),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (21, 11, 1, ?)",
            (RESERVE_PROFILE,),
        )
        for profile_id, seat_id in ((FULL_TIME_PROFILE, 20), (RESERVE_PROFILE, 21)):
            await db.execute(
                "INSERT INTO driver_season_assignments "
                "(driver_profile_id, season_id, division_id, team_seat_id) "
                "VALUES (?, ?, ?, ?)",
                (profile_id, SEASON_ID, DIVISION_ID, seat_id),
            )
        await db.commit()
    return db_path


async def _seed_embed_row(
    db_path: str, *, last_notice: str | None = None, distribution: str | None = None
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at, "
            " last_notice_msg_id, distribution_msg_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ROUND_ID,
                DIVISION_ID,
                CALL_MSG_ID,
                str(RSVP_CHANNEL_ID),
                datetime.now(timezone.utc).isoformat(),
                last_notice,
                distribution,
            ),
        )
        await db.commit()


async def _seed_answers(db_path: str, answers: dict[int, str]) -> None:
    async with get_connection(db_path) as db:
        for profile_id, status in answers.items():
            await db.execute(
                "INSERT INTO driver_round_attendance "
                "(round_id, division_id, driver_profile_id, rsvp_status) VALUES (?, ?, ?, ?)",
                (ROUND_ID, DIVISION_ID, profile_id, status),
            )
        await db.commit()


async def _answers(db_path: str) -> dict[int, str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id, rsvp_status FROM driver_round_attendance "
            "WHERE round_id = ?",
            (ROUND_ID,),
        )
        return {r["driver_profile_id"]: r["rsvp_status"] for r in await cursor.fetchall()}


async def _embed_rows(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM rsvp_embed_messages WHERE round_id = ?", (ROUND_ID,)
        )
        return (await cursor.fetchone())["n"]


def _make_channel() -> MagicMock:
    """A channel whose `fetch_message` hands back a deletable message."""
    channel = MagicMock()
    channel.deleted: list[int] = []

    async def _fetch(message_id: int) -> MagicMock:
        message = MagicMock()
        message.id = message_id
        message.delete = AsyncMock(
            side_effect=lambda: channel.deleted.append(message_id)
        )
        return message

    channel.fetch_message = AsyncMock(side_effect=_fetch)
    sent = MagicMock()
    sent.id = 990099
    channel.send = AsyncMock(return_value=sent)
    return channel


def _make_bot(db_path: str, channel: MagicMock | None = None) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.get_channel = MagicMock(return_value=channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


# ---------------------------------------------------------------------------
# withdraw_rsvp_call
# ---------------------------------------------------------------------------


async def test_withdrawing_a_call_that_was_never_posted_reports_nothing_to_do(tmp_path):
    """The return value is what tells an amendment whether a repost is needed at all."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path, _make_channel())

    assert await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot) is False


async def test_withdrawing_a_call_deletes_it_and_forgets_the_row(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    assert await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot) is True

    assert channel.deleted == [int(CALL_MSG_ID)]
    assert await _embed_rows(db_path) == 0


async def test_withdrawing_takes_the_last_notice_and_the_announcement_with_it(tmp_path):
    """All three are posted for one round. Leaving either behind gives the division a
    last-notice ping for a call that is no longer standing."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(
        db_path, last_notice=LAST_NOTICE_MSG_ID, distribution=DISTRIBUTION_MSG_ID
    )
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    assert sorted(channel.deleted) == sorted(
        int(m) for m in (CALL_MSG_ID, LAST_NOTICE_MSG_ID, DISTRIBUTION_MSG_ID)
    )


async def test_a_message_already_deleted_by_hand_does_not_stop_the_withdrawal(tmp_path):
    """Discord is where these live and a league can delete one itself. The row must go
    either way, or the round can never be reposted."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    channel.fetch_message = AsyncMock(
        side_effect=discord.HTTPException(MagicMock(), "unknown message")
    )
    bot = _make_bot(db_path, channel)

    assert await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot) is True
    assert await _embed_rows(db_path) == 0


async def test_a_channel_the_bot_can_no_longer_see_still_clears_the_row(tmp_path):
    """The channel may have been deleted outright. Same reasoning: the row goes."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    bot = _make_bot(db_path, channel=None)

    assert await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot) is True
    assert await _embed_rows(db_path) == 0


async def test_a_message_discord_refuses_to_delete_is_reported(tmp_path):
    """A cancellation must be able to tell the admin a call is still standing (#175). The row
    goes all the same, as it always has."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    channel.fetch_message = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "missing permissions")
    )
    bot = _make_bot(db_path, channel)
    undeleted: list[str] = []

    assert await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot, undeleted=undeleted) is True

    assert undeleted == [str(CALL_MSG_ID)]
    assert await _embed_rows(db_path) == 0


async def test_a_message_already_gone_is_not_reported(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    channel.fetch_message = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404), "unknown message")
    )
    bot = _make_bot(db_path, channel)
    undeleted: list[str] = []

    await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot, undeleted=undeleted)

    assert undeleted == []


async def test_a_channel_gone_reports_every_message_in_it(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(
        db_path, last_notice=LAST_NOTICE_MSG_ID, distribution=DISTRIBUTION_MSG_ID
    )
    bot = _make_bot(db_path, channel=None)
    undeleted: list[str] = []

    await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot, undeleted=undeleted)

    assert sorted(undeleted) == sorted(
        str(m) for m in (CALL_MSG_ID, LAST_NOTICE_MSG_ID, DISTRIBUTION_MSG_ID)
    )


async def test_withdrawing_leaves_every_recorded_answer_untouched(tmp_path):
    """Withdrawal is half of a repost, and the answers are what the repost carries over."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "DECLINED"})
    bot = _make_bot(db_path, _make_channel())

    await withdraw_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    assert await _answers(db_path) == {
        FULL_TIME_PROFILE: "ACCEPTED",
        RESERVE_PROFILE: "DECLINED",
    }


# ---------------------------------------------------------------------------
# repost_rsvp_call
# ---------------------------------------------------------------------------


@pytest.fixture
def notice():
    """Stub the call poster — `run_rsvp_notice` has its own cover."""
    with patch.object(rsvp_service, "run_rsvp_notice", new=AsyncMock(return_value=None)) as m:
        yield m


async def test_a_repost_keeps_every_answer_already_given(tmp_path, notice):
    """The rule this file exists for. A driver who said they were racing has not unsaid it
    because the round moved; clearing the answers here would make every amendment look to
    the league like nobody had replied."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "DECLINED"})
    bot = _make_bot(db_path, _make_channel())

    await repost_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    assert await _answers(db_path) == {
        FULL_TIME_PROFILE: "ACCEPTED",
        RESERVE_PROFILE: "DECLINED",
    }


async def test_a_repost_discards_an_answer_from_a_driver_who_has_left(tmp_path, notice):
    """The counterpart. The delete is "the ones no longer of the division", not "none" —
    a departed driver's name must not appear on the new call."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "ACCEPTED"})
    async with get_connection(db_path) as db:
        # The reserve gives up their seat, so they are no longer of the division.
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL WHERE id = 21")
        await db.commit()
    bot = _make_bot(db_path, _make_channel())

    await repost_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    assert await _answers(db_path) == {FULL_TIME_PROFILE: "ACCEPTED"}


async def test_a_repost_with_an_empty_division_discards_every_answer(tmp_path, notice):
    """The `NOT IN ()` branch: an empty roster cannot be expressed as a placeholder list,
    so it takes a separate delete. Without it the SQL would be malformed."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "ACCEPTED"})
    async with get_connection(db_path) as db:
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL")
        await db.commit()
    bot = _make_bot(db_path, _make_channel())

    await repost_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    assert await _answers(db_path) == {}


async def test_a_repost_posts_the_call_again(tmp_path, notice):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    bot = _make_bot(db_path, _make_channel())

    await repost_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    notice.assert_awaited_once_with(ROUND_ID, bot)


async def test_a_repost_of_a_round_with_no_standing_call_still_posts_one(tmp_path, notice):
    """`/season amend` reaches here without knowing whether the call went out."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path, _make_channel())

    await repost_rsvp_call(ROUND_ID, DIVISION_ID, bot)

    notice.assert_awaited_once()


# ---------------------------------------------------------------------------
# _post_no_reserve_notice
# ---------------------------------------------------------------------------


async def test_the_no_reserve_notice_is_posted_and_recorded(tmp_path):
    """Recording the message id is what lets the round's cleanup take it down."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _post_no_reserve_notice(ROUND_ID, DIVISION_ID, bot)

    channel.send.assert_awaited_once()
    assert "No reserves were placed" in channel.send.await_args.args[0]
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        assert (await cursor.fetchone())["distribution_msg_id"] == "990099"


async def test_the_no_reserve_notice_is_skipped_without_a_configured_channel(
    tmp_path, caplog
):
    db_path = await _make_db(tmp_path, with_division_config=False)
    bot = _make_bot(db_path, _make_channel())

    with caplog.at_level("WARNING"):
        await _post_no_reserve_notice(ROUND_ID, DIVISION_ID, bot)

    assert "no attendance config" in caplog.text


async def test_the_no_reserve_notice_is_skipped_when_the_channel_is_not_cached(
    tmp_path, caplog
):
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path, channel=None)

    with caplog.at_level("WARNING"):
        await _post_no_reserve_notice(ROUND_ID, DIVISION_ID, bot)

    assert "not in bot cache" in caplog.text


async def test_a_failed_no_reserve_notice_records_no_message(tmp_path, caplog):
    """A send that fails must not leave a message id pointing at nothing, which the round's
    cleanup would try to delete."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "forbidden"))
    bot = _make_bot(db_path, channel)

    with caplog.at_level("ERROR"):
        await _post_no_reserve_notice(ROUND_ID, DIVISION_ID, bot)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        assert (await cursor.fetchone())["distribution_msg_id"] is None


# ---------------------------------------------------------------------------
# _post_distribution_announcement — FR-025 / FR-026
# ---------------------------------------------------------------------------


async def _accept_reserve(db_path: str, *, team_id: int | None, standby: int = 0) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status, assigned_team_id, "
            " is_standby) VALUES (?, ?, ?, 'ACCEPTED', ?, ?)",
            (ROUND_ID, DIVISION_ID, RESERVE_PROFILE, team_id, standby),
        )
        await db.commit()


async def test_no_eligible_reserves_posts_no_announcement(tmp_path):
    """FR-026. A round nobody stood in for gets no announcement at all."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    channel.send.assert_not_awaited()


async def test_a_full_time_driver_s_acceptance_is_not_a_distribution(tmp_path):
    """The query is filtered to `src_ti.is_reserve = 1`. Without that every driver who
    said yes would be announced as though they had been placed."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED"})
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    channel.send.assert_not_awaited()


async def test_a_placed_reserve_is_announced_with_their_team(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _accept_reserve(db_path, team_id=10)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    body = channel.send.await_args.args[0]
    assert "Reserve Distribution Results" in body
    assert "Alpha" in body
    assert "Stand In" in body


async def test_a_standby_reserve_is_announced_as_standby(tmp_path):
    """A reserve beyond the available vacancies. Announcing them as placed would send a
    driver to a race they have no seat in."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _accept_reserve(db_path, team_id=None, standby=1)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    assert "Standby" in channel.send.await_args.args[0]


async def test_a_reserve_with_neither_seat_nor_standby_is_announced_as_unassigned(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _accept_reserve(db_path, team_id=None, standby=0)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    assert "no assignment" in channel.send.await_args.args[0]


async def test_the_announcement_is_skipped_without_a_configured_channel(tmp_path, caplog):
    db_path = await _make_db(tmp_path, with_division_config=False)
    await _accept_reserve(db_path, team_id=10)
    bot = _make_bot(db_path, _make_channel())

    with caplog.at_level("WARNING"):
        await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    assert "no attendance config" in caplog.text


async def test_the_announcement_is_skipped_when_the_channel_is_not_cached(tmp_path, caplog):
    db_path = await _make_db(tmp_path)
    await _accept_reserve(db_path, team_id=10)
    bot = _make_bot(db_path, channel=None)

    with caplog.at_level("WARNING"):
        await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    assert "not in bot cache" in caplog.text


async def test_a_failed_announcement_records_no_message(tmp_path, caplog):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _accept_reserve(db_path, team_id=10)
    channel = _make_channel()
    channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "forbidden"))
    bot = _make_bot(db_path, channel)

    with caplog.at_level("ERROR"):
        await _post_distribution_announcement(ROUND_ID, DIVISION_ID, bot)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        assert (await cursor.fetchone())["distribution_msg_id"] is None
