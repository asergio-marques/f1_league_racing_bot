"""DivisionCog — the /division command group.

Commands:
  /division add              — add a division to pending setup
  /division duplicate        — copy a division with datetime offset, in placements
  /division delete           — remove a division from pending setup
  /division rename           — rename a division while the season is in placements
  /division amend            — amend a division's name, tier or role while the season is in placements
  /division cancel           — cancel a division in the active season
  /division lineup-channel   — set a division's lineup channel
  /division calendar-channel — set a division's calendar channel
  /division calendar-sync    — post a division's calendar again
"""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services.cancellation_changes import DIVISION_CANCEL
from leaguebot.core.services.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.services.division_add_change import KIND as DIVISION_ADD
from leaguebot.core.services.division_duplicate_change import KIND as DIVISION_DUPLICATE
from leaguebot.core.services.division_delete_change import DIVISION_DELETE
from leaguebot.core.services.division_edit_changes import DIVISION_AMEND, DIVISION_RENAME
from leaguebot.core.services.season_end_changes import season_end_in_hand, season_end_refusal
from leaguebot.core.services.season_service import SeasonImmutableError
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.log_lines import refuse


class DivisionCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

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
    @league_manager_only
    async def division_add(
        self,
        interaction: discord.Interaction,
        name: str,
        role: discord.Role,
        tier: int,
    ) -> None:
        """Add a division: ask the change queue for it, which refuses what the command has always
        refused at the ask and again as the change starts, and adds the division with its teams
        in one save (`core/services/division_add_change.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_ADD, {"name": name, "role_id": role.id, "tier": tier},
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="duplicate",
        description="Copy a division's rounds with a datetime offset while the season is in placements.",
    )
    @app_commands.describe(
        source_name="Name of the division to copy from",
        new_name="Name for the new division",
        role="The Discord role to mention for the new division",
        tier="Tier number for the new division (must be unique within this season)",
        day_offset="Days to shift all round datetimes (can be negative)",
        hour_offset="Hours to shift all round datetimes (can be negative, decimals OK)",
    )
    @league_manager_only
    async def division_duplicate(
        self,
        interaction: discord.Interaction,
        source_name: str,
        new_name: str,
        role: discord.Role,
        tier: int,
        day_offset: int = 0,
        hour_offset: float = 0.0,
    ) -> None:
        """Copy a division: ask the change queue for it, which refuses what the command has always
        refused at the ask and again as the change starts, and saves the copy with its rounds, its
        teams and its audit entry in one save (`core/services/division_duplicate_change.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_DUPLICATE,
            {
                "source_name": source_name, "new_name": new_name, "role_id": role.id,
                "tier": tier, "day_offset": day_offset, "hour_offset": hour_offset,
            },
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="delete",
        description="Remove a division and all its rounds from pending setup.",
    )
    @app_commands.describe(name="Name of the division to delete")
    @league_admin_only
    async def division_delete(
        self,
        interaction: discord.Interaction,
        name: str,
    ) -> None:
        """Delete a division: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and deletes the division, its
        rounds and every module's rows of them with its audit entry in one save
        (`core/services/division_delete_change.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_DELETE, {"name": name},
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="rename",
        description="Rename a division while the season is in placements.",
    )
    @app_commands.describe(
        current_name="Current name of the division",
        new_name="New name for the division",
    )
    @league_manager_only
    async def division_rename(
        self,
        interaction: discord.Interaction,
        current_name: str,
        new_name: str,
    ) -> None:
        """Rename a division: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and renames the division with
        its audit entry in one save (`core/services/division_edit_changes.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_RENAME, {"current_name": current_name, "new_name": new_name},
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="amend",
        description="Amend a division's name, tier, or role while the season is in placements.",
    )
    @app_commands.describe(
        name="Current name of the division",
        new_name="New name for the division (optional)",
        tier="New tier number (optional, must be unique within this season)",
        role="New Discord role (optional)",
    )
    @league_manager_only
    async def division_amend(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        tier: int | None = None,
        role: discord.Role | None = None,
    ) -> None:
        """Amend a division: ask the change queue for it, which refuses what the command has
        always refused at the ask and again as the change starts, and changes what was given of
        the division with its audit entry in one save
        (`core/services/division_edit_changes.py`)."""
        await self.bot.change_queue.ask(
            DIVISION_AMEND,
            {
                "name": name, "new_name": new_name, "tier": tier,
                "role_id": role.id if role is not None else None,
                "role_name": role.name if role is not None else None,
            },
            interaction=interaction, what=describe(interaction),
        )

    @division.command(
        name="cancel",
        description="Cancel a division of the season being raced (irreversible).",
    )
    @app_commands.describe(
        name="Name of the division to cancel",
        confirm='Type "CONFIRM" to proceed.',
    )
    @league_admin_only
    async def division_cancel(
        self,
        interaction: discord.Interaction,
        name: str,
        confirm: str,
    ) -> None:
        if confirm != "CONFIRM":
            await refuse(
                interaction,
                "\u274c Type exactly `CONFIRM` in the `confirm` field to proceed.",
                what=describe(interaction),
            )
            return

        season = await self.bot.season_service.get_confirmed_season()
        from leaguebot.core.models.season import ONGOING_STAGES

        # Not while the season's own end is in hand: refused at once, naming the job, ahead of
        # the stage gate.
        if season is not None:
            hand = await season_end_in_hand(self.bot.db_path, season.id)
            if hand is not None:
                await refuse(
                    interaction,
                    season_end_refusal(None, hand, season.season_number),
                    what=describe(interaction),
                )
                return

        # Available only while the season is ongoing.
        if season is None or season.stage not in ONGOING_STAGES:
            await refuse(
                interaction,
                "\u274c `/division cancel` is available only while the season is ongoing.",
                what=describe(interaction),
            )
            return

        try:
            await self.bot.season_service.assert_season_mutable(season)
        except SeasonImmutableError:
            await refuse(
                interaction,
                "\u274c This season is archived (COMPLETED) and cannot be modified.",
                what=describe(interaction),
            )
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"\u274c Division `{name}` not found.",
                what=describe(interaction),
            )
            return

        # Whether the division is already cancelled is the change type's check, made now and again
        # as the cancellation comes up to run. The queue's acknowledgement is this command's
        # response, so nothing is deferred.
        await self.bot.change_queue.ask(
            DIVISION_CANCEL,
            {
                "division_id": div.id,
                "division_name": div.name,
                "season_number": season.season_number,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # Division channel assignment
    # ------------------------------------------------------------------

    async def _ask_division_channel(
        self,
        interaction: discord.Interaction,
        kind: str,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Ask the change queue to give the division *name* of the live season *channel* as the
        channel *kind* (`division_settings`) names.

        The season and the division are found here, since the change is for the division by its
        id; the channel's use is the setting's own `check`, made as the queue is asked and again
        as the change starts. The queue's acknowledgement is the command's response, so nothing
        is deferred.
        """
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
            kind,
            {
                "division_id": div.id,
                "division_name": name,
                "value": channel.id,
                "channel_name": channel.name,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @division.command(
        name="lineup-channel",
        description="Set the lineup posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Lineup channel")
    @league_manager_only
    async def division_lineup_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._ask_division_channel(
            interaction, "core.setting.lineup_channel_id", name, channel
        )

    @division.command(
        name="calendar-channel",
        description="Set the calendar posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Calendar channel")
    @league_manager_only
    async def division_calendar_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._ask_division_channel(
            interaction, "core.setting.calendar_channel_id", name, channel
        )

    @division.command(
        name="calendar-sync",
        description="Redraw a division's calendar and replace the posted message.",
    )
    @app_commands.describe(name="Division name")
    @league_manager_only
    async def division_calendar_sync(
        self, interaction: discord.Interaction, name: str
    ) -> None:
        """Post a division's calendar anew, as a change on the change queue.

        Gated on **no** module: it refreshes whichever form the server's configuration
        calls for — the graphic where the images module and the `calendar` aspect are both
        on, the traditional textual calendar otherwise.

        The command keeps its gates and answers them at once in its own words; the posting is
        the queue's (`calendar_post_service.calendar_sync_change`), acknowledged and the
        acknowledgement updated with the outcome, the old calendar deleted by a job after the
        new one stands. A **commanded** posting, so a fatal render rejects the change and posts
        nothing rather than quietly substituting text (Constitution XIV.7): the person at the
        keyboard is the one able to fix the template, and the outcome says that nothing was
        deleted and the previous calendar still stands.

        **The season's number is passed bare, and a falsy one is drawn rather than hidden.**
        `seasons.season_number` is `NOT NULL` with no default and numbering starts at one, so
        a season reaching this command always carries a real number; a `0` can only come from
        a malformed row and is a bug. The obvious reflex, `season.season_number or None`,
        would empty the template's field and silently drop the whole "SEASON n" line where
        the template groups it — a quiet failure. Drawing "SEASON 0" is visibly wrong and
        gets reported, so the value goes through untouched and the job logs the fault beside
        it. Pinned by `test_a_season_with_no_number_still_draws_and_is_logged`.
        """
        from leaguebot.core.services import calendar_post_service as _calendar
        from leaguebot.core.utils.season_gate import season_for_command

        await interaction.response.defer(ephemeral=True)

        # Refused once every division is done: the calendar describes rounds somebody will drive,
        # and a season pending completion has none left. It is no part of the end-of-season output
        # either — completing posts the final classification and the final attendance sheet, and
        # neither is a calendar.
        season = await season_for_command(
            interaction, self.bot.season_service, "division calendar-sync"
        )
        if season is None:
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            await refuse(
                interaction,
                f"❌ Division **{name}** not found in the current season.",
                what=describe(interaction),
            )
            return

        if not div.calendar_channel_id:
            await refuse(
                interaction,
                f"❌ **{name}** has no calendar channel configured. "
                f"Set one with `/division calendar-channel` first.",
                what=describe(interaction),
            )
            return

        await self.bot.change_queue.ask(
            _calendar.CALENDAR_SYNC,
            {
                "division_id": div.id,
                "division_name": div.name,
                "season_id": season.id,
                "season_number": season.season_number,
            },
            interaction=interaction,
            what=f"posting the calendar of **{div.name}** again",
            refusal_what=describe(interaction),
        )
