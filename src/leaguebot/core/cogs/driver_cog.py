"""DriverCog — the member `/driver` command group.

Each command's driver field is a Discord member; what the command does with the driver is
`driver_commands`, shared with the test-mode group (`driver_test_cog`).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.cogs import driver_commands
from leaguebot.core.cogs.driver_commands import ResolvedDriver
from leaguebot.core.services.drivers.driver_changes import PAST_ACCOUNT_REFUSAL
from leaguebot.framework.utils.autocomplete import bounded_autocomplete
from leaguebot.core.utils.team_autocomplete import team_autocomplete
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.input_validator import parse_user_id
from leaguebot.framework.utils.interaction_errors import describe
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.log_lines import refuse

log = logging.getLogger(__name__)


class DriverCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    driver = app_commands.Group(
        name="driver",
        description="Driver profile management commands",
        guild_only=True,
        default_permissions=None,
    )

    async def _current_member(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> discord.Member | None:
        """The member behind the current account of the driver *user* names.

        Any account a driver has held names them, so a manager may give a command one the
        driver has since left. Everything the command does — the roles, the lineup, the
        log — belongs to the account the driver uses now, so it is swapped in here, before
        anything reads it. An account no driver holds is returned as it came.

        Where the current account is no longer in the server there is no member to act on,
        and the command is refused with the manager told so; returns None, having replied.
        """
        current = await self.bot.driver_service.current_account(
            user.id
        )
        if current == str(user.id):
            return user
        guild = interaction.guild
        member = guild.get_member(int(current)) if guild is not None else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(int(current))
            except discord.HTTPException:
                member = None
        if member is None:
            await refuse(
                interaction,
                PAST_ACCOUNT_REFUSAL.format(account=user.id, current=current),
                what=describe(interaction),
            )
        return member

    async def _resolved(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> ResolvedDriver | None:
        """The driver *user* names, as the commands act on them; None, having replied, where the
        driver's current account is no longer in the server."""
        current = await self._current_member(interaction, user)
        if current is None:
            return None
        return ResolvedDriver(str(current.id), current.display_name)

    # ------------------------------------------------------------------
    # /driver reassign
    # ------------------------------------------------------------------

    @driver.command(
        name="reassign",
        description="Make another Discord account a driver's current one.",
    )
    @app_commands.describe(
        old_user="Any account of the driver's (mention; use old_user_id for an account no longer in the server).",
        old_user_id="Raw Discord snowflake ID of any account of the driver's.",
        new_user="The account to make current: a new one, or one of the driver's past accounts.",
    )
    @league_manager_only
    async def reassign(
        self,
        interaction: discord.Interaction,
        new_user: discord.Member,
        old_user: discord.Member | None = None,
        old_user_id: str | None = None,
    ) -> None:
        """Make *new_user* the current account of the driver the old account names.

        Only while drivers are being placed (`driver_commands.reassign_stage_ok`).
        """
        if not await driver_commands.reassign_stage_ok(self.bot, interaction):
            return

        # Resolve old user ID — accept Member mention or raw snowflake string
        if old_user is not None:
            resolved_old_id = str(old_user.id)
        elif old_user_id is not None:
            parsed_old_id = parse_user_id(old_user_id)
            if parsed_old_id is None:
                await refuse(
                    interaction,
                    "⛔ `old_user_id` must be a numeric Discord user ID.",
                    what=describe(interaction),
                )
                return
            resolved_old_id = str(parsed_old_id)
        else:
            await refuse(
                interaction,
                "⛔ You must supply either `old_user` (mention) or `old_user_id` (raw snowflake).",
                what=describe(interaction),
            )
            return

        await driver_commands.reassign(
            self.bot,
            interaction,
            resolved_old_id,
            ResolvedDriver(str(new_user.id), new_user.display_name),
        )

    # ------------------------------------------------------------------
    # /driver assign
    # ------------------------------------------------------------------

    @driver.command(
        name="assign",
        description="Assign an Unassigned driver to a team and division.",
    )
    @app_commands.describe(
        user="The Discord member to assign.",
        division="Division tier number or name.",
        team="The team's shorthand.",
    )
    @league_manager_only
    async def assign(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
        team: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        driver = await self._resolved(interaction, user)
        if driver is None:
            return
        await driver_commands.assign(self.bot, interaction, driver, division, team)

    # ------------------------------------------------------------------
    # /driver unassign
    # ------------------------------------------------------------------

    @driver.command(
        name="unassign",
        description="Remove a driver's placement from a specific division.",
    )
    @app_commands.describe(
        user="The Discord member to unassign.",
        division="Division tier number or name.",
    )
    @league_manager_only
    async def unassign(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        driver = await self._resolved(interaction, user)
        if driver is None:
            return
        await driver_commands.unassign(self.bot, interaction, driver, division)

    # ------------------------------------------------------------------
    # /driver move
    # ------------------------------------------------------------------

    @driver.command(
        name="move",
        description="Move a confirmed driver to another team, in the same division or another.",
    )
    @app_commands.describe(
        user="The Discord member to move.",
        from_division="Division tier number or name the driver is moved from.",
        team="The shorthand of the team the driver is moved into.",
        to_division="Division tier number or name moved into. Omit for the same division.",
    )
    @league_manager_only
    async def move(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        from_division: str,
        team: str,
        to_division: str | None = None,
    ) -> None:
        """Move a committed driver in one step (`driver_commands.move`)."""
        await interaction.response.defer(ephemeral=True)
        driver = await self._resolved(interaction, user)
        if driver is None:
            return
        await driver_commands.move(self.bot, interaction, driver, from_division, team, to_division)

    # ------------------------------------------------------------------
    # /driver release
    # ------------------------------------------------------------------

    @driver.command(
        name="release",
        description="Release a confirmed driver from one division, keeping their other seats.",
    )
    @app_commands.describe(
        user="The Discord member to release.",
        division="Division tier number or name to release them from.",
    )
    @league_manager_only
    async def release(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        division: str,
    ) -> None:
        """Release a committed driver from one division, in the ongoing stages."""
        await interaction.response.defer(ephemeral=True)
        driver = await self._resolved(interaction, user)
        if driver is None:
            return
        await driver_commands.release(self.bot, interaction, driver, division)

    # ------------------------------------------------------------------
    # /driver reject
    # ------------------------------------------------------------------

    @driver.command(
        name="reject",
        description="Turn down an approved driver who has not been placed.",
    )
    @app_commands.describe(user="The Unassigned driver to turn down.")
    @league_manager_only
    async def reject(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        """Turn down an Unassigned driver (`driver_commands.reject`)."""
        await interaction.response.defer(ephemeral=True)
        driver = await self._resolved(interaction, user)
        if driver is None:
            return
        await driver_commands.reject(self.bot, interaction, driver)

    # ------------------------------------------------------------------
    # /driver sack
    # ------------------------------------------------------------------

    @driver.command(
        name="sack",
        description="Sack a driver: revoke all roles, clear assignments, revert to Not Signed Up.",
    )
    @app_commands.describe(
        user="The Discord member to sack.",
    )
    @league_admin_only
    async def sack(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        driver = await self._resolved(interaction, user)
        if driver is None:
            return
        await driver_commands.sack(self.bot, interaction, driver)

    # ------------------------------------------------------------------
    # The team parameter's autocomplete
    # ------------------------------------------------------------------

    @bounded_autocomplete()
    async def _team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """A team is typed by its shorthand; offered under both its names, Reserve included."""
        return await team_autocomplete(self.bot, current, include_reserve=True)

    assign.autocomplete("team")(_team_autocomplete)
    move.autocomplete("team")(_team_autocomplete)
