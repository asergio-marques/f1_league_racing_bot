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
from utils.channel_guard import admin_only

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
        guild_only=True,
        default_permissions=None,
    )

    # ------------------------------------------------------------------
    # /test-mode toggle
    # ------------------------------------------------------------------

    @test_mode.command(
        name="toggle",
        description="Enable or disable test mode. State persists across bot restarts.",
    )
    @admin_only
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
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            # Defer so the flush (multiple Discord API calls) has time to complete
            await interaction.response.defer(ephemeral=True)
            from services.forecast_cleanup_service import flush_pending_deletions
            await flush_pending_deletions(interaction.guild_id, self.bot)  # type: ignore[attr-defined]
            msg = (
                "✅ Test mode **disabled**. "
                "The scheduler will resume normal operation for any remaining pending phases."
            )
            await interaction.followup.send(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # /test-mode advance
    # ------------------------------------------------------------------

    @test_mode.command(
        name="advance",
        description="Execute the next pending weather phase immediately.",
    )
    @admin_only
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
            # Non-mystery phases exhausted.  If the season is still active its end
            # was supposed to fire on the previous Phase-3 advance; trigger it now
            # as a safety net (e.g. timing race or past-dates path left season open).
            from services.season_end_service import execute_season_end
            season = await self.bot.season_service.get_active_season(  # type: ignore[attr-defined]
                interaction.guild_id
            )
            if season is not None:
                self.bot.scheduler_service.cancel_season_end(  # type: ignore[attr-defined]
                    interaction.guild_id
                )
                await execute_season_end(interaction.guild_id, season.id, self.bot)
                await interaction.followup.send(
                    "🏁 **Season complete!** All phases have been executed and "
                    "all data has been cleared.\n"
                    "Run `/season-setup` to begin a new season.",
                    ephemeral=True,
                )
            else:
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

        phase_number = entry["phase_number"]

        log.info(
            "Test mode advance: Phase %s, round=%d (id=%d), division=%s, track=%s",
            "mystery-notice" if phase_number == 0 else phase_number,
            entry["round_number"],
            entry["round_id"],
            entry["division_name"],
            entry["track_name"],
        )

        # ── Mystery round notice (phase_number=0) ──────────────────────────────
        if phase_number == 0:
            from services.mystery_notice_service import run_mystery_notice
            from db.database import get_connection
            try:
                await run_mystery_notice(entry["round_id"], self.bot)
            except Exception:
                log.exception(
                    "Test mode advance: unhandled error in mystery notice for round_id=%d",
                    entry["round_id"],
                )
                await interaction.followup.send(
                    f"❌ An internal error occurred while posting the Mystery Round notice "
                    f"for **{entry['division_name']}** — **Round {entry['round_number']}**. "
                    "Check the bot logs for details.",
                    ephemeral=True,
                )
                return
            # Mark notice as sent so this round is excluded from future advance calls
            async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
                await db.execute(
                    "UPDATE rounds SET phase1_done = 1 WHERE id = ?",
                    (entry["round_id"],),
                )
                await db.commit()
            await interaction.followup.send(
                f"🔮 Posted **Mystery Round notice** for "
                f"**{entry['division_name']}** — **Round {entry['round_number']}**. "
                f"Notice posted to the division forecast channel.",
                ephemeral=True,
            )
            return

        # ── Normal phase dispatch ───────────────────────────────────────────────
        phase_runners = {1: run_phase1, 2: run_phase2, 3: run_phase3}
        runner = phase_runners[phase_number]

        try:
            await runner(entry["round_id"], self.bot)
        except Exception:
            log.exception(
                "Test mode advance: unhandled error in phase %d runner for round_id=%d",
                phase_number, entry["round_id"],
            )
            await interaction.followup.send(
                f"\u274c An internal error occurred while advancing Phase {phase_number} "
                f"for **{entry['division_name']}** \u2014 **{entry['track_name']}**. "
                "Check the bot logs for details.",
                ephemeral=True,
            )
            return

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
    @admin_only
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
