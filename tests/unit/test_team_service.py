"""Unit tests for TeamService.get_teams_with_roles and get_setup_season_team_names."""
from __future__ import annotations

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402


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


async def _add_default_team(db_path: str, name: str, is_reserve: int = 0) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) VALUES (?, ?, 2, ?)",
            (name, name, is_reserve),
        )
        await db.commit()


async def _add_role_config(db_path: str, team_name: str, role_id: int) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
            (team_name, role_id),
        )
        await db.commit()


async def _add_season_with_divisions(db_path: str, div_count: int = 1) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, season_number, status) "
            "VALUES ('2026-01-01', 1, 'SETUP')"
        )
        season_id = cursor.lastrowid
        for number in range(1, div_count + 1):
            await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (?, ?, ?)",
                (season_id, f"Division {number}", 100 + number),
            )
        await db.commit()
    return season_id


async def _add_team_instance(db_path: str, season_id: int, team_name: str, is_reserve: int = 0) -> None:
    async with get_connection(db_path) as db:
        div_rows = await (await db.execute("SELECT id FROM divisions WHERE season_id = ?", (season_id,))).fetchall()
        for div in div_rows:
            await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) VALUES (?, ?, ?, 2, ?)",
                (div[0], team_name, team_name, is_reserve),
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
