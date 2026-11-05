"""Unit tests for ModuleService — T030."""

from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    """A migrated database with the league's one server_configs row."""
    path = str(tmp_path / "test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# ModuleService tests
# ---------------------------------------------------------------------------


#: The two modules whose flag sits on core's `server_configs`, each read by a method of its own.
MODULES = ["weather", "signup"]


@pytest.mark.parametrize("module", MODULES)
async def test_a_module_is_off_by_default(db_path, module):
    from leaguebot.core.services.module_service import ModuleService
    svc = ModuleService(db_path)
    assert await getattr(svc, f"is_{module}_enabled")() is False


async def test_weather_and_signup_are_independent(db_path):
    """Weather's flag on, then signup's, each written as its switch leaves it: each getter
    reads its own module's flag alone."""
    from leaguebot.core.services.module_service import ModuleService
    from tests.support.season_league import set_module_flag

    svc = ModuleService(db_path)
    await set_module_flag(db_path, "weather", True)
    assert await svc.is_signup_enabled() is False
    await set_module_flag(db_path, "signup", True)
    assert await svc.is_weather_enabled() is True
    assert await svc.is_signup_enabled() is True
