"""AmendmentCog — /round-amend command.

Allows trusted admins to amend round track, date/time, or format
with atomic invalidation of prior weather phases.
"""

from __future__ import annotations

import logging
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from models.round import RoundFormat
from models.track import TRACKS
from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)


class AmendmentCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="round-amend",
        description="Amend a round's configuration (admin only). Invalidates prior weather phases.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to amend",
        track="New track name (leave blank to keep current)",
        scheduled_at="New race datetime in ISO format YYYY-MM-DDTHH:MM:SS (leave blank to keep current)",
        format="New format: NORMAL, SPRINT, MYSTERY, or ENDURANCE (leave blank to keep current)",
    )
    @channel_guard
    @admin_only
    async def round_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        track: str = "",
        scheduled_at: str = "",
        format: str = "",
    ) -> None:
        if not any([track, scheduled_at, format]):
            await interaction.response.send_message(
                "❌ Provide at least one field to amend: `track`, `scheduled_at`, or `format`.",
                ephemeral=True,
            )
            return

        # Resolve round
        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "❌ No active season found.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"❌ Division `{division_name}` not found.",
                ephemeral=True,
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.response.send_message(
                f"❌ Round {round_number} not found in division `{division_name}`.",
                ephemeral=True,
            )
            return

        # Validate fields before showing confirm dialog
        amendments: list[tuple[str, object]] = []

        if track:
            if track not in TRACKS:
                await interaction.response.send_message(
                    f"❌ Unknown track `{track}`. Valid: {', '.join(sorted(TRACKS))}",
                    ephemeral=True,
                )
                return
            amendments.append(("track_name", track))

        if scheduled_at:
            try:
                new_dt = datetime.fromisoformat(scheduled_at)
            except ValueError:
                await interaction.response.send_message(
                    "❌ Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
                    ephemeral=True,
                )
                return
            amendments.append(("scheduled_at", new_dt))

        if format:
            try:
                new_fmt = RoundFormat(format.upper())
            except ValueError:
                await interaction.response.send_message(
                    f"❌ Invalid format `{format}`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
                    ephemeral=True,
                )
                return
            amendments.append(("format", new_fmt))

        # Confirmation view
        summary_lines = [
            f"**Amend Round {rnd.round_number}** in division **{div.name}**:",
        ]
        for f_name, f_val in amendments:
            summary_lines.append(f"  • `{f_name}` → `{f_val}`")
        summary_lines.append("\n⚠️ This will invalidate all prior weather phases for this round.")

        view = _ConfirmView(
            cog=self,
            interaction_user_id=interaction.user.id,
            round_id=rnd.id,
            amendments=amendments,
        )
        await interaction.response.send_message(
            "\n".join(summary_lines),
            view=view,
            ephemeral=True,
        )


class _ConfirmView(discord.ui.View):
    def __init__(
        self,
        cog: AmendmentCog,
        interaction_user_id: int,
        round_id: int,
        amendments: list[tuple[str, object]],
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._user_id = interaction_user_id
        self._round_id = round_id
        self._amendments = amendments

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("⛔ Not your action.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        errors: list[str] = []
        for field_name, new_value in self._amendments:
            try:
                await self._cog.bot.amendment_service.amend_round(
                    self._round_id,
                    interaction.user,
                    field_name,
                    new_value,
                    self._cog.bot,
                )
            except Exception as exc:
                log.exception("Amendment failed for %s: %s", field_name, exc)
                errors.append(f"`{field_name}`: {exc}")

        if errors:
            await interaction.followup.send(
                f"⚠️ Some amendments failed:\n" + "\n".join(errors),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"✅ Round amended successfully.",
                ephemeral=True,
            )
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message("Amendment cancelled.", ephemeral=True)
        self.stop()
