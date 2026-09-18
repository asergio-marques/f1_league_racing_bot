"""Unit tests for PlacementService.delete_team_role_config and rename_team_role_config."""
from __future__ import annotations

import json
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
    """Temp SQLite DB with server_configs, team_role_configs, and audit_entries."""
    path = str(tmp_path / "test.db")
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

            CREATE TABLE team_role_configs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name  TEXT    NOT NULL,
                role_id    INTEGER NOT NULL,
                updated_at TEXT    NOT NULL,
                UNIQUE(team_name)
            );

            CREATE TABLE audit_entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id    INTEGER NOT NULL,
                actor_name  TEXT    NOT NULL,
                division_id INTEGER,
                change_type TEXT    NOT NULL,
                old_value   TEXT    NOT NULL,
                new_value   TEXT    NOT NULL,
                timestamp   TEXT    NOT NULL
            );
            """
        )
    return path


async def _get_role_row(db_path: str, team_name: str):
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM team_role_configs WHERE team_name = ?",
            (team_name,),
        )
        return await cursor.fetchone()


async def _count_audit(db_path: str, change_type: str) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM audit_entries WHERE change_type = ?", (change_type,)
        )
        row = await cursor.fetchone()
        return row[0]


async def _seed_role(db_path: str, team_name: str, role_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) "
            "VALUES (?, ?, datetime('now'))",
            (team_name, role_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# delete_team_role_config
# ---------------------------------------------------------------------------

class TestDeleteTeamRoleConfig:
    async def test_existing_row_is_deleted(self, db_path):
        await _seed_role(db_path, "Ferrari", 111)
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.delete_team_role_config("Ferrari", actor_id=9, actor_name="admin")
        row = await _get_role_row(db_path, "Ferrari")
        assert row is None

    async def test_existing_row_writes_audit(self, db_path):
        await _seed_role(db_path, "Ferrari", 111)
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.delete_team_role_config("Ferrari", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 1

    async def test_not_found_is_silent_no_op(self, db_path):
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        # Should not raise
        await svc.delete_team_role_config("NonExistent", actor_id=9, actor_name="admin")

    async def test_not_found_writes_no_audit(self, db_path):
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.delete_team_role_config("NonExistent", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 0


# ---------------------------------------------------------------------------
# rename_team_role_config
# ---------------------------------------------------------------------------

class TestRenameTeamRoleConfig:
    async def test_existing_row_is_renamed(self, db_path):
        await _seed_role(db_path, "Red Bull", 222)
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Red Bull", "Oracle Red Bull", actor_id=9, actor_name="admin")
        old = await _get_role_row(db_path, "Red Bull")
        new = await _get_role_row(db_path, "Oracle Red Bull")
        assert old is None
        assert new is not None
        assert new["role_id"] == 222

    async def test_existing_row_writes_audit(self, db_path):
        await _seed_role(db_path, "Red Bull", 222)
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Red Bull", "Oracle Red Bull", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 1

    async def test_not_found_is_silent_no_op(self, db_path):
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Ghost", "Ghost2", actor_id=9, actor_name="admin")

    async def test_not_found_writes_no_audit(self, db_path):
        from services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Ghost", "Ghost2", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 0
