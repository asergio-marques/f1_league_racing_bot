"""DriverTestCog — the test-mode `/driver` command group (#600).

Discord's member field is filled with a real account only, so a fake driver's ID never reaches a
command that takes a `discord.Member`. While test mode is on, `/driver` is this group instead of the
member one (`driver_cog`): the same commands at the same tiers, each driver field a text field
taking a mention or an ID, with the server's drivers offered by name as it is typed. The text is
resolved to a driver through any account they have held (`driver_reference`), and what the command
then does is `driver_commands`', as for the member group, so a fake driver is acted on as a real
one is.

**Which group the server shows is the swap's** (`test_mode/driver_commands.py`): the member group is
global and this one is put on the league's server only while test mode is on. So that the two never
clash on the global tree, this cog adds no command to it as it loads. Every command re-reads the
flag as it is given and is refused while test mode is off, as the swap may have failed to take the
group down; a restart puts `/driver` right.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.cogs import driver_commands
from leaguebot.core.cogs.driver_commands import ResolvedDriver
from leaguebot.core.services.drivers.driver_names import read_driver_names
from leaguebot.core.services.drivers.driver_reference import driver_choices, resolve_driver
from leaguebot.core.utils.channel_guard import league_admin_only, league_manager_only
from leaguebot.core.utils.input_validator import parse_user
from leaguebot.core.utils.team_autocomplete import team_autocomplete
from leaguebot.framework.utils.autocomplete import bounded_autocomplete
from leaguebot.framework.utils.interaction_errors import describe
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.log_lines import refuse

log = logging.getLogger(__name__)

#: What a command is refused with while test mode is off.
TEST_MODE_OFF_REFUSAL = (
    "⛔ Test mode is off, so this `/driver` no longer applies. The `/driver` commands will be "
    "put right when the bot next restarts."
)

#: What the typed text is clipped to where a refusal names it.
_TEXT_SHOWN = 100


class DriverTestCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    driver = app_commands.Group(
        name="driver",
        description="Driver profile management commands",
        guild_only=True,
        default_permissions=None,
    )

    async def cog_load(self) -> None:
        """Keep the group off the global tree: discord.py adds a cog's commands to it once this
        has run, so the cog offers it none. The swap puts the group on the league's server."""
        self.__cog_app_commands__.clear()

    async def _test_mode_on(self, interaction: discord.Interaction) -> bool:
        """Whether test mode is on, read as the command is given; where it is not, the command is
        refused."""
        config = await self.bot.config_service.get_server_config()
        if config is not None and config.test_mode_active:
            return True
        await refuse(interaction, TEST_MODE_OFF_REFUSAL, what=describe(interaction))
        return False

    async def _name_of(
        self, interaction: discord.Interaction, account: str, *, is_test_driver: bool
    ) -> str:
        """The name a driver is called by: as `driver_names` names them. A fake driver has no member
        to look up, so none is."""
        names = await read_driver_names(
            self.bot.db_path,
            self.bot.driver_names_read,
            None if is_test_driver else interaction.guild,
            [int(account)],
        )
        return names[int(account)]

    async def _driver(self, interaction: discord.Interaction, text: str) -> ResolvedDriver | None:
        """The driver *text* names; None, having refused, where it names none."""
        reference = await resolve_driver(self.bot.db_path, text)
        if reference is None:
            await _refuse_unknown(interaction, text)
            return None
        account = reference.discord_user_id
        guild = interaction.guild
        member = (
            None
            if reference.is_test_driver or guild is None
            else guild.get_member(int(account))
        )
        name = await self._name_of(interaction, account, is_test_driver=reference.is_test_driver)
        return ResolvedDriver(account, name, member)

    async def _the_member_to_make_current(
        self, interaction: discord.Interaction, text: str
    ) -> ResolvedDriver | None:
        """The account *text* names, to be made a driver's current one: any member of the server,
        whether or not it has a driver profile, or a driver's past account as it stands. A fake
        driver is returned as it is, for the reassignment to refuse; None, having refused, where
        *text* is no account or no member of the server holds it."""
        reference = await resolve_driver(self.bot.db_path, text)
        if reference is not None and reference.is_test_driver:
            account = reference.discord_user_id
            return ResolvedDriver(account, await self._name_of(interaction, account, is_test_driver=True))
        parsed = parse_user(text)
        if parsed is None:
            await _refuse_unknown(interaction, text)
            return None
        guild = interaction.guild
        member = guild.get_member(parsed) if guild is not None else None
        if member is None and guild is not None:
            try:
                member = await guild.fetch_member(parsed)
            except discord.HTTPException:
                member = None
        if member is None:
            await refuse(
                interaction,
                f"⛔ `{parsed}` is not a member of this server, and only a member may be made a "
                "driver's current account.",
                what=describe(interaction),
            )
            return None
        return ResolvedDriver(str(member.id), member.display_name, member)

    # ------------------------------------------------------------------
    # /driver reassign
    # ------------------------------------------------------------------

    @driver.command(
        name="reassign",
        description="Make another Discord account a driver's current one.",
    )
    @app_commands.describe(
        old_user="Any account of the driver's: a mention or an ID.",
        new_user="The account to make current: any member of the server, or one of the driver's past accounts.",
    )
    @league_manager_only
    async def reassign(
        self,
        interaction: discord.Interaction,
        old_user: str,
        new_user: str,
    ) -> None:
        """Make *new_user* the current account of the driver *old_user* names.

        Only while drivers are being placed (`driver_commands.reassign_stage_ok`); a fake driver is
        refused on either side, as a real driver may not be given a test-mode account.
        """
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        if not await driver_commands.reassign_stage_ok(self.bot, interaction):
            return
        old = await self._driver(interaction, old_user)
        if old is None:
            return
        new = await self._the_member_to_make_current(interaction, new_user)
        if new is None:
            return
        await driver_commands.reassign(self.bot, interaction, old.account, new)

    # ------------------------------------------------------------------
    # /driver assign
    # ------------------------------------------------------------------

    @driver.command(
        name="assign",
        description="Assign an Unassigned driver to a team and division.",
    )
    @app_commands.describe(
        user="The driver to assign: a mention or an ID.",
        division="Division tier number or name.",
        team="The team's shorthand.",
    )
    @league_manager_only
    async def assign(
        self,
        interaction: discord.Interaction,
        user: str,
        division: str,
        team: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        driver = await self._driver(interaction, user)
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
        user="The driver to unassign: a mention or an ID.",
        division="Division tier number or name.",
    )
    @league_manager_only
    async def unassign(
        self,
        interaction: discord.Interaction,
        user: str,
        division: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        driver = await self._driver(interaction, user)
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
        user="The driver to move: a mention or an ID.",
        from_division="Division tier number or name the driver is moved from.",
        team="The shorthand of the team the driver is moved into.",
        to_division="Division tier number or name moved into. Omit for the same division.",
    )
    @league_manager_only
    async def move(
        self,
        interaction: discord.Interaction,
        user: str,
        from_division: str,
        team: str,
        to_division: str | None = None,
    ) -> None:
        """Move a committed driver in one step (`driver_commands.move`)."""
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        driver = await self._driver(interaction, user)
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
        user="The driver to release: a mention or an ID.",
        division="Division tier number or name to release them from.",
    )
    @league_manager_only
    async def release(
        self,
        interaction: discord.Interaction,
        user: str,
        division: str,
    ) -> None:
        """Release a committed driver from one division, in the ongoing stages."""
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        driver = await self._driver(interaction, user)
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
    @app_commands.describe(user="The Unassigned driver to turn down: a mention or an ID.")
    @league_manager_only
    async def reject(self, interaction: discord.Interaction, user: str) -> None:
        """Turn down an Unassigned driver (`driver_commands.reject`)."""
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        driver = await self._driver(interaction, user)
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
    @app_commands.describe(user="The driver to sack: a mention or an ID.")
    @league_admin_only
    async def sack(self, interaction: discord.Interaction, user: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self._test_mode_on(interaction):
            return
        driver = await self._driver(interaction, user)
        if driver is None:
            return
        await driver_commands.sack(self.bot, interaction, driver)

    # ------------------------------------------------------------------
    # The autocompletes
    # ------------------------------------------------------------------

    @bounded_autocomplete()
    async def _driver_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """A driver is offered by name, a fake driver by its test name, each sending its ID."""
        return await driver_choices(self.bot.db_path, self.bot.driver_names_read, current)

    @bounded_autocomplete()
    async def _team_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """A team is typed by its shorthand; offered under both its names, Reserve included."""
        return await team_autocomplete(self.bot, current, include_reserve=True)

    reassign.autocomplete("old_user")(_driver_autocomplete)
    assign.autocomplete("user")(_driver_autocomplete)
    unassign.autocomplete("user")(_driver_autocomplete)
    move.autocomplete("user")(_driver_autocomplete)
    release.autocomplete("user")(_driver_autocomplete)
    reject.autocomplete("user")(_driver_autocomplete)
    sack.autocomplete("user")(_driver_autocomplete)
    assign.autocomplete("team")(_team_autocomplete)
    move.autocomplete("team")(_team_autocomplete)


async def _refuse_unknown(interaction: discord.Interaction, text: str) -> None:
    """Refuse a driver field naming no driver, naming what was typed."""
    shown = text.strip()[:_TEXT_SHOWN]
    await refuse(
        interaction,
        f"⛔ No driver found for `{shown}`. Give a mention or an ID of any account the driver has held.",
        what=describe(interaction),
    )
