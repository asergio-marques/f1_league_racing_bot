"""Taking a check-in call down, putting it back, and the notices posted beside it.

Issue #208. Posting a call again in an earlier one's place, the notice that no reserves were
placed and the distribution announcement were unexecuted by any test. Between them they carry the
whole amendment path for attendance: what a league sees when a round moves after its check-in
call has already gone out.

The rule that matters most, and the one with no cover at all before this file, is that
**a repost carries the answers over**: a driver who said they were racing has not unsaid it
because the round moved, and asking the division to answer again from nothing is how an
amendment comes to look like nobody replied. `test_a_repost_keeps_every_answer_already_given` is
what holds it. A later reader clearing `driver_round_attendance` alongside `rsvp_embed_messages`
would look tidy, pass every other test in the suite, and quietly wipe a division's answers every
time a round was amended.

Its counterpart is `test_a_repost_discards_an_answer_from_a_driver_who_has_left`: the delete
is deliberately *not* "delete nothing", it is "delete the ones no longer of the division", and
the two tests sit either side of that distinction.

Since #439 (slice 6b) the call is posted, and posted again in an earlier call's place, by its own
change on the queue (`ask_check_in_call`, with `replacing` for a repost), so the call's tests here
ask the queue and run it: a call that does not post stops the queue, and the stop notice says what
is left for the round. The earlier call's messages come down as delete jobs after it, and a day
after the round the clean-up's own delete jobs take a call down (`test_check_in_clean_up_change.py`),
so the taking-down function these tests once called directly is gone. The deadline's announcement,
or its notice that no reserves were placed, is posted by the deadline's own job on the queue too
(`ask_check_in_deadline`), on the answers as they stood at the deadline.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    attach_queue,
    discard_job,
    http_error,
    league_double,
    run_queue,
    stopped_job,
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




# ---------------------------------------------------------------------------
# The call, and the call posted again in an earlier one's place, on the change queue (#439, 6b)
# ---------------------------------------------------------------------------
#
# A call is asked of the change queue as its timer asks it (`ask_check_in_call`), and posted in
# an earlier call's place where asked with `replacing`, as an amendment asks it. Each test runs
# the queue; a call that does not post stops it, and the stop notice in the log channel says
# what is left for the round.

#: A channel the earlier call stands in, other than the one the division is set to now.
EARLIER_CHANNEL_ID = 770078

#: What the stop notice says is left where no call stands and no answers are kept.
NOTHING_LEFT = "no attendance rows are open for this round until this call is posted"
#: What the stop notice says the round comes to where no call stands and no answers are kept
#: (attendance spec :120: "the round will count nothing against anyone until the call is posted").
COUNTS_NOTHING = "will count nothing against anyone"


#: The log channel `_make_db` sets in the server's config, where the queue writes its lines.
DB_LOG_CHANNEL_ID = 300


def _queue_bot(db_path: str, *channel_ids: int) -> MagicMock:
    """The league's bot double with its change queue, attendance on and images off, the server
    holding *channel_ids*, each recording its sends and deletes in `bot.events`; the earlier
    call's messages (900001, 900002) stand in each. The log channel the database names
    (`DB_LOG_CHANNEL_ID`) is the double's own log channel, so every line the queue writes there,
    a Discard's among them, reaches `bot.log_channel.sent`."""
    from tests.support.review_league import channel as recording_channel

    bot = league_double(db_path)
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.events = []
    bot.channels = {cid: recording_channel(cid, bot.events) for cid in channel_ids}
    for chan in bot.channels.values():
        chan.seed(int(CALL_MSG_ID), "the earlier call")
        chan.seed(int(LAST_NOTICE_MSG_ID), "the earlier last notice")
    known = {bot.log_channel.id: bot.log_channel,
             DB_LOG_CHANNEL_ID: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    bot.get_channel = MagicMock(
        side_effect=lambda cid: bot.channels.get(int(cid)) or known.get(int(cid)))
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot


async def _ask(bot: MagicMock, *, replacing: bool = False) -> None:
    """Ask the queue for round 1's call, as its timer does or, *replacing*, as an amendment
    posts it again; then run the queue."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_call
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_call(bot.change_queue, bot.db_path, ROUND_ID, origin=ChangeOrigin.BOT,
                            replacing=replacing)
    await run_queue(bot)


def _sent(bot: MagicMock) -> list[int]:
    return [mid for kind, cid, mid in bot.events if kind == "send" and cid == RSVP_CHANNEL_ID]


def _logged(bot: MagicMock) -> str:
    return "\n".join(bot.log_channel.sent)


async def test_a_repost_keeps_every_answer_already_given(tmp_path):
    """The rule this file exists for. A driver who said they were racing has not unsaid it
    because the round moved; clearing the answers here would make every amendment look to
    the league like nobody had replied. The call posted in the earlier one's place keeps both."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "DECLINED"})
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask(bot, replacing=True)

    assert len(_sent(bot)) == 1
    assert await _answers(db_path) == {
        FULL_TIME_PROFILE: "ACCEPTED",
        RESERVE_PROFILE: "DECLINED",
    }


async def test_a_repost_discards_an_answer_from_a_driver_who_has_left(tmp_path):
    """The counterpart. The delete is "the ones no longer of the division", not "none" —
    a departed driver's name must not appear on the new call."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "ACCEPTED"})
    async with get_connection(db_path) as db:
        # The reserve gives up their seat, so they are no longer of the division.
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL WHERE id = 21")
        await db.commit()
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask(bot, replacing=True)

    assert await _answers(db_path) == {FULL_TIME_PROFILE: "ACCEPTED"}


async def test_a_repost_with_an_empty_division_discards_every_answer(tmp_path):
    """Nobody is seated in the division any more: every answer kept for the round goes."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "ACCEPTED"})
    async with get_connection(db_path) as db:
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL")
        await db.commit()
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask(bot, replacing=True)

    assert await _answers(db_path) == {}


async def test_a_repost_posts_the_call_again(tmp_path):
    """Round 1's call stands. Asked again in its place, the queue posts a new call, records it in
    the earlier one's place and does not stop."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask(bot, replacing=True)

    [posted] = _sent(bot)
    stored = await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID)
    assert stored is not None and stored.message_id == str(posted)
    assert await stopped_job(db_path) is None


async def test_a_repost_of_a_round_with_no_standing_call_still_posts_one(tmp_path):
    """`/season amend` reaches here without knowing whether the call went out: asked in an
    earlier call's place where none stands, the call is posted all the same."""
    db_path = await _make_db(tmp_path)
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask(bot, replacing=True)

    [posted] = _sent(bot)
    stored = await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID)
    assert stored is not None and stored.message_id == str(posted)


