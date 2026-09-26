"""AttendanceCog — /attendance config, sync, post-check-in, channel and test commands."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.attendance.services.attendance_service import (
    recalculation_faults,
    sync_attendance,
    validate_timing_invariant,
)
from leaguebot.core.services import audit_service
from leaguebot.core.services.channel_registry_service import as_text_channel, channel_refusal
from leaguebot.core.services.season_lifecycle_service import uncommitted_seat_excluded
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.input_validator import parse_user_id
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import LeagueModal, guild_of

log = logging.getLogger(__name__)


def _as_utc(moment) -> datetime:
    """A round's ``scheduled_at`` as an aware UTC datetime, however the driver hands it back.

    SQLite stores it as an ISO 8601 string and aiosqlite returns it as one; a naive datetime,
    from any source, is UTC as everything else here is.
    """
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


class AttendanceCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
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

    channel_group = app_commands.Group(
        name="channel",
        description="Set the channels a division's check-in calls and attendance are posted to.",
        parent=attendance,
    )

    test_group = app_commands.Group(
        name="test",
        description="Test-mode attendance utilities.",
        parent=attendance,
    )

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _guard_module_enabled(self, interaction: discord.Interaction) -> bool:
        """Return True (and send error) if module is NOT enabled."""
        if not await self.bot.module_service.is_attendance_enabled():
            await interaction.response.send_message(
                "\u274c The Attendance module is not enabled. "
                "Use `/module enable attendance` first.",
                ephemeral=True,
            )
            return False
        return True

    async def _guard_no_active_season(self, interaction: discord.Interaction) -> bool:
        """Return True (and send error) if there IS an active season."""
        season = await self.bot.season_service.get_confirmed_season()
        if season is not None:
            await interaction.response.send_message(
                "\u274c Attendance configuration cannot be changed once a season's placements are confirmed.",
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
    @league_manager_only
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

        cfg = await self.bot.attendance_service.get_config()
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
        await self.bot.attendance_service.update_rsvp_notice_days(days)
        await interaction.followup.send(
            f"\u2705 RSVP notice set to **{days}** day(s) before the race.", ephemeral=True
        )

    # ── /attendance config rsvp-last-notice ───────────────────────────────

    @config.command(
        name="rsvp-last-notice",
        description="Set hours before the race for the last RSVP reminder (0 = disabled).",
    )
    @app_commands.describe(hours="Hours before the race for the last notice (0 to disable)")
    @league_manager_only
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

        cfg = await self.bot.attendance_service.get_config()
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
        await self.bot.attendance_service.update_rsvp_last_notice_hours(hours)
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
    @league_manager_only
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

        cfg = await self.bot.attendance_service.get_config()
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
        await self.bot.attendance_service.update_rsvp_deadline_hours(hours)
        await interaction.followup.send(
            f"\u2705 RSVP deadline set to **{hours}** hour(s) before the race.", ephemeral=True
        )

    # ── /attendance config no-rsvp-penalty ────────────────────────────────

    @config.command(
        name="no-rsvp-penalty",
        description="Set the point penalty for failing to RSVP.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @league_manager_only
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

        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_no_rsvp_penalty(points)
        await interaction.followup.send(
            f"\u2705 No-RSVP penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config absent-penalty ────────────────────────────────────────

    @config.command(
        name="absent-penalty",
        description="Penalty for absent drivers without ACCEPTED RSVP (stacks with no-RSVP penalty for NO_RSVP drivers).",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @league_manager_only
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

        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_absent_penalty(points)
        await interaction.followup.send(
            f"\u2705 Absent penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config no-show-penalty ────────────────────────────────────

    @config.command(
        name="no-show-penalty",
        description="Penalty for a driver who RSVP'd ACCEPTED but did not attend.",
    )
    @app_commands.describe(points="Penalty points (≥ 0)")
    @league_manager_only
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

        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_no_show_penalty(points)
        await interaction.followup.send(
            f"\u2705 No-show penalty set to **{points}** point(s).", ephemeral=True
        )

    # ── /attendance config autosack ────────────────────────────────────────

    @config.command(
        name="autosack",
        description="Set the cumulative no-show threshold that triggers auto-sack (0 = disabled).",
    )
    @app_commands.describe(points="Cumulative threshold for auto-sack (0 to disable)")
    @league_manager_only
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

        value = None if points == 0 else points
        if value is not None:
            cfg = await self.bot.attendance_service.get_config()
            if cfg and cfg.autoreserve_threshold:
                await interaction.response.send_message(
                    "\u274c Cannot set auto-sack while auto-reserve is active. "
                    "Disable auto-reserve first (`/attendance config autoreserve 0`).",
                    ephemeral=True,
                )
                return
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_autosack_threshold(value)
        if value is None:
            msg = "\u2705 Auto-sack **disabled**."
        else:
            # Autosack reaches every division, whichever one's points carried a driver over
            # (issue #220); a league wanting one division alone is pointed at autoreserve.
            msg = (
                f"\u2705 Auto-sack threshold set to **{value}** point(s).\n"
                "A driver who reaches it is removed from **every seat in every division**, "
                "whichever division's points carried them over. To drop a driver to reserve "
                "only in the division where they missed rounds, use "
                "`/attendance config autoreserve` instead."
            )
        await interaction.followup.send(msg, ephemeral=True)

    # ── /attendance config autoreserve ────────────────────────────────────

    @config.command(
        name="autoreserve",
        description="Set the cumulative threshold that triggers auto-reserve (0 = disabled).",
    )
    @app_commands.describe(points="Cumulative threshold for auto-reserve (0 to disable)")
    @league_manager_only
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

        value = None if points == 0 else points
        if value is not None:
            cfg = await self.bot.attendance_service.get_config()
            if cfg and cfg.autosack_threshold:
                await interaction.response.send_message(
                    "\u274c Cannot set auto-reserve while auto-sack is active. "
                    "Disable auto-sack first (`/attendance config autosack 0`).",
                    ephemeral=True,
                )
                return
        await interaction.response.defer(ephemeral=True)
        await self.bot.attendance_service.update_autoreserve_threshold(value)
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
    @league_manager_only
    async def config_show(self, interaction: discord.Interaction) -> None:
        if not await self._guard_module_enabled(interaction):
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


    # ── /attendance sync ───────────────────────────────────────────────────

    @attendance.command(
        name="sync",
        description="Recalculate a division's attendance from a round on, and apply any sanction still owed.",
    )
    @app_commands.describe(
        division="Division name",
        round="Round number to recalculate from; every finalised round after it follows.",
    )
    @league_manager_only
    async def sync(
        self, interaction: discord.Interaction, division: str, round: int
    ) -> None:
        """Finish a run of attendance sanctions that did not all apply (decided 2026-09-18, #239).

        A league manager's, like the thresholds it enforces: it makes no judgement of its own,
        only applies what the league configured. Refused, with nothing changed, outside the
        ongoing stages — a sanction then has no seat to take — for a round not yet finalised,
        and where a channel the recalculation posts to cannot be reached, the gate an
        amendment's recalculation holds to (#187).
        """
        if not await self._guard_module_enabled(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await interaction.followup.send(
                "\u26d4 `/attendance sync` is available only while the season is ongoing.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"\u274c Division '{division}' not found.", ephemeral=True
            )
            return

        db_path = self.bot.db_path

        # **Not while a round of the division is being amended** (#345, decided 2026-09-21). The
        # recalculation reads the results, which hold an open amendment's corrections before
        # they are approved, and publishes the sheet and applies the sanctions it finds —
        # neither of which a cancelled or lapsed amendment then takes back.
        from leaguebot.results.services.result_submission_service import (
            amendment_wait_text,
            open_amendment_in_division,
        )

        held = await open_amendment_in_division(db_path, div.id)
        if held is not None:
            await interaction.followup.send(
                f"\u23f8\ufe0f Round {held['round_number']} of **{div.name}** is being amended "
                f"in <#{held['channel_id']}>, and its corrections are not approved yet, so its "
                f"attendance cannot be recalculated until that ends — {amendment_wait_text()}. "
                "Run this again then.",
                ephemeral=True,
            )
            return

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT id, status FROM rounds WHERE division_id = ? AND round_number = ?",
                (div.id, round),
            )
            round_row = await cursor.fetchone()
        if round_row is None:
            await interaction.followup.send(
                f"\u274c **{div.name}** has no round {round}.", ephemeral=True
            )
            return
        if round_row["status"] not in ("AWAITING_APPEAL_VERDICTS", "FINAL"):
            await interaction.followup.send(
                f"\u26d4 Round {round} of **{div.name}** has not had its penalties approved "
                f"yet, so it holds no attendance to recalculate.",
                ephemeral=True,
            )
            return

        faults = await recalculation_faults(
            db_path, season.id, interaction.guild, self.bot
        )
        if faults:
            await interaction.followup.send(
                "\u26d4 Nothing was changed \u2014 the attendance could not be posted:\n\u2022 "
                + "\n\u2022 ".join(faults),
                ephemeral=True,
            )
            return

        outcome = await sync_attendance(
            self.bot, guild_of(interaction), db_path, div.id, round_row["id"], season.id
        )

        lines = [f"\u2705 Attendance of **{div.name}** recalculated from round {round} on."]
        if outcome.applied:
            lines.append("Sanctions applied:")
            lines += [f"\u2022 {driver} \u2014 {sanction}" for driver, sanction in outcome.applied]
        elif outcome.complete:
            lines.append("No sanction was owed.")
        if not outcome.complete:
            lines.append("\u26a0\ufe0f Still not applied:")
            lines += [f"\u2022 {line}" for line in outcome.failure_lines()]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

        result = "Success" if outcome.complete else "Incomplete"
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) "
            f"| /attendance sync | {result}\n"
            f"  division: {div.name}\n"
            f"  from round: {round}\n"
            f"  sanctions applied: {len(outcome.applied)}",
        )

    # ── /attendance post-check-in ──────────────────────────────────────────

    @attendance.command(
        name="post-check-in",
        description="Post a round's check-in call by hand, where the scheduled one never went out.",
    )
    @app_commands.describe(
        division="Division name",
        round="Round number whose check-in call is missing.",
    )
    @league_manager_only
    async def post_check_in(
        self, interaction: discord.Interaction, division: str, round: int
    ) -> None:
        """Post a check-in call a league never received (decided 2026-09-20, #123).

        **A last resort, not a routine.** A call reaches a division from its scheduled job and
        from the recovery that re-arms it after a restart; this is the repair for when both
        have failed, and the advice `_report_call_failure` writes to the log channel — post the
        call again once the cause is cleared — had no command behind it until now. A league
        reaching for this regularly has a cause nobody has fixed.

        It is confined to the window in which a call *should* be standing and is not. Before
        the call is due the scheduled one is still coming, and posting early would override the
        lead time the league configured; after the deadline the buttons lock on arrival, so the
        call would be unanswerable. A deadline of zero disables the *closing* of a check-in and
        not the race, so the round's own moment is the boundary then. A call already standing is **not** replaced — an amendment
        is the path that takes one down and carries the answers over, and a second call beside
        the first would split a division's answers across two messages.

        The post itself is `run_rsvp_notice`, unchanged. That returns silently on every fault
        it handles, so the reply is decided by whether a call *stands* afterwards rather than by
        it returning — otherwise a failed post would be reported as a success.

        **This is the first path that posts a call without first taking one down.** The
        scheduler, the restart recovery and `/test-mode advance` all post a call the round is
        not expected to have; `repost_rsvp_call` withdraws before it posts. So the standing-call
        check is load-bearing here in a way it is nowhere else, and it is made twice — once to
        answer the manager, and again immediately before posting, because the scheduled call
        falls due at the very moment this command's window opens.

        The window left between that second check and the `channel.send` inside
        `run_rsvp_notice` is closed in the shared posting path (#429): `run_rsvp_notice` checks
        for a standing call itself, under the round's check-in lock, so a scheduled call landing
        in that window leaves this one posting nothing. The reply then says a call was posted,
        which is true of the channel. `test_the_scheduled_call_winning_the_race_stops_this_one`
        pins the check made here, which is what lets the reply name the scheduled call.
        """
        if not await self._guard_module_enabled(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await interaction.followup.send(
                "⛔ `/attendance post-check-in` is available only while the season is ongoing.",
                ephemeral=True,
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await interaction.followup.send(
                f"❌ Division '{division}' not found.", ephemeral=True
            )
            return

        db_path = self.bot.db_path
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT id, status, scheduled_at FROM rounds "
                "WHERE division_id = ? AND round_number = ?",
                (div.id, round),
            )
            round_row = await cursor.fetchone()
        if round_row is None:
            await interaction.followup.send(
                f"❌ **{div.name}** has no round {round}.", ephemeral=True
            )
            return
        if round_row["status"] == RoundStatus.CANCELLED.value:
            await interaction.followup.send(
                f"⛔ Round {round} of **{div.name}** is cancelled, so it has no check-in "
                f"to answer.",
                ephemeral=True,
            )
            return

        round_id: int = round_row["id"]
        if await _call_stands(self.bot, round_id, div.id):
            await interaction.followup.send(
                f"⛔ A check-in call is already standing for round {round} of "
                f"**{div.name}**, so nothing was posted. Amend the round if it needs to go "
                f"out again — that carries every answer already given across.",
                ephemeral=True,
            )
            return

        cfg = await self.bot.attendance_service.get_config()
        if cfg is None:
            await interaction.followup.send(
                "\u274c No attendance configuration found. Enable the module first.",
                ephemeral=True,
            )
            return

        scheduled_at = _as_utc(round_row["scheduled_at"])
        now = datetime.now(timezone.utc)

        due_at = scheduled_at - timedelta(days=cfg.rsvp_notice_days)
        if now < due_at:
            await interaction.followup.send(
                f"⛔ The check-in call for round {round} of **{div.name}** is not due "
                f"until <t:{int(due_at.timestamp())}:F>, and is still scheduled to post then. "
                f"Nothing was posted.",
                ephemeral=True,
            )
            return

        # A deadline of zero disables the *closing* of the check-in, not the race itself: the
        # round's own moment is the boundary then, because a call posted after the race has
        # started asks a division to say whether it is racing in something already run. Without
        # this the command would post a call for a race six days past.
        deadline_at = scheduled_at - timedelta(hours=cfg.rsvp_deadline_hours)
        if now >= deadline_at:
            closed = (
                f"closed at <t:{int(deadline_at.timestamp())}:F>"
                if cfg.rsvp_deadline_hours > 0
                else f"started at <t:{int(deadline_at.timestamp())}:F>"
            )
            await interaction.followup.send(
                f"⛔ Round {round} of **{div.name}** {closed}, so a call posted now could "
                f"not be answered. Nothing was posted.",
                ephemeral=True,
            )
            return

        # The scheduled call becomes due at exactly the moment the window above opens, so a
        # manager running this around that moment races it. Were both to post, the second to
        # land would overwrite the first's id and orphan a live call in the channel: still
        # answerable, tracked by nothing, never locked at the deadline. `run_rsvp_notice` now
        # refuses a round whose call stands (#429); checking again here as well is what lets the
        # reply say the scheduled call won.
        if await _call_stands(self.bot, round_id, div.id):
            await interaction.followup.send(
                f"\u26d4 The scheduled check-in call for round {round} of **{div.name}** "
                f"posted while this ran, so nothing was posted on top of it.",
                ephemeral=True,
            )
            return

        from leaguebot.attendance.services.rsvp_service import run_rsvp_notice

        await run_rsvp_notice(round_id, self.bot)

        posted = await _call_stands(self.bot, round_id, div.id)
        if posted:
            await interaction.followup.send(
                f"✅ Check-in call posted for round {round} of **{div.name}**.",
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f"⛔ The check-in call for round {round} of **{div.name}** could not be "
                f"posted. The log channel says why.",
                ephemeral=True,
            )

        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) "
            f"| /attendance post-check-in | {'Success' if posted else 'Failed'}\n"
            f"  division: {div.name}\n"
            f"  round: {round}",
        )

    # ── /attendance channel rsvp and attendance ───────────────────────────
    #
    # Attendance's own commands, under attendance's own group: they sat under core's `/division`
    # until #462 moved them, and only their names changed. Each checks the module first in its
    # own words, not `_guard_module_enabled`'s, as it was worded before it moved; defers; refuses
    # a channel the bot cannot post in; then takes the live season's division (#220), Pending
    # completion included, so a channel lost may be repaired. A channel does one job
    # (`channel_refusal`), checked before the write, and the change is recorded by core's
    # `audit_service`, its ids as integers (#212).

    @channel_group.command(
        name="rsvp",
        description="Set the RSVP notice channel for a division (attendance module).",
    )
    @app_commands.describe(name="Division name", channel="RSVP notice channel")
    @league_manager_only
    async def channel_rsvp(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_attendance_enabled():
            await interaction.response.send_message(
                "\u274c The Attendance module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await interaction.followup.send(
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
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

        refused = await channel_refusal(
            self.bot.db_path, channel, "rsvp", division_name=div.name
        )
        if refused is not None:
            await interaction.followup.send(refused, ephemeral=True)
            return

        old_cfg = await self.bot.attendance_service.get_division_config(div.id)
        # Stored as text; audited as the integer every other channel command writes (#212).
        old_id = int(old_cfg.rsvp_channel_id) if old_cfg and old_cfg.rsvp_channel_id else None

        await self.bot.attendance_service.set_rsvp_channel(div.id, channel.id)

        await audit_service.record_change(
            self.bot.db_path,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            change_type="RSVP_CHANNEL_SET",
            old_value={"channel_id": old_id},
            new_value={"channel_id": channel.id},
            now=datetime.now(timezone.utc),
            division_id=div.id,
        )

        if old_id is None:
            msg = f"\u2705 RSVP channel for {name} set to {channel.mention}."
        else:
            msg = f"\u2705 RSVP channel for {name} updated to {channel.mention}."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /attendance channel rsvp | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    @channel_group.command(
        name="attendance",
        description="Set the attendance logging channel for a division (attendance module).",
    )
    @app_commands.describe(name="Division name", channel="Attendance logging channel")
    @league_manager_only
    async def channel_attendance(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        if not await self.bot.module_service.is_attendance_enabled():
            await interaction.response.send_message(
                "\u274c The Attendance module is not enabled.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild

        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await interaction.followup.send(
                "\u274c Cannot access that channel. Ensure the bot has permission to post there.",
                ephemeral=True,
            )
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await interaction.followup.send(
                "\u274c No season is live. A division's channels belong to the season being built or raced \u2014 start one with `/season setup`.",
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

        refused = await channel_refusal(
            self.bot.db_path, channel, "attendance", division_name=div.name
        )
        if refused is not None:
            await interaction.followup.send(refused, ephemeral=True)
            return

        old_cfg = await self.bot.attendance_service.get_division_config(div.id)
        # Stored as text; audited as the integer every other channel command writes (#212).
        old_id = int(old_cfg.attendance_channel_id) if old_cfg and old_cfg.attendance_channel_id else None

        await self.bot.attendance_service.set_attendance_channel(div.id, channel.id)

        await audit_service.record_change(
            self.bot.db_path,
            actor_id=interaction.user.id,
            actor_name=str(interaction.user),
            change_type="ATTENDANCE_CHANNEL_SET",
            old_value={"channel_id": old_id},
            new_value={"channel_id": channel.id},
            now=datetime.now(timezone.utc),
            division_id=div.id,
        )

        if old_id is None:
            msg = f"\u2705 Attendance channel for {name} set to {channel.mention}."
        else:
            msg = f"\u2705 Attendance channel for {name} updated to {channel.mention}."
        await interaction.followup.send(msg, ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /attendance channel attendance | Success\n"
            f"  division: {name}\n"
            f"  channel: #{channel.name}",
        )

    # ── /attendance test rsvp ─────────────────────────────────────────────

    @test_group.command(
        name="rsvp",
        description="Bulk-set RSVP statuses for test drivers in a division via a modal.",
    )
    @app_commands.describe(
        division="Division name whose active RSVP round to update.",
    )
    @league_admin_only
    async def test_rsvp(
        self,
        interaction: discord.Interaction,
        division: str,
    ) -> None:
        """Open the modal that sets fake drivers' check-in answers for a division's call.

        Attendance's own test tool, under attendance's own group: it sat under core's
        `/test-mode` until #462 moved it, and only its name changed. It is test mode's all the
        same — a league admin's, like every test mode command, and **refused first while test
        mode is off**, whatever else is off with it; then while the attendance module is off;
        then for a division not in a season being raced, or with no call standing.
        """
        config = await self.bot.config_service.get_server_config()
        if config is None or not config.test_mode_active:
            await interaction.response.send_message(
                "ℹ️ Test mode is not active.", ephemeral=True
            )
            return

        # The attendance module gate (issue #114). A call posted while the module was on
        # leaves its `rsvp_embed_messages` row behind, so without this the command finds that
        # embed and writes check-in answers for a module the league has switched off.
        if not await self.bot.module_service.is_attendance_enabled():
            await interaction.response.send_message(
                "❌ The Attendance module is not enabled, so there is no check-in to set.",
                ephemeral=True,
            )
            return

        from leaguebot.core.db.database import get_connection as _gc

        # Validate the division is of a season being raced — one of the three ongoing stages,
        # a check-in belonging to nothing else (issue #220) — and has an active RSVP embed.
        from leaguebot.core.models.season import ONGOING_STAGES

        ongoing = [stage.value for stage in ONGOING_STAGES]
        async with _gc(self.bot.db_path) as db:
            cur = await db.execute(
                f"""
                SELECT d.id AS division_id
                  FROM divisions d
                  JOIN seasons s ON s.id = d.season_id
                 WHERE s.stage IN ({",".join("?" for _ in ongoing)})
                   AND LOWER(d.name) = LOWER(?)
                """,
                (*ongoing, division),
            )
            div_row = await cur.fetchone()

        if div_row is None:
            await interaction.response.send_message(
                f"❌ Division **{division}** not found in a season being raced.", ephemeral=True
            )
            return
        division_id: int = div_row["division_id"]

        target_embed = await self.bot.attendance_service.get_current_embed_message(division_id)
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


# ── /attendance test rsvp: the bulk RSVP status setter ───────────────────

_STATUS_MAP = {
    "accept":   "ACCEPTED",
    "accepted": "ACCEPTED",
    "tentative": "TENTATIVE",
    "decline":  "DECLINED",
    "declined": "DECLINED",
}


class _RsvpBulkSetModal(LeagueModal, title="Bulk Set RSVP Statuses"):
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
        bot: LeagueBot,
    ) -> None:
        super().__init__()
        self._division_name = division_name
        self._division_id = division_id
        self._round_id = round_id
        self._embed_channel_id = embed_channel_id
        self._embed_message_id = embed_message_id
        self._bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        from leaguebot.core.db.database import get_connection as _gc
        from leaguebot.attendance.services.rsvp_service import _rebuild_embed_for_round, RsvpView

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
            discord_uid = parse_user_id(id_str)
            if discord_uid is None:
                errors.append(f"Line {line_no}: `{id_str}` is not a valid numeric ID")
                continue
            new_status = _STATUS_MAP.get(status_str.lower())
            if new_status is None:
                errors.append(
                    f"Line {line_no}: unknown status `{status_str}` "
                    "(use accept, tentative, or decline)"
                )
                continue

            async with _gc(self._bot.db_path) as db:
                cur = await db.execute(
                    "SELECT id FROM driver_profiles "
                    "WHERE CAST(discord_user_id AS INTEGER) = ?",
                    (discord_uid,),
                )
                profile_row = await cur.fetchone()

            if profile_row is None:
                errors.append(f"Line {line_no}: no driver profile for ID `{id_str}`")
                continue
            driver_profile_id: int = profile_row["id"]

            dra = await self._bot.attendance_service.get_attendance_row_for_driver(
                round_id=self._round_id,
                division_id=self._division_id,
                driver_profile_id=driver_profile_id,
            )
            if dra is None:
                errors.append(
                    f"Line {line_no}: driver `{id_str}` has no attendance row for this round"
                )
                continue

            await self._bot.attendance_service.upsert_rsvp_status(
                round_id=self._round_id,
                division_id=self._division_id,
                driver_profile_id=driver_profile_id,
                status=new_status,
            )
            applied.append(f"`{id_str}` → {new_status.lower()}")

        # Rebuild embed once after all updates
        if applied:
            channel = as_text_channel(self._bot.get_channel(self._embed_channel_id))
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
            await self._bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) "
                f"| /attendance test rsvp | {len(applied)} update(s)\n"
                f"  division: {self._division_name}\n"
                f"  round_id: {self._round_id}\n"
                f"  changes: {', '.join(applied)}",
            )


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


async def _call_stands(bot: LeagueBot, round_id: int, division_id: int) -> bool:
    """Whether a check-in call for *round_id* is still recorded as standing.

    The row goes when the call is taken down — by a cancellation, or by an amendment that
    withdraws it — and it is written when one is posted, so its absence means there is no call
    to answer even where the message itself could not be deleted.
    """
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT 1 FROM rsvp_embed_messages WHERE round_id = ? AND division_id = ?",
            (round_id, division_id),
        )
        return await cursor.fetchone() is not None


async def handle_rsvp_button(interaction: discord.Interaction, custom_id: str) -> None:
    """Handle an RSVP button press.

    This function is called by _RsvpButton.callback in rsvp_service.py.
    It performs all validation, locking, DB updates, and embed refresh.

    It carries its own module gate. The cog's slash commands get theirs from
    ``_guard_module_enabled``, but this is a module-level handler reached straight from a
    button on a message that outlives the module being switched off — a call posted while
    attendance was on, whose buttons a driver presses after it went off (issue #114).
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

    bot = bot_of(interaction)
    discord_user_id = interaction.user.id

    # The module gate — see the docstring for why it sits here and not on the cog.
    if not await bot.module_service.is_attendance_enabled():
        await interaction.response.send_message(
            "❌ The Attendance module is switched off for this server, so check-in is "
            "no longer running. Your answer has not been recorded.",
            ephemeral=True,
        )
        return

    # Look up driver profile by Discord user ID (FR-011). Any account the driver has held
    # answers for them, a past one as well as the current (issue #243).
    from leaguebot.core.services.driver_service import resolve_driver_profile_id

    async with get_connection(bot.db_path) as db:
        resolved_profile_id = await resolve_driver_profile_id(discord_user_id, db)

        if resolved_profile_id is None:
            await interaction.response.send_message(
                "❌ You are not registered as a driver in this server.", ephemeral=True
            )
            return
        driver_profile_id: int = resolved_profile_id

        # Get round info
        cur = await db.execute(
            """
            SELECT r.division_id, r.scheduled_at, r.format, r.status,
                   ac.rsvp_deadline_hours
              FROM rounds r
              JOIN divisions d ON d.id = r.division_id
              JOIN seasons s ON s.id = d.season_id
              CROSS JOIN attendance_config ac
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

    # A cancelled round's call is taken down with the cancellation (#175), so a press arriving
    # here is one that raced it, or one on a call the bot was not allowed to delete. Either way
    # there is nothing left to answer, and an answer recorded now would stand beside a round
    # that is off.
    #
    # The withdrawal is read as well as the round's status, because the two are written apart:
    # cancelling a season takes its calls down several steps before the cascade records its
    # rounds cancelled, and a failure in between would leave a round still `NOT_RUN` whose call
    # is gone. A call that no longer stands answers nobody, whatever the round says.
    if round_row["status"] == RoundStatus.CANCELLED.value or not await _call_stands(
        bot, round_id, round_row["division_id"]
    ):
        await interaction.response.send_message(
            "❌ This check-in is no longer open — the round has been cancelled, or its call "
            "has been taken down. Your answer has not been recorded.",
            ephemeral=True,
        )
        return

    division_id: int = round_row["division_id"]

    # Verify the driver holds a confirmed placement in this division (full-time or reserve)
    # (FR-011). Only a driver with one may answer the call: an unconfirmed placement stands
    # outside the championship until `/season placements-review` confirms it (issue #220), so
    # it is called to no check-in and accrues no attendance. This is the one reader of the
    # championship that walked the seats without `uncommitted_seat_excluded`, which went
    # unnoticed while `upsert_rsvp_status` discarded every answer it had no row for; now that
    # it opens rows, the predicate is what stops it opening one here (issue #209).
    #
    # No answer of its own: the refusal below is one message for every way of not being a
    # driver of this division — an unconfirmed placement, a seat in another division, or no
    # driver profile at all. A league manager who does not drive and presses a button out of
    # curiosity is in the same position, and telling them apart would serve nobody.
    async with get_connection(bot.db_path) as db:
        cur = await db.execute(
            f"""
            SELECT ti.is_reserve
              FROM driver_season_assignments dsa
              JOIN team_seats ts ON ts.driver_profile_id = dsa.driver_profile_id
              JOIN team_instances ti ON ti.id = ts.team_instance_id
                                    AND ti.division_id = dsa.division_id
             WHERE dsa.driver_profile_id = ?
               AND dsa.division_id = ?
               AND {uncommitted_seat_excluded("ts")}
            """,  # noqa: S608
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
        scheduled_at = scheduled_at_raw
    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

    deadline_hours: int = round_row["rsvp_deadline_hours"] or 0
    now = datetime.now(timezone.utc)

    # Get current rsvp_status for locking check
    async with get_connection(bot.db_path) as db:
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

    # Upsert status. Reported on rather than assumed: issue #209 was a success message
    # standing over a write that changed nothing, and a driver told their answer was recorded
    # has no way of discovering otherwise until the round is scored against them. The embed
    # is not rebuilt either — it is a view of the answers, and redrawing it here would show
    # the division a state the database does not hold.
    recorded = await bot.attendance_service.upsert_rsvp_status(
        round_id=round_id,
        division_id=division_id,
        driver_profile_id=driver_profile_id,
        status=new_status,
    )
    if not recorded:
        log.error(
            "handle_rsvp_button: recorded no answer for driver %s on round %s / division %s",
            driver_profile_id, round_id, division_id,
        )
        await interaction.response.send_message(
            "❌ Your answer could not be recorded. Please tell a league manager, and do "
            "not assume you are signed up for this round.",
            ephemeral=True,
        )
        return

    # Rebuild and edit embed in-place (FR-010 / FR-012)
    from leaguebot.attendance.services.rsvp_service import _rebuild_embed_for_round, RsvpView
    embed_row = await bot.attendance_service.get_embed_message(round_id, division_id)
    if embed_row is not None:
        channel = as_text_channel(bot.get_channel(int(embed_row.channel_id)))
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
