"""ModuleCog — /module enable and /module disable commands.

Manages the weather and signup modules for each server.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from models.driver_profile import DriverState
from utils.channel_guard import admin_only, channel_guard, server_admin_only

log = logging.getLogger(__name__)

_MODULE_CHOICES = [
    app_commands.Choice(name="weather", value="weather"),
    app_commands.Choice(name="signup", value="signup"),
    app_commands.Choice(name="results", value="results"),
    app_commands.Choice(name="attendance", value="attendance"),
]

# ---------------------------------------------------------------------------
# Shared forced-close sub-flow (called by both module_cog and signup_cog)
# ---------------------------------------------------------------------------


async def execute_forced_close(server_id: int, bot: commands.Bot, *, audit_action: str) -> None:
    """Force-close the signup window.

    1. Transition in-progress drivers to NOT_SIGNED_UP.
    2. Delete signup button message (graceful NotFound).
    3. Post "signups are closed" to signup channel.
    4. Set window closed.
    5. Emit audit entry.
    """
    cfg = await bot.signup_module_service.get_config(server_id)
    if cfg is None:
        return

    # 1. Transition in-progress drivers (only PENDING_SIGNUP_COMPLETION; approved/correcting
    #    drivers retain their state per FR-002/FR-003)
    in_progress_states = {
        DriverState.PENDING_SIGNUP_COMPLETION,
    }
    async with get_connection(bot.db_path) as db:
        placeholders = ",".join("?" for _ in in_progress_states)
        cursor = await db.execute(
            f"SELECT discord_user_id FROM driver_profiles "
            f"WHERE server_id = ? AND current_state IN ({placeholders})",
            (server_id, *[s.value for s in in_progress_states]),
        )
        rows = await cursor.fetchall()

    for row in rows:
        try:
            await bot.driver_service.transition(
                server_id, row["discord_user_id"], DriverState.NOT_SIGNED_UP
            )
        except Exception:
            log.exception("forced_close: failed to transition driver %s", row["discord_user_id"])

    # T046: cancel wizard APScheduler jobs for each force-transitioned driver
    svc = bot.scheduler_service  # type: ignore[attr-defined]
    for row in rows:
        uid = row["discord_user_id"]
        for prefix in ("wizard_inactivity", "wizard_channel_delete"):
            try:
                svc._scheduler.remove_job(f"{prefix}_{server_id}_{uid}")
            except Exception:
                pass  # Job already fired or never existed

    # Post cancellation notice in each wizard channel and schedule deletion.
    # This mirrors the withdraw() path so drivers see a message and the channel
    # is cleaned up after a 24-hour hold.
    _guild = bot.get_guild(server_id)
    if _guild is not None:
        _wizard_svc = bot.wizard_service  # type: ignore[attr-defined]
        for row in rows:
            try:
                await _wizard_svc._trigger_channel_hold(
                    server_id, row["discord_user_id"], _guild,
                    "🔒 Signups have closed. This channel will be automatically deleted in 24 hours.",
                )
            except Exception:
                log.exception("forced_close: _trigger_channel_hold failed for driver %s", row["discord_user_id"])

    # 2. Delete button message
    if cfg.signup_button_message_id:
        guild = bot.get_guild(server_id)
        if guild:
            channel = guild.get_channel(cfg.signup_channel_id)
            if channel:
                try:
                    msg = await channel.fetch_message(cfg.signup_button_message_id)
                    await msg.delete()
                except discord.NotFound:
                    pass
                except Exception:
                    log.exception("forced_close: could not delete button message")

    # 3. Post closed message; capture ID so it can be deleted when re-opening
    closed_msg_id: int | None = None
    guild = bot.get_guild(server_id)
    if guild:
        channel = guild.get_channel(cfg.signup_channel_id)
        if channel:
            try:
                closed_msg = await channel.send("🔒 Signups are now closed.")
                closed_msg_id = closed_msg.id
            except Exception:
                log.exception("forced_close: could not post closed message")

    # 4. Set window closed (persists closed_msg_id)
    await bot.signup_module_service.set_window_closed(server_id, closed_msg_id=closed_msg_id)

    # 5. Audit entry
    now = datetime.now(timezone.utc).isoformat()
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "INSERT INTO audit_entries "
            "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?)",
            (server_id, 0, "system", audit_action, "open", "closed", now),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# ModuleCog
# ---------------------------------------------------------------------------


class ModuleCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    module = app_commands.Group(
        name="module",
        description="Enable or disable bot modules for this server.",
        default_permissions=None,
    )

    # ── /module enable ─────────────────────────────────────────────────

    @module.command(
        name="enable",
        description="Enable a bot module for this server.",
    )
    @app_commands.describe(
        module_name="Module to enable",
    )
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @channel_guard
    @server_admin_only
    async def enable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if module_name.value == "weather":
            await self._enable_weather(interaction, server_id)
        elif module_name.value == "results":
            await self._enable_results(interaction, server_id)
        elif module_name.value == "attendance":
            await self._enable_attendance(interaction, server_id)
        else:
            await self._enable_signup(interaction, server_id)

    # ── /module disable ────────────────────────────────────────────────

    @module.command(
        name="disable",
        description="Disable a bot module for this server.",
    )
    @app_commands.describe(module_name="Module to disable")
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @channel_guard
    @server_admin_only
    async def disable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if module_name.value == "weather":
            await self._disable_weather(interaction, server_id)
        elif module_name.value == "results":
            await self._disable_results(interaction, server_id)
        elif module_name.value == "attendance":
            await self._disable_attendance(interaction)
        else:
            await self._disable_signup(interaction, server_id)

    # ── Weather enable (T011) ──────────────────────────────────────────

    async def _enable_weather(
        self, interaction: discord.Interaction, server_id: int
    ) -> None:
        # 1. Guard already-enabled
        if await self.bot.module_service.is_weather_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Weather module is already enabled.", ephemeral=True
            )
            return

        # 2. Validate all active-season divisions have forecast_channel_id
        season = await self.bot.season_service.get_active_season(server_id)
        if season:
            divisions = await self.bot.season_service.get_divisions(season.id)
            missing = [d.name for d in divisions if not d.forecast_channel_id]
            if missing:
                names = ", ".join(f"**{n}**" for n in missing)
                await interaction.response.send_message(
                    f"❌ Weather module cannot be enabled — the following divisions are missing "
                    f"a forecast channel: {names}. Add a forecast channel to each division first.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        # 3. Atomically set flag + audit
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with get_connection(self.bot.db_path) as db:
                await db.execute(
                    "UPDATE server_configs SET weather_module_enabled = 1 WHERE server_id = ?",
                    (server_id,),
                )
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                    "VALUES (?, ?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
                    (server_id, interaction.user.id, str(interaction.user),
                     json.dumps({"module": "weather"}), now),
                )
                await db.commit()
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Weather module enable failed: {exc}. Module remains disabled.",
                ephemeral=True,
            )
            return

        # 4. Run catch-up phases and schedule future jobs
        if season:
            try:
                await self._catchup_and_schedule_weather(server_id, season)
            except Exception as exc:
                log.exception("Weather enable catch-up failed for server %s", server_id)
                # Rollback: cancel any partially-created jobs, reset flag
                await self.bot.scheduler_service.cancel_all_weather_for_server(server_id)
                async with get_connection(self.bot.db_path) as db:
                    await db.execute(
                        "UPDATE server_configs SET weather_module_enabled = 0 WHERE server_id = ?",
                        (server_id,),
                    )
                    await db.commit()
                await interaction.followup.send(
                    f"❌ Weather module enable failed during phase execution: {exc}. "
                    "Module remains disabled.",
                    ephemeral=True,
                )
                return

        # 5. Post log channel confirmation
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable weather | Success",
        )
        await interaction.followup.send("✅ Weather module enabled.", ephemeral=True)

    async def _catchup_and_schedule_weather(self, server_id: int, season: object) -> None:
        """Run any overdue phase horizons and schedule future ones."""
        from services.phase1_service import run_phase1
        from services.phase2_service import run_phase2
        from services.phase3_service import run_phase3
        from services.weather_config_service import get_weather_pipeline_config
        from models.round import RoundFormat

        cfg = await get_weather_pipeline_config(self.bot.db_path, server_id)

        now = datetime.now(timezone.utc)
        divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[union-attr]
        season_number: int = getattr(season, "season_number", 0)
        div_meta: dict[int, tuple[int, int]] = {
            div.id: (season_number, div.tier) for div in divisions
        }
        all_rounds = []
        for div in divisions:
            rounds = await self.bot.season_service.get_division_rounds(div.id)
            all_rounds.extend(rounds)

        for rnd in all_rounds:
            scheduled_at = rnd.scheduled_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

            # Catch-up phase execution only applies to non-mystery rounds
            if rnd.format != RoundFormat.MYSTERY:
                p1_horizon = scheduled_at - timedelta(days=cfg.phase_1_days)
                p2_horizon = scheduled_at - timedelta(days=cfg.phase_2_days)
                p3_horizon = scheduled_at - timedelta(hours=cfg.phase_3_hours)

                if not rnd.phase1_done and now >= p1_horizon:
                    log.info("Weather enable catch-up: Phase 1 for round %s", rnd.id)
                    await run_phase1(rnd.id, self.bot)
                if not rnd.phase2_done and now >= p2_horizon:
                    log.info("Weather enable catch-up: Phase 2 for round %s", rnd.id)
                    await run_phase2(rnd.id, self.bot)
                if not rnd.phase3_done and now >= p3_horizon:
                    log.info("Weather enable catch-up: Phase 3 for round %s", rnd.id)
                    await run_phase3(rnd.id, self.bot)

            s_num, d_tier = div_meta.get(rnd.division_id, (0, 0))
            self.bot.scheduler_service.schedule_round(
                rnd,
                season_number=s_num,
                division_tier=d_tier,
                phase_1_days=cfg.phase_1_days,
                phase_2_days=cfg.phase_2_days,
                phase_3_hours=cfg.phase_3_hours,
            )

    # ── Weather disable (T012) ─────────────────────────────────────────

    async def _disable_weather(
        self, interaction: discord.Interaction, server_id: int
    ) -> None:
        if not await self.bot.module_service.is_weather_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Weather module is already disabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        await self.bot.scheduler_service.cancel_all_weather_for_server(server_id)
        await self.bot.module_service.set_weather_enabled(server_id, False)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"module": "weather"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable weather | Success",
        )
        await interaction.followup.send(
            "✅ Weather module disabled. All scheduled weather jobs have been cancelled.",
            ephemeral=True,
        )

    # ── Results & Standings enable ─────────────────────────────────────

    async def _enable_results(
        self, interaction: discord.Interaction, server_id: int
    ) -> None:
        # 1. Guard already-enabled
        if await self.bot.module_service.is_results_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Results & Standings module is already enabled.", ephemeral=True
            )
            return

        # 2. Block if ACTIVE season exists (FR-003)
        active_season = await self.bot.season_service.get_active_season(server_id)
        if active_season is not None:
            await interaction.response.send_message(
                "❌ Results & Standings module cannot be enabled while a season is active.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # 3. Atomically set flag + audit
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results_module_config (server_id, module_enabled) VALUES (?, 1)",
                (server_id,),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"module": "results"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable results | Success",
        )
        await interaction.followup.send("✅ Results & Standings module enabled.", ephemeral=True)

    # ── Results & Standings disable ────────────────────────────────────

    async def _disable_results(
        self, interaction: discord.Interaction, server_id: int
    ) -> None:
        if not await self.bot.module_service.is_results_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Results & Standings module is already disabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO results_module_config (server_id, module_enabled) VALUES (?, 0)",
                (server_id,),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"module": "results"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable results | Success",
        )
        await interaction.followup.send(
            "✅ Results & Standings module disabled.", ephemeral=True
        )

        # Cascade: disable attendance if it is currently enabled
        if await self.bot.module_service.is_attendance_enabled(server_id):
            await self._disable_attendance(interaction, cascade=True)

    # ── Attendance enable ──────────────────────────────────────────────

    async def _enable_attendance(
        self, interaction: discord.Interaction, server_id: int
    ) -> None:
        # 1. Guard: R&S must be enabled first
        if not await self.bot.module_service.is_results_enabled(server_id):
            await interaction.response.send_message(
                "❌ The Attendance module requires the Results & Standings module to be enabled first.",
                ephemeral=True,
            )
            return

        # 2. Guard: no ACTIVE season
        active_season = await self.bot.season_service.get_active_season(server_id)
        if active_season is not None:
            await interaction.response.send_message(
                "❌ Attendance module cannot be enabled while a season is active.",
                ephemeral=True,
            )
            return

        # 3. Guard: already enabled
        if await self.bot.module_service.is_attendance_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Attendance module is already enabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # 4. Atomically insert config row with defaults + audit entry
        now = datetime.now(timezone.utc).isoformat()
        try:
            async with get_connection(self.bot.db_path) as db:
                await db.execute(
                    "INSERT OR REPLACE INTO attendance_config "
                    "(server_id, module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
                    "rsvp_deadline_hours, no_rsvp_penalty, no_rsvp_absent_penalty, rsvp_absent_penalty, "
                    "autoreserve_threshold, autosack_threshold) "
                    "VALUES (?, 1, 5, 24, 2, 1, 1, 1, NULL, NULL)",
                    (server_id,),
                )
                await db.execute(
                    "INSERT INTO audit_entries "
                    "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                    "VALUES (?, ?, ?, NULL, 'ATTENDANCE_MODULE_ENABLED', '', '', ?)",
                    (server_id, interaction.user.id, str(interaction.user), now),
                )
                await db.commit()
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Attendance module enable failed: {exc}. Module remains disabled.",
                ephemeral=True,
            )
            return

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable attendance | Success",
        )
        await interaction.followup.send("✅ Attendance module enabled.", ephemeral=True)

    # ── Attendance disable ─────────────────────────────────────────────

    async def _disable_attendance(
        self, interaction: discord.Interaction, *, cascade: bool = False
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if not cascade:
            if not await self.bot.module_service.is_attendance_enabled(server_id):
                await interaction.response.send_message(
                    "⚠️ Attendance module is already disabled.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)

        change_type = (
            "ATTENDANCE_MODULE_CASCADE_DISABLED" if cascade else "ATTENDANCE_MODULE_DISABLED"
        )
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET module_enabled = 0 WHERE server_id = ?",
                (server_id,),
            )
            await db.execute(
                "DELETE FROM attendance_division_config WHERE server_id = ?",
                (server_id,),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, ?, '', '', ?)",
                (server_id, interaction.user.id, str(interaction.user), change_type, now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable attendance | Success",
        )
        if not cascade:
            await interaction.followup.send(
                "✅ Attendance module disabled.", ephemeral=True
            )

    # ── Signup enable (T010) ───────────────────────────────────────────

    async def _enable_signup(
        self,
        interaction: discord.Interaction,
        server_id: int,
    ) -> None:
        # Guard already-enabled
        if await self.bot.module_service.is_signup_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Signup module is already enabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        # Upsert a bare config row — channel/role fields all NULL
        from models.signup_module import SignupModuleConfig
        new_cfg = SignupModuleConfig(
            server_id=server_id,
            signup_channel_id=None,
            base_role_id=None,
            signed_up_role_id=None,
            signups_open=False,
            signup_button_message_id=None,
            selected_tracks=[],
        )
        await self.bot.signup_module_service.save_config(new_cfg)

        # Set enabled + audit
        await self.bot.module_service.set_signup_enabled(server_id, True)
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'MODULE_ENABLE', '', ?, ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"module": "signup"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module enable signup | Success",
        )
        await interaction.followup.send(
            "✅ Signup module enabled.\n"
            "Next steps — configure with:\n"
            "  • `/signup channel <channel>`\n"
            "  • `/signup base-role <role>`\n"
            "  • `/signup complete-role <role>`",
            ephemeral=True,
        )

    # ── Signup disable (T018) ──────────────────────────────────────────

    async def _disable_signup(
        self, interaction: discord.Interaction, server_id: int
    ) -> None:
        if not await self.bot.module_service.is_signup_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Signup module is already disabled.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        signup_cfg = await self.bot.signup_module_service.get_config(server_id)

        # Force-close if signups are open
        if signup_cfg and signup_cfg.signups_open:
            await execute_forced_close(server_id, self.bot, audit_action="SIGNUP_FORCE_CLOSE")

        # Cancel any active signup close timer
        self.bot.scheduler_service.cancel_signup_close_timer(server_id)

        # Remove bot-applied permission overwrites (only those set by /signup channel)
        if signup_cfg and signup_cfg.signup_channel_id is not None:
            guild = self.bot.get_guild(server_id)
            if guild:
                channel = guild.get_channel(signup_cfg.signup_channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    targets_to_revert = [guild.default_role, guild.me]
                    if signup_cfg.base_role_id is not None:
                        base_role = guild.get_role(signup_cfg.base_role_id)
                        if base_role:
                            targets_to_revert.append(base_role)
                    server_cfg = await self.bot.config_service.get_server_config(server_id)
                    if server_cfg:
                        interaction_role = guild.get_role(server_cfg.interaction_role_id)
                        if interaction_role:
                            targets_to_revert.append(interaction_role)
                    for target in targets_to_revert:
                        try:
                            await channel.set_permissions(target, overwrite=None)
                        except Exception:
                            log.exception(
                                "disable_signup: could not clear overwrite for %s", target
                            )

        # Cancel all wizard inactivity and channel-delete APScheduler jobs for this server
        if signup_cfg:
            active_wizards = await self.bot.signup_module_service.get_all_active_wizards(
                server_id
            )
            scheduler = self.bot.scheduler_service._scheduler
            for wiz in active_wizards:
                for prefix in ("wizard_inactivity", "wizard_channel_delete"):
                    job_id = f"{prefix}_{server_id}_{wiz.discord_user_id}"
                    try:
                        scheduler.remove_job(job_id)
                    except Exception:
                        pass

        # Delete config (cascades to settings + slots)
        await self.bot.signup_module_service.delete_config(server_id)

        # Set disabled + audit
        await self.bot.module_service.set_signup_enabled(server_id, False)
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, NULL, 'MODULE_DISABLE', ?, '', ?)",
                (server_id, interaction.user.id, str(interaction.user),
                 json.dumps({"module": "signup"}), now),
            )
            await db.commit()

        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /module disable signup | Success",
        )
        await interaction.followup.send(
            "✅ Signup module disabled. All signup configuration has been cleared.",
            ephemeral=True,
        )
