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
from utils.channel_guard import channel_guard, admin_only

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
    @channel_guard
    @admin_only
    async def toggle(self, interaction: discord.Interaction) -> None:
        new_state = await toggle_test_mode(
            interaction.guild_id,
            self.bot.db_path,  # type: ignore[attr-defined]
        )
        if new_state:
            # Auto-seed default point configs for the current season (SETUP or ACTIVE)
            await interaction.response.defer(ephemeral=True)
            from db.database import get_connection
            from services.test_roster_service import ensure_test_configs

            async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
                season_cursor = await db.execute(
                    "SELECT id FROM seasons WHERE server_id = ? AND status IN ('SETUP', 'ACTIVE')",
                    (interaction.guild_id,),
                )
                season_row = await season_cursor.fetchone()

            config_note = ""
            if season_row is not None:
                new_configs = await ensure_test_configs(
                    server_id=interaction.guild_id,
                    season_id=season_row["id"],
                    db_path=self.bot.db_path,  # type: ignore[attr-defined]
                )
                if new_configs:
                    config_note = (
                        f"\n📌 Added default point configs: **{', '.join(new_configs)}**"
                    )

            msg = (
                "✅ Test mode **enabled**. "
                "Use `/test-mode advance` to step through phases, "
                f"or `/test-mode review` to inspect season status.{config_note}"
            )
            await interaction.followup.send(msg, ephemeral=True)
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode toggle | Success\n"
                f"  test_mode: enabled"
                + (f"\n  seeded_configs: {', '.join(new_configs)}" if new_configs else ""),
            )
        else:
            # Defer so the flush (multiple Discord API calls) has time to complete
            await interaction.response.defer(ephemeral=True)
            from services.forecast_cleanup_service import flush_pending_deletions
            await flush_pending_deletions(interaction.guild_id, self.bot)  # type: ignore[attr-defined]
            from services.test_roster_service import clear_all_test_drivers
            removed = await clear_all_test_drivers(interaction.guild_id, self.bot.db_path)  # type: ignore[attr-defined]
            if removed:
                log.info(
                    "Test mode disabled: cleared %d fake driver(s) for server %s",
                    removed,
                    interaction.guild_id,
                )
            msg = (
                "✅ Test mode **disabled**. "
                "The scheduler will resume normal operation for any remaining pending phases."
            )
            if removed:
                msg += f"\n🗑️ Removed **{removed}** fake driver(s)."
            await interaction.followup.send(msg, ephemeral=True)
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode toggle | Success\n"
                f"  test_mode: disabled"
                + (f"\n  removed_fake_drivers: {removed}" if removed else ""),
            )

    # ------------------------------------------------------------------
    # /test-mode advance
    # ------------------------------------------------------------------

    @test_mode.command(
        name="advance",
        description="Execute the next pending scheduled event (weather phase or result submission) immediately.",
    )
    @channel_guard
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
            self.bot.scheduler_service,  # type: ignore[attr-defined]
        )

        if entry is None:
            await interaction.followup.send(
                "ℹ️ All phases for all rounds and divisions have been executed. "
                "There is nothing left to advance.\n"
                "Use `/season complete` when all rounds are finalized to end the season.",
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
            # Cancel the scheduler job so it doesn't double-fire later
            if entry["job_id"] is not None:
                self.bot.scheduler_service.cancel_job(entry["job_id"])  # type: ignore[attr-defined]
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

        # ── Result submission (phase_number=4) ───────────────────────────────────
        if phase_number == 4:
            import asyncio
            from services.result_submission_service import (
                run_result_submission_job,
                is_submission_open,
            )
            from services.test_mode_service import is_round_finalized

            # Guard: if a submission channel is already open, the admin must complete
            # that submission before advancing to the next round.
            if await is_submission_open(self.bot.db_path, entry["round_id"]):  # type: ignore[attr-defined]
                # Check if we're in penalty-review state (results submitted but not finalized)
                if not await is_round_finalized(self.bot.db_path, entry["round_id"]):  # type: ignore[attr-defined]
                    await interaction.followup.send(
                        f"⏸️ **{entry['division_name']}** — **Round {entry['round_number']}** "
                        f"is awaiting penalty review approval. Please approve or dismiss the "
                        f"penalties in the submission channel before advancing.",
                        ephemeral=True,
                    )
                    return
                # Finalized (shouldn't normally reach here, but handle gracefully)
                await interaction.followup.send(
                    f"⏸️ Result submission for "
                    f"**{entry['division_name']}** — **Round {entry['round_number']}** "
                    f"is already in progress. Please submit results in the submission "
                    f"channel before advancing.",
                    ephemeral=True,
                )
                return

            # Always cancel any results_r job for this round — handles the case where
            # a real future-dated results_r job exists (e.g. weather-enabled season)
            # so it doesn't double-fire after advance has already triggered submission.
            self.bot.scheduler_service.cancel_job(f"results_r{entry['round_id']}")  # type: ignore[attr-defined]
            await interaction.followup.send(
                f"⏩ Opening result submission wizard for "
                f"**{entry['division_name']}** — **Round {entry['round_number']}** "
                f"(**{entry['track_name']}**). A submission channel will appear shortly.",
                ephemeral=True,
            )
            asyncio.create_task(run_result_submission_job(entry["round_id"], self.bot))
            return

        # ── Normal weather phase dispatch ───────────────────────────────────────
        phase_runners = {1: run_phase1, 2: run_phase2, 3: run_phase3}
        runner = phase_runners[phase_number]

        # Cancel the scheduler job before running so it doesn't double-fire later
        if entry["job_id"] is not None:
            self.bot.scheduler_service.cancel_job(entry["job_id"])  # type: ignore[attr-defined]

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
            self.bot.scheduler_service,  # type: ignore[attr-defined]
        )

        await interaction.followup.send(
            f"⏩ Advanced **Phase {phase_number}** for "
            f"**{entry['division_name']}** — **{entry['track_name']}**. "
            f"Outputs posted to the configured forecast and log channels.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode advance | Success\n"
            f"  phase: {phase_number}\n"
            f"  division: {entry['division_name']}\n"
            f"  track: {entry['track_name']}\n"
            f"  round: {entry['round_number']}",
        )
    # ------------------------------------------------------------------
    # /test-mode review
    # ------------------------------------------------------------------

    @test_mode.command(
        name="review",
        description="Show season configuration and phase completion status.",
    )
    @channel_guard
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

    # ------------------------------------------------------------------
    # /test-mode set-former-driver
    # ------------------------------------------------------------------

    @test_mode.command(
        name="set-former-driver",
        description="Manually set the former_driver flag on a driver profile (test mode only).",
    )
    @app_commands.describe(
        user="The driver whose flag is being updated.",
        value="The new value for the former_driver flag.",
    )
    @channel_guard
    @admin_only
    async def set_former_driver(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        value: bool,
    ) -> None:
        """Set former_driver flag — only available when test mode is active."""
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "⛔ This command is only available when test mode is enabled.",
                ephemeral=True,
            )
            return

        try:
            old_val, new_val = await self.bot.driver_service.set_former_driver(  # type: ignore[attr-defined]
                interaction.guild_id,
                str(user.id),
                value,
                interaction.user.id,
                str(interaction.user),
            )
        except ValueError as exc:
            await interaction.response.send_message(f"⛔ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ former_driver flag updated.\n"
            f"   User     : {user.display_name}\n"
            f"   Old value: {old_val}\n"
            f"   New value: {new_val}",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode set-former-driver | Success\n"
            f"  user: {user.display_name} (<@{user.id}>)\n"
            f"  former_driver: {old_val} -> {new_val}",
        )
        log.info(
            "set-former-driver on server %s: user=%s %s→%s by %s",
            interaction.guild_id, user.id, old_val, new_val, interaction.user,
        )

    # ------------------------------------------------------------------
    # /test-mode roster (subgroup)
    # ------------------------------------------------------------------

    roster = app_commands.Group(
        name="roster",
        description="Manage fake driver roster for test mode.",
        parent=test_mode,
        guild_only=True,
        default_permissions=None,
    )

    # /test-mode roster add ------------------------------------------------

    @roster.command(
        name="add",
        description="Add a fake driver to a team in a division.",
    )
    @app_commands.describe(
        driver_name="Display name for the fake driver.",
        team_name="Team to assign the driver to (must exist in the division).",
        division="Name of the division.",
    )
    @channel_guard
    @admin_only
    async def roster_add(
        self,
        interaction: discord.Interaction,
        driver_name: str,
        team_name: str,
        division: str,
    ) -> None:
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "⛔ This command is only available when test mode is enabled.",
                ephemeral=True,
            )
            return

        from services.test_roster_service import add_test_driver

        result = await add_test_driver(
            server_id=interaction.guild_id,
            driver_name=driver_name,
            team_name=team_name,
            division_name=division,
            db_path=self.bot.db_path,  # type: ignore[attr-defined]
        )

        if isinstance(result, str):
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)
            return

        mention_str = f"<@{result['discord_user_id']}>"
        await interaction.response.send_message(
            f"✅ Added fake driver **{result['display_name']}** to **{result['team_name']}**.\n"
            f"Mention string (copy-paste into results): `{mention_str}`",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode roster add | Success\n"
            f"  driver: {result['display_name']}\n"
            f"  team: {result['team_name']}\n"
            f"  division: {division}",
        )

    # /test-mode roster list -----------------------------------------------

    @roster.command(
        name="list",
        description="Show all fake drivers in a division (cheat sheet for result submission).",
    )
    @app_commands.describe(division="Name of the division.")
    @channel_guard
    @admin_only
    async def roster_list(
        self,
        interaction: discord.Interaction,
        division: str,
    ) -> None:
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "⛔ This command is only available when test mode is enabled.",
                ephemeral=True,
            )
            return

        from services.test_roster_service import list_test_drivers

        result = await list_test_drivers(
            server_id=interaction.guild_id,
            division_name=division,
            db_path=self.bot.db_path,  # type: ignore[attr-defined]
        )

        if isinstance(result, str):
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)
            return

        if not result:
            await interaction.response.send_message(
                f"ℹ️ No fake drivers in **{division}**. Use `/test-mode roster add` to create some.",
                ephemeral=True,
            )
            return

        lines = [f"**Fake Driver Roster — {division}**\n"]
        lines.append(f"{'Name':<20} {'Mention':<30} Team")
        lines.append("-" * 65)
        for driver in result:
            mention = f"<@{driver['discord_user_id']}>"
            lines.append(f"{driver['display_name']:<20} {mention:<30} {driver['team_name']}")
        lines.append(
            "\nCopy mention strings above when submitting results in the format:\n"
            "`Position, <@user_id>, <@&role_id>, ...`"
        )

        await interaction.response.send_message(
            "```\n" + "\n".join(lines) + "\n```",
            ephemeral=True,
        )

    # /test-mode roster clear ----------------------------------------------

    @roster.command(
        name="clear",
        description="Remove all fake drivers from a division.",
    )
    @app_commands.describe(division="Name of the division to clear.")
    @channel_guard
    @admin_only
    async def roster_clear(
        self,
        interaction: discord.Interaction,
        division: str,
    ) -> None:
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "⛔ This command is only available when test mode is enabled.",
                ephemeral=True,
            )
            return

        from services.test_roster_service import clear_test_drivers

        result = await clear_test_drivers(
            server_id=interaction.guild_id,
            division_name=division,
            db_path=self.bot.db_path,  # type: ignore[attr-defined]
        )

        if isinstance(result, str):
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)
            return

        if result == 0:
            await interaction.response.send_message(
                f"ℹ️ No fake drivers found in **{division}**.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"✅ Removed **{result}** fake driver(s) from **{division}**.", ephemeral=True
            )
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode roster clear | Success\n"
                f"  division: {division}\n"
                f"  removed_drivers: {result}",
            )

    # (submit-results removed — use /test-mode advance which now handles result submission)
