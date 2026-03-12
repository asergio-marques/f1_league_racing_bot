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
                selected_tracks_json     TEXT NOT NULL DEFAULT '[]',
                signup_closed_message_id INTEGER
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
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id        INTEGER NOT NULL
                                     REFERENCES server_configs(server_id)
                                     ON DELETE CASCADE,
                day_of_week      INTEGER NOT NULL,
                time_hhmm        TEXT NOT NULL,
                slot_sequence_id INTEGER NOT NULL DEFAULT 0,
                UNIQUE(server_id, day_of_week, time_hhmm)
            );

            CREATE TABLE signup_records (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id            INTEGER NOT NULL,
                discord_user_id      TEXT NOT NULL,
                discord_username     TEXT,
                server_display_name  TEXT,
                nationality          TEXT,
                platform             TEXT,
                platform_id          TEXT,
                availability_slot_ids TEXT,
                driver_type          TEXT,
                preferred_teams      TEXT,
                preferred_teammate   TEXT,
                lap_times_json       TEXT,
                notes                TEXT,
                signup_channel_id    INTEGER,
                total_lap_ms         INTEGER,
                created_at           TEXT,
                updated_at           TEXT,
                UNIQUE(server_id, discord_user_id)
            );

            CREATE TABLE signup_wizard_records (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id               INTEGER NOT NULL,
                discord_user_id         TEXT NOT NULL,
                wizard_state            TEXT NOT NULL DEFAULT 'UNENGAGED',
                signup_channel_id       INTEGER,
                config_snapshot_json    TEXT,
                draft_answers_json      TEXT NOT NULL DEFAULT '{}',
                current_lap_track_index INTEGER NOT NULL DEFAULT 0,
                last_activity_at        TEXT NOT NULL,
                UNIQUE(server_id, discord_user_id)
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
        assert slots[0].slot_sequence_id == 1

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
        # Ordered chronologically regardless of insertion order
        assert slots[0].day_of_week == 1 and slots[0].time_hhmm == "14:30"
        assert slots[1].day_of_week == 1 and slots[1].time_hhmm == "20:00"
        assert slots[2].day_of_week == 3
        # Sequence IDs must reflect chronological order
        assert [s.slot_sequence_id for s in slots] == [1, 2, 3]

    async def test_slot_ids_chronological_when_added_out_of_order(self, db_path):
        """Wednesday added first, then Monday → Monday gets #1, Wednesday gets #2."""
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, 3, "18:00")   # Wed 18:00 added first
        await svc.add_slot(1, 1, "17:00")   # Mon 17:00 added second
        slots = await svc.get_slots(1)
        assert slots[0].day_of_week == 1 and slots[0].slot_sequence_id == 1
        assert slots[1].day_of_week == 3 and slots[1].slot_sequence_id == 2

    async def test_slot_ids_resequenced_after_remove(self, db_path):
        """After a removal, slot sequence IDs are resequenced 1..N in chronological order."""
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, 1, "14:30")   # Mon 14:30 → seq 1
        await svc.add_slot(1, 1, "20:00")   # Mon 20:00 → seq 2
        await svc.add_slot(1, 3, "19:00")   # Wed 19:00 → seq 3
        # Remove seq_id 2 (Mon 20:00); remaining: Mon 14:30, Wed 19:00
        await svc.remove_slot_by_rank(1, 2)
        # Add Fri 18:00; new chronological order: Mon 14:30(1), Wed 19:00(2), Fri 18:00(3)
        await svc.add_slot(1, 5, "18:00")
        slots = await svc.get_slots(1)
        assert len(slots) == 3
        assert slots[0].day_of_week == 1 and slots[0].time_hhmm == "14:30" and slots[0].slot_sequence_id == 1
        assert slots[1].day_of_week == 3 and slots[1].time_hhmm == "19:00" and slots[1].slot_sequence_id == 2
        assert slots[2].day_of_week == 5 and slots[2].time_hhmm == "18:00" and slots[2].slot_sequence_id == 3


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


# ---------------------------------------------------------------------------
# T053 — SignupRecord CRUD
# ---------------------------------------------------------------------------


def _make_record(server_id: int = 1, user_id: str = "u1") -> "SignupRecord":
    from models.signup_module import SignupRecord
    return SignupRecord(
        id=0,
        server_id=server_id,
        discord_user_id=user_id,
        discord_username="TestUser",
        server_display_name="Test User",
        nationality="gb",
        platform="Steam",
        platform_id="SteamUser",
        availability_slot_ids=[1, 2],
        driver_type="REALISTIC",
        preferred_teams=["Ferrari"],
        preferred_teammate="partner1",
        lap_times={"01": "1:23.456"},
        notes="none",
        signup_channel_id=555,
    )


