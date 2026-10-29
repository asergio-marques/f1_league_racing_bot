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


#: The two modules whose flag ModuleService reads and sets by a method of its own.
MODULES = ["weather", "signup"]


@pytest.mark.parametrize("module", MODULES)
async def test_a_module_is_off_by_default(db_path, module):
    from leaguebot.core.services.module_service import ModuleService
    svc = ModuleService(db_path)
    assert await getattr(svc, f"is_{module}_enabled")() is False


@pytest.mark.parametrize("module", MODULES)
@pytest.mark.parametrize(
    "settings, expected",
    [
        ((True,), True),
        ((True, False), False),
        ((True, True), True),
        ((False, False), False),
    ],
    ids=["enable", "disable", "enable twice", "disable twice"],
)
async def test_a_module_s_flag_is_the_last_one_set(db_path, module, settings, expected):
    """Setting the flag again to what it already holds is a no-op, and safe."""
    from leaguebot.core.services.module_service import ModuleService
    svc = ModuleService(db_path)
    for value in settings:
        await getattr(svc, f"set_{module}_enabled")(value)
    assert await getattr(svc, f"is_{module}_enabled")() is expected


async def test_weather_and_signup_are_independent(db_path):
    from leaguebot.core.services.module_service import ModuleService
    svc = ModuleService(db_path)
    await svc.set_weather_enabled(True)
    assert await svc.is_signup_enabled() is False
    await svc.set_signup_enabled(True)
    assert await svc.is_weather_enabled() is True
    assert await svc.is_signup_enabled() is True

