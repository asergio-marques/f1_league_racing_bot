"""Unit tests for season approval prerequisite gates (T024).

Tests verify the gate conditions via the service layer methods that
_do_approve uses directly, against an in-memory SQLite database seeded
with the migration schema.
"""

from __future__ import annotations

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations
from services.module_service import ModuleService
from services.season_service import SeasonService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """Temp SQLite DB with full migration schema applied."""
    path = str(tmp_path / "gate_test.db")
    await run_migrations(path)
    return path


async def _seed_server(db_path: str, server_id: int = 1) -> None:
    """Insert a minimal server_configs row."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (?, 100, 200, 300)",
            (server_id,),
        )
        await db.commit()


async def _seed_setup_season(db_path: str, server_id: int = 1) -> int:
    """Insert a SETUP season and return its id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number) "
            "VALUES (?, '2026-01-01', 'SETUP', 1)",
            (server_id,),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def _seed_division(
    db_path: str,
    season_id: int,
    name: str = "Div A",
    forecast_channel_id: int | None = None,
) -> int:
    """Insert a division and return its id."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, tier) "
            "VALUES (?, ?, 10, ?, 1)",
            (season_id, name, forecast_channel_id),
        )
        await db.commit()
        return cursor.lastrowid  # type: ignore[return-value]


async def _set_results_config(
    db_path: str,
    division_id: int,
    results_channel_id: int | None = None,
    standings_channel_id: int | None = None,
) -> None:
    """Upsert division_results_config for a division."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO division_results_config "
            "(division_id, results_channel_id, standings_channel_id) VALUES (?, ?, ?) "
            "ON CONFLICT(division_id) DO UPDATE SET "
            "results_channel_id = excluded.results_channel_id, "
            "standings_channel_id = excluded.standings_channel_id",
            (division_id, results_channel_id, standings_channel_id),
        )
        await db.commit()


async def _add_points_link(db_path: str, season_id: int, config_name: str = "100%") -> None:
    """Insert a season_points_links row."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
            (season_id, config_name),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# Helper: evaluate gate conditions (mirrors _do_approve gate logic)
# ---------------------------------------------------------------------------


async def _check_weather_gate(
    db_path: str, server_id: int, season_id: int
) -> list[str]:
    """Return list of division names missing forecast channel (empty = gate passes)."""
    mod_svc = ModuleService(db_path)
    if not await mod_svc.is_weather_enabled(server_id):
        return []
    svc = SeasonService(db_path)
    divisions = await svc.get_divisions(season_id)
    return [d.name for d in divisions if not d.forecast_channel_id]


async def _check_rs_gate(
    db_path: str, server_id: int, season_id: int
) -> list[str]:
    """Return list of error strings (empty = gate passes)."""
    mod_svc = ModuleService(db_path)
    if not await mod_svc.is_results_enabled(server_id):
        return []
    svc = SeasonService(db_path)
    divs_rs = await svc.get_divisions_with_results_config(season_id)
    errors: list[str] = []
    for d in divs_rs:
        if not d.results_channel_id:
            errors.append(f"{d.name} missing results channel")
        if not d.standings_channel_id:
            errors.append(f"{d.name} missing standings channel")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM season_points_links WHERE season_id = ?",
            (season_id,),
        )
        (count,) = await cursor.fetchone()
    if count == 0:
        errors.append("no points configuration attached")
    return errors


# ---------------------------------------------------------------------------
# Gate behaviour — no modules enabled
# ---------------------------------------------------------------------------


class TestNoGatesWhenModulesDisabled:
    async def test_neither_module_enabled_no_gate_fires(self, db_path):
        """When no modules are enabled, both gate helpers return empty lists."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        await _seed_division(db_path, season_id, forecast_channel_id=None)

        # Divisions intentionally have no channels — should not matter
        assert await _check_weather_gate(db_path, 1, season_id) == []
        assert await _check_rs_gate(db_path, 1, season_id) == []


# ---------------------------------------------------------------------------
# Weather gate
# ---------------------------------------------------------------------------


