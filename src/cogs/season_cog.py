"""SeasonCog — /season, /division, /round command groups.

Commands:
  /season setup    — start season configuration (admin only)
  /season review   — view pending config with Approve/Amend actions
  /season approve  — commit the pending config to the database
  /season status   — read-only summary of active season
  /season cancel   — delete the active season (admin only, destructive)

  /division add       — add a division to pending setup
  /division duplicate — copy a division with datetime offset (setup only)
  /division delete    — remove a division from pending setup
  /division rename    — rename a division (setup only)
  /division cancel    — cancel a division in the active season

  /round add    — add a round to pending setup (auto-numbered by date)
  /round delete — remove a round and renumber (setup only)
  /round cancel — cancel a round in the active season
"""

from __future__ import annotations

import asyncio
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
from services import season_points_service
import services.track_service as track_service
from services.season_service import SeasonImmutableError
from utils.channel_guard import channel_guard, admin_only
from utils.message_builder import discord_ts, format_division_list, format_round_list, format_roster_block

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory pending config store
# ---------------------------------------------------------------------------


@dataclass
class PendingDivision:
    name: str = ""
    role_id: int = 0
    channel_id: int | None = None
    tier: int = 0
    rounds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PendingConfig:
    server_id: int = 0
    start_date: date = field(default_factory=date.today)
    divisions: list[PendingDivision] = field(default_factory=list)
    season_id: int = 0  # set after first DB snapshot; 0 = not yet persisted
    season_number: int = 0  # set after first DB snapshot
    game_edition: int = 0  # positive integer (e.g. 25 for F1 25)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


