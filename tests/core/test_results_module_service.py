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


async def _set_flag(db_path: str, on: bool) -> None:
    """Results' flag written as `/module enable|disable results` leaves it."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, ?)",
            (int(on),),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# is_results_enabled
# ---------------------------------------------------------------------------


class TestIsResultsEnabled:
    async def test_default_false_no_row(self, db_path):
        """No row in results_module_config → False."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        assert await svc.is_results_enabled() is False

    async def test_returns_true_after_enable(self, db_path):
        """With the flag on, is_results_enabled returns True."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await _set_flag(db_path, True)
        assert await svc.is_results_enabled() is True

    async def test_returns_false_after_disable(self, db_path):
        """With the flag turned on and then off, returns False."""
        from leaguebot.core.services.module_service import ModuleService
        svc = ModuleService(db_path)
        await _set_flag(db_path, True)
        await _set_flag(db_path, False)
        assert await svc.is_results_enabled() is False