#: What the earlier call's message answers when it is looked up after a refused post: an error
#: that is not Discord saying it is gone, so the call is not read as gone (`_call_vanished`).
PROBE_FAULTS = {
    "Discord refuses the post, the call's lookup forbidden":
        discord.Forbidden(MagicMock(status=403), "missing access"),
    "Discord refuses the post, the call's lookup failing with a 503":
        discord.HTTPException(MagicMock(status=503), "service unavailable"),
    "Discord refuses the post, the call's lookup timing out": asyncio.TimeoutError(),
    "Discord refuses the post, the connection dropped on the call's lookup":
        aiohttp.ClientConnectionError("connection reset"),
    "Discord refuses the post, the network failing on the call's lookup":
        OSError("network unreachable"),
}

#: The lookup faults that are not Discord's own but the connection's.
TRANSPORT_FAULTS = list(PROBE_FAULTS)[2:]


@pytest.mark.parametrize("fault", [
    "Discord refuses the post",
    "the channel is gone",
    "the channel set is gone, the earlier call's stands",
    *list(PROBE_FAULTS)[:2],
    *TRANSPORT_FAULTS,
])
async def test_a_repost_that_fails_leaves_the_earlier_call_standing_and_says_so(tmp_path, fault):
    """Round 1's call stands (900001, its last notice 900002), answered by both drivers. Posting
    it again in its place fails, and the queue stops at it. (Discord refuses the post; the
    channel set is gone, the earlier call's stands, the division's channel having been changed
    since) The earlier call stands, its record and messages untouched, its answers kept, and the
    stop notice says the earlier call still stands and its answers count, never that no
    attendance rows are open (owner, 2026-10-09: "Fold it in"). (the channel is gone, the earlier
    call's with it) No call can be seen: its record is dropped, its answers kept, and the stop
    notice says no call for the round can be seen (owner, 2026-10-09: "Drop the record, say
    so"). (the call's lookup forbidden, or failing) Discord does not say the earlier call is
    gone, so it is taken to stand, as when the post alone is refused. In every case the stop
    notice is the only report: no separate NOT POSTED line reaches the log channel (owner,
    2026-10-11: "Notice says what's left")."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path, last_notice=LAST_NOTICE_MSG_ID)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "DECLINED"})
    if fault == "the channel is gone":
        bot = _queue_bot(db_path)
    elif fault == "the channel set is gone, the earlier call's stands":
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET channel_id = ? WHERE round_id = ?",
                (str(EARLIER_CHANNEL_ID), ROUND_ID),
            )
            await db.commit()
        bot = _queue_bot(db_path, EARLIER_CHANNEL_ID)
    else:
        bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
        bot.channels[RSVP_CHANNEL_ID].send_fails = discord.HTTPException(
            MagicMock(status=403), "Missing Permissions")
        if fault in PROBE_FAULTS:
            bot.channels[RSVP_CHANNEL_ID].fetch_message = AsyncMock(
                side_effect=PROBE_FAULTS[fault])

    await _ask(bot, replacing=True)

    assert await stopped_job(db_path) is not None
    stored = await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID)
    assert await _answers(db_path) == {
        FULL_TIME_PROFILE: "ACCEPTED",
        RESERVE_PROFILE: "DECLINED",
    }
    notice = _logged(bot)
    assert "NOT POSTED" not in notice
    assert "no attendance rows were opened" not in notice
    assert NOTHING_LEFT not in notice
    assert COUNTS_NOTHING not in notice
    if fault == "the channel is gone":
        assert stored is None
        assert "no call for this round can be seen" in notice
        assert "the earlier call still stands" not in notice
        return
    assert stored is not None and stored.message_id == CALL_MSG_ID
    assert stored.last_notice_msg_id == LAST_NOTICE_MSG_ID
    assert [event for event in bot.events if event[0] == "delete"] == []
    assert "the earlier call still stands, and the answers given to it count" in notice


async def test_a_repost_that_lands_withdraws_the_earlier_call_after_it(tmp_path):
    """Round 1's call stands (900001, its last notice 900002), answered by both drivers. The call
    is posted again in its place and lands: only then does the earlier call come down, its
    messages deleted and its record replaced by the new call's, the answers carried over. The new
    call is recorded before the first of the earlier call's messages is deleted, so that a press
    on the new call meanwhile finds it, and a stop between them cannot leave it untracked."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path, last_notice=LAST_NOTICE_MSG_ID)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "DECLINED"})
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
    recorded_at_delete: list[str | None] = []
    for message_id in (int(CALL_MSG_ID), int(LAST_NOTICE_MSG_ID)):
        message = bot.channels[RSVP_CHANNEL_ID].messages[message_id]
        deleting = message.delete.side_effect

        async def _delete(*args: Any, _deleting: Any = deleting, **kwargs: Any) -> None:
            stored = await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID)
            recorded_at_delete.append(stored.message_id if stored else None)
            await _deleting(*args, **kwargs)

        message.delete = AsyncMock(side_effect=_delete)

    await _ask(bot, replacing=True)

    [posted] = _sent(bot)
    sent_at = bot.events.index(("send", RSVP_CHANNEL_ID, posted))
    for message_id in (int(CALL_MSG_ID), int(LAST_NOTICE_MSG_ID)):
        assert bot.events.index(("delete", RSVP_CHANNEL_ID, message_id)) > sent_at
    assert recorded_at_delete == [str(posted), str(posted)]
    stored = await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID)
    assert stored is not None and stored.message_id == str(posted)
    assert stored.last_notice_msg_id is None
    assert await _answers(db_path) == {
        FULL_TIME_PROFILE: "ACCEPTED",
        RESERVE_PROFILE: "DECLINED",
    }