class TestWeatherGate:
    async def test_blocks_when_division_missing_forecast_channel(self, db_path):
        """Weather enabled + division without forecast channel → gate lists that division."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        await _seed_division(db_path, season_id, "Div A", forecast_channel_id=None)

        mod_svc = ModuleService(db_path)
        await mod_svc.set_weather_enabled(1, True)

        missing = await _check_weather_gate(db_path, 1, season_id)
        assert "Div A" in missing

    async def test_passes_when_all_divisions_have_forecast_channel(self, db_path):
        """Weather enabled + all divisions have forecast channels → gate returns empty list."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        await _seed_division(db_path, season_id, "Div A", forecast_channel_id=999)

        mod_svc = ModuleService(db_path)
        await mod_svc.set_weather_enabled(1, True)

        missing = await _check_weather_gate(db_path, 1, season_id)
        assert missing == []

    async def test_only_lists_divisions_missing_channel(self, db_path):
        """With 2 divisions: one with channel, one without — only missing one is returned."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        await _seed_division(db_path, season_id, "Div A", forecast_channel_id=111)
        await _seed_division(db_path, season_id, "Div B", forecast_channel_id=None)

        mod_svc = ModuleService(db_path)
        await mod_svc.set_weather_enabled(1, True)

        missing = await _check_weather_gate(db_path, 1, season_id)
        assert missing == ["Div B"]


# ---------------------------------------------------------------------------
# R&S gate
# ---------------------------------------------------------------------------


class TestResultsStandingsGate:
    async def test_blocks_when_division_missing_results_channel(self, db_path):
        """R&S enabled + division missing results channel → error reported."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        div_id = await _seed_division(db_path, season_id, "Div A")
        await _set_results_config(db_path, div_id, results_channel_id=None, standings_channel_id=555)
        await _add_points_link(db_path, season_id)

        mod_svc = ModuleService(db_path)
        await mod_svc.set_results_enabled(1, True)

        errors = await _check_rs_gate(db_path, 1, season_id)
        assert any("results channel" in e for e in errors)

    async def test_blocks_when_division_missing_standings_channel(self, db_path):
        """R&S enabled + division missing standings channel → error reported."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        div_id = await _seed_division(db_path, season_id, "Div A")
        await _set_results_config(db_path, div_id, results_channel_id=444, standings_channel_id=None)
        await _add_points_link(db_path, season_id)

        mod_svc = ModuleService(db_path)
        await mod_svc.set_results_enabled(1, True)

        errors = await _check_rs_gate(db_path, 1, season_id)
        assert any("standings channel" in e for e in errors)

    async def test_blocks_when_no_points_config_attached(self, db_path):
        """R&S enabled + channels set but no season_points_links row → error reported."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        div_id = await _seed_division(db_path, season_id, "Div A")
        await _set_results_config(db_path, div_id, results_channel_id=444, standings_channel_id=555)
        # Deliberately NOT adding a points link

        mod_svc = ModuleService(db_path)
        await mod_svc.set_results_enabled(1, True)

        errors = await _check_rs_gate(db_path, 1, season_id)
        assert any("points configuration" in e for e in errors)

    async def test_passes_when_all_prerequisites_met(self, db_path):
        """R&S enabled + all channels set + points link exists → gate returns empty list."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        div_id = await _seed_division(db_path, season_id, "Div A")
        await _set_results_config(db_path, div_id, results_channel_id=444, standings_channel_id=555)
        await _add_points_link(db_path, season_id)

        mod_svc = ModuleService(db_path)
        await mod_svc.set_results_enabled(1, True)

        errors = await _check_rs_gate(db_path, 1, season_id)
        assert errors == []

    async def test_multiple_error_messages_when_all_missing(self, db_path):
        """Division with no channels and no points config → multiple errors returned."""
        await _seed_server(db_path)
        season_id = await _seed_setup_season(db_path)
        await _seed_division(db_path, season_id, "Div A")
        # No division_results_config row, no points link

        mod_svc = ModuleService(db_path)
        await mod_svc.set_results_enabled(1, True)

        errors = await _check_rs_gate(db_path, 1, season_id)
        assert len(errors) >= 3  # missing results, missing standings, no points config


# ---------------------------------------------------------------------------
# US5: weather enable guard — active season + missing forecast channel
# ---------------------------------------------------------------------------


class TestWeatherEnableGuardActiveSeason:
    async def test_is_weather_enabled_can_be_read_for_active_season_check(self, db_path):
        """Verifies the data path that _enable_weather uses: get_active_season +
        get_divisions + check forecast_channel_id. Direct DB verification."""
        await _seed_server(db_path)
        svc = SeasonService(db_path)
        mod_svc = ModuleService(db_path)

        # No active season — weather can be enabled (no gate triggered)
        active = await svc.get_active_season(1)
        assert active is None  # no season, gate won't fire

        # Seed an active season with a division missing a forecast channel
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "INSERT INTO seasons (server_id, start_date, status, season_number) "
                "VALUES (1, '2026-01-01', 'ACTIVE', 1)"
            )
            season_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id, tier) "
                "VALUES (?, 'Div A', 10, NULL, 1)",
                (season_id,),
            )
            await db.commit()

        active = await svc.get_active_season(1)
        assert active is not None

        divisions = await svc.get_divisions(active.id)
        missing = [d.name for d in divisions if not d.forecast_channel_id]
        assert "Div A" in missing
