"""A round's cleanups that fell due while the bot was stopped are run when it starts again.

Issue #425. A round's Phase 3 forecast, and its check-in call with its last notice and its
distribution message, come down 24 hours after the round's start, by the ``cleanup`` and
``rsvp_cleanup`` jobs. A job that falls due while the bot is down is discarded by the
scheduler's five-minute misfire grace rather than run late, so without this a restart at the
wrong moment left those messages standing for good. The league decided both modules catch up
(2026-09-24).

**Only what is at least a day past is taken down.** A round that started under a day ago still
has its job armed, which will run at its own moment.

**A cancelled round or division is left alone**, as its cancellation removed both jobs and the
weather module keeps the forecasts of a round called off. **A completed season is not**: the
live jobs fire after a season completes, and the catch-up does what they would have.

**Each module's gate applies.** Weather's is asked here; attendance's is inside
`run_rsvp_cleanup`, which this hands every due round to.

The two cleanups themselves are tested elsewhere; here they are stubbed, and what is pinned is
which rounds reach them.
"""
from __future__ import annotations

import inspect
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import leaguebot.__main__ as bot_module  # noqa: E402
from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402

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
) -> str:
    """A round *hours_since_start* past its start, with a Phase 3 forecast and a call standing.

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
            "scheduled_at, status) VALUES (?, ?, 1, 'NORMAL', 'Silverstone Circuit', ?, ?)",
            (ROUND_ID, DIVISION_ID, started.isoformat(), round_status),
        )
        if forecast:
            await db.execute(
                "INSERT INTO forecast_messages "
                "(round_id, division_id, phase_number, message_id, posted_at) "
                "VALUES (?, ?, 3, 800, ?)",
                (ROUND_ID, DIVISION_ID, started.isoformat()),
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
    """Run the recovery, returning the rounds whose forecast and whose check-in it cleaned."""
    forecasts: list[int] = []
    check_ins: list[int] = []

    async def _forecast(round_id, _bot):
        if forecast_error is not None:
            raise forecast_error
        forecasts.append(round_id)

    async def _check_in(round_id, _bot):
        check_ins.append(round_id)

    with patch(
        "leaguebot.weather.services.forecast_cleanup_service.run_post_race_cleanup",
        new=AsyncMock(side_effect=_forecast),
    ), patch("leaguebot.attendance.services.rsvp_service.run_rsvp_cleanup", new=AsyncMock(side_effect=_check_in)):
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


# ---------------------------------------------------------------------------
# What is left
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [{"round_status": "CANCELLED"}, {"division_status": "CANCELLED"}],
    ids=["cancelled-round", "cancelled-division"],
)
async def test_a_cancelled_round_is_left_alone(tmp_path, kwargs):
    """Its cancellation removed both jobs, and a cancelled round keeps its forecasts."""
    db_path = await _make_db(tmp_path, **kwargs)

    assert await _recover(_bot(db_path)) == ([], [])


async def test_weather_switched_off_keeps_its_forecasts(tmp_path):
    """Disabling weather cancels its cleanup jobs; the catch-up must not run them anyway. The
    check-in's gate is attendance's own, inside `run_rsvp_cleanup`."""
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
