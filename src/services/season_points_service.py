"""Season points service — attach/detach configs, snapshot, validate, view."""
from __future__ import annotations

import logging
from itertools import groupby

import aiosqlite

from db.database import get_connection
from models.points_config import PointsConfigEntry, PointsConfigFastestLap, SessionType
from services import points_config_service

log = logging.getLogger(__name__)


class SeasonNotInSetupError(Exception):
    pass


class ConfigAlreadyAttachedError(Exception):
    pass


class ConfigNotAttachedError(Exception):
    pass


async def attach_config(
    db_path: str,
    season_id: int,
    config_name: str,
    season_status: str,
) -> None:
    if season_status != "SETUP":
        raise SeasonNotInSetupError(
            f"Config attachment is only allowed for seasons in SETUP (status: {season_status})"
        )
    async with get_connection(db_path) as db:
        try:
            await db.execute(
                "INSERT INTO season_points_links (season_id, config_name) VALUES (?, ?)",
                (season_id, config_name),
            )
            await db.commit()
        except aiosqlite.IntegrityError:
            raise ConfigAlreadyAttachedError(config_name)


async def detach_config(
    db_path: str,
    season_id: int,
    config_name: str,
    season_status: str,
) -> None:
    if season_status != "SETUP":
        raise SeasonNotInSetupError(
            f"Config detachment is only allowed for seasons in SETUP (status: {season_status})"
        )
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "DELETE FROM season_points_links WHERE season_id = ? AND config_name = ?",
            (season_id, config_name),
        )
        await db.commit()
        if cursor.rowcount == 0:
            raise ConfigNotAttachedError(config_name)


async def get_attached_config_names(db_path: str, season_id: int) -> list[str]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name FROM season_points_links WHERE season_id = ? ORDER BY config_name",
            (season_id,),
        )
        rows = await cursor.fetchall()
    return [r["config_name"] for r in rows]


async def snapshot_configs_to_season(
    db_path: str,
    season_id: int,
    server_id: int,
) -> None:
    """Copy all attached server-level configs into the season's own points store."""
    config_names = await get_attached_config_names(db_path, season_id)
    async with get_connection(db_path) as db:
        for config_name in config_names:
            entries, fl_entries = await points_config_service.get_config_entries(
                db_path, server_id, config_name
            )
            for entry in entries:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO season_points_entries
                        (season_id, config_name, session_type, position, points)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (season_id, config_name, entry.session_type.value, entry.position, entry.points),
                )
            for fl in fl_entries:
                await db.execute(
                    """
                    INSERT OR REPLACE INTO season_points_fl
                        (season_id, config_name, session_type, fl_points, fl_position_limit)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (season_id, config_name, fl.session_type.value, fl.fl_points, fl.fl_position_limit),
                )
        await db.commit()


async def validate_monotonic_ordering(db_path: str, season_id: int) -> list[str]:
    """Return a list of error strings for any non-monotonic config/session/position groups."""
    errors: list[str] = []
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT config_name, session_type, position, points
            FROM season_points_entries
            WHERE season_id = ?
            ORDER BY config_name, session_type, position
            """,
            (season_id,),
        )
        rows = await cursor.fetchall()

    for (config_name, session_type), group in groupby(
        rows, key=lambda r: (r["config_name"], r["session_type"])
    ):
        entries = list(group)
        for i in range(len(entries) - 1):
            curr = entries[i]
            nxt = entries[i + 1]
            if curr["points"] < nxt["points"]:
                errors.append(
                    f"Config '{config_name}', {session_type}: "
                    f"position {curr['position']} ({curr['points']} pts) < "
                    f"position {nxt['position']} ({nxt['points']} pts)"
                )
    return errors


async def get_season_points_view(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type_filter: SessionType | None = None,
) -> dict[str, dict]:
    """
    Return points tables and FL data for a config in the season store.

    Returns a dict keyed by session_type label, each value being:
        {"entries": [(position_label, points), ...], "fl": (fl_points, fl_position_limit) | None}

    Trailing zero positions are collapsed to a single "{n}th+: 0" sentinel.
    """
    async with get_connection(db_path) as db:
        query = """
            SELECT config_name, session_type, position, points
            FROM season_points_entries
            WHERE season_id = ? AND config_name = ?
        """
        params: list = [season_id, config_name]
        if session_type_filter is not None:
            query += " AND session_type = ?"
            params.append(session_type_filter.value)
        query += " ORDER BY session_type, position"
        cursor = await db.execute(query, params)
        entry_rows = await cursor.fetchall()

        fl_query = """
            SELECT session_type, fl_points, fl_position_limit
            FROM season_points_fl
            WHERE season_id = ? AND config_name = ?
        """
        fl_params: list = [season_id, config_name]
        if session_type_filter is not None:
            fl_query += " AND session_type = ?"
            fl_params.append(session_type_filter.value)
        fl_cursor = await db.execute(fl_query, fl_params)
        fl_rows = await fl_cursor.fetchall()

    fl_map: dict[str, tuple[int, int | None]] = {
        r["session_type"]: (r["fl_points"], r["fl_position_limit"]) for r in fl_rows
    }

    result: dict[str, dict] = {}
    for session_type, group in groupby(entry_rows, key=lambda r: r["session_type"]):
        raw = [(r["position"], r["points"]) for r in group]
        collapsed = _collapse_trailing_zeros(raw)
        result[session_type] = {
            "entries": collapsed,
            "fl": fl_map.get(session_type),
        }
    return result


def _collapse_trailing_zeros(rows: list[tuple[int, int]]) -> list[tuple[str, int]]:
    """
    Given [(pos, pts), ...] in ascending position order, collapse trailing zeros.

    Returns labelled tuples: [("1", 25), ("2", 18), ("3+", 0)] etc.
    """
    if not rows:
        return []

    # Find the last position with points > 0
    last_nonzero = -1
    for i, (_, pts) in enumerate(rows):
        if pts > 0:
            last_nonzero = i

    if last_nonzero == -1:
        # All zeros — collapse everything
        first_pos = rows[0][0]
        return [(f"{first_pos}+", 0)]

    result: list[tuple[str, int]] = []
    for i, (pos, pts) in enumerate(rows):
        if i <= last_nonzero:
            result.append((str(pos), pts))
        else:
            # First trailing zero — emit sentinel and stop
            result.append((f"{pos}+", 0))
            break

    return result
