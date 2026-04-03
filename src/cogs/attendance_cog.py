"""AttendanceCog — /attendance config commands."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from services.attendance_service import validate_timing_invariant
from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)


class AttendanceCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    attendance = app_commands.Group(
        name="attendance",
        description="Attendance module commands.",
        default_permissions=None,
    )

    config = app_commands.Group(
        name="config",
        description="Configure attendance module settings.",
        parent=attendance,
    )

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _guard_module_enabled(self, interaction: discord.Interaction) -> bool:
        """Return True (and send error) if module is NOT enabled."""
        if not await self.bot.module_service.is_attendance_enabled(interaction.guild_id):  # type: ignore[attr-defined]
            await interaction.response.send_message(
                "\u274c The Attendance module is not enabled. "
                "Use `/module enable attendance` first.",
                ephemeral=True,
            )
            return False
        return True

    async def _guard_no_active_season(self, interaction: discord.Interaction) -> bool:
        """Return True (and send error) if there IS an active season."""
        season = await self.bot.season_service.get_active_season(interaction.guild_id)  # type: ignore[attr-defined]
        if season is not None:
            await interaction.response.send_message(
                "\u274c Attendance configuration cannot be changed while a season is active.",
                ephemeral=True,
            )
            return False
        return True

    # ── /attendance config rsvp-notice ────────────────────────────────────

    @config.command(
        name="rsvp-notice",
        description="Set how many days before the race to send the first RSVP notice.",
    )
    @app_commands.describe(days="Number of days before the race for the RSVP notice (≥ 1)")
    @channel_guard
    @admin_only
    async def config_rsvp_notice(
        self, interaction: discord.Interaction, days: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if not await self._guard_no_active_season(interaction):
            return
        if days < 1:
            await interaction.response.send_message(
                "\u274c `rsvp_notice_days` must be at least 1.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        cfg = await self.bot.attendance_service.get_config(server_id)  # type: ignore[attr-defined]
        if cfg is None:
            await interaction.response.send_message(
                "\u274c No attendance configuration found. Enable the module first.",
                ephemeral=True,
            )
            return

        error = validate_timing_invariant(days, cfg.rsvp_last_notice_hours, cfg.rsvp_deadline_hours)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_rsvp_notice_days(server_id, days)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 RSVP notice set to **{days}** day(s) before the race.", ephemeral=True
        )

    # ── /attendance config rsvp-last-notice ───────────────────────────────

    @config.command(
        name="rsvp-last-notice",
        description="Set hours before the race for the last RSVP reminder (0 = disabled).",
    )
    @app_commands.describe(hours="Hours before the race for the last notice (0 to disable)")
    @channel_guard
    @admin_only
    async def config_rsvp_last_notice(
        self, interaction: discord.Interaction, hours: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if not await self._guard_no_active_season(interaction):
            return
        if hours < 0:
            await interaction.response.send_message(
                "\u274c `rsvp_last_notice_hours` cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        cfg = await self.bot.attendance_service.get_config(server_id)  # type: ignore[attr-defined]
        if cfg is None:
            await interaction.response.send_message(
                "\u274c No attendance configuration found. Enable the module first.",
                ephemeral=True,
            )
            return

        error = validate_timing_invariant(cfg.rsvp_notice_days, hours, cfg.rsvp_deadline_hours)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_rsvp_last_notice_hours(server_id, hours)  # type: ignore[attr-defined]
        if hours == 0:
            msg = "\u2705 Last RSVP reminder **disabled** (set to 0)."
        else:
            msg = f"\u2705 Last RSVP reminder set to **{hours}** hour(s) before the race."
        await interaction.followup.send(msg, ephemeral=True)

    # ── /attendance config rsvp-deadline ──────────────────────────────────

    @config.command(
        name="rsvp-deadline",
        description="Set the RSVP deadline in hours before the race.",
    )
    @app_commands.describe(hours="Hours before the race when RSVPs close")
    @channel_guard
    @admin_only
    async def config_rsvp_deadline(
        self, interaction: discord.Interaction, hours: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if not await self._guard_no_active_season(interaction):
            return
        if hours < 0:
            await interaction.response.send_message(
                "\u274c `rsvp_deadline_hours` cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        cfg = await self.bot.attendance_service.get_config(server_id)  # type: ignore[attr-defined]
        if cfg is None:
            await interaction.response.send_message(
                "\u274c No attendance configuration found. Enable the module first.",
                ephemeral=True,
            )
            return

        error = validate_timing_invariant(cfg.rsvp_notice_days, cfg.rsvp_last_notice_hours, hours)
        if error:
            await interaction.response.send_message(f"\u274c {error}", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_rsvp_deadline_hours(server_id, hours)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 RSVP deadline set to **{hours}** hour(s) before the race.", ephemeral=True
        )

    # ── /attendance config no-rsvp-penalty ────────────────────────────────

    @config.command(
        name="no-rsvp-penalty",
        description="Set the point penalty for failing to RSVP.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @channel_guard
    @admin_only
    async def config_no_rsvp_penalty(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if points < 0:
            await interaction.response.send_message(
                "\u274c Penalty points cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_no_rsvp_penalty(server_id, points)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 No-RSVP penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config no-attend-penalty ──────────────────────────────

    @config.command(
        name="no-attend-penalty",
        description="Set the point penalty for missing attendance without notice.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @channel_guard
    @admin_only
    async def config_no_attend_penalty(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if points < 0:
            await interaction.response.send_message(
                "\u274c Penalty points cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_no_attend_penalty(server_id, points)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 No-attend penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config no-show-penalty ────────────────────────────────

    @config.command(
        name="no-show-penalty",
        description="Set the point penalty for a no-show (RSVP'd but did not attend).",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @channel_guard
    @admin_only
    async def config_no_show_penalty(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if points < 0:
            await interaction.response.send_message(
                "\u274c Penalty points cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_no_show_penalty(server_id, points)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 No-show penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config autosack ────────────────────────────────────────

    @config.command(
        name="autosack",
        description="Set the cumulative no-show threshold that triggers auto-sack (0 = disabled).",
    )
    @app_commands.describe(points="Cumulative threshold for auto-sack (0 to disable)")
    @channel_guard
    @admin_only
    async def config_autosack(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if points < 0:
            await interaction.response.send_message(
                "\u274c Threshold cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        value = None if points == 0 else points
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_autosack_threshold(server_id, value)  # type: ignore[attr-defined]
        if value is None:
            msg = "\u2705 Auto-sack **disabled**."
        else:
            msg = f"\u2705 Auto-sack threshold set to **{value}** point(s)."
        await interaction.followup.send(msg, ephemeral=True)

    # ── /attendance config autoreserve ────────────────────────────────────

    @config.command(
        name="autoreserve",
        description="Set the cumulative threshold that triggers auto-reserve (0 = disabled).",
    )
    @app_commands.describe(points="Cumulative threshold for auto-reserve (0 to disable)")
    @channel_guard
    @admin_only
    async def config_autoreserve(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        if not await self._guard_module_enabled(interaction):
            return
        if points < 0:
            await interaction.response.send_message(
                "\u274c Threshold cannot be negative.", ephemeral=True
            )
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        value = None if points == 0 else points
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_autoreserve_threshold(server_id, value)  # type: ignore[attr-defined]
        if value is None:
            msg = "\u2705 Auto-reserve **disabled**."
        else:
            msg = f"\u2705 Auto-reserve threshold set to **{value}** point(s)."
        await interaction.followup.send(msg, ephemeral=True)
