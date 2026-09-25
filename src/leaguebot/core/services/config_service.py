"""ConfigService — the league's bot configuration, and the claim on its server.

**The table holds one row, and the claim is a column of it** (issue #247). `server_id` is the
league's server, and NULL while the bot serves none. `/bot pack` clears it with the four
settings and leaves the row, because the row also carries what belongs to the league rather
than the server — test mode and two module flags — and those travel with it. Deleting the
row, as the withdrawn full reset did, would have lost them. So "configured" means "the row
holds a server", never "a row exists", and every read here says so.
"""

from __future__ import annotations

import logging

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.server_config import ServerConfig

log = logging.getLogger(__name__)


#: Frees the claim: the server and the four settings, leaving the row. Shared with
#: `pack_service`, which runs it inside its own transaction.
RELEASE_CLAIM_SQL = (
    "UPDATE server_configs SET server_id = NULL, interaction_role_id = NULL, "
    "interaction_channel_id = NULL, log_channel_id = NULL, league_admin_role_id = NULL"
)


class ConfigService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def get_server_config(self) -> ServerConfig | None:
        """Return the league's ServerConfig, or None while no server is claimed."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT server_id, interaction_role_id, interaction_channel_id, "
                "       log_channel_id, league_admin_role_id, test_mode_active, "
                "       test_mode_nationality_required, "
                "       base_role_id, driver_role_id, hub_channel_id, hub_message_id "
                "FROM server_configs WHERE server_id IS NOT NULL",
            )
            row = await cursor.fetchone()

        if row is None:
            return None
        return ServerConfig(
            server_id=row["server_id"],
            interaction_role_id=row["interaction_role_id"],
            interaction_channel_id=row["interaction_channel_id"],
            log_channel_id=row["log_channel_id"],
            league_admin_role_id=row["league_admin_role_id"],
            test_mode_active=bool(row["test_mode_active"]),
            test_mode_nationality_required=bool(row["test_mode_nationality_required"]),
            base_role_id=row["base_role_id"],
            driver_role_id=row["driver_role_id"],
            hub_channel_id=row["hub_channel_id"],
            hub_message_id=row["hub_message_id"],
        )

    async def get_league_server_id(self) -> int | None:
        """The league's server: the one that holds the configuration row, or None.

        `/bot init` claims it and `/bot pack` frees it; see `leaguebot.core.utils.league_server`, which asks
        this before every command.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT server_id FROM server_configs WHERE server_id IS NOT NULL LIMIT 1"
            )
            row = await cursor.fetchone()
        return None if row is None else int(row["server_id"])

    async def save_server_config(self, cfg: ServerConfig) -> bool:
        """Claim the league's server while no server holds the claim. Returns whether it did.

        Claim-only, and deliberately so. `/bot init` is the sole caller and runs once; every
        later change to one of the three settings goes through the setters
        below, which write a single column each.

        An upsert here used to overwrite `test_mode_active` from whatever the caller's
        `ServerConfig` happened to carry. `/bot init` builds one without reading the stored
        row first, so its `force` path silently switched test mode off while leaving the
        test drivers seated. Refusing to update at all makes that unreachable rather than
        merely corrected.

        **It is also the claim** (issue #244). One bot serves one league, and the league's
        server is the `server_id` of the one row this table holds. A fresh bot has no row,
        and the insert writes one only while none exists; a packed bot has a row whose
        `server_id` is NULL (issue #247), and the update writes only while it still is. Only
        the four settings are written there — test mode and the module flags are the
        league's and came with it. Either way the condition is part of the statement, not a
        read before it, so two servers racing to `/bot init` cannot both win.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                UPDATE server_configs
                SET server_id = ?, interaction_role_id = ?, interaction_channel_id = ?,
                    log_channel_id = ?, league_admin_role_id = ?
                WHERE server_id IS NULL
                """,
                (
                    cfg.server_id,
                    cfg.interaction_role_id,
                    cfg.interaction_channel_id,
                    cfg.log_channel_id,
                    cfg.league_admin_role_id,
                ),
            )
            if cursor.rowcount > 0:
                await db.commit()
                return True
            cursor = await db.execute(
                """
                INSERT INTO server_configs
                    (server_id, interaction_role_id, interaction_channel_id,
                     log_channel_id, league_admin_role_id, test_mode_active,
                     test_mode_nationality_required)
                SELECT ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM server_configs)
                """,
                (
                    cfg.server_id,
                    cfg.interaction_role_id,
                    cfg.interaction_channel_id,
                    cfg.log_channel_id,
                    cfg.league_admin_role_id,
                    int(cfg.test_mode_active),
                    int(cfg.test_mode_nationality_required),
                ),
            )
            await db.commit()
            return cursor.rowcount > 0

    #: The four settings `/bot init` establishes and the four commands beside it repair, the
    #: league's two roles, which `/bot base-role` and `/bot driver-role` set (issue #276), and
    #: the hub's channel and the panel posted there (issue #279).
    #: Named here rather than interpolated from the caller so that no command can reach a
    #: column of its own choosing.
    _SETTABLE_COLUMNS = {
        "interaction_role_id",
        "interaction_channel_id",
        "log_channel_id",
        "league_admin_role_id",
        "base_role_id",
        "driver_role_id",
        "hub_channel_id",
        "hub_message_id",
    }

    async def set_core_setting(self, column: str, value: int | None) -> bool:
        """Write one core-config column, leaving every other column untouched.

        One column at a time is the point: test mode, the module flags, the league's two
        roles and the other settings are each written by their own command, and a whole-row
        save from any of them would carry stale values over the others.

        Returns False where no server is claimed, a packed row included: its settings
        belong to the next server's `/bot init`.
        """
        if column not in self._SETTABLE_COLUMNS:
            raise ValueError(f"{column!r} is not a core setting")

        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                f"UPDATE server_configs SET {column} = ? WHERE server_id IS NOT NULL",
                (value,),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def release_claim(self) -> None:
        """Free the claim on the league's server: clear it and the four settings.

        The row stays, and with it test mode and the module flags; see the module docstring.
        `/bot pack` runs the same statement inside its own transaction.
        """
        async with get_connection(self._db_path) as db:
            await db.execute(RELEASE_CLAIM_SQL)
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
