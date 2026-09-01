"""ConfigService — per-server bot configuration CRUD."""

from __future__ import annotations

import logging

import discord

from db.database import get_connection
from models.server_config import ServerConfig

log = logging.getLogger(__name__)


class ConfigService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def get_server_config(self, server_id: int) -> ServerConfig | None:
        """Return the ServerConfig for *server_id*, or None if not configured."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT server_id, interaction_role_id, interaction_channel_id, "
                "       log_channel_id, test_mode_active, test_mode_nationality_required, "
                "       weather_module_enabled, signup_module_enabled "
                "FROM server_configs WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return ServerConfig(
            server_id=row["server_id"],
            interaction_role_id=row["interaction_role_id"],
            interaction_channel_id=row["interaction_channel_id"],
            log_channel_id=row["log_channel_id"],
            test_mode_active=bool(row["test_mode_active"]),
            test_mode_nationality_required=bool(row["test_mode_nationality_required"]),
            weather_module_enabled=bool(row["weather_module_enabled"]),
            signup_module_enabled=bool(row["signup_module_enabled"]),
        )

    async def save_server_config(self, cfg: ServerConfig) -> bool:
        """Create the ServerConfig row for a server that has none. Returns whether it did.

        Insert-only, and deliberately so. `/bot-init` is the sole caller and runs once per
        server; every later change to one of the three settings goes through the setters
        below, which write a single column each.

        An upsert here used to overwrite `test_mode_active` from whatever the caller's
        `ServerConfig` happened to carry. `/bot-init` builds one without reading the stored
        row first, so its `force` path silently switched test mode off while leaving the
        test drivers seated. Refusing to update at all makes that unreachable rather than
        merely corrected, which is why the conflict clause does nothing.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO server_configs
                    (server_id, interaction_role_id, interaction_channel_id,
                     log_channel_id, test_mode_active, test_mode_nationality_required,
                     weather_module_enabled, signup_module_enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO NOTHING
                """,
                (
                    cfg.server_id,
                    cfg.interaction_role_id,
                    cfg.interaction_channel_id,
                    cfg.log_channel_id,
                    int(cfg.test_mode_active),
                    int(cfg.test_mode_nationality_required),
                    int(cfg.weather_module_enabled),
                    int(cfg.signup_module_enabled),
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    #: The three settings `/bot-init` establishes and the three commands beside it repair.
    #: Named here rather than interpolated from the caller so that no command can reach a
    #: column of its own choosing.
    _SETTABLE_COLUMNS = {
        "interaction_role_id",
        "interaction_channel_id",
        "log_channel_id",
    }

    async def set_core_setting(self, server_id: int, column: str, value: int) -> bool:
        """Write one core-config column, leaving every other column untouched.

        One column at a time is the point: test mode, the module flags and the other two
        settings are each written by their own command, and a whole-row save from any of
        them would carry stale values over the others.

        Returns False where the server has no configuration row to amend.
        """
        if column not in self._SETTABLE_COLUMNS:
            raise ValueError(f"{column!r} is not a core setting")

        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                f"UPDATE server_configs SET {column} = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Validation helpers (require a live guild object)
    # ------------------------------------------------------------------

    @staticmethod
    def validate_role(guild: discord.Guild, role_id: int) -> discord.Role:
        """Return the Role object; raise ValueError if not found."""
        role = guild.get_role(role_id)
        if role is None:
            raise ValueError(f"Role id={role_id} not found in guild {guild.id}")
        return role

    @staticmethod
    def validate_channel(guild: discord.Guild, channel_id: int) -> discord.TextChannel:
        """Return the TextChannel; raise ValueError if not found or wrong type."""
        channel = guild.get_channel(channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            raise ValueError(
                f"Text channel id={channel_id} not found in guild {guild.id}"
            )
        return channel
