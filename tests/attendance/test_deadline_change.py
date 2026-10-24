"""A round's check-in deadline on the change queue (#439, slice 6b, C30): asked by its timer and
the restart, run by jobs of its own — the reserves placed (`distribute`), the call's buttons
taken off (`lock_call`) and the placements announced (`announce`).

The league is `ongoing_league`'s with attendance on, its queue rebuilt on a real
`AttendanceService`: Pro has Lewis and Max seated at Ferrari (three seats), its check-in channel
is 603, and round 3's call stands there as message 7001 with its last notice 7002, Lewis having
accepted and Max not answered. Round 3 is moved to an hour off, so its two-hour check-in deadline
passed an hour ago; no announcement is recorded and the deadline is not marked as run. Where a
test needs reserves, Pro gains a Reserve team holding Rhea (user 105) and Sam (user 106).

A test asks the deadline of the queue as its timer does, as the bot (`ask_check_in_deadline`),
and runs the queue. The reserves are placed on the answers as they stood at the deadline (Q7);
the announcement goes to the division's check-in channel as it stands when the job runs
(attendance spec :106; decision 13 of the slice) and is recorded with that channel; a refused
lock or announcement stops the queue, and a discarded announcement is named and not posted again
(Q8).
"""
from __future__ import annotations

import inspect
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import discord
import pytest

from leaguebot.attendance.services.attendance_service import AttendanceService
from tests.support.change_queue import (
    acknowledgement,
    attach_queue,
    change_rows,
    discard_job,
    http_error,
    refuse_the_job,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
)
from tests.support.review_league import channel
from tests.support.season_league import (
    CALL_MESSAGES,
    DIVISIONS,
    PRO,
    SEASON_ID,
    SeasonLeague,
    ongoing_league,
    round_id,
)

KIND = "attendance.check_in.deadline"
CLEAN_UP_KIND = "attendance.check_in.clean_up"
CHECK_IN = DIVISIONS[PRO][3].checkin
#: The check-in channel Pro's is moved to.
MOVED = 608
ROUND_3 = round_id(PRO, 3)
CALL = CALL_MESSAGES[0]
#: Pro's Reserve team, and the two reserves seated in it.
RESERVE_TEAM = 39
RHEA, SAM = 105, 106
FERRARI = 31


async def _league(tmp_path: Any) -> SeasonLeague:
    """The league of the module docstring: round 3 an hour off, its deadline passed and not
    run, its call standing and nothing announced."""
    league = await ongoing_league(tmp_path, attendance=True)
    league.bot.attendance_service = AttendanceService(league.db_path)
    attach_queue(league.bot, league.db_path, now=league.clock)
    when = league.clock.now + timedelta(hours=1)
    await league.write("UPDATE rounds SET scheduled_at = ? WHERE id = ?",
                       when.replace(tzinfo=None).isoformat(), ROUND_3)
    del league.channel(CHECK_IN).messages[CALL_MESSAGES[2]]
    await league.write("UPDATE rsvp_embed_messages SET distribution_msg_id = NULL "
                       "WHERE round_id = ?", ROUND_3)
    return league


def _deadline(league: SeasonLeague) -> Any:
    """Round 3's check-in deadline: two hours before the race, an hour ago."""
    return league.clock.now - timedelta(hours=1)


async def _reserves(league: SeasonLeague, *, rhea: Any, sam: Any) -> None:
    """Pro gains a Reserve team seating Rhea and Sam, each having accepted round 3's call at the
    moment given."""
    await league.write(
        "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
        "VALUES (?, ?, 'Reserve', 'Reserve', 2, 1)", RESERVE_TEAM, PRO,
    )
    for seat, (user_id, name, accepted) in enumerate(((RHEA, "Rhea", rhea),
                                                       (SAM, "Sam", sam)), start=1):
        profile = user_id - 60
        await league.write(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
            "VALUES (?, ?, 'ASSIGNED', 0)", profile, str(user_id),
        )
        await league.write(
            "INSERT INTO signup_records (season_id, discord_user_id, server_display_name) "
            "VALUES (?, ?, ?)", SEASON_ID, str(user_id), name,
        )
        await league.write(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, ?, ?)", RESERVE_TEAM, seat, profile,
        )
        await league.write(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status, accepted_at) VALUES (?, ?, ?, 'ACCEPTED', ?)",
            ROUND_3, PRO, profile, accepted.isoformat(),
        )


