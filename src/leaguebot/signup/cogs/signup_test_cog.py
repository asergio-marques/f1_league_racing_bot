"""SignupTestCog — the test-mode `/signup` command group (#600).

Discord's member field is filled with a real account only, so a fake driver's ID never reaches a
command that takes a `discord.Member`. While test mode is on, `/signup` is this group instead of
the member one (`signup_cog`): the same commands at the same tiers, each doing what the member
group's does (`signup_commands`), with two differences. `cancel` takes its driver as text, a mention
or an ID, offered by name as it is typed and resolved through any account the driver has held
(`driver_reference`), so a fake driver's signup may be ended; and `start`, which the member group
has not, starts a fake driver's signup in the stead of the Sign Up press it cannot make.

**Which group the server shows is the swap's** (`test_mode/driver_commands.py`): the member group is
global and this one is put on the league's server only while test mode is on. So that the two never
clash on the global tree, this cog adds no command to it as it loads. It listens to nothing: the
wizard's listeners are the member cog's. Every command re-reads the flag as it is given and is
refused while test mode is off, as the swap may have failed to take the group down; a restart puts
`/signup` right.

The group's subgroups are declared here, in the member group's order; a Discord command group is
one cog's.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services.drivers.driver_reference import (
    NO_DRIVER,
    DriverReference,
    driver_choices,
    reference_name,
    resolve_driver,
)
from leaguebot.core.utils.channel_guard import (
    changes_nothing,
    league_admin_only,
    league_manager_only,
)
from leaguebot.framework.utils.autocomplete import bounded_autocomplete
from leaguebot.framework.utils.catch_up_gate import after_the_catch_up
from leaguebot.framework.utils.interaction_errors import describe
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.log_lines import refuse
from leaguebot.signup.cogs import signup_commands
from leaguebot.signup.services.wizard_changes import START

#: What a command is refused with while test mode is off.
TEST_MODE_OFF_REFUSAL = (
    "⛔ Test mode is off, so this `/signup` no longer applies. The `/signup` commands will be "
    "put right when the bot next restarts."
)

#: What `/signup start` is refused with for a driver who is not a fake one.
NOT_A_FAKE_DRIVER = (
    "⛔ **{name}** is a real driver: only a fake driver is signed up this way. A real driver "
    "presses Sign Up."
)

#: What the typed text is clipped to where a refusal names it.
_TEXT_SHOWN = 100

#: Waits for the catch-up after a restart before a command answered off the change queue acts.
_after_the_catch_up = after_the_catch_up(lambda cog: cog.bot.change_queue)


class SignupTestCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    signup = app_commands.Group(
        name="signup",
        description="Manage the signup module.",
        default_permissions=None,
    )

    async def cog_load(self) -> None:
        """Keep the group off the global tree: discord.py adds a cog's commands to it once this
        has run, so the cog offers it none. The swap puts the group on the league's server."""
        self.__cog_app_commands__.clear()

    def get_app_commands(
        self,
    ) -> list[app_commands.Command[SignupTestCog, ..., object] | app_commands.Group]:
        """The group the cog declares, which it still does once it is loaded and offers the tree
        none: the swap is handed it by the entry point."""
        return [self.signup]

    async def _test_mode_on(self, interaction: discord.Interaction) -> bool:
        """Whether test mode is on, read as the command is given; where it is not, the command is
        refused."""
        config = await self.bot.config_service.get_server_config()
        if config is not None and config.test_mode_active:
            return True
        await refuse(interaction, TEST_MODE_OFF_REFUSAL, what=describe(interaction))
        return False

    async def _name_of(
        self, interaction: discord.Interaction, reference: DriverReference
    ) -> str:
        """The name a driver is called by: a fake driver's test name, otherwise as `driver_names`
        names them."""
        return await reference_name(
            self.bot.db_path, self.bot.driver_names_read, interaction.guild, reference
        )

    async def _driver(
        self, interaction: discord.Interaction, text: str
    ) -> DriverReference | None:
        """The driver *text* names; None, having refused, where it names none."""
        reference = await resolve_driver(self.bot.db_path, text)
        if reference is None:
            shown = text.strip()[:_TEXT_SHOWN]
            await refuse(interaction, NO_DRIVER.format(text=shown), what=describe(interaction))
        return reference

    # ── /signup config ─────────────────────────────────────────────────

    config_group = app_commands.Group(
        name="config",
        description="Configure the signup module.",
        parent=signup,
    )

    @config_group.command(name="view", description="View current signup module configuration.")
    @league_manager_only
    @changes_nothing
    async def config_view(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.config_view(self.bot, interaction)

    # ── /signup channel ────────────────────────────────────────────────

    @signup.command(name="channel", description="Set the signup channel and apply permission overwrites.")
    @app_commands.describe(channel="Channel for signup interactions")
    @league_manager_only
    @_after_the_catch_up
    async def signup_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.signup_channel(self.bot, interaction, channel)

    # ── /signup nationality ────────────────────────────────────────────

    @signup.command(name="nationality", description="Toggle whether nationality is required in signups.")
    @league_manager_only
    @_after_the_catch_up
    async def nationality(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.nationality(self.bot, interaction)

    # ── /signup time-type ──────────────────────────────────────────────

    @signup.command(name="time-type", description="Toggle the time type setting (Time Trial / Short Qualification).")
    @league_manager_only
    @_after_the_catch_up
    async def time_type(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.time_type(self.bot, interaction)

    # ── /signup time-image ─────────────────────────────────────────────

    @signup.command(name="time-image", description="Toggle whether a time image is required in signups.")
    @league_manager_only
    @_after_the_catch_up
    async def time_image(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.time_image(self.bot, interaction)

    # ── /signup time-slot ──────────────────────────────────────────────

    time_slot_group = app_commands.Group(
        name="time-slot",
        description="Manage signup availability time slots.",
        parent=signup,
    )

    @time_slot_group.command(name="add", description="Add an availability time slot.")
    @app_commands.describe(day="Day of week", time="Time in HH:MM 24h or 12h format (e.g. 14:30 or 2:30pm)")
    @app_commands.choices(day=signup_commands.DAY_CHOICES)
    @league_manager_only
    @_after_the_catch_up
    async def time_slot_add(
        self,
        interaction: discord.Interaction,
        day: app_commands.Choice[str],
        time: str,
    ) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.time_slot_add(self.bot, interaction, day, time)

    @time_slot_group.command(name="remove", description="Remove an availability time slot by its sequence ID.")
    @app_commands.describe(slot_id="Stable sequence ID shown in /signup time-slot list")
    @league_manager_only
    @_after_the_catch_up
    async def time_slot_remove(
        self, interaction: discord.Interaction, slot_id: int
    ) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.time_slot_remove(self.bot, interaction, slot_id)

    @time_slot_group.command(name="list", description="List all configured availability time slots.")
    @league_manager_only
    @changes_nothing
    async def time_slot_list(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.time_slot_list(self.bot, interaction)

    # ── /signup close-time ─────────────────────────────────────────────

    close_time_group = app_commands.Group(
        name="close-time",
        description="Manage the signup window's auto-close time.",
        parent=signup,
    )

    @close_time_group.command(
        name="add", description="Arm an auto-close time for the open signup window."
    )
    @app_commands.describe(
        close_time="Auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_add(
        self, interaction: discord.Interaction, close_time: str
    ) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.close_time_add(self.bot, interaction, close_time)

    @close_time_group.command(
        name="cancel", description="Clear the auto-close time, leaving signups open."
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_cancel(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.close_time_cancel(self.bot, interaction)

    @close_time_group.command(
        name="modify", description="Replace the armed auto-close time with a new one."
    )
    @app_commands.describe(
        close_time="New auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_modify(
        self, interaction: discord.Interaction, close_time: str
    ) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.close_time_modify(self.bot, interaction, close_time)

    # ── /signup cancel ─────────────────────────────────────────────────

    @signup.command(
        name="cancel",
        description="End the signup a driver has under way.",
    )
    @app_commands.describe(user="The driver whose signup to end: a mention or an ID.")
    @league_manager_only
    async def signup_cancel(self, interaction: discord.Interaction, user: str) -> None:
        """End the signup of the driver *user* names, a fake driver's as a real one's."""
        if not await self._test_mode_on(interaction):
            return
        reference = await self._driver(interaction, user)
        if reference is None:
            return
        await signup_commands.signup_cancel(
            self.bot, interaction,
            signup_commands.SignupDriver(
                reference.discord_user_id, await self._name_of(interaction, reference)
            ),
        )

    # ── /signup start ──────────────────────────────────────────────────

    @signup.command(
        name="start",
        description="Start a fake driver's signup, in the stead of the Sign Up button.",
    )
    @app_commands.describe(user="The fake driver to sign up: a mention or an ID.")
    @league_admin_only
    async def signup_start(self, interaction: discord.Interaction, user: str) -> None:
        """Ask `signup.start` for the fake driver *user* names, as its Sign Up press would, the
        league admin who ran this recorded as the one who asked. Every other refusal (signups
        closed, a signup under way or approved) is the change's check, which a Sign Up press meets
        too; a real driver is refused here, for it presses Sign Up itself."""
        if not await self._test_mode_on(interaction):
            return
        reference = await self._driver(interaction, user)
        if reference is None:
            return
        if not reference.is_test_driver:
            name = await self._name_of(interaction, reference)
            await refuse(
                interaction, NOT_A_FAKE_DRIVER.format(name=name), what=describe(interaction)
            )
            return
        await self.bot.change_queue.ask(
            START,
            {"user_id": reference.discord_user_id, "fake_name": reference.test_name},
            interaction=interaction, what=describe(interaction),
        )

    # ── /signup open ───────────────────────────────────────────────────

    @signup.command(name="open", description="Open the signup window.")
    @app_commands.describe(
        track_ids="Optional: space- or comma-separated track IDs (e.g. '1 3 12')",
        close_time="Optional: auto-close UTC datetime in ISO 8601 format (e.g. 2025-06-15T20:00:00)",
    )
    @league_manager_only
    async def signup_open(
        self,
        interaction: discord.Interaction,
        track_ids: str | None = None,
        close_time: str | None = None,
    ) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.signup_open(self.bot, interaction, track_ids, close_time)

    # ── /signup close ──────────────────────────────────────────────────

    @signup.command(name="close", description="Close the signup window.")
    @league_manager_only
    async def signup_close(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.signup_close(self.bot, interaction)

    # ── /signup unassigned ─────────────────────────────────────────────

    unassigned_group = app_commands.Group(
        name="unassigned",
        description="Commands for listing and exporting the unsettled signups.",
        parent=signup,
    )

    @unassigned_group.command(
        name="list",
        description="List the unsettled signups: Unassigned drivers by seed, then those still in review.",
    )
    @league_manager_only
    @changes_nothing
    async def signup_unassigned_list(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.signup_unassigned_list(self.bot, interaction)

    @unassigned_group.command(
        name="export",
        description="Export the unsettled signups to a CSV file.",
    )
    @league_manager_only
    @changes_nothing
    async def signup_unassigned_export(self, interaction: discord.Interaction) -> None:
        if not await self._test_mode_on(interaction):
            return
        await signup_commands.signup_unassigned_export(self.bot, interaction)

    # ── The autocomplete ───────────────────────────────────────────────

    @bounded_autocomplete()
    async def _driver_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """A driver is offered by name, a fake driver by its test name, each sending its ID."""
        return await driver_choices(self.bot.db_path, self.bot.driver_names_read, current)

    signup_cancel.autocomplete("user")(_driver_autocomplete)
    signup_start.autocomplete("user")(_driver_autocomplete)
