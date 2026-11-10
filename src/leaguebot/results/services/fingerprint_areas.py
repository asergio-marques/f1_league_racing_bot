"""Results' own configuration, handed to the season's fingerprint.

Core's `fingerprint_areas_read` hook asks each module for the rows of its own configuration, on
the connection the fingerprint holds open, whatever the module's switch: a change to what the
module is set to is a change to what the review reported, off or on. Results hands two areas: the
channels and the reserves' place in the standings it holds for each division of the season, and the
points configurations attached to the season, with the server's own entries and fastest-lap rows
of those, which the approval copies onto the season. Reads only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import tuple_rows

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService


async def _in_divisions(
    db: aiosqlite.Connection, seasons: "SeasonService", season_id: int, sql: str
) -> list[tuple[Any, ...]]:
    """The rows *sql* returns for the divisions of *season_id*, as the season service reads them:
    its `{marks}` stand for the divisions' ids."""
    ids = sorted(division.id for division in await seasons.get_divisions_on(db, season_id))
    return await tuple_rows(db, sql.format(marks=", ".join("?" for _ in ids)), *ids)


async def fingerprint_areas(
    seasons: "SeasonService", db: aiosqlite.Connection, season_id: int
) -> dict[str, Any]:
    """Results' areas of the fingerprint of *season_id*: its channels and `reserves_in_standings`
    by division, and its points configurations."""
    return {
        "channels": await _in_divisions(
            db,
            seasons,
            season_id,
            "SELECT division_id, results_channel_id, standings_channel_id, penalty_channel_id, "
            "       reserves_in_standings "
            "FROM division_results_config WHERE division_id IN ({marks}) ORDER BY division_id",
        ),
        "points": [
            await tuple_rows(
                db,
                "SELECT config_name FROM season_points_links "
                "WHERE season_id = ? ORDER BY config_name",
                season_id,
            ),
            await tuple_rows(
                db,
                "SELECT config_name, session_type, position, points "
                "FROM season_points_entries WHERE season_id = ? "
                "ORDER BY config_name, session_type, position",
                season_id,
            ),
            await tuple_rows(
                db,
                "SELECT config_name, session_type, fl_points, fl_position_limit "
                "FROM season_points_fl WHERE season_id = ? "
                "ORDER BY config_name, session_type",
                season_id,
            ),
            # What the approval copies onto the season: the server's own entries and
            # fastest-lap rows of the attached configurations, which an edit before the
            # approval would otherwise change without the fingerprint noticing.
            await tuple_rows(
                db,
                "SELECT s.config_name, e.session_type, e.position, e.points "
                "FROM points_config_entries e "
                "JOIN points_config_store s ON s.id = e.config_id "
                "WHERE s.config_name IN "
                "(SELECT config_name FROM season_points_links WHERE season_id = ?) "
                "ORDER BY s.config_name, e.session_type, e.position",
                season_id,
            ),
            await tuple_rows(
                db,
                "SELECT s.config_name, f.session_type, f.fl_points, f.fl_position_limit "
                "FROM points_config_fl f "
                "JOIN points_config_store s ON s.id = f.config_id "
                "WHERE s.config_name IN "
                "(SELECT config_name FROM season_points_links WHERE season_id = ?) "
                "ORDER BY s.config_name, f.session_type",
                season_id,
            ),
        ],
    }
