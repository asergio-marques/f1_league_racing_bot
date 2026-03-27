"""TrackCog — /track command group.

Subcommands:
  /track config track mu sigma  — set server-level override for a track's Beta parameters
  /track reset  track           — revert a track's override to the bot-packaged default
  /track info   track           — show effective (mu, sigma) and their source

All subcommands require channel_guard (interaction role + channel).
config and reset additionally require admin_only (Manage Server permission).
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from models.track import TRACK_DEFAULTS, TRACK_IDS, get_default_rpc_params
from services.track_service import get_track_override, set_track_override, reset_track_override
from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)


class TrackCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Command group
    # ------------------------------------------------------------------

    track = app_commands.Group(
        name="track",
        description="Track Beta distribution parameter commands",
    )

    # ------------------------------------------------------------------
    # Autocomplete helper
    # ------------------------------------------------------------------

    async def _autocomplete_track(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Suggest track names from TRACK_IDS for autocomplete."""
        results: list[app_commands.Choice[str]] = []
        current_lower = current.lower()
        for track_id, name in TRACK_IDS.items():
            if current_lower in name.lower() or current_lower in track_id:
                results.append(app_commands.Choice(name=f"{track_id} — {name}", value=name))
        return results[:25]

    # ------------------------------------------------------------------
    # /track config
    # ------------------------------------------------------------------

    @track.command(
        name="config",
        description="Set server-level μ (mean rain %) and σ (dispersion) for a track. Admin only.",
    )
    @app_commands.describe(
        track="Track name or ID",
        mu="Mean rain probability (0.0 – 1.0 exclusive, e.g. 0.30 for 30%)",
        sigma="Dispersion / standard deviation (must be > 0)",
    )
    @app_commands.autocomplete(track=_autocomplete_track)
    @channel_guard
    @admin_only
    async def config(
        self,
        interaction: discord.Interaction,
        track: str,
        mu: float,
        sigma: float,
    ) -> None:
        # Resolve track name from ID or exact name
        resolved = _resolve_track(track)
        if resolved is None:
            await interaction.response.send_message(
                f"❌ Unknown track `{track}`. Use a valid track name or ID.",
                ephemeral=True,
            )
            return

        try:
            async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
                await set_track_override(
                    db,
                    server_id=interaction.guild_id,  # type: ignore[arg-type]
                    track_name=resolved,
                    mu=mu,
                    sigma=sigma,
                    actor_id=interaction.user.id,
                    actor_name=str(interaction.user),
                )
                await db.commit()
        except ValueError as exc:
            await interaction.response.send_message(
                f"❌ Validation error: {exc}",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ **{resolved}** updated — μ = `{mu}`, σ = `{sigma}`. "
            "Applies to future Phase 1 draws; existing results are unchanged.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /track set | Success\n"
            f"  track: {resolved}\n"
            f"  mu: {mu}, sigma: {sigma}",
        )
    # ------------------------------------------------------------------
    # /track reset
    # ------------------------------------------------------------------

    @track.command(
        name="reset",
        description="Reset a track's override to the bot-packaged default. Admin only.",
    )
    @app_commands.describe(track="Track name or ID to reset")
    @app_commands.autocomplete(track=_autocomplete_track)
    @channel_guard
    @admin_only
    async def reset(
        self,
        interaction: discord.Interaction,
        track: str,
    ) -> None:
        resolved = _resolve_track(track)
        if resolved is None:
            await interaction.response.send_message(
                f"❌ Unknown track `{track}`.",
                ephemeral=True,
            )
            return

        async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
            old = await reset_track_override(
                db,
                server_id=interaction.guild_id,  # type: ignore[arg-type]
                track_name=resolved,
                actor_id=interaction.user.id,
                actor_name=str(interaction.user),
            )
            await db.commit()

        if old is None:
            default_mu, default_sigma = get_default_rpc_params(resolved)
            await interaction.response.send_message(
                f"ℹ️ **{resolved}** had no server override — already using packaged defaults "
                f"(μ = `{default_mu}`, σ = `{default_sigma}`).",
                ephemeral=True,
            )
        else:
            default_mu, default_sigma = get_default_rpc_params(resolved)
            await interaction.response.send_message(
                f"✅ **{resolved}** reset to packaged defaults — "
                f"μ = `{default_mu}`, σ = `{default_sigma}` "
                f"(was μ = `{old[0]}`, σ = `{old[1]}`).",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /track reset | Success\n"
                f"  track: {resolved}\n"
                f"  old_mu: {old[0]}, old_sigma: {old[1]}\n"
                f"  new_mu: {default_mu}, new_sigma: {default_sigma}",
            )
    # ------------------------------------------------------------------
    # /track info
    # ------------------------------------------------------------------

    @track.command(
        name="info",
        description="Show the effective μ and σ for a track (override or packaged default).",
    )
    @app_commands.describe(track="Track name or ID")
    @app_commands.autocomplete(track=_autocomplete_track)
    @channel_guard
    async def info(
        self,
        interaction: discord.Interaction,
        track: str,
    ) -> None:
        resolved = _resolve_track(track)
        if resolved is None:
            await interaction.response.send_message(
                f"❌ Unknown track `{track}`.",
                ephemeral=True,
            )
            return

        async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
            override = await get_track_override(db, resolved)

        if override is not None:
            mu, sigma = override
            source = "⚙️ Server override"
            # Fetch updated_at / updated_by from DB
            async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
                cursor = await db.execute(
                    "SELECT updated_at, updated_by FROM track_rpc_params WHERE track_name = ?",
                    (resolved,),
                )
                meta = await cursor.fetchone()
            footer = (
                f"Set by **{meta['updated_by']}** at `{meta['updated_at']}`"
                if meta else ""
            )
        else:
            try:
                mu, sigma = get_default_rpc_params(resolved)
            except ValueError:
                await interaction.response.send_message(
                    f"⚠️ **{resolved}** has no packaged default and no server override. "
                    "Use `/track config` to set parameters before Phase 1 can fire.",
                    ephemeral=True,
                )
                return
            source = "📦 Bot-packaged default"
            footer = ""

        lines = [
            f"**{resolved}** — {source}",
            f"μ (mean rain %): `{mu:.4f}` ({mu * 100:.1f}%)",
            f"σ (dispersion):   `{sigma:.4f}` ({sigma * 100:.1f}%)",
        ]
        if footer:
            lines.append(footer)

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_track(value: str) -> str | None:
    """Resolve *value* to a canonical track name.

    Accepts a two-digit ID (e.g. "07") or an exact canonical name (e.g. "Belgium").
    Returns None for unknown inputs.
    """
    if value in TRACK_DEFAULTS:
        return value
    if value in TRACK_IDS:
        return TRACK_IDS[value]
    # Partial case-insensitive match on name
    value_lower = value.lower()
    for name in TRACK_DEFAULTS:
        if name.lower() == value_lower:
            return name
    return None
