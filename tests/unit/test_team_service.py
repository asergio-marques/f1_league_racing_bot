"""Unit tests for TeamService.get_teams_with_roles and get_setup_season_team_names."""
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
    """Temp SQLite DB with the tables needed for team service read methods."""
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
                weather_module_enabled INTEGER NOT NULL DEFAULT 0,
                signup_module_enabled  INTEGER NOT NULL DEFAULT 0
            );
            INSERT INTO server_configs (server_id) VALUES (1);

            CREATE TABLE default_teams (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT    NOT NULL,
                max_seats  INTEGER NOT NULL DEFAULT 2,
                is_reserve INTEGER NOT NULL DEFAULT 0,
                UNIQUE(name)
            );

            CREATE TABLE team_role_configs (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name  TEXT    NOT NULL,
                role_id    INTEGER NOT NULL,
                updated_at TEXT    NOT NULL,
                UNIQUE(team_name)
            );

            CREATE TABLE seasons (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                season_number INTEGER NOT NULL DEFAULT 1,
                status        TEXT    NOT NULL DEFAULT 'SETUP'
            );

            CREATE TABLE divisions (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                season_id INTEGER NOT NULL REFERENCES seasons(id)
            );

            CREATE TABLE team_instances (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                division_id INTEGER NOT NULL REFERENCES divisions(id),
                name        TEXT    NOT NULL,
                max_seats   INTEGER NOT NULL DEFAULT 2,
                is_reserve  INTEGER NOT NULL DEFAULT 0,
                UNIQUE(division_id, name)
            );
            """
        )
    return path


async def _add_default_team(db_path: str, name: str, is_reserve: int = 0) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, max_seats, is_reserve) VALUES (?, 2, ?)",
            (name, is_reserve),
        )
        await db.commit()


async def _add_role_config(db_path: str, team_name: str, role_id: int) -> None:
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES (?, ?, datetime('now'))",
            (team_name, role_id),
        )
        await db.commit()


async def _add_season_with_divisions(db_path: str, div_count: int = 1) -> int:
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (season_number, status) VALUES (1, 'SETUP')"
        )
        season_id = cursor.lastrowid
        for _ in range(div_count):
            await db.execute("INSERT INTO divisions (season_id) VALUES (?)", (season_id,))
        await db.commit()
    return season_id


async def _add_team_instance(db_path: str, season_id: int, team_name: str, is_reserve: int = 0) -> None:
    async with aiosqlite.connect(db_path) as db:
        div_rows = await (await db.execute("SELECT id FROM divisions WHERE season_id = ?", (season_id,))).fetchall()
        for div in div_rows:
            await db.execute(
                "INSERT OR IGNORE INTO team_instances (division_id, name, max_seats, is_reserve) VALUES (?, ?, 2, ?)",
                (div[0], team_name, is_reserve),
            )
        await db.commit()


# ---------------------------------------------------------------------------
# get_teams_with_roles
# ---------------------------------------------------------------------------

class TestGetTeamsWithRoles:
    async def test_empty_returns_empty_list(self, db_path):
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_teams_with_roles()
        assert result == []

    async def test_teams_without_roles_have_none_role_id(self, db_path):
        await _add_default_team(db_path, "Alpine")
        await _add_default_team(db_path, "Ferrari")
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_teams_with_roles()
        names = [r["name"] for r in result]
        assert "Alpine" in names
        assert "Ferrari" in names
        assert all(r["role_id"] is None for r in result)

    async def test_teams_with_roles_have_correct_role_id(self, db_path):
        await _add_default_team(db_path, "Mercedes")
        await _add_role_config(db_path, "Mercedes", 999)
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_teams_with_roles()
        assert len(result) == 1
        assert result[0]["name"] == "Mercedes"
        assert result[0]["role_id"] == 999

    async def test_mixed_teams_some_with_roles(self, db_path):
        await _add_default_team(db_path, "Alpine")
        await _add_default_team(db_path, "Ferrari")
        await _add_role_config(db_path, "Ferrari", 777)
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_teams_with_roles()
        by_name = {r["name"]: r for r in result}
        assert by_name["Alpine"]["role_id"] is None
        assert by_name["Ferrari"]["role_id"] == 777

    async def test_reserve_team_included_last(self, db_path):
        await _add_default_team(db_path, "Alpine")
        await _add_default_team(db_path, "Reserve", is_reserve=1)
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_teams_with_roles()
        assert result[-1]["name"] == "Reserve"
        assert result[-1]["is_reserve"] is True


# ---------------------------------------------------------------------------
# get_setup_season_team_names
# ---------------------------------------------------------------------------

class TestGetSetupSeasonTeamNames:
    async def test_empty_season_returns_empty_set(self, db_path):
        season_id = await _add_season_with_divisions(db_path)
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_setup_season_team_names(season_id)
        assert result == set()

    async def test_returns_team_names_present_in_divisions(self, db_path):
        season_id = await _add_season_with_divisions(db_path)
        await _add_team_instance(db_path, season_id, "Ferrari")
        await _add_team_instance(db_path, season_id, "Alpine")
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_setup_season_team_names(season_id)
        assert result == {"Ferrari", "Alpine"}

    async def test_excludes_reserve_teams(self, db_path):
        season_id = await _add_season_with_divisions(db_path)
        await _add_team_instance(db_path, season_id, "Ferrari")
        await _add_team_instance(db_path, season_id, "Reserve", is_reserve=1)
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_setup_season_team_names(season_id)
        assert "Reserve" not in result
        assert "Ferrari" in result

    async def test_deduplicates_across_multiple_divisions(self, db_path):
        season_id = await _add_season_with_divisions(db_path, div_count=2)
        await _add_team_instance(db_path, season_id, "Ferrari")
        from services.team_service import TeamService
        svc = TeamService(db_path)
        result = await svc.get_setup_season_team_names(season_id)
        # Ferrari appears in both divisions but should only appear once in set
        assert result == {"Ferrari"}
