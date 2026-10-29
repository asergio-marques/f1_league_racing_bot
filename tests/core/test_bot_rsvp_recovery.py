"""What a restart does about check-in — re-arming buttons, and catching up a missed deadline.

Issue #208. `_recover_rsvp_views_and_deadlines` runs on every start and does two things a
restart would otherwise lose: it re-arms the persistent buttons on every check-in call still
standing, and it asks the change queue for any deadline whose moment passed while the bot was
down (#439, slice 6b), running none itself; the queue runs it once it starts.

**Neither happens for a server with attendance switched off** (issue #114). A restart is one of
the three paths the core specification names, and both halves would break the module-output
rule: re-arming the buttons revives check-in for a league that turned it off, and the catch-up
distributes its reserves into seats hours after the fact.
`test_a_switched_off_module_s_buttons_are_not_re_armed` and its deadline counterpart are the
regression tests, and the gate is applied by a join in the query rather than a check per row —
which is why the embed rows, carrying no server id of their own, are filtered against a resolved
set.

**A missed last-notice is deliberately *not* fired retroactively** (FR-029). A reminder to
answer before a deadline that has already passed is worse than no reminder, so the recovery
covers deadlines only.

**The deadline is skipped where it has already run.** A second run would redistribute seats the
division has already been told about, or post its notice again. A call carrying the mark its
deadline's distribution sets has run (#429; #439, slice 6b), whether or not any driver was
placed and whatever became of its announcement.

**Every failure is contained.** This runs during startup, so a database that cannot be read, a
row whose timestamp will not parse, or an ask of the queue that raises must not take the bot
down — each is logged and the rest of the recovery continues.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.__main__ import _recover_rsvp_views_and_deadlines
from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.change_queue import (
    attach_queue,
    change_rows,
    league_double,
    run_queue,
    text_channel,
)

SERVER_ID = 12908
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
MESSAGE_ID = 900901


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    attendance_enabled: bool = True,
    season_status: str = "ACTIVE",
    round_status: str = "NOT_RUN",
    deadline_hours: int = 6,
    hours_until_round: int = -1,
    placed: bool = False,
    distribution_msg_id: str | None = None,
    marked: bool = False,
    with_embed: bool = True,
) -> str:
    """A division with a check-in call standing, and a round *hours_until_round* away.

    A negative offset puts the round — and so its deadline — in the past. Taken from the
    clock rather than pinned, because the recovery reads the clock itself.

    What a deadline leaves behind is in two places, and they are seeded apart: *placed* gives a
    reserve a team, as the distribution writes it onto the driver, and *distribution_msg_id*
    records the message the deadline posted, as it writes it onto the call. *marked* sets the
    call's mark that its deadline has run (`deadline_run_at`), which the deadline's
    distribution writes.
    """
    db_path = os.path.join(str(tmp_path), "rsvp_recovery.db")
    await run_migrations(db_path)
    scheduled = datetime.now(timezone.utc) + timedelta(hours=hours_until_round)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO attendance_config "
            "(id, module_enabled, rsvp_deadline_hours) VALUES (?, ?, ?)",
            (1, int(attendance_enabled), deadline_hours),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) "
            "VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', ?, ?)",
            (ROUND_ID, DIVISION_ID, scheduled.isoformat(), round_status),
        )
        if with_embed:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at, "
                " distribution_msg_id) "
                "VALUES (?, ?, ?, '770001', ?, ?)",
                (
                    ROUND_ID,
                    DIVISION_ID,
                    str(MESSAGE_ID),
                    datetime.now(timezone.utc).isoformat(),
                    distribution_msg_id,
                ),
            )
            if marked:
                await db.execute(
                    "UPDATE rsvp_embed_messages SET deadline_run_at = ? WHERE round_id = ?",
                    (datetime.now(timezone.utc).isoformat(), ROUND_ID),
                )
        if placed:
            await db.execute(
                "INSERT INTO driver_profiles "
                "(id, discord_user_id, current_state) "
                "VALUES (1, '4242', 'ACTIVE')"
            )
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
                (DIVISION_ID,),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance "
                "(round_id, division_id, driver_profile_id, rsvp_status, assigned_team_id) "
                "VALUES (?, ?, 1, 'ACCEPTED', 10)",
                (ROUND_ID, DIVISION_ID),
            )
        await db.commit()
    return db_path


DEADLINE = "attendance.check_in.deadline"



def _bot(db_path: str, *, embed_error: Exception | None = None):
    """The league's bot double with a real change queue on it, and the call's message among the
    standing check-in calls the attendance service lists (or *embed_error* raised listing them)."""
    bot = league_double(db_path)
    bot.add_view = MagicMock()
    bot.attendance_service = MagicMock()
    bot.attendance_service.get_all_embed_messages = AsyncMock(
        side_effect=embed_error,
        return_value=[
            SimpleNamespace(round_id=ROUND_ID, message_id=str(MESSAGE_ID))
        ]
        if embed_error is None
        else None,
    )
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot


async def _deadlines(db_path: str) -> int:
    """How many check-in deadlines have been asked of the queue."""
    return len([row for row in await change_rows(db_path) if row["kind"] == DEADLINE])


async def _recover(bot) -> int:
    """Run the recovery, returning how many check-in deadlines it asked of the queue. The queue
    is not run."""
    await _recover_rsvp_views_and_deadlines(bot)
    return await _deadlines(bot.db_path)


# ---------------------------------------------------------------------------
# Re-arming the buttons
# ---------------------------------------------------------------------------


async def test_a_standing_call_s_buttons_are_re_armed(tmp_path):
    """The view is persistent but the process that registered it has gone; without this
    every button on every standing call stops responding after a restart."""
    db_path = await _make_db(tmp_path, hours_until_round=48)
    bot = _bot(db_path)

    await _recover(bot)

    bot.add_view.assert_called_once()
    assert bot.add_view.call_args.kwargs["message_id"] == MESSAGE_ID


async def test_a_switched_off_module_s_buttons_are_not_re_armed(tmp_path):
    """Issue #114. Re-arming would revive check-in for a league that turned it off — the
    module-output rule, reached by the restart path."""
    db_path = await _make_db(tmp_path, attendance_enabled=False, hours_until_round=48)
    bot = _bot(db_path)

    await _recover(bot)

    bot.add_view.assert_not_called()


async def test_a_failure_fetching_the_embeds_stops_the_recovery_quietly(tmp_path, caplog):
    """It runs during startup, so raising would take the bot down over a check-in call. The
    recovery stops there: no button is re-armed and no deadline asked of the queue."""
    db_path = await _make_db(tmp_path)
    bot = _bot(db_path, embed_error=RuntimeError("database is locked"))

    with caplog.at_level("ERROR"):
        asked = await _recover(bot)

    assert asked == 0
    bot.add_view.assert_not_called()
    assert "failed to fetch embed messages" in caplog.text


async def test_a_view_that_cannot_be_re_armed_does_not_stop_the_rest(tmp_path, caplog):
    """One malformed message id must not cost every other division its buttons."""
    db_path = await _make_db(tmp_path, hours_until_round=48)
    bot = _bot(db_path)
    bot.add_view = MagicMock(side_effect=ValueError("bad message id"))

    with caplog.at_level("WARNING"):
        await _recover(bot)

    assert "could not re-arm view" in caplog.text


# ---------------------------------------------------------------------------
# Catching up a missed deadline
# ---------------------------------------------------------------------------


async def test_a_deadline_that_passed_while_the_bot_was_down_is_run(tmp_path):
    """The deadline is what moves reserve drivers into seats; missing it leaves a division
    a driver short on race day. The restart asks the queue for it, once, and runs nothing
    itself; the queue runs it once it starts."""
    db_path = await _make_db(tmp_path, hours_until_round=-1)
    bot = _bot(db_path)

    assert await _recover(bot) == 1


async def test_a_deadline_still_ahead_is_not_run_early(tmp_path):
    """Its job is re-armed by the scheduler; asking it now would close check-in before
    the division had finished answering."""
    db_path = await _make_db(tmp_path, hours_until_round=48, deadline_hours=6)
    bot = _bot(db_path)

    assert await _recover(bot) == 0


async def test_a_deadline_exactly_now_is_run(tmp_path):
    """The comparison is `<=`, so a deadline reached at the moment of the restart counts
    as missed rather than as pending: it is asked of the queue."""
    db_path = await _make_db(tmp_path, hours_until_round=6, deadline_hours=6)
    bot = _bot(db_path)

    assert await _recover(bot) == 1


async def test_a_switched_off_module_s_deadline_is_not_caught_up(tmp_path):
    """Issue #114's other half: the catch-up would distribute reserves into seats hours
    after the fact, for a league that had switched check-in off."""
    db_path = await _make_db(
        tmp_path, attendance_enabled=False, hours_until_round=-1
    )
    bot = _bot(db_path)

    assert await _recover(bot) == 0


@pytest.mark.parametrize("placed", [
    pytest.param(True, id="reserves placed"),
    pytest.param(False, id="no reserves placed"),
])
async def test_a_deadline_marked_as_run_is_not_run_again(tmp_path, placed):
    """A second run would redistribute seats the division has already been told about. The
    deadline that ran is marked as run on the call, its announcement recorded beside the mark:
    the restart asks nothing, whether or not it placed a reserve.

    Issue #429. A deadline with no reserve to place writes nothing onto a driver, but it marks
    the call as run like any other — and that is what says it ran. Judged by the placements it
    looked as if it never had, and every restart before the round's check-in came down posted
    another "No reserves were placed"."""
    db_path = await _make_db(
        tmp_path, hours_until_round=-1, placed=placed, distribution_msg_id="990099", marked=True
    )
    bot = _bot(db_path)

    assert await _recover(bot) == 0


async def test_a_call_whose_deadline_recorded_no_message_is_run_whatever_the_placements_say(
    tmp_path,
):
    """The call's own record decides, never the drivers' placements (#429). Two things leave
    placements on a call whose deadline has not run: a call posted again after an amendment
    carries over the placements of the call it replaced, and a deadline whose distribution was
    never saved. Its mark unset, the restart asks the queue for the deadline."""
    db_path = await _make_db(tmp_path, hours_until_round=-1, placed=True)
    bot = _bot(db_path)

    assert await _recover(bot) == 1


def _check_in_bot(db_path: str) -> tuple:
    """The league's bot double on the real attendance service, Division 1's check-in channel
    (770001) among its channels, images off, and a real change queue on it; with the channel."""
    from leaguebot.attendance.services.attendance_service import AttendanceService

    bot = league_double(db_path)
    bot.add_view = MagicMock()
    bot.attendance_service = AttendanceService(db_path)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    check_in = text_channel(770001)
    known = {770001: check_in, bot.log_channel.id: bot.log_channel,
             bot.interaction_channel.id: bot.interaction_channel}
    bot.get_channel = MagicMock(side_effect=lambda cid: known.get(int(cid)))
    attach_queue(bot, db_path, now=datetime.now(timezone.utc))
    return bot, check_in


async def test_a_restart_after_a_deadline_that_placed_nobody_posts_no_second_notice(tmp_path):
    """Issue #429, end to end. Division 1's round is an hour off, its six-hour deadline passed
    while the bot was down, and no reserve is there to place. The first start asks the queue
    for the deadline and the queue runs it for real: nobody is placed, the no-reserves notice
    goes out in the check-in channel, and the call is marked as run. The second start must find
    it done and ask nothing."""
    db_path = await _make_db(tmp_path, hours_until_round=1)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, rsvp_channel_id) "
            "VALUES (?, '770001')",
            (DIVISION_ID,),
        )
        await db.commit()
    bot, check_in = _check_in_bot(db_path)

    await _recover_rsvp_views_and_deadlines(bot)
    await run_queue(bot)
    await _recover_rsvp_views_and_deadlines(bot)
    await run_queue(bot)

    assert await _deadlines(db_path) == 1, "the restart did not ask, or asked again"
    assert len(check_in.sent) == 1, "a restart posted the deadline's notice again"


@pytest.mark.parametrize("status", [
    # Nobody is racing it, so there is nothing to distribute into.
    pytest.param({"round_status": "CANCELLED"}, id="a cancelled round"),
    # A season in setup has not started; its check-in calls are not yet owed.
    pytest.param({"season_status": "SETUP"}, id="a season not running"),
])
async def test_a_round_not_to_be_run_has_no_deadline_to_catch_up(tmp_path, status):
    """A round called off, or one of a season not yet running, has its passed deadline left
    alone: the restart asks nothing."""
    db_path = await _make_db(tmp_path, hours_until_round=-1, **status)
    bot = _bot(db_path)

    assert await _recover(bot) == 0


async def test_a_deadline_of_zero_hours_falls_at_the_round(tmp_path):
    """Zero is not "no deadline" — it means check-in closes when the round starts."""
    db_path = await _make_db(tmp_path, hours_until_round=1, deadline_hours=0)
    bot = _bot(db_path)

    assert await _recover(bot) == 0


async def test_a_failing_deadline_run_does_not_stop_the_startup(tmp_path, caplog):
    """The recovery runs before the bot is ready; an exception here would leave the league
    with no bot at all rather than one missing a distribution. Asking the queue for the
    deadline raises: the restart logs an error naming the round, and goes on."""
    db_path = await _make_db(tmp_path, hours_until_round=-1)
    bot = _bot(db_path)

    with caplog.at_level("ERROR"), patch(
        "leaguebot.attendance.services.check_in_changes.ask_check_in_deadline",
        new=AsyncMock(side_effect=RuntimeError("database is locked")),
    ):
        await _recover_rsvp_views_and_deadlines(bot)

    assert any(
        record.levelname == "ERROR" and str(ROUND_ID) in record.getMessage()
        for record in caplog.records
    )


async def test_a_naive_timestamp_is_read_as_utc(tmp_path):
    """Rows written before the timestamps carried a zone still exist; read as local time
    the deadline would move by the host's offset."""
    db_path = await _make_db(tmp_path, hours_until_round=-1)
    naive = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET scheduled_at = ? WHERE id = ?", (naive.isoformat(), ROUND_ID)
        )
        await db.commit()
    bot = _bot(db_path)

    assert await _recover(bot) == 1


