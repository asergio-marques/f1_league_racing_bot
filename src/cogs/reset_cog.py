"""ResetCog — /bot-reset command.

A league admin's command, given in the interaction channel like every other.

It used to be exempt from the channel rule on the reasoning that a full reset deletes the
`server_configs` row, so the configured channel no longer exists once it has run. That is
true and beside the point: the guard reads the configuration *before* the command runs, and
the row is still there at that moment. What the exemption actually bought was a destructive
wipe of a league's entire history reachable from any channel on the server.

It does not share `/bot-init`'s footing either. The setup commands run from anywhere because
they *repair* the settings the guards read; a reset destroys them, which is the opposite
errand. A league that has reset itself runs `/bot-init` again, and that command is exempt.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import reset_service
from utils.channel_guard import league_admin_only

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
    @league_admin_only
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
        log_mode = "Full reset (config deleted)" if full else "Partial reset (config preserved)"
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot-reset | Success\n"
            f"  mode: {log_mode}\n"
            f"  deleted: {seasons} season(s), {divisions} division(s), {rounds} round(s)",
        )
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
