"""ModuleService — reads the on/off flag of each optional module: weather, signup, results,
attendance and images. Each flag is written by that module's own switch, a `Setting` on the
change queue (`<module>/services/module_switch_settings.py`), never by this service."""
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import aiosqlite

from leaguebot.core.db.database import get_connection

log = logging.getLogger(__name__)


#: A module's switch reader: whether the module is on, read on the connection it is handed.
SwitchReader = Callable[[aiosqlite.Connection], Awaitable[bool]]


class ModuleService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._switch_readers: dict[str, SwitchReader] = {}

    def register_switch(self, module: str, reader: SwitchReader) -> None:
        """Sign *module* up with the reader of its own switch: the builder's, once for each
        module, before the bot connects."""
        self._switch_readers[module] = reader

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

    async def is_results_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT module_enabled FROM results_module_config",
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])

    async def is_attendance_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT module_enabled FROM attendance_config")
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])

    async def is_images_enabled(self) -> bool:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT module_enabled FROM image_config"
            )
            row = await cursor.fetchone()
        if row is None:
            return False
        return bool(row[0])
