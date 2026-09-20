"""Unit tests for ModuleService — T030."""

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
    """Temp SQLite DB with the server_configs table."""
    import aiosqlite

    path = str(tmp_path / "test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            CREATE TABLE server_configs (
                server_id               INTEGER PRIMARY KEY,
                interaction_role_id     INTEGER NOT NULL DEFAULT 0,
                interaction_channel_id  INTEGER NOT NULL DEFAULT 0,
                log_channel_id          INTEGER NOT NULL DEFAULT 0,
                test_mode_active        INTEGER NOT NULL DEFAULT 0,
                weather_module_enabled  INTEGER NOT NULL DEFAULT 0,
                signup_module_enabled   INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        await db.execute(
            "INSERT INTO server_configs (server_id) VALUES (1)"
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# ModuleService tests
# ---------------------------------------------------------------------------


class TestIsWeatherEnabled:
    async def test_returns_false_by_default(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_weather_enabled()
        assert result is False

    async def test_returns_false_for_unknown_server(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_weather_enabled()
        assert result is False


class TestIsSignupEnabled:
    async def test_returns_false_by_default(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_signup_enabled()
        assert result is False

    async def test_returns_false_for_unknown_server(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_signup_enabled()
        assert result is False


class TestSetWeatherEnabled:
    async def test_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(True)
        assert await svc.is_weather_enabled() is True

    async def test_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(True)
        await svc.set_weather_enabled(False)
        assert await svc.is_weather_enabled() is False

    async def test_idempotent_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(True)
        await svc.set_weather_enabled(True)  # second enable is a no-op
        assert await svc.is_weather_enabled() is True

    async def test_idempotent_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(False)
        await svc.set_weather_enabled(False)  # second disable is safe
        assert await svc.is_weather_enabled() is False


class TestSetSignupEnabled:
    async def test_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(True)
        assert await svc.is_signup_enabled() is True

    async def test_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(True)
        await svc.set_signup_enabled(False)
        assert await svc.is_signup_enabled() is False

    async def test_idempotent_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(True)
        await svc.set_signup_enabled(True)
        assert await svc.is_signup_enabled() is True

    async def test_idempotent_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(False)
        await svc.set_signup_enabled(False)
        assert await svc.is_signup_enabled() is False

    async def test_weather_and_signup_are_independent(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(True)
        assert await svc.is_signup_enabled() is False
        await svc.set_signup_enabled(True)
        assert await svc.is_weather_enabled() is True
        assert await svc.is_signup_enabled() is True