async def _placement(league: SeasonLeague, user_id: int) -> tuple[Any, int]:
    """The team a reserve was placed in, and whether they were put on standby."""
    [row] = await league.rows(
        "SELECT assigned_team_id, is_standby FROM driver_round_attendance "
        "WHERE round_id = ? AND driver_profile_id = ?", ROUND_3, user_id - 60,
    )
    return row["assigned_team_id"], row["is_standby"]


async def _ask(league: SeasonLeague) -> None:
    """Ask the queue for round 3's deadline as its timer does; the queue is not run."""
    from leaguebot.attendance.services.check_in_changes import ask_check_in_deadline
    from leaguebot.core.models.change import ChangeOrigin

    await ask_check_in_deadline(league.bot.change_queue, league.db_path, ROUND_3,
                                origin=ChangeOrigin.BOT)


def _sent(league: SeasonLeague, cid: int = CHECK_IN) -> list[int]:
    """The ids of the messages posted in channel *cid*, in order."""
    return [mid for kind, ch, mid in league.events if kind == "send" and ch == cid]


def _edited(league: SeasonLeague) -> list[int]:
    """The ids of the messages edited in Pro's check-in channel, in order."""
    return [mid for kind, ch, mid in league.events if kind == "edit" and ch == CHECK_IN]


async def _call_row(league: SeasonLeague) -> dict[str, Any]:
    [row] = await league.rows("SELECT * FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3)
    return row


async def _deadline_changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return [row for row in await change_rows(league.db_path) if row["kind"] == KIND]


def _refusing_edit(league: SeasonLeague, message_id: int) -> None:
    """Discord refuses every edit of *message_id* in Pro's check-in channel."""
    league.channel(CHECK_IN).messages[message_id].edit = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Permissions")
    )


async def _restart_asks(league: SeasonLeague) -> list[int]:
    """Run the restart's recovery of check-in deadlines, returning the rounds whose deadline it
    asked of the queue."""
    from leaguebot.__main__ import _recover_rsvp_views_and_deadlines

    asked: list[int] = []

    async def _record(_queue: Any, _db_path: Any, rid: int, **_kwargs: Any) -> None:
        asked.append(rid)

    with patch("leaguebot.attendance.services.check_in_changes.ask_check_in_deadline",
               new=AsyncMock(side_effect=_record)):
        await _recover_rsvp_views_and_deadlines(league.bot)
    return asked


async def test_the_timer_asks_for_the_deadline_and_the_queue_distributes_locks_and_announces(
    tmp_path,
):
    """Nothing is edited or posted as the deadline is asked. The queue runs its jobs in order —
    `distribute`, `lock_call`, `announce` — taking the buttons off the call (7001) and posting
    one announcement in Pro's check-in channel, recorded on the call with the deadline marked
    as run. The deadline timer's callback asks the queue rather than running the deadline."""
    import leaguebot.__main__ as bot_module

    league = await _league(tmp_path)

    await _ask(league)
    assert _sent(league) == [] and _edited(league) == []
    await run_queue(league.bot)

    assert _edited(league) == [CALL]
    [posted] = _sent(league)
    row = await _call_row(league)
    assert row["distribution_msg_id"] == str(posted)
    assert row["deadline_run_at"] is not None
    [change] = await _deadline_changes(league)
    assert [step["name"] for step in await step_rows(league.db_path, change["id"])] == [
        "distribute", "lock_call", "announce",
    ]
    assert await stopped_job(league.db_path) is None
    assert "ask_check_in_deadline(" in inspect.getsource(bot_module.main)


