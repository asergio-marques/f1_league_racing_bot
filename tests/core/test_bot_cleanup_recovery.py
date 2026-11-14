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

**Each module's gate applies.** Weather's is the start-up sweep's, which passes over its step
where it is switched off (#287); attendance's is the check-in clean-up change's own, which this
asks of the queue for every due round.

The two cleanups themselves are tested elsewhere; here they are stubbed, and what is pinned is
which rounds reach them. Both are asked of the change queue (#439): the forecast's through
weather's `ask_forecast_clean_up`, patched in `forecast_changes`, and the check-in's through
attendance's `ask_check_in_clean_up` (slice 6b), read off the change queue's asks; attendance's
is run as its "missed check-in clean-ups" start-up step, the queue's clock its moment. Weather's
is run likewise, as its "missed forecast clean-ups" start-up step, and a round whose ask fails
stops none of the others: the failures are raised once at the end, as weather's own
`RecoveryFailed`, which the sweep reports in the log channel (#287).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.start_up_sweep import round_failures, run_weather_start_up_step

SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
#: The kind the check-in clean-up is asked of the queue under.
CHECK_IN_CLEAN_UP = "attendance.check_in.clean_up"
#: The name weather's start-up step runs under.
MISSED_FORECAST_CLEAN_UPS = "missed forecast clean-ups"


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


def _bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=True)
    return bot


def _check_ins_asked(bot) -> list[int]:
    """The rounds whose check-in clean-up the bot's change queue was asked for, in order."""
    return [call.args[1]["round_id"] for call in bot.change_queue.ask.call_args_list
            if call.args[0] == CHECK_IN_CLEAN_UP]


async def _recover(
    bot, *, forecast_error: Exception | None = None, weather_on: bool = True
) -> tuple[list[int], list[int]]:
    """Run the two recoveries in the order the start-up runs them, weather's "missed forecast
    clean-ups" and then attendance's "missed check-in clean-ups" start-up step, each as its
    module's `start_up_steps(bot)` gives it, the queue's clock pinned at the host's moment the
    rounds are timed from. Returns the rounds whose forecast clean-up and whose check-in clean-up
    they asked the queue for; *forecast_error* makes the forecast clean-up's ask raise, and the
    error weather's step raised is raised again once attendance's step has run, as the sweep goes
    on to the next step. With *weather_on* False, weather's step is run through the start-up
    sweep, its switch off."""
    from leaguebot.attendance.wiring import start_up_steps
    from leaguebot.weather.wiring import start_up_steps as weather_start_up_steps

    forecasts: list[int] = []

    async def _forecast(_queue, _db_path, round_id):
        if forecast_error is not None:
            raise forecast_error
        forecasts.append(round_id)

    bot.change_queue.ask = AsyncMock(return_value=1)
    bot.change_queue.now = MagicMock(return_value=datetime.now(timezone.utc))
    raised: Exception | None = None
    with patch(
        "leaguebot.weather.services.forecast_changes.ask_forecast_clean_up",
        new=AsyncMock(side_effect=_forecast),
    ):
        if weather_on:
            try:
                await dict(weather_start_up_steps(bot))[MISSED_FORECAST_CLEAN_UPS]()
            except Exception as error:
                raised = error
        else:
            await run_weather_start_up_step(bot, MISSED_FORECAST_CLEAN_UPS, on=False)
        await dict(start_up_steps(bot))["missed check-in clean-ups"]()
    if raised is not None:
        raise raised
    return forecasts, _check_ins_asked(bot)


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

    assert _check_ins_asked(bot) == [ROUND_ID]


async def test_the_forecast_clean_up_catch_up_reads_the_queue_s_clock(tmp_path):
    """Round 21 started two hours ago by the host's clock, its Phase 3 forecast standing, while
    the queue's clock, the bot's, stands a day and three hours after its start. Weather's "missed
    forecast clean-ups" start-up step judges the round a day past at the queue's clock, and asks
    its forecast clean-up."""
    from leaguebot.weather.wiring import start_up_steps

    bot = _bot(await _make_db(tmp_path, hours_since_start=2, check_in=False))
    bot.change_queue.now = MagicMock(
        return_value=datetime.now(timezone.utc) + timedelta(hours=25)
    )
    asked = AsyncMock(return_value=None)

    with patch("leaguebot.weather.services.forecast_changes.ask_forecast_clean_up", new=asked):
        await dict(start_up_steps(bot))[MISSED_FORECAST_CLEAN_UPS]()

    assert [call.args[2] for call in asked.await_args_list] == [ROUND_ID]


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
    """Disabling weather cancels its cleanup jobs; the catch-up must not run them anyway. Run
    through the start-up sweep with weather switched off, weather's step does not run, and no
    forecast clean-up is asked. The check-in's gate is attendance's own, in its clean-up change's
    check, and its clean-up is still asked."""
    db_path = await _make_db(tmp_path)

    assert await _recover(_bot(db_path), weather_on=False) == ([], [ROUND_ID])


async def test_a_failing_cleanup_does_not_stop_the_rest(tmp_path):
    """Round 21 is a day past, its forecast and its call standing, and asking its forecast
    clean-up fails. Weather's step raises its own `RecoveryFailed` naming round 21 with that
    error, and attendance's step, run after it, still asks the round's check-in clean-up."""
    from leaguebot.weather.models.recovery_failed import RecoveryFailed

    bot = _bot(await _make_db(tmp_path))
    gone = RuntimeError("gone")

    with pytest.raises(RecoveryFailed) as raised:
        await _recover(bot, forecast_error=gone)

    assert raised.value.failures == {ROUND_ID: gone}
    assert _check_ins_asked(bot) == [ROUND_ID]


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


async def test_a_database_the_forecast_clean_up_catch_up_cannot_read_is_reported_in_the_log_channel(
    tmp_path,
):
    """The database holding the standing forecasts cannot be opened. Run through the start-up
    sweep, weather's "missed forecast clean-ups" step asks no clean-up, and the sweep posts one
    line to the log channel, its failure line for that step, rather than the fault going to the
    host's log alone."""
    from leaguebot.core.utils.interaction_errors import start_up_failure_line

    bot = _swept_bot(os.path.join(str(tmp_path), "missing", "nowhere.db"))
    asked = AsyncMock(return_value=None)

    with patch("leaguebot.weather.services.forecast_changes.ask_forecast_clean_up", new=asked):
        lines, error = await run_weather_start_up_step(bot, MISSED_FORECAST_CLEAN_UPS)

    assert error is not None
    assert lines == [start_up_failure_line(MISSED_FORECAST_CLEAN_UPS, error)]
    asked.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason="#287: weather's RecoveryFailed names a round by its database id")
async def test_one_round_failing_its_recovery_stops_none_of_the_others(tmp_path):
    """Division 1 has two Phase 3 forecasts standing: round 21's, raced 25 hours ago, and round
    22's (its round 2), raced 30 hours ago. At the restart the queue refuses round 22's forecast
    clean-up. Run through the start-up sweep, weather's "missed forecast clean-ups" step still asks
    round 21's, then raises weather's `RecoveryFailed` naming round 22 alone with the queue's
    error, its message, which the host's log carries, naming it as a league reads it, "round 2 in
    **Division 1**", never by its id 22 (#287, B10); and the sweep posts one line to the log
    channel, its failure line for that step."""
    from leaguebot.core.utils.interaction_errors import start_up_failure_line

    db_path = await _make_db(tmp_path, check_in=False)
    earlier = datetime.now(timezone.utc) - timedelta(hours=30)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (22, ?, 2, 'NORMAL', 'Monza Circuit', ?, 'FINAL')",
            (DIVISION_ID, earlier.isoformat()),
        )
        await db.execute(
            "INSERT INTO forecast_messages "
            "(round_id, division_id, phase_number, message_id, posted_at) "
            "VALUES (22, ?, 3, 900, ?)",
            (DIVISION_ID, earlier.isoformat()),
        )
        await db.commit()
    bot = _swept_bot(db_path)
    refused = RuntimeError("the database is locked")
    asked: list[int] = []

    async def ask(_queue, _db_path, round_id):
        asked.append(round_id)
        if round_id == 22:
            raise refused

    with patch("leaguebot.weather.services.forecast_changes.ask_forecast_clean_up",
               new=AsyncMock(side_effect=ask)):
        lines, error = await run_weather_start_up_step(bot, MISSED_FORECAST_CLEAN_UPS)

    assert asked == [22, ROUND_ID]
    assert type(error).__module__ == "leaguebot.weather.models.recovery_failed", type(error)
    assert round_failures(error, module="weather") == {22: refused}
    assert "round 2 in **Division 1**" in str(error) and "22" not in str(error), str(error)
    assert lines == [start_up_failure_line(MISSED_FORECAST_CLEAN_UPS, error)]


