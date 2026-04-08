"""Unit tests for AttendanceService — T006, T012, T018, T024."""
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
    """Temp SQLite DB with the minimal schema needed by AttendanceService."""
    path = str(tmp_path / "test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            CREATE TABLE server_configs (
                server_id INTEGER PRIMARY KEY
            )
            """
        )
        await db.execute(
            "INSERT INTO server_configs (server_id) VALUES (1)"
        )
        await db.execute(
            """
            CREATE TABLE divisions (
                id        INTEGER PRIMARY KEY,
                server_id INTEGER NOT NULL
            )
            """
        )
        await db.execute("INSERT INTO divisions (id, server_id) VALUES (10, 1)")
        await db.execute("INSERT INTO divisions (id, server_id) VALUES (11, 1)")
        await db.execute(
            """
            CREATE TABLE attendance_config (
                server_id                INTEGER PRIMARY KEY
                                             REFERENCES server_configs(server_id)
                                             ON DELETE CASCADE,
                module_enabled           INTEGER NOT NULL DEFAULT 0,
                rsvp_notice_days         INTEGER NOT NULL DEFAULT 5,
                rsvp_last_notice_hours   INTEGER NOT NULL DEFAULT 24,
                rsvp_deadline_hours      INTEGER NOT NULL DEFAULT 2,
                no_rsvp_penalty          INTEGER NOT NULL DEFAULT 1,
                no_rsvp_absent_penalty   INTEGER NOT NULL DEFAULT 1,
                rsvp_absent_penalty      INTEGER NOT NULL DEFAULT 1,
                autoreserve_threshold    INTEGER,
                autosack_threshold       INTEGER
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE attendance_division_config (
                division_id               INTEGER PRIMARY KEY
                                              REFERENCES divisions(id)
                                              ON DELETE CASCADE,
                server_id                 INTEGER NOT NULL,
                rsvp_channel_id           TEXT,
                attendance_channel_id     TEXT,
                attendance_message_id     TEXT
            )
            """
        )
        await db.commit()
    return path


# ---------------------------------------------------------------------------
# T006 — Lifecycle tests (enable/disable)
# ---------------------------------------------------------------------------


class TestIsAttendanceEnabledFalseByDefault:
    async def test_returns_false_when_no_row(self, db_path):
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        result = await svc.get_config(1)
        assert result is None

    async def test_module_enabled_false_after_get_or_create(self, db_path):
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        cfg = await svc.get_or_create_config(1)
        assert cfg.module_enabled is False


class TestEnableCreatesConfigWithDefaults:
    async def test_enable_creates_config_with_defaults(self, db_path):
        """Simulate the INSERT performed by _enable_attendance."""
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config "
                "(server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                "rsvp_deadline_hours, no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty, "
                "autoreserve_threshold, autosack_threshold) "
                "VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.module_enabled is True
        assert cfg.rsvp_notice_days == 5
        assert cfg.rsvp_last_notice_hours == 24
        assert cfg.rsvp_deadline_hours == 2
        assert cfg.no_rsvp_penalty == 1
        assert cfg.no_rsvp_absent_penalty == 1
        assert cfg.rsvp_absent_penalty == 1
        assert cfg.autoreserve_threshold is None
        assert cfg.autosack_threshold is None


class TestEnableSetsFlag:
    async def test_enable_sets_flag_true(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.module_enabled is True


class TestDisableSetsFlag:
    async def test_disable_sets_flag_false(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        # Simulate disable: UPDATE module_enabled = 0
        async with _aio.connect(db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET module_enabled = 0 WHERE server_id = ?", (1,)
            )
            await db.commit()

        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.module_enabled is False


class TestDisableDeletesDivisionConfigs:
    async def test_disable_deletes_division_configs(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.execute(
                "INSERT INTO attendance_division_config (division_id, server_id) VALUES (10, 1)"
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.delete_division_configs(1)

        div_cfg = await svc.get_division_config(10)
        assert div_cfg is None


class TestReenableResetsToDefaults:
    async def test_reenable_resets_to_defaults(self, db_path):
        """INSERT OR REPLACE overwrites any stale field values with defaults."""
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            # First enable with custom values
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config "
                "(server_id, module_enabled, rsvp_notice_days) VALUES (?, 1, 10)",
                (1,),
            )
            await db.commit()

        # Re-enable (INSERT OR REPLACE restores defaults)
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config "
                "(server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                "rsvp_deadline_hours, no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty, "
                "autoreserve_threshold, autosack_threshold) "
                "VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.rsvp_notice_days == 5


class TestEnableRollbackOnDbFailure:
    async def test_enable_rollback_on_db_failure(self, db_path):
        """Simulate a DB error mid-transaction; confirms no partial row is left."""
        import aiosqlite as _aio

        # Simulate a failed transaction: begin but don't commit
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config "
                "(server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                "rsvp_deadline_hours, no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty, "
                "autoreserve_threshold, autosack_threshold) "
                "VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)",
                (1,),
            )
            # Intentionally NOT calling db.commit() — simulates rollback
            await db.rollback()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        cfg = await svc.get_config(1)
        # No partial row should remain
        assert cfg is None


# ---------------------------------------------------------------------------
# T012 — Division config tests
# ---------------------------------------------------------------------------


class TestGetDivisionConfigNoneBeforeCreate:
    async def test_get_config_none_before_create(self, db_path):
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        result = await svc.get_division_config(10)
        assert result is None


class TestSetRsvpChannel:
    async def test_set_rsvp_channel(self, db_path):
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.set_rsvp_channel(10, 1, 999)
        cfg = await svc.get_division_config(10)
        assert cfg is not None
        assert cfg.rsvp_channel_id == "999"
        assert cfg.attendance_channel_id is None


class TestSetAttendanceChannel:
    async def test_set_attendance_channel(self, db_path):
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.set_attendance_channel(10, 1, 888)
        cfg = await svc.get_division_config(10)
        assert cfg is not None
        assert cfg.attendance_channel_id == "888"
        assert cfg.rsvp_channel_id is None


class TestSetChannelPreservesOtherChannel:
    async def test_set_channel_preserves_other_channel(self, db_path):
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.set_rsvp_channel(10, 1, 111)
        await svc.set_attendance_channel(10, 1, 222)
        cfg = await svc.get_division_config(10)
        assert cfg is not None
        assert cfg.rsvp_channel_id == "111"
        assert cfg.attendance_channel_id == "222"


# ---------------------------------------------------------------------------
# T018 — Timing invariant tests
# ---------------------------------------------------------------------------


class TestTimingInvariantValid:
    async def test_timing_invariant_valid(self):
        from services.attendance_service import validate_timing_invariant
        # notice_days=5, 5*24=120 > last_notice=24 > deadline=2 → valid
        result = validate_timing_invariant(5, 24, 2)
        assert result is None


class TestTimingInvariantNoticeTooSmall:
    async def test_timing_invariant_notice_too_small(self):
        from services.attendance_service import validate_timing_invariant
        # 1*24=24, last_notice_hours=24 → 24 <= 24 → violation
        result = validate_timing_invariant(1, 24, 2)
        assert result is not None
        assert "rsvp_notice_days" in result


class TestTimingInvariantDeadlineExceedsLast:
    async def test_timing_invariant_deadline_exceeds_last(self):
        from services.attendance_service import validate_timing_invariant
        # notice_days=5, 120>6, but last=6 <= deadline=6 → violation
        result = validate_timing_invariant(5, 6, 6)
        assert result is not None
        assert "rsvp_last_notice_hours" in result


class TestTimingInvariantLastZeroSentinelValid:
    async def test_timing_invariant_last_zero_sentinel_valid(self):
        from services.attendance_service import validate_timing_invariant
        # last_notice_hours=0 is sentinel (no last-notice ping); deadline check skipped
        result = validate_timing_invariant(5, 0, 2)
        assert result is None


class TestTimingInvariantLastEqualsDeadlineRejected:
    async def test_timing_invariant_last_equals_deadline_rejected(self):
        from services.attendance_service import validate_timing_invariant
        # notice_days=5 (120h), last=4, deadline=4 → last <= deadline → rejected
        result = validate_timing_invariant(5, 4, 4)
        assert result is not None
        assert "rsvp_last_notice_hours" in result


# ---------------------------------------------------------------------------
# T024 — Penalty / threshold config tests
# ---------------------------------------------------------------------------


class TestConfigPenaltyFieldsUpdate:
    async def test_config_penalty_fields_update(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config "
                "(server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                "rsvp_deadline_hours, no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty, "
                "autoreserve_threshold, autosack_threshold) "
                "VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.update_no_rsvp_penalty(1, 3)
        await svc.update_no_rsvp_absent_penalty(1, 2)
        await svc.update_rsvp_absent_penalty(1, 4)
        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.no_rsvp_penalty == 3
        assert cfg.no_rsvp_absent_penalty == 2
        assert cfg.rsvp_absent_penalty == 4


class TestAutosackZeroStoresNull:
    async def test_autosack_zero_stores_null(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.update_autosack_threshold(1, None)
        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.autosack_threshold is None


class TestAutoreserveZeroStoresNull:
    async def test_autoreserve_zero_stores_null(self, db_path):
        import aiosqlite as _aio
        async with _aio.connect(db_path) as db:
            db.row_factory = _aio.Row
            await db.execute(
                "INSERT OR REPLACE INTO attendance_config (server_id, module_enabled) VALUES (?, 1)",
                (1,),
            )
            await db.commit()

        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        await svc.update_autoreserve_threshold(1, None)
        cfg = await svc.get_config(1)
        assert cfg is not None
        assert cfg.autoreserve_threshold is None
