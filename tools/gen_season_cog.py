# -*- coding: utf-8 -*-
"""Generator script for the new season_cog.py (010-command-streamlining)."""
import os, pathlib

TARGET = pathlib.Path(__file__).parent.parent / "src" / "cogs" / "season_cog.py"

CONTENT = """\
\"\"\"SeasonCog \u2014 /season, /division, /round command groups.

Commands:
  /season setup    \u2014 start season configuration (admin only)
  /season review   \u2014 view pending config with Approve/Amend actions
  /season approve  \u2014 commit the pending config to the database
  /season status   \u2014 read-only summary of active season
  /season cancel   \u2014 delete the active season (admin only, destructive)

  /division add       \u2014 add a division to pending setup
  /division duplicate \u2014 copy a division with datetime offset (setup only)
  /division delete    \u2014 remove a division from pending setup
  /division rename    \u2014 rename a division (setup only)
  /division cancel    \u2014 cancel a division in the active season

  /round add    \u2014 add a round to pending setup (auto-numbered by date)
  /round delete \u2014 remove a round and renumber (setup only)
  /round cancel \u2014 cancel a round in the active season
\"\"\"

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from models.division import Division
from models.round import RoundFormat
from models.track import TRACK_DEFAULTS, TRACK_IDS
from utils.channel_guard import channel_guard, admin_only
from utils.message_builder import format_division_list, format_round_list

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
    season_id: int = 0  # set after first DB snapshot; 0 = not yet persisted


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


async def _get_setup_season_id(bot, guild_id: int) -> int | None:
    \"\"\"Return the season_id for a SETUP-status season for the guild, or None.\"\"\"
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM seasons WHERE server_id = ? AND status = 'SETUP' LIMIT 1",
            (guild_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


def _pending_to_division_models(cfg: PendingConfig) -> list[Division]:
    \"\"\"Convert PendingDivision entries to Division model objects for formatting.\"\"\"
    return [
        Division(
            id=0,
            season_id=0,
            name=d.name,
            mention_role_id=d.role_id,
            forecast_channel_id=d.channel_id,
        )
        for d in cfg.divisions
        if d.name
    ]


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class SeasonCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Keyed by user_id (or server_id on recovery) \\u2192 PendingConfig
        self._pending: dict[int, PendingConfig] = {}

    # ------------------------------------------------------------------
    # /season group
    # ------------------------------------------------------------------

    season = app_commands.Group(
        name="season",
        description="Season management commands",
        guild_only=True,
        default_permissions=None,
    )

    @season.command(
        name="setup",
        description="Start season configuration (admin only).",
    )
    @channel_guard
    @admin_only
    async def season_setup(self, interaction: discord.Interaction) -> None:
        \"\"\"Begin season setup \\u2014 no parameters required.\"\"\"
        server_id = interaction.guild_id

        if self._get_pending_for_server(server_id) is not None:
            await interaction.response.send_message(
                "\\u274c A season setup is already in progress for this server. "
                "Use `/season review` to approve, or `/bot-reset` to cancel it first.",
                ephemeral=True,
            )
            return

        if await self.bot.season_service.has_existing_season(server_id):
            await interaction.response.send_message(
                "\\u274c A season already exists for this server. "
                "Use `/season cancel` to delete it, or `/bot-reset` to clear all data.",
                ephemeral=True,
            )
            return

        cfg = PendingConfig(server_id=server_id)
        self._pending[interaction.user.id] = cfg
        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            "\\u2705 Season setup started.\\n\\n"
            "Use `/division add` for each division, then `/round add` for each round.\\n"
            "When done, run `/season review` to review and approve.",
            ephemeral=True,
        )

    @season.command(
        name="review",
        description="Review pending season configuration before approving.",
    )
    @channel_guard
    @admin_only
    async def season_review(self, interaction: discord.Interaction) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "\\u274c No pending season setup. Run `/season setup` first.",
                ephemeral=True,
            )
            return

        lines = [
            "**Season Review**",
            f"Start date: {cfg.start_date}",
            f"Server: {interaction.guild_id}",
            "",
        ]
        for div in cfg.divisions:
            if not div.name:
                continue
            lines.append(
                f"\\U0001f4c2 **{div.name}** | "
                f"Role <@&{div.role_id}> | "
                f"Channel <#{div.channel_id}>"
            )
            for r in div.rounds:
                lines.append(
                    f"  Round {r['round_number']}: {r['format'].value} "
                    f"@ {r['track_name'] or 'Mystery'} \\u2014 {r['scheduled_at'].isoformat()}"
                )
            lines.append("")

        view = _ApproveView(self)
        await interaction.response.send_message("\\n".join(lines), view=view, ephemeral=True)

    @season.command(
        name="approve",
        description="Commit the pending season configuration to the bot.",
    )
    @channel_guard
    @admin_only
    async def season_approve(self, interaction: discord.Interaction) -> None:
        await self._do_approve(interaction)

    @season.command(
        name="status",
        description="View a summary of the active season.",
    )
    @channel_guard
    async def season_status(self, interaction: discord.Interaction) -> None:
        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\\u2139\\ufe0f No active season found for this server.",
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
            active_rounds = [r for r in rounds if r.status == "ACTIVE"]
            next_round = next(
                (
                    r for r in active_rounds
                    if r.format != RoundFormat.MYSTERY
                    and not (r.phase1_done and r.phase2_done and r.phase3_done)
                ),
                None,
            )
            div_tag = " ~~[CANCELLED]~~" if div.status == "CANCELLED" else ""
            lines.append(
                f"\\U0001f4c2 **{div.name}**{div_tag} \\u2014 "
                "Next round: "
                + (
                    f"R{next_round.round_number} @ {next_round.track_name or 'Mystery'} "
                    f"({next_round.scheduled_at.isoformat()})"
                    if next_round
                    else "None remaining"
                )
            )

        await interaction.response.send_message("\\n".join(lines), ephemeral=True)

    @season.command(
        name="cancel",
        description="Cancel and delete the active season (server admin only, irreversible).",
    )
    @app_commands.describe(confirm='Type "CONFIRM" to proceed with season cancellation.')
    @channel_guard
    @admin_only
    async def season_cancel(
        self,
        interaction: discord.Interaction,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await interaction.response.send_message(
                "\\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\\u274c No active season to cancel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        divisions = await self.bot.season_service.get_divisions(season.id)
        active_divs = [d for d in divisions if d.status == "ACTIVE"]
        for div in active_divs:
            try:
                channel = interaction.guild.get_channel(div.forecast_channel_id)
                if channel is not None:
                    await channel.send(
                        "\\U0001f4e2 **Season Cancelled**\\n"
                        "The active season has been cancelled by an administrator. "
                        "All data has been deleted."
                    )
            except Exception:
                log.exception("Failed to post cancellation notice for division %s", div.name)

        for div in divisions:
            div_rounds = await self.bot.season_service.get_division_rounds(div.id)
            for rnd in div_rounds:
                self.bot.scheduler_service.cancel_round(rnd.id)
        self.bot.scheduler_service.cancel_season_end(interaction.guild_id)

        await self.bot.season_service.delete_season(season.id)

        await interaction.followup.send(
            "\\u2705 Season cancelled and all data deleted.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /division group
    # ------------------------------------------------------------------

    division = app_commands.Group(
        name="division",
        description="Division management commands",
        guild_only=True,
        default_permissions=None,
    )

    @division.command(
        name="add",
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
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "\\u274c No pending season setup. Run `/season setup` first.",
                ephemeral=True,
            )
            return

        if any(d.name.lower() == name.lower() for d in cfg.divisions if d.name):
            await interaction.response.send_message(
                f"\\u274c A division named **{name}** already exists in this setup.",
                ephemeral=True,
            )
            return

        div = PendingDivision(name=name, role_id=role.id, channel_id=forecast_channel.id)
        empty = [d for d in cfg.divisions if not d.name]
        if empty:
            idx = cfg.divisions.index(empty[0])
            cfg.divisions[idx] = div
        else:
            cfg.divisions.append(div)

        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            f"\\u2705 Division **{name}** added.\\n"
            f"Role: {role.mention} | Channel: {forecast_channel.mention}\\n\\n"
            + format_division_list(_pending_to_division_models(cfg)),
            ephemeral=True,
        )

    @division.command(
        name="duplicate",
        description="Copy a division's rounds with a datetime offset (setup only).",
    )
    @app_commands.describe(
        source_name="Name of the division to copy from",
        new_name="Name for the new division",
        role="The Discord role to mention for the new division",
        forecast_channel="Forecast channel for the new division",
        day_offset="Days to shift all round datetimes (can be negative)",
        hour_offset="Hours to shift all round datetimes (can be negative, decimals OK)",
    )
    @channel_guard
    @admin_only
    async def division_duplicate(
        self,
        interaction: discord.Interaction,
        source_name: str,
        new_name: str,
        role: discord.Role,
        forecast_channel: discord.TextChannel,
        day_offset: int,
        hour_offset: float,
    ) -> None:
        season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
        if season_id is None:
            await interaction.response.send_message(
                "\\u274c `/division duplicate` can only be used during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        src_div = next((d for d in divisions if d.name.lower() == source_name.lower()), None)
        if src_div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{source_name}` not found in pending setup.",
                ephemeral=True,
            )
            return

        if any(d.name.lower() == new_name.lower() for d in divisions):
            await interaction.response.send_message(
                f"\\u274c A division named **{new_name}** already exists.",
                ephemeral=True,
            )
            return

        from collections import Counter
        from datetime import timedelta
        delta = timedelta(days=day_offset, hours=hour_offset)
        src_rounds = await self.bot.season_service.get_division_rounds(src_div.id)
        shifted = [rnd.scheduled_at + delta for rnd in src_rounds]
        now = datetime.now(timezone.utc)
        warnings: list[str] = []
        for dt in shifted:
            dt_aware = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
            if dt_aware < now:
                warnings.append(f"\\u26a0\\ufe0f One or more shifted datetimes are in the past: {dt.isoformat()}")
                break
        dt_counts = Counter(shifted)
        for dt, count in dt_counts.items():
            if count > 1:
                warnings.append(f"\\u26a0\\ufe0f Multiple rounds share the same shifted datetime: {dt.isoformat()}")
                break

        await interaction.response.defer(ephemeral=True)

        new_div = await self.bot.season_service.duplicate_division(
            division_id=src_div.id,
            name=new_name,
            role_id=role.id,
            forecast_channel_id=forecast_channel.id,
            day_offset=day_offset,
            hour_offset=hour_offset,
        )

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        updated_divisions = await self.bot.season_service.get_divisions(season_id)
        new_rounds = await self.bot.season_service.get_division_rounds(new_div.id)

        warn_block = ("\\n" + "\\n".join(warnings)) if warnings else ""
        await interaction.followup.send(
            f"\\u2705 Division **{new_name}** created from **{source_name}**"
            f" (offset: {day_offset:+}d {hour_offset:+}h).\\n\\n"
            + format_division_list(updated_divisions)
            + "\\n\\n"
            + f"**{new_name} rounds:**\\n"
            + format_round_list(new_rounds)
            + warn_block,
            ephemeral=True,
        )

    @division.command(
        name="delete",
        description="Remove a division and all its rounds from pending setup.",
    )
    @app_commands.describe(name="Name of the division to delete")
    @channel_guard
    @admin_only
    async def division_delete(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
        if season_id is None:
            await interaction.response.send_message(
                "\\u274c `/division delete` can only be used during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{name}` not found.",
                ephemeral=True,
            )
            return

        await self.bot.season_service.delete_division(div.id)

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        remaining = await self.bot.season_service.get_divisions(season_id)
        await interaction.response.send_message(
            f"\\u2705 Division **{name}** deleted.\\n\\n"
            + format_division_list(remaining),
            ephemeral=True,
        )

    @division.command(
        name="rename",
        description="Rename a division (setup only).",
    )
    @app_commands.describe(
        current_name="Current name of the division",
        new_name="New name for the division",
    )
    @channel_guard
    @admin_only
    async def division_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
        if season_id is None:
            await interaction.response.send_message(
                "\\u274c `/division rename` can only be used during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == current_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{current_name}` not found.",
                ephemeral=True,
            )
            return

        if any(d.name.lower() == new_name.lower() for d in divisions if d.id != div.id):
            await interaction.response.send_message(
                f"\\u274c A division named **{new_name}** already exists.",
                ephemeral=True,
            )
            return

        await self.bot.season_service.rename_division(div.id, new_name)

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            for pd in cfg.divisions:
                if pd.name.lower() == current_name.lower():
                    pd.name = new_name
                    break

        remaining = await self.bot.season_service.get_divisions(season_id)
        await interaction.response.send_message(
            f"\\u2705 Division **{current_name}** renamed to **{new_name}**.\\n\\n"
            + format_division_list(remaining),
            ephemeral=True,
        )

    @division.command(
        name="cancel",
        description="Cancel a division in the active season (irreversible).",
    )
    @app_commands.describe(
        name="Name of the division to cancel",
        confirm='Type "CONFIRM" to proceed.',
    )
    @channel_guard
    @admin_only
    async def division_cancel(
        self,
        interaction: discord.Interaction,
        name: str,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await interaction.response.send_message(
                "\\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\\u274c `/division cancel` requires an active season.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{name}` not found.",
                ephemeral=True,
            )
            return

        if div.status == "CANCELLED":
            await interaction.response.send_message(
                f"\\u274c Division **{name}** is already cancelled.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        for rnd in rounds:
            self.bot.scheduler_service.cancel_round(rnd.id)

        await self.bot.season_service.cancel_division(
            division_id=div.id,
            server_id=interaction.guild_id,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
        )

        try:
            channel = interaction.guild.get_channel(div.forecast_channel_id)
            if channel is not None:
                await channel.send(
                    f"\\U0001f4e2 **Division Cancelled: {div.name}**\\n"
                    "This division has been cancelled by an administrator. "
                    "No further weather forecasts will be posted for this division."
                )
        except Exception:
            log.exception("Failed to post division cancel notice for %s", div.name)

        await interaction.followup.send(
            f"\\u2705 Division **{name}** cancelled.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /round group
    # ------------------------------------------------------------------

    round = app_commands.Group(
        name="round",
        description="Round management commands",
        guild_only=True,
        default_permissions=None,
    )

    @round.command(
        name="add",
        description="Add a round to a division. Round number is auto-derived from scheduled date.",
    )
    @app_commands.describe(
        division_name="Name of the division this round belongs to",
        format="Round format (NORMAL, SPRINT, MYSTERY, ENDURANCE)",
        scheduled_at="Race date/time in ISO format (YYYY-MM-DDTHH:MM:SS UTC)",
        track="Track ID or name (e.g. 27 or United Kingdom). Leave blank for Mystery rounds.",
    )
    @channel_guard
    @admin_only
    async def round_add(
        self,
        interaction: discord.Interaction,
        division_name: str,
        format: str,
        scheduled_at: str,
        track: str = "",
    ) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "\\u274c No pending season setup. Run `/season setup` first.",
                ephemeral=True,
            )
            return

        try:
            fmt = RoundFormat(format.upper())
        except ValueError:
            await interaction.response.send_message(
                f"\\u274c Invalid format `{format}`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.",
                ephemeral=True,
            )
            return

        track_name = track.strip() or None

        if fmt != RoundFormat.MYSTERY and not track_name:
            await interaction.response.send_message(
                f"\\u274c A track is required for `{fmt.value}` rounds. "
                "Leave track blank only for `MYSTERY` rounds.",
                ephemeral=True,
            )
            return

        if track_name and track_name not in TRACK_DEFAULTS:
            track_name = TRACK_IDS.get(track_name.zfill(2), track_name)
        if track_name and track_name not in TRACK_DEFAULTS:
            await interaction.response.send_message(
                f"\\u274c Unknown track `{track_name}`.\\n"
                "Use `/round add` and type a number or name \\u2014 autocomplete will guide you.",
                ephemeral=True,
            )
            return

        try:
            sched = datetime.fromisoformat(scheduled_at)
        except ValueError:
            await interaction.response.send_message(
                "\\u274c Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`",
                ephemeral=True,
            )
            return

        div = next((d for d in cfg.divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{division_name}` not found in pending setup.",
                ephemeral=True,
            )
            return

        new_round: dict[str, Any] = {
            "round_number": 0,
            "format": fmt,
            "track_name": track_name,
            "scheduled_at": sched,
        }
        div.rounds.append(new_round)
        div.rounds.sort(key=lambda r: r["scheduled_at"])
        for i, r in enumerate(div.rounds, start=1):
            r["round_number"] = i

        await self._snapshot_pending(cfg)

        assigned_number = new_round["round_number"]
        from models.round import Round as RoundModel
        round_models = [
            RoundModel(
                id=0,
                division_id=0,
                round_number=r["round_number"],
                format=r["format"],
                track_name=r["track_name"],
                scheduled_at=r["scheduled_at"],
            )
            for r in div.rounds
        ]
        await interaction.response.send_message(
            f"\\u2705 Round **{assigned_number}** added to **{div.name}**.\\n"
            f"Format: {fmt.value} | Track: {track_name or 'Mystery'} | {sched.isoformat()} UTC\\n\\n"
            + format_round_list(round_models),
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
            label = f"{id_str} \\u2013 {name}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=name))
        return results[:25]

    @round.command(
        name="delete",
        description="Remove a round from pending setup and renumber siblings.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="Round number to delete",
    )
    @channel_guard
    @admin_only
    async def round_delete(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
    ) -> None:
        season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
        if season_id is None:
            await interaction.response.send_message(
                "\\u274c `/round delete` can only be used during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{division_name}` not found.",
                ephemeral=True,
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.response.send_message(
                f"\\u274c Round {round_number} not found in division `{division_name}`.",
                ephemeral=True,
            )
            return

        await self.bot.season_service.delete_round(rnd.id)

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        remaining = await self.bot.season_service.get_division_rounds(div.id)
        await interaction.response.send_message(
            f"\\u2705 Round **{round_number}** deleted from **{division_name}** and rounds renumbered.\\n\\n"
            + format_round_list(remaining),
            ephemeral=True,
        )

    @round.command(
        name="cancel",
        description="Cancel a round in the active season (irreversible).",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to cancel",
        confirm='Type "CONFIRM" to proceed.',
    )
    @channel_guard
    @admin_only
    async def round_cancel(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await interaction.response.send_message(
                "\\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\\u274c `/round cancel` requires an active season.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\\u274c Division `{division_name}` not found.",
                ephemeral=True,
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.response.send_message(
                f"\\u274c Round {round_number} not found in division `{division_name}`.",
                ephemeral=True,
            )
            return

        if rnd.status == "CANCELLED":
            await interaction.response.send_message(
                f"\\u274c Round {round_number} in **{division_name}** is already cancelled.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        self.bot.scheduler_service.cancel_round(rnd.id)

        await self.bot.season_service.cancel_round(
            round_id=rnd.id,
            server_id=interaction.guild_id,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
        )

        try:
            channel = interaction.guild.get_channel(div.forecast_channel_id)
            if channel is not None:
                await channel.send(
                    f"\\U0001f4e2 **Round {round_number} Cancelled: {div.name}**\\n"
                    f"Round {round_number} ({rnd.track_name or 'Mystery'}) has been cancelled by "
                    "an administrator. No weather forecast will be posted for this round."
                )
        except Exception:
            log.exception("Failed to post round cancel notice for round %s in %s", round_number, div.name)

        await interaction.followup.send(
            f"\\u2705 Round **{round_number}** in **{division_name}** cancelled.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # Shared instance methods
    # ------------------------------------------------------------------

    def clear_pending_for_server(self, server_id: int) -> None:
        \"\"\"Discard any in-memory pending setup belonging to *server_id*.\"\"\"
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

    def _get_pending_for_server(self, server_id: int) -> PendingConfig | None:
        \"\"\"Return the in-memory pending config for *server_id*, or None.\"\"\"
        return next(
            (cfg for cfg in self._pending.values() if cfg.server_id == server_id),
            None,
        )

    async def _snapshot_pending(self, cfg: PendingConfig) -> None:
        \"\"\"Write the current PendingConfig to DB (status=SETUP) and update cfg.season_id.\"\"\"
        divisions_data = [
            {
                "name": d.name,
                "role_id": d.role_id,
                "channel_id": d.channel_id,
                "rounds": d.rounds,
            }
            for d in cfg.divisions
            if d.name
        ]
        cfg.season_id = await self.bot.season_service.save_pending_snapshot(
            cfg.server_id, cfg.start_date, cfg.season_id, divisions_data
        )

    async def _reload_pending_from_db(self, cfg: PendingConfig) -> None:
        \"\"\"Resync the in-memory PendingConfig.divisions from DB (after direct DB operations).\"\"\"
        if cfg.season_id == 0:
            return
        db_divisions = await self.bot.season_service.get_divisions(cfg.season_id)
        cfg.divisions = []
        for d in db_divisions:
            rounds_db = await self.bot.season_service.get_division_rounds(d.id)
            cfg.divisions.append(PendingDivision(
                name=d.name,
                role_id=d.mention_role_id,
                channel_id=d.forecast_channel_id,
                rounds=[
                    {
                        "round_number": r.round_number,
                        "format": r.format,
                        "track_name": r.track_name,
                        "scheduled_at": r.scheduled_at,
                    }
                    for r in rounds_db
                ],
            ))

    async def recover_pending_setups(self) -> None:
        \"\"\"Restore in-memory _pending from DB SETUP seasons on bot startup.\"\"\"
        for s in await self.bot.season_service.load_all_setup_seasons():
            if self._get_pending_for_server(s["server_id"]) is not None:
                continue
            cfg = PendingConfig(
                server_id=s["server_id"],
                start_date=s["start_date"],
                season_id=s["season_id"],
                divisions=[
                    PendingDivision(
                        name=d["name"],
                        role_id=d["role_id"],
                        channel_id=d["channel_id"],
                        rounds=d["rounds"],
                    )
                    for d in s["divisions"]
                ],
            )
            self._pending[s["server_id"]] = cfg
        log.info("Recovered %d pending setup(s) from DB", len(self._pending))

    async def _do_approve(self, interaction: discord.Interaction) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "\\u274c No pending season setup.",
                ephemeral=True,
            )
            return

        if cfg.season_id == 0:
            await interaction.response.send_message(
                "\\u274c Season setup state is incomplete. Use `/bot-reset` and start again.",
                ephemeral=True,
            )
            return

        season_svc = self.bot.season_service

        divisions = await season_svc.get_divisions(cfg.season_id)
        all_rounds = []
        for div_db in divisions:
            rounds_db = await season_svc.get_division_rounds(div_db.id)
            for rnd in rounds_db:
                await season_svc.create_sessions_for_round(rnd.id, rnd.format)
                all_rounds.append(rnd)

        # Schedule FIRST \\u2014 if this fails the season stays SETUP in DB (fix #5)
        self.bot.scheduler_service.schedule_all_rounds(all_rounds)

        # Only transition to ACTIVE after scheduling succeeds
        await season_svc.transition_to_active(cfg.season_id)

        stale_keys = [uid for uid, c in self._pending.items() if c.server_id == cfg.server_id]
        for uid in stale_keys:
            del self._pending[uid]

        msg = (
            f"\\u2705 **Season approved and activated!**\\n"
            f"Season ID: {cfg.season_id} | "
            f"Rounds scheduled: {len(all_rounds)}"
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

        log.info("Season %s activated for server %s by %s", cfg.season_id, cfg.server_id, interaction.user)


# ---------------------------------------------------------------------------
# Approve button view
# ---------------------------------------------------------------------------


class _ApproveView(discord.ui.View):
    def __init__(self, cog: SeasonCog) -> None:
        super().__init__(timeout=300)
        self._cog = cog

    @discord.ui.button(label="\\u2705 Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._cog._do_approve(interaction)
        self.stop()

    @discord.ui.button(label="\\u270f\\ufe0f Go Back to Edit", style=discord.ButtonStyle.secondary)
    async def amend(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Use `/round amend` to correct a round, or `/division add` / `/round add` to add more. "
            "Then run `/season review` again.",
            ephemeral=True,
        )
        self.stop()
"""

TARGET.write_text(CONTENT, encoding="utf-8")
print(f"Written {len(CONTENT)} chars, {len(CONTENT.splitlines())} lines to {TARGET}")