async def test_a_last_notice_recorded_while_a_repost_is_sent_comes_down_with_the_earlier_call(
    tmp_path,
):
    """Round 1's call stands (900001) with no last notice yet. While its repost is being sent,
    the last notice's timer posts and records its message (900005) on the earlier call. Once the
    repost lands, that last notice comes down with the earlier call, read as it stands just
    before the new call takes its record."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
    chan = bot.channels[RSVP_CHANNEL_ID]
    chan.seed(900005, "the last notice")
    sending = chan.send.side_effect

    async def _send(*args: Any, **kwargs: Any) -> Any:
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET last_notice_msg_id = '900005' "
                "WHERE round_id = ? AND division_id = ?",
                (ROUND_ID, DIVISION_ID),
            )
            await db.commit()
        return await sending(*args, **kwargs)

    chan.send = AsyncMock(side_effect=_send)

    await _ask(bot, replacing=True)

    deleted = sorted(mid for kind, _cid, mid in bot.events if kind == "delete")
    assert deleted == [int(CALL_MSG_ID), 900005]
    [posted] = _sent(bot)
    stored = await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID)
    assert stored is not None and stored.message_id == str(posted)
    assert stored.last_notice_msg_id is None


@pytest.mark.parametrize("fault", [
    pytest.param(OSError("network unreachable"), id="the network failing"),
    pytest.param(aiohttp.ClientConnectionError("connection reset"), id="the connection dropped"),
    pytest.param(asyncio.TimeoutError(), id="the send timing out"),
])
async def test_a_call_whose_send_fails_on_the_connection_is_reported_as_not_posted(
    tmp_path, fault,
):
    """No call stands for round 1 and none is kept. Its post fails on the connection rather than
    on Discord: as a refusal does, it stops the queue, no call recorded, the stop notice saying
    no attendance rows are open and the round will count nothing against anyone until the call
    is posted. A send that timed out may have reached Discord all the same: since the queue posts
    the call again on its next try, the notice says so and that any second copy is to be deleted
    by hand, naming the fault's kind rather than nothing (attendance spec :120; owner, 2026-10-09:
    "Fold it in", 2026-10-11)."""
    db_path = await _make_db(tmp_path)
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
    bot.channels[RSVP_CHANNEL_ID].send_fails = fault

    await _ask(bot)

    assert await stopped_job(db_path) is not None
    assert await _embed_rows(db_path) == 0
    notice = _logged(bot)
    assert NOTHING_LEFT in notice
    assert COUNTS_NOTHING in notice
    timed_out = isinstance(fault, TimeoutError)
    assert ("the bot posts it again on its next try" in notice) == timed_out
    assert ("any second copy is to be deleted by hand" in notice) == timed_out
    assert "check the channel" not in notice
    if timed_out:
        assert "TimeoutError" in notice


@pytest.mark.parametrize("fault", ["Discord refuses the post", "the channel is gone"])
async def test_a_call_that_fails_beside_answers_kept_says_they_count(tmp_path, fault):
    """No call stands for round 1, but answers to an earlier call are kept for it (both drivers
    answered it before it was taken down). Its post fails, Discord refusing it or the check-in
    channel gone: the queue stops, its notice saying the answers are kept and count, never that
    no attendance rows are open (owner, 2026-10-09: "Make the line tell the truth"); a Discard
    names `/attendance post-check-in` to post the call by hand."""
    db_path = await _make_db(tmp_path)
    await _seed_answers(db_path, {FULL_TIME_PROFILE: "ACCEPTED", RESERVE_PROFILE: "DECLINED"})
    if fault == "the channel is gone":
        bot = _queue_bot(db_path)
    else:
        bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
        bot.channels[RSVP_CHANNEL_ID].send_fails = discord.HTTPException(
            MagicMock(status=403), "Missing Permissions")

    await _ask(bot)

    assert await stopped_job(db_path) is not None
    assert await _embed_rows(db_path) == 0
    assert await _answers(db_path) == {
        FULL_TIME_PROFILE: "ACCEPTED",
        RESERVE_PROFILE: "DECLINED",
    }
    notice = _logged(bot)
    assert "answers given to an earlier call of this round are kept, and count" in notice
    assert NOTHING_LEFT not in notice
    assert "no attendance rows were opened" not in notice

    await discard_job(bot)

    assert "/attendance post-check-in" in _logged(bot)[len(notice):]


@pytest.mark.parametrize("fault", ["Discord refuses the post", "the channel is gone"])
async def test_a_call_that_fails_with_no_answers_kept_says_no_rows_were_opened(tmp_path, fault):
    """No call and no answers for round 1: a post that fails stops the queue, its notice saying,
    as it always has, that no attendance rows are open and the round counts nothing against
    anyone until the call is posted; a Discard names `/attendance post-check-in`."""
    db_path = await _make_db(tmp_path)
    if fault == "the channel is gone":
        bot = _queue_bot(db_path)
    else:
        bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
        bot.channels[RSVP_CHANNEL_ID].send_fails = discord.HTTPException(
            MagicMock(status=403), "Missing Permissions")

    await _ask(bot)

    assert await stopped_job(db_path) is not None
    notice = _logged(bot)
    assert NOTHING_LEFT in notice
    assert COUNTS_NOTHING in notice

    await discard_job(bot)

    assert "/attendance post-check-in" in _logged(bot)[len(notice):]


# ---------------------------------------------------------------------------
# The deadline's announcement on the change queue (#439, slice 6b, C30) — FR-025 / FR-026
# ---------------------------------------------------------------------------
#
# The announcement, or the notice that no reserves were placed, is posted by the deadline's own
# `announce` job once its `distribute` job has placed the reserves on the answers as they stood
# at the deadline. Each test moves round 1 to an hour off, so its two-hour check-in deadline
# passed an hour ago, asks the queue for the deadline as its timer does, and runs it.

#: A second reserve, accepting after the deadline.
LATE_PROFILE = 204


async def _past_its_deadline(db_path: str) -> datetime:
    """Move round 1 to an hour off, so its deadline passed an hour ago; the deadline."""
    now = datetime.now(timezone.utc)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                         ((now + timedelta(hours=1)).isoformat(), ROUND_ID))
        await db.commit()
    return now - timedelta(hours=1)


async def _accept(db_path: str, profile_id: int, at: datetime) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status, accepted_at) "
            "VALUES (?, ?, ?, 'ACCEPTED', ?)",
            (ROUND_ID, DIVISION_ID, profile_id, at.isoformat()),
        )
        await db.commit()


async def _decline(db_path: str, profile_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance "
            "(round_id, division_id, driver_profile_id, rsvp_status) "
            "VALUES (?, ?, ?, 'DECLINED')",
            (ROUND_ID, DIVISION_ID, profile_id),
        )
        await db.commit()


async def _second_reserve(db_path: str) -> None:
    """A second reserve, 'Late Comer', seated in the Reserve team."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, ?, 'ACTIVE', 1, 'Late Comer')",
            (LATE_PROFILE, str(LATE_PROFILE)),
        )
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (22, 11, 2, ?)", (LATE_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id) VALUES (?, ?, ?, 22)",
            (LATE_PROFILE, SEASON_ID, DIVISION_ID),
        )
        await db.commit()


