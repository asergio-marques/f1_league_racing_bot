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
from models.track import TRACKS, TRACK_IDS
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
        track="New track ID or name (leave blank to keep current)",
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

        # ------------------------------------------------------------------
        # Pending-config path: check in-memory setup before the DB
        # ------------------------------------------------------------------
        season_cog = self.bot.get_cog("SeasonCog")
        pending_cfg = (
            season_cog._get_pending_for_server(interaction.guild_id)
            if season_cog is not None
            else None
        )
        if pending_cfg is not None:
            # Locate the division and round in the in-memory config
            pend_div = next(
                (d for d in pending_cfg.divisions if d.name.lower() == division_name.lower()),
                None,
            )
            if pend_div is None:
                await interaction.response.send_message(
                    f"❌ Division `{division_name}` not found in pending setup.",
                    ephemeral=True,
                )
                return

            pend_rnd = next(
                (r for r in pend_div.rounds if r["round_number"] == round_number),
                None,
            )
            if pend_rnd is None:
                await interaction.response.send_message(
                    f"❌ Round {round_number} not found in division `{division_name}` of the pending setup.",
                    ephemeral=True,
                )
                return

            # Validate and resolve track
            new_track: str | None = ...
            if track:
                resolved = TRACK_IDS.get(track.zfill(2), track)
                if resolved not in TRACKS:
                    await interaction.response.send_message(
                        f"❌ Unknown track `{track}`. Use autocomplete to pick a valid track.",
                        ephemeral=True,
                    )
                    return
                new_track = resolved
            else:
                new_track = ...  # sentinel — not supplied

            # Validate scheduled_at
            new_dt = ...
            if scheduled_at:
                try:
                    new_dt = datetime.fromisoformat(scheduled_at)
                except ValueError:
                    await interaction.response.send_message(
                        "❌ Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
                        ephemeral=True,
                    )
                    return

            # Validate format
            new_fmt = ...
            if format:
                try:
                    new_fmt = RoundFormat(format.upper())
                except ValueError:
                    await interaction.response.send_message(
                        f"❌ Invalid format `{format}`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
                        ephemeral=True,
                    )
                    return

            # Determine effective format after amendment
            effective_fmt = new_fmt if new_fmt is not ... else pend_rnd["format"]
            # Determine effective track for MYSTERY validation
            effective_track = new_track if new_track is not ... else pend_rnd["track_name"]

            if effective_fmt != RoundFormat.MYSTERY and not effective_track:
                await interaction.response.send_message(
                    f"❌ Format `{effective_fmt.value}` requires a track. "
                    "Supply a `track` value or change format to MYSTERY.",
                    ephemeral=True,
                )
                return

            # Apply changes in-memory — no DB write, no phase-invalidation
            if new_fmt is not ...:
                pend_rnd["format"] = new_fmt
            if new_dt is not ...:
                pend_rnd["scheduled_at"] = new_dt
            if new_track is not ...:
                pend_rnd["track_name"] = new_track
            # MYSTERY clears the track regardless of what was supplied
            if pend_rnd["format"] == RoundFormat.MYSTERY:
                pend_rnd["track_name"] = None

            # Persist the updated in-memory setup to DB
            new_sid = await self.bot.season_service.save_pending_snapshot(
                server_id=pending_cfg.server_id,
                start_date=pending_cfg.start_date,
                existing_season_id=pending_cfg.season_id,
                divisions=[
                    {
                        "name": d.name,
                        "role_id": d.role_id,
                        "channel_id": d.channel_id,
                        "rounds": d.rounds,
                    }
                    for d in pending_cfg.divisions
                    if d.name
                ],
            )
            pending_cfg.season_id = new_sid

            await interaction.response.send_message(
                f"✅ Round {round_number} in **{pend_div.name}** updated in pending setup "
                f"(no DB write — use `/season-approve` to commit).",
                ephemeral=True,
            )
            return

        # ------------------------------------------------------------------
        # Active-season DB path (unchanged)
        # ------------------------------------------------------------------

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
            # Allow lookup by numeric ID (e.g. "27" → "United Kingdom")
            resolved = TRACK_IDS.get(track.zfill(2), track)
            if resolved not in TRACKS:
                await interaction.response.send_message(
                    f"\u274c Unknown track `{track}`. Use `/round-amend` and let autocomplete guide you.",
                    ephemeral=True,
                )
                return
            amendments.append(("track_name", resolved))

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

    @round_amend.autocomplete("track")
    async def round_amend_track_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        results: list[app_commands.Choice[str]] = []
        for id_str, name in TRACK_IDS.items():
            label = f"{id_str} \u2013 {name}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=name))
        return results[:25]


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
