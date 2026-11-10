"""ModuleService — answers whether each optional module is on: weather, signup, results,
attendance and images. Each module's flag is written by that module's own switch, a `Setting` on
the change queue (`<module>/services/module_switch_settings.py`), never by this service.

**A module's switch is asked of the module.** The service reads no module's table or column: each
module signs up the reader of its own switch (`register_switch`, from the builder), and
`is_<module>_enabled` hands that reader a connection to the league's database and gives back its
answer. A module that never signed up is a fault in the bot's assembly, and asking for it raises
`LookupError` naming it, rather than reading as off.
"""
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

    async def is_enabled_on(self, db: aiosqlite.Connection, module: str) -> bool:
        """Whether *module* is on, read on *db*, a connection the caller holds: a save asks a
        module's switch here, so that the read is part of it."""
        reader = self._switch_readers.get(module)
        if reader is None:
            raise LookupError(f"no switch reader is signed up for the {module} module")
        return await reader(db)

    async def _is_enabled(self, module: str) -> bool:
        async with get_connection(self._db_path) as db:
            return await self.is_enabled_on(db, module)

    async def is_weather_enabled(self) -> bool:
        return await self._is_enabled("weather")

    async def is_signup_enabled(self) -> bool:
        return await self._is_enabled("signup")

    async def is_results_enabled(self) -> bool:
        return await self._is_enabled("results")

    async def is_attendance_enabled(self) -> bool:
        return await self._is_enabled("attendance")

    async def is_images_enabled(self) -> bool:
        return await self._is_enabled("images")
