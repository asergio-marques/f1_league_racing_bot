"""`_recover_missed_phases` — re-firing weather phases a restart went through.

Issue #208. A phase is a scheduled job, and a job does not survive the process. On restart the
bot therefore asks which horizons have passed without their phase having run, and fires those.

**The horizons are the league's own** (issue #111), read from `weather_pipeline_config` rather
than the packaged 5 / 2 / 2. Every other path that decides whether a phase is overdue reads that
config — the confirmation of placements, the catch-up `/module enable weather`, `amend_round` — and a restart
judging by the defaults made the same league see one set of timings on an enable and another on
a restart: a longer phase 1 was never published at all, a shorter one was published days early.
`test_the_league_s_own_horizons_are_used_not_the_packaged_ones` is the regression test.

**The config is resolved once per server, not once per round.** A season's rounds all share one,
and a league with three divisions of twenty-four rounds would otherwise read it seventy-two
times on every start. `test_the_configuration_is_read_once_per_server` holds it — and holds it
against the obvious "simplification" of moving the read inside the loop.

**And only after the module gate**, so a server with weather switched off is never queried for a
configuration it does not use — the module-output rule, applied to reads rather than writes.

**A phase already done is not fired again.** The `phase*_done` flags are what stop a restart
republishing a forecast the division has already read, and every restart would otherwise post
the whole season's forecasts afresh.

**Mystery rounds are excluded at the query.** They have no phases to recover — their one notice
is posted by a different path — so including them would fire a forecast for a round whose whole
point is that there isn't one.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from bot import _recover_missed_phases  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 12008
SEASON_ID = 1
DIVISION_ID = 11


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    rounds=((1, 3, False, False, False, "NORMAL"),),
    season_status: str = "ACTIVE",
    servers=(SERVER_ID,),
) -> str:
    """Rounds as ``(id, days_until, p1_done, p2_done, p3_done, format)``.

    *days_until* is relative to now, so a negative value puts the round in the past and
    every horizon behind it — the test cannot rot into passing.
    """
    db_path = os.path.join(str(tmp_path), "phase_recovery.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        for index, server_id in enumerate(servers):
            await db.execute(
                "INSERT INTO server_configs (server_id, interaction_role_id, "
                "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
                (server_id,),
            )
            await db.execute(
                "INSERT INTO seasons (id, season_number, start_date, status) "
                "VALUES (?, 1, '2026-01-01', ?)",
                (SEASON_ID + index, season_status),
            )
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, 1, 555)",
                (DIVISION_ID + index, SEASON_ID + index, f"Division {index + 1}"),
            )
        for round_id, days_until, p1, p2, p3, fmt in rounds:
            division = DIVISION_ID + (0 if round_id < 100 else 1)
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, phase1_done, phase2_done, phase3_done) "
                "VALUES (?, ?, ?, ?, 'Silverstone Circuit', ?, ?, ?, ?)",
                (
                    round_id,
                    division,
                    round_id,
                    fmt,
                    (datetime.now(timezone.utc) + timedelta(days=days_until)).isoformat(),
                    int(p1),
                    int(p2),
                    int(p3),
                ),
            )
        await db.commit()
    return db_path


def _bot(db_path: str, *, weather_enabled: bool = True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service = MagicMock()
    bot.module_service.is_weather_enabled = AsyncMock(return_value=weather_enabled)
    return bot


def _config(*, phase_1_days: int = 5, phase_2_days: int = 2, phase_3_hours: int = 2):
    return SimpleNamespace(
        phase_1_days=phase_1_days,
        phase_2_days=phase_2_days,
        phase_3_hours=phase_3_hours,
    )


async def _recover(bot, *, config=None, reads: list | None = None):
    """Run the recovery, recording which phases fired and which configs were read."""
    fired: list[tuple[int, int]] = []

    async def _phase(number):
        async def _run(round_id, _bot):
            fired.append((number, round_id))

        return _run

    async def _get_config(db_path):
        if reads is not None:
            reads.append(db_path)
        return config or _config()

    with patch("services.phase1_service.run_phase1", new=await _phase(1)), patch(
        "services.phase2_service.run_phase2", new=await _phase(2)
    ), patch("services.phase3_service.run_phase3", new=await _phase(3)), patch(
        "services.weather_config_service.get_weather_pipeline_config",
        new=AsyncMock(side_effect=_get_config),
    ):
        await _recover_missed_phases(bot)
    return fired


# ---------------------------------------------------------------------------
# Which phases fire
# ---------------------------------------------------------------------------


async def test_a_round_past_every_horizon_fires_all_three(tmp_path):
    """A restart after a round's phase 3 horizon has to catch up on everything."""
    db_path = await _make_db(tmp_path, rounds=((1, -1, False, False, False, "NORMAL"),))

    fired = await _recover(_bot(db_path))

    assert sorted(p for p, _ in fired) == [1, 2, 3]


