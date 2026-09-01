"""ImageConfigService — CRUD over image_config and image_aspect_toggles.

Configuration retention (FR-004a): disabling the module clears only ``module_enabled``.
Nothing in this service deletes a configuration row or resets a toggle, which is the
Principle X.6 exception for configuration that cannot go stale — no value here names a
Discord channel, role, message or scheduled job.
"""
from __future__ import annotations

import dataclasses
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
    | {"pfp_daily_time"}
)

#: The three portrait toggles. Booleans, so they are written through `set_pfp_flag` rather
#: than the string-valued `set_field`, and are deliberately absent from SETTABLE_COLUMNS.
PFP_FLAG_COLUMNS: frozenset[str] = frozenset({"use_pfp", "pfp_prerender", "pfp_daily"})

#: Ordered column list used to build an ImageConfig from a row.
_CONFIG_COLUMNS: tuple[str, ...] = (
    ("server_id", "module_enabled", "template_directory")
    + tuple(TEMPLATE_COLUMNS)
    + tuple(ASSET_DIRECTORIES)
    + ("use_pfp", "pfp_prerender", "pfp_daily", "pfp_daily_time")
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

    async def candidate_config(
        self, server_id: int, column: str, value: str
    ) -> ImageConfig | None:
        """The stored configuration with *column* overridden — **not** persisted.

        This is what makes validate-then-store possible (FR-005). The validity engine
        already takes an ``ImageConfig``, so evaluating a copy reuses it exactly and no
        second code path can drift from what ``/images config view`` reports.

        Writing first and rolling back on failure was rejected: a concurrent read could
        observe the bad value, and a crash between write and rollback would leave it
        stored — the outcome FR-005 exists to prevent.
        """
        if column not in SETTABLE_COLUMNS:
            raise UnknownConfigField(f"`{column}` is not a settable image config field.")

        current = await self.get_config(server_id)
        if current is None:
            return None
        return dataclasses.replace(current, **{column: value})

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

    async def set_pfp_flag(self, server_id: int, column: str, enabled: bool) -> None:
        """Write one of the three portrait toggles.

        Separate from `set_field` because these are booleans and that setter is string-valued.
        The at-least-one rule between `pfp_prerender` and `pfp_daily` is **not** enforced here:
        it is a rule about a command being refused, and lives in `pfp_change_refusal` so that
        the caller can state the refusal rather than catching an exception to do it.
        """
        if column not in PFP_FLAG_COLUMNS:
            raise UnknownConfigField(column)
        async with get_connection(self._db_path) as db:
            await db.execute(
                f"UPDATE image_config SET {column} = ? WHERE server_id = ?",
                (1 if enabled else 0, server_id),
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


def pfp_change_refusal(config: ImageConfig, column: str, enabled: bool) -> str | None:
    """Why this change to a portrait toggle must be refused, or None where it may proceed.

    Where portraits are enabled at all, at least one of the two update triggers must be
    enabled too. A configuration with neither obtains a portrait at no moment whatever, which
    is precisely what disabling `use_pfp` already provides, so the module does not hold it: a
    change that would leave neither is refused and the configuration left as it stood.

    Pure and total, taking the configuration as it stands and the change proposed. The rule is
    therefore stated once and tested without a database, rather than restated in each of the
    three commands that must honour it.

    Only a change that *reduces* cover is ever refused. Enabling anything is always allowed,
    and disabling `use_pfp` is always allowed -- turning the feature off wholesale is the very
    thing the invalid configuration was an awkward spelling of.
    """
    if enabled:
        return None

    if column == "use_pfp":
        return None

    other = "pfp_daily" if column == "pfp_prerender" else "pfp_prerender"
    if not config.use_pfp or getattr(config, other):
        return None

    names = {
        "pfp_prerender": ("pre-render updates", "daily updates"),
        "pfp_daily": ("daily updates", "pre-render updates"),
    }
    this_one, other_one = names[column]
    return (
        f"Cannot disable {this_one}: {other_one} are off, so this is the only way "
        f"profile pictures would ever be fetched. Enable {other_one} first, or turn "
        f"off `/images use-pfp toggle` entirely."
    )


def _row_to_config(row) -> ImageConfig:
    values = {name: row[name] for name in _CONFIG_COLUMNS}
    values["module_enabled"] = bool(values["module_enabled"])
    for name in ("use_pfp", "pfp_prerender", "pfp_daily"):
        values[name] = bool(values[name])
    return ImageConfig(**values)
