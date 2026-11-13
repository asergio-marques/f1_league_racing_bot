"""A round's cleanups that fell due while the bot was stopped are run when it starts again.

Issue #425. A round's forecast (whichever of Phases 1 to 3 still stands), and its check-in call
with its last notice and its distribution message, come down 24 hours after the round's start, by
the ``cleanup`` and ``rsvp_cleanup`` jobs. A job that falls due while the bot is down is discarded
by the scheduler's five-minute misfire grace rather than run late, so without this a restart at
the wrong moment left those messages standing for good. The league decided both modules catch up
(2026-09-24).

**Only what is at least a day past is taken down.** A round that started under a day ago still
has its job armed, which will run at its own moment.

**A round called off, or a round of a cancelled division, is left alone**, as its cancellation
removed both jobs and the weather module keeps the forecasts of a round called off. **A round
whose every session was entered as not held is not**: it ends cancelled, but its clean-ups stay
armed and come down a day after its start like any round raced (#439, slice 8, Part E). **A completed season is not**: the
live jobs fire after a season completes, and the catch-up does what they would have.

**Each module's gate applies.** Weather's is asked here; attendance's is the check-in
clean-up change's own, which this asks of the queue for every due round.

The two cleanups themselves are tested elsewhere; here they are stubbed, and what is pinned is
which rounds reach them. Both are asked of the change queue (#439): the forecast's through
weather's `ask_forecast_clean_up`, patched in `forecast_changes`, and the check-in's through
attendance's `ask_check_in_clean_up` (slice 6b), read off the change queue's asks; attendance's
is run as its "missed check-in clean-ups" start-up step, the queue's clock its moment.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.weather import wiring as weather_wiring

SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
#: The kind the check-in clean-up is asked of the queue under.
CHECK_IN_CLEAN_UP = "attendance.check_in.clean_up"


async def _make_db(
    tmp_path,
    *,
    hours_since_start: float = 25,
    round_status: str = "FINAL",
    division_status: str = "ACTIVE",
    season_status: str = "ACTIVE",
    season_stage: str | None = None,
    forecast: bool = True,
    check_in: bool = True,
    phases: tuple[int, ...] = (3,),
    round_format: str = "NORMAL",
    not_held: int | None = None,
) -> str:
    """A round *hours_since_start* past its start, with a forecast of each of *phases* (Phase 3
    alone unless given; 0 a mystery round's notice) and a call standing. *not_held*, where given,
    is written to the round's `not_held` flag, 1 marking a round whose every session was entered
    as not held.

    Placed against the clock rather than pinned, because the recovery reads the clock itself.
    """
    db_path = os.path.join(str(tmp_path), "cleanup_recovery.db")
    await run_migrations(db_path)
    started = datetime.now(timezone.utc) - timedelta(hours=hours_since_start)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, season_status),
        )
        if season_stage is not None:
            await db.execute("UPDATE seasons SET stage = ? WHERE id = ?",
                             (season_stage, SEASON_ID))
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, 'Division 1', 1, 555, ?)",
            (DIVISION_ID, SEASON_ID, division_status),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (?, ?, 1, ?, 'Silverstone Circuit', ?, ?)",
            (ROUND_ID, DIVISION_ID, round_format, started.isoformat(), round_status),
        )
        if not_held is not None:
            await db.execute("UPDATE rounds SET not_held = ? WHERE id = ?", (not_held, ROUND_ID))
        for phase in phases if forecast else ():
            await db.execute(
                "INSERT INTO forecast_messages "
                "(round_id, division_id, phase_number, message_id, posted_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ROUND_ID, DIVISION_ID, phase, 797 + phase, started.isoformat()),
            )
        if check_in:
            await db.execute(
                "INSERT INTO rsvp_embed_messages "
                "(round_id, division_id, message_id, channel_id, posted_at) "
                "VALUES (?, ?, '900', '700', ?)",
                (ROUND_ID, DIVISION_ID, started.isoformat()),
            )
        await db.commit()
    return db_path


def _bot(db_path: str, *, weather_enabled: bool = True) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    return bot


async def _recover(
    bot, *, forecast_error: Exception | None = None
) -> tuple[list[int], list[int]]:
    """Run the two recoveries in the order the start-up runs them, weather's missed forecast
    clean-ups and then attendance's "missed check-in clean-ups" start-up step, as
    `start_up_steps(bot)` gives it, the queue's clock pinned at the host's moment the rounds are
    timed from. Returns the rounds whose forecast clean-up and whose check-in clean-up they asked
    the queue for; *forecast_error* makes the forecast clean-up's ask raise."""
    from leaguebot.attendance.wiring import start_up_steps

    forecasts: list[int] = []

    async def _forecast(_queue, _db_path, round_id):
        if forecast_error is not None:
            raise forecast_error
        forecasts.append(round_id)

    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.change_queue.now = MagicMock(return_value=datetime.now(timezone.utc))
    with patch(
        "leaguebot.weather.services.forecast_changes.ask_forecast_clean_up",
        new=AsyncMock(side_effect=_forecast),
    ):
        await weather_wiring.recover_missed_forecast_clean_ups(bot)
        await dict(start_up_steps(bot))["missed check-in clean-ups"]()
    check_ins = [call.args[1]["round_id"] for call in bot.change_queue.ask.call_args_list
                 if call.args[0] == CHECK_IN_CLEAN_UP]
    return forecasts, check_ins


# ---------------------------------------------------------------------------
# What is taken down
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [
    pytest.param({"hours_since_start": 25}, id="a day past"),
    # The live jobs fire at the round's start plus a day, so that moment is already due.
    pytest.param({"hours_since_start": 24}, id="exactly a day past"),
    # The live jobs outlive the season's completion, and the catch-up does as they would.
    pytest.param({"season_status": "COMPLETED"}, id="a completed season"),
    # A season awaiting completion is not in an ongoing stage, but its last round's check-in
    # still comes down a day after the race, as its timer would take it (decided 2026-11-13).
    pytest.param({"season_stage": "PENDING_COMPLETION"}, id="a season pending completion"),
    # Round 21 started 25 hours ago, its Phase 3 forecast and its call standing, and ended
    # cancelled because every session of it was entered as not held: the catch-up asks both its
    # clean-ups, as for any round a day past (#439, slice 8, Part E; attendance :75; weather :143).
    pytest.param({"round_status": "CANCELLED", "not_held": 1}, id="every session not held"),
])
async def test_a_round_a_day_past_has_both_its_cleanups_run(tmp_path, seed):
    """A round at least a day past its start, its forecast and its call standing, has both its
    forecast clean-up and its check-in clean-up asked when the bot starts again."""
    db_path = await _make_db(tmp_path, **seed)

    assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])


