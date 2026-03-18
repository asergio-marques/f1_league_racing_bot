"""Unit tests for ModuleService results-module methods (T023)."""

from __future__ import annotations

import sys
import os

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """Temp SQLite DB with server_configs and results_module_config tables."""
    path = str(tmp_path / "results_module_test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(
            """
            CREATE TABLE server_configs (
                server_id               INTEGER PRIMARY KEY,
                interaction_role_id     INTEGER NOT NULL DEFAULT 0,
                interaction_channel_id  INTEGER NOT NULL DEFAULT 0,
                log_channel_id          INTEGER NOT NULL DEFAULT 0,
                test_mode_active        INTEGER NOT NULL DEFAULT 0,
                previous_season_number  INTEGER NOT NULL DEFAULT 0,
                weather_module_enabled  INTEGER NOT NULL DEFAULT 0,
                signup_module_enabled   INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE results_module_config (
                server_id      INTEGER PRIMARY KEY
                                   REFERENCES server_configs(server_id)
                                   ON DELETE CASCADE,
                module_enabled INTEGER NOT NULL DEFAULT 0
            );

            INSERT INTO server_configs (server_id) VALUES (1);
            """
        )
    return path


# ---------------------------------------------------------------------------
# is_results_enabled
# ---------------------------------------------------------------------------


class TestIsResultsEnabled:
    async def test_default_false_no_row(self, db_path):
        """No row in results_module_config → False."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        assert await svc.is_results_enabled(1) is False

    async def test_default_false_unknown_server(self, db_path):
        """Server with no config row at all → False."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        assert await svc.is_results_enabled(999) is False

    async def test_returns_true_after_enable(self, db_path):
        """After set_results_enabled(True), is_results_enabled returns True."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(1, True)
        assert await svc.is_results_enabled(1) is True

    async def test_returns_false_after_disable(self, db_path):
        """After enable then disable, returns False."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(1, True)
        await svc.set_results_enabled(1, False)
        assert await svc.is_results_enabled(1) is False


# ---------------------------------------------------------------------------
# set_results_enabled
# ---------------------------------------------------------------------------


class TestSetResultsEnabled:
    async def test_upsert_creates_row(self, db_path):
        """set_results_enabled creates the row when it does not exist."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(1, True)
        assert await svc.is_results_enabled(1) is True

    async def test_idempotent_double_enable(self, db_path):
        """Calling set_results_enabled(True) twice does not error."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(1, True)
        await svc.set_results_enabled(1, True)
        assert await svc.is_results_enabled(1) is True

    async def test_idempotent_double_disable(self, db_path):
        """Calling set_results_enabled(False) twice does not error."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(1, False)
        await svc.set_results_enabled(1, False)
        assert await svc.is_results_enabled(1) is False

    async def test_toggle_true_then_false(self, db_path):
        """Enable then disable cycles correctly."""
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(1, True)
        assert await svc.is_results_enabled(1) is True
        await svc.set_results_enabled(1, False)
        assert await svc.is_results_enabled(1) is False
