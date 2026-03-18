"""Points configuration service — server-level named config CRUD."""
from __future__ import annotations

import logging

import aiosqlite

from db.database import get_connection
from models.points_config import (
    PointsConfigEntry,
    PointsConfigFastestLap,
    PointsConfigStore,
    SessionType,
)

log = logging.getLogger(__name__)


class ConfigAlreadyExistsError(Exception):
    pass


class ConfigNotFoundError(Exception):
    pass


class InvalidSessionTypeError(Exception):
    pass


async def create_config(db_path: str, server_id: int, config_name: str) -> PointsConfigStore:
    async with get_connection(db_path) as db:
        try:
            cursor = await db.execute(
                "INSERT INTO points_config_store (server_id, config_name) VALUES (?, ?)",
                (server_id, config_name),
            )
            await db.commit()
            row_id = cursor.lastrowid
        except aiosqlite.IntegrityError:
            raise ConfigAlreadyExistsError(config_name)
    return PointsConfigStore(id=row_id, server_id=server_id, config_name=config_name)


async def remove_config(db_path: str, server_id: int, config_name: str) -> None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM points_config_store WHERE server_id = ? AND config_name = ?",
            (server_id, config_name),
        )
        row = await cursor.fetchone()
        if row is None:
            raise ConfigNotFoundError(config_name)
        await db.execute(
            "DELETE FROM points_config_store WHERE id = ?",
            (row["id"],),
        )
        await db.commit()


async def _get_config_id(db: aiosqlite.Connection, server_id: int, config_name: str) -> int:
    cursor = await db.execute(
        "SELECT id FROM points_config_store WHERE server_id = ? AND config_name = ?",
        (server_id, config_name),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ConfigNotFoundError(config_name)
    return row["id"]


async def set_session_points(
    db_path: str,
    server_id: int,
    config_name: str,
    session_type: SessionType,
    position: int,
    points: int,
) -> None:
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, server_id, config_name)
        await db.execute(
            """
            INSERT INTO points_config_entries (config_id, session_type, position, points)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type, position)
            DO UPDATE SET points = excluded.points
            """,
            (config_id, session_type.value, position, points),
        )
        await db.commit()


async def set_fl_bonus(
    db_path: str,
    server_id: int,
    config_name: str,
    session_type: SessionType,
    fl_points: int,
) -> None:
    if session_type.is_qualifying:
        raise InvalidSessionTypeError(
            f"Fastest-lap bonus cannot be set for qualifying session type: {session_type.value}"
        )
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, server_id, config_name)
        # Preserve existing fl_position_limit if row already exists
        cursor = await db.execute(
            "SELECT fl_position_limit FROM points_config_fl WHERE config_id = ? AND session_type = ?",
            (config_id, session_type.value),
        )
        existing = await cursor.fetchone()
        fl_position_limit = existing["fl_position_limit"] if existing else None
        await db.execute(
            """
            INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type)
            DO UPDATE SET fl_points = excluded.fl_points
            """,
            (config_id, session_type.value, fl_points, fl_position_limit),
        )
        await db.commit()


async def set_fl_position_limit(
    db_path: str,
    server_id: int,
    config_name: str,
    session_type: SessionType,
    limit: int,
) -> None:
    if session_type.is_qualifying:
        raise InvalidSessionTypeError(
            f"Fastest-lap position limit cannot be set for qualifying session type: {session_type.value}"
        )
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, server_id, config_name)
        cursor = await db.execute(
            "SELECT fl_points FROM points_config_fl WHERE config_id = ? AND session_type = ?",
            (config_id, session_type.value),
        )
        existing = await cursor.fetchone()
        fl_points = existing["fl_points"] if existing else 0
        await db.execute(
            """
            INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(config_id, session_type)
            DO UPDATE SET fl_position_limit = excluded.fl_position_limit
            """,
            (config_id, session_type.value, fl_points, limit),
        )
        await db.commit()


async def get_config_entries(
    db_path: str,
    server_id: int,
    config_name: str,
) -> tuple[list[PointsConfigEntry], list[PointsConfigFastestLap]]:
    async with get_connection(db_path) as db:
        config_id = await _get_config_id(db, server_id, config_name)
        cursor = await db.execute(
            "SELECT id, config_id, session_type, position, points "
            "FROM points_config_entries WHERE config_id = ? ORDER BY session_type, position",
            (config_id,),
        )
        entry_rows = await cursor.fetchall()
        cursor = await db.execute(
            "SELECT id, config_id, session_type, fl_points, fl_position_limit "
            "FROM points_config_fl WHERE config_id = ?",
            (config_id,),
        )
        fl_rows = await cursor.fetchall()

    entries = [
        PointsConfigEntry(
            id=r["id"],
            config_id=r["config_id"],
            session_type=SessionType(r["session_type"]),
            position=r["position"],
            points=r["points"],
        )
        for r in entry_rows
    ]
    fl_entries = [
        PointsConfigFastestLap(
            id=r["id"],
            config_id=r["config_id"],
            session_type=SessionType(r["session_type"]),
            fl_points=r["fl_points"],
            fl_position_limit=r["fl_position_limit"],
        )
        for r in fl_rows
    ]
    return entries, fl_entries


async def list_configs(db_path: str, server_id: int) -> list[PointsConfigStore]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, server_id, config_name FROM points_config_store WHERE server_id = ? ORDER BY config_name",
            (server_id,),
        )
        rows = await cursor.fetchall()
    return [
        PointsConfigStore(id=r["id"], server_id=r["server_id"], config_name=r["config_name"])
        for r in rows
    ]