async def test_the_distribution_reads_the_answers_as_they_stood_at_the_deadline(tmp_path):
    """Q7: Rhea accepted half an hour before the deadline; Sam's acceptance was saved ten minutes
    after it, before the queue reached the deadline. Rhea is placed at Ferrari, which has a seat
    free and Max unanswered; Sam is left out, neither placed nor on standby."""
    league = await _league(tmp_path)
    deadline = _deadline(league)
    await _reserves(league, rhea=deadline - timedelta(minutes=30),
                    sam=deadline + timedelta(minutes=10))

    await _ask(league)
    await run_queue(league.bot)

    assert await _placement(league, RHEA) == (FERRARI, 0)
    assert await _placement(league, SAM) == (None, 0)
    assert await stopped_job(league.db_path) is None


async def test_placing_the_reserves_cannot_be_discarded(tmp_path):
    """Rhea accepted round 3's call half an hour before its deadline. The deadline is asked, and
    the save of its first job, which places the reserves, fails and stops the queue at
    `distribute`. A league admin's Discard on it is refused, saying the reserves of round 3 in Pro
    would never be placed; the queue stays stopped there, Rhea is not placed and the deadline is
    not marked as run. Once the fault is lifted, a Retry places Rhea at Ferrari, takes the call's
    buttons off and posts the announcement."""
    league = await _league(tmp_path)
    await _reserves(league, rhea=_deadline(league) - timedelta(minutes=30),
                    sam=_deadline(league) + timedelta(minutes=10))
    await refuse_the_job(league.db_path, KIND, "distribute", True)
    await _ask(league)
    await run_queue(league.bot)
    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "distribute"

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == (
        "⛔ This job can't be discarded: without it the reserves of round 3 in **Pro** would "
        "never be placed. Fix what stopped it and press **Retry**."
    )
    assert (await stopped_job(league.db_path))["id"] == stopped["id"]
    assert await _placement(league, RHEA) == (None, 0)
    assert (await _call_row(league))["deadline_run_at"] is None

    await refuse_the_job(league.db_path, KIND, "distribute", False)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert await _placement(league, RHEA) == (FERRARI, 0)
    assert _edited(league) == [CALL]
    [posted] = _sent(league)
    assert (await _call_row(league))["distribution_msg_id"] == str(posted)


async def test_a_refused_lock_stops_the_queue_with_the_reserves_placed(tmp_path):
    """Rhea accepted before the deadline. Discord refuses to edit the call: the queue stops at
    `lock_call`, Rhea already placed at Ferrari and the deadline marked as run, and nothing is
    announced."""
    league = await _league(tmp_path)
    await _reserves(league, rhea=_deadline(league) - timedelta(minutes=30),
                    sam=_deadline(league) + timedelta(minutes=10))
    _refusing_edit(league, CALL)

    await _ask(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "lock_call"
    assert await _placement(league, RHEA) == (FERRARI, 0)
    assert (await _call_row(league))["deadline_run_at"] is not None
    assert _sent(league) == []


async def test_a_refused_announcement_stops_the_queue_and_a_discard_names_it_leaving_the_placements(
    tmp_path,
):
    """Q8: Rhea accepted before the deadline. Discord refuses the announcement: the queue stops
    at `announce`, with Rhea placed and the call locked. A league admin discards it: the queue
    runs on, the log channel's line names the announcement as not posted, Rhea stays placed, and
    no announcement is recorded while the deadline stays marked as run."""
    league = await _league(tmp_path)
    await _reserves(league, rhea=_deadline(league) - timedelta(minutes=30),
                    sam=_deadline(league) + timedelta(minutes=10))
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")

    await _ask(league)
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path)
    assert stopped is not None and stopped["name"] == "announce"
    assert _edited(league) == [CALL]
    told_before = league.log()

    await discard_job(league.bot)

    assert await stopped_job(league.db_path) is None
    assert "announcement" in league.log()[len(told_before):].lower()
    assert await _placement(league, RHEA) == (FERRARI, 0)
    row = await _call_row(league)
    assert row["distribution_msg_id"] is None
    assert row["deadline_run_at"] is not None


