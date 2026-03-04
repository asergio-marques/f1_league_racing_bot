"""SeasonCog — /season commands.

Commands:
  /season setup   — start interactive season configuration
  /season review  — view pending config with Approve/Amend actions
  /season approve — commit the pending config to the database
  /season status  — read-only summary of active season
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from models.round import RoundFormat
from models.track import TRACKS, TRACK_IDS
from utils.channel_guard import channel_guard, admin_only

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory pending config store
# ---------------------------------------------------------------------------

@dataclass
class PendingDivision:
    name: str = ""
    role_id: int = 0
    channel_id: int = 0
    rounds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PendingConfig:
    server_id: int = 0
    start_date: date = field(default_factory=date.today)
    divisions: list[PendingDivision] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class SeasonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Keyed by user_id → PendingConfig
        self._pending: dict[int, PendingConfig] = {}

    # ------------------------------------------------------------------
    # /season setup
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-setup",
        description="Start interactive season configuration (admin only).",
    )
    @app_commands.describe(
        start_date="Season start date (YYYY-MM-DD)",
        num_divisions="Number of divisions (1–10)",
    )
    @channel_guard
    @admin_only
    async def season_setup(
        self,
        interaction: discord.Interaction,
        start_date: str,
        num_divisions: int,
    ) -> None:
        """Begin the season setup wizard. Produces a modal-like step-by-step flow."""
        try:
            parsed_date = date.fromisoformat(start_date)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Please use `YYYY-MM-DD`.",
                ephemeral=True,
            )
            return

        if not (1 <= num_divisions <= 10):
            await interaction.response.send_message(
                "❌ Number of divisions must be between 1 and 10.",
                ephemeral=True,
            )
            return

        server_id = interaction.guild_id

        # Guard: reject if a DB season already exists for this server
        if await self.bot.season_service.has_existing_season(server_id):
            await interaction.response.send_message(
                "❌ A season already exists for this server (active or pending "
                "approval). Use `/bot-reset` to clear it first.",
                ephemeral=True,
            )
            return

        # Guard: reject if any user already has a pending in-memory setup
        if any(p.server_id == server_id for p in self._pending.values()):
            await interaction.response.send_message(
                "❌ A season setup is already in progress for this server. "
                "Use `/season-review` to approve or cancel it first.",
                ephemeral=True,
            )
            return

        cfg = PendingConfig(
            server_id=server_id,
            start_date=parsed_date,
            divisions=[PendingDivision() for _ in range(num_divisions)],
        )
        self._pending[interaction.user.id] = cfg

        await interaction.response.send_message(
            f"✅ Season setup started.\n"
            f"**Start date**: {parsed_date}\n"
            f"**Divisions**: {num_divisions}\n\n"
            f"Use `/division-add` for each division, then `/round-add` for each round.\n"
            f"When done, run `/season-review` to review and approve.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /division-add  (part of the setup flow)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="division-add",
        description="Add a division to the pending season setup.",
    )
    @app_commands.describe(
        name="Division name",
        role="The Discord role to mention for this division",
        forecast_channel="Channel where weather forecasts are posted",
    )
    @channel_guard
    @admin_only
    async def division_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
        forecast_channel: discord.TextChannel,
    ) -> None:
        cfg = self._pending.get(interaction.user.id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup. Run `/season-setup` first.",
                ephemeral=True,
            )
            return

        div = PendingDivision(
            name=name,
            role_id=role.id,
            channel_id=forecast_channel.id,
        )

        # Replace first empty division or append
        empty = [d for d in cfg.divisions if not d.name]
        if empty:
            idx = cfg.divisions.index(empty[0])
            cfg.divisions[idx] = div
        else:
            cfg.divisions.append(div)

        await interaction.response.send_message(
            f"✅ Division **{name}** added.\n"
            f"Role: {role.mention} | Channel: {forecast_channel.mention}",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /round-add  (part of the setup flow)
    # ------------------------------------------------------------------

    @app_commands.command(
        name="round-add",
        description="Add a round to a division in the pending season setup.",
    )
    @app_commands.describe(
        division_name="Name of the division this round belongs to",
        round_number="Round number",
        format="Round format (NORMAL, SPRINT, MYSTERY, ENDURANCE)",
        track="Track ID or name (e.g. 27 or United Kingdom). Leave blank for Mystery rounds.",
        scheduled_at="Race date/time in ISO format (YYYY-MM-DDTHH:MM:SS UTC)",
    )
    @channel_guard
    @admin_only
    async def round_add(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        format: str,
        scheduled_at: str,
        track: str = "",
    ) -> None:
        cfg = self._pending.get(interaction.user.id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup. Run `/season-setup` first.",
                ephemeral=True,
            )
            return

        try:
            fmt = RoundFormat(format.upper())
        except ValueError:
            await interaction.response.send_message(
                f"❌ Invalid format `{format}`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.",
                ephemeral=True,
            )
            return

        track_name = track.strip() or None

        # Non-Mystery rounds must have a track specified
        if fmt != RoundFormat.MYSTERY and not track_name:
            await interaction.response.send_message(
                f"❌ A track is required for `{fmt.value}` rounds. "
                "Leave track blank only for `MYSTERY` rounds.",
                ephemeral=True,
            )
            return

        if track_name and track_name not in TRACKS:
            # Allow lookup by numeric ID (e.g. "27" → "United Kingdom")
            track_name = TRACK_IDS.get(track_name.zfill(2), track_name)
        if track_name and track_name not in TRACKS:
            await interaction.response.send_message(
                f"\u274c Unknown track `{track_name}`.\n"
                f"Use `/round-add` and type a number or name — autocomplete will guide you.",
                ephemeral=True,
            )
            return

        try:
            sched = datetime.fromisoformat(scheduled_at)
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`",
                ephemeral=True,
            )
            return

        div = next((d for d in cfg.divisions if d.name == division_name), None)
        if div is None:
            await interaction.response.send_message(
                f"❌ Division `{division_name}` not found in pending setup.",
                ephemeral=True,
            )
            return

        div.rounds.append({
            "round_number": round_number,
            "format": fmt,
            "track_name": track_name,
            "scheduled_at": sched,
        })

        await interaction.response.send_message(
            f"✅ Round {round_number} added to **{division_name}**.\n"
            f"Format: {fmt.value} | Track: {track_name or 'Mystery'} | {sched.isoformat()} UTC",
            ephemeral=True,
        )

    @round_add.autocomplete("track")
    async def round_add_track_autocomplete(
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

    # ------------------------------------------------------------------
    # /season-review
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-review",
        description="Review pending season configuration before approving.",
    )
    @channel_guard
    @admin_only
    async def season_review(self, interaction: discord.Interaction) -> None:
        cfg = self._pending.get(interaction.user.id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup. Run `/season-setup` first.",
                ephemeral=True,
            )
            return

        lines = [
            f"**Season Review**",
            f"Start date: {cfg.start_date}",
            f"Server: {interaction.guild_id}",
            "",
        ]
        for div in cfg.divisions:
            if not div.name:
                continue
            lines.append(
                f"\ud83d\udcc2 **{div.name}** | "
                f"Role <@&{div.role_id}> | "
                f"Channel <#{div.channel_id}>"
            )
            for r in div.rounds:
                lines.append(
                    f"  Round {r['round_number']}: {r['format'].value} "
                    f"@ {r['track_name'] or 'Mystery'} — {r['scheduled_at'].isoformat()}"
                )
            lines.append("")

        content = "\n".join(lines)
        view = _ApproveView(self)
        await interaction.response.send_message(content, view=view, ephemeral=True)

    # ------------------------------------------------------------------
    # /season-approve
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-approve",
        description="Commit the pending season configuration to the bot.",
    )
    @channel_guard
    @admin_only
    async def season_approve(self, interaction: discord.Interaction) -> None:
        await self._do_approve(interaction)

    def clear_pending_for_server(self, server_id: int) -> None:
        """Discard any in-memory pending setup belonging to *server_id*.

        Called by ResetCog after a server reset so stale in-memory state
        does not block subsequent /season-setup invocations.
        """
        stale_keys = [
            uid for uid, cfg in self._pending.items()
            if cfg.server_id == server_id
        ]
        for uid in stale_keys:
            del self._pending[uid]
        if stale_keys:
            log.info(
                "Cleared %d pending season setup(s) for server %s",
                len(stale_keys), server_id,
            )

    async def _do_approve(self, interaction: discord.Interaction) -> None:
        cfg = self._pending.get(interaction.user.id)
        if cfg is None:
            await interaction.response.send_message(
                "❌ No pending season setup.",
                ephemeral=True,
            )
            return

        season_svc = self.bot.season_service
        season = await season_svc.create_season(cfg.server_id, cfg.start_date)

        all_rounds = []
        for div_cfg in cfg.divisions:
            if not div_cfg.name:
                continue
            div = await season_svc.add_division(
                season.id,
                div_cfg.name,
                div_cfg.role_id,
                div_cfg.channel_id,
            )
            for r in div_cfg.rounds:
                rnd = await season_svc.add_round(
                    div.id,
                    r["round_number"],
                    r["format"],
                    r["track_name"],
                    r["scheduled_at"],
                )
                await season_svc.create_sessions_for_round(rnd.id, r["format"])
                all_rounds.append(rnd)

        await season_svc.transition_to_active(season.id)
        self.bot.scheduler_service.schedule_all_rounds(all_rounds)

        del self._pending[interaction.user.id]

        msg = (
            f"✅ **Season approved and activated!**\n"
            f"Season ID: {season.id} | "
            f"Rounds scheduled: {len(all_rounds)}"
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

        log.info("Season %s activated for server %s by %s", season.id, cfg.server_id, interaction.user)

    # ------------------------------------------------------------------
    # /season-status
    # ------------------------------------------------------------------

    @app_commands.command(
        name="season-status",
        description="View a summary of the active season.",
    )
    @channel_guard
    async def season_status(self, interaction: discord.Interaction) -> None:
        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "ℹ️ No active season found for this server.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        lines = [
            f"**Active Season** (ID: {season.id})",
            f"Start date: {season.start_date}",
            f"Divisions: {len(divisions)}",
            "",
        ]
        for div in divisions:
            rounds = await self.bot.season_service.get_division_rounds(div.id)
            next_round = next(
                (r for r in rounds if not (r.phase1_done and r.phase2_done and r.phase3_done)),
                None,
            )
            lines.append(
                f"📂 **{div.name}** — "
                f"Next round: "
                + (f"R{next_round.round_number} @ {next_round.track_name or 'Mystery'} "
                   f"({next_round.scheduled_at.isoformat()})" if next_round else "None remaining")
            )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)


# ---------------------------------------------------------------------------
# Approve button view
# ---------------------------------------------------------------------------

class _ApproveView(discord.ui.View):
    def __init__(self, cog: SeasonCog) -> None:
        super().__init__(timeout=300)
        self._cog = cog

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._cog._do_approve(interaction)
        self.stop()

    @discord.ui.button(label="✏️ Go Back to Edit", style=discord.ButtonStyle.secondary)
    async def amend(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Use `/division-add` or `/round-add` to make changes, then `/season-review` again.",
            ephemeral=True,
        )
        self.stop()
