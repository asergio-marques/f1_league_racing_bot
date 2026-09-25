"""ModuleService — reads and writes the on/off flag of each optional module: weather, signup,
results, attendance and images."""
from __future__ import annotations

import logging

from db.database import get_connection

log = logging.getLogger(__name__)


class ModuleService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def is_weather_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT weather_module_enabled FROM server_configs",
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row["weather_module_enabled"])

    async def is_signup_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT signup_module_enabled FROM server_configs",
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row["signup_module_enabled"])

    async def set_weather_enabled(self, value: bool) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE server_configs SET weather_module_enabled = ?",
                (int(value),),
            )
            await db.commit()

    async def set_signup_enabled(self, value: bool) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE server_configs SET signup_module_enabled = ?",
                (int(value),),
            )
            await db.commit()

    async def is_results_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT module_enabled FROM results_module_config",
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])

    async def set_results_enabled(self, value: bool) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results_module_config (id, module_enabled) "
                "VALUES (?, ?)",
                (1, int(value)),
            )
            await db.commit()

    async def is_attendance_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT module_enabled FROM attendance_config")
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])

    async def set_attendance_enabled(self, value: bool) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET module_enabled = ?", (int(value),)
            )
            await db.commit()

    async def is_images_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT module_enabled FROM image_config"
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])

    async def set_images_enabled(self, value: bool) -> None:
        """Set the flag, creating the row if absent.

        An UPDATE alone silently no-ops when no row exists, so the first enable would
        appear to succeed and leave the module off. Disabling touches nothing else:
        the whole image configuration survives (FR-004a, Principle X.6 exception).
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT INTO image_config (id, module_enabled) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET module_enabled = excluded.module_enabled",
                (int(value),),
            )
            await db.commit()
