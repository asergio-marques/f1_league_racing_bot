"""Unit tests for SignupModuleService — T031."""

from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path(tmp_path):
    import aiosqlite

    path = str(tmp_path / "signup_test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(
            """
            CREATE TABLE server_configs (
                server_id              INTEGER PRIMARY KEY,
                interaction_role_id    INTEGER NOT NULL DEFAULT 0,
                interaction_channel_id INTEGER NOT NULL DEFAULT 0,
                log_channel_id         INTEGER NOT NULL DEFAULT 0,
                test_mode_active       INTEGER NOT NULL DEFAULT 0,
                previous_season_number INTEGER NOT NULL DEFAULT 0,
                weather_module_enabled INTEGER NOT NULL DEFAULT 0,
                signup_module_enabled  INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO server_configs (server_id) VALUES (1);

            CREATE TABLE signup_module_config (
                server_id                INTEGER PRIMARY KEY
                                            REFERENCES server_configs(server_id)
                                            ON DELETE CASCADE,
                signup_channel_id        INTEGER NOT NULL,
                base_role_id             INTEGER NOT NULL,
                signed_up_role_id        INTEGER NOT NULL,
                signups_open             INTEGER NOT NULL DEFAULT 0,
                signup_button_message_id INTEGER,
                selected_tracks_json     TEXT NOT NULL DEFAULT '[]'
            );

            CREATE TABLE signup_module_settings (
                server_id            INTEGER PRIMARY KEY
                                        REFERENCES server_configs(server_id)
                                        ON DELETE CASCADE,
                nationality_required INTEGER NOT NULL DEFAULT 1,
                time_type            TEXT NOT NULL DEFAULT 'TIME_TRIAL',
                time_image_required  INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE signup_availability_slots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id   INTEGER NOT NULL
                                REFERENCES server_configs(server_id)
                                ON DELETE CASCADE,
                day_of_week INTEGER NOT NULL,
                time_hhmm   TEXT NOT NULL,
                UNIQUE(server_id, day_of_week, time_hhmm)
            );
            """
        )
    return path


# ---------------------------------------------------------------------------
# Slot tests
# ---------------------------------------------------------------------------


class TestSlotAdd:
    async def test_add_happy_path(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, 1, "14:30")
        slots = await svc.get_slots(1)
        assert len(slots) == 1
        assert slots[0].day_of_week == 1
        assert slots[0].time_hhmm == "14:30"
        assert slots[0].slot_id == 1

    async def test_add_duplicate_raises(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, 1, "14:30")
        with pytest.raises(ValueError, match="already exists"):
            await svc.add_slot(1, 1, "14:30")


class TestSlotRemove:
    async def test_remove_happy_path(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, 2, "10:00")
        result = await svc.remove_slot_by_rank(1, 1)
        assert result is True
        slots = await svc.get_slots(1)
        assert slots == []

    async def test_remove_out_of_range_returns_false(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        result = await svc.remove_slot_by_rank(1, 99)
        assert result is False

    async def test_no_slots_returns_false(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        result = await svc.remove_slot_by_rank(1, 1)
        assert result is False


class TestChronologicalRanking:
    async def test_slots_ordered_by_day_then_time(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        # Add out of order
        await svc.add_slot(1, 3, "19:00")   # Wed 19:00
        await svc.add_slot(1, 1, "20:00")   # Mon 20:00
        await svc.add_slot(1, 1, "14:30")   # Mon 14:30
        slots = await svc.get_slots(1)
        assert len(slots) == 3
        assert slots[0].slot_id == 1
        assert slots[0].day_of_week == 1
        assert slots[0].time_hhmm == "14:30"
        assert slots[1].day_of_week == 1
        assert slots[1].time_hhmm == "20:00"
        assert slots[2].day_of_week == 3

    async def test_slot_ids_renumber_after_remove(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, 1, "14:30")
        await svc.add_slot(1, 1, "20:00")
        await svc.add_slot(1, 3, "19:00")
        # Remove slot #2 (Mon 20:00)
        await svc.remove_slot_by_rank(1, 2)
        slots = await svc.get_slots(1)
        assert len(slots) == 2
        assert slots[0].slot_id == 1
        assert slots[1].slot_id == 2


class TestWindowState:
    async def _make_config(self, db_path):
        """Helper: insert a signup_module_config row for server 1."""
        from services.signup_module_service import SignupModuleService
        from models.signup_module import SignupModuleConfig
        svc = SignupModuleService(db_path)
        cfg = SignupModuleConfig(
            server_id=1,
            signup_channel_id=100,
            base_role_id=200,
            signed_up_role_id=300,
            signups_open=False,
            signup_button_message_id=None,
            selected_tracks=[],
        )
        await svc.save_config(cfg)
        return svc

    async def test_default_window_closed(self, db_path):
        svc = await self._make_config(db_path)
        assert await svc.get_window_state(1) is False

    async def test_set_open_then_closed(self, db_path):
        svc = await self._make_config(db_path)
        await svc.set_window_open(1, button_message_id=999, selected_tracks=["01", "03"])
        assert await svc.get_window_state(1) is True
        cfg = await svc.get_config(1)
        assert cfg.signup_button_message_id == 999
        assert cfg.selected_tracks == ["01", "03"]

        await svc.set_window_closed(1)
        assert await svc.get_window_state(1) is False
        cfg2 = await svc.get_config(1)
        assert cfg2.signup_button_message_id is None
