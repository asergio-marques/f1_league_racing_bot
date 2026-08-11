"""ImageConfigService — CRUD over image_config and image_aspect_toggles.

Configuration retention (FR-004a): disabling the module clears only ``module_enabled``.
Nothing in this service deletes a configuration row or resets a toggle, which is the
Principle X.6 exception for configuration that cannot go stale — no value here names a
Discord channel, role, message or scheduled job.
"""
from __future__ import annotations

import logging

from db.database import get_connection
from models.image_constants import ASPECTS, ASSET_DIRECTORIES, TEMPLATE_COLUMNS
from models.image_module import ImageConfig

log = logging.getLogger(__name__)


#: Columns a slash command may write. Anything outside this set is rejected, so a caller
#: cannot reach `module_enabled` or `server_id` through the generic setter.
SETTABLE_COLUMNS: frozenset[str] = frozenset(
    {"template_directory"}
    | set(TEMPLATE_COLUMNS)
    | set(ASSET_DIRECTORIES)
    | {"time_zone", "time_format", "date_format", "fastest_lap_colour"}
)

#: Ordered column list used to build an ImageConfig from a row.
_CONFIG_COLUMNS: tuple[str, ...] = (
    ("server_id", "module_enabled", "template_directory")
    + tuple(TEMPLATE_COLUMNS)
    + tuple(ASSET_DIRECTORIES)
    + ("time_zone", "time_format", "date_format", "fastest_lap_colour")
)


class UnknownConfigField(ValueError):
    """Raised when a write targets a column outside SETTABLE_COLUMNS."""


class ImageConfigService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Reads ─────────────────────────────────────────────────────────────

    async def get_config(self, server_id: int) -> ImageConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM image_config WHERE server_id = ?", (server_id,)
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_config(row)

    async def get_toggles(self, server_id: int) -> dict[str, bool]:
        """Return every aspect's state. Aspects with no row read as disabled."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT aspect, enabled FROM image_aspect_toggles WHERE server_id = ?",
                (server_id,),
            )
            rows = await cursor.fetchall()
        stored = {row["aspect"]: bool(row["enabled"]) for row in rows}
        return {aspect: stored.get(aspect, False) for aspect in ASPECTS}

    async def is_aspect_enabled(self, server_id: int, aspect: str) -> bool:
        return (await self.get_toggles(server_id)).get(aspect, False)

    # ── Writes ────────────────────────────────────────────────────────────

    async def create_with_defaults(self, server_id: int) -> ImageConfig:
        """Create the config row and all eight toggle rows in one transaction.

        Idempotent: an existing configuration is left exactly as it is, which is what
        makes re-enabling after a disable lossless (FR-004a).
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO image_config (server_id) VALUES (?)",
                (server_id,),
            )
            await db.executemany(
                "INSERT OR IGNORE INTO image_aspect_toggles (server_id, aspect, enabled) "
                "VALUES (?, ?, 0)",
                [(server_id, aspect) for aspect in ASPECTS],
            )
            await db.commit()

            cursor = await db.execute(
                "SELECT * FROM image_config WHERE server_id = ?", (server_id,)
            )
            row = await cursor.fetchone()

        return _row_to_config(row)

    async def set_field(self, server_id: int, column: str, value: str) -> None:
        """Write a single configuration column, guarded by the allow-list."""
        if column not in SETTABLE_COLUMNS:
            raise UnknownConfigField(f"`{column}` is not a settable image config field.")

        # Column name is interpolated because SQLite cannot parameterise identifiers;
        # it is safe only because it was checked against the allow-list above.
        async with get_connection(self._db_path) as db:
            await db.execute(
                f"UPDATE image_config SET {column} = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def set_aspect(self, server_id: int, aspect: str, enabled: bool) -> None:
        if aspect not in ASPECTS:
            raise UnknownConfigField(f"`{aspect}` is not a known image aspect.")
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT INTO image_aspect_toggles (server_id, aspect, enabled) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(server_id, aspect) DO UPDATE SET enabled = excluded.enabled",
                (server_id, aspect, int(enabled)),
            )
            await db.commit()

    async def toggle_aspect(self, server_id: int, aspect: str) -> bool:
        """Flip an aspect and return its new state."""
        current = await self.is_aspect_enabled(server_id, aspect)
        await self.set_aspect(server_id, aspect, not current)
        return not current


def _row_to_config(row) -> ImageConfig:
    values = {name: row[name] for name in _CONFIG_COLUMNS}
    values["module_enabled"] = bool(values["module_enabled"])
    return ImageConfig(**values)
