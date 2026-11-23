"""SignupCog — /signup command group for managing the signup module.

Commands:
  /signup channel  <channel>                  — set signup channel
  /signup config view                         — view current config
  /signup nationality toggle                  — toggle nationality requirement
  /signup time-type toggle                    — cycle time type setting
  /signup time-image toggle                   — toggle time image requirement
  /signup time-slot add   <day> <time>        — add availability slot
  /signup time-slot remove <slot_id>          — remove slot by sequence ID
  /signup time-slot list                      — list all slots
  /signup open [track_ids] [close_time]       — open signup window
  /signup close                               — close signup window
  /signup close-time add    <close_time>      — arm an auto-close time
  /signup close-time cancel                   — clear the auto-close time
  /signup close-time modify <close_time>      — replace the armed auto-close time
  /signup cancel <user>                       — end a driver's signup that is under way

The league's base role and driver role, which this module reads, are core's and are set by
`/bot base-role` and `/bot driver-role` (issue #276).

Each command is declared here, with its tier and its guard; what it does is `signup_commands`,
shared with the test-mode group (`signup_test_cog`).
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.signup.cogs import signup_commands
from leaguebot.signup.services.signup_views import SignupButtonView, may_answer
from leaguebot.signup.services.wizard_answers import take_typed_answer
from leaguebot.signup.services.wizard_changes import DELETED, ask_channel_gone, ask_member_left
from leaguebot.framework.utils.league_bot import LeagueBot
from leaguebot.framework.utils.catch_up_gate import after_the_catch_up
from leaguebot.core.utils.channel_guard import league_manager_only, league_role_faults, changes_nothing
from leaguebot.framework.utils.league_server import is_foreign_guild
from leaguebot.framework.utils.log_lines import name_of_member

#: Waits for the catch-up after a restart before a command answered off the change queue acts.
_after_the_catch_up = after_the_catch_up(lambda cog: cog.bot.change_queue)


log = logging.getLogger(__name__)


class SignupCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ── Wizard message listener ────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """T029: Route messages in wizard channels to the wizard state machine."""
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        wizard = await self.bot.wizard_service.get_wizard_by_channel(
            message.channel.id
        )
        if wizard is None or not await may_answer(
            message.author, wizard.discord_user_id,
            config=self.bot.config_service, drivers=self.bot.driver_service,
        ):
            return
        await take_typed_answer(
            self.bot, message, wizard, queue=self.bot.change_queue,
            router=self.bot.output_router, drivers=self.bot.driver_service,
        )

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        """A channel of the server was deleted: where a wizard names it, its signup is ended.

        The ending is the change queue's: the listener asks it, as the bot, for `signup.end` with
        the reason `CHANNEL_GONE`, found "deleted", and the change's own check decides whether the
        signup is still under way. The bot's own deletions of held channels fire this too, and
        each finds its driver no longer signing up and is dropped. A channel no wizard names is
        nothing to the signup module.

        Only a channel of the league's own server counts.
        """
        if await is_foreign_guild(self.bot, channel.guild.id):
            return
        wizard = await self.bot.signup_module_service.get_wizard_by_channel(channel.id)
        if wizard is None:
            return
        await ask_channel_gone(
            self.bot.change_queue,
            user_id=wizard.discord_user_id,
            channel_id=channel.id,
            named=await name_of_member(self.bot, int(wizard.discord_user_id)),
            found=DELETED,
        )

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """T048: Clean up wizard state when a member leaves the server (FR-027).

        The clean-up is the change queue's: the listener asks it, as the bot, for
        `signup.member_left` for every member who leaves, and the change's own check decides what
        there is to clean up. A member who leaves while the queue is stopped keeps their channel
        and state until it clears. Its line, "Driver left server", is the change's too, for a
        driver approved or placed as for one still signing up.

        Only a member leaving the league's own server counts: the same person leaving another
        server the bot sits in is nothing to the league.
        """
        if await is_foreign_guild(self.bot, member.guild.id):
            return
        await ask_member_left(
            self.bot.change_queue, self.bot.db_path, str(member.id),
            member.display_name or str(member.id),
        )

    # ── /signup (root group) ───────────────────────────────────────────

    signup = app_commands.Group(
        name="signup",
        description="Manage the signup module.",
        default_permissions=None,
    )

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
        await signup_commands.config_view(self.bot, interaction)

    # ── /signup channel (T011) ─────────────────────────────────────────

    @signup.command(name="channel", description="Set the signup channel and apply permission overwrites.")
    @app_commands.describe(channel="Channel for signup interactions")
    @league_manager_only
    @_after_the_catch_up
    async def signup_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await signup_commands.signup_channel(self.bot, interaction, channel)

    # ── /signup nationality toggle (T020) ──────────────────────────────

    @signup.command(name="nationality", description="Toggle whether nationality is required in signups.")
    @league_manager_only
    @_after_the_catch_up
    async def nationality(self, interaction: discord.Interaction) -> None:
        await signup_commands.nationality(self.bot, interaction)

    # ── /signup time-type toggle (T021) ────────────────────────────────

    @signup.command(name="time-type", description="Toggle the time type setting (Time Trial / Short Qualification).")
    @league_manager_only
    @_after_the_catch_up
    async def time_type(self, interaction: discord.Interaction) -> None:
        await signup_commands.time_type(self.bot, interaction)

    # ── /signup time-image toggle (T022) ───────────────────────────────

    @signup.command(name="time-image", description="Toggle whether a time image is required in signups.")
    @league_manager_only
    @_after_the_catch_up
    async def time_image(self, interaction: discord.Interaction) -> None:
        await signup_commands.time_image(self.bot, interaction)

    # ── /signup time-slot (sub-group) ──────────────────────────────────

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
        await signup_commands.time_slot_add(self.bot, interaction, day, time)

    @time_slot_group.command(name="remove", description="Remove an availability time slot by its sequence ID.")
    @app_commands.describe(slot_id="Stable sequence ID shown in /signup time-slot list")
    @league_manager_only
    @_after_the_catch_up
    async def time_slot_remove(
        self, interaction: discord.Interaction, slot_id: int
    ) -> None:
        await signup_commands.time_slot_remove(self.bot, interaction, slot_id)

    @time_slot_group.command(name="list", description="List all configured availability time slots.")
    @league_manager_only
    @changes_nothing
    async def time_slot_list(self, interaction: discord.Interaction) -> None:
        await signup_commands.time_slot_list(self.bot, interaction)

    # ── /signup close-time (sub-group) ─────────────────────────────────

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
        await signup_commands.close_time_add(self.bot, interaction, close_time)

    @close_time_group.command(
        name="cancel", description="Clear the auto-close time, leaving signups open."
    )
    @league_manager_only
    @_after_the_catch_up
    async def close_time_cancel(self, interaction: discord.Interaction) -> None:
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
        await signup_commands.close_time_modify(self.bot, interaction, close_time)

    # ── /signup cancel (#502) ──────────────────────────────────────────

    @signup.command(
        name="cancel",
        description="End the signup a driver has under way.",
    )
    @app_commands.describe(user="The driver whose signup to end.")
    @league_manager_only
    async def signup_cancel(self, interaction: discord.Interaction, user: discord.Member) -> None:
        await signup_commands.signup_cancel(
            self.bot, interaction, signup_commands.SignupDriver(str(user.id), user.mention)
        )

    # ── /signup open (T017) ───────────────────────────────────────────

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
        await signup_commands.signup_open(self.bot, interaction, track_ids, close_time)

    # ── /signup close (T019) ──────────────────────────────────────────

    @signup.command(name="close", description="Close the signup window.")
    @league_manager_only
    async def signup_close(self, interaction: discord.Interaction) -> None:
        await signup_commands.signup_close(self.bot, interaction)

    # ------------------------------------------------------------------
    # /signup unassigned (group with list + export subcommands)
    # ------------------------------------------------------------------

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
        await signup_commands.signup_unassigned_list(self.bot, interaction)

    @unassigned_group.command(
        name="export",
        description="Export the unsettled signups to a CSV file.",
    )
    @league_manager_only
    @changes_nothing
    async def signup_unassigned_export(self, interaction: discord.Interaction) -> None:
        await signup_commands.signup_unassigned_export(self.bot, interaction)