async def _ask_deadline(bot: MagicMock) -> None:
    """Ask the queue for round 1's deadline as its timer does, and run the queue."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_deadline
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_deadline(bot.change_queue, bot.db_path, ROUND_ID, origin=ChangeOrigin.BOT)
    await run_queue(bot)


def _announced(bot: MagicMock) -> list[str]:
    """What was posted in the division's check-in channel, in order."""
    chan = bot.channels[RSVP_CHANNEL_ID]
    return [chan.messages[mid].content for mid in _sent(bot)]


async def _distribution_recorded(db_path: str) -> Any:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        return (await cursor.fetchone())["distribution_msg_id"]


async def test_the_no_reserve_notice_is_posted_and_recorded(tmp_path):
    """Recording the message id is what lets the round's cleanup take it down."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    deadline = await _past_its_deadline(db_path)
    await _accept(db_path, FULL_TIME_PROFILE, deadline - timedelta(hours=1))
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask_deadline(bot)

    [notice] = _announced(bot)
    assert "No reserves were placed" in notice
    [posted] = _sent(bot)
    assert await _distribution_recorded(db_path) == str(posted)


async def test_a_deadline_with_no_check_in_channel_configured_stops_the_queue_naming_the_command(
    tmp_path,
):
    """A bot change lacking its channel stops the queue, the notice naming the command that
    sets it, rather than the host's log alone saying so."""
    db_path = await _make_db(tmp_path, with_division_config=False)
    await _seed_embed_row(db_path)
    await _past_its_deadline(db_path)
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask_deadline(bot)

    assert _sent(bot) == []
    assert await stopped_job(db_path) is not None
    assert "/attendance channel rsvp" in _logged(bot)


