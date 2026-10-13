"""A round's check-in deadline runs once for each call (#429).

Two runs of one deadline can meet. The scheduler runs a job overdue by less than its five-minute
misfire grace as soon as it starts, and the restart's catch-up reaches the same deadline moments
later; two presses of `/test-mode advance` can do the same. What says a deadline has run — the
message it records on the call — is written at its very end, so neither could tell the other was
running, and the division was sent two announcements.

`run_rsvp_deadline` now runs under a lock of its round's and reads the call again once it holds
it. `test_two_deadline_runs_at_once_post_one_announcement` interleaves two runs for real: the
first to fetch the call waits for the second to arrive, so without the lock both reach the post.
A deadline whose message never posted has not run, and runs again, which is how a failed
announcement is retried.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services import rsvp_service
from leaguebot.attendance.services.attendance_service import AttendanceService

SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 429
CHANNEL_ID = 770001
CALL_MSG_ID = 900901
POSTED_MSG_ID = 990099


async def _make_db(
    tmp_path, *, with_call: bool = True, distribution_msg_id: str | None = None
) -> str:
    """A division with its check-in channel set, and a round whose call is standing."""
    db_path = os.path.join(str(tmp_path), "deadline_once.db")
    await run_migrations(db_path)
    scheduled = datetime.now(timezone.utc) + timedelta(hours=1)
    async with get_connection(db_path) as db:
        await db.execute("INSERT INTO attendance_config (id, module_enabled) VALUES (1, 1)")
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
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, str(CHANNEL_ID)),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', ?)",
            (ROUND_ID, DIVISION_ID, scheduled.isoformat()),
        )
        if with_call:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at, "
                " distribution_msg_id) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    ROUND_ID,
                    DIVISION_ID,
                    str(CALL_MSG_ID),
                    str(CHANNEL_ID),
                    datetime.now(timezone.utc).isoformat(),
                    distribution_msg_id,
                ),
            )
        await db.commit()
    return db_path


def _make_channel(*, wait_for_second_fetch: bool = False) -> MagicMock:
    """A check-in channel holding the call, recording what is posted to it.

    With *wait_for_second_fetch*, the first run to fetch the call waits — for up to a second —
    until a second run fetches it too. Two runs that do not exclude each other therefore both
    reach the post; two that do, the second waiting on the first, carry on after the timeout.
    """
    channel = MagicMock()
    call = MagicMock()
    call.edit = AsyncMock()
    fetches = 0
    second_arrived = asyncio.Event()

    async def _fetch(_message_id: int) -> MagicMock:
        nonlocal fetches
        fetches += 1
        if fetches >= 2:
            second_arrived.set()
        elif wait_for_second_fetch:
            try:
                await asyncio.wait_for(second_arrived.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        return call

    channel.fetch_message = AsyncMock(side_effect=_fetch)
    posted = MagicMock()
    posted.id = POSTED_MSG_ID
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


async def _recorded(db_path: str) -> str | None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT distribution_msg_id FROM rsvp_embed_messages WHERE round_id = ?",
            (ROUND_ID,),
        )
        row = await cursor.fetchone()
    return row["distribution_msg_id"] if row is not None else None


async def test_two_deadline_runs_at_once_post_one_announcement(tmp_path):
    """The scheduler's late run and the restart's catch-up, side by side."""
    db_path = await _make_db(tmp_path)
    channel = _make_channel(wait_for_second_fetch=True)
    bot = _make_bot(db_path, channel)

    await asyncio.gather(
        rsvp_service.run_rsvp_deadline(ROUND_ID, bot),
        rsvp_service.run_rsvp_deadline(ROUND_ID, bot),
    )

    assert channel.send.await_count == 1, "two runs of one deadline both posted"
    assert await _recorded(db_path) == str(POSTED_MSG_ID)


# A deadline that has run is marked so on its call (`deadline_run_at`), apart from the message it
# posts (#439, slice 6b): a discarded announcement leaves the placements made and the deadline
# run, and it must not be run again, at a restart or otherwise. These use `ongoing_league` with
# attendance on, its queue rebuilt on a real `AttendanceService`: Pro's round 3 is an hour off,
# its two-hour deadline passed, its call standing in Pro's check-in channel (603) as message 7001
# with its last notice 7002. The deadline is asked of the queue as its timer asks it, and the
# queue run.

NO_DEADLINE_MARK = (
    "#439: the deadline has no mark of its own yet, and is not yet a change on the queue"
)


async def _league_past_its_deadline(tmp_path, *, marked: bool, announced: bool):
    from tests.support.change_queue import attach_queue
    from tests.support.season_league import CALL_MESSAGES, DIVISIONS, PRO, ongoing_league, round_id

    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    now = league.clock.now
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       (now + timedelta(hours=1)).replace(tzinfo=None).isoformat(),
                       round_id(PRO, 3))
    if not announced:
        del league.channel(DIVISIONS[PRO][3].checkin).messages[CALL_MESSAGES[2]]
        await league.write("UPDATE rsvp_embed_messages SET distribution_msg_id = NULL "
                           "WHERE round_id = ?", round_id(PRO, 3))
    if marked:
        await league.write("UPDATE rsvp_embed_messages SET deadline_run_at = ? WHERE round_id = ?",
                           (now - timedelta(minutes=30)).isoformat(), round_id(PRO, 3))
    return league


async def _ask_deadline(league) -> None:
    from leaguebot.attendance.services.check_in_changes import ask_check_in_deadline
    from leaguebot.core.models.change import ChangeOrigin
    from tests.support.change_queue import run_queue
    from tests.support.season_league import PRO, round_id

    await ask_check_in_deadline(league.bot.change_queue, league.db_path, round_id(PRO, 3),
                                origin=ChangeOrigin.BOT)
    await run_queue(league.bot)


def _check_in_events(league) -> list[tuple[str, int]]:
    from tests.support.season_league import DIVISIONS, PRO

    return [(kind, mid) for kind, cid, mid in league.events
            if cid == DIVISIONS[PRO][3].checkin]


async def _call(league) -> dict:
    from tests.support.season_league import PRO, round_id

    [row] = await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?",
                              round_id(PRO, 3))
    return row


@pytest.mark.xfail(strict=True, reason=NO_DEADLINE_MARK)
async def test_a_deadline_whose_mark_is_set_does_nothing(tmp_path):
    """It has run, and its announcement stands. The call is not locked again and nothing is
    posted."""
    from tests.support.change_queue import stopped_job

    league = await _league_past_its_deadline(tmp_path, marked=True, announced=True)

    await _ask_deadline(league)

    assert _check_in_events(league) == []
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=NO_DEADLINE_MARK)
async def test_a_deadline_marked_run_is_not_run_again_whatever_became_of_its_announcement(
    tmp_path,
):
    """Its announcement was discarded, so no message is recorded on the call; the mark still
    says the deadline ran, and the placements it made stand without a second announcement."""
    from tests.support.change_queue import stopped_job

    league = await _league_past_its_deadline(tmp_path, marked=True, announced=False)

    await _ask_deadline(league)

    assert _check_in_events(league) == []
    assert await stopped_job(league.db_path) is None
    assert (await _call(league))["distribution_msg_id"] is None


@pytest.mark.xfail(strict=True, reason=NO_DEADLINE_MARK)
async def test_a_deadline_whose_message_never_posted_is_run_again(tmp_path):
    """No mark and no message: the deadline has not run, so it locks the call, posts its
    announcement, and records both the message and the mark."""
    from tests.support.change_queue import stopped_job
    from tests.support.season_league import CALL_MESSAGES

    league = await _league_past_its_deadline(tmp_path, marked=False, announced=False)

    await _ask_deadline(league)

    assert await stopped_job(league.db_path) is None
    events = _check_in_events(league)
    assert ("edit", CALL_MESSAGES[0]) in events
    sent = [mid for kind, mid in events if kind == "send"]
    assert len(sent) == 1
    call = await _call(league)
    assert call["distribution_msg_id"] == str(sent[0])
    assert call["deadline_run_at"] is not None


async def test_a_deadline_with_no_call_standing_posts_nothing(tmp_path):
    """A deadline closes the call standing, and with none there is nothing to close. It used to
    distribute and post its notice anyway, with no call to record the message on — so nothing
    took the notice down a day after the round, and it stood in the channel for good."""
    db_path = await _make_db(tmp_path, with_call=False)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    channel.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# Past the deadline with no call standing, nothing counts (owner, 2026-10-09)
# ---------------------------------------------------------------------------


async def _answer_kept(db_path: str) -> None:
    """A driver's answer to an earlier call of the round, kept after it was taken down."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (201, '201', 'ASSIGNED')"
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (?, ?, 201, 'ACCEPTED')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()


