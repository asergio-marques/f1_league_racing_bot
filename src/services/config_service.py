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
                "SELECT server_id, interaction_role_id, interaction_channel_id, log_channel_id "
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
        )

    async def save_server_config(self, cfg: ServerConfig) -> None:
        """Insert or replace the ServerConfig row."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO server_configs
                    (server_id, interaction_role_id, interaction_channel_id, log_channel_id)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    interaction_role_id    = excluded.interaction_role_id,
                    interaction_channel_id = excluded.interaction_channel_id,
                    log_channel_id         = excluded.log_channel_id
                """,
                (
                    cfg.server_id,
                    cfg.interaction_role_id,
                    cfg.interaction_channel_id,
                    cfg.log_channel_id,
                ),
            )
            await db.commit()

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
