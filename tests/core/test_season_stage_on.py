"""A season's stage, read on a handed connection (#284, S7).

`season_stage_on` is the core half of what a module's query joining `seasons` asked for the stage:
the stage of one season by its id, None where there is no such season.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.seasons.season_lifecycle_service import season_stage_on


async def _seed(path: str) -> None:
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status, stage) VALUES "
            "(1, 1, '2026-06-01', 'ACTIVE', 'PENDING_COMPLETION'), "
            "(2, 2, '2025-01-01', 'COMPLETED', 'COMPLETED')"
        )
        await db.commit()


async def test_a_season_s_stage_is_read(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        assert await season_stage_on(db, 1) is SeasonStage.PENDING_COMPLETION


async def test_a_completed_season_reads_its_stage_and_no_season_reads_none(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        assert await season_stage_on(db, 2) is SeasonStage.COMPLETED
        assert await season_stage_on(db, 99) is None