async def _get_setup_season_id(bot, guild_id: int) -> int | None:
    """Return the season_id for a SETUP-status season for the guild, or None."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM seasons WHERE server_id = ? AND status = 'SETUP' LIMIT 1",
            (guild_id,),
        )
        row = await cursor.fetchone()
    return row[0] if row else None


def _pending_to_division_models(cfg: PendingConfig) -> list[Division]:
    """Convert PendingDivision entries to Division model objects for formatting."""
    return [
        Division(
            id=0,
            season_id=0,
            name=d.name,
            mention_role_id=d.role_id,
            forecast_channel_id=d.channel_id,
            tier=d.tier,
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
        # Keyed by user_id (or server_id on recovery) \u2192 PendingConfig
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
    @app_commands.describe(
        game_edition="Game edition year (e.g. 25 for F1 25). Required.",
    )
    @channel_guard
    @admin_only
    async def season_setup(
        self,
        interaction: discord.Interaction,
        game_edition: app_commands.Range[int, 1, 9999],
    ) -> None:
        """Begin season setup."""
        server_id = interaction.guild_id

        if self._get_pending_for_server(server_id) is not None:
            await interaction.response.send_message(
                "\u274c A season setup is already in progress for this server. "
                "Use `/season review` to approve, or `/bot-reset` to cancel it first.",
                ephemeral=True,
            )
            return

        if await self.bot.season_service.get_active_season(server_id) is not None:
            await interaction.response.send_message(
                "\u274c A season is currently active for this server. "
                "Complete it before starting a new one.",
                ephemeral=True,
            )
            return

        if await self.bot.season_service.get_setup_season(server_id) is not None:
            await interaction.response.send_message(
                "\u274c A season setup is already in progress for this server. "
                "Use `/season review` to continue, or cancel it first.",
                ephemeral=True,
            )
            return

        cfg = PendingConfig(server_id=server_id, game_edition=game_edition)
        self._pending[interaction.user.id] = cfg
        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            f"\u2705 Season setup started. **Season #{cfg.season_number} (F1 {cfg.game_edition})** is being configured.\n\n"
            "Use `/division add` for each division, then `/round add` for each round.\n"
            "When done, run `/season review` to review and approve.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /season setup | Success\n"
            f"  season: Season #{cfg.season_number} (F1 {cfg.game_edition})",
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
                "\u274c No pending season setup. Run `/season setup` first.",
                ephemeral=True,
            )
            return

        season_num = f" (Season #{cfg.season_number} — F1 {cfg.game_edition})" if cfg.season_number > 0 else (f" (F1 {cfg.game_edition})" if cfg.game_edition > 0 else "")
        lines = [
            f"**Season Review{season_num}**",
            "",
        ]

        # Load from DB to get tier and team roster data
        if cfg.season_id != 0:
            # ── Modules ───────────────────────────────────────────────
            weather_on = await self.bot.module_service.is_weather_enabled(interaction.guild_id)  # type: ignore[attr-defined]
            signup_on = await self.bot.module_service.is_signup_enabled(interaction.guild_id)  # type: ignore[attr-defined]
            results_on = await self.bot.module_service.is_results_enabled(interaction.guild_id)  # type: ignore[attr-defined]
            attendance_on = await self.bot.module_service.is_attendance_enabled(interaction.guild_id)  # type: ignore[attr-defined]
            on = "✅ Enabled"
            off = "❌ Disabled"
            if signup_on:
                signup_cfg = await self.bot.signup_module_service.get_config(interaction.guild_id)  # type: ignore[attr-defined]
                if signup_cfg:
                    signup_ch = f"<#{signup_cfg.signup_channel_id}>" if signup_cfg.signup_channel_id else "*(not configured)*"
                    signup_br = f"<@&{signup_cfg.base_role_id}>" if signup_cfg.base_role_id else "*(not configured)*"
                    signup_cr = f"<@&{signup_cfg.signed_up_role_id}>" if signup_cfg.signed_up_role_id else "*(not configured)*"
                    signup_line = (
                        f"  Signup: {on} | Channel: {signup_ch} | "
                        f"Base role: {signup_br} | Complete role: {signup_cr}"
                    )
                else:
                    signup_line = f"  Signup: {on}"
            else:
                signup_line = f"  Signup: {off}"
            lines += [
                "**Modules**",
                f"  Weather: {on if weather_on else off}",
                signup_line,
                f"  Results: {on if results_on else off}",
                f"  Attendance: {on if attendance_on else off}",
                "",
            ]

            # ── Weather config ────────────────────────────────────────
            if weather_on:
                from services.weather_config_service import get_weather_pipeline_config as _gwpc
                _wcfg = await _gwpc(self.bot.db_path, interaction.guild_id)  # type: ignore[attr-defined]
                lines += [
                    "**Weather Config**",
                    f"  • Phase 1 deadline: {_wcfg.phase_1_days} day(s) before race",
                    f"  • Phase 2 deadline: {_wcfg.phase_2_days} day(s) before race",
                    f"  • Phase 3 deadline: {_wcfg.phase_3_hours}h before race",
                    "",
                ]

            # ── Signup config ─────────────────────────────────────────
            if signup_on:
                _s_cfg = await self.bot.signup_module_service.get_config(interaction.guild_id)  # type: ignore[attr-defined]
                _s_settings = await self.bot.signup_module_service.get_settings(interaction.guild_id)  # type: ignore[attr-defined]
                _s_slots = await self.bot.signup_module_service.get_slots(interaction.guild_id)  # type: ignore[attr-defined]
                _signup_ch = f"<#{_s_cfg.signup_channel_id}>" if _s_cfg and _s_cfg.signup_channel_id else "*(not configured)*"
                _signup_br = f"<@&{_s_cfg.base_role_id}>" if _s_cfg and _s_cfg.base_role_id else "*(not configured)*"
                _signup_cr = f"<@&{_s_cfg.signed_up_role_id}>" if _s_cfg and _s_cfg.signed_up_role_id else "*(not configured)*"
                _time_type = _s_settings.time_type.replace("_", " ").title()
                _time_img = "Required" if _s_settings.time_image_required else "Not required"
                _nationality = "Required" if _s_settings.nationality_required else "Not required"
                _slot_labels = [s.display_label for s in _s_slots] if _s_slots else ["*(none configured)*"]
                lines += [
                    "**Signup Config**",
                    f"  • Channel: {_signup_ch}",
                    f"  • Base role: {_signup_br}",
                    f"  • Sign-up role: {_signup_cr}",
                    f"  • Time type: {_time_type}",
                    f"  • Time image: {_time_img}",
                    f"  • Nationality: {_nationality}",
                    f"  • Available slots: {', '.join(_slot_labels)}",
                    "",
                ]

            # ── Attendance server-level config ────────────────────────
            if attendance_on:
                att_cfg = await self.bot.attendance_service.get_config(interaction.guild_id)  # type: ignore[attr-defined]
                if att_cfg:
                    ar = att_cfg.autoreserve_threshold
                    as_ = att_cfg.autosack_threshold
                    ar_str = f"{ar} pts" if ar is not None else "*(not set)*"
                    as_str = f"{as_} pts" if as_ is not None else "*(not set)*"
                    ln_last = (
                        f"{att_cfg.rsvp_last_notice_hours}h before deadline"
                        if att_cfg.rsvp_last_notice_hours
                        else "*(disabled)*"
                    )
                    lines += [
                        "**Attendance Config**",
                        f"  • RSVP notice: {att_cfg.rsvp_notice_days} day(s) before race",
                        f"  • Last notice: {ln_last}",
                        f"  • Deadline: {att_cfg.rsvp_deadline_hours}h before race",
                        f"  • No-RSVP penalty: {att_cfg.no_rsvp_penalty} pt(s)",
                        f"  • No-attend penalty: {att_cfg.no_attend_penalty} pt(s)",
                        f"  • No-show penalty: {att_cfg.no_show_penalty} pt(s)",
                        f"  • Auto-reserve threshold: {ar_str}",
                        f"  • Auto-sack threshold: {as_str}",
                        "",
                    ]

            # ── Points configs ────────────────────────────────────────
            config_names = await season_points_service.get_season_config_names(self.bot.db_path, cfg.season_id)  # type: ignore[attr-defined]
            if config_names:
                lines.append("**Points Configs:** " + ", ".join(config_names))
            elif results_on:
                server_config_tm = await self.bot.config_service.get_server_config(interaction.guild_id)  # type: ignore[attr-defined]
                if server_config_tm is not None and server_config_tm.test_mode_active:
                    lines.append(
                        "**Points Configs:** *(none attached)* "
                        "\u26a0\ufe0f Test mode active \u2014 Standard & Half Points will be auto-seeded on approval."
                    )
                else:
                    lines.append("**Points Configs:** *(none attached)*")
            else:
                lines.append("**Points Configs:** *(none attached)*")
            lines.append("")

            # Pre-fetch role configs so we can warn about teams missing a role
            teams_with_roles = await self.bot.team_service.get_teams_with_roles(  # type: ignore[attr-defined]
                interaction.guild_id
            )
            roleless = {t["name"] for t in teams_with_roles if not t["role_id"] and not t["is_reserve"]}
            reserve_has_role = any(t["is_reserve"] and t["role_id"] for t in teams_with_roles)
            if not reserve_has_role:
                lines.append(
                    "⚠️ **Reserve team has no role assigned** — use `/team reserve-role` "
                    "before approving. Drivers on the reserve team will fail result validation."
                )
                lines.append("")

            db_divisions = await self.bot.season_service.get_divisions_with_results_config(cfg.season_id)
            for div in db_divisions:
                if not div.name:
                    continue
                tier_tag = f" (Tier {div.tier})" if div.tier > 0 else ""
                lines.append(f"\U0001f4c2 **{div.name}**{tier_tag}")
                lines.append(f"  Role: <@&{div.mention_role_id}>")
                weather_chan = f"<#{div.forecast_channel_id}>" if div.forecast_channel_id else "*(none)*"
                results_chan = f"<#{div.results_channel_id}>" if div.results_channel_id else "*(none)*"
                standings_chan = f"<#{div.standings_channel_id}>" if div.standings_channel_id else "*(none)*"
                verdicts_chan = f"<#{div.penalty_channel_id}>" if div.penalty_channel_id else "*(not configured)*"
                lines.append(f"  Weather channel: {weather_chan}")
                lines.append(f"  Results channel: {results_chan}")
                lines.append(f"  Standings channel: {standings_chan}")
                lines.append(f"  Verdicts channel: {verdicts_chan}")
                teams = await self.bot.team_service.get_division_teams(div.id)
                if teams:
                    lines.append("  **Teams:** " + ", ".join(t["name"] for t in teams))
                    missing_roles = [t["name"] for t in teams if t["name"] in roleless]
                    if missing_roles:
                        lines.append(
                            "  ⚠️ **No role assigned:** "
                            + ", ".join(f'"{n}"' for n in missing_roles)
                            + " — result submission will reject drivers in these teams."
                        )
                rounds_db = await self.bot.season_service.get_division_rounds(div.id)
                for r in rounds_db:
                    lines.append(
                        f"  Round {r.round_number}: {r.format.value} "
                        f"@ {r.track_name or 'Mystery'} \u2014 {discord_ts(r.scheduled_at)}"
                    )
                # Lineup & calendar channels (US3 / FR-007)
                lineup_chan = f"<#{div.lineup_channel_id}>" if div.lineup_channel_id else "*(not set)*"
                cal_chan = f"<#{div.calendar_channel_id}>" if div.calendar_channel_id else "*(not set)*"
                lines.append(f"  Lineup channel: {lineup_chan}")
                lines.append(f"  Calendar channel: {cal_chan}")
                # Attendance channels (gated on attendance module being enabled)
                if attendance_on:
                    att_div_cfg = await self.bot.attendance_service.get_division_config(div.id)  # type: ignore[attr-defined]
                    rsvp_chan = (
                        f"<#{att_div_cfg.rsvp_channel_id}>"
                        if att_div_cfg and att_div_cfg.rsvp_channel_id
                        else "*(not set)*"
                    )
                    att_chan = (
                        f"<#{att_div_cfg.attendance_channel_id}>"
                        if att_div_cfg and att_div_cfg.attendance_channel_id
                        else "*(not set)*"
                    )
                    lines.append(f"  RSVP channel: {rsvp_chan}")
                    lines.append(f"  Attendance channel: {att_chan}")
                # ASSIGNED drivers grouped by team
                async with get_connection(self.bot.db_path) as _db:  # type: ignore[attr-defined]
                    _cur = await _db.execute(
                        """
                        SELECT dp.discord_user_id, ti.name AS team_name
                        FROM driver_season_assignments dsa
                        JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                        JOIN team_seats ts ON ts.id = dsa.team_seat_id
                        JOIN team_instances ti ON ti.id = ts.team_instance_id
                        WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                        ORDER BY ti.name, dp.discord_user_id
                        """,
                        (div.id,),
                    )
                    assignment_rows = await _cur.fetchall()
                if assignment_rows:
                    by_team: dict[str, list[str]] = {}
                    for _row in assignment_rows:
                        by_team.setdefault(_row["team_name"], []).append(f"<@{_row['discord_user_id']}>")
                    for t_name, mentions in by_team.items():
                        lines.append(f"  **{t_name}**: {', '.join(mentions)}")
                else:
                    lines.append("  *(no drivers assigned)*")
                lines.append("")
            # Server-level UNASSIGNED warning (approved but unplaced drivers)
            async with get_connection(self.bot.db_path) as _db:  # type: ignore[attr-defined]
                _cur = await _db.execute(
                    "SELECT COUNT(*) AS cnt FROM driver_profiles WHERE server_id = ? AND current_state = 'UNASSIGNED'",
                    (interaction.guild_id,),
                )
                _unassigned_row = await _cur.fetchone()
            if _unassigned_row and _unassigned_row["cnt"] > 0:
                lines.append(f"⚠️ {_unassigned_row['cnt']} driver(s) UNASSIGNED — placement incomplete")
                lines.append("")
        else:
            for div in cfg.divisions:
                if not div.name:
                    continue
                tier_tag = f" (Tier {div.tier})" if div.tier > 0 else ""
                pending_chan = f"<#{div.channel_id}>" if div.channel_id else "*(none)*"
                lines.append(f"\U0001f4c2 **{div.name}**{tier_tag}")
                lines.append(f"  Role: <@&{div.role_id}>")
                lines.append(f"  Weather channel: {pending_chan}")
                for r in div.rounds:
                    lines.append(
                        f"  Round {r['round_number']}: {r['format'].value} "
                        f"@ {r['track_name'] or 'Mystery'} \u2014 {discord_ts(r['scheduled_at'])}"
                    )
                lines.append("")

        view = _ApproveView(self)
        await interaction.response.send_message("\n".join(lines), view=view, ephemeral=True)

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
                "\u2139\ufe0f No active season found for this server.",
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
                f"\U0001f4c2 **{div.name}**{div_tag} \u2014 "
                "Next round: "
                + (
                    f"R{next_round.round_number} @ {next_round.track_name or 'Mystery'} "
                    f"{discord_ts(next_round.scheduled_at)}"
                    if next_round
                    else "None remaining"
                )
            )

        await interaction.response.send_message("\n".join(lines), ephemeral=True)

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
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\u274c No active season to cancel.",
                ephemeral=True,
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await interaction.response.send_message(
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
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
                        "\U0001f4e2 **Season Cancelled**\n"
                        "The active season has been cancelled by an administrator."
                    )
            except Exception:
                log.exception("Failed to post cancellation notice for division %s", div.name)

        for div in divisions:
            div_rounds = await self.bot.season_service.get_division_rounds(div.id)
            for rnd in div_rounds:
                self.bot.scheduler_service.cancel_round(rnd.id)
        self.bot.scheduler_service.cancel_season_end(interaction.guild_id)

        await self.bot.season_service.cancel_season(season.id)

        await interaction.followup.send(
            "\u2705 Season cancelled.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /season cancel | Success",
        )

    @season.command(
        name="complete",
        description="Manually mark the current season as complete (requires all rounds finalized).",
    )
    @channel_guard
    @admin_only
    async def season_complete(
        self,
        interaction: discord.Interaction,
    ) -> None:
        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\u274c No active season to complete.", ephemeral=True
            )
            return

        all_done = await self.bot.season_service.all_rounds_finalized(interaction.guild_id)
        if not all_done:
            pending = await self.bot.season_service.get_unfinalized_rounds(interaction.guild_id)
            lines = "\n".join(
                f"• {r['division']} — Round {r['round_number']}"
                + (f" ({r['track_name']})" if r.get("track_name") else "")
                for r in pending[:20]
            )
            await interaction.response.send_message(
                f"\u274c Cannot complete season — the following rounds are not yet finalized:\n{lines}",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        from services.season_end_service import execute_season_end
        await execute_season_end(interaction.guild_id, season.id, self.bot)
        await interaction.followup.send("\u2705 Season marked as complete.", ephemeral=True)
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /season complete | Success",
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
        tier="Tier number for this division (1 = top tier, must be sequential and unique)",
    )
    @channel_guard
    @admin_only
    async def division_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
        tier: int,
    ) -> None:
        cfg = self._pending.get(interaction.user.id) or self._get_pending_for_server(interaction.guild_id)
        if cfg is None:
            await interaction.response.send_message(
                "\u274c No pending season setup. Run `/season setup` first.",
                ephemeral=True,
            )
            return

        if tier < 1:
            await interaction.response.send_message(
                "\u26d4 Tier must be 1 or higher.",
                ephemeral=True,
            )
            return

        if any(d.name.lower() == name.lower() for d in cfg.divisions if d.name):
            await interaction.response.send_message(
                f"\u274c A division named **{name}** already exists in this setup.",
                ephemeral=True,
            )
            return

        if any(d.tier == tier for d in cfg.divisions if d.name):
            await interaction.response.send_message(
                f"\u26d4 A division with tier **{tier}** already exists in this setup.",
                ephemeral=True,
            )
            return

        div = PendingDivision(name=name, role_id=role.id, channel_id=None, tier=tier)
        empty = [d for d in cfg.divisions if not d.name]
        if empty:
            idx = cfg.divisions.index(empty[0])
            cfg.divisions[idx] = div
        else:
            cfg.divisions.append(div)

        await self._snapshot_pending(cfg)

        await interaction.response.send_message(
            f"\u2705 Division **{name}** (Tier {tier}) added.\n"
            f"Role: {role.mention}\n\n"
            + format_division_list(_pending_to_division_models(cfg)),
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division add | Success\n"
            f"  division: {name}, tier: {tier}",
        )

    @division.command(
        name="duplicate",
        description="Copy a division's rounds with a datetime offset (setup only).",
    )
    @app_commands.describe(
        source_name="Name of the division to copy from",
        new_name="Name for the new division",
        role="The Discord role to mention for the new division",
        tier="Tier number for the new division (must be unique within this season)",
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
        tier: int = 1,
        day_offset: int = 0,
        hour_offset: float = 0.0,
    ) -> None:
        season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
        if season_id is None:
            await interaction.response.send_message(
                "\u274c `/division duplicate` can only be used during season setup.",
                ephemeral=True,
            )
            return

        if tier < 1:
            await interaction.response.send_message(
                "\u26d4 Tier must be 1 or higher.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        src_div = next((d for d in divisions if d.name.lower() == source_name.lower()), None)
        if src_div is None:
            await interaction.response.send_message(
                f"\u274c Division `{source_name}` not found in pending setup.",
                ephemeral=True,
            )
            return

        if any(d.name.lower() == new_name.lower() for d in divisions):
            await interaction.response.send_message(
                f"\u274c A division named **{new_name}** already exists.",
                ephemeral=True,
            )
            return

        if any(d.tier == tier for d in divisions):
            await interaction.response.send_message(
                f"\u26d4 A division with tier **{tier}** already exists in this season.",
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
                warnings.append(f"\u26a0\ufe0f One or more shifted datetimes are in the past: {discord_ts(dt_aware)}")
                break
        dt_counts = Counter(shifted)
        for dt, count in dt_counts.items():
            if count > 1:
                dt_aware2 = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
                warnings.append(f"\u26a0\ufe0f Multiple rounds share the same shifted datetime: {discord_ts(dt_aware2)}")
                break

        await interaction.response.defer(ephemeral=True)

        try:
            new_div = await self.bot.season_service.duplicate_division(
                division_id=src_div.id,
                name=new_name,
                role_id=role.id,
                forecast_channel_id=None,
                day_offset=day_offset,
                hour_offset=hour_offset,
                tier=tier,
            )
        except ValueError as exc:
            await interaction.followup.send(f"\u26d4 {exc}", ephemeral=True)
            return

        # Seed teams for the newly created division
        await self.bot.team_service.seed_division_teams(new_div.id, interaction.guild_id)

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        updated_divisions = await self.bot.season_service.get_divisions(season_id)
        new_rounds = await self.bot.season_service.get_division_rounds(new_div.id)

        warn_block = ("\n" + "\n".join(warnings)) if warnings else ""
        await interaction.followup.send(
            f"\u2705 Division **{new_name}** (Tier {tier}) created from **{source_name}**"
            f" (offset: {day_offset:+}d {hour_offset:+}h).\n\n"
            + format_division_list(updated_divisions)
            + "\n\n"
            + f"**{new_name} rounds:**\n"
            + format_round_list(new_rounds)
            + warn_block,
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division duplicate | Success\n"
            f"  source: {source_name}\n"
            f"  new_division: {new_name}, tier: {tier}\n"
            f"  offset: {day_offset:+}d {hour_offset:+}h",
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
                "\u274c `/division delete` can only be used during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{name}` not found.",
                ephemeral=True,
            )
            return

        await self.bot.season_service.delete_division(div.id)

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        remaining = await self.bot.season_service.get_divisions(season_id)
        await interaction.response.send_message(
            f"\u2705 Division **{name}** deleted.\n\n"
            + format_division_list(remaining),
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division delete | Success\n"
            f"  division: {name}",
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
                "\u274c `/division rename` can only be used during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == current_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{current_name}` not found.",
                ephemeral=True,
            )
            return

        if any(d.name.lower() == new_name.lower() for d in divisions if d.id != div.id):
            await interaction.response.send_message(
                f"\u274c A division named **{new_name}** already exists.",
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
            f"\u2705 Division **{current_name}** renamed to **{new_name}**.\n\n"
            + format_division_list(remaining),
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division rename | Success\n"
            f"  old_name: {current_name}\n"
            f"  new_name: {new_name}",
        )

    @division.command(
        name="amend",
        description="Amend a division's name, tier, or role during season setup.",
    )
    @app_commands.describe(
        name="Current name of the division",
        new_name="New name for the division (optional)",
        tier="New tier number (optional, must be unique within this season)",
        role="New Discord role (optional)",
    )
    @channel_guard
    @admin_only
    async def division_amend(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        tier: int | None = None,
        role: discord.Role | None = None,
    ) -> None:
        import json as _json

        if new_name is None and tier is None and role is None:
            await interaction.response.send_message(
                "\u274c Provide at least one of: `new_name`, `tier`, `role`.",
                ephemeral=True,
            )
            return

        season_id = await _get_setup_season_id(self.bot, interaction.guild_id)
        if season_id is None:
            await interaction.response.send_message(
                "\u274c `/division amend` is only permitted during season setup.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{name}` not found.",
                ephemeral=True,
            )
            return

        if new_name is not None and any(
            d.name.lower() == new_name.lower() for d in divisions if d.id != div.id
        ):
            await interaction.response.send_message(
                f"\u274c A division named **{new_name}** already exists.",
                ephemeral=True,
            )
            return

        old_value = _json.dumps({
            "name": div.name,
            "tier": div.tier,
            "mention_role_id": div.mention_role_id,
        })

        set_clauses: list[str] = []
        params: list = []
        if new_name is not None:
            set_clauses.append("name = ?")
            params.append(new_name)
        if tier is not None:
            set_clauses.append("tier = ?")
            params.append(tier)
        if role is not None:
            set_clauses.append("mention_role_id = ?")
            params.append(role.id)
        params.append(div.id)

        new_value = _json.dumps({
            "name": new_name if new_name is not None else div.name,
            "tier": tier if tier is not None else div.tier,
            "mention_role_id": role.id if role is not None else div.mention_role_id,
        })
        now = datetime.now(timezone.utc).isoformat()

        async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
            await db.execute(
                f"UPDATE divisions SET {', '.join(set_clauses)} WHERE id = ?",
                params,
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'DIVISION_AMENDED', ?, ?, ?)",
                (
                    interaction.guild_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    old_value,
                    new_value,
                    now,
                ),
            )
            await db.commit()

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        updated_divisions = await self.bot.season_service.get_divisions(season_id)
        await interaction.response.send_message(
            f"\u2705 Division **{name}** amended.\n\n"
            + format_division_list(updated_divisions),
            ephemeral=True,
        )

        log_parts = [f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division amend | Success",
                     f"  division: {name}"]
        if new_name is not None:
            log_parts.append(f"  new_name: {new_name}")
        if tier is not None:
            log_parts.append(f"  tier: {tier}")
        if role is not None:
            log_parts.append(f"  role: {role.name}")
        await self.bot.output_router.post_log(interaction.guild_id, "\n".join(log_parts))

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
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\u274c `/division cancel` requires an active season.",
                ephemeral=True,
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await interaction.response.send_message(
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{name}` not found.",
                ephemeral=True,
            )
            return

        if div.status == "CANCELLED":
            await interaction.response.send_message(
                f"\u274c Division **{name}** is already cancelled.",
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
                    f"\U0001f4e2 **Division Cancelled: {div.name}**\n"
                    "This division has been cancelled by an administrator. "
                    "No further weather forecasts will be posted for this division."
                )
        except Exception:
            log.exception("Failed to post division cancel notice for %s", div.name)

        await interaction.followup.send(
            f"\u2705 Division **{name}** cancelled.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division cancel | Success\n"
            f"  division: {name}",
        )

    # ------------------------------------------------------------------
    # Division channel assignment (shared helper + 3 commands)
    # ------------------------------------------------------------------

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "weather" | "results" | "standings"
    ) -> None:
        import json as _json
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        # 1. Find current season (any state)
        season = await self.bot.season_service.get_season_for_server(server_id)
        if season is None:
            await interaction.response.send_message(
                "\u274c No season found. Set up a season before assigning channels.",
                ephemeral=True,
            )
            return

        # 2. Find division by name
        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division **{name}** not found in the current season.",
                ephemeral=True,
            )
            return

        # 3. Upsert channel + get old value (for idempotency check and audit)
        if channel_type == "weather":
            old_id = await self.bot.season_service.set_division_forecast_channel(div.id, channel.id)
            type_label = "Weather forecast"
        elif channel_type == "results":
            old_id = await self.bot.season_service.set_division_results_channel(div.id, channel.id)
            type_label = "Results"
        else:
            old_id = await self.bot.season_service.set_division_standings_channel(div.id, channel.id)
            type_label = "Standings"

        # 4. Idempotency: same value
        if old_id == channel.id:
            await interaction.response.send_message(
                f"\u2139\ufe0f {type_label} channel for **{name}** is already set to {channel.mention}.",
                ephemeral=True,
            )
            return

        # 5. Audit
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'DIVISION_CHANNEL_SET', ?, ?, ?)",
                (
                    server_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    _json.dumps({"channel_type": channel_type, "channel_id": old_id}),
                    _json.dumps({"channel_type": channel_type, "channel_id": channel.id}),
                    now,
                ),
            )
            await db.commit()

        await interaction.response.send_message(
            f"\u2705 {type_label} channel for **{name}** set to {channel.mention}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division {channel_type}-channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @division.command(
        name="weather-channel",
        description="Set the weather forecast channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Weather forecast channel")
    @channel_guard
    async def division_weather_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_weather_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Weather module is not enabled.", ephemeral=True
            )
            return
        await self._set_division_channel(interaction, name, channel, "weather")

    @division.command(
        name="results-channel",
        description="Set the results posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Results channel")
    @channel_guard
    async def division_results_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
            )
            return
        await self._set_division_channel(interaction, name, channel, "results")

    @division.command(
        name="standings-channel",
        description="Set the standings posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Standings channel")
    @channel_guard
    async def division_standings_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
            )
            return
        await self._set_division_channel(interaction, name, channel, "standings")

    @division.command(
        name="verdicts-channel",
        description="Set the verdicts (penalty announcement) channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Verdicts announcement channel")
    @channel_guard
    @admin_only
    async def division_verdicts_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        import json as _json
        if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        # Validate bot access
        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_season_for_server(server_id)
        if season is None:
            await interaction.followup.send(
                "\u274c No season found. Set up a season before assigning channels.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division \"{name}\" not found.",
                ephemeral=True,
            )
            return

        old_id = await self.bot.season_service.set_division_penalty_channel(div.id, channel.id)

        # Audit log
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'VERDICTS_CHANNEL_SET', ?, ?, ?)",
                (
                    server_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    _json.dumps({"channel_id": old_id}),
                    _json.dumps({"channel_id": channel.id}),
                    now,
                ),
            )
            await db.commit()

        if old_id is None:
            msg = f"\u2705 Verdicts channel for {name} set to #{channel.name}."
        else:
            msg = f"\u2705 Verdicts channel for {name} updated to #{channel.name}."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division verdicts-channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @division.command(
        name="rsvp-channel",
        description="Set the RSVP notice channel for a division (attendance module).",
    )
    @app_commands.describe(name="Division name", channel="RSVP notice channel")
    @channel_guard
    @admin_only
    async def division_rsvp_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        import json as _json
        if not await self.bot.module_service.is_attendance_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Attendance module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_season_for_server(server_id)
        if season is None:
            await interaction.followup.send(
                "\u274c No season found. Set up a season before assigning channels.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division \"{name}\" not found.",
                ephemeral=True,
            )
            return

        old_cfg = await self.bot.attendance_service.get_division_config(div.id)
        old_id = old_cfg.rsvp_channel_id if old_cfg else None

        await self.bot.attendance_service.set_rsvp_channel(div.id, server_id, channel.id)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'RSVP_CHANNEL_SET', ?, ?, ?)",
                (
                    server_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    _json.dumps({"channel_id": old_id}),
                    _json.dumps({"channel_id": str(channel.id)}),
                    now,
                ),
            )
            await db.commit()

        if old_id is None:
            msg = f"\u2705 RSVP channel for {name} set to #{channel.name}."
        else:
            msg = f"\u2705 RSVP channel for {name} updated to #{channel.name}."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division rsvp-channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @division.command(
        name="attendance-channel",
        description="Set the attendance logging channel for a division (attendance module).",
    )
    @app_commands.describe(name="Division name", channel="Attendance logging channel")
    @channel_guard
    @admin_only
    async def division_attendance_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        import json as _json
        if not await self.bot.module_service.is_attendance_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Attendance module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        server_id: int = interaction.guild_id  # type: ignore[assignment]

        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_season_for_server(server_id)
        if season is None:
            await interaction.followup.send(
                "\u274c No season found. Set up a season before assigning channels.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division \"{name}\" not found.",
                ephemeral=True,
            )
            return

        old_cfg = await self.bot.attendance_service.get_division_config(div.id)
        old_id = old_cfg.attendance_channel_id if old_cfg else None

        await self.bot.attendance_service.set_attendance_channel(div.id, server_id, channel.id)

        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'ATTENDANCE_CHANNEL_SET', ?, ?, ?)",
                (
                    server_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    _json.dumps({"channel_id": old_id}),
                    _json.dumps({"channel_id": str(channel.id)}),
                    now,
                ),
            )
            await db.commit()

        if old_id is None:
            msg = f"\u2705 Attendance channel for {name} set to #{channel.name}."
        else:
            msg = f"\u2705 Attendance channel for {name} updated to #{channel.name}."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division attendance-channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @division.command(
        name="lineup-channel",
        description="Set the lineup posting channel for a division (signup module).",
    )
    @app_commands.describe(name="Division name", channel="Lineup channel")
    @channel_guard
    @admin_only
    async def division_lineup_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        import json as _json
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        season = await self.bot.season_service.get_season_for_server(server_id)  # type: ignore[attr-defined]
        if season is None:
            await interaction.response.send_message(
                "\u274c No season found. Set up a season before assigning channels.",
                ephemeral=True,
            )
            return
        divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[attr-defined]
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division **{name}** not found in the current season.",
                ephemeral=True,
            )
            return
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
            await db.execute(
                "UPDATE divisions SET lineup_channel_id = ? WHERE id = ?",
                (channel.id, div.id),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'SIGNUP_LINEUP_CHANNEL_SET', '', ?, ?)",
                (
                    server_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    _json.dumps({"channel_id": channel.id}),
                    now,
                ),
            )
            await db.commit()
        await interaction.response.send_message(
            f"\u2705 Lineup channel for **{name}** set to {channel.mention}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division lineup-channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @division.command(
        name="calendar-channel",
        description="Set the calendar posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Calendar channel")
    @channel_guard
    @admin_only
    async def division_calendar_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        import json as _json
        server_id: int = interaction.guild_id  # type: ignore[assignment]
        season = await self.bot.season_service.get_season_for_server(server_id)  # type: ignore[attr-defined]
        if season is None:
            await interaction.response.send_message(
                "\u274c No season found. Set up a season before assigning channels.",
                ephemeral=True,
            )
            return
        divisions = await self.bot.season_service.get_divisions(season.id)  # type: ignore[attr-defined]
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division **{name}** not found in the current season.",
                ephemeral=True,
            )
            return
        now = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
            await db.execute(
                "UPDATE divisions SET calendar_channel_id = ? WHERE id = ?",
                (channel.id, div.id),
            )
            await db.execute(
                "INSERT INTO audit_entries "
                "(server_id, actor_id, actor_name, division_id, change_type, old_value, new_value, timestamp) "
                "VALUES (?, ?, ?, ?, 'DIVISION_CALENDAR_CHANNEL_SET', '', ?, ?)",
                (
                    server_id,
                    interaction.user.id,
                    str(interaction.user),
                    div.id,
                    _json.dumps({"channel_id": channel.id}),
                    now,
                ),
            )
            await db.commit()
        await interaction.response.send_message(
            f"\u2705 Calendar channel for **{name}** set to {channel.mention}.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /division calendar-channel | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
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
                "\u274c No pending season setup. Run `/season setup` first.",
                ephemeral=True,
            )
            return

        try:
            fmt = RoundFormat(format.upper())
        except ValueError:
            await interaction.response.send_message(
                f"\u274c Invalid format `{format}`. Choose from: NORMAL, SPRINT, MYSTERY, ENDURANCE.",
                ephemeral=True,
            )
            return

        track_name = track.strip() or None

        if fmt != RoundFormat.MYSTERY and not track_name:
            await interaction.response.send_message(
                f"\u274c A track is required for `{fmt.value}` rounds. "
                "Leave track blank only for `MYSTERY` rounds.",
                ephemeral=True,
            )
            return

        if track_name:
            async with get_connection(self.bot.db_path) as _tdb:
                if track_name.isdigit():
                    _tcur = await _tdb.execute("SELECT name FROM tracks WHERE id = ?", (int(track_name),))
                else:
                    _tcur = await _tdb.execute("SELECT name FROM tracks WHERE name = ?", (track_name,))
                _trow = await _tcur.fetchone()
            if _trow is None:
                await interaction.response.send_message(
                    f"\u274c Unknown track `{track_name}`.\n"
                    "Use `/round add` and type a number or name \u2014 autocomplete will guide you.",
                    ephemeral=True,
                )
                return
            track_name = _trow["name"]

        try:
            sched = datetime.fromisoformat(scheduled_at)
        except ValueError:
            await interaction.response.send_message(
                "\u274c Invalid datetime. Use ISO format: `YYYY-MM-DDTHH:MM:SS`",
                ephemeral=True,
            )
            return

        div = next((d for d in cfg.divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{division_name}` not found in pending setup.",
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
            f"\u2705 Round **{assigned_number}** added to **{div.name}**.\n"
            f"Format: {fmt.value} | Track: {track_name or 'Mystery'} | {discord_ts(sched)}\n\n"
            + format_round_list(round_models),
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /round add | Success\n"
            f"  division: {div.name}\n"
            f"  round: {assigned_number}, format: {fmt.value}\n"
            f"  track: {track_name or 'Mystery'}\n"
            f"  scheduled_at: {sched.isoformat()} UTC",
        )

    @round_add.autocomplete("track")
    async def round_add_track_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        results: list[app_commands.Choice[str]] = []
        async with get_connection(self.bot.db_path) as _tdb:
            _tracks = await track_service.get_all_tracks(_tdb)
        for r in _tracks:
            label = f"{r['id']:02d} \u2013 {r['name']}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=r["name"]))
        return results[:25]

    @round.command(
        name="amend",
        description="Amend a round's configuration. Invalidates prior weather phases.",
    )
    @app_commands.describe(
        division_name="Name of the division containing this round",
        round_number="The round number to amend",
        track="New track ID or name (leave blank to keep current)",
        scheduled_at="New race datetime in ISO format YYYY-MM-DDTHH:MM:SS (leave blank to keep current)",
        format="New format: NORMAL, SPRINT, MYSTERY, or ENDURANCE (leave blank to keep current)",
    )
    @channel_guard
    @admin_only
    async def round_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        track: str = "",
        scheduled_at: str = "",
        format: str = "",
    ) -> None:
        if not any([track, scheduled_at, format]):
            await interaction.response.send_message(
                "\u274c Provide at least one field to amend: `track`, `scheduled_at`, or `format`.",
                ephemeral=True,
            )
            return

        # Pending-config path
        pending_cfg = self._get_pending_for_server(interaction.guild_id)
        if pending_cfg is not None:
            pend_div = next(
                (d for d in pending_cfg.divisions if d.name.lower() == division_name.lower()),
                None,
            )
            if pend_div is None:
                await interaction.response.send_message(
                    f"\u274c Division `{division_name}` not found in pending setup.",
                    ephemeral=True,
                )
                return

            pend_rnd = next(
                (r for r in pend_div.rounds if r["round_number"] == round_number),
                None,
            )
            if pend_rnd is None:
                await interaction.response.send_message(
                    f"\u274c Round {round_number} not found in division `{division_name}` of the pending setup.",
                    ephemeral=True,
                )
                return

            new_track: str | None = ...
            if track:
                async with get_connection(self.bot.db_path) as _tdb:
                    if track.isdigit():
                        _tcur = await _tdb.execute("SELECT name FROM tracks WHERE id = ?", (int(track),))
                    else:
                        _tcur = await _tdb.execute("SELECT name FROM tracks WHERE name = ?", (track,))
                    _trow = await _tcur.fetchone()
                if _trow is None:
                    await interaction.response.send_message(
                        f"\u274c Unknown track `{track}`. Use autocomplete to pick a valid track.",
                        ephemeral=True,
                    )
                    return
                new_track = _trow["name"]

            new_dt = ...
            if scheduled_at:
                try:
                    new_dt = datetime.fromisoformat(scheduled_at)
                except ValueError:
                    await interaction.response.send_message(
                        "\u274c Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
                        ephemeral=True,
                    )
                    return

            new_fmt = ...
            if format:
                try:
                    new_fmt = RoundFormat(format.upper())
                except ValueError:
                    await interaction.response.send_message(
                        f"\u274c Invalid format `{format}`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
                        ephemeral=True,
                    )
                    return

            effective_fmt = new_fmt if new_fmt is not ... else pend_rnd["format"]
            effective_track = new_track if new_track is not ... else pend_rnd["track_name"]
            if effective_fmt != RoundFormat.MYSTERY and not effective_track:
                await interaction.response.send_message(
                    f"\u274c Format `{effective_fmt.value}` requires a track. "
                    "Supply a `track` value or change format to MYSTERY.",
                    ephemeral=True,
                )
                return

            if new_fmt is not ...:
                pend_rnd["format"] = new_fmt
            if new_dt is not ...:
                pend_rnd["scheduled_at"] = new_dt
            if new_track is not ...:
                pend_rnd["track_name"] = new_track
            if pend_rnd["format"] == RoundFormat.MYSTERY:
                pend_rnd["track_name"] = None

            # Re-sort rounds by scheduled_at and renumber
            pend_div.rounds.sort(key=lambda r: r["scheduled_at"])
            for i, r in enumerate(pend_div.rounds, start=1):
                r["round_number"] = i

            await self._snapshot_pending(pending_cfg)

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
                for r in pend_div.rounds
            ]
            await interaction.response.send_message(
                f"\u2705 Round {round_number} in **{pend_div.name}** updated in pending setup "
                f"(no DB write \u2014 use `/season approve` to commit).\n\n"
                + format_round_list(round_models),
                ephemeral=True,
            )
            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /round amend (pending) | Success\n"
                f"  division: {pend_div.name}\n"
                f"  round: {round_number}",
            )
            return

        # Active-season DB path
        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message("\u274c No active season found.", ephemeral=True)
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{division_name}` not found.", ephemeral=True
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.response.send_message(
                f"\u274c Round {round_number} not found in division `{division_name}`.",
                ephemeral=True,
            )
            return

        amendments: list[tuple[str, object]] = []

        if track:
            async with get_connection(self.bot.db_path) as _tdb:
                if track.isdigit():
                    _tcur = await _tdb.execute("SELECT name FROM tracks WHERE id = ?", (int(track),))
                else:
                    _tcur = await _tdb.execute("SELECT name FROM tracks WHERE name = ?", (track,))
                _trow = await _tcur.fetchone()
            if _trow is None:
                await interaction.response.send_message(
                    f"\u274c Unknown track `{track}`. Use autocomplete to pick a valid track.",
                    ephemeral=True,
                )
                return
            amendments.append(("track_name", _trow["name"]))

        if scheduled_at:
            try:
                new_dt = datetime.fromisoformat(scheduled_at)
            except ValueError:
                await interaction.response.send_message(
                    "\u274c Invalid datetime. Use `YYYY-MM-DDTHH:MM:SS`.",
                    ephemeral=True,
                )
                return
            amendments.append(("scheduled_at", new_dt))

        if format:
            try:
                new_fmt = RoundFormat(format.upper())
            except ValueError:
                await interaction.response.send_message(
                    f"\u274c Invalid format `{format}`. Use NORMAL, SPRINT, MYSTERY, or ENDURANCE.",
                    ephemeral=True,
                )
                return
            amendments.append(("format", new_fmt))

        summary_lines = [f"**Amend Round {rnd.round_number}** in division **{div.name}**:"]
        for f_name, f_val in amendments:
            summary_lines.append(f"  \u2022 `{f_name}` \u2192 `{f_val}`")
        summary_lines.append("\n\u26a0\ufe0f This will invalidate all prior weather phases for this round.")

        view = _ConfirmView(
            cog=self,
            interaction_user_id=interaction.user.id,
            round_id=rnd.id,
            amendments=amendments,
        )
        await interaction.response.send_message("\n".join(summary_lines), view=view, ephemeral=True)

    @round_amend.autocomplete("track")
    async def round_amend_track_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        results: list[app_commands.Choice[str]] = []
        async with get_connection(self.bot.db_path) as _tdb:
            _tracks = await track_service.get_all_tracks(_tdb)
        for r in _tracks:
            label = f"{r['id']:02d} \u2013 {r['name']}"
            if current.lower() in label.lower():
                results.append(app_commands.Choice(name=label, value=r["name"]))
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
                "\u274c `/round delete` can only be used during season setup.",
                ephemeral=True,
            )
            return

        setup_season = await self.bot.season_service.get_setup_season(interaction.guild_id)
        if setup_season is not None:
            try:
                await self.bot.season_service.assert_season_mutable(setup_season)
            except SeasonImmutableError:
                await interaction.response.send_message(
                    "\u274c This season is archived (COMPLETED) and cannot be modified.",
                    ephemeral=True,
                )
                return

        divisions = await self.bot.season_service.get_divisions(season_id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{division_name}` not found.",
                ephemeral=True,
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.response.send_message(
                f"\u274c Round {round_number} not found in division `{division_name}`.",
                ephemeral=True,
            )
            return

        await self.bot.season_service.delete_round(rnd.id)

        cfg = self._get_pending_for_server(interaction.guild_id)
        if cfg is not None:
            await self._reload_pending_from_db(cfg)

        remaining = await self.bot.season_service.get_division_rounds(div.id)
        await interaction.response.send_message(
            f"\u2705 Round **{round_number}** deleted from **{division_name}** and rounds renumbered.\n\n"
            + format_round_list(remaining),
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /round delete | Success\n"
            f"  division: {division_name}\n"
            f"  round: {round_number}",
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
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_active_season(interaction.guild_id)
        if season is None:
            await interaction.response.send_message(
                "\u274c `/round cancel` requires an active season.",
                ephemeral=True,
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await interaction.response.send_message(
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.response.send_message(
                f"\u274c Division `{division_name}` not found.",
                ephemeral=True,
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.response.send_message(
                f"\u274c Round {round_number} not found in division `{division_name}`.",
                ephemeral=True,
            )
            return

        if rnd.status == "CANCELLED":
            await interaction.response.send_message(
                f"\u274c Round {round_number} in **{division_name}** is already cancelled.",
                ephemeral=True,
            )
            return

        # Guard: block cancel while a results submission channel is open (FR-020)
        from services.result_submission_service import is_submission_open
        if await is_submission_open(self.bot.db_path, rnd.id):
            await interaction.response.send_message(
                f"\u274c Cannot cancel Round {round_number} — a results submission channel is "
                "currently open. Close the submission first.",
                ephemeral=True,
            )
            return

        # Guard: block cancel if results have already been submitted for this round
        from db.database import get_connection
        async with get_connection(self.bot.db_path) as _db:
            _cur = await _db.execute(
                "SELECT COUNT(*) FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
                (rnd.id,),
            )
            _row = await _cur.fetchone()
        if _row and _row[0] > 0:
            await interaction.response.send_message(
                f"\u274c Cannot cancel Round {round_number} — results have already been "
                "submitted for this round.",
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
                    f"\U0001f4e2 **Round {round_number} Cancelled: {div.name}**\n"
                    f"Round {round_number} ({rnd.track_name or 'Mystery'}) has been cancelled by "
                    "an administrator. No weather forecast will be posted for this round."
                )
        except Exception:
            log.exception("Failed to post round cancel notice for round %s in %s", round_number, div.name)

        await interaction.followup.send(
            f"\u2705 Round **{round_number}** in **{division_name}** cancelled.",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /round cancel | Success\n"
            f"  division: {division_name}\n"
            f"  round: {round_number}",
        )

    # ------------------------------------------------------------------
    # /round results sub-group (T021, T023)
    # ------------------------------------------------------------------

    round_results = app_commands.Group(
        name="results",
        description="Round results management (penalties, amendments)",
        parent=round,
    )

    @round_results.command(
        name="amend",
        description="Re-submit results for one session of a completed round.",
    )
    @app_commands.describe(
        division_name="Division name",
        round_number="Round number",
        session="Session to amend (if omitted, bot will ask)",
    )
    @app_commands.choices(session=[
        app_commands.Choice(name="Sprint Qualifying", value="SPRINT_QUALIFYING"),
        app_commands.Choice(name="Sprint Race", value="SPRINT_RACE"),
        app_commands.Choice(name="Feature Qualifying", value="FEATURE_QUALIFYING"),
        app_commands.Choice(name="Feature Race", value="FEATURE_RACE"),
    ])
    @channel_guard
    @admin_only
    async def round_results_amend(
        self,
        interaction: discord.Interaction,
        division_name: str,
        round_number: int,
        session: app_commands.Choice[str] | None = None,
    ) -> None:
        if not await self.bot.module_service.is_results_enabled(interaction.guild_id):
            await interaction.response.send_message(
                "\u274c The Results & Standings module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        from models.points_config import SessionType
        from db.database import get_connection

        # --- Resolve division and round ---
        season = await self.bot.season_service.get_season_for_server(interaction.guild_id)
        if season is None:
            await interaction.followup.send("\u274c No active season.", ephemeral=True)
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division_name.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division `{division_name}` not found.", ephemeral=True
            )
            return

        rounds = await self.bot.season_service.get_division_rounds(div.id)
        rnd = next((r for r in rounds if r.round_number == round_number), None)
        if rnd is None:
            await interaction.followup.send(
                f"\u274c Round {round_number} not found.", ephemeral=True
            )
            return

        # T009: amend is only permitted on FINAL rounds
        if rnd.result_status != "FINAL":
            await interaction.followup.send(
                "\u274c This round cannot be amended yet. Round results must reach **FINAL** status "
                "(approved through the full penalty review and appeals process) before they can be amended.",
                ephemeral=True,
            )
            return

        # Load ACTIVE session_results
        async with get_connection(self.bot.db_path) as db:
            cursor = await db.execute(
                "SELECT session_type, id, config_name FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
                (rnd.id,),
            )
            sr_rows = await cursor.fetchall()

        if not sr_rows:
            await interaction.followup.send(
                "\u274c No results found for this round.", ephemeral=True
            )
            return

        # Determine target session type
        chosen_session_type: SessionType | None = (
            SessionType(session.value) if session is not None else None
        )

        if chosen_session_type is None:
            # Ask user to select which session to amend (ephemeral in the command channel)
            _stype_order = list(SessionType)
            session_types_present = sorted(
                [SessionType(r["session_type"]) for r in sr_rows],
                key=lambda s: _stype_order.index(s),
            )
            _LABEL = {
                SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
                SessionType.SPRINT_RACE: "Sprint Race",
                SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
                SessionType.FEATURE_RACE: "Feature Race",
            }

            class _SessionView(discord.ui.View):
                def __init__(self_v) -> None:
                    super().__init__(timeout=None)
                    self_v.selected: SessionType | None = None
                    self_v.cancelled = False
                    for st in session_types_present:
                        btn = discord.ui.Button(label=_LABEL.get(st, st.value), custom_id=f"asess_{st.value}")
                        async def _cb(bi: discord.Interaction, _st=st) -> None:
                            self_v.selected = _st
                            self_v.stop()
                            await bi.response.defer()
                        btn.callback = _cb
                        self_v.add_item(btn)
                    cancel_btn = discord.ui.Button(label="\u274c Cancel", style=discord.ButtonStyle.secondary)
                    async def _cancel(bi: discord.Interaction) -> None:
                        self_v.cancelled = True
                        self_v.stop()
                        await bi.response.defer()
                    cancel_btn.callback = _cancel
                    self_v.add_item(cancel_btn)

            sv = _SessionView()
            await interaction.followup.send(
                "\U0001f4cb Select the session to re-submit:", view=sv, ephemeral=True
            )
            await sv.wait()
            if sv.cancelled or sv.selected is None:
                await interaction.followup.send("\u2139\ufe0f Amendment cancelled.", ephemeral=True)
                return
            chosen_session_type = sv.selected

        sr_match = next((r for r in sr_rows if r["session_type"] == chosen_session_type.value), None)
        if sr_match is None:
            await interaction.followup.send(
                f"\u274c No {chosen_session_type.value} session found for this round.", ephemeral=True
            )
            return

        existing_config_name = sr_match["config_name"]

        # --- Create a dedicated amend channel ---
        import re as _re
        from datetime import datetime, timezone

        server_cfg = await self.bot.config_service.get_server_config(interaction.guild_id)  # type: ignore[attr-defined]
        bot_cmd_channel_id: int | None = server_cfg.interaction_channel_id if server_cfg else None
        admin_role: discord.Role | None = None
        if server_cfg and server_cfg.interaction_role_id:
            admin_role = interaction.guild.get_role(server_cfg.interaction_role_id)

        # Derive category from bot command channel (same pattern as submission channel)
        category: discord.CategoryChannel | None = None
        if bot_cmd_channel_id is not None:
            cmd_channel = interaction.guild.get_channel(bot_cmd_channel_id)
            if cmd_channel is not None:
                category = getattr(cmd_channel, "category", None)

        slug = _re.sub(r"[^a-z0-9-]", "", division_name.lower().replace(" ", "-"))[:20]
        amend_ch_name = f"amend-S{season.season_number}-{slug}-R{round_number}"
        overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
        }
        if interaction.guild.me is not None:
            overwrites[interaction.guild.me] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True, manage_messages=True
            )
        if admin_role is not None:
            overwrites[admin_role] = discord.PermissionOverwrite(
                read_messages=True, send_messages=True
            )
        amend_channel = await interaction.guild.create_text_channel(
            name=amend_ch_name,
            category=category,
            overwrites=overwrites,
            reason="Results amend channel",
        )

        # Record the channel so restart recovery can detect and clean it up.
        _amend_created_at = datetime.now(timezone.utc).isoformat()
        async with get_connection(self.bot.db_path) as _adb:
            await _adb.execute(
                """
                INSERT OR REPLACE INTO round_amend_channels
                    (round_id, server_id, channel_id, session_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (rnd.id, interaction.guild_id, amend_channel.id,
                 chosen_session_type.value, _amend_created_at),
            )
            await _adb.commit()

        _SESSION_LABEL = {
            SessionType.SPRINT_QUALIFYING: "Sprint Qualifying",
            SessionType.SPRINT_RACE: "Sprint Race",
            SessionType.FEATURE_QUALIFYING: "Feature Qualifying",
            SessionType.FEATURE_RACE: "Feature Race",
        }
        session_label = _SESSION_LABEL.get(chosen_session_type, chosen_session_type.value)

        # Cancel button posted in the amend channel
        cancelled_flag: list[bool] = [False]

        class _CancelView(discord.ui.View):
            def __init__(self_v) -> None:
                super().__init__(timeout=None)

            @discord.ui.button(label="❌ Cancel Amendment", style=discord.ButtonStyle.danger)
            async def cancel_btn(self_v, bi: discord.Interaction, btn: discord.ui.Button) -> None:
                if bi.user.id != interaction.user.id:
                    if admin_role is None or admin_role not in getattr(bi.user, "roles", []):
                        await bi.response.send_message("⛔ Only league managers can cancel.", ephemeral=True)
                        return
                cancelled_flag[0] = True
                self_v.stop()
                await bi.response.send_message("Amendment cancelled.", ephemeral=True)

        cancel_view = _CancelView()
        prompt_msg = await amend_channel.send(
            f"📋 **Amend Results — {session_label} | Round {round_number} ({division_name})**\n"
            "Paste the corrected results below (same format as original submission).\n"
            "Click **❌ Cancel Amendment** to abort and delete this channel.",
            view=cancel_view,
        )

        await interaction.followup.send(
            f"✅ Amendment channel created: {amend_channel.mention}", ephemeral=True
        )

        # --- Collect new results ---
        from services.result_submission_service import validate_submission_block
        from services.result_submission_service import extract_fl_override  # type: ignore[attr-defined]
        from services.result_submission_service import _build_division_validation_data  # type: ignore[attr-defined]

        driver_ids, team_role_ids, reserve_role_id, driver_team_map, reserve_driver_ids = await _build_division_validation_data(
            div.id, interaction.guild_id, interaction.client
        )

        async def _cleanup_channel() -> None:
            async with get_connection(self.bot.db_path) as _cdb:
                await _cdb.execute(
                    "DELETE FROM round_amend_channels WHERE round_id = ? AND session_type = ?",
                    (rnd.id, chosen_session_type.value),
                )
                await _cdb.commit()
            try:
                await amend_channel.delete(reason="Results amend complete")
            except discord.HTTPException:
                pass

        while True:
            # Wait for either a message in the amend channel or the cancel button
            done_task = self.bot.loop.create_task(
                interaction.client.wait_for(
                    "message",
                    check=lambda m: (
                        m.channel.id == amend_channel.id
                        and m.author.id == interaction.user.id
                    ),
                )
            )
            cancel_task = self.bot.loop.create_task(cancel_view.wait())

            import asyncio as _asyncio
            _AMEND_TIMEOUT_S = 300  # 5 minutes
            done, pending = await _asyncio.wait(
                {done_task, cancel_task},
                return_when=_asyncio.FIRST_COMPLETED,
                timeout=_AMEND_TIMEOUT_S,
            )
            for t in pending:
                t.cancel()

            if not done:
                # Timed out — no input received within 5 minutes
                await self.bot.output_router.post_log(
                    interaction.guild_id,
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_TIMEOUT | "
                    f"round {rnd.round_number} session {chosen_session_type.value}",
                )
                await _cleanup_channel()
                return

            if cancelled_flag[0] or (cancel_task in done and not cancelled_flag[0]):
                # Cancel button was clicked (cancel_view.wait() finished) or flag set
                cancelled_flag[0] = True

            if cancelled_flag[0]:
                await self.bot.output_router.post_log(
                    interaction.guild_id,
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_CANCELLED | "
                    f"round {rnd.round_number} session {chosen_session_type.value}",
                )
                await _cleanup_channel()
                await interaction.followup.send("ℹ️ Amendment cancelled.", ephemeral=True)
                return

            msg = done_task.result()
            lines_raw = [ln.strip() for ln in msg.content.strip().splitlines() if ln.strip()]
            fl_amend_override: int | None = None
            if not chosen_session_type.is_qualifying:
                fl_amend_override, lines_raw = extract_fl_override(lines_raw)
            parsed = validate_submission_block(
                lines_raw,
                chosen_session_type,
                driver_ids,
                team_role_ids,
                reserve_role_id,
                driver_team_map,
                reserve_driver_ids,
            )
            try:
                await msg.delete()
            except Exception:
                pass

            if isinstance(parsed, list) and parsed and isinstance(parsed[0], str):
                # Validation failed — log and delete channel
                await self.bot.output_router.post_log(
                    interaction.guild_id,
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_REJECTED | "
                    f"round {rnd.round_number} session {chosen_session_type.value}\n"
                    f"  errors: {'; '.join(parsed[:10])}",
                )
                await _cleanup_channel()
                await interaction.followup.send(
                    "❌ Amendment rejected — validation errors were found. "
                    "Check the log channel for details, then re-run `/round results amend`.",
                    ephemeral=True,
                )
                return

            # Validate FL override references a driver in the submitted results
            if fl_amend_override is not None:
                submitted_driver_ids = {r.driver_user_id for r in parsed}
                if fl_amend_override not in submitted_driver_ids:
                    fl_member = amend_channel.guild.get_member(int(fl_amend_override)) if amend_channel.guild else None
                    fl_name = fl_member.display_name if fl_member else str(fl_amend_override)
                    await self.bot.output_router.post_log(
                        interaction.guild_id,
                        f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_REJECTED | "
                        f"round {rnd.round_number} session {chosen_session_type.value}\n"
                        f"  error: FL override {fl_name} not in submitted results",
                    )
                    await _cleanup_channel()
                    await interaction.followup.send(
                        f"❌ Amendment rejected — FL override **{fl_name}** is not in the submitted results. "
                        "Re-run `/round results amend` to try again.",
                        ephemeral=True,
                    )
                    return

            # Valid — determine config name
            from services.season_points_service import get_season_config_names

            config_names = await get_season_config_names(self.bot.db_path, season.id)
            if len(config_names) == 1:
                config_name = config_names[0]
            elif existing_config_name and existing_config_name in config_names:
                config_name = existing_config_name
            else:
                from services.result_submission_service import _ConfigSelectView  # type: ignore[attr-defined]
                cfg_view = _ConfigSelectView(config_names)
                await amend_channel.send(
                    "Select the points configuration for this session:", view=cfg_view
                )
                await cfg_view.wait()
                config_name = cfg_view.selected or config_names[0]

            from services.result_submission_service import amend_session_result
            try:
                await amend_session_result(
                    self.bot.db_path,
                    rnd.id,
                    div.id,
                    chosen_session_type,
                    parsed,  # type: ignore[arg-type]
                    config_name,
                    interaction.user.id,
                    interaction.client,
                    fl_driver_override=fl_amend_override,
                )
            except Exception as exc:
                import traceback as _tb
                error_summary = f"{type(exc).__name__}: {exc}"
                await self.bot.output_router.post_log(
                    interaction.guild_id,
                    f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_FAILED | "
                    f"round {rnd.round_number} session {chosen_session_type.value}\n"
                    f"  error: {error_summary}\n"
                    f"```\n{_tb.format_exc()[-1500:]}\n```",
                )
                await _cleanup_channel()
                await interaction.followup.send(
                    "❌ Amendment failed due to an internal error. Check the log channel for details.",
                    ephemeral=True,
                )
                return

            await self.bot.output_router.post_log(
                interaction.guild_id,
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | AMEND_SUCCESS | "
                f"round {rnd.round_number} session {chosen_session_type.value} "
                f"config: {config_name}",
            )
            await _cleanup_channel()
            await interaction.followup.send(
                "✅ Session amended and standings updated.", ephemeral=True
            )
            return

    # ------------------------------------------------------------------
    # Shared instance methods
    # ------------------------------------------------------------------

    def clear_pending_for_server(self, server_id: int) -> None:
        """Discard any in-memory pending setup belonging to *server_id*."""
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
        """Return the in-memory pending config for *server_id*, or None."""
        return next(
            (cfg for cfg in self._pending.values() if cfg.server_id == server_id),
            None,
        )

    async def _snapshot_pending(self, cfg: PendingConfig) -> None:
        """Write the current PendingConfig to DB (status=SETUP) and update cfg.season_id."""
        divisions_data = [
            {
                "name": d.name,
                "role_id": d.role_id,
                "channel_id": d.channel_id,
                "tier": d.tier,
                "rounds": d.rounds,
            }
            for d in cfg.divisions
            if d.name
        ]
        new_season_id, season_number = await self.bot.season_service.save_pending_snapshot(
            cfg.server_id, cfg.start_date, cfg.season_id, divisions_data, cfg.game_edition
        )
        cfg.season_id = new_season_id
        cfg.season_number = season_number

        # Re-seed teams for all new divisions (old team_instances were cleaned up by snapshot)
        new_divisions = await self.bot.season_service.get_divisions(cfg.season_id)
        for div in new_divisions:
            await self.bot.team_service.seed_division_teams(div.id, cfg.server_id)

    async def _reload_pending_from_db(self, cfg: PendingConfig) -> None:
        """Resync the in-memory PendingConfig.divisions from DB (after direct DB operations)."""
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
                tier=d.tier,
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
        """Restore in-memory _pending from DB SETUP seasons on bot startup."""
        for s in await self.bot.season_service.load_all_setup_seasons():
            if self._get_pending_for_server(s["server_id"]) is not None:
                continue
            cfg = PendingConfig(
                server_id=s["server_id"],
                start_date=s["start_date"],
                season_id=s["season_id"],
                season_number=s.get("season_number", 0),
                game_edition=s.get("game_edition", 0),
                divisions=[
                    PendingDivision(
                        name=d["name"],
                        role_id=d["role_id"],
                        channel_id=d["channel_id"],
                        tier=d.get("tier", 0),
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
                "\u274c No pending season setup.",
                ephemeral=True,
            )
            return

        if cfg.season_id == 0:
            await interaction.response.send_message(
                "\u274c Season setup state is incomplete. Use `/bot-reset` and start again.",
                ephemeral=True,
            )
            return

        season_svc = self.bot.season_service

        # Validate tier sequential integrity before committing
        try:
            await season_svc.validate_division_tiers(cfg.season_id)
        except ValueError as exc:
            msg = f"\u26d4 Season cannot be approved. {exc}"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        divisions = await season_svc.get_divisions(cfg.season_id)
        div_rounds: dict[int, list] = {}
        for div_db in divisions:
            div_rounds[div_db.id] = await season_svc.get_division_rounds(div_db.id)

        # ── Gate 0: every division must have at least one round ────────────────
        empty_divs = [d.name for d in divisions if not div_rounds[d.id]]
        if empty_divs:
            names = ", ".join(f"**{n}**" for n in empty_divs)
            msg = (
                f"\u274c Season cannot be approved \u2014 the following divisions have no rounds: "
                f"{names}. Add at least one round to each division first."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        # ── Gate 0b: no two rounds in the same division may share a datetime ──
        duplicate_errors: list[str] = []
        for div_db in divisions:
            seen: set = set()
            for rnd in div_rounds[div_db.id]:
                if rnd.scheduled_at in seen:
                    duplicate_errors.append(
                        f"**{div_db.name}** has multiple rounds scheduled at {discord_ts(rnd.scheduled_at)}"
                    )
                    break
                seen.add(rnd.scheduled_at)
        if duplicate_errors:
            bullet_list = "\n\u2022 ".join(duplicate_errors)
            msg = (
                f"\u274c Season cannot be approved \u2014 duplicate round times detected:\n\u2022 {bullet_list}\n"
                f"Reschedule rounds so each has a unique datetime within its division."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        all_rounds = []
        for div_db in divisions:
            for rnd in div_rounds[div_db.id]:
                await season_svc.create_sessions_for_round(rnd.id, rnd.format)
                all_rounds.append(rnd)

        # ── Gate 1: weather channel prerequisite (FR-011) ──────────────────────
        if await self.bot.module_service.is_weather_enabled(cfg.server_id):
            missing_weather = [d.name for d in divisions if not d.forecast_channel_id]
            if missing_weather:
                names = ", ".join(f"**{n}**" for n in missing_weather)
                msg = (
                    f"\u274c Season cannot be approved \u2014 the following divisions are missing a "
                    f"weather forecast channel: {names}. Assign a weather channel to each division first."
                )
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                return

        # ── Gate 2: R&S channel and points-config prerequisites (FR-013) ───────
        if await self.bot.module_service.is_results_enabled(cfg.server_id):
            # Auto-seed point configs if test mode is active and none are attached yet
            server_config = await self.bot.config_service.get_server_config(cfg.server_id)  # type: ignore[attr-defined]
            if server_config is not None and server_config.test_mode_active:
                async with get_connection(self.bot.db_path) as _db:
                    _cur = await _db.execute(
                        "SELECT COUNT(*) FROM season_points_links WHERE season_id = ?",
                        (cfg.season_id,),
                    )
                    _cnt = await _cur.fetchone()
                if (_cnt[0] if _cnt else 0) == 0:
                    from services.test_roster_service import ensure_test_configs
                    await ensure_test_configs(
                        server_id=cfg.server_id,
                        season_id=cfg.season_id,
                        db_path=self.bot.db_path,  # type: ignore[attr-defined]
                    )

            divs_rs = await season_svc.get_divisions_with_results_config(cfg.season_id)
            errors: list[str] = []
            for d in divs_rs:
                if not d.results_channel_id:
                    errors.append(f"**{d.name}** is missing a results channel")
                if not d.standings_channel_id:
                    errors.append(f"**{d.name}** is missing a standings channel")
                if not d.penalty_channel_id:
                    errors.append(
                        f"**{d.name}** is missing a verdicts channel \u2014 "
                        f"run /division verdicts-channel {d.name} <channel>"
                    )

            async with get_connection(self.bot.db_path) as _db:
                cursor = await _db.execute(
                    "SELECT COUNT(*) FROM season_points_links WHERE season_id = ?",
                    (cfg.season_id,),
                )
                count_row = await cursor.fetchone()
            if (count_row[0] if count_row else 0) == 0:
                errors.append("no points configuration is attached to this season")

            if errors:
                bullet_list = "\n\u2022 ".join(errors)
                msg = f"\u274c Season cannot be approved \u2014 R&S prerequisites not met:\n\u2022 {bullet_list}"
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                return

            # ── Gate 3: monotonic ordering check (FR-008) ────────────────────
            mono_errors = await season_points_service.validate_monotonic_ordering(
                self.bot.db_path, cfg.season_id
            )
            if mono_errors:
                bullet_list = "\n\u2022 ".join(mono_errors)
                msg = (
                    f"\u274c Season cannot be approved \u2014 points configuration "
                    f"violates monotonic ordering:\n\u2022 {bullet_list}"
                )
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                return

        # ── Gate 3: signup module config prerequisites ────────────────────────
        if await self.bot.module_service.is_signup_enabled(cfg.server_id):
            signup_cfg = await self.bot.signup_module_service.get_config(cfg.server_id)
            if signup_cfg:
                missing: list[str] = []
                if signup_cfg.signup_channel_id is None:
                    missing.append("**Signup channel** (use `/signup channel`)")
                if signup_cfg.base_role_id is None:
                    missing.append("**Base role** (use `/signup base-role`)")
                if signup_cfg.signed_up_role_id is None:
                    missing.append("**Complete role** (use `/signup complete-role`)")
                if missing:
                    bullet_list = "\n\u2022 ".join(missing)
                    msg = (
                        f"\u274c Season cannot be approved \u2014 signup module is enabled but "
                        f"missing required configuration:\n\u2022 {bullet_list}"
                    )
                    if interaction.response.is_done():
                        await interaction.followup.send(msg, ephemeral=True)
                    else:
                        await interaction.response.send_message(msg, ephemeral=True)
                    return

        # ── Gate 4: attendance module channel prerequisites ───────────────────
        if await self.bot.module_service.is_attendance_enabled(cfg.server_id):
            att_errors: list[str] = []
            for _div in divisions:
                att_div_cfg = await self.bot.attendance_service.get_division_config(_div.id)  # type: ignore[attr-defined]
                if att_div_cfg is None or not att_div_cfg.rsvp_channel_id:
                    att_errors.append(
                        f"**{_div.name}** is missing an RSVP channel "
                        f"(use `/division rsvp-channel {_div.name} <channel>`)"
                    )
                if att_div_cfg is None or not att_div_cfg.attendance_channel_id:
                    att_errors.append(
                        f"**{_div.name}** is missing an attendance channel "
                        f"(use `/division attendance-channel {_div.name} <channel>`)"
                    )
            if att_errors:
                bullet_list = "\n\u2022 ".join(att_errors)
                msg = (
                    f"\u274c Season cannot be approved \u2014 attendance module is enabled but "
                    f"missing required channel configuration:\n\u2022 {bullet_list}"
                )
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                return

        # Snapshot attached points configs before transitioning (FR-007)
        if await self.bot.module_service.is_results_enabled(cfg.server_id):
            await season_points_service.snapshot_configs_to_season(
                self.bot.db_path, cfg.season_id, cfg.server_id
            )

        # Schedule FIRST — if this fails the season stays SETUP in DB (fix #5)
        weather_enabled = await self.bot.module_service.is_weather_enabled(cfg.server_id)
        results_enabled = await self.bot.module_service.is_results_enabled(cfg.server_id)
        if weather_enabled:
            # schedule_round creates weather phase jobs AND the results_r job together
            from services.weather_config_service import get_weather_pipeline_config
            _wcfg = await get_weather_pipeline_config(self.bot.db_path, cfg.server_id)
            self.bot.scheduler_service.schedule_all_rounds(
                all_rounds,
                phase_1_days=_wcfg.phase_1_days,
                phase_2_days=_wcfg.phase_2_days,
                phase_3_hours=_wcfg.phase_3_hours,
            )
        elif results_enabled:
            # Weather off but results on: schedule results_r jobs for production
            # (real future race times).  In test mode we skip this because past-dated
            # jobs auto-fire immediately; the advance command uses DB-state detection.
            server_config = await self.bot.config_service.get_server_config(cfg.server_id)  # type: ignore[attr-defined]
            if server_config is None or not server_config.test_mode_active:
                self.bot.scheduler_service.schedule_result_submission_jobs(all_rounds)

        if await self.bot.module_service.is_attendance_enabled(cfg.server_id):
            _att_cfg = await self.bot.attendance_service.get_or_create_config(cfg.server_id)
            for _rnd in all_rounds:
                self.bot.scheduler_service.schedule_attendance_round(
                    _rnd,
                    notice_days=_att_cfg.rsvp_notice_days,
                    last_notice_hours=_att_cfg.rsvp_last_notice_hours,
                    deadline_hours=_att_cfg.rsvp_deadline_hours,
                )

        # Only transition to ACTIVE after scheduling succeeds
        await season_svc.transition_to_active(cfg.season_id)

        # ── T015: Bulk role grant for all ASSIGNED drivers (FR-006) ──────────
        _guild = interaction.guild
        if _guild is not None:
            for _div in divisions:
                async with get_connection(self.bot.db_path) as _db:  # type: ignore[attr-defined]
                    _cur = await _db.execute(
                        """
                        SELECT dp.discord_user_id, ti.name AS team_name
                        FROM driver_season_assignments dsa
                        JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
                        JOIN team_seats ts ON ts.id = dsa.team_seat_id
                        JOIN team_instances ti ON ti.id = ts.team_instance_id
                        WHERE dsa.division_id = ? AND dp.current_state = 'ASSIGNED'
                          AND dp.is_test_driver = 0
                        """,
                        (_div.id,),
                    )
                    _assign_rows = await _cur.fetchall()
                for _row in _assign_rows:
                    try:
                        _member = _guild.get_member(int(_row["discord_user_id"])) or (
                            await _guild.fetch_member(int(_row["discord_user_id"]))
                        )
                        _role_ids = [_div.mention_role_id]
                        _team_cfg = await self.bot.placement_service.get_team_role_config(  # type: ignore[attr-defined]
                            cfg.server_id, _row["team_name"]
                        )
                        if _team_cfg is not None:
                            _role_ids.append(_team_cfg.role_id)
                        await self.bot.placement_service._grant_roles(_member, *_role_ids)  # type: ignore[attr-defined]
                    except Exception:
                        log.exception(
                            "_do_approve: role grant failed for user %s", _row["discord_user_id"]
                        )

        # ── T016: Post lineup per division (FR-010) ──────────────────────────
        if _guild is not None:
            for _div in divisions:
                if _div.lineup_channel_id:
                    try:
                        await self.bot.placement_service._refresh_lineup_post(_guild, _div.id)  # type: ignore[attr-defined]
                    except Exception:
                        log.exception(
                            "_do_approve: lineup post failed for division %s", _div.id
                        )

        # ── T017: Post calendar per division (FR-011) ─────────────────────────
        if _guild is not None:
            for _div in divisions:
                if _div.calendar_channel_id:
                    _cal_channel = _guild.get_channel(_div.calendar_channel_id)
                    if _cal_channel is not None and isinstance(_cal_channel, discord.TextChannel):
                        try:
                            _rounds_for_cal = sorted(
                                div_rounds.get(_div.id, []),
                                key=lambda r: r.scheduled_at,
                            )
                            _cal_lines = [f"\U0001f4c5 **{_div.name} \u2014 Race Calendar**"]
                            for _rnd in _rounds_for_cal:
                                _unix = int(_rnd.scheduled_at.timestamp())
                                _track = _rnd.track_name or "Mystery"
                                _cal_lines.append(
                                    f"Round {_rnd.round_number}: {_track} \u2014 <t:{_unix}:F>"
                                )
                            await _cal_channel.send("\n".join(_cal_lines))
                        except discord.HTTPException:
                            log.exception(
                                "_do_approve: calendar post failed for division %s", _div.id
                            )

        stale_keys = [uid for uid, c in self._pending.items() if c.server_id == cfg.server_id]
        for uid in stale_keys:
            del self._pending[uid]

        msg = (
            f"\u2705 **Season approved and activated!**\n"
            f"Season #{cfg.season_number} (ID: {cfg.season_id})"
        )
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)

        await self.bot.output_router.post_log(
            cfg.server_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /season approve | Success\n"
            f"  season: {cfg.season_number}\n"
            f"  season_id: {cfg.season_id}",
        )
        log.info("Season %s activated for server %s by %s", cfg.season_id, cfg.server_id, interaction.user)

    # ------------------------------------------------------------------
    # Guard: block messages while round is in penalty review (T014)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        from services.result_submission_service import is_channel_in_penalty_review

        if await is_channel_in_penalty_review(self.bot.db_path, message.channel.id):
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            try:
                await message.channel.send(
                    f"❌ **{message.author.display_name}** this channel is locked during penalty review."
                    " Please use the buttons above.",
                    delete_after=8,
                )
            except discord.HTTPException:
                pass


# ---------------------------------------------------------------------------
# Approve button view
# ---------------------------------------------------------------------------


class _ApproveView(discord.ui.View):
    def __init__(self, cog: SeasonCog) -> None:
        super().__init__(timeout=300)
        self._cog = cog

    @discord.ui.button(label="\u2705 Approve", style=discord.ButtonStyle.success)
    async def approve(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._cog._do_approve(interaction)
        self.stop()

    @discord.ui.button(label="\u270f\ufe0f Go Back to Edit", style=discord.ButtonStyle.secondary)
    async def amend(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Use `/round amend` to correct a round, or `/division add` / `/round add` to add more. "
            "Then run `/season review` again.",
            ephemeral=True,
        )
        self.stop()


# ---------------------------------------------------------------------------
# Round amendment confirm view
# ---------------------------------------------------------------------------


class _ConfirmView(discord.ui.View):
    def __init__(
        self,
        cog: SeasonCog,
        interaction_user_id: int,
        round_id: int,
        amendments: list[tuple[str, object]],
    ) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._user_id = interaction_user_id
        self._round_id = round_id
        self._amendments = amendments

    @discord.ui.button(label="\u2705 Confirm", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._user_id:
            await interaction.response.send_message("\u26d4 Not your action.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        scheduled_at_changed = any(f == "scheduled_at" for f, _ in self._amendments)
        errors: list[str] = []
        for field_name, new_value in self._amendments:
            try:
                await self._cog.bot.amendment_service.amend_round(
                    self._round_id,
                    interaction.user,
                    field_name,
                    new_value,
                    self._cog.bot,
                )
            except Exception as exc:
                log.exception("Amendment failed for %s: %s", field_name, exc)
                errors.append(f"`{field_name}`: {exc}")

        if errors:
            await interaction.followup.send(
                "\u26a0\ufe0f Some amendments failed:\n" + "\n".join(errors),
                ephemeral=True,
            )
            self.stop()
            return

        rnd = await self._cog.bot.season_service.get_round(self._round_id)
        if rnd is not None and scheduled_at_changed:
            await self._cog.bot.season_service.renumber_rounds(rnd.division_id)

        division_id = rnd.division_id if rnd is not None else None
        rounds = (
            await self._cog.bot.season_service.get_division_rounds(division_id)
            if division_id is not None
            else []
        )
        msg = "\u2705 Round amended successfully."
        if rounds:
            msg += "\n\n" + format_round_list(rounds)
        await interaction.followup.send(msg, ephemeral=True)
        await self._cog.bot.output_router.post_log(
            interaction.guild_id,
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /round amend | Success\n"
            f"  round_id: {self._round_id}\n"
            f"  fields: {', '.join(f for f, _ in self._amendments)}",
        )
        self.stop()

    @discord.ui.button(label="\u274c Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message("Amendment cancelled.", ephemeral=True)
        self.stop()
