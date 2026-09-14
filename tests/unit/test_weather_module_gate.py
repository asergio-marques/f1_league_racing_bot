"""The weather module produces nothing while it is switched off — issue #113.

`core_specification.md` — "A disabled module shall produce nothing. While a module is disabled
the bot shall neither compute, record nor post any of that module's output, whatever the path
arrives at it — a scheduled job, a restart, or a command that amends work arranged while the
module was still enabled", and "Nothing done while a module was disabled shall be recorded as
that module's work, so that enabling the module later does not find its work already done."

`/round amend` broke both halves: it re-ran any forecast whose horizon had passed without asking
whether weather was on, and marked the phase done, so the later enable skipped it for good.

These tests hold the gate at both ends — inside the three phase runners, where it holds for every
route in, and at the `amend_round` call site — and pin the enabled path alongside, so the gate
cannot be over-applied and quietly switch the feature off for everyone.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services import phase1_service, phase2_service, phase3_service  # noqa: E402
from services.amendment_service import AmendmentService  # noqa: E402

SEEDED_TRACK = "Bahrain International Circuit"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

async def _make_db(tmp_path: str) -> str:
    db_path = os.path.join(tmp_path, "test.db")
    await run_migrations(db_path)
    return db_path


async def _seed(
    db_path: str,
    *,
    server_id: int = 1,
    scheduled_at: datetime | None = None,
    phase1_done: int = 0,
) -> tuple[int, int]:
    """Insert server_config, season, division and one round.

    *scheduled_at* defaults to a time far enough in the past that all three phase horizons
    (T−5 days, T−2 days, T−2 hours) have passed. It is computed from the real clock rather
    than pinned to a date, so the test cannot rot into passing.

    Returns ``(division_id, round_id)``.
    """
    if scheduled_at is None:
        scheduled_at = datetime.now(timezone.utc) - timedelta(days=1)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
            " weather_module_enabled) VALUES (?, 100, 200, 300, 0)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (1, ?, 1, '2026-01-01', 'ACTIVE')",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, forecast_channel_id, mention_role_id) "
            "VALUES (1, 1, 'Div A', 1, 999, 555)"
        )
        await db.execute(
            "INSERT INTO rounds "
            "(id, division_id, round_number, format, track_name, scheduled_at, phase1_done) "
            "VALUES (1, 1, 1, 'NORMAL', ?, ?, ?)",
            (SEEDED_TRACK, scheduled_at.isoformat(), phase1_done),
        )
        await db.commit()
    return 1, 1


async def _set_weather(db_path: str, enabled: bool, server_id: int = 1) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE server_configs SET weather_module_enabled = ? WHERE server_id = ?",
            (int(enabled), server_id),
        )
        await db.commit()


def _make_bot(db_path: str, *, weather_enabled: bool) -> MagicMock:
    """A bot double whose module_service answers from the flag under test."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    # These tests are about the weather gate, so attendance is off throughout and the
    # amendment's check-in re-arm has nothing to do.
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=False)
    bot.output_router.post_forecast = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock(return_value=None)
    return bot


