"""AttendanceCog — /attendance config commands."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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

    # ── /attendance config absent-penalty ────────────────────────────────────────

    @config.command(
        name="absent-penalty",
        description="Penalty for any absent driver without an ACCEPTED RSVP (NO_RSVP+absent, TENTATIVE+absent, DECLINED+absent; stacks with no-RSVP penalty for NO_RSVP drivers).",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @channel_guard
    @admin_only
    async def config_absent_penalty(
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
        await self.bot.attendance_service.update_absent_penalty(server_id, points)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 Absent penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config rsvp-absent-penalty ────────────────────────────────

    @config.command(
        name="rsvp-absent-penalty",
        description="Penalty for a driver who RSVP'd ACCEPTED but did not attend.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @channel_guard
    @admin_only
    async def config_rsvp_absent_penalty(
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
        await self.bot.attendance_service.update_rsvp_absent_penalty(server_id, points)  # type: ignore[attr-defined]
        await interaction.followup.send(
            f"\u2705 RSVP-absent penalty set to **{points}** point(s).", ephemeral=True
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
        if value is not None:
            cfg = await self.bot.attendance_service.get_config(server_id)  # type: ignore[attr-defined]
            if cfg and cfg.autoreserve_threshold:
                await interaction.response.send_message(
                    "\u274c Cannot set auto-sack while auto-reserve is active. "
                    "Disable auto-reserve first (`/attendance config autoreserve 0`).",
                    ephemeral=True,
                )
                return
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
        if value is not None:
            cfg = await self.bot.attendance_service.get_config(server_id)  # type: ignore[attr-defined]
            if cfg and cfg.autosack_threshold:
                await interaction.response.send_message(
                    "\u274c Cannot set auto-reserve while auto-sack is active. "
                    "Disable auto-sack first (`/attendance config autosack 0`).",
                    ephemeral=True,
                )
                return
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_autoreserve_threshold(server_id, value)  # type: ignore[attr-defined]
        if value is None:
            msg = "\u2705 Auto-reserve **disabled**."
        else:
            msg = f"\u2705 Auto-reserve threshold set to **{value}** point(s)."
        await interaction.followup.send(msg, ephemeral=True)

    # ── /attendance config show ────────────────────────────────────────────

    @config.command(
        name="show",
        description="Show the current attendance configuration for this server.",
    )
    @channel_guard
    @admin_only
    async def config_show(self, interaction: discord.Interaction) -> None:
        if not await self._guard_module_enabled(interaction):
            return

        server_id: int = interaction.guild_id  # type: ignore[assignment]
        cfg = await self.bot.attendance_service.get_config(server_id)  # type: ignore[attr-defined]
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
            f"  RSVP'd + absent: **{cfg.rsvp_absent_penalty}** pt(s)",
            "",
            "**Auto-actions**",
            f"  Auto-reserve threshold: **{_fmt_opt(cfg.autoreserve_threshold)}**",
            f"  Auto-sack threshold: **{_fmt_opt(cfg.autosack_threshold)}**",
        ]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


# ── RSVP button interaction handler (T011 / T012 / T014) ─────────────────────

# Status string mapped from button action name
_ACTION_TO_STATUS = {
    "accept":   "ACCEPTED",
    "tentative": "TENTATIVE",
    "decline":  "DECLINED",
}

_STATUS_LABELS = {
    "ACCEPTED":  "✅ Accepted",
    "TENTATIVE": "❓ Tentative",
    "DECLINED":  "❌ Declined",
    "NO_RSVP":   "(no response)",
}


async def handle_rsvp_button(interaction: discord.Interaction, custom_id: str) -> None:
    """Handle an RSVP button press.

    This function is called by _RsvpButton.callback in rsvp_service.py.
    It performs all validation, locking, DB updates, and embed refresh.
    """
    # Parse action and round_id from custom_id: rsvp_{action}_r{round_id}
    try:
        # Strip "rsvp_" prefix, then split off "_r{round_id}" suffix
        without_prefix = custom_id[len("rsvp_"):]       # e.g. "accept_r42"
        action_part, round_id_str = without_prefix.rsplit("_r", 1)
        round_id = int(round_id_str)
        action = action_part  # "accept" | "tentative" | "decline"
    except (ValueError, IndexError):
        log.error("handle_rsvp_button: could not parse custom_id=%r", custom_id)
        await interaction.response.send_message(
            "❌ Internal error: invalid button ID.", ephemeral=True
        )
        return

    new_status = _ACTION_TO_STATUS.get(action)
    if new_status is None:
        await interaction.response.send_message(
            "❌ Internal error: unknown action.", ephemeral=True
        )
        return

    bot = interaction.client
    discord_user_id = interaction.user.id
    guild_id: int = interaction.guild_id  # type: ignore[assignment]

    # Look up driver profile by Discord user ID (FR-011)
    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cur = await db.execute(
            "SELECT id FROM driver_profiles WHERE server_id = ? AND CAST(discord_user_id AS INTEGER) = ?",
            (guild_id, discord_user_id),
        )
        profile_row = await cur.fetchone()

        if profile_row is None:
            await interaction.response.send_message(
                "❌ You are not registered as a driver in this server.", ephemeral=True
            )
            return
        driver_profile_id: int = profile_row["id"]

        # Get round info
        cur = await db.execute(
            """
            SELECT r.division_id, r.scheduled_at, r.format,
                   ac.rsvp_deadline_hours
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
              JOIN seasons s ON s.id = d.season_id
              JOIN attendance_config ac ON ac.server_id = s.server_id
             WHERE r.id = ?
            """,
            (round_id,),
        )
        round_row = await cur.fetchone()

    if round_row is None:
        await interaction.response.send_message(
            "❌ This round no longer exists.", ephemeral=True
        )
        return

    division_id: int = round_row["division_id"]

    # Verify the driver is in this division (full-time or reserve) (FR-011)
    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cur = await db.execute(
            """
            SELECT ti.is_reserve
              FROM driver_season_assignments dsa
              JOIN team_seats ts ON ts.driver_profile_id = dsa.driver_profile_id
              JOIN team_instances ti ON ti.id = ts.team_instance_id
                                    AND ti.division_id = dsa.division_id
             WHERE dsa.driver_profile_id = ?
               AND dsa.division_id = ?
            """,
            (driver_profile_id, division_id),
        )
        assignment_row = await cur.fetchone()

    if assignment_row is None:
        await interaction.response.send_message(
            "❌ You are not a member of this division.", ephemeral=True
        )
        return

    is_reserve: bool = bool(assignment_row["is_reserve"])

    # Parse scheduled_at and compute locking (FR-014 / FR-015 / FR-016 / FR-017)
    scheduled_at_raw = round_row["scheduled_at"]
    if isinstance(scheduled_at_raw, str):
        scheduled_at = datetime.fromisoformat(scheduled_at_raw)
    else:
        scheduled_at = scheduled_at_raw  # type: ignore[assignment]
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    deadline_hours: int = round_row["rsvp_deadline_hours"] or 0
    now = datetime.now(timezone.utc)

    # Get current rsvp_status for locking check
    async with get_connection(bot.db_path) as db:  # type: ignore[attr-defined]
        cur = await db.execute(
            """
            SELECT rsvp_status FROM driver_round_attendance
             WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?
            """,
            (round_id, division_id, driver_profile_id),
        )
        dra_row = await cur.fetchone()

    current_status: str = dra_row["rsvp_status"] if dra_row is not None else "NO_RSVP"

    # Compute lock threshold
    if deadline_hours > 0:
        lock_deadline_at = scheduled_at - timedelta(hours=deadline_hours)
    else:
        lock_deadline_at = scheduled_at  # FR-017: treat as round start

    if not is_reserve:
        # Full-time: locked after deadline (FR-014)
        if now >= lock_deadline_at:
            await interaction.response.send_message(
                "❌ The RSVP deadline has passed. Your response cannot be changed.",
                ephemeral=True,
            )
            return
    else:
        if current_status == "ACCEPTED":
            # Reserve with ACCEPTED: locked after deadline too (FR-015)
            if now >= lock_deadline_at:
                await interaction.response.send_message(
                    "❌ You have already accepted and the RSVP deadline has passed. "
                    "Your response cannot be changed.",
                    ephemeral=True,
                )
                return
        else:
            # Reserve not-ACCEPTED: locked only at round start (FR-016)
            if now >= scheduled_at:
                await interaction.response.send_message(
                    "❌ The round has started. Your response cannot be changed.",
                    ephemeral=True,
                )
                return

    # No-op check (FR-013)
    if current_status == new_status:
        label = _STATUS_LABELS.get(new_status, new_status)
        await interaction.response.send_message(
            f"ℹ️ You are already marked as **{label}**.", ephemeral=True
        )
        return

    # Upsert status
    await bot.attendance_service.upsert_rsvp_status(  # type: ignore[attr-defined]
        round_id=round_id,
        division_id=division_id,
        driver_profile_id=driver_profile_id,
        status=new_status,
    )

    # Rebuild and edit embed in-place (FR-010 / FR-012)
    from services.rsvp_service import _rebuild_embed_for_round, RsvpView
    embed_row = await bot.attendance_service.get_embed_message(round_id, division_id)  # type: ignore[attr-defined]
    if embed_row is not None:
        channel = bot.get_channel(int(embed_row.channel_id))
        if channel is not None:
            try:
                msg = await channel.fetch_message(int(embed_row.message_id))
                new_embed = await _rebuild_embed_for_round(round_id, division_id, bot)
                await msg.edit(embed=new_embed, view=RsvpView(round_id=round_id))
            except discord.HTTPException as exc:
                log.error("handle_rsvp_button: failed to edit embed: %s", exc)

    label = _STATUS_LABELS.get(new_status, new_status)
    await interaction.response.send_message(
        f"✅ Your RSVP has been updated to **{label}**.", ephemeral=True
    )