async def test_the_clean_ups_missed_are_asked_in_the_order_their_rounds_raced(tmp_path):
    """Round 21 started 25 hours ago, and round 22, added after it, started 49 hours ago, each
    with its Phase 3 forecast and its call standing and its moment stored in UTC with its offset.
    The restart asks round 22's clean-ups before round 21's, for the forecasts and the check-ins
    alike: the rounds are taken in the order they raced, not the order they were added."""
    db_path = await _make_db(tmp_path)
    earlier = (datetime.now(timezone.utc) - timedelta(hours=49)).isoformat()
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (?, ?, 2, 'NORMAL', 'Monza Circuit', ?, 'FINAL')",
            (ROUND_ID + 1, DIVISION_ID, earlier),
        )
        await db.execute(
            "INSERT INTO forecast_messages "
            "(round_id, division_id, phase_number, message_id, posted_at) "
            "VALUES (?, ?, 3, 900, ?)",
            (ROUND_ID + 1, DIVISION_ID, earlier),
        )
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (?, ?, '901', '700', ?)",
            (ROUND_ID + 1, DIVISION_ID, earlier),
        )
        await db.commit()

    assert await _recover(_bot(db_path)) == ([ROUND_ID + 1, ROUND_ID], [ROUND_ID + 1, ROUND_ID])


async def test_a_round_under_a_day_past_is_left_to_its_own_job(tmp_path):
    db_path = await _make_db(tmp_path, hours_since_start=23)

    assert await _recover(_bot(db_path)) == ([], [])