async def test_a_deadline_whose_check_in_channel_the_bot_cannot_see_stops_the_queue(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _past_its_deadline(db_path)
    bot = _queue_bot(db_path)

    await _ask_deadline(bot)

    assert await stopped_job(db_path) is not None
    assert "/attendance channel rsvp" in _logged(bot)
    assert await _distribution_recorded(db_path) is None


async def test_a_refused_no_reserve_notice_stops_the_queue_recording_nothing(tmp_path):
    """A message id pointing at nothing would have the round's cleanup delete something else,
    or nothing: Discord refusing the notice stops the queue with nothing recorded."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    await _past_its_deadline(db_path)
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
    bot.channels[RSVP_CHANNEL_ID].send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access")

    await _ask_deadline(bot)

    assert await stopped_job(db_path) is not None
    assert await _distribution_recorded(db_path) is None


async def test_a_full_time_driver_s_acceptance_is_not_a_distribution(tmp_path):
    """Only reserves are distributed. Were every driver who said yes announced, they would
    read as though they had been placed: the deadline posts the no-reserve notice, naming
    nobody."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    deadline = await _past_its_deadline(db_path)
    await _accept(db_path, FULL_TIME_PROFILE, deadline - timedelta(hours=1))
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask_deadline(bot)

    [notice] = _announced(bot)
    assert "Reserve Distribution Results" not in notice
    assert "Full Timer" not in notice


async def test_a_placed_reserve_is_announced_with_their_team(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    deadline = await _past_its_deadline(db_path)
    await _decline(db_path, FULL_TIME_PROFILE)
    await _accept(db_path, RESERVE_PROFILE, deadline - timedelta(hours=1))
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask_deadline(bot)

    [body] = _announced(bot)
    assert "Reserve Distribution Results" in body
    assert "Alpha" in body
    assert "Stand In" in body


async def test_a_standby_reserve_is_announced_as_standby(tmp_path):
    """A reserve beyond the available vacancies. Announcing them as placed would send a
    driver to a race they have no seat in."""
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    deadline = await _past_its_deadline(db_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE team_instances SET max_seats = 1 WHERE id = 10")
        await db.commit()
    await _accept(db_path, FULL_TIME_PROFILE, deadline - timedelta(hours=2))
    await _accept(db_path, RESERVE_PROFILE, deadline - timedelta(hours=1))
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask_deadline(bot)

    [body] = _announced(bot)
    assert "Standby" in body


async def test_a_reserve_who_accepted_after_the_deadline_is_announced_as_unassigned(tmp_path):
    """The reserves are placed on the answers as they stood at the deadline (Q7): a reserve
    who accepted after it is neither seated nor on standby, and the announcement says so."""
    db_path = await _make_db(tmp_path)
    await _second_reserve(db_path)
    await _seed_embed_row(db_path)
    deadline = await _past_its_deadline(db_path)
    await _decline(db_path, FULL_TIME_PROFILE)
    await _accept(db_path, RESERVE_PROFILE, deadline - timedelta(hours=1))
    await _accept(db_path, LATE_PROFILE, deadline + timedelta(minutes=30))
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)

    await _ask_deadline(bot)

    [body] = _announced(bot)
    [late] = [line for line in body.splitlines() if "Late Comer" in line]
    assert "no assignment" in late


async def test_a_refused_announcement_stops_the_queue_recording_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_embed_row(db_path)
    deadline = await _past_its_deadline(db_path)
    await _decline(db_path, FULL_TIME_PROFILE)
    await _accept(db_path, RESERVE_PROFILE, deadline - timedelta(hours=1))
    bot = _queue_bot(db_path, RSVP_CHANNEL_ID)
    bot.channels[RSVP_CHANNEL_ID].send_fails = http_error(
        discord.Forbidden, status=403, text="Missing Access")

    await _ask_deadline(bot)

    assert await stopped_job(db_path) is not None
    assert await _distribution_recorded(db_path) is None


# ---------------------------------------------------------------------------
# reopen_check_in_on (#439, slice 4b, amendment A)
#
# `/round amend` on the change queue writes everything an amendment changes in one save. Where it
# reopens a round's check-in, what attendance resets — the round's cleared mark and the reserves'
# distribution — is attendance's own, so attendance writes it, on the connection the save hands
# it, committing nothing.
# ---------------------------------------------------------------------------

#: A second reserve, put on standby by the distribution.
STANDBY_PROFILE = 203


async def _distributed(db) -> dict[int, tuple]:
    cursor = await db.execute(
        "SELECT driver_profile_id, rsvp_status, assigned_team_id, is_standby "
        "FROM driver_round_attendance WHERE round_id = ?",
        (ROUND_ID,),
    )
    return {r["driver_profile_id"]: tuple(r)[1:] for r in await cursor.fetchall()}


async def _cleared(db) -> int:
    cursor = await db.execute("SELECT checkin_cleared FROM rounds WHERE id = ?", (ROUND_ID,))
    return (await cursor.fetchone())["checkin_cleared"]


async def test_reopening_a_check_in_on_the_connection_handed_commits_nothing(tmp_path):
    """Round 1's check-in has been cleared and its reserves distributed: Stand In placed in
    Alpha, and a second reserve on standby, both having accepted. Reopening the check-in on a
    connection marks the round not cleared and undoes the distribution, keeping both answers.
    Nothing of it outlasts a rollback."""
    from leaguebot.attendance.services.rsvp_service import reopen_check_in_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute("UPDATE rounds SET checkin_cleared = 1 WHERE id = ?", (ROUND_ID,))
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, ?, 'ACTIVE', 1, 'Second Reserve')",
            (STANDBY_PROFILE, str(STANDBY_PROFILE)),
        )
        for profile_id, team_id, standby in ((RESERVE_PROFILE, 10, 0), (STANDBY_PROFILE, None, 1)):
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status, assigned_team_id, is_standby) VALUES (?, ?, ?, 'ACCEPTED', ?, ?)",
                (ROUND_ID, DIVISION_ID, profile_id, team_id, standby),
            )
        await db.commit()

    async with get_connection(db_path) as db:
        await reopen_check_in_on(db, ROUND_ID)
        assert await _cleared(db) == 0
        assert await _distributed(db) == {
            RESERVE_PROFILE: ("ACCEPTED", None, 0),
            STANDBY_PROFILE: ("ACCEPTED", None, 0),
        }
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _cleared(db) == 1
        assert await _distributed(db) == {
            RESERVE_PROFILE: ("ACCEPTED", 10, 0),
            STANDBY_PROFILE: ("ACCEPTED", None, 1),
        }


