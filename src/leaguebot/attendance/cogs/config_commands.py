"""`ConfigCommands`: the `/attendance config` subgroup, the settings and `show`.

Each of the eight settings asks the change queue to set it (`_ask_setting`), the setting's check
judging every refusal; `show` only reads.
"""
from __future__ import annotations

import discord
from discord import app_commands

from leaguebot.attendance.cogs.attendance_group import (
    AFTER_THE_CATCH_UP,
    ATTENDANCE_GROUP,
    AttendanceSubgroup,
)
from leaguebot.attendance.services.attendance_settings import KINDS
from leaguebot.core.utils.channel_guard import league_manager_only, changes_nothing
from leaguebot.core.utils.interaction_errors import describe


class ConfigCommands(AttendanceSubgroup):
    config = app_commands.Group(
        name="config",
        description="Configure attendance module settings.",
        parent=ATTENDANCE_GROUP,
    )

    async def _ask_setting(
        self, interaction: discord.Interaction, command: str, value: int | None
    ) -> None:
        """Ask the change queue to set the setting *command* names to *value*: a threshold's 0,
        which disables it, is asked as None.

        The setting's check judges every refusal, in the words the command has always used, as it
        is asked and again as the change starts (`attendance_settings`), so a command holds no
        rule of its own. The queue's acknowledgement is the command's response, so nothing is
        deferred; it writes the value, its `ATTENDANCE_CONFIG_SET` entry and its log line in one
        save, and answers and logs after it.
        """
        await self.bot.change_queue.ask(
            KINDS[command], {"value": value}, interaction=interaction, what=describe(interaction)
        )

    # ── /attendance config rsvp-notice ────────────────────────────────────

    @config.command(
        name="rsvp-notice",
        description="Set how many days before the race to send the first RSVP notice.",
    )
    @app_commands.describe(days="Number of days before the race for the RSVP notice (≥ 1)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_rsvp_notice(
        self, interaction: discord.Interaction, days: int
    ) -> None:
        await self._ask_setting(interaction, "rsvp-notice", days)

    # ── /attendance config rsvp-last-notice ───────────────────────────────

    @config.command(
        name="rsvp-last-notice",
        description="Set hours before the race for the last RSVP reminder (0 = disabled).",
    )
    @app_commands.describe(hours="Hours before the race for the last notice (0 to disable)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_rsvp_last_notice(
        self, interaction: discord.Interaction, hours: int
    ) -> None:
        await self._ask_setting(interaction, "rsvp-last-notice", hours)

    # ── /attendance config rsvp-deadline ──────────────────────────────────

    @config.command(
        name="rsvp-deadline",
        description="Set the RSVP deadline in hours before the race.",
    )
    @app_commands.describe(hours="Hours before the race when RSVPs close")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_rsvp_deadline(
        self, interaction: discord.Interaction, hours: int
    ) -> None:
        await self._ask_setting(interaction, "rsvp-deadline", hours)

    # ── /attendance config no-rsvp-penalty, absent-penalty, no-show-penalty ──

    @config.command(
        name="no-rsvp-penalty",
        description="Set the point penalty for failing to RSVP.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_no_rsvp_penalty(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        await self._ask_setting(interaction, "no-rsvp-penalty", points)

    @config.command(
        name="absent-penalty",
        description="Penalty for absent drivers without ACCEPTED RSVP (stacks with no-RSVP penalty for NO_RSVP drivers).",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_absent_penalty(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        await self._ask_setting(interaction, "absent-penalty", points)

    @config.command(
        name="no-show-penalty",
        description="Penalty for a driver who RSVP'd ACCEPTED but did not attend.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_no_show_penalty(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        await self._ask_setting(interaction, "no-show-penalty", points)

    # ── /attendance config autosack ────────────────────────────────────────

    @config.command(
        name="autosack",
        description="Set the cumulative no-show threshold that triggers auto-sack (0 = disabled).",
    )
    @app_commands.describe(points="Cumulative threshold for auto-sack (0 to disable)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_autosack(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        await self._ask_setting(interaction, "autosack", None if points == 0 else points)

    # ── /attendance config autoreserve ────────────────────────────────────

    @config.command(
        name="autoreserve",
        description="Set the cumulative threshold that triggers auto-reserve (0 = disabled).",
    )
    @app_commands.describe(points="Cumulative threshold for auto-reserve (0 to disable)")
    @league_manager_only
    @AFTER_THE_CATCH_UP
    async def config_autoreserve(
        self, interaction: discord.Interaction, points: int
    ) -> None:
        await self._ask_setting(interaction, "autoreserve", None if points == 0 else points)

    # ── /attendance config show ────────────────────────────────────────────

    @config.command(
        name="show",
        description="Show the current attendance configuration for this server.",
    )
    @league_manager_only
    @changes_nothing
    async def config_show(self, interaction: discord.Interaction) -> None:
        if not await self._module_gate(interaction, record=False):
            return

        cfg = await self.bot.attendance_service.get_config()
        if cfg is None:
            await interaction.response.send_message(
                "\u274c No attendance configuration found. Enable the module first.",
                ephemeral=True,
            )
            return

        def _fmt_opt(value: int | None) -> str:
            return str(value) if value is not None else "disabled"

        lines = [
            "**Attendance Configuration**",
            "",
            "**Timing**",
            f"  RSVP notice: **{cfg.rsvp_notice_days}** day(s) before race",
            f"  Last reminder: **{cfg.rsvp_last_notice_hours}** hr(s) before race"
            + (" *(disabled)*" if cfg.rsvp_last_notice_hours == 0 else ""),
            f"  RSVP deadline: **{cfg.rsvp_deadline_hours}** hr(s) before race",
            "",
            "**Penalties**",
            f"  No-RSVP: **{cfg.no_rsvp_penalty}** pt(s)",
            f"  Absent penalty: **{cfg.absent_penalty}** pt(s)",
            f"  No-show (ACCEPTED + absent): **{cfg.no_show_penalty}** pt(s)",
            "",
            "**Auto-actions**",
            f"  Auto-reserve threshold: **{_fmt_opt(cfg.autoreserve_threshold)}**",
            f"  Auto-sack threshold: **{_fmt_opt(cfg.autosack_threshold)}**",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)
