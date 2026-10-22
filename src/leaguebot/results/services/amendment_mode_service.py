"""The amendment mode of a season's points, in results.

Results owns the mode's tables (`season_amendment_state`, `season_modification_entries`,
`season_modification_fl`), so it holds the readers of them here. A season in the mode edits a
modification store rather than the configurations it scores on; the readers say whether the
mode is on and whether it is modified, what the store holds for one position or one session's
fastest lap, and whether a staged table reads out of order.
"""
from __future__ import annotations

from leaguebot.core.db.database import get_connection
from leaguebot.results.models.amendment_state import SeasonAmendmentState
from leaguebot.results.utils.points_ordering import ordering_violations


async def get_amendment_state(db_path: str, season_id: int) -> SeasonAmendmentState | None:
    """Return SeasonAmendmentState or None if no record exists."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_id, amendment_active, modified_flag FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return SeasonAmendmentState(
        season_id=row["season_id"],
        amendment_active=bool(row["amendment_active"]),
        modified_flag=bool(row["modified_flag"]),
    )


async def modification_ordering_warnings(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
) -> list[str]:
    """Return how one staged session's table now reads out of order, if it does.

    The modification store's answer to
    :func:`leaguebot.results.services.points_config_service.ordering_warnings`, and given on the same
    terms: a staged edit that breaks the ordering **warns and still applies**. A manager
    restructuring a table mid-season moves through the same transient states as one
    building it in the first place, and the refusal waits for
    the approval, which is where the table would stop being staged and start scoring a
    championship.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_modification_entries "
            "WHERE season_id = ? AND config_name = ? AND session_type = ?",
            (season_id, config_name, session_type),
        )
        rows = await cursor.fetchall()

    return [
        f"position {position} ({points} pts) < position {next_position} ({next_points} pts)"
        for position, points, next_position, next_points in ordering_violations(
            [(r["position"], r["points"]) for r in rows]
        )
    ]


async def staged_position_points(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    position: int,
) -> dict[str, int | None]:
    """The points the modification store holds for one position, read so that the results cog
    can judge whether a request changes nothing (#482).

    ``{"points": None}`` where nothing is held, and always where the season is not in
    amendment mode: a request made outside the mode can never stand, so it reaches the
    store's own refusal, which comes before "nothing changed".
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT e.points FROM season_modification_entries e "
            "JOIN season_amendment_state s ON s.season_id = e.season_id AND s.amendment_active = 1 "
            "WHERE e.season_id = ? AND e.config_name = ? AND e.session_type = ? "
            "AND e.position = ?",
            (season_id, config_name, session_type, position),
        )
        row = await cursor.fetchone()
    return {"points": None if row is None else row["points"]}


async def staged_fastest_lap(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
) -> dict[str, int | None]:
    """The fastest-lap bonus and position limit the modification store holds for one session,
    both ``None`` where it holds no row or the season is not in amendment mode (#482). See
    :func:`staged_position_points`."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT f.fl_points, f.fl_position_limit FROM season_modification_fl f "
            "JOIN season_amendment_state s ON s.season_id = f.season_id AND s.amendment_active = 1 "
            "WHERE f.season_id = ? AND f.config_name = ? AND f.session_type = ?",
            (season_id, config_name, session_type),
        )
        row = await cursor.fetchone()
    if row is None:
        return {"fl_points": None, "fl_position_limit": None}
    return {"fl_points": row["fl_points"], "fl_position_limit": row["fl_position_limit"]}