async def test_a_round_whose_start_cannot_be_read_fails_its_recovery_alone(tmp_path):
    """Division 1 has two Phase 3 forecasts standing: round 21's, raced 25 hours ago, and round
    22's, its start stored without its offset, which no writer produces and the bot refuses to
    read. Run through the start-up sweep, weather's "missed forecast clean-ups" step still asks
    round 21's clean-up, then raises weather's `RecoveryFailed` naming round 22 alone, with the
    `ValueError` its start raised, rather than passing it over in silence; and the sweep posts one
    line to the log channel, its failure line for that step."""
    from leaguebot.core.utils.interaction_errors import start_up_failure_line

    db_path = await _make_db(tmp_path, check_in=False)
    unreadable = (datetime.now(timezone.utc) - timedelta(hours=30)).replace(tzinfo=None)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at, status) VALUES (22, ?, 2, 'NORMAL', 'Monza Circuit', ?, 'FINAL')",
            (DIVISION_ID, unreadable.isoformat()),
        )
        await db.execute(
            "INSERT INTO forecast_messages "
            "(round_id, division_id, phase_number, message_id, posted_at) "
            "VALUES (22, ?, 3, 900, ?)",
            (DIVISION_ID, datetime.now(timezone.utc).isoformat()),
        )
        await db.commit()
    bot = _swept_bot(db_path)
    asked = AsyncMock(return_value=None)

    with patch("leaguebot.weather.services.forecast_changes.ask_forecast_clean_up", new=asked):
        lines, error = await run_weather_start_up_step(bot, MISSED_FORECAST_CLEAN_UPS)

    assert [call.args[2] for call in asked.await_args_list] == [ROUND_ID]
    assert type(error).__module__ == "leaguebot.weather.models.recovery_failed", type(error)
    failures = round_failures(error, module="weather")
    assert list(failures) == [22]
    assert isinstance(failures[22], ValueError), failures
    assert lines == [start_up_failure_line(MISSED_FORECAST_CLEAN_UPS, error)]