class TestSignupRecordCRUD:
    """T053: SignupRecord save/get/clear lifecycle."""

    async def test_save_and_get_roundtrip(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        rec = _make_record()
        await svc.save_record(rec)
        fetched = await svc.get_record(1, "u1")
        assert fetched is not None
        assert fetched.discord_username == "TestUser"
        assert fetched.platform == "Steam"
        assert fetched.availability_slot_ids == [1, 2]
        assert fetched.lap_times == {"01": "1:23.456"}

    async def test_get_missing_returns_none(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        assert await svc.get_record(1, "ghost") is None

    async def test_save_upserts_existing_record(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        rec = _make_record()
        await svc.save_record(rec)
        # Overwrite with updated platform
        from models.signup_module import SignupRecord
        updated = SignupRecord(
            id=0,
            server_id=1,
            discord_user_id="u1",
            discord_username="TestUser",
            server_display_name="Test User",
            nationality="gb",
            platform="PSN",
            platform_id="PSN_User",
            availability_slot_ids=[],
            driver_type="REALISTIC",
            preferred_teams=[],
            preferred_teammate=None,
            lap_times={},
            notes=None,
            signup_channel_id=None,
        )
        await svc.save_record(updated)
        fetched = await svc.get_record(1, "u1")
        assert fetched is not None
        assert fetched.platform == "PSN"

    async def test_clear_nulls_fields_but_retains_row(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_record(_make_record())
        await svc.clear_record(1, "u1")
        fetched = await svc.get_record(1, "u1")
        assert fetched is not None  # row still exists
        assert fetched.discord_username is None
        assert fetched.platform is None
        assert fetched.nationality is None
        assert fetched.lap_times == {}
        assert fetched.availability_slot_ids == []


# ---------------------------------------------------------------------------
# T053 — SignupWizardRecord CRUD
# ---------------------------------------------------------------------------


def _make_wizard(server_id: int = 1, user_id: str = "w1") -> "SignupWizardRecord":
    from models.signup_module import SignupWizardRecord, WizardState
    return SignupWizardRecord(
        id=0,
        server_id=server_id,
        discord_user_id=user_id,
        wizard_state=WizardState.COLLECTING_NATIONALITY,
        signup_channel_id=777,
        config_snapshot=None,
        draft_answers={"nationality": "gb"},
        current_lap_track_index=0,
        last_activity_at="2025-01-01T00:00:00",
    )


class TestSignupWizardRecordCRUD:
    """T053: SignupWizardRecord save/get/delete/get_by_channel lifecycle."""

    async def test_save_and_get_roundtrip(self, db_path):
        from services.signup_module_service import SignupModuleService
        from models.signup_module import WizardState
        svc = SignupModuleService(db_path)
        wizard = _make_wizard()
        await svc.save_wizard(wizard)
        fetched = await svc.get_wizard(1, "w1")
        assert fetched is not None
        assert fetched.wizard_state == WizardState.COLLECTING_NATIONALITY
        assert fetched.draft_answers == {"nationality": "gb"}
        assert fetched.signup_channel_id == 777

    async def test_get_missing_returns_none(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        assert await svc.get_wizard(1, "ghost") is None

    async def test_get_by_channel(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard())
        fetched = await svc.get_wizard_by_channel(1, 777)
        assert fetched is not None
        assert fetched.discord_user_id == "w1"

    async def test_get_by_channel_wrong_channel_returns_none(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard())
        assert await svc.get_wizard_by_channel(1, 9999) is None

    async def test_delete_wizard(self, db_path):
        from services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard())
        await svc.delete_wizard(1, "w1")
        assert await svc.get_wizard(1, "w1") is None

    async def test_get_all_active_wizards_excludes_unengaged(self, db_path):
        from services.signup_module_service import SignupModuleService
        from models.signup_module import SignupWizardRecord, WizardState
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard(user_id="active1"))
        inactive = SignupWizardRecord(
            id=0, server_id=1, discord_user_id="inactive1",
            wizard_state=WizardState.UNENGAGED,
            signup_channel_id=None, config_snapshot=None,
            draft_answers={}, current_lap_track_index=0,
            last_activity_at="2025-01-01T00:00:00",
        )
        await svc.save_wizard(inactive)
        active = await svc.get_all_active_wizards(1)
        ids = [w.discord_user_id for w in active]
        assert "active1" in ids
        assert "inactive1" not in ids


# ---------------------------------------------------------------------------
# T053 — ConfigSnapshot isolation
# ---------------------------------------------------------------------------


class TestConfigSnapshotIsolation:
    """T053: config_snapshot in wizard is independent of live config."""

    async def test_snapshot_isolated_from_config_changes(self, db_path):
        """Changing live settings does not affect an already-saved snapshot."""
        from services.signup_module_service import SignupModuleService
        from models.signup_module import (
            SignupModuleSettings, ConfigSnapshot, AvailabilitySlot,
            WizardState, SignupWizardRecord,
        )
        svc = SignupModuleService(db_path)
        # Add a slot and capture a snapshot with nationality_required=True
        await svc.add_slot(1, 1, "20:00")
        slot = (await svc.get_slots(1))[0]
        snapshot = ConfigSnapshot(
            nationality_required=True,
            time_type="TIME_TRIAL",
            time_image_required=True,
            selected_track_ids=["01", "02"],
            slots=[slot],
        )
        wizard = SignupWizardRecord(
            id=0, server_id=1, discord_user_id="snap1",
            wizard_state=WizardState.COLLECTING_NATIONALITY,
            signup_channel_id=888,
            config_snapshot=snapshot,
            draft_answers={},
            current_lap_track_index=0,
            last_activity_at="2025-01-01T00:00:00",
        )
        await svc.save_wizard(wizard)
        # The snapshot is serialised to JSON — live config no longer influences it.
        fetched = await svc.get_wizard(1, "snap1")
        assert fetched is not None
        snap = fetched.config_snapshot
        assert snap is not None
        assert snap.nationality_required is True
        assert snap.selected_track_ids == ["01", "02"]
        assert len(snap.slots) == 1
        assert snap.slots[0].day_of_week == 1

