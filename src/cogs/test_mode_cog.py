"""TestModeCog — /test-mode command group.

Provides subcommands for system-level testing without waiting for the real
APScheduler triggers:

  /test-mode toggle            — enable or disable test mode (state persists)
  /test-mode nationality       — toggle whether mock drivers carry a nationality
  /test-mode advance           — immediately execute the next pending phase
  /test-mode review            — show season/round/phase status summary (ephemeral)
  /test-mode set-former-driver — set or clear a driver's former-driver flag
  /test-mode roster …          — manage the fake driver roster of a division
  /test-mode rsvp …            — set the RSVP status of fake drivers
  /test-mode backup …          — save the whole database, and put it back

All commands are gated by @channel_guard (interaction role + channel).
Every command but toggle additionally requires test mode to be active. Toggle itself
refuses to *enable* test mode while the server holds real drivers or its signup window
is open, and to *disable* it while a running season holds fake drivers.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services.test_mode_service import (
    toggle_test_mode,
    toggle_test_mode_nationality,
    count_live_real_drivers,
    count_test_drivers_in_a_started_season,
    get_next_pending_phase,
    build_review_summary,
)
from services import backup_service
from utils.channel_guard import channel_guard, admin_only, server_admin_only
from utils.message_builder import paginate_fenced

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
        """Flip test mode, refusing to enable it while the server holds real drivers.

        Test mode may not be switched on over a real league: disabling it again deletes
        every fake driver on the server without confirmation, and while it is on the
        signup and placement paths refuse real drivers outright, so a league that enabled
        it mid-season would find itself unable to run.

        Leaving is refused in one case only: a season that has started while holding fake
        drivers keeps test mode on until it is completed. Disabling deletes every fake
        driver, and one that has raced cannot be deleted — the check-in, the standings and
        the season's end each write a row carrying a foreign key to the profile — so the
        delete raised after the flag had already been flipped, stranding the server out of
        test mode with its roster still seated and no reply sent.

        An open signup window is refused for the same reason. `/signup open` will not run
        under test mode, and it would be inconsistent to leave a window already open —
        one whose button is posted and pinging the base role — silently rejecting every
        driver who pressed it. The window is not closed here: that posts a public notice
        in a channel a league reads, which is not something a flag flip should do.
        """
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is not None and not config.test_mode_active:
            real_drivers = await count_live_real_drivers(
                interaction.guild_id,
                self.bot.db_path,  # type: ignore[attr-defined]
            )
            if real_drivers:
                await interaction.response.send_message(
                    f"⛔ Test mode cannot be enabled while this server has "
                    f"**{real_drivers}** real driver(s).\n"
                    "Test mode is for an empty league — disabling it deletes every fake "
                    "driver, and while it is on no real driver may sign up or be placed.",
                    ephemeral=True,
                )
                return

            signups_open = await self.bot.signup_module_service.get_window_state(  # type: ignore[attr-defined]
                interaction.guild_id
            )
            if signups_open:
                await interaction.response.send_message(
                    "⛔ Test mode cannot be enabled while signups are open.\n"
                    "No real driver may sign up under test mode, so the button would "
                    "refuse everyone who pressed it. Close the window with "
                    "`/signup close` first.",
                    ephemeral=True,
                )
                return

        if config is not None and config.test_mode_active:
            seated = await count_test_drivers_in_a_started_season(
                interaction.guild_id,
                self.bot.db_path,  # type: ignore[attr-defined]
            )
            if seated:
                await interaction.response.send_message(
                    f"⛔ Test mode cannot be disabled while a running season holds "
                    f"**{seated}** fake driver(s).\n"
                    "Disabling deletes them, and a driver the season has raced cannot be "
                    "deleted. Finish the season with `/season complete` first.",
                    ephemeral=True,
                )
                return

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

            new_configs: list[str] = []
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
    # /test-mode nationality
    # ------------------------------------------------------------------

    @test_mode.command(
        name="nationality",
        description="Toggle whether mock drivers carry a nationality.",
    )
    @channel_guard
    @admin_only
    async def nationality(self, interaction: discord.Interaction) -> None:
        """The test-mode counterpart of /signup nationality.

        While test mode is active this switch stands in for the signup one everywhere the
        image module asks whether the league collects nationality, so the graphics of a
        server under test may be seen with flags and without them without the setting real
        signups run on being touched.
        """
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "⛔ This command is only available when test mode is enabled.",
                ephemeral=True,
            )
            return

        new_state = await toggle_test_mode_nationality(
            interaction.guild_id,
            self.bot.db_path,  # type: ignore[attr-defined]
        )
        note = (
            "Mock drivers may be given one with `/test-mode roster add`."
            if new_state
            else "Mock drivers already holding one keep it, and graphics draw no flag at all."
        )
        await interaction.response.send_message(
            f"✅ Test-mode nationality: **{'ON' if new_state else 'OFF'}**.\n{note}",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode nationality | Success\n"
            f"  test_mode_nationality: {'enabled' if new_state else 'disabled'}",
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
            from services.test_mode_service import round_result_status

            # Guard: if a submission channel is already open, the admin must complete
            # that submission before advancing to the next round.
            if await is_submission_open(self.bot.db_path, entry["round_id"]):  # type: ignore[attr-defined]
                # Results are not final until the appeals review is approved. A round sitting
                # awaiting appeal verdicts has had its report verdicts settled already,
                # so name whichever review is actually standing rather than always the first.
                status = await round_result_status(self.bot.db_path, entry["round_id"])  # type: ignore[attr-defined]
                if status != "FINAL":
                    review = (
                        "appeals review"
                        if status == "AWAITING_APPEAL_VERDICTS"
                        else "penalty review"
                    )
                    await interaction.followup.send(
                        f"⏸️ **{entry['division_name']}** — **Round {entry['round_number']}** "
                        f"is awaiting {review} approval. Please complete the {review} in the "
                        f"submission channel before advancing.",
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

        # ── RSVP notice (phase_number=5) ─────────────────────────────────────────
        if phase_number == 5:
            from services.rsvp_service import run_rsvp_notice
            if entry["job_id"] is not None:
                self.bot.scheduler_service.cancel_job(entry["job_id"])  # type: ignore[attr-defined]
            try:
                await run_rsvp_notice(entry["round_id"], self.bot)
            except Exception:
                log.exception(
                    "Test mode advance: unhandled error in rsvp_notice for round_id=%d",
                    entry["round_id"],
                )
                await interaction.followup.send(
                    f"❌ An internal error occurred while firing the RSVP notice for "
                    f"**{entry['division_name']}** — **Round {entry['round_number']}**. "
                    "Check the bot logs for details.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"📨 Fired **RSVP notice** for "
                f"**{entry['division_name']}** — **Round {entry['round_number']}** "
                f"(**{entry['track_name']}**). Embed posted to the RSVP channel.",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode advance | Success\n"
                f"  phase: rsvp_notice\n"
                f"  division: {entry['division_name']}\n"
                f"  round: {entry['round_number']}",
            )
            return

        # ── RSVP last-notice (phase_number=6) ────────────────────────────────────
        if phase_number == 6:
            from services.rsvp_service import run_rsvp_last_notice
            if entry["job_id"] is not None:
                self.bot.scheduler_service.cancel_job(entry["job_id"])  # type: ignore[attr-defined]
            try:
                await run_rsvp_last_notice(entry["round_id"], self.bot)
            except Exception:
                log.exception(
                    "Test mode advance: unhandled error in rsvp_last_notice for round_id=%d",
                    entry["round_id"],
                )
                await interaction.followup.send(
                    f"❌ An internal error occurred while firing the RSVP last-notice for "
                    f"**{entry['division_name']}** — **Round {entry['round_number']}**.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"⏰ Fired **RSVP last-notice** for "
                f"**{entry['division_name']}** — **Round {entry['round_number']}**.",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode advance | Success\n"
                f"  phase: rsvp_last_notice\n"
                f"  division: {entry['division_name']}\n"
                f"  round: {entry['round_number']}",
            )
            return

        # ── RSVP deadline (phase_number=7) ────────────────────────────────────────
        if phase_number == 7:
            from services.rsvp_service import run_rsvp_deadline
            if entry["job_id"] is not None:
                self.bot.scheduler_service.cancel_job(entry["job_id"])  # type: ignore[attr-defined]
            try:
                await run_rsvp_deadline(entry["round_id"], self.bot)
            except Exception:
                log.exception(
                    "Test mode advance: unhandled error in rsvp_deadline for round_id=%d",
                    entry["round_id"],
                )
                await interaction.followup.send(
                    f"❌ An internal error occurred while firing the RSVP deadline for "
                    f"**{entry['division_name']}** — **Round {entry['round_number']}**.",
                    ephemeral=True,
                )
                return
            await interaction.followup.send(
                f"🏁 Fired **RSVP deadline** for "
                f"**{entry['division_name']}** — **Round {entry['round_number']}**. "
                f"Reserve distribution complete.",
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode advance | Success\n"
                f"  phase: rsvp_deadline\n"
                f"  division: {entry['division_name']}\n"
                f"  round: {entry['round_number']}",
            )
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
            self.bot.scheduler_service,  # type: ignore[attr-defined]
        )
        # Discord message limit is 2000 characters; chunk if needed.
        chunks = [summary[i:i + 2000] for i in range(0, len(summary), 2000)]
        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

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
    # /test-mode backup (subgroup)
    # ------------------------------------------------------------------
    #
    # Under `/test-mode` because that is the only circumstance in which they run. They
    # copy and replace the whole database, and the test-mode gate below is what stands
    # between that and a league's history — putting them anywhere else would suggest they
    # were an ordinary administrative tool.

    backup = app_commands.Group(
        name="backup",
        description="Save the database while testing, and put it back.",
        parent=test_mode,
        guild_only=True,
        default_permissions=None,
    )

    async def _refuse_outside_test_mode(self, interaction: discord.Interaction) -> bool:
        """Reply and return True where the server is not in test mode.

        Read at the moment of the command rather than trusted from earlier: the flag can
        be turned off between one command and the next, and a restore is not something to
        run on the strength of a stale reading.
        """
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is not None and config.test_mode_active:
            return False
        await interaction.followup.send(
            "⛔ The backup commands run only while the server is in **test mode**. They "
            "copy and replace the whole database, which is not something to do to a "
            "league that is running. Turn test mode on with `/test-mode toggle` first.",
            ephemeral=True,
        )
        return True

    # ── save ──────────────────────────────────────────────────────────────

    @backup.command(
        name="save",
        description="Save the current database and scheduler as a backup.",
    )
    @channel_guard
    @server_admin_only
    async def backup_save(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        db_path = self.bot.db_path  # type: ignore[attr-defined]
        scheduler = getattr(self.bot, "scheduler_service", None)

        # Paused around the copy. APScheduler writes its jobstore on the event-loop
        # thread, so a job added mid-copy would be caught half-written; the backup API
        # makes that unlikely and this makes it impossible.
        paused = False
        try:
            if scheduler is not None and getattr(scheduler, "_scheduler", None) is not None:
                if scheduler._scheduler.running:
                    scheduler._scheduler.pause()
                    paused = True
            backup_service.save(db_path, _jobstore_path(self.bot))
        except backup_service.BackupError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return
        except Exception:
            log.exception("backup save: failed for server %s", interaction.guild_id)
            await interaction.followup.send(
                "⛔ The backup could not be taken. The log channel has the detail.",
                ephemeral=True,
            )
            return
        finally:
            if paused:
                scheduler._scheduler.resume()

        state = backup_service.state(db_path)
        await interaction.followup.send(
            f"✅ Saved. The backup holds {state.size_bytes // 1024} KB and replaces "
            f"whatever was there before.\n"
            f"Lock it with `/backup lock` if you want to keep this one.",
            ephemeral=True,
        )
        log.info(
            "backup save: server=%s by %s", interaction.guild_id, interaction.user
        )

    # ── lock ──────────────────────────────────────────────────────────────

    @backup.command(
        name="lock",
        description="Lock or unlock the saved backup, so a save cannot overwrite it.",
    )
    @channel_guard
    @server_admin_only
    async def backup_lock(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        db_path = self.bot.db_path  # type: ignore[attr-defined]
        if not backup_service.state(db_path).exists:
            await interaction.followup.send(
                "⛔ There is no saved backup to lock. Take one with `/backup save`.",
                ephemeral=True,
            )
            return

        locked = backup_service.set_lock(db_path, who=interaction.user.display_name)
        await interaction.followup.send(
            "🔒 Locked. `/backup save` will refuse to overwrite it until you run this "
            "again."
            if locked
            else "🔓 Unlocked. `/backup save` will overwrite it from now on.",
            ephemeral=True,
        )

    # ── status ────────────────────────────────────────────────────────────

    @backup.command(
        name="status",
        description="Show whether a backup exists, when it was taken, and if it is locked.",
    )
    @channel_guard
    @server_admin_only
    async def backup_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        state = backup_service.state(self.bot.db_path)  # type: ignore[attr-defined]
        if not state.exists:
            await interaction.followup.send(
                "📭 There is no saved backup. Take one with `/backup save`.",
                ephemeral=True,
            )
            return

        lines = [
            "📦 **Saved backup**",
            f"  Taken: {discord.utils.format_dt(state.taken_at, 'F')}",
            f"  Size: {state.size_bytes // 1024} KB",
            f"  Readable: {'yes' if state.readable else '**no — it cannot be restored**'}",
            f"  Locked: {state.locked_by if state.locked else 'no'}",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ── restore ───────────────────────────────────────────────────────────

    @backup.command(
        name="restore",
        description="Replace the database with the saved backup. Needs a restart.",
    )
    @channel_guard
    @server_admin_only
    async def backup_restore(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        state = backup_service.state(self.bot.db_path)  # type: ignore[attr-defined]
        if not state.exists:
            await interaction.followup.send(
                "⛔ There is no saved backup to restore. Take one with `/backup save`.",
                ephemeral=True,
            )
            return
        if not state.readable:
            await interaction.followup.send(
                "⛔ The saved backup is not a readable database, so it will not be "
                "restored. Take a fresh one with `/backup save`.",
                ephemeral=True,
            )
            return

        # Confirmed rather than done: everything the bot currently holds is about to be
        # replaced by a snapshot of some earlier moment, and the command that does it is
        # one word from `/backup save`.
        view = _ConfirmRestoreView(self, interaction.user.id)
        await interaction.followup.send(
            f"⚠️ **Restore the backup taken "
            f"{discord.utils.format_dt(state.taken_at, 'R')}?**\n"
            f"Everything the bot holds now — seasons, rounds, drivers, results and every "
            f"scheduled job — is replaced by what that backup holds. A copy of the current "
            f"database is kept beside it first.\n"
            f"**The bot must be restarted afterwards** to pick it up.",
            view=view,
            ephemeral=True,
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
        nationality="Optional. A nationality (e.g. British), a country name (e.g. United Kingdom), or 'other'.",
    )
    @channel_guard
    @admin_only
    async def roster_add(
        self,
        interaction: discord.Interaction,
        driver_name: str,
        team_name: str,
        division: str,
        nationality: str | None = None,
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

        # A nationality cannot be recorded while the switch is off, as the signup wizard
        # drops the question entirely rather than collecting an answer it will not keep.
        if nationality is not None and not config.test_mode_nationality_required:
            await interaction.response.send_message(
                "⛔ Nationality is switched off for test mode, so one cannot be recorded. "
                "Turn it on with `/test-mode nationality`, or leave the parameter out.",
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
            nationality=nationality,
        )

        if isinstance(result, str):
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)
            return

        mention_str = f"<@{result['discord_user_id']}>"
        nationality_line = (
            f"Nationality: **{result['nationality']}**\n" if result["nationality"] else ""
        )
        # The division is named because a team name repeats across divisions — every
        # division is seeded from the same server team list — so the team alone does not
        # say where the driver landed, which is the one thing the manager is choosing.
        await interaction.response.send_message(
            f"✅ Added fake driver **{result['display_name']}** to **{result['team_name']}** "
            f"in **{division}**.\n"
            f"{nationality_line}"
            f"Mention string (copy-paste into results): `{mention_str}`",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode roster add | Success\n"
            f"  driver: {result['display_name']}\n"
            f"  team: {result['team_name']}\n"
            f"  division: {division}\n"
            f"  nationality: {result['nationality'] or '(none)'}",
        )

    # /test-mode roster remove --------------------------------------------

    # /test-mode roster add-bulk -------------------------------------------

    @roster.command(
        name="add-bulk",
        description="Seat a whole roster from the generator's roster.csv.",
    )
    @channel_guard
    @admin_only
    async def roster_add_bulk(self, interaction: discord.Interaction) -> None:
        """Open the box a roster is pasted into.

        **Does not defer.** `send_modal` has to be the interaction's first response, which
        inverts the rule the rest of this cog follows — see `roster_add` above, which
        defers as normal. Do not "correct" it.
        """
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "⛔ This command is only available when test mode is enabled.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(_RosterImportModal(self))

    @roster.command(
        name="remove",
        description="Remove a single fake driver by their synthetic user ID.",
    )
    @app_commands.describe(
        user_id="Synthetic user ID of the fake driver (shown in /test-mode roster list or roster add).",
    )
    @channel_guard
    @admin_only
    async def roster_remove(
        self,
        interaction: discord.Interaction,
        user_id: str,
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

        try:
            discord_uid = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                "❌ `user_id` must be a numeric Discord user ID.", ephemeral=True
            )
            return

        from services.test_roster_service import remove_test_driver

        result = await remove_test_driver(
            server_id=interaction.guild_id,
            discord_user_id=discord_uid,
            db_path=self.bot.db_path,  # type: ignore[attr-defined]
        )

        if isinstance(result, str):
            await interaction.response.send_message(f"⛔ {result}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"✅ Removed fake driver **{result['display_name']}** from **{result['team_name']}**.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /test-mode roster remove | Success\n"
            f"  driver: {result['display_name']}\n"
            f"  team: {result['team_name']}\n"
            f"  user_id: {discord_uid}",
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
        # A division's roster outgrows one Discord message somewhere past twenty
        # drivers, and the listing is then sent as several — so the reply is deferred
        # and every page goes out through followup.
        await interaction.response.defer(ephemeral=True)

        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.followup.send(
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
            await interaction.followup.send(f"⛔ {result}", ephemeral=True)
            return

        if not result:
            await interaction.followup.send(
                f"ℹ️ No fake drivers in **{division}**. Use `/test-mode roster add` to create some.",
                ephemeral=True,
            )
            return

        body = [f"{'Name':<20} {'Mention':<30} {'Team':<20} Nationality", "-" * 86]
        for driver in result:
            mention = f"<@{driver['discord_user_id']}>"
            body.append(
                f"{driver['display_name']:<20} {mention:<30} "
                f"{driver['team_name']:<20} {driver['nationality'] or '—'}"
            )

        for page in paginate_fenced(
            header=f"**Fake Driver Roster — {division}**",
            body_lines=body,
            footer=(
                "Copy mention strings above when submitting results in the format:\n"
                "`Position, <@user_id>, <@&role_id>, ...`"
            ),
        ):
            await interaction.followup.send(page, ephemeral=True)

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

    # ------------------------------------------------------------------
    # /test-mode rsvp (subgroup) — T029
    # ------------------------------------------------------------------

    rsvp = app_commands.Group(
        name="rsvp",
        description="Test-mode RSVP utilities.",
        parent=test_mode,
        guild_only=True,
        default_permissions=None,
    )

    # /test-mode rsvp set-status ------------------------------------------

    @rsvp.command(
        name="set-status",
        description="Bulk-set RSVP statuses for test drivers in a division via a modal.",
    )
    @app_commands.describe(
        division="Division name whose active RSVP round to update.",
    )
    @channel_guard
    @admin_only
    async def rsvp_set_status(
        self,
        interaction: discord.Interaction,
        division: str,
    ) -> None:
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "ℹ️ Test mode is not active.", ephemeral=True
            )
            return

        guild_id: int = interaction.guild_id  # type: ignore[assignment]

        from db.database import get_connection as _gc

        # Validate division exists in the active season and has an active RSVP embed
        async with _gc(self.bot.db_path) as db:  # type: ignore[attr-defined]
            cur = await db.execute(
                """
                SELECT d.id AS division_id
                  FROM divisions d
                  JOIN seasons s ON s.id = d.season_id
                 WHERE s.server_id = ? AND s.status = 'ACTIVE'
                   AND LOWER(d.name) = LOWER(?)
                """,
                (guild_id, division),
            )
            div_row = await cur.fetchone()

        if div_row is None:
            await interaction.response.send_message(
                f"❌ Division **{division}** not found in the active season.", ephemeral=True
            )
            return
        division_id: int = div_row["division_id"]

        embed_rows = await self.bot.attendance_service.get_all_embed_messages()  # type: ignore[attr-defined]
        target_embed = next((r for r in embed_rows if r.division_id == division_id), None)
        if target_embed is None:
            await interaction.response.send_message(
                f"❌ No active RSVP embed found for division **{division}**. "
                "Run `/test-mode advance` to fire the RSVP notice first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_modal(
            _RsvpBulkSetModal(
                division_name=division,
                division_id=division_id,
                round_id=target_embed.round_id,
                embed_channel_id=int(target_embed.channel_id),
                embed_message_id=int(target_embed.message_id),
                bot=self.bot,
            )
        )


# ---------------------------------------------------------------------------
# Modal: bulk RSVP status setter
# ---------------------------------------------------------------------------

_STATUS_MAP = {
    "accept":   "ACCEPTED",
    "accepted": "ACCEPTED",
    "tentative": "TENTATIVE",
    "decline":  "DECLINED",
    "declined": "DECLINED",
}


class _RsvpBulkSetModal(discord.ui.Modal, title="Bulk Set RSVP Statuses"):
    """Modal for bulk-setting test-driver RSVP statuses in a single division."""

    entries: discord.ui.TextInput = discord.ui.TextInput(
        label="ID, status — one entry per line",
        style=discord.TextStyle.paragraph,
        placeholder="900000001, accept\n900000002, tentative\n900000003, decline",
        required=True,
        max_length=4000,
    )

    def __init__(
        self,
        *,
        division_name: str,
        division_id: int,
        round_id: int,
        embed_channel_id: int,
        embed_message_id: int,
        bot: commands.Bot,
    ) -> None:
        super().__init__()
        self._division_name = division_name
        self._division_id = division_id
        self._round_id = round_id
        self._embed_channel_id = embed_channel_id
        self._embed_message_id = embed_message_id
        self._bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True)

        from db.database import get_connection as _gc
        from services.rsvp_service import _rebuild_embed_for_round, RsvpView

        guild_id: int = interaction.guild_id  # type: ignore[assignment]
        applied: list[str] = []
        errors: list[str] = []

        for line_no, raw_line in enumerate(self.entries.value.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            parts = [p.strip() for p in line.split(",", 1)]
            if len(parts) != 2:
                errors.append(f"Line {line_no}: expected `ID, status` — got `{line}`")
                continue
            id_str, status_str = parts
            try:
                discord_uid = int(id_str)
            except ValueError:
                errors.append(f"Line {line_no}: `{id_str}` is not a valid numeric ID")
                continue
            new_status = _STATUS_MAP.get(status_str.lower())
            if new_status is None:
                errors.append(
                    f"Line {line_no}: unknown status `{status_str}` "
                    "(use accept, tentative, or decline)"
                )
                continue

            async with _gc(self._bot.db_path) as db:  # type: ignore[attr-defined]
                cur = await db.execute(
                    "SELECT id FROM driver_profiles "
                    "WHERE server_id = ? AND CAST(discord_user_id AS INTEGER) = ?",
                    (guild_id, discord_uid),
                )
                profile_row = await cur.fetchone()

            if profile_row is None:
                errors.append(f"Line {line_no}: no driver profile for ID `{id_str}`")
                continue
            driver_profile_id: int = profile_row["id"]

            dra = await self._bot.attendance_service.get_attendance_row_for_driver(  # type: ignore[attr-defined]
                round_id=self._round_id,
                division_id=self._division_id,
                driver_profile_id=driver_profile_id,
            )
            if dra is None:
                errors.append(
                    f"Line {line_no}: driver `{id_str}` has no attendance row for this round"
                )
                continue

            await self._bot.attendance_service.upsert_rsvp_status(  # type: ignore[attr-defined]
                round_id=self._round_id,
                division_id=self._division_id,
                driver_profile_id=driver_profile_id,
                status=new_status,
            )
            applied.append(f"`{id_str}` → {new_status.lower()}")

        # Rebuild embed once after all updates
        if applied:
            channel = self._bot.get_channel(self._embed_channel_id)
            if channel is not None:
                try:
                    msg = await channel.fetch_message(self._embed_message_id)
                    new_embed = await _rebuild_embed_for_round(
                        self._round_id, self._division_id, self._bot
                    )
                    await msg.edit(embed=new_embed, view=RsvpView(round_id=self._round_id))
                except Exception as exc:
                    log.warning("_RsvpBulkSetModal: failed to edit embed: %s", exc)

        lines: list[str] = []
        if applied:
            lines.append(
                f"✅ Applied {len(applied)} update(s) in **{self._division_name}**:\n"
                + "\n".join(f"  {a}" for a in applied)
            )
        if errors:
            lines.append("⚠️ Errors:\n" + "\n".join(f"  • {e}" for e in errors))
        await interaction.followup.send("\n".join(lines) or "No valid entries.", ephemeral=True)

        if applied:
            await self._bot.output_router.post_log(  # type: ignore[attr-defined]
                guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                f"| /test-mode rsvp set-status | {len(applied)} update(s)\n"
                f"  division: {self._division_name}\n"
                f"  round_id: {self._round_id}\n"
                f"  changes: {', '.join(applied)}",
            )


class _RosterImportModal(discord.ui.Modal, title="Import a test roster"):
    """The box a `roster.csv` is pasted into.

    **4000 characters is the whole of what Discord allows a text input**, which is roughly
    sixty drivers at the width the generator writes. A larger roster is imported in two
    passes — the import appends to what a season already holds, and refuses only a
    *division* that already holds drivers, so a second paste of the remaining divisions
    lands cleanly.
    """

    # The placeholder is capped at 100 characters by Discord, which is not room for an
    # example row; it names the file instead, and the label carries the shape.
    csv_text: discord.ui.TextInput = discord.ui.TextInput(
        label="Paste roster.csv, header and all",
        style=discord.TextStyle.paragraph,
        placeholder="ID,Driver name,Team,Division,Nationality",
        required=True,
        max_length=4000,
    )

    def __init__(self, cog: "TestModeCog") -> None:
        super().__init__()
        self._cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from services.test_roster_service import add_test_drivers_in_bulk
        from utils.roster_import import divisions_named, parse_roster_csv

        await interaction.response.defer(ephemeral=True)

        drivers, errors = parse_roster_csv(str(self.csv_text.value))
        if errors:
            await interaction.followup.send(
                _format_roster_errors(errors), ephemeral=True
            )
            return

        seated, errors = await add_test_drivers_in_bulk(
            interaction.guild_id,
            drivers,
            self._cog.bot.db_path,  # type: ignore[attr-defined]
        )
        if errors:
            await interaction.followup.send(
                _format_roster_errors(errors), ephemeral=True
            )
            return

        divisions = divisions_named(drivers)
        await interaction.followup.send(
            f"✅ Seated **{seated}** test driver(s) across "
            f"{len(divisions)} division(s): {', '.join(divisions)}.\n"
            f"`/test-mode roster list` shows a division's drivers with the mentions "
            f"result submission wants.",
            ephemeral=True,
        )


def _format_roster_errors(errors: list[str]) -> str:
    """One shape for every refusal, capped so a systematically wrong file still replies.

    A file whose every row is wrong produces one fault per row, and fifty-one of them
    exceed Discord's 2000-character limit — which refuses the whole message rather than
    truncating it, leaving the manager with no reply at all.
    """
    shown = errors[:15]
    lines = [
        f"⛔ The roster was not imported — {len(errors)} problem(s). "
        f"**No drivers were added.**"
    ]
    lines += [f"• {problem}" for problem in shown]
    if len(errors) > len(shown):
        lines.append(f"…and {len(errors) - len(shown)} more.")
    return "\n".join(lines)


#: Re-exported from the backup service, which owns it now that the season approval
#: needs it too. Kept as a name here because this module's tests import it.
_jobstore_path = backup_service.jobstore_path_of


class _ConfirmRestoreView(discord.ui.View):
    """The confirmation on a restore, and the staging behind it."""

    def __init__(self, cog: "TestModeCog", requester_id: int) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._requester_id = requester_id

    @discord.ui.button(label="♻️ Restore", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "⛔ Only the person who ran the command can confirm it.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        bot = self._cog.bot
        try:
            backup_service.stage_restore(bot.db_path, _jobstore_path(bot))  # type: ignore[attr-defined]
        except backup_service.BackupError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            self.stop()
            return
        except Exception:
            log.exception("backup restore: staging failed")
            await interaction.followup.send(
                "⛔ The restore could not be prepared. Nothing has been changed.",
                ephemeral=True,
            )
            self.stop()
            return

        await interaction.followup.send(
            "✅ The backup is staged. **Restart the bot** and it will come back on the "
            "restored database — under a service it will restart itself; from a terminal, "
            "stop it and run it again.\n"
            "The database being replaced was copied to `bot.prerestore.db` first, so this "
            "can be walked back by hand if it was not what you wanted.",
            ephemeral=True,
        )
        log.info("backup restore: staged by %s", interaction.user)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Nothing has been changed.", ephemeral=True
        )
        self.stop()
