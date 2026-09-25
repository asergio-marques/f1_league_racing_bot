"""Unit tests for ModuleService results-module methods (T023)."""

from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database with the league's server_configs row and no results_module_config
    row, which is how a league that has never touched the module stands."""
    path = str(tmp_path / "results_module_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# is_results_enabled
# ---------------------------------------------------------------------------


class TestIsResultsEnabled:
    async def test_default_false_no_row(self, db_path):
        """No row in results_module_config → False."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        assert await svc.is_results_enabled() is False

    async def test_default_false_unknown_server(self, db_path):
        """Server with no config row at all → False."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        assert await svc.is_results_enabled() is False

    async def test_returns_true_after_enable(self, db_path):
        """After set_results_enabled(True), is_results_enabled returns True."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(True)
        assert await svc.is_results_enabled() is True

    async def test_returns_false_after_disable(self, db_path):
        """After enable then disable, returns False."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(True)
        await svc.set_results_enabled(False)
        assert await svc.is_results_enabled() is False


# ---------------------------------------------------------------------------
# set_results_enabled
# ---------------------------------------------------------------------------


class TestSetResultsEnabled:
    async def test_upsert_creates_row(self, db_path):
        """set_results_enabled creates the row when it does not exist."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(True)
        assert await svc.is_results_enabled() is True

    async def test_idempotent_double_enable(self, db_path):
        """Calling set_results_enabled(True) twice does not error."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(True)
        await svc.set_results_enabled(True)
        assert await svc.is_results_enabled() is True

    async def test_idempotent_double_disable(self, db_path):
        """Calling set_results_enabled(False) twice does not error."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(False)
        await svc.set_results_enabled(False)
        assert await svc.is_results_enabled() is False

    async def test_toggle_true_then_false(self, db_path):
        """Enable then disable cycles correctly."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_results_enabled(True)
        assert await svc.is_results_enabled() is True
        await svc.set_results_enabled(False)
        assert await svc.is_results_enabled() is False
