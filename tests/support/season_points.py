"""Copy a season's attached points configurations onto it, for a test seeding the season's store.

The approval writes the season's points snapshot inside its one save, through results'
`snapshot_configs_to_season_on`, which reads results' own switch on the connection it is handed
and writes nothing while results is off (#439, slice 4a). A test that only wants the season's
store filled, whatever its modules, calls `snapshot_points`: it switches results on for the
length of the save, copies, and puts the switch back as it was before it commits, so the test's
modules read afterwards as the test set them.

The function is imported inside the helper, so a test file using it still collects while it is
unbuilt.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection


async def snapshot_points(db_path: str, season_id: int) -> None:
    """Copy every configuration attached to *season_id* into its own store, and commit."""
    from leaguebot.results.services.season_points_service import snapshot_configs_to_season_on

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT module_enabled FROM results_module_config WHERE id = 1")
        row = await cursor.fetchone()
        await db.execute(
            "INSERT OR REPLACE INTO results_module_config (id, module_enabled) VALUES (1, 1)"
        )
        await snapshot_configs_to_season_on(db, season_id)
        if row is None:
            await db.execute("DELETE FROM results_module_config WHERE id = 1")
        else:
            await db.execute(
                "UPDATE results_module_config SET module_enabled = ? WHERE id = 1",
                (row["module_enabled"],),
            )
        await db.commit()