async def test_a_round_before_every_horizon_fires_nothing(tmp_path):
    """Its jobs are re-armed by the scheduler; firing now would publish a forecast days
    early."""
    db_path = await _make_db(tmp_path, rounds=((1, 30, False, False, False, "NORMAL"),))

    assert await _recover(_bot(db_path)) == []


async def test_only_the_horizons_that_have_passed_fire(tmp_path):
    """Four days out, with a five-day phase 1 and a two-day phase 2: the first is overdue
    and the second is not."""
    db_path = await _make_db(tmp_path, rounds=((1, 4, False, False, False, "NORMAL"),))

    fired = await _recover(_bot(db_path))

    assert [p for p, _ in fired] == [1]


async def test_a_phase_already_done_is_not_fired_again(tmp_path):
    """Otherwise every restart would republish the whole season's forecasts."""
    db_path = await _make_db(tmp_path, rounds=((1, -1, True, True, False, "NORMAL"),))

    fired = await _recover(_bot(db_path))

    assert [p for p, _ in fired] == [3]


async def test_every_overdue_round_is_recovered(tmp_path):
    """A restart after a weekend away can leave several behind."""
    db_path = await _make_db(
        tmp_path,
        rounds=(
            (1, -2, False, True, True, "NORMAL"),
            (2, -1, False, True, True, "NORMAL"),
        ),
    )

    fired = await _recover(_bot(db_path))

    assert sorted(r for _, r in fired) == [1, 2]


# ---------------------------------------------------------------------------
# Issue #111 — whose horizons
# ---------------------------------------------------------------------------


async def test_the_league_s_own_horizons_are_used_not_the_packaged_ones(tmp_path):
    """The regression. A league with a ten-day phase 1 has a round eight days out already
    past it; judged by the packaged five days it would not be, and the forecast would
    never be published at all."""
    db_path = await _make_db(tmp_path, rounds=((1, 8, False, True, True, "NORMAL"),))

    fired = await _recover(_bot(db_path), config=_config(phase_1_days=10))

    assert [p for p, _ in fired] == [1]


async def test_a_shorter_horizon_holds_a_phase_back(tmp_path):
    """The other direction of the same fault: judged by the packaged five days, a league
    running a two-day phase 1 would have its forecast published three days early."""
    db_path = await _make_db(tmp_path, rounds=((1, 4, False, True, True, "NORMAL"),))

    fired = await _recover(_bot(db_path), config=_config(phase_1_days=2))

    assert fired == []


async def test_the_configuration_is_read_once(tmp_path):
    """The league has one, and a league with seventy-two rounds would otherwise read it
    seventy-two times on every start. Held against the obvious "simplification" of moving
    the read inside the loop."""
    db_path = await _make_db(
        tmp_path,
        rounds=(
            (1, -1, False, True, True, "NORMAL"),
            (2, -1, False, True, True, "NORMAL"),
            (3, -1, False, True, True, "NORMAL"),
        ),
    )
    reads: list[str] = []

    await _recover(_bot(db_path), reads=reads)

    assert reads == [db_path]


# ---------------------------------------------------------------------------
# Where recovery does not reach
# ---------------------------------------------------------------------------


async def test_a_server_with_weather_off_recovers_nothing(tmp_path):
    db_path = await _make_db(tmp_path, rounds=((1, -1, False, False, False, "NORMAL"),))

    assert await _recover(_bot(db_path, weather_enabled=False)) == []


async def test_a_server_with_weather_off_is_never_asked_for_its_configuration(tmp_path):
    """The module gate runs first, so a server that does not use the config is not queried
    for it — the module-output rule applied to reads."""
    db_path = await _make_db(tmp_path, rounds=((1, -1, False, False, False, "NORMAL"),))
    reads: list[str] = []

    await _recover(_bot(db_path, weather_enabled=False), reads=reads)

    assert reads == []


async def test_a_mystery_round_is_excluded_at_the_query(tmp_path):
    """It has no phases to recover — its one notice is posted by a different path — so
    firing one would publish a forecast for a round whose whole point is that there is
    not one."""
    db_path = await _make_db(tmp_path, rounds=((1, -1, False, False, False, "MYSTERY"),))

    assert await _recover(_bot(db_path)) == []


async def test_a_season_not_running_recovers_nothing(tmp_path):
    """A season in setup has not started, and a completed one is over — neither has a
    forecast owing."""
    db_path = await _make_db(
        tmp_path,
        rounds=((1, -1, False, False, False, "NORMAL"),),
        season_status="SETUP",
    )

    assert await _recover(_bot(db_path)) == []


async def test_a_naive_timestamp_is_read_as_utc(tmp_path):
    """Rows written before the timestamps carried a zone still exist. Read as local time
    the horizon would move by the host's offset, so the same database would recover
    differently on the Pi than on a developer's machine."""
    db_path = await _make_db(tmp_path)
    naive = (datetime.now(timezone.utc) - timedelta(days=1)).replace(tzinfo=None)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET scheduled_at = ? WHERE id = 1", (naive.isoformat(),)
        )
        await db.commit()

    fired = await _recover(_bot(db_path))

    assert sorted(p for p, _ in fired) == [1, 2, 3]