async def _round_state(db_path: str) -> tuple[int, int]:
    """How many answers the round holds, and whether its check-in is marked over."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_round_attendance WHERE round_id = ?", (ROUND_ID,)
        )
        answers = (await cursor.fetchone())["n"]
        cursor = await db.execute("SELECT checkin_cleared FROM rounds WHERE id = ?", (ROUND_ID,))
        cleared = (await cursor.fetchone())["checkin_cleared"]
    return answers, cleared


async def test_a_deadline_with_no_call_standing_clears_the_answers_kept_and_says_so(tmp_path):
    """No call stands for the round, its earlier call having been taken down, but a driver's
    answer to it is kept. The deadline fires, the bot running throughout: the answer is cleared,
    the check-in marked over, and the log channel says the round counts nothing against anyone,
    as a restart past the deadline would (owner, 2026-10-09: "Past the deadline, nothing
    counts"). Nothing is posted in the check-in channel."""
    db_path = await _make_db(tmp_path, with_call=False)
    await _answer_kept(db_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    assert await _round_state(db_path) == (0, 1)
    [line] = [c.args[0] for c in bot.output_router.post_log.await_args_list]
    assert line.startswith("ATTENDANCE | check-in call | NOT POSTED")
    assert "this round will count nothing against anyone" in line
    channel.send.assert_not_awaited()


async def test_a_deadline_with_a_call_standing_keeps_its_answers(tmp_path):
    """With the call standing, the deadline runs as it always has: the answers given to it are
    kept, its check-in is not marked over, and nothing says the call was not posted."""
    db_path = await _make_db(tmp_path)
    await _answer_kept(db_path)
    bot = _make_bot(db_path, _make_channel())

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)

    assert await _round_state(db_path) == (1, 0)
    assert not any("NOT POSTED" in c.args[0] for c in bot.output_router.post_log.await_args_list)


async def test_a_deadline_and_a_give_up_closing_one_check_in_report_it_once(tmp_path, monkeypatch):
    """No call stands for the round and an answer is kept. The deadline closes its check-in
    while a restart's give-up of the same round runs too, committing first, inside the close,
    before its guarded save. The give-up clears the answer and tells the log channel; the
    deadline's close finds the check-in over and says nothing: the round is reported once."""
    import leaguebot.__main__ as bot_module

    db_path = await _make_db(tmp_path, with_call=False)
    await _answer_kept(db_path)
    bot = _make_bot(db_path, _make_channel())
    row = {"round_id": ROUND_ID, "round_number": 3, "division_id": DIVISION_ID,
           "division_name": "Division 1", "season_number": 1}
    gave_up: list[bool] = []
    withdrawing = rsvp_service.withdraw_rsvp_call

    async def _give_up_first(*args: object, **kwargs: object) -> object:
        if not gave_up:
            gave_up.append(await bot_module._give_up_missed_check_in_call(bot, row))
        return await withdrawing(*args, **kwargs)

    monkeypatch.setattr(rsvp_service, "withdraw_rsvp_call", _give_up_first)

    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)
    # The give-up committed inside the deadline's close, so the race is the one exercised.
    assert gave_up == [True]

    assert await _round_state(db_path) == (0, 1)
    lines = [c.args[0] for c in bot.output_router.post_log.await_args_list]
    assert len([line for line in lines if "NOT POSTED" in line]) == 1


async def test_a_call_waiting_to_post_when_the_deadline_closes_the_check_in_posts_nothing(
    tmp_path,
):
    """No call stands for the round and an answer is kept; the deadline closes its check-in,
    clearing the answer. A post of the round's call that was waiting meanwhile (a repost after a
    take-down, a late post) then runs: the check-in is over, so nothing is posted and no
    attendance rows are opened (owner, 2026-10-09: "Fold it in")."""
    from unittest.mock import patch

    db_path = await _make_db(tmp_path, with_call=False)
    await _answer_kept(db_path)
    channel = _make_channel()
    bot = _make_bot(db_path, channel)
    await rsvp_service.run_rsvp_deadline(ROUND_ID, bot)
    assert await _round_state(db_path) == (0, 1)

    with patch.object(rsvp_service, "_checkin_attachment", AsyncMock(return_value=None)):
        await rsvp_service.run_rsvp_notice(ROUND_ID, bot)

    channel.send.assert_not_awaited()
    assert await _round_state(db_path) == (0, 1)
    assert await bot.attendance_service.get_embed_message(ROUND_ID, DIVISION_ID) is None
