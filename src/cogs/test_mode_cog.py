"""TestModeCog — /test-mode command group.

Provides three subcommands for system-level testing without waiting for
the real APScheduler triggers:

  /test-mode toggle  — enable or disable test mode (state persists)
  /test-mode advance — immediately execute the next pending phase
  /test-mode review  — show season/round/phase status summary (ephemeral)

All commands are gated by @channel_guard (interaction role + channel).
advance and review additionally require test mode to be active.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services.test_mode_service import (
    toggle_test_mode,
    get_next_pending_phase,
    build_review_summary,
)
from utils.channel_guard import channel_guard

log = logging.getLogger(__name__)


class TestModeCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Command group
    # ------------------------------------------------------------------

    test_mode = app_commands.Group(
        name="test-mode",
        description="Test mode commands for system verification",
    )

    # ------------------------------------------------------------------
    # /test-mode toggle
    # ------------------------------------------------------------------

    @test_mode.command(
        name="toggle",
        description="Enable or disable test mode. State persists across bot restarts.",
    )
    @channel_guard
    async def toggle(self, interaction: discord.Interaction) -> None:
        new_state = await toggle_test_mode(
            interaction.guild_id,
            self.bot.db_path,  # type: ignore[attr-defined]
        )
        if new_state:
            msg = (
                "✅ Test mode **enabled**. "
                "Use `/test-mode advance` to step through phases, "
                "or `/test-mode review` to inspect season status."
            )
        else:
            msg = (
                "✅ Test mode **disabled**. "
                "The scheduler will resume normal operation for any remaining pending phases."
            )
        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # /test-mode advance
    # ------------------------------------------------------------------

    @test_mode.command(
        name="advance",
        description="Execute the next pending weather phase immediately.",
    )
    @channel_guard
    async def advance(self, interaction: discord.Interaction) -> None:
        # Check test mode is active before doing any heavy work
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.",
                ephemeral=True,
            )
            return

        # Defer because phase execution posts to Discord channels (can take seconds)
        await interaction.response.defer(ephemeral=True)

        entry = await get_next_pending_phase(
            interaction.guild_id,
            self.bot.db_path,  # type: ignore[attr-defined]
        )

        if entry is None:
            await interaction.followup.send(
                "ℹ️ All phases for all rounds and divisions have been executed. "
                "There is nothing left to advance.",
                ephemeral=True,
            )
            return

        # Dispatch to the appropriate phase service
        from services.phase1_service import run_phase1
        from services.phase2_service import run_phase2
        from services.phase3_service import run_phase3

        phase_runners = {1: run_phase1, 2: run_phase2, 3: run_phase3}
        phase_number = entry["phase_number"]
        runner = phase_runners[phase_number]

        log.info(
            "Test mode advance: Phase %d, round_id=%d, division=%s, track=%s",
            phase_number,
            entry["round_id"],
            entry["division_name"],
            entry["track_name"],
        )

        await runner(entry["round_id"], self.bot)

        # After running this phase, check if the entire season is now complete
        next_entry = await get_next_pending_phase(
            interaction.guild_id,
            self.bot.db_path,  # type: ignore[attr-defined]
        )

        if next_entry is None and phase_number == 3:
            # This was the last phase of the last round — end the season now
            from services.season_end_service import execute_season_end
            season = await self.bot.season_service.get_active_season(  # type: ignore[attr-defined]
                interaction.guild_id
            )
            if season is not None:
                # Cancel any pending scheduled job (executing immediately)
                self.bot.scheduler_service.cancel_season_end(  # type: ignore[attr-defined]
                    interaction.guild_id
                )
                await execute_season_end(interaction.guild_id, season.id, self.bot)
                await interaction.followup.send(
                    f"⏩ Advanced **Phase {phase_number}** for "
                    f"**{entry['division_name']}** — **{entry['track_name']}**. "
                    f"That was the final phase.\n"
                    f"🏁 **Season complete!** All data has been cleared. "
                    f"Run `/season-setup` to begin a new season.",
                    ephemeral=True,
                )
                return

        await interaction.followup.send(
            f"⏩ Advanced **Phase {phase_number}** for "
            f"**{entry['division_name']}** — **{entry['track_name']}**. "
            f"Outputs posted to the configured forecast and log channels.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /test-mode review
    # ------------------------------------------------------------------

    @test_mode.command(
        name="review",
        description="Show season configuration and phase completion status.",
    )
    @channel_guard
    async def review(self, interaction: discord.Interaction) -> None:
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "ℹ️ Test mode is not active. Use `/test-mode toggle` to enable it first.",
                ephemeral=True,
            )
            return

        summary = await build_review_summary(
            interaction.guild_id,
            self.bot.db_path,  # type: ignore[attr-defined]
        )
        await interaction.response.send_message(summary, ephemeral=True)
