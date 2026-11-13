"""`TestCommands`: the `/attendance test` subgroup, and the form it opens.

`/attendance test rsvp` opens `_RsvpBulkSetModal`, which sets fake drivers' check-in answers for
a division's call; the modal is here with it.
"""
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

from leaguebot.attendance.cogs.attendance_group import (
    ATTENDANCE_GROUP,
    AttendanceSubgroup,
    record,
)
from leaguebot.attendance.services.check_in.check_in_answer_changes import ask_check_in_answers
from leaguebot.core.services.seasons.season_service import division_in_ongoing_season
from leaguebot.core.utils.channel_guard import league_admin_only
from leaguebot.core.utils.input_validator import parse_user_id
from leaguebot.core.utils.interaction_errors import describe, describe_form
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import LeagueModal
from leaguebot.core.utils.log_lines import refuse


class TestCommands(AttendanceSubgroup):
    test_group = app_commands.Group(
        name="test",
        description="Test-mode attendance utilities.",
        parent=ATTENDANCE_GROUP,
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

        # Validate the division is of a season being raced — one of the three ongoing stages,
        # a check-in belonging to nothing else (issue #220) — and has an active RSVP embed.
        found = await division_in_ongoing_season(self.bot.db_path, division)

        if found is None:
            await refuse(
                interaction,
                f"❌ Division **{division}** not found in a season being raced.",
                what=describe(interaction),
            )
            return
        division_id = found.id

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
                division_name=found.name,
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

        now = self._bot.change_queue.now()
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
            await record(
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
