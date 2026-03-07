"""ModuleService — read/write weather and signup module enable flags."""
from __future__ import annotations

import logging

from db.database import get_connection

log = logging.getLogger(__name__)


class ModuleService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def is_weather_enabled(self, server_id: int) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT weather_module_enabled FROM server_configs WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row["weather_module_enabled"])

    async def is_signup_enabled(self, server_id: int) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT signup_module_enabled FROM server_configs WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row["signup_module_enabled"])

    async def set_weather_enabled(self, server_id: int, value: bool) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE server_configs SET weather_module_enabled = ? WHERE server_id = ?",
                (int(value), server_id),
            )
            await db.commit()

    async def set_signup_enabled(self, server_id: int, value: bool) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE server_configs SET signup_module_enabled = ? WHERE server_id = ?",
                (int(value), server_id),
            )
            await db.commit()
