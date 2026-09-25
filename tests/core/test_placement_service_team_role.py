"""Unit tests for PlacementService.delete_team_role_config and rename_team_role_config."""
from __future__ import annotations

import json

import aiosqlite
import pytest

from leaguebot.core.db.database import get_connection, run_migrations


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db_path(tmp_path):
    """A migrated database with the league's server_configs row."""
    path = str(tmp_path / "test.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute("INSERT INTO server_configs (server_id) VALUES (1)")
        await db.commit()
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
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
            (team_name, role_id),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# delete_team_role_config
# ---------------------------------------------------------------------------

class TestDeleteTeamRoleConfig:
    async def test_existing_row_is_deleted(self, db_path):
        await _seed_role(db_path, "Ferrari", 111)
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.delete_team_role_config("Ferrari", actor_id=9, actor_name="admin")
        row = await _get_role_row(db_path, "Ferrari")
        assert row is None

    async def test_existing_row_writes_audit(self, db_path):
        await _seed_role(db_path, "Ferrari", 111)
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.delete_team_role_config("Ferrari", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 1

    async def test_not_found_is_silent_no_op(self, db_path):
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        # Should not raise
        await svc.delete_team_role_config("NonExistent", actor_id=9, actor_name="admin")

    async def test_not_found_writes_no_audit(self, db_path):
        from leaguebot.core.services.placement_service import PlacementService
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
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Red Bull", "Oracle Red Bull", actor_id=9, actor_name="admin")
        old = await _get_role_row(db_path, "Red Bull")
        new = await _get_role_row(db_path, "Oracle Red Bull")
        assert old is None
        assert new is not None
        assert new["role_id"] == 222

    async def test_existing_row_writes_audit(self, db_path):
        await _seed_role(db_path, "Red Bull", 222)
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Red Bull", "Oracle Red Bull", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 1

    async def test_not_found_is_silent_no_op(self, db_path):
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Ghost", "Ghost2", actor_id=9, actor_name="admin")

    async def test_not_found_writes_no_audit(self, db_path):
        from leaguebot.core.services.placement_service import PlacementService
        svc = PlacementService(db_path)
        await svc.rename_team_role_config("Ghost", "Ghost2", actor_id=9, actor_name="admin")
        count = await _count_audit(db_path, "TEAM_ROLE_CONFIG")
        assert count == 0
