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
    """The real schema, migrated. It was once built by hand here, and drifted from the
    migrations the moment signups came to belong to a season (issue #220)."""
    from leaguebot.core.db.database import get_connection, run_migrations

    path = str(tmp_path / "signup_test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (1, 0, 0, 0)"
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# Slot tests
# ---------------------------------------------------------------------------


class TestSlotIdFormat:
    """The durable slot identity is "Mon_19_00" — see AvailabilitySlot's docstring."""

    def test_slot_id_format(self):
        from leaguebot.signup.models.signup_module import AvailabilitySlot
        assert AvailabilitySlot.make_slot_id(1, "19:00") == "Mon_19_00"
        assert AvailabilitySlot.make_slot_id(5, "21:30") == "Fri_21_30"
        assert AvailabilitySlot.make_slot_id(7, "09:05") == "Sun_09_05"

    def test_slot_id_covers_every_weekday(self):
        from leaguebot.signup.models.signup_module import AvailabilitySlot
        assert [AvailabilitySlot.make_slot_id(d, "12:00") for d in range(1, 8)] == [
            "Mon_12_00", "Tue_12_00", "Wed_12_00", "Thu_12_00",
            "Fri_12_00", "Sat_12_00", "Sun_12_00",
        ]

    async def test_get_slots_populates_slot_id(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(5, "21:00")
        slots = await svc.get_slots()
        assert slots[0].slot_id == "Fri_21_00"


class TestSlotAdd:
    async def test_add_happy_path(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, "14:30")
        slots = await svc.get_slots()
        assert len(slots) == 1
        assert slots[0].day_of_week == 1
        assert slots[0].time_hhmm == "14:30"
        assert slots[0].slot_sequence_id == 1

    async def test_add_duplicate_raises(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, "14:30")
        with pytest.raises(ValueError, match="already exists"):
            await svc.add_slot(1, "14:30")


class TestSlotRemove:
    async def test_remove_happy_path(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(2, "10:00")
        result = await svc.remove_slot_by_rank(1)
        assert result is True
        slots = await svc.get_slots()
        assert slots == []

    async def test_remove_out_of_range_returns_false(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        result = await svc.remove_slot_by_rank(99)
        assert result is False

    async def test_no_slots_returns_false(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        result = await svc.remove_slot_by_rank(1)
        assert result is False


class TestChronologicalRanking:
    async def test_slots_ordered_by_day_then_time(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        # Add out of order
        await svc.add_slot(3, "19:00")   # Wed 19:00
        await svc.add_slot(1, "20:00")   # Mon 20:00
        await svc.add_slot(1, "14:30")   # Mon 14:30
        slots = await svc.get_slots()
        assert len(slots) == 3
        # Ordered chronologically regardless of insertion order
        assert slots[0].day_of_week == 1 and slots[0].time_hhmm == "14:30"
        assert slots[1].day_of_week == 1 and slots[1].time_hhmm == "20:00"
        assert slots[2].day_of_week == 3
        # Sequence IDs must reflect chronological order
        assert [s.slot_sequence_id for s in slots] == [1, 2, 3]

    async def test_slot_ids_chronological_when_added_out_of_order(self, db_path):
        """Wednesday added first, then Monday → Monday gets #1, Wednesday gets #2."""
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(3, "18:00")   # Wed 18:00 added first
        await svc.add_slot(1, "17:00")   # Mon 17:00 added second
        slots = await svc.get_slots()
        assert slots[0].day_of_week == 1 and slots[0].slot_sequence_id == 1
        assert slots[1].day_of_week == 3 and slots[1].slot_sequence_id == 2

    async def test_display_ordinals_recomputed_after_remove(self, db_path):
        """Display ordinals are always 1..N chronologically, so a removal renumbers them.

        That is intended: the ordinal is a display convenience, recomputed on read. What
        must not move with it is a driver's recorded availability — see
        ``TestDurableSlotIdentity``.
        """
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, "14:30")   # Mon 14:30 → seq 1
        await svc.add_slot(1, "20:00")   # Mon 20:00 → seq 2
        await svc.add_slot(3, "19:00")   # Wed 19:00 → seq 3
        # Remove seq_id 2 (Mon 20:00); remaining: Mon 14:30, Wed 19:00
        await svc.remove_slot_by_rank(2)
        # Add Fri 18:00; new chronological order: Mon 14:30(1), Wed 19:00(2), Fri 18:00(3)
        await svc.add_slot(5, "18:00")
        slots = await svc.get_slots()
        assert len(slots) == 3
        assert slots[0].day_of_week == 1 and slots[0].time_hhmm == "14:30" and slots[0].slot_sequence_id == 1
        assert slots[1].day_of_week == 3 and slots[1].time_hhmm == "19:00" and slots[1].slot_sequence_id == 2
        assert slots[2].day_of_week == 5 and slots[2].time_hhmm == "18:00" and slots[2].slot_sequence_id == 3


class TestDurableSlotIdentity:
    """Issue #126: changing the slot list must not change what a driver said.

    The ordinals shift — that is what ``test_display_ordinals_recomputed_after_remove``
    pins — but the identity a driver's answer is stored against must not.
    """

    async def _friday_league(self, db_path):
        """Mon 19:00 (#1), Wed 20:00 (#2), Fri 21:00 (#3) — the issue's worked example."""
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.add_slot(1, "19:00")
        await svc.add_slot(3, "20:00")
        await svc.add_slot(5, "21:00")
        return svc

    async def test_removing_a_slot_does_not_change_other_slot_ids(self, db_path):
        """A driver picks Friday (#3). Monday is removed. They are still on Friday."""
        svc = await self._friday_league(db_path)
        slots = await svc.get_slots()
        chosen = next(s for s in slots if s.slot_sequence_id == 3).slot_id
        assert chosen == "Fri_21_00"

        await svc.remove_slot_by_rank(1)   # remove Mon 19:00

        remaining = await svc.get_slots()
        # Friday's display ordinal has moved from 3 to 2 …
        assert [s.slot_sequence_id for s in remaining] == [1, 2]
        # … but the answer still names the same time, and still matches a live slot.
        assert chosen in {s.slot_id for s in remaining}
        assert next(s for s in remaining if s.slot_id == chosen).display_label == (
            "Friday 21:00 UTC"
        )

    async def test_adding_an_earlier_slot_does_not_shift_stored_answers(self, db_path):
        """Inserting a slot before the one a driver chose must not move their answer."""
        svc = await self._friday_league(db_path)
        chosen = next(
            s for s in await svc.get_slots() if s.slot_sequence_id == 3
        ).slot_id

        await svc.add_slot(1, "08:00")   # a new Monday morning slot, earliest of all

        after = await svc.get_slots()
        assert [s.slot_sequence_id for s in after] == [1, 2, 3, 4]
        # Friday is now #4, yet the stored answer resolves to Friday as before.
        assert next(s for s in after if s.slot_id == chosen).time_hhmm == "21:00"
        assert next(s for s in after if s.slot_id == chosen).slot_sequence_id == 4

    async def test_a_re_added_slot_recovers_its_own_answers(self, db_path):
        """Remove a slot and put it back: answers naming it mean the same time again."""
        svc = await self._friday_league(db_path)
        await svc.remove_slot_by_rank(2)   # remove Wed 20:00
        assert "Wed_20_00" not in {s.slot_id for s in await svc.get_slots()}

        await svc.add_slot(3, "20:00")
        assert "Wed_20_00" in {s.slot_id for s in await svc.get_slots()}


class TestSnapshotStoresTheDurableIdentity:
    """A frozen wizard snapshot stores each slot by identity, not by position.

    The display ordinal is rebuilt from the snapshot's own chronological order, so the
    driver mid-wizard still sees the numbers they were shown, and no ordinal is persisted
    anywhere — which is what issue #126 was.
    """

    async def _saved_snapshot(self, db_path, slots):
        from leaguebot.signup.models.signup_module import (
            ConfigSnapshot, SignupWizardRecord, WizardState,
        )
        from leaguebot.signup.services.signup_module_service import SignupModuleService

        svc = SignupModuleService(db_path)
        await svc.save_wizard(SignupWizardRecord(
            id=0, discord_user_id="snap",
            wizard_state=WizardState.COLLECTING_AVAILABILITY,
            signup_channel_id=888,
            config_snapshot=ConfigSnapshot(
                nationality_required=False,
                time_type="TIME_TRIAL",
                time_image_required=False,
                selected_track_ids=[],
                slots=slots,
            ),
            draft_answers={},
            current_lap_track_index=0,
            last_activity_at="2026-01-01T00:00:00",
        ))
        return svc

    async def test_no_display_ordinal_is_persisted(self, db_path):
        import json

        from leaguebot.core.db.database import get_connection
        from leaguebot.signup.services.signup_module_service import SignupModuleService

        svc = SignupModuleService(db_path)
        for day, time_hhmm in ((1, "19:00"), (3, "20:00"), (5, "21:00")):
            await svc.add_slot(day, time_hhmm)
        await self._saved_snapshot(db_path, await svc.get_slots())

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT config_snapshot_json FROM signup_wizard_records "
                "WHERE discord_user_id = 'snap'"
            )
            stored = json.loads((await cursor.fetchone())[0])
        assert [s["slot_id"] for s in stored["slots"]] == [
            "Mon_19_00", "Wed_20_00", "Fri_21_00",
        ]
        assert all("slot_sequence_id" not in s for s in stored["slots"])

    async def test_display_ordinals_rebuilt_on_read(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService

        svc = SignupModuleService(db_path)
        for day, time_hhmm in ((1, "19:00"), (3, "20:00"), (5, "21:00")):
            await svc.add_slot(day, time_hhmm)
        await self._saved_snapshot(db_path, await svc.get_slots())

        snap = (await svc.get_wizard("snap")).config_snapshot
        assert [s.slot_sequence_id for s in snap.slots] == [1, 2, 3]
        assert [s.slot_id for s in snap.slots] == [
            "Mon_19_00", "Wed_20_00", "Fri_21_00",
        ]

    async def test_ordinals_rebuilt_chronologically_however_stored(self, db_path):
        """Order in the stored JSON must not decide the numbers a driver was shown."""
        from leaguebot.signup.services.signup_module_service import SignupModuleService

        svc = SignupModuleService(db_path)
        for day, time_hhmm in ((1, "19:00"), (3, "20:00"), (5, "21:00")):
            await svc.add_slot(day, time_hhmm)
        scrambled = list(reversed(await svc.get_slots()))
        await self._saved_snapshot(db_path, scrambled)

        snap = (await svc.get_wizard("snap")).config_snapshot
        assert [(s.slot_sequence_id, s.slot_id) for s in snap.slots] == [
            (1, "Mon_19_00"), (2, "Wed_20_00"), (3, "Fri_21_00"),
        ]

    async def test_a_snapshot_written_before_durable_ids_still_reads(self, db_path):
        """An in-flight wizard from before this change carries no slot_id — derive it."""
        import json

        from leaguebot.core.db.database import get_connection
        from leaguebot.signup.services.signup_module_service import SignupModuleService

        legacy = {
            "nationality_required": False,
            "time_type": "TIME_TRIAL",
            "time_image_required": False,
            "selected_track_ids": [],
            "team_names": [],
            "slots": [
                {"id": 1, "slot_sequence_id": 1,
                 "day_of_week": 1, "time_hhmm": "19:00"},
                {"id": 2, "slot_sequence_id": 2,
                 "day_of_week": 5, "time_hhmm": "21:00"},
            ],
        }
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_wizard_records (discord_user_id, "
                "wizard_state, signup_channel_id, config_snapshot_json, "
                "draft_answers_json, current_lap_track_index, last_activity_at) "
                "VALUES ('legacy', 'COLLECTING_AVAILABILITY', 888, ?, '{}', 0, "
                "'2026-01-01T00:00:00')",
                (json.dumps(legacy),),
            )
            await db.commit()

        snap = (await SignupModuleService(db_path).get_wizard("legacy")).config_snapshot
        assert [(s.slot_sequence_id, s.slot_id) for s in snap.slots] == [
            (1, "Mon_19_00"), (2, "Fri_21_00"),
        ]


class TestWindowState:
    async def _make_config(self, db_path):
        """Helper: insert a signup_module_config row for server 1."""
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        from leaguebot.signup.models.signup_module import SignupModuleConfig
        svc = SignupModuleService(db_path)
        cfg = SignupModuleConfig(
            signup_channel_id=100,
            signups_open=False,
            signup_button_message_id=None,
            selected_tracks=[],
        )
        await svc.save_config(cfg)
        return svc

    async def test_default_window_closed(self, db_path):
        svc = await self._make_config(db_path)
        assert await svc.get_window_state() is False

    async def test_set_open_then_closed(self, db_path):
        svc = await self._make_config(db_path)
        await svc.set_window_open(button_message_id=999, selected_tracks=["01", "03"])
        assert await svc.get_window_state() is True
        cfg = await svc.get_config()
        assert cfg.signup_button_message_id == 999
        assert cfg.selected_tracks == ["01", "03"]

        await svc.set_window_closed()
        assert await svc.get_window_state() is False
        cfg2 = await svc.get_config()
        assert cfg2.signup_button_message_id is None


# ---------------------------------------------------------------------------
# T053 — SignupRecord CRUD
# ---------------------------------------------------------------------------


def _make_record(user_id: str = "u1") -> "SignupRecord":
    from leaguebot.signup.models.signup_module import SignupRecord
    return SignupRecord(
        id=0,
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
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        rec = _make_record()
        await svc.save_record(rec)
        fetched = await svc.get_record("u1")
        assert fetched is not None
        assert fetched.discord_username == "TestUser"
        assert fetched.platform == "Steam"
        assert fetched.availability_slot_ids == [1, 2]
        assert fetched.lap_times == {"01": "1:23.456"}

    async def test_get_missing_returns_none(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        assert await svc.get_record("ghost") is None

    async def test_a_second_signup_is_kept_beside_the_first(self, db_path):
        """Records are never overwritten (issue #220): the latest is what a review reads."""
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_record(_make_record())
        second = _make_record()
        second.platform = "PSN"
        await svc.save_record(second)

        fetched = await svc.get_record("u1")
        assert fetched is not None
        assert fetched.platform == "PSN"
        from leaguebot.core.db.database import get_connection
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) AS n FROM signup_records WHERE discord_user_id = 'u1'"
            )
            assert (await cursor.fetchone())["n"] == 2

    async def test_saving_a_stored_record_updates_it_in_place(self, db_path):
        """A correction amends the signup it was asked of."""
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        record_id = await svc.save_record(_make_record())
        stored = await svc.get_record("u1")
        stored.platform = "PSN"

        assert await svc.save_record(stored) == record_id
        fetched = await svc.get_record("u1")
        assert fetched.id == record_id
        assert fetched.platform == "PSN"


# ---------------------------------------------------------------------------
# T053 — SignupWizardRecord CRUD
# ---------------------------------------------------------------------------


def _make_wizard(user_id: str = "w1") -> "SignupWizardRecord":
    from leaguebot.signup.models.signup_module import SignupWizardRecord, WizardState
    return SignupWizardRecord(
        id=0,
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
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        from leaguebot.signup.models.signup_module import WizardState
        svc = SignupModuleService(db_path)
        wizard = _make_wizard()
        await svc.save_wizard(wizard)
        fetched = await svc.get_wizard("w1")
        assert fetched is not None
        assert fetched.wizard_state == WizardState.COLLECTING_NATIONALITY
        assert fetched.draft_answers == {"nationality": "gb"}
        assert fetched.signup_channel_id == 777

    async def test_get_missing_returns_none(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        assert await svc.get_wizard("ghost") is None

    async def test_get_by_channel(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard())
        fetched = await svc.get_wizard_by_channel(777)
        assert fetched is not None
        assert fetched.discord_user_id == "w1"

    async def test_get_by_channel_wrong_channel_returns_none(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard())
        assert await svc.get_wizard_by_channel(9999) is None

    async def test_delete_wizard(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard())
        await svc.delete_wizard("w1")
        assert await svc.get_wizard("w1") is None

    async def test_get_all_active_wizards_excludes_unengaged(self, db_path):
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        from leaguebot.signup.models.signup_module import SignupWizardRecord, WizardState
        svc = SignupModuleService(db_path)
        await svc.save_wizard(_make_wizard(user_id="active1"))
        inactive = SignupWizardRecord(
            id=0, discord_user_id="inactive1",
            wizard_state=WizardState.UNENGAGED,
            signup_channel_id=None, config_snapshot=None,
            draft_answers={}, current_lap_track_index=0,
            last_activity_at="2025-01-01T00:00:00",
        )
        await svc.save_wizard(inactive)
        active = await svc.get_all_active_wizards()
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
        from leaguebot.signup.services.signup_module_service import SignupModuleService
        from leaguebot.signup.models.signup_module import (
            SignupModuleSettings, ConfigSnapshot, AvailabilitySlot,
            WizardState, SignupWizardRecord,
        )
        svc = SignupModuleService(db_path)
        # Add a slot and capture a snapshot with nationality_required=True
        await svc.add_slot(1, "20:00")
        slot = (await svc.get_slots())[0]
        snapshot = ConfigSnapshot(
            nationality_required=True,
            time_type="TIME_TRIAL",
            time_image_required=True,
            selected_track_ids=["01", "02"],
            slots=[slot],
        )
        wizard = SignupWizardRecord(
            id=0, discord_user_id="snap1",
            wizard_state=WizardState.COLLECTING_NATIONALITY,
            signup_channel_id=888,
            config_snapshot=snapshot,
            draft_answers={},
            current_lap_track_index=0,
            last_activity_at="2025-01-01T00:00:00",
        )
        await svc.save_wizard(wizard)
        # The snapshot is serialised to JSON — live config no longer influences it.
        fetched = await svc.get_wizard("snap1")
        assert fetched is not None
        snap = fetched.config_snapshot
        assert snap is not None
        assert snap.nationality_required is True
        assert snap.selected_track_ids == ["01", "02"]
        assert len(snap.slots) == 1
        assert snap.slots[0].day_of_week == 1

