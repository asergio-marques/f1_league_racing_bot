"""A driver's test display name, read on a handed connection (#284, S7).

`driver_test_names_on` is the core half of what a module's query of `driver_profiles` asked when it
named a driver in a message: the name each profile carries, keyed by account.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.drivers.driver_service import driver_test_names_on


async def _seed(path: str) -> None:
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, test_display_name) "
            "VALUES ('101', 'UNASSIGNED', 'Ada'), ('102', 'UNASSIGNED', NULL), "
            "('103', 'UNASSIGNED', 'Cy')"
        )
        await db.commit()


async def test_each_profile_asked_for_is_read_with_its_name(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        names = await driver_test_names_on(db, [101, 102, 999])
    assert names == {101: "Ada", 102: None}


async def test_none_asked_for_reads_nothing(tmp_path):
    path = str(tmp_path / "league.db")
    await _seed(path)
    async with get_connection(path) as db:
        assert await driver_test_names_on(db, []) == {}
