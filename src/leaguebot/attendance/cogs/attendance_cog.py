"""AttendanceCog — /attendance config, sync, post-check-in, channel and test commands."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import ChangeOrigin
from leaguebot.core.models.season import ONGOING_STAGES
from leaguebot.attendance.services.channel_settings import (
    ATTENDANCE_CHANNEL,
    NO_ACCESS,
    NOT_ENABLED,
    RSVP_CHANNEL,
    division_not_found,
)
from leaguebot.attendance.services.check_in_answer_changes import (
    ask_check_in_answer,
    ask_check_in_answers,
)
from leaguebot.attendance.services.check_in_changes import ask_check_in_call, judge_hand_post
from leaguebot.attendance.services.attendance_settings import KINDS
from leaguebot.attendance.services.attendance_recompute import recalculation_faults, sync_attendance
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL
from leaguebot.core.utils.catch_up_gate import after_the_catch_up
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only, changes_nothing
from leaguebot.core.utils.input_validator import parse_user_id
from leaguebot.core.utils.interaction_errors import describe, describe_form
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import LeagueModal, LeagueView, guild_of
from leaguebot.core.utils.log_lines import interaction_member, refuse

#: Waits for the catch-up after a restart before a command answered off the change queue acts.
_after_the_catch_up = after_the_catch_up(lambda cog: cog.bot.change_queue)


log = logging.getLogger(__name__)

#: The kind of change each `/attendance channel` command asks of the queue.
_CHANNEL_KINDS = {"rsvp": RSVP_CHANNEL, "attendance": ATTENDANCE_CHANNEL}


def _command(interaction: discord.Interaction) -> str:
    """The slash command *interaction* ran, as a success line names it: "/attendance sync"."""
    return describe(interaction).strip("`")


def _line(
    interaction: discord.Interaction, what: str, outcome: str, details: tuple[str, ...]
) -> str:
    return (
        f"{interaction_member(interaction)} | {what} | {outcome}"
        + "".join(f"\n  {detail}" for detail in details)
    )


async def _record(
    bot: LeagueBot,
    interaction: discord.Interaction,
    what: str,
    outcome: str,
    *details: str,
) -> None:
    """Write one line in the success form, "Name (<@id>) | *what* | *outcome*", *details* beneath.

    For a success and for nothing changed, where the line is not saved with what it records (the
    settings' lines are, by their change). The post is bare: a line reporting that nothing went
    wrong is no place for a catch-all, so a post that raises reaches the command's error
    handling. A press that failed without raising has `_record_failed`, and a refusal, a cancel,
    a lapse and a fault have lines of their own (`core/utils/log_lines.py`, `report_failure`).
    """
    await bot.output_router.post_log(_line(interaction, what, outcome, details))


async def _record_failed(
    bot: LeagueBot, interaction: discord.Interaction, what: str, *details: str
) -> None:
    """Write the "Failed" line of *what*, as `/attendance post-check-in`'s does.

    It reports a failure, and, as `report_failure` does, it must not raise where the member has
    been answered: a log channel that is down is the host's log's to hear of, and nothing else's.
    """
    try:
        await bot.output_router.post_log(_line(interaction, what, "Failed", details))
    except Exception as exc:  # noqa: BLE001 — the report itself might fail; the member is answered
        log.error(
            "could not record in the log channel that %s failed: %s", what, exc, exc_info=True
        )


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

    async def _module_gate(self, interaction: discord.Interaction, *, record: bool = True) -> bool:
        """Whether the attendance module is on; where it is not, refuse and return False.

        The refusal is recorded in the log channel, as every refusal of a command that acts is,
        unless *record* is False, which `config show` passes: it changes nothing and records
        nothing.
        """
        if not await self.bot.module_service.is_attendance_enabled():
            reply = (
                "❌ The Attendance module is not enabled. "
                "Use `/module enable attendance` first."
            )
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return False
        return True

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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
    @_after_the_catch_up
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
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "\u26d4 `/attendance sync` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division '{division}' not found.",
                what=describe(interaction),
            )
            return

        db_path = self.bot.db_path

        # **Not while a round of the division is being amended** (#345, decided 2026-09-21). The
        # recalculation reads the results, which hold an open amendment's corrections before
        # they are approved, and publishes the sheet and applies the sanctions it finds —
        # neither of which a cancelled or lapsed amendment then takes back.
        from leaguebot.results.services.round_amendment.amendment_writes import (
            amendment_wait_text,
            open_amendment_in_division,
        )

        held = await open_amendment_in_division(db_path, div.id)
        if held is not None:
            await refuse(
                interaction,
                f"\u23f8\ufe0f Round {held['round_number']} of **{div.name}** is being amended "
                f"in <#{held['channel_id']}>, and its corrections are not approved yet, so its "
                f"attendance cannot be recalculated until that ends — {amendment_wait_text()}. "
                "Run this again then.",
                what=describe(interaction),
            )
            return

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT id, status FROM rounds WHERE division_id = ? AND round_number = ?",
                (div.id, round),
            )
            round_row = await cursor.fetchone()
        if round_row is None:
            await refuse(
                interaction,
                f"\u274c **{div.name}** has no round {round}.",
                what=describe(interaction),
            )
            return
        if round_row["status"] not in ("AWAITING_APPEAL_VERDICTS", "FINAL"):
            await refuse(
                interaction,
                f"\u26d4 Round {round} of **{div.name}** has not had its penalties approved "
                f"yet, so it holds no attendance to recalculate.",
                what=describe(interaction),
            )
            return

        faults = await recalculation_faults(
            db_path, season.id, interaction.guild, self.bot
        )
        if faults:
            await refuse(
                interaction,
                "\u26d4 Nothing was changed \u2014 the attendance could not be posted:\n\u2022 "
                + "\n\u2022 ".join(faults),
                what=describe(interaction),
                reason="the attendance could not be posted:\n"
                + "\n".join(f"\u2022 {fault}" for fault in faults),
            )
            return

        outcome = await sync_attendance(
            self.bot, guild_of(interaction), db_path, div.id, round_row["id"], season.id,
            queue=self.bot.change_queue,
        )

        lines = [f"\u2705 Attendance of **{div.name}** recalculated from round {round} on."]
        if outcome.applied:
            lines.append("Sanctions applied:")
            lines += [f"\u2022 {driver} \u2014 {sanction}" for driver, sanction in outcome.applied]
            lines.append("Their announcements follow on the change queue.")
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
        have failed, and the advice a failed call's line gives in the log channel — post the
        call again once the cause is cleared — had no command behind it until now. A league
        reaching for this regularly has a cause nobody has fixed.

        It is confined to the window in which a call *should* be standing and is not. Before
        the call is due the scheduled one is still coming, and posting early would override the
        lead time the league configured; after the deadline the buttons lock on arrival, so the
        call would be unanswerable. A deadline of zero disables the *closing* of a check-in and
        not the race, so the round's own moment is the boundary then. A call already standing is **not** replaced — an amendment
        is the path that takes one down and carries the answers over, and a second call beside
        the first would split a division's answers across two messages.

        **It is a change on the queue** (#439): the call is `attendance.check_in.call`, the change
        the check-in timer asks, asked with the manager's interaction. The manager is told it is
        under way and then what became of it, never a success for a post Discord refused. What
        the window allows is judged here to answer at once and held again when the change runs, so
        a scheduled call racing this one is met by the queue running one change at a time, and the
        reply names it.
        """
        if not await self._module_gate(interaction):
            return
        await interaction.response.defer(ephemeral=True)

        season = await self.bot.season_service.get_confirmed_season()
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "⛔ `/attendance post-check-in` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == division.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"❌ Division '{division}' not found.",
                what=describe(interaction),
            )
            return

        # What the window allows is judged here to answer at once, and again by the change when
        # its turn comes (`hand_post_refusal`): a queue stopped for a while can bring it up to
        # run after its deadline, or beside a call the timer has posted meanwhile.
        round_id, refusal = await judge_hand_post(
            self.bot.db_path,
            await self.bot.attendance_service.get_config(),
            div.id,
            div.name,
            round,
            now=datetime.now(timezone.utc),
        )
        if refusal is not None or round_id is None:
            await refuse(interaction, refusal or "", what=describe(interaction))
            return

        await ask_check_in_call(
            self.bot.change_queue,
            self.bot.db_path,
            round_id,
            origin=ChangeOrigin.MEMBER,
            interaction=interaction,
            refusal_what=describe(interaction),
        )

    # ── /attendance channel rsvp and attendance ───────────────────────────
    #
    # Attendance's own commands, under attendance's own group.

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "rsvp" | "attendance"
    ) -> None:
        """Ask the change queue to set a division's RSVP or attendance channel, for the two
        commands below.

        Each checks the module first in its own words, not `_module_gate`'s, then refuses a
        channel the bot cannot post in.

        **The live season's division**: a division's channels belong to the season being built or
        raced, and an archived one's no longer matter. Pending completion is live, so a channel
        lost before the season completes may be repaired. The command finds the division from the
        name typed, since the change is for it by its id (`attendance_channel_settings`).

        **A channel does one job** (`channel_registry_service`, judged by the setting's `check`),
        as the queue is asked and again as the change starts, so a refusal leaves the
        configuration exactly as it stood — the value the setting already holds included. The
        queue's acknowledgement is the command's response, so nothing is deferred; the queue
        writes the channel and its audit entry, its ids as integers, in one save and answers and
        logs after it.

        Every refusal is recorded: `refuse` answers and writes one line naming the command, which
        is read from the interaction.
        """
        if not await self.bot.module_service.is_attendance_enabled():
            await refuse(interaction, NOT_ENABLED, what=describe(interaction))
            return

        guild = interaction.guild
        if guild is None or not channel.permissions_for(guild.me).send_messages:
            await refuse(interaction, NO_ACCESS, what=describe(interaction))
            return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(interaction, NO_SEASON_REFUSAL, what=describe(interaction))
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(interaction, division_not_found(name), what=describe(interaction))
            return

        await self.bot.change_queue.ask(
            _CHANNEL_KINDS[channel_type],
            {
                "division_id": div.id,
                "division_name": name,
                "value": channel.id,
                "channel_name": channel.name,
            },
            interaction=interaction,
            what=describe(interaction),
        )

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
        await self._set_division_channel(interaction, name, channel, "rsvp")

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
        await self._set_division_channel(interaction, name, channel, "attendance")

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
            await refuse(
                interaction,
                "ℹ️ Test mode is not active.",
                what=describe(interaction),
            )
            return

        # The attendance module gate (issue #114). A call posted while the module was on
        # leaves its `rsvp_embed_messages` row behind, so without this the command finds that
        # embed and writes check-in answers for a module the league has switched off.
        if not await self.bot.module_service.is_attendance_enabled():
            await refuse(
                interaction,
                "❌ The Attendance module is not enabled, so there is no check-in to set.",
                what=describe(interaction),
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
                SELECT d.id AS division_id, d.name AS division_name
                  FROM divisions d
                  JOIN seasons s ON s.id = d.season_id
                 WHERE s.stage IN ({",".join("?" for _ in ongoing)})
                   AND LOWER(d.name) = LOWER(?)
                """,
                (*ongoing, division),
            )
            div_row = await cur.fetchone()

        if div_row is None:
            await refuse(
                interaction,
                f"❌ Division **{division}** not found in a season being raced.",
                what=describe(interaction),
            )
            return
        division_id: int = div_row["division_id"]

        target_embed = await self.bot.attendance_service.get_current_embed_message(division_id)
        if target_embed is None:
            await refuse(
                interaction,
                f"❌ No active RSVP embed found for division **{division}**. "
                "Run `/test-mode advance` to fire the RSVP notice first.",
                what=describe(interaction),
            )
            return

        await interaction.response.send_modal(
            _RsvpBulkSetModal(
                division_name=div_row["division_name"],
                division_id=division_id,
                round_id=target_embed.round_id,
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
        embed_message_id: int,
        bot: LeagueBot,
    ) -> None:
        super().__init__()
        self._division_name = division_name
        self._division_id = division_id
        self._round_id = round_id
        self._embed_message_id = embed_message_id
        self._bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Ask the change queue to set the pasted answers, and record what is turned away here.

        **Test mode, the module and the call are checked again here** (#482, decided
        2026-10-01). `/attendance test rsvp` checked all three when it opened the form, and a
        form stands open for minutes: test mode or the module switched off in that time, or the
        call taken down, posted afresh, or no longer the division's open one (in a
        double-header, once its deadline passes the next call is), and the answers would land
        on a state the command would have refused. Each is refused, with nothing set.

        The lines are read here, and every one that cannot be used is named by its number. What
        can be is asked of the queue as one `attendance.check_in.answer` (#439, slice 6b): its
        save writes every answer and its redraw draws the call once, and the reply, updated, says
        what was applied. A paste with nothing usable is a refusal, its rejected entries the
        reason; a paste of blank lines asks nothing and records that nothing changed.
        """
        form = f"{describe_form(self)} for {self._division_name}"
        config = await self._bot.config_service.get_server_config()
        if config is None or not config.test_mode_active:
            await refuse(
                interaction,
                "\u2139\ufe0f Test mode was switched off while this form was open. Nothing was set.",
                what=form,
            )
            return
        if not await self._bot.module_service.is_attendance_enabled():
            await refuse(
                interaction,
                "\u274c The Attendance module was switched off while this form was open. "
                "Nothing was set.",
                what=form,
            )
            return
        current = await self._bot.attendance_service.get_current_embed_message(self._division_id)
        if current is None or int(current.message_id) != self._embed_message_id:
            await refuse(
                interaction,
                "\u274c The check-in call this form was opened on is no longer the division's "
                "open call. Nothing was set. Run `/attendance test rsvp` again.",
                what=form,
            )
            return

        now = datetime.now(timezone.utc)
        entries: list[dict[str, Any]] = []
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

            driver_profile_id = await self._bot.attendance_service.driver_profile_id_for_account(
                discord_uid
            )
            if driver_profile_id is None:
                errors.append(f"Line {line_no}: no driver profile for ID `{id_str}`")
                continue

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
            entries.append({
                "line": line_no, "shown": id_str, "driver_profile_id": driver_profile_id,
                "status": new_status,
            })

        if not entries:
            if errors:
                lines = "⚠️ Errors:\n" + "\n".join(f"  • {e}" for e in errors)
                await refuse(interaction, lines, what=form, reason="\n".join(errors))
                return
            await interaction.response.send_message("No valid entries.", ephemeral=True)
            await _record(
                self._bot, interaction, "/attendance test rsvp", "Nothing changed",
                f"division: {self._division_name}", "no entries given",
            )
            return
        await ask_check_in_answers(
            self._bot.change_queue, interaction, form=form,
            division_name=self._division_name, division_id=self._division_id,
            round_id=self._round_id, embed_message_id=self._embed_message_id,
            entries=entries, passed_over=errors, pressed_at=now,
        )


# ── RSVP button interaction handler (T011 / T012 / T014) ─────────────────────

# Status string mapped from button action name
_ACTION_TO_STATUS = {
    "accept":   "ACCEPTED",
    "tentative": "TENTATIVE",
    "decline":  "DECLINED",
}

#: Each button's label as a log line names it, without its emoji.
_ACTION_LABELS = {
    "accept":    "Accept",
    "tentative": "Tentative",
    "decline":   "Decline",
}

class RsvpView(LeagueView):
    """Persistent RSVP view — three action buttons (Accept / Tentative / Decline).

    custom_id values embed the round_id so handlers can identify the target round
    without querying an extra DB table.  Timeout=None keeps the view alive across
    bot restarts when re-registered via bot.add_view(). The id is `rsvp_{action}_r{round_id}`,
    written here and read back by `handle_rsvp_button`, the only two places that know it. The
    services that post or redraw a call are handed a way to build this view
    (`CheckInViewFactory`), as they may not import a cog.
    """

    def __init__(self, round_id: int = 0) -> None:
        super().__init__(timeout=None)
        self._round_id = round_id
        # Buttons must be added dynamically so custom_ids include the round_id.
        # discord.py requires custom_id to be set at construction for persistence.
        self.add_item(_RsvpButton("accept",   round_id, "✅ Accept",   discord.ButtonStyle.success))
        self.add_item(_RsvpButton("tentative", round_id, "❓ Tentative", discord.ButtonStyle.secondary))
        self.add_item(_RsvpButton("decline",  round_id, "❌ Decline",  discord.ButtonStyle.danger))


class _RsvpButton(discord.ui.Button):
    def __init__(
        self,
        action: str,
        round_id: int,
        label: str,
        style: discord.ButtonStyle,
    ) -> None:
        self._custom_id = f"rsvp_{action}_r{round_id}"
        super().__init__(label=label, style=style, custom_id=self._custom_id)
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        # The press is handled here, beside the id it carries: validation, the module gate and
        # the answer asked of the change queue.
        await handle_rsvp_button(interaction, self._custom_id)


async def handle_rsvp_button(interaction: discord.Interaction, custom_id: str) -> None:
    """Handle an RSVP button press.

    This function is called by `_RsvpButton.callback`, above. It reads the button (an id it cannot
    read is a fault, raised, and an action it does not know a refusal) and
    asks the change queue for the answer (`attendance.check_in.answer`, #439), which saves it and
    redraws the call when its turn comes. The press is acknowledged privately as under way and
    the acknowledgement updated with what became of it; a press the locks, the round or the
    driver's place bar is refused as it is asked, and again if it is barred as it comes to run.
    The answer is judged at the moment the button was pressed (Constitution XIII).

    It carries its own module gate. The cog's slash commands get theirs from
    ``_module_gate``, but this is a module-level handler reached straight from a
    button on a message that outlives the module being switched off — a call posted while
    attendance was on, whose buttons a driver presses after it went off (issue #114).

    **Every press is recorded in the log channel** (#482, decided 2026-09-29: every driver
    press is logged, each check-in answer included): a refusal as `refuse` writes it, and an
    answer given, an answer already held and an answer that could not be recorded in the success
    form. A line names the button and, once the round is read, the call it sits on: "the
    “Accept” button of the check-in call for round 3 of Division 1".
    """
    # Parse action and round_id from custom_id: rsvp_{action}_r{round_id}. The bot writes every
    # id itself (`_RsvpButton`), so one it cannot read is a fault of the bot's, not a refusal of
    # the driver's: it raises, and the view's `on_error` reports it through `report_failure`.
    # An action it does not know, below, is a refusal: a button an older version left behind.
    try:
        # Strip "rsvp_" prefix, then split off "_r{round_id}" suffix
        without_prefix = custom_id[len("rsvp_"):]       # e.g. "accept_r42"
        action_part, round_id_str = without_prefix.rsplit("_r", 1)
        round_id = int(round_id_str)
        action = action_part  # "accept" | "tentative" | "decline"
    except (ValueError, IndexError) as exc:
        raise ValueError(f"a check-in button carries an id the bot cannot read: {custom_id!r}") from exc

    new_status = _ACTION_TO_STATUS.get(action)
    if new_status is None:
        await refuse(
            interaction, "❌ Internal error: unknown action.", what="a check-in call button"
        )
        return
    button = f"the \u201c{_ACTION_LABELS[action]}\u201d button"

    bot = bot_of(interaction)

    # The module gate — see the docstring for why it sits here and not on the cog.
    if not await bot.module_service.is_attendance_enabled():
        await refuse(
            interaction,
            "❌ The Attendance module is switched off for this server, so check-in is "
            "no longer running. Your answer has not been recorded.",
            what=f"{button} of a check-in call",
        )
        return

    await ask_check_in_answer(
        bot.change_queue, bot.db_path, interaction,
        round_id=round_id, status=new_status, button=button,
    )
