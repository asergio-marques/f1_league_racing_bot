"""BotCog — the `/bot` group: the bot upon its server.

`/bot init` and the four settings beside it configure the bot upon the league's server.
They were five top-level `/bot-…` commands, and were gathered into one group with the
commands that release the server (decided 2026-09-19, issue #247).

Every setup command here is exempt from the interaction-channel rule, and each accepts Discord's
Administrator permission as well as the league admin role. For `/bot init` that is the
original chicken-and-egg: no configuration exists to gate against. For the other four it is
load-bearing in a different way — the ordinary guards admit a command only in the configured
interaction channel and only to holders of a configured role, so gating these on the very
settings they exist to repair would lock a league out of the exact failure they are for. A
deleted interaction channel, or a league admin role removed from the server, would otherwise
be unrecoverable short of wiping the configuration.

`/bot base-role` and `/bot driver-role` set the league's two roles (issue #276), which were the
signup module's until a second module needed the base role. They are a league manager's, as
they were under `/signup`, and are given in the interaction channel like any other: they
repair nothing the guards read. Confirming a season's configuration fixes both until the
season ends.

`/bot hub-channel` sets the hub (issue #279): the one channel every member may use, holding the
panel `services.hub_service` keeps. It is a league manager's, like every other channel command.

`/bot pack` is a league admin's command and is given in the interaction channel like any
other: it releases the settings rather than repairing them. `/bot factory-reset` is the server
owner's alone, from any channel; see `utils.channel_guard.server_owner_only`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from models.server_config import ServerConfig
from services import backup_service, factory_reset_service, pack_service
from utils.channel_guard import (
    DRIVER_ROLE_STANDS_FOR,
    bot_setup_only,
    league_admin_only,
    league_manager_only,
    role_grant_refusal,
    server_owner_only,
)
from utils.league_bot import LeagueBot
from utils.league_server import guild_of

log = logging.getLogger(__name__)

#: Named in every refusal, so an administrator meeting one is told where to go next.
_SETTINGS_COMMANDS = (
    "`/bot log-channel`, `/bot interaction-channel`, `/bot interaction-role` "
    "and `/bot admin-role`"
)

#: The word `/bot pack` asks to be typed, as the reset it replaced did.
_CONFIRM_WORD = "CONFIRM"

#: One bot serves one league (issue #244). The tree refuses a command from another server
#: before it gets here; this is the clearer message for the one command a second server is
#: likeliest to try, and the answer to a lost race between two.
_ANOTHER_SERVER = (
    "⛔ This bot already serves the league on another server. One bot serves one league."
)


class BotCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot
        # The factory reset's Discord clean-up, which outlives the command. Held so that it
        # is not collected mid-run, asyncio keeping only a weak reference to a task.
        self._clean_up: asyncio.Task | None = None

    # Not named `bot`, which is the cog's handle on the bot itself, nor `bot_…`, which
    # discord.py reserves.
    group = app_commands.Group(
        name="bot",
        description="The bot upon the league's server",
        guild_only=True,
    )

    # ------------------------------------------------------------------
    # /bot init — once per server
    # ------------------------------------------------------------------

    @group.command(
        name="init",
        description="One-time bot setup: register interaction role and channels.",
    )
    @app_commands.describe(
        interaction_role="The role allowed to use bot commands.",
        league_admin_role="The role that governs the bot and may undo a league entire.",
        interaction_channel="The channel where bot commands are accepted.",
        log_channel="The channel where calculation logs are posted.",
    )
    @bot_setup_only
    async def handle_bot_init(
        self,
        interaction: discord.Interaction,
        interaction_role: discord.Role,
        league_admin_role: discord.Role,
        interaction_channel: discord.TextChannel,
        log_channel: discord.TextChannel,
    ) -> None:
        """Register the bot configuration for this server, once.

        There is no `force`: a second run is refused outright rather than overwriting.
        Each of the four settings has a command of its own now, so the only thing an
        overwrite offered was the chance to reset the others by accident — which is
        precisely what it did to test mode. `/bot pack` frees the claim for a league that
        genuinely means to start again, on this server or another.

        The league admin role is asked for here rather than left to be set afterwards
        because it is the tier that governs the bot: a server initialised without one has
        nobody who may cancel its season, and the refusal every league admin command would
        then give is a worse introduction than one more parameter.
        """
        server_id = interaction.guild_id
        # `bot_setup_only` admits only a member of a server, so there is always one here.
        assert server_id is not None

        league = await self.bot.config_service.get_league_server_id()
        if league is not None and league != server_id:
            await interaction.response.send_message(_ANOTHER_SERVER, ephemeral=True)
            return

        existing = await self.bot.config_service.get_server_config()
        if existing:
            await interaction.response.send_message(
                "⚠️ This server is already configured, and `/bot init` runs once.\n"
                f"To change a setting use {_SETTINGS_COMMANDS}.\n"
                "To move the league to another server, use `/bot pack` first.",
                ephemeral=True,
            )
            return

        claimed = {
            "server_id": server_id,
            "interaction_role_id": interaction_role.id,
            "league_admin_role_id": league_admin_role.id,
            "interaction_channel_id": interaction_channel.id,
            "log_channel_id": log_channel.id,
        }
        created = await self.bot.config_service.save_server_config(
            ServerConfig(
                server_id=server_id,
                interaction_role_id=interaction_role.id,
                league_admin_role_id=league_admin_role.id,
                interaction_channel_id=interaction_channel.id,
                log_channel_id=log_channel.id,
            )
        )
        if not created:
            # Lost a race with a concurrent /bot init. Report the refusal that fits whoever
            # won rather than claiming a success that wrote nothing.
            if await self.bot.config_service.get_league_server_id() != server_id:
                await interaction.response.send_message(_ANOTHER_SERVER, ephemeral=True)
                return
            await interaction.response.send_message(
                "⚠️ This server is already configured, and `/bot init` runs once.\n"
                f"To change a setting use {_SETTINGS_COMMANDS}.",
                ephemeral=True,
            )
            return

        # Audited as replacing nothing (issue #371): the claim is taken only where no server
        # holds it, so a first run finds no row and a run after a pack finds all five cleared.
        await _audit(
            self.bot, interaction.user, "BOT_INITIALISED", dict.fromkeys(claimed), claimed
        )

        # Seed default F1 teams + Reserve for this server if none exist yet
        await self.bot.team_service.seed_default_teams_if_empty()

        await interaction.response.send_message(
            f"✅ Bot configuration saved!\n"
            f"**Interaction role**: {interaction_role.mention}\n"
            f"**Interaction channel**: {interaction_channel.mention}\n"
            f"**Log channel**: {log_channel.mention}",
            ephemeral=True,
        )
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot init | Success\n"
            f"  interaction_role: {interaction_role.name} (<@&{interaction_role.id}>)\n"
            f"  interaction_channel: <#{interaction_channel.id}>\n"
            f"  log_channel: <#{log_channel.id}>",
        )
        log.info("Bot configured by %s", interaction.user)

    # ------------------------------------------------------------------
    # The four settings, each written on its own
    # ------------------------------------------------------------------

    async def _set_one(
        self,
        interaction: discord.Interaction,
        *,
        column: str,
        value: int,
        command: str,
        label: str,
        mention: str,
        change_type: str,
    ) -> None:
        """Shared body of the four setting commands.

        Writes a single column, so nothing else in the row — test mode, the module flags,
        the other three settings — can be carried over stale from a half-built model.

        Audited with the value it replaced (issue #371): these decide who may command and
        govern the bot, and once a setting is overwritten nothing else keeps what it was.
        """

        # A channel does one job (decided 2026-09-06). Keyed on the column, because this
        # body also carries the interaction *role*, which no channel rule governs.
        _setting = {
            "interaction_channel_id": "interaction",
            "log_channel_id": "log",
        }.get(column)
        if _setting is not None:
            from services.channel_registry_service import (
                ChannelUse,
                find_channel_use,
                refusal,
            )

            use = await find_channel_use(self.bot.db_path, value)
            if use is not None:
                await interaction.response.send_message(
                    refusal(mention, use, same_setting=(use == ChannelUse(_setting))),
                    ephemeral=True,
                )
                return

        config = await self.bot.config_service.get_server_config()
        old_value = None if config is None else getattr(config, column)

        changed = await self.bot.config_service.set_core_setting(column, value)
        if not changed:
            await interaction.response.send_message(
                "⛔ This server is not configured yet — run `/bot init` first.",
                ephemeral=True,
            )
            return

        key = "channel_id" if _setting is not None else "role_id"
        await _audit(
            self.bot, interaction.user, change_type, {key: old_value}, {key: value}
        )

        await interaction.response.send_message(
            f"✅ **{label}** set to {mention}.", ephemeral=True
        )
        # Posted after the write, so a new log channel is told about itself and an
        # administrator sees at once that the repair took.
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | {command} | Success\n"
            f"  {column}: {mention}",
        )
        log.info("%s set %s", interaction.user, column)
        if column in ("interaction_role_id", "league_admin_role_id"):
            await _reapply_hub_permissions(self.bot)

    @group.command(
        name="log-channel",
        description="Change the channel the bot writes its calculation log to.",
    )
    @app_commands.describe(channel="The channel where calculation logs are posted.")
    @bot_setup_only
    async def handle_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self._set_one(
            interaction,
            column="log_channel_id",
            value=channel.id,
            command="/bot log-channel",
            label="Log channel",
            mention=f"<#{channel.id}>",
            change_type="LOG_CHANNEL_SET",
        )

    @group.command(
        name="interaction-channel",
        description="Change the channel the bot accepts commands in.",
    )
    @app_commands.describe(channel="The channel where bot commands are accepted.")
    @bot_setup_only
    async def handle_interaction_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self._set_one(
            interaction,
            column="interaction_channel_id",
            value=channel.id,
            command="/bot interaction-channel",
            label="Interaction channel",
            mention=f"<#{channel.id}>",
            change_type="INTERACTION_CHANNEL_SET",
        )

    @group.command(
        name="interaction-role",
        description="Change the role allowed to use bot commands.",
    )
    @app_commands.describe(role="The role allowed to use bot commands.")
    @bot_setup_only
    async def handle_interaction_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        await self._set_one(
            interaction,
            column="interaction_role_id",
            value=role.id,
            command="/bot interaction-role",
            label="Interaction role",
            mention=f"<@&{role.id}>",
            change_type="INTERACTION_ROLE_SET",
        )

    @group.command(
        name="admin-role",
        description="Change the role that governs the bot and may undo a league entire.",
    )
    @app_commands.describe(role="The role holding the league admin tier.")
    @bot_setup_only
    async def handle_admin_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        """Set the league admin role.

        The one command that can be reached without it. A league configured before the role
        existed holds none, and every league admin command is refused until this has run —
        so it is gated on Discord's Administrator permission alongside the role, as its four
        siblings are, and runs from any channel for the same reason they do.
        """
        await self._set_one(
            interaction,
            column="league_admin_role_id",
            value=role.id,
            command="/bot admin-role",
            label="League admin role",
            mention=f"<@&{role.id}>",
            change_type="LEAGUE_ADMIN_ROLE_SET",
        )

    # ------------------------------------------------------------------
    # The league's two roles (issue #276)
    # ------------------------------------------------------------------

    async def _set_league_role(
        self,
        interaction: discord.Interaction,
        *,
        column: str,
        role: discord.Role,
        command: str,
        label: str,
        change_type: str,
    ) -> None:
        """Shared body of `/bot base-role` and `/bot driver-role`.

        Not `_set_one`, whose commands repair the settings the guards read and so run from
        any channel on the Administrator permission. These are ordinary league manager
        commands, refused once a season's configuration is confirmed. Like `_set_one`'s,
        each is audited with the role it replaced: a league that finds its members locked
        out needs to know which role used to hold the access, and nothing else keeps it
        once it is overwritten.

        **A role gone from the server may be replaced while it is fixed** (decided 2026-09-22,
        #374). The roles are fixed so that no driver is left holding one the season's end will
        not revoke; nobody holds a deleted role, so that reason is spent, and refusing would
        leave a season unable to open a window until it ended. The replacement is granted to
        every driver, who lost the old one with it — the base role too, the one time the bot
        grants it: it knows its drivers, and the reply tells the league its other members need
        the role by hand.
        """
        from services.season_lifecycle_service import configuration_fixed

        season_number = await configuration_fixed(self.bot.db_path)
        config = await self.bot.config_service.get_server_config()
        old_role_id = getattr(config, column) if config is not None else None
        replacing_gone = (
            season_number is not None
            and old_role_id is not None
            and interaction.guild is not None
            and interaction.guild.get_role(old_role_id) is None
        )
        if season_number is not None and not replacing_gone:
            await interaction.response.send_message(
                f"❌ The league's roles are fixed for Season {season_number} now that its "
                f"configuration has been confirmed. `{command}` is available again once the "
                "season has ended, or while a new season is in configuration — or at once, "
                "should the role be deleted from the server.",
                ephemeral=True,
            )
            return

        if config is None:
            await interaction.response.send_message(
                "⛔ This server is not configured yet — run `/bot init` first.",
                ephemeral=True,
            )
            return

        # The driver role is granted at every approval, and `wizard_service.approve_signup`
        # only logs a grant Discord refuses (#374). The base role is granted by the league.
        if column == "driver_role_id":
            refusal = role_grant_refusal(
                role,
                stands_for=DRIVER_ROLE_STANDS_FOR,
                remedy="Choose a role of the drivers' own.",
            )
            if refusal is not None:
                await interaction.response.send_message(f"❌ {refusal}", ephemeral=True)
                return

        # Two permission edits on the signup channel outrun Discord's three seconds.
        await interaction.response.defer(ephemeral=True)

        if not await self.bot.config_service.set_core_setting(column, role.id):
            await interaction.followup.send(
                "⛔ This server is not configured yet — run `/bot init` first.",
                ephemeral=True,
            )
            return

        if column == "base_role_id" and interaction.guild is not None:
            await self.bot.signup_module_service.move_base_role_overwrite(
                interaction.guild, old_role_id, role
            )
            # The hub is seen by the base role, or by everyone where there is none (#279).
            await _reapply_hub_permissions(self.bot)

        await _audit(
            self.bot,
            interaction.user,
            change_type,
            {"role_id": old_role_id},
            {"role_id": role.id},
        )

        reply = f"✅ **{label}** set to {role.mention}."
        log_line = (
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | {command} | Success\n"
            f"  role: {role.name} (<@&{role.id}>)"
        )
        if replacing_gone:
            given, given_log = await self._give_replaced_role_to_every_driver(
                guild_of(interaction), role, column
            )
            reply += (
                f"\nThe role it replaces is no longer on the server, so it could be replaced "
                f"although Season {season_number}'s configuration is fixed.\n{given}"
            )
            log_line += f"\n  replaced: a role no longer on the server\n{given_log}"

        await interaction.followup.send(reply, ephemeral=True)
        await self.bot.output_router.post_log(log_line)
        log.info("%s set %s", interaction.user, column)

    async def _give_replaced_role_to_every_driver(
        self, guild: discord.Guild, role: discord.Role, column: str
    ) -> tuple[str, str]:
        """Grant a replaced league role to every driver: what to tell the manager and the log.

        `@everyone` is held by everybody already, so a base role set to it is granted to
        nobody. Otherwise every driver who could not be given it is named, for the league to
        give it by hand, and a replaced base role reminds the league that the bot knows only
        its drivers.
        """
        if role.is_default():
            return "Everybody holds it already.", "  given to: everybody, as @everyone"
        outcome = await self.bot.placement_service.grant_to_every_driver(
            guild, role.id
        )
        told = f"It has been given to {outcome.granted} driver(s)."
        if outcome.not_granted:
            told += (
                " It could not be given to "
                + ", ".join(f"<@{uid}>" for uid in outcome.not_granted)
                + " — give it to them by hand."
            )
        if column == "base_role_id":
            told += (
                " The league's other members need it too: give it to them by hand, the bot "
                "knowing only its drivers."
            )
        logged = f"  given to: {outcome.granted} driver(s)" + "".join(
            f"\n  not given to: <@{uid}>" for uid in outcome.not_granted
        )
        return told, logged

    @group.command(
        name="base-role",
        description="Set the role the league's members hold.",
    )
    @app_commands.describe(role="The role every member of the league holds.")
    @league_manager_only
    async def handle_base_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        """Set the base role: who the league's members are.

        Where the signup module is enabled it decides who may see the signup channel, and
        is the role the opening of signups calls upon.
        """
        await self._set_league_role(
            interaction,
            column="base_role_id",
            role=role,
            command="/bot base-role",
            label="Base role",
            change_type="BASE_ROLE_SET",
        )

    @group.command(
        name="driver-role",
        description="Set the role the league's drivers hold.",
    )
    @app_commands.describe(
        role="The role granted when a signup is approved, and taken back when the driver leaves."
    )
    @league_manager_only
    async def handle_driver_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        """Set the driver role: who the league's drivers are.

        Granted when a signup is approved, and revoked whenever the driver returns to Not
        Signed Up. It is granted to a person, not applied to a place, so it touches no
        channel.
        """
        await self._set_league_role(
            interaction,
            column="driver_role_id",
            role=role,
            command="/bot driver-role",
            label="Driver role",
            change_type="DRIVER_ROLE_SET",
        )

    # ------------------------------------------------------------------
    # /bot hub-channel — the hub (issue #279)
    # ------------------------------------------------------------------

    @group.command(
        name="hub-channel",
        description="Set the channel every member of the league uses the bot from.",
    )
    @app_commands.describe(channel="The channel to hold the hub's panel.")
    @league_manager_only
    async def handle_hub_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        """Point the hub at *channel*: set who may see it, and post its panel there.

        Moving the hub deletes the panel from the old channel and clears the permissions the
        bot set on it, as moving the signup channel does. Everything that fails after the
        write — a permission Discord refuses, a panel it will not post — is reported in the
        reply and the log rather than undoing the setting: the channel is the right one, and
        the repair is in Discord's settings.
        """
        from services import hub_service
        from services.channel_registry_service import ChannelUse, find_channel_use, refusal

        use = await find_channel_use(self.bot.db_path, channel.id)
        if use is not None:
            await interaction.response.send_message(
                refusal(channel.mention, use, same_setting=(use == ChannelUse("hub"))),
                ephemeral=True,
            )
            return

        guild = guild_of(interaction)
        perms = channel.permissions_for(guild.me)
        missing = [
            name
            for name, held in (
                ("Manage Channel", perms.manage_channels),
                ("Manage Permissions", perms.manage_roles),
            )
            if not held
        ]
        if missing:
            await interaction.response.send_message(
                f"❌ The bot needs {' and '.join(f'**{m}**' for m in missing)} on "
                f"{channel.mention} to set who may see the hub. Nothing was changed.",
                ephemeral=True,
            )
            return

        config = await self.bot.config_service.get_server_config()
        if config is None:
            await interaction.response.send_message(
                "⛔ This server is not configured yet — run `/bot init` first.",
                ephemeral=True,
            )
            return
        old_channel_id, old_message_id = config.hub_channel_id, config.hub_message_id

        # Permission edits and a post on two channels outrun Discord's three seconds.
        await interaction.response.defer(ephemeral=True)

        await self.bot.config_service.set_core_setting("hub_channel_id", channel.id)
        await self.bot.config_service.set_core_setting("hub_message_id", None)

        faults: list[str] = []
        if old_channel_id is not None and old_channel_id != channel.id:
            faults += await _stand_down_old_hub(guild, old_channel_id, old_message_id)
        for fault in (
            await hub_service.apply_hub_permissions(self.bot, guild, channel),
            await hub_service.refresh_panel(self.bot),
        ):
            if fault is not None:
                faults.append(fault)

        await _audit(
            self.bot,
            interaction.user,
            "HUB_CHANNEL_SET",
            {"channel_id": old_channel_id},
            {"channel_id": channel.id},
        )

        reply = f"✅ **Hub channel** set to {channel.mention}."
        if faults:
            reply += "\n⚠️ " + "\n⚠️ ".join(faults)
        await interaction.followup.send(reply, ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot hub-channel | "
            f"{'Success' if not faults else 'Success, with faults'}\n"
            f"  channel: <#{channel.id}>"
            + "".join(f"\n  {fault}" for fault in faults),
        )
        log.info("%s set the hub channel", interaction.user)

    # ------------------------------------------------------------------
    # /bot pack — ready the bot for another server
    # ------------------------------------------------------------------

    @group.command(
        name="pack",
        description="Free the bot from this server so the league can move to another.",
    )
    @app_commands.describe(
        confirm=f'Type "{_CONFIRM_WORD}" (case-sensitive) to free this server.'
    )
    @league_admin_only
    async def handle_pack(self, interaction: discord.Interaction, confirm: str) -> None:
        """Clear everything tied to this server, keep the league, and free the claim.

        The log is written *before* the pack, because the pack clears the log channel. The
        refusal for a current season is therefore asked first, read-only, so that the log
        does not announce a pack that will not happen; the service asks again inside its own
        transaction, and the rare season set up between the two is logged as a refusal.
        """
        if confirm != _CONFIRM_WORD:
            await interaction.response.send_message(
                f"❌ Nothing was changed. Pass `confirm:{_CONFIRM_WORD}` (case-sensitive) "
                f"to free this server.",
                ephemeral=True,
            )
            return

        async with get_connection(self.bot.db_path) as db:
            season = await pack_service.current_season(db)
        if season is not None:
            await interaction.response.send_message(
                _current_season_refusal(*season), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot pack | Success\n"
            f"  The bot no longer serves this server. `/bot init` on another claims it."
        )
        try:
            result = await pack_service.pack(
                self.bot.db_path,
                self.bot.scheduler_service,
                self.bot,
                actor_id=interaction.user.id,
                actor_name=str(interaction.user),
            )
        except pack_service.PackRefused as refused:
            await self.bot.output_router.post_log(
                f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot pack | "
                f"Refused — season {refused.season_number} was set up meanwhile. "
                f"Nothing was changed."
            )
            await interaction.followup.send(
                _current_season_refusal(refused.season_number, refused.stage),
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "✅ The bot no longer serves this server.\n"
            f"Cleared: the four bot settings, the base role and the driver role, "
            f"**{result.team_roles}** team role(s), the hub channel, the signup channel, "
            f"**{result.wizards}** signup wizard(s), "
            f"**{result.queued_messages}** undelivered message(s) and "
            f"**{result.scheduled_jobs}** scheduled job(s).\n"
            "Kept: every driver, past seasons, the team list, points configurations and "
            "module settings.\n"
            "Run `/bot init` on the league's new server to claim it. The bot's messages "
            "stay on this server, and their buttons now refuse.",
            ephemeral=True,
        )
        log.info("/bot pack by %s: %s", interaction.user, result)

    # ------------------------------------------------------------------
    # /bot factory-reset — return the bot to a fresh install
    # ------------------------------------------------------------------

    @group.command(
        name="factory-reset",
        description="Server owner only: back up, then erase the league and the bot's posts.",
    )
    @app_commands.describe(
        confirm=f'Type "{_CONFIRM_WORD}" (case-sensitive) to erase the league entire.'
    )
    @server_owner_only
    async def handle_factory_reset(
        self, interaction: discord.Interaction, confirm: str
    ) -> None:
        """Back up, wipe, and clean Discord — in that order, and never the second without the
        first. See `services.factory_reset_service`.

        Only the server the command is given in is cleaned. A bot that was packed and not
        yet claimed may still hold the ids of another server's channels, and the owner of
        this one has no say over that one.
        """
        if confirm != _CONFIRM_WORD:
            await interaction.response.send_message(
                f"❌ Nothing was changed. Pass `confirm:{_CONFIRM_WORD}` (case-sensitive) "
                f"to erase the league.",
                ephemeral=True,
            )
            return
        if self._clean_up is not None and not self._clean_up.done():
            await interaction.response.send_message(
                "⛔ A factory reset is still cleaning up Discord. Wait for it to finish.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        db_path = self.bot.db_path
        scheduler = self.bot.scheduler_service

        paused = False
        try:
            inner = getattr(scheduler, "_scheduler", None)
            if inner is not None and inner.running:
                inner.pause()
                paused = True
            backup = factory_reset_service.take_backup(
                db_path, backup_service.jobstore_path_of(self.bot)
            )
        except backup_service.BackupError as exc:
            await interaction.followup.send(
                f"⛔ Nothing was erased: the backup could not be taken, and a factory reset "
                f"never runs without one. {exc}",
                ephemeral=True,
            )
            return
        finally:
            if paused:
                scheduler._scheduler.resume()

        targets = await factory_reset_service.gather_targets(db_path)
        await factory_reset_service.wipe(db_path, scheduler, self.bot)
        log.warning(
            "/bot factory-reset by %s (id=%s): the league was erased; backup at %s",
            interaction.user, interaction.user.id, backup.database,
        )

        progress = await _open_progress(interaction.user)
        where = "in a direct message to you" if progress is not None else "in the host's log"
        await interaction.followup.send(
            "✅ The bot is back to a fresh install, and serves no server.\n"
            f"A backup was taken first, as `{backup.database.name}`"
            + (f" and `{backup.jobstore.name}`" if backup.jobstore is not None else "")
            + ", beside the live database on the host; restoring it is the host's job.\n"
            f"The bot's channels and messages on this server are being deleted now. That "
            f"can take a long while, and progress is reported {where}.",
            ephemeral=True,
        )

        async def report(text: str) -> None:
            log.info("factory reset: %s", text.splitlines()[0])
            if progress is not None:
                try:
                    await progress.edit(content=text)
                except discord.HTTPException:
                    log.warning("factory reset: the progress message could not be edited")

        # A command runs on a bot that has logged in, which is when it has a user.
        assert self.bot.user is not None
        bot_user_id = self.bot.user.id
        self._clean_up = asyncio.create_task(
            _clean_up(interaction.guild, bot_user_id, targets, report)
        )


async def _audit(bot: LeagueBot, user, change_type: str, old: dict, new: dict) -> None:
    """Write the audit entry for a change to the bot's configuration upon its server.

    The other half of the log line each command posts: a configuration change is recorded
    in both (issue #371). None of these belongs to a division.
    """
    async with get_connection(bot.db_path) as db:
        await db.execute(
            "INSERT INTO audit_entries "
            "(actor_id, actor_name, division_id, change_type, old_value, new_value, "
            "timestamp) VALUES (?, ?, NULL, ?, ?, ?, ?)",
            (
                user.id,
                str(user),
                change_type,
                json.dumps(old),
                json.dumps(new),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        await db.commit()


async def _reapply_hub_permissions(bot: LeagueBot) -> None:
    """Set the hub's permissions again after a role they name has changed (issue #279).

    Logged where it fails, and never failing the role command that asked for it: the role is
    set either way, and the hub is repaired in Discord.
    """
    from services.hub_service import reapply_hub_permissions

    try:
        fault = await reapply_hub_permissions(bot)
    except Exception:  # noqa: BLE001 — see the docstring
        log.exception("the hub's permissions could not be applied again")
        return
    if fault is not None:
        await bot.output_router.post_log(f"Hub permissions not updated: {fault}")


async def _stand_down_old_hub(
    guild: discord.Guild, channel_id: int, message_id: int | None
) -> list[str]:
    """Delete the panel from the hub's old channel and clear what the bot set there.

    A panel already deleted, or a channel already gone, is not a fault: there is nothing
    left to stand down.
    """
    old = guild.get_channel(channel_id)
    if not isinstance(old, discord.TextChannel):
        return []
    faults: list[str] = []
    if message_id is not None:
        try:
            await old.get_partial_message(message_id).delete()
        except discord.NotFound:
            pass
        except discord.HTTPException as exc:
            faults.append(f"The old hub panel in <#{channel_id}> could not be deleted: {exc}")
    try:
        await old.edit(overwrites={})
    except discord.HTTPException as exc:
        faults.append(f"The permissions on the old hub <#{channel_id}> could not be cleared: {exc}")
    return faults


async def _clean_up(guild, bot_user_id: int, targets, report) -> None:
    """Run the clean-up, and say so where something it did not expect stops it."""
    try:
        await factory_reset_service.clean_discord(guild, bot_user_id, targets, report)
    except Exception as exc:  # noqa: BLE001 — the last report must say it stopped
        log.exception("factory reset: the Discord clean-up stopped")
        await report(f"⛔ Factory reset: the Discord clean-up stopped: {exc}")


async def _open_progress(user: discord.User | discord.Member) -> discord.Message | None:
    """The direct message the clean-up edits as it goes, or None where DMs are closed."""
    try:
        return await user.send("🧹 Factory reset: starting the Discord clean-up.")
    except discord.HTTPException:
        log.warning("factory reset: could not message %s; progress goes to the log only", user)
        return None


def _current_season_refusal(season_number: int, stage: str | None) -> str:
    """Why pack will not run while the league has a current season."""
    shown = (stage or "setup").replace("_", " ").lower()
    return (
        f"⛔ Season {season_number} is current (stage: {shown}). The bot does not leave a "
        f"server while a season is under way — complete it, or cancel or abort it, first."
    )