async def _phase_state(db_path: str, round_id: int = 1) -> tuple[int, int, int, int]:
    """Return ``(phase1_done, phase2_done, phase3_done, phase_results_rows)``."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT phase1_done, phase2_done, phase3_done FROM rounds WHERE id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
        counter = await db.execute(
            "SELECT COUNT(*) AS n FROM phase_results WHERE round_id = ?", (round_id,)
        )
        count = await counter.fetchone()
    return row["phase1_done"], row["phase2_done"], row["phase3_done"], count["n"]


# ---------------------------------------------------------------------------
# The gate inside the phase runners
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "runner",
    [phase1_service.run_phase1, phase2_service.run_phase2, phase3_service.run_phase3],
    ids=["phase1", "phase2", "phase3"],
)
async def test_phase_runner_produces_nothing_while_weather_is_disabled(runner, tmp_path):
    """Nothing computed, nothing recorded, nothing posted — for every runner."""
    db_path = await _make_db(str(tmp_path))
    await _seed(db_path)
    bot = _make_bot(db_path, weather_enabled=False)

    with patch(
        "services.forecast_cleanup_service.post_phase_message", new=AsyncMock()
    ) as posted:
        await runner(1, bot)

    assert await _phase_state(db_path) == (0, 0, 0, 0)
    posted.assert_not_awaited()
    bot.output_router.post_log.assert_not_awaited()
    bot.output_router.post_forecast.assert_not_awaited()


async def test_phase_runner_runs_when_weather_is_enabled(tmp_path):
    """The gate must not switch the feature off for a league that has it on."""
    db_path = await _make_db(str(tmp_path))
    await _seed(db_path)
    await _set_weather(db_path, True)
    bot = _make_bot(db_path, weather_enabled=True)

    with patch(
        "services.forecast_cleanup_service.post_phase_message", new=AsyncMock()
    ) as posted, patch(
        "services.image_weather_post.attach_forecast", new=AsyncMock(return_value=None)
    ):
        await phase1_service.run_phase1(1, bot)

    phase1_done, _, _, results = await _phase_state(db_path)
    assert phase1_done == 1
    assert results == 1
    posted.assert_awaited_once()


# ---------------------------------------------------------------------------
# The gate at the amend_round call site — issue #113 as reported
# ---------------------------------------------------------------------------

def _make_actor() -> MagicMock:
    actor = MagicMock()
    actor.id = 4242
    actor.display_name = "Race Control"
    return actor


def _amend_bot(db_path: str, *, weather_enabled: bool) -> MagicMock:
    bot = _make_bot(db_path, weather_enabled=weather_enabled)
    bot.scheduler_service.cancel_round = MagicMock()
    bot.scheduler_service.schedule_round = MagicMock()
    return bot


async def test_amend_round_runs_no_overdue_phase_while_weather_is_disabled(tmp_path):
    """The reported defect: amending a round drew and posted a forecast with weather off."""
    db_path = await _make_db(str(tmp_path))
    await _seed(db_path)
    bot = _amend_bot(db_path, weather_enabled=False)

    with patch(
        "services.phase1_service.run_phase1", new=AsyncMock()
    ) as p1, patch(
        "services.phase2_service.run_phase2", new=AsyncMock()
    ) as p2, patch(
        "services.phase3_service.run_phase3", new=AsyncMock()
    ) as p3:
        await AmendmentService(db_path).amend_round(
            1, _make_actor(), [("track_name", "Silverstone Circuit")], bot
        )

    p1.assert_not_awaited()
    p2.assert_not_awaited()
    p3.assert_not_awaited()
    bot.scheduler_service.schedule_round.assert_not_called()


async def test_amend_round_leaves_the_phases_for_a_later_enable(tmp_path):
    """The knock-on: a phase marked done while the module was off could never be redone.

    Run for real rather than with the runners patched out, so the whole path is exercised —
    the flags must be left at 0 for the enable catch-up to pick up.
    """
    db_path = await _make_db(str(tmp_path))
    await _seed(db_path)
    bot = _amend_bot(db_path, weather_enabled=False)

    await AmendmentService(db_path).amend_round(
        1, _make_actor(), [("track_name", "Silverstone Circuit")], bot
    )

    assert await _phase_state(db_path) == (0, 0, 0, 0)


async def test_amend_round_runs_overdue_phases_while_weather_is_enabled(tmp_path):
    """The gate must not cost a league that has weather on its re-run."""
    db_path = await _make_db(str(tmp_path))
    await _seed(db_path)
    await _set_weather(db_path, True)
    bot = _amend_bot(db_path, weather_enabled=True)

    with patch(
        "services.phase1_service.run_phase1", new=AsyncMock()
    ) as p1, patch(
        "services.phase2_service.run_phase2", new=AsyncMock()
    ) as p2, patch(
        "services.phase3_service.run_phase3", new=AsyncMock()
    ) as p3:
        await AmendmentService(db_path).amend_round(
            1, _make_actor(), [("track_name", "Silverstone Circuit")], bot
        )

    p1.assert_awaited_once()
    p2.assert_awaited_once()
    p3.assert_awaited_once()


#: A round close enough that its first forecast is out, and the delay that withdraws it.
#
# The notice follows what the amendment actually took away, so these two tests need a round
# where something *is* taken away: Phase 1 falls five days before the round, so at three days
# out it has been performed, and moving the round a month out puts it back in the future — it
# would not have run under the new moment, so the forecast drawn for it is withdrawn.
_NOTICE_ROUND_AT = timedelta(days=3)
_NOTICE_DELAYED_TO = timedelta(days=30)


async def test_amend_round_posts_no_invalidation_notice_while_weather_is_disabled(tmp_path):
    """The same rule by a second route: weather on, phases run, weather off, round amended."""
    db_path = await _make_db(str(tmp_path))
    now = datetime.now(timezone.utc)
    await _seed(db_path, scheduled_at=now + _NOTICE_ROUND_AT, phase1_done=1)
    bot = _amend_bot(db_path, weather_enabled=False)

    await AmendmentService(db_path).amend_round(
        1, _make_actor(), [("scheduled_at", now + _NOTICE_DELAYED_TO)], bot, now=now
    )

    bot.output_router.post_forecast.assert_not_awaited()
    # The amendment's own audit line is not weather output and is posted either way.
    bot.output_router.post_log.assert_awaited_once()


async def test_amend_round_posts_the_invalidation_notice_while_weather_is_enabled(tmp_path):
    """A league with weather on must still be told which forecasts were thrown away."""
    db_path = await _make_db(str(tmp_path))
    now = datetime.now(timezone.utc)
    await _seed(db_path, scheduled_at=now + _NOTICE_ROUND_AT, phase1_done=1)
    await _set_weather(db_path, True)
    bot = _amend_bot(db_path, weather_enabled=True)

    with patch("services.phase1_service.run_phase1", new=AsyncMock()), patch(
        "services.phase2_service.run_phase2", new=AsyncMock()
    ), patch("services.phase3_service.run_phase3", new=AsyncMock()):
        await AmendmentService(db_path).amend_round(
            1, _make_actor(), [("scheduled_at", now + _NOTICE_DELAYED_TO)], bot, now=now
        )

    bot.output_router.post_forecast.assert_awaited_once()


async def test_amend_round_posts_no_notice_when_every_forecast_still_stands(tmp_path):
    """Nothing withdrawn, nothing announced.

    A round whose phases would all have run under its new moment loses no forecast, so the
    division is told nothing — the one in its channel is still the one that stands. Before the
    phases were judged one at a time, every amendment announced that the forecasts had been
    thrown away whether or not any had.
    """
    db_path = await _make_db(str(tmp_path))
    now = datetime.now(timezone.utc)
    # An hour later on the same day: every horizon stays behind us, so nothing is withdrawn.
    await _seed(db_path, scheduled_at=now + timedelta(hours=1), phase1_done=1)
    await _set_weather(db_path, True)
    bot = _amend_bot(db_path, weather_enabled=True)

    with patch("services.phase1_service.run_phase1", new=AsyncMock()), patch(
        "services.phase2_service.run_phase2", new=AsyncMock()
    ), patch("services.phase3_service.run_phase3", new=AsyncMock()):
        await AmendmentService(db_path).amend_round(
            1, _make_actor(), [("scheduled_at", now + timedelta(hours=2))], bot, now=now
        )

    bot.output_router.post_forecast.assert_not_awaited()
    # And the forecast it kept is still marked as performed.
    assert (await _phase_state(db_path))[0] == 1