async def test_the_check_in_clean_up_catch_up_reads_the_queue_s_clock(tmp_path):
    """Round 21 started two hours ago by the host's clock, its call standing, while the queue's
    clock, the bot's, stands a day and three hours after its start. Attendance's "missed check-in
    clean-ups" start-up step judges the round a day past at the queue's clock, and asks the queue
    for its check-in clean-up."""
    from leaguebot.attendance.wiring import start_up_steps

    bot = _bot(await _make_db(tmp_path, hours_since_start=2, forecast=False))
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.change_queue.now = MagicMock(
        return_value=datetime.now(timezone.utc) + timedelta(hours=25)
    )

    await dict(start_up_steps(bot))["missed check-in clean-ups"]()

    assert [call.args[1]["round_id"] for call in bot.change_queue.ask.call_args_list
            if call.args[0] == CHECK_IN_CLEAN_UP] == [ROUND_ID]


async def test_only_what_is_standing_is_cleaned(tmp_path):
    db_path = await _make_db(tmp_path, forecast=False)

    assert await _recover(_bot(db_path)) == ([], [ROUND_ID])


@pytest.mark.parametrize(
    "phases",
    [(2,), (1,), (1, 2)],
    ids=["phase-3-never-posted", "phase-2-never-posted", "phases-1-and-2-standing"],
)
async def test_a_round_a_day_past_with_an_earlier_phase_standing_has_its_forecast_cleanup_run(
    tmp_path, phases
):
    """A day after the race the clean-up deletes whichever of Phases 1 to 3 is still recorded
    (owner, 2026-10-12): Phase 2 where Phase 3 never posted, Phase 1 where Phase 2 never did
    either, or both where Phase 1's supersession was never carried out. The catch-up asks it."""
    db_path = await _make_db(tmp_path, phases=phases)

    assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])


async def test_a_superseded_forecast_whose_deletion_was_discarded_is_caught_up_with_the_round(
    tmp_path, caplog
):
    """Phase 3 of round 21 posted, but deleting the Phase 2 forecast it superseded failed and a
    league admin discarded that job, so both forecasts are still recorded. The bot starts a day
    after the race: the catch-up asks the round's forecast clean-up, naming Phase 2 among what it
    deletes, so the discarded deletion is carried out with the round's clean-up (#439, slice 8,
    W4). The host's log line begins with the name of weather's recovery that writes it."""
    db_path = await _make_db(tmp_path, phases=(2, 3))

    with caplog.at_level("INFO", logger="leaguebot"):
        assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])

    lines = [record.getMessage() for record in caplog.records]
    assert (f"recover_missed_forecast_clean_ups: deleting the Phase 2 and 3 forecasts of "
            f"round_id={ROUND_ID}") in lines, lines


@pytest.mark.parametrize(
    "phases, named",
    [((3,), "the Phase 3 forecast"), ((2,), "the Phase 2 forecast"),
     ((1, 2), "the Phase 1 and 2 forecasts")],
    ids=["phase-3-posted", "phase-3-never-posted", "phases-1-and-2-standing"],
)
async def test_the_catch_up_s_log_line_names_the_forecast_it_deletes(
    tmp_path, caplog, phases, named
):
    """The host's log line for a forecast clean-up the catch-up asks names the forecast still
    standing, "deleting the Phase 2 forecast of round_id=21", never a phase that is not, and
    begins with the name of weather's recovery that writes it."""
    db_path = await _make_db(tmp_path, phases=phases)

    with caplog.at_level("INFO", logger="leaguebot"):
        await _recover(_bot(db_path))

    lines = [record.getMessage() for record in caplog.records]
    assert (f"recover_missed_forecast_clean_ups: deleting {named} of round_id={ROUND_ID}"
            in lines), lines


async def test_a_mystery_round_s_notice_asks_no_forecast_cleanup(tmp_path):
    """A mystery round's notice (phase 0) is not a forecast the clean-up deletes: with nothing
    else recorded, the catch-up asks no clean-up of it."""
    db_path = await _make_db(tmp_path, phases=(0,), round_format="MYSTERY")

    assert await _recover(_bot(db_path)) == ([], [ROUND_ID])


