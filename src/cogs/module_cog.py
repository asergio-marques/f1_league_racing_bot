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
from utils.channel_guard import admin_only, channel_guard

log = logging.getLogger(__name__)

_MODULE_CHOICES = [
    app_commands.Choice(name="weather", value="weather"),
    app_commands.Choice(name="signup", value="signup"),
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

    # 1. Transition in-progress drivers
    in_progress_states = {
        DriverState.PENDING_SIGNUP_COMPLETION,
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.PENDING_DRIVER_CORRECTION,
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
        channel="(signup only) The channel designated for signup interactions",
        base_role="(signup only) Role granted to members eligible to sign up",
        signed_up_role="(signup only) Role granted on successful signup completion",
    )
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @channel_guard
    @admin_only
    async def enable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
        channel: discord.TextChannel | None = None,
        base_role: discord.Role | None = None,
        signed_up_role: discord.Role | None = None,
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if module_name.value == "weather":
            await self._enable_weather(interaction, server_id)
        else:
            await self._enable_signup(interaction, server_id, channel, base_role, signed_up_role)

    # ── /module disable ────────────────────────────────────────────────

    @module.command(
        name="disable",
        description="Disable a bot module for this server.",
    )
    @app_commands.describe(module_name="Module to disable")
    @app_commands.choices(module_name=_MODULE_CHOICES)
    @channel_guard
    @admin_only
    async def disable(
        self,
        interaction: discord.Interaction,
        module_name: app_commands.Choice[str],
    ) -> None:
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if module_name.value == "weather":
            await self._disable_weather(interaction, server_id)
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
        self.bot.output_router.post_log(server_id, "✅ Weather module **enabled**.")
        await interaction.followup.send("✅ Weather module enabled.", ephemeral=True)

    async def _catchup_and_schedule_weather(self, server_id: int, season: object) -> None:
        """Run any overdue phase horizons and schedule future ones."""
        from services.phase1_service import run_phase1
        from services.phase2_service import run_phase2
        from services.phase3_service import run_phase3
        from models.round import RoundFormat

        now = datetime.now(timezone.utc)
        divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[union-attr]
        all_rounds = []
        for div in divisions:
            rounds = await self.bot.season_service.get_division_rounds(div.id)
            all_rounds.extend(rounds)

        for rnd in all_rounds:
            if rnd.format == RoundFormat.MYSTERY:
                continue

            scheduled_at = rnd.scheduled_at
            if scheduled_at.tzinfo is None:
                scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

            p1_horizon = scheduled_at - timedelta(days=5)
            p2_horizon = scheduled_at - timedelta(days=2)
            p3_horizon = scheduled_at - timedelta(hours=2)

            if not rnd.phase1_done and now >= p1_horizon:
                log.info("Weather enable catch-up: Phase 1 for round %s", rnd.id)
                await run_phase1(rnd.id, self.bot)
            if not rnd.phase2_done and now >= p2_horizon:
                log.info("Weather enable catch-up: Phase 2 for round %s", rnd.id)
                await run_phase2(rnd.id, self.bot)
            if not rnd.phase3_done and now >= p3_horizon:
                log.info("Weather enable catch-up: Phase 3 for round %s", rnd.id)
                await run_phase3(rnd.id, self.bot)

            self.bot.scheduler_service.schedule_round(rnd)

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

        self.bot.output_router.post_log(
            server_id, "✅ Weather module **disabled**. All scheduled weather jobs cancelled."
        )
        await interaction.followup.send(
            "✅ Weather module disabled. All scheduled weather jobs have been cancelled.",
            ephemeral=True,
        )

    # ── Signup enable (T017) ───────────────────────────────────────────

    async def _enable_signup(
        self,
        interaction: discord.Interaction,
        server_id: int,
        channel: discord.TextChannel | None,
        base_role: discord.Role | None,
        signed_up_role: discord.Role | None,
    ) -> None:
        # Guard already-enabled
        if await self.bot.module_service.is_signup_enabled(server_id):
            await interaction.response.send_message(
                "⚠️ Signup module is already enabled.", ephemeral=True
            )
            return

        # Validate required params
        if channel is None or base_role is None or signed_up_role is None:
            await interaction.response.send_message(
                "❌ `channel`, `base_role`, and `signed_up_role` are required when enabling the signup module.",
                ephemeral=True,
            )
            return

        # Guard: channel must not be the interaction channel itself (FR-017)
        cfg = await self.bot.config_service.get_server_config(server_id)
        if cfg and channel.id == cfg.interaction_channel_id:
            await interaction.response.send_message(
                "❌ The signup channel cannot be the same as the bot interaction channel.",
                ephemeral=True,
            )
            return

        # Check bot has manage_channels on the channel
        guild = interaction.guild
        assert guild is not None
        bot_member = guild.get_member(self.bot.user.id)  # type: ignore[union-attr]
        if bot_member:
            perms = channel.permissions_for(bot_member)
            if not perms.manage_channels and not perms.manage_roles:
                await interaction.response.send_message(
                    f"❌ Bot is missing `manage_channels` permission on {channel.mention}.",
                    ephemeral=True,
                )
                return

        await interaction.response.defer(ephemeral=True)

        # Apply permission overwrites
        try:
            interaction_role = guild.get_role(cfg.interaction_role_id) if cfg else None
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                base_role: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=False,
                    use_application_commands=True,
                ),
                guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            }
            if interaction_role:
                overwrites[interaction_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=True
                )
            await channel.edit(overwrites=overwrites)
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Failed to apply channel permission overwrites: {exc}",
                ephemeral=True,
            )
            return

        # Upsert SignupModuleConfig
        from models.signup_module import SignupModuleConfig
        new_cfg = SignupModuleConfig(
            server_id=server_id,
            signup_channel_id=channel.id,
            base_role_id=base_role.id,
            signed_up_role_id=signed_up_role.id,
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

        # Post initial "signups closed" notice to the signup channel
        closed_msg_id: int | None = None
        try:
            closed_msg = await channel.send("🔒 Signups are currently closed.")
            closed_msg_id = closed_msg.id
        except Exception:
            log.exception("_enable_signup: could not post initial closed message")
        if closed_msg_id is not None:
            await self.bot.signup_module_service.save_closed_message_id(server_id, closed_msg_id)

        self.bot.output_router.post_log(server_id, "✅ Signup module **enabled**.")
        await interaction.followup.send("✅ Signup module enabled.", ephemeral=True)

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

        # Remove bot-applied permission overwrites (only those set by _enable_signup)
        if signup_cfg:
            guild = self.bot.get_guild(server_id)
            if guild:
                channel = guild.get_channel(signup_cfg.signup_channel_id)
                if channel and isinstance(channel, discord.TextChannel):
                    # Build the exact set of targets we set during _enable_signup
                    targets_to_revert = [guild.default_role, guild.me]
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

        self.bot.output_router.post_log(server_id, "✅ Signup module **disabled**.")
        await interaction.followup.send(
            "✅ Signup module disabled. All signup configuration has been cleared.",
            ephemeral=True,
        )