async def test_a_round_whose_timestamp_will_not_parse_is_skipped(tmp_path):
    """One malformed row must not cost every other division its catch-up."""
    db_path = await _make_db(tmp_path, hours_until_round=-1)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET scheduled_at = 'not a date' WHERE id = ?", (ROUND_ID,)
        )
        await db.commit()
    bot = _bot(db_path)

    assert await _recover(bot) == 0


async def test_a_server_with_no_standing_calls_recovers_nothing(tmp_path):
    """The ordinary case on most restarts."""
    db_path = await _make_db(tmp_path, with_embed=False)
    bot = _bot(db_path)
    bot.attendance_service.get_all_embed_messages = AsyncMock(return_value=[])

    assert await _recover(bot) == 0
    bot.add_view.assert_not_called()


# ---------------------------------------------------------------------------
# The deadline's own mark (#439, slice 6b)
# ---------------------------------------------------------------------------

async def _asked(bot) -> list[int]:
    """Run the recovery, returning the rounds whose deadline it asked of the queue."""
    asked: list[int] = []

    async def _ask(_queue, _db_path, round_id, **_kwargs):
        asked.append(round_id)

    with patch("leaguebot.attendance.services.check_in_changes.ask_check_in_deadline",
               new=AsyncMock(side_effect=_ask)):
        await _recover_rsvp_views_and_deadlines(bot)
    return asked


@pytest.mark.parametrize(("marked", "asked"), [(True, []), (False, [ROUND_ID])],
                         ids=["marked-run", "not-marked"])
async def test_a_restart_reads_the_deadline_s_own_mark(tmp_path, marked, asked):
    """The deadline passed while the bot was down, and no announcement is recorded on the
    call: one whose announcement was discarded, or one that never ran. Only the mark tells
    them apart, and only the deadline that never ran is asked for again."""
    db_path = await _make_db(tmp_path, hours_until_round=-1)
    if marked:
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET deadline_run_at = ? WHERE round_id = ?",
                (datetime.now(timezone.utc).isoformat(), ROUND_ID),
            )
            await db.commit()

    assert await _asked(_bot(db_path)) == asked