async def test_a_discarded_announcement_is_not_run_again_at_a_restart(tmp_path):
    """Discord refuses the announcement and a league admin discards it. The bot then starts
    again with the deadline past and no announcement recorded: the restart asks no deadline of
    the queue for round 3, its mark saying it ran."""
    league = await _league(tmp_path)
    league.channel(CHECK_IN).send_fails = http_error(discord.Forbidden, status=403,
                                                     text="Missing Permissions")
    await _ask(league)
    await run_queue(league.bot)
    await discard_job(league.bot)
    assert (await _call_row(league))["distribution_msg_id"] is None

    assert ROUND_3 not in await _restart_asks(league)


async def test_a_call_gone_completes_its_lock_with_a_line(tmp_path):
    """The call (7001) was deleted by hand before the queue reached the deadline: the lock
    finds it gone and completes, the queue does not stop, and the announcement is posted and
    recorded all the same."""
    league = await _league(tmp_path)
    del league.channel(CHECK_IN).messages[CALL]

    await _ask(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path) is None
    [change] = await _deadline_changes(league)
    [lock] = [step for step in await step_rows(league.db_path, change["id"])
              if step["name"] == "lock_call"]
    assert lock["done_at"] is not None
    [posted] = _sent(league)
    assert (await _call_row(league))["distribution_msg_id"] == str(posted)


async def test_a_deadline_with_no_call_closes_the_check_in_and_asks_the_clean_up(tmp_path):
    """Round 3's call is off the record when the deadline is asked; its answers (Lewis
    accepted, Max not answered) are kept from it. The queue posts nothing in the check-in
    channel; the deadline clears the answers, the log channel saying the round counts nothing
    against anyone, and asks the round's clean-up of the queue, which marks the check-in over."""
    league = await _league(tmp_path)
    await league.write("DELETE FROM rsvp_embed_messages WHERE round_id = ?", ROUND_3)

    await _ask(league)
    await run_queue(league.bot)

    assert _sent(league) == []
    assert await league.rows(
        "SELECT * FROM driver_round_attendance WHERE round_id = ?", ROUND_3
    ) == []
    assert "nothing against anyone" in league.log()
    assert CLEAN_UP_KIND in [row["kind"] for row in await change_rows(league.db_path)]
    [row] = await league.rows("SELECT checkin_cleared FROM rounds WHERE id = ?", ROUND_3)
    assert row["checkin_cleared"] == 1
    assert await stopped_job(league.db_path) is None


async def test_the_start_up_leaves_a_deadline_in_hand_alone(tmp_path):
    """Round 3's deadline is asked of the queue and has not yet run when the bot starts again:
    the restart asks no second deadline for it, and the queue then runs the first, once."""
    league = await _league(tmp_path)
    await _ask(league)

    assert ROUND_3 not in await _restart_asks(league)
    await run_queue(league.bot)

    assert len(await _deadline_changes(league)) == 1
    assert len(_sent(league)) == 1


async def test_the_announcement_goes_to_the_division_s_current_check_in_channel_and_is_recorded_with_it(
    tmp_path,
):
    """Decision 13: Pro's check-in channel was moved to 608 after round 3's call was posted in
    603. The call is locked where it stands, in 603; the announcement goes to 608 and is
    recorded with 608 as its channel, so that the round's clean-up deletes it from there."""
    league = await _league(tmp_path)
    moved = league._recording(channel(MOVED, league.events))
    moved.guild = league.guild
    league.channels[MOVED] = moved
    await league.write(
        "UPDATE attendance_division_config SET rsvp_channel_id = ? WHERE division_id = ?",
        str(MOVED), PRO,
    )

    await _ask(league)
    await run_queue(league.bot)

    assert _edited(league) == [CALL]
    assert _sent(league) == []
    [posted] = _sent(league, MOVED)
    row = await _call_row(league)
    assert (row["distribution_msg_id"], row["distribution_channel_id"]) == (
        str(posted), str(MOVED),
    )