async def test_clearing_a_round_s_answers_on_the_connection_handed_commits_nothing(tmp_path):
    """Round 1's reserve placed in Alpha, a second reserve on standby, both having accepted.
    Clearing the round's check-in answers on a connection deletes both answers, their placements
    with them. Nothing of it outlasts a rollback."""
    from leaguebot.attendance.services.rsvp_service import clear_check_in_answers_on

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
            "test_display_name) VALUES (?, ?, 'ACTIVE', 1, 'Second Reserve')",
            (STANDBY_PROFILE, str(STANDBY_PROFILE)),
        )
        for profile_id, team_id, standby in ((RESERVE_PROFILE, 10, 0), (STANDBY_PROFILE, None, 1)):
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status, assigned_team_id, is_standby) VALUES (?, ?, ?, 'ACCEPTED', ?, ?)",
                (ROUND_ID, DIVISION_ID, profile_id, team_id, standby),
            )
        await db.commit()

    async with get_connection(db_path) as db:
        await clear_check_in_answers_on(db, ROUND_ID)
        assert await _distributed(db) == {}
        await db.rollback()

    async with get_connection(db_path) as db:
        assert await _distributed(db) == {
            RESERVE_PROFILE: ("ACCEPTED", 10, 0),
            STANDBY_PROFILE: ("ACCEPTED", None, 1),
        }
