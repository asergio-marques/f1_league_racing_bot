"""ResetCog — /bot-reset command.

This command is intentionally exempt from channel_guard (chicken-and-egg:
after a full reset the server_configs row is gone, so the configured channel
no longer exists).  It requires MANAGE_GUILD permission instead, matching the
/bot-init pattern.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import reset_service
from utils.channel_guard import admin_only

log = logging.getLogger(__name__)

_CONFIRM_WORD = "CONFIRM"


class ResetCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="bot-reset",
        description=(
            "Reset server data. "
            "Add full:True to also wipe bot configuration."
        ),
    )
    @app_commands.describe(
        confirm=(
            f'Type "{_CONFIRM_WORD}" (case-sensitive) to authorise deletion.'
        ),
        full=(
            "If True, also deletes bot configuration "
            "(you must run /bot-init again afterwards)."
        ),
    )
    @admin_only
    async def handle_bot_reset(
        self,
        interaction: discord.Interaction,
        confirm: str,
        full: bool = False,
    ) -> None:
        """Purge all season data for this server (optionally including bot config)."""
        # ── confirmation gate ─────────────────────────────────────────────────
        if confirm != _CONFIRM_WORD:
            await interaction.response.send_message(
                f"❌ Reset aborted. "
                f"You must pass `confirm:{_CONFIRM_WORD}` (case-sensitive) to proceed.",
                ephemeral=True,
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]

        # ── defer so we can safely await the service ──────────────────────────
        await interaction.response.defer(ephemeral=True)

        try:
            result = await reset_service.reset_server_data(
                server_id=server_id,
                db_path=self.bot.db_path,  # type: ignore[attr-defined]
                scheduler_service=self.bot.scheduler_service,  # type: ignore[attr-defined]
                full=full,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("Reset failed for server %s", server_id)
            await interaction.followup.send(
                f"❌ Reset failed unexpectedly: {exc}",
                ephemeral=True,
            )
            return

        # Cancel any pending season-end scheduled job
        self.bot.scheduler_service.cancel_season_end(server_id)  # type: ignore[attr-defined]

        # Clear any in-memory pending season setups for this server
        season_cog = self.bot.get_cog("SeasonCog")
        if season_cog is not None:
            season_cog.clear_pending_for_server(server_id)

        seasons = result["seasons_deleted"]
        divisions = result["divisions_deleted"]
        rounds = result["rounds_deleted"]

        if full:
            footer = "Server config removed — run `/bot-init` to re-configure."
        else:
            footer = "Server config preserved — bot remains active in this channel."

        mode_label = "fully reset" if full else "reset"
        await interaction.followup.send(
            f"✅ Server data {mode_label}.\n"
            f"Deleted: **{seasons}** season(s), **{divisions}** division(s), "
            f"**{rounds}** round(s).\n"
            f"{footer}",
            ephemeral=True,
        )
        log.info(
            "/bot-reset by %s on server %s: %d season(s), %d division(s), "
            "%d round(s) deleted (full=%s)",
            interaction.user,
            server_id,
            seasons,
            divisions,
            rounds,
            full,
        )