# ---------------------------------------------------------------------------
# What is left
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [{"round_status": "CANCELLED"}, {"division_status": "CANCELLED"}],
    ids=["cancelled-round", "cancelled-division"],
)
async def test_a_cancelled_round_is_left_alone(tmp_path, kwargs):
    """A round called off, or one of a division cancelled, is left alone: its cancellation removed
    both jobs, and a round called off keeps its forecasts."""
    db_path = await _make_db(tmp_path, **kwargs)

    assert await _recover(_bot(db_path)) == ([], [])


async def test_weather_switched_off_keeps_its_forecasts(tmp_path):
    """Disabling weather cancels its cleanup jobs; the catch-up must not run them anyway. The
    check-in's gate is attendance's own, in its clean-up change's check."""
    db_path = await _make_db(tmp_path)

    assert await _recover(_bot(db_path, weather_enabled=False)) == ([], [ROUND_ID])


async def test_a_failing_cleanup_does_not_stop_the_rest(tmp_path, caplog):
    db_path = await _make_db(tmp_path)

    forecasts, check_ins = await _recover(_bot(db_path), forecast_error=RuntimeError("gone"))

    assert check_ins == [ROUND_ID]
    assert "forecast cleanup failed" in caplog.text


def _swept_bot(db_path: str) -> MagicMock:
    """The bot as the start-up sweep hands it to attendance's step: its change queue asked by an
    `AsyncMock`, its clock the host's, which the seeded rounds are timed from."""
    bot = _bot(db_path)
    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.change_queue.now = MagicMock(return_value=datetime.now(timezone.utc))
    return bot


async def test_a_database_the_clean_up_catch_up_cannot_read_is_reported_in_the_log_channel(
    tmp_path,
):
    """The database holding the standing check-in calls cannot be opened. Run through the
    start-up sweep, attendance's "missed check-in clean-ups" step asks no clean-up, and the sweep
    posts one line to the log channel, its failure line for that step, rather than the fault
    going to the host's log alone."""
    from leaguebot.core.utils.interaction_errors import start_up_failure_line

    from tests.support.start_up_sweep import run_attendance_start_up_step

    bot = _swept_bot(os.path.join(str(tmp_path), "missing", "nowhere.db"))

    lines, error = await run_attendance_start_up_step(bot, "missed check-in clean-ups")

    assert error is not None
    assert lines == [start_up_failure_line("missed check-in clean-ups", error)]
    bot.change_queue.ask.assert_not_called()


async def test_a_clean_up_that_cannot_be_asked_is_reported_once_and_the_next_round_s_still_is(
    tmp_path,
):
    """Division 1 has two check-in calls standing: round 21's, raced 25 hours ago, and round
    22's, raced 30 hours ago. At the restart the queue refuses round 22's clean-up. Run through
    the start-up sweep, attendance's "missed check-in clean-ups" step still asks round 21's, and
    the sweep posts one line to the log channel, its failure line for that step; the error the
    step raised names round 22 alone, with the queue's error."""
    from leaguebot.core.utils.interaction_errors import start_up_failure_line

    from tests.support.start_up_sweep import round_failures, run_attendance_start_up_step

    db_path = await _make_db(tmp_path, forecast=False)
    earlier = datetime.now(timezone.utc) - timedelta(hours=30)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (22, ?, 2, 'NORMAL', 'Monza Circuit', ?, 'FINAL')",
            (DIVISION_ID, earlier.isoformat()),
        )
        await db.execute(
            "INSERT INTO rsvp_embed_messages "
            "(round_id, division_id, message_id, channel_id, posted_at) "
            "VALUES (22, ?, '901', '700', ?)",
            (DIVISION_ID, earlier.isoformat()),
        )
        await db.commit()
    bot = _swept_bot(db_path)
    refused = RuntimeError("the database is locked")

    async def ask(kind, payload, **_):
        if payload["round_id"] == 22:
            raise refused
        return 1

    bot.change_queue.ask = AsyncMock(side_effect=ask)

    lines, error = await run_attendance_start_up_step(bot, "missed check-in clean-ups")

    asked = [call.args[1]["round_id"] for call in bot.change_queue.ask.call_args_list]
    assert asked == [22, ROUND_ID]
    assert round_failures(error, module="attendance") == {22: refused}
    assert lines == [start_up_failure_line("missed check-in clean-ups", error)]
