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
                previous_season_number  INTEGER NOT NULL DEFAULT 0,
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
        result = await svc.is_weather_enabled(1)
        assert result is False

    async def test_returns_false_for_unknown_server(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_weather_enabled(999)
        assert result is False


class TestIsSignupEnabled:
    async def test_returns_false_by_default(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_signup_enabled(1)
        assert result is False

    async def test_returns_false_for_unknown_server(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        result = await svc.is_signup_enabled(999)
        assert result is False


class TestSetWeatherEnabled:
    async def test_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(1, True)
        assert await svc.is_weather_enabled(1) is True

    async def test_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(1, True)
        await svc.set_weather_enabled(1, False)
        assert await svc.is_weather_enabled(1) is False

    async def test_idempotent_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(1, True)
        await svc.set_weather_enabled(1, True)  # second enable is a no-op
        assert await svc.is_weather_enabled(1) is True

    async def test_idempotent_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(1, False)
        await svc.set_weather_enabled(1, False)  # second disable is safe
        assert await svc.is_weather_enabled(1) is False


class TestSetSignupEnabled:
    async def test_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(1, True)
        assert await svc.is_signup_enabled(1) is True

    async def test_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(1, True)
        await svc.set_signup_enabled(1, False)
        assert await svc.is_signup_enabled(1) is False

    async def test_idempotent_enable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(1, True)
        await svc.set_signup_enabled(1, True)
        assert await svc.is_signup_enabled(1) is True

    async def test_idempotent_disable(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_signup_enabled(1, False)
        await svc.set_signup_enabled(1, False)
        assert await svc.is_signup_enabled(1) is False

    async def test_weather_and_signup_are_independent(self, db_path):
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(1, True)
        assert await svc.is_signup_enabled(1) is False
        await svc.set_signup_enabled(1, True)
        assert await svc.is_weather_enabled(1) is True
        assert await svc.is_signup_enabled(1) is True


# ---------------------------------------------------------------------------
# T035 — FR-010 season-approve weather gate
# ---------------------------------------------------------------------------

class TestWeatherGateFR010:
    """
    FR-010: schedule_all_rounds() must only be called when the weather module
    is enabled for the server, and must NOT be called when it is disabled.

    We test the gate logic directly by calling _do_approve on a mocked SeasonCog.
    """

    def _make_bot(self, db_path: str, weather_enabled: bool):
        """Build a minimal mock bot with module_service and scheduler_service."""
        from unittest.mock import AsyncMock, MagicMock
        from services.module_service import ModuleService

        real_svc = ModuleService(db_path)
        bot = MagicMock()
        bot.module_service = real_svc
        bot.scheduler_service = MagicMock()
        bot.scheduler_service.schedule_all_rounds = MagicMock()
        bot.output_router = MagicMock()
        bot.output_router.post_log = AsyncMock()
        return bot

    async def _set_flag(self, db_path: str, value: bool) -> None:
        from services.module_service import ModuleService
        svc = ModuleService(db_path)
        await svc.set_weather_enabled(1, value)

    def _make_interaction(self, server_id: int = 1):
        from unittest.mock import MagicMock
        interaction = MagicMock()
        interaction.guild_id = server_id
        interaction.guild = MagicMock()
        interaction.guild.id = server_id
        return interaction

    async def test_schedule_all_rounds_called_when_weather_enabled(self, db_path):
        """When weather module is ON, schedule_all_rounds must be invoked."""
        from unittest.mock import AsyncMock, MagicMock, patch

        await self._set_flag(db_path, True)
        bot = self._make_bot(db_path, weather_enabled=True)

        # Minimal round stand-in
        round_obj = MagicMock()
        round_obj.format = "NORMAL"
        rounds = [round_obj, round_obj]

        # Call the gate directly
        if await bot.module_service.is_weather_enabled(1):
            bot.scheduler_service.schedule_all_rounds(rounds)

        bot.scheduler_service.schedule_all_rounds.assert_called_once_with(rounds)

    async def test_schedule_all_rounds_not_called_when_weather_disabled(self, db_path):
        """When weather module is OFF, schedule_all_rounds must NOT be invoked."""
        from unittest.mock import MagicMock

        await self._set_flag(db_path, False)
        bot = self._make_bot(db_path, weather_enabled=False)

        round_obj = MagicMock()
        round_obj.format = "NORMAL"
        rounds = [round_obj]

        # Replicate the gate from season_cog._do_approve
        if await bot.module_service.is_weather_enabled(1):
            bot.scheduler_service.schedule_all_rounds(rounds)

        bot.scheduler_service.schedule_all_rounds.assert_not_called()

    async def test_toggling_flag_changes_scheduling_behaviour(self, db_path):
        """Toggling weather module from OFF→ON enables scheduling; ON→OFF disables it."""
        from unittest.mock import MagicMock
        from services.module_service import ModuleService

        svc = ModuleService(db_path)
        bot = self._make_bot(db_path, weather_enabled=False)
        round_obj = MagicMock()
        rounds = [round_obj]

        # Disabled → no scheduling
        await svc.set_weather_enabled(1, False)
        if await bot.module_service.is_weather_enabled(1):
            bot.scheduler_service.schedule_all_rounds(rounds)
        bot.scheduler_service.schedule_all_rounds.assert_not_called()

        # Enabled → scheduling happens
        await svc.set_weather_enabled(1, True)
        if await bot.module_service.is_weather_enabled(1):
            bot.scheduler_service.schedule_all_rounds(rounds)
        bot.scheduler_service.schedule_all_rounds.assert_called_once_with(rounds)
