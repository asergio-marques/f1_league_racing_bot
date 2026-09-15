"""A restart recovers phases at the league's own horizons, not the packaged ones — issue #111.

`weather_module_specification.md` — "The horizons a recovery judges by shall be the league's own
configured ones, not the packaged ones."

Every other path that decides whether a phase is overdue reads `weather_pipeline_config`:
`/season approve`, the catch-up `/module enable weather` runs, and `amend_round` since #110.
`_recover_missed_phases` did not — it worked from `timedelta(days=5)`, `timedelta(days=2)` and
`timedelta(hours=2)` literals, so a restart judged every league by the packaged 5 / 2 / 2. A league
running a longer phase 1 lost the forecast entirely until the bot happened to restart inside the
last five days; one running a shorter phase 1 got it days early. The same league therefore saw one
set of timings when the module was enabled and another when the bot restarted.

The function carried no test at all, which is how it survived the amendment path's fix beside it.
These tests hold the recovery to the league's horizons in both directions, pin the packaged
defaults for a league that configured nothing, pin that two servers on one restart are each judged
by their own settings, and keep the module gate beside them so the fix cannot be read as a licence
to recover for a switched-off module.

Every moment here is computed from the real clock rather than pinned to a date, so the tests cannot
rot into passing.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 5511
SEASON_ID = 1
DIVISION_ID = 1
ROUND_ID = 1


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    """A migrated database with nothing in it yet."""
    db_path = os.path.join(str(tmp_path), "weather_recovery.db")
    await run_migrations(db_path)
    return db_path


async def _seed_server(
    db_path: str,
    *,
    server_id: int = SERVER_ID,
    season_id: int = SEASON_ID,
    division_id: int = DIVISION_ID,
    round_id: int = ROUND_ID,
    days_until_round: float,
    horizons: tuple[int, int, int] | None = None,
    round_format: str = "NORMAL",
    season_status: str = "ACTIVE",
) -> None:
    """One server, an active season, a division and a single round *days_until_round* away.

    *horizons* is ``(phase_1_days, phase_2_days, phase_3_hours)``; ``None`` leaves the server with
    no `weather_pipeline_config` row at all, which is what a league that never ran
    `/weather config` looks like.
    """
    scheduled_at = datetime.now(timezone.utc) + timedelta(days=days_until_round)

    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id, "
            " weather_module_enabled) VALUES (?, 100, 200, 300, 1)",
            (server_id,),
        )
        if horizons is not None:
            await db.execute(
                "INSERT INTO weather_pipeline_config "
                "(server_id, phase_1_days, phase_2_days, phase_3_hours) VALUES (?, ?, ?, ?)",
                (server_id, *horizons),
            )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', ?)",
            (season_id, server_id, season_status),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (division_id, season_id),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, scheduled_at) "
            "VALUES (?, ?, 1, ?, 'Silverstone Circuit', ?)",
            (round_id, division_id, round_format, scheduled_at.isoformat()),
        )
        await db.commit()


def _make_bot(db_path: str, *, weather_enabled: bool = True) -> MagicMock:
    """A bot double whose module_service answers from the flag under test."""
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    return bot


class _Phases:
    """The three phase runners, stubbed, with the rounds each was asked to draw for."""

    def __init__(self) -> None:
        self.one = AsyncMock(return_value=None)
        self.two = AsyncMock(return_value=None)
        self.three = AsyncMock(return_value=None)

    def rounds(self, runner: AsyncMock) -> list[int]:
        return [call.args[0] for call in runner.await_args_list]


async def _recover(bot: MagicMock) -> _Phases:
    """Run the restart recovery with the three phase runners stubbed out."""
    from bot import _recover_missed_phases

    phases = _Phases()
    with (
        patch("services.phase1_service.run_phase1", phases.one),
        patch("services.phase2_service.run_phase2", phases.two),
        patch("services.phase3_service.run_phase3", phases.three),
    ):
        await _recover_missed_phases(bot)
    return phases


# ---------------------------------------------------------------------------
# The league's own horizons
# ---------------------------------------------------------------------------


async def test_a_restart_fires_a_phase_overdue_by_the_leagues_longer_deadline(tmp_path):
    """Phase 1 at seven days, the bot back up six days out: the forecast is overdue."""
    db_path = await _make_db(tmp_path)
    await _seed_server(db_path, days_until_round=6, horizons=(7, 2, 2))
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    assert phases.rounds(phases.one) == [ROUND_ID], (
        "a restart judged a seven-day phase 1 by the packaged five days and left the "
        "forecast unpublished"
    )
    phases.two.assert_not_awaited()
    phases.three.assert_not_awaited()


async def test_a_restart_holds_a_phase_not_yet_due_by_the_leagues_shorter_deadline(tmp_path):
    """Phase 1 at three days, the bot back up four days out: the forecast is not due yet."""
    db_path = await _make_db(tmp_path)
    await _seed_server(db_path, days_until_round=4, horizons=(3, 2, 2))
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    phases.one.assert_not_awaited()
    phases.two.assert_not_awaited()
    phases.three.assert_not_awaited()


async def test_a_restart_fires_every_phase_the_leagues_horizons_have_passed(tmp_path):
    """All three configured horizons behind us, so all three phases are recovered."""
    db_path = await _make_db(tmp_path)
    await _seed_server(db_path, days_until_round=0.25, horizons=(7, 3, 12))
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    assert phases.rounds(phases.one) == [ROUND_ID]
    assert phases.rounds(phases.two) == [ROUND_ID]
    assert phases.rounds(phases.three) == [ROUND_ID]


async def test_a_restart_still_uses_the_packaged_horizons_where_the_league_set_none(tmp_path):
    """A league that never ran `/weather config` keeps the packaged 5 / 2 / 2."""
    db_path = await _make_db(tmp_path)
    await _seed_server(db_path, days_until_round=4, horizons=None)
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    assert phases.rounds(phases.one) == [ROUND_ID], "the packaged five-day phase 1 was not recovered"
    phases.two.assert_not_awaited()
    phases.three.assert_not_awaited()


async def test_a_restart_uses_each_servers_own_horizons(tmp_path):
    """Two leagues recovered in one restart are each judged by their own settings."""
    db_path = await _make_db(tmp_path)
    # Six days out: overdue under the first league's seven-day phase 1, not under the second's five.
    await _seed_server(db_path, days_until_round=6, horizons=(7, 2, 2))
    await _seed_server(
        db_path,
        server_id=SERVER_ID + 1,
        season_id=SEASON_ID + 1,
        division_id=DIVISION_ID + 1,
        round_id=ROUND_ID + 1,
        days_until_round=6,
        horizons=(5, 2, 2),
    )
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    assert phases.rounds(phases.one) == [ROUND_ID], (
        "one config was read for every server, so the two leagues were judged alike"
    )


# ---------------------------------------------------------------------------
# The gates the recovery already carried
# ---------------------------------------------------------------------------


async def test_a_restart_recovers_nothing_for_a_server_with_weather_disabled(tmp_path):
    """The module gate stands: a switched-off module produces nothing, however overdue."""
    db_path = await _make_db(tmp_path)
    await _seed_server(db_path, days_until_round=0.25, horizons=(7, 3, 12))
    bot = _make_bot(db_path, weather_enabled=False)

    phases = await _recover(bot)

    phases.one.assert_not_awaited()
    phases.two.assert_not_awaited()
    phases.three.assert_not_awaited()


async def test_a_restart_recovers_no_phase_for_a_mystery_round(tmp_path):
    """A mystery round's forecast is never drawn, so nothing is recovered for it."""
    db_path = await _make_db(tmp_path)
    await _seed_server(
        db_path, days_until_round=0.25, horizons=(7, 3, 12), round_format="MYSTERY"
    )
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    phases.one.assert_not_awaited()
    phases.two.assert_not_awaited()
    phases.three.assert_not_awaited()


async def test_a_restart_recovers_no_phase_for_a_season_that_is_not_active(tmp_path):
    """Recovery reaches the rounds of active seasons only."""
    db_path = await _make_db(tmp_path)
    await _seed_server(
        db_path, days_until_round=0.25, horizons=(7, 3, 12), season_status="SETUP"
    )
    bot = _make_bot(db_path)

    phases = await _recover(bot)

    phases.one.assert_not_awaited()
    phases.two.assert_not_awaited()
    phases.three.assert_not_awaited()
