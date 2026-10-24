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
attendance's `ask_check_in_clean_up` (slice 6b), patched in `check_in_changes`; these record
each ask.
"""
from __future__ import annotations

import inspect
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import leaguebot.__main__ as bot_module
from leaguebot.core.db.database import get_connection, run_migrations

SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21


async def _make_db(
    tmp_path,
    *,
    hours_since_start: float = 25,
    round_status: str = "FINAL",
    division_status: str = "ACTIVE",
    season_status: str = "ACTIVE",
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
    """Run the recovery, returning the rounds whose forecast clean-up and whose check-in
    clean-up it asked the queue for; *forecast_error* makes the forecast clean-up's ask raise."""
    forecasts: list[int] = []
    check_ins: list[int] = []

    async def _forecast(_queue, _db_path, round_id):
        if forecast_error is not None:
            raise forecast_error
        forecasts.append(round_id)

    async def _check_in(_queue, _db_path, round_id, **_kwargs):
        check_ins.append(round_id)

    with patch(
        "leaguebot.weather.services.forecast_changes.ask_forecast_clean_up",
        new=AsyncMock(side_effect=_forecast),
    ), patch(
        "leaguebot.attendance.services.check_in_changes.ask_check_in_clean_up",
        new=AsyncMock(side_effect=_check_in),
    ):
        await bot_module._recover_missed_cleanups(bot)
    return forecasts, check_ins


# ---------------------------------------------------------------------------
# What is taken down
# ---------------------------------------------------------------------------


async def test_a_round_a_day_past_has_both_its_cleanups_run(tmp_path):
    db_path = await _make_db(tmp_path, hours_since_start=25)

    assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])


async def test_a_round_exactly_a_day_past_is_due(tmp_path):
    """The live jobs fire at the round's start plus a day, so that moment is already due."""
    db_path = await _make_db(tmp_path, hours_since_start=24)

    assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])


async def test_a_round_under_a_day_past_is_left_to_its_own_job(tmp_path):
    db_path = await _make_db(tmp_path, hours_since_start=23)

    assert await _recover(_bot(db_path)) == ([], [])


async def test_a_completed_season_is_still_cleaned_up(tmp_path):
    """The live jobs outlive the season's completion, and the catch-up does as they would."""
    db_path = await _make_db(tmp_path, season_status="COMPLETED")

    assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])


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
    W4)."""
    db_path = await _make_db(tmp_path, phases=(2, 3))

    with caplog.at_level("INFO", logger=bot_module.__name__):
        assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])

    lines = [record.getMessage() for record in caplog.records]
    assert (f"_recover_missed_cleanups: deleting the Phase 2 and 3 forecasts of "
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
    standing, "deleting the Phase 2 forecast of round_id=21", never a phase that is not."""
    db_path = await _make_db(tmp_path, phases=phases)

    with caplog.at_level("INFO", logger=bot_module.__name__):
        await _recover(_bot(db_path))

    lines = [record.getMessage() for record in caplog.records]
    assert f"_recover_missed_cleanups: deleting {named} of round_id={ROUND_ID}" in lines, lines


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


@pytest.mark.xfail(strict=True, reason=(
    "#439: rounds has no not_held column, and the catch-up passes over every cancelled round, so "
    "a round whose every session was entered as not held keeps its call and forecast"
))
async def test_a_round_whose_every_session_was_not_held_has_its_cleanups_caught_up(tmp_path):
    """Round 21 started 25 hours ago, its Phase 3 forecast and its call standing, and ended
    cancelled because every session of it was entered as not held. The bot starts again: the
    catch-up asks both its forecast clean-up and its check-in clean-up, as for any round a day
    past (attendance :75; weather :143)."""
    db_path = await _make_db(tmp_path, round_status="CANCELLED", not_held=1)

    assert await _recover(_bot(db_path)) == ([ROUND_ID], [ROUND_ID])


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


async def test_a_database_it_cannot_read_stops_the_recovery_quietly(tmp_path, caplog):
    bot = _bot(os.path.join(str(tmp_path), "missing", "nowhere.db"))

    assert await _recover(bot) == ([], [])
    assert "failed to read the messages still standing" in caplog.text


# ---------------------------------------------------------------------------
# Where it runs
# ---------------------------------------------------------------------------


def test_the_catch_up_runs_after_the_deadlines_are_caught_up():
    """So a check-in whose deadline and cleanup both passed while the bot was down is
    distributed before it is taken down. Read from the source, because both run inside
    `on_ready` among a dozen start-up steps no test drives whole."""
    source = inspect.getsource(bot_module.main)
    deadlines = source.index("await _recover_rsvp_views_and_deadlines(bot)")
    cleanups = source.index("await _recover_missed_cleanups(bot)")
    assert deadlines < cleanups
