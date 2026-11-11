"""BotCog — the `/bot` group: the bot upon its server.

`/bot init` and the four settings beside it configure the bot upon the league's server, and
share one group with the commands that release the server.

Every setup command here is exempt from the interaction-channel rule, and each accepts Discord's
Administrator permission as well as the league admin role. For `/bot init` that is the
original chicken-and-egg: no configuration exists to gate against. For the other four it is
load-bearing in a different way — the ordinary guards admit a command only in the configured
interaction channel and only to holders of a configured role, so gating these on the very
settings they exist to repair would lock a league out of the exact failure they are for. A
deleted interaction channel, or a league admin role removed from the server, would otherwise
be unrecoverable short of wiping the configuration.

`/bot base-role` and `/bot driver-role` set the league's two roles, which are core's rather than
the signup module's because a module besides signup needs the base role. They are a league
manager's, and are given in the interaction channel like any other: they repair nothing the
guards read. Confirming a season's configuration fixes both until the
season ends.

`/bot hub-channel` sets the hub: the one channel every member may use, holding the panel
`leaguebot.core.services.modules.hub_service` keeps. It is a league manager's, like every other channel
command.

`/bot pack` is a league admin's command and is given in the interaction channel like any
other: it releases the settings rather than repairing them. `/bot factory-reset` is the server
owner's alone, from any channel; see `leaguebot.core.utils.channel_guard.server_owner_only`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.models.change import LEAGUE_LANE
from leaguebot.core.services import backup_service, factory_reset_service, pack_service
from leaguebot.core.services.settings.bot_init_change import ANOTHER_SERVER as _ANOTHER_SERVER
from leaguebot.core.services.settings.bot_init_change import KIND as BOT_INIT
from leaguebot.core.utils.channel_guard import (
    DRIVER_ROLE_STANDS_FOR,
    bot_setup_only,
    league_admin_only,
    league_manager_only,
    role_grant_refusal,
    server_owner_only,
)
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.interaction_errors import describe, describe_fault, report_failure
from leaguebot.core.utils.league_server import guild_of
from leaguebot.core.utils.log_lines import lane_name, refuse

log = logging.getLogger(__name__)

#: The word `/bot pack` asks to be typed.
_CONFIRM_WORD = "CONFIRM"


async def _refuse_on_host(interaction: discord.Interaction, reply: str) -> None:
    """Answer the member with *reply*, seen by them alone, and record the refusal in the host's
    log alone.

    For a refusal made on another server, or before the bot is set up: there is no log channel
    of the league's to write to.
    """
    log.info(
        "%s refused for %s (%s): %s",
        describe(interaction),
        getattr(interaction.user, "display_name", "a member"),
        getattr(interaction.user, "id", None),
        reply.strip().splitlines()[0] if reply.strip() else "",
    )
    if interaction.response.is_done():
        await interaction.followup.send(reply, ephemeral=True)
    else:
        await interaction.response.send_message(reply, ephemeral=True)


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
            await _refuse_on_host(interaction, _ANOTHER_SERVER)
            return

        if self._clean_up is not None and not self._clean_up.done():
            # A new claim would be made while the last reset is still deleting the bot's
            # messages and roles here. Nothing is configured, so the host's log alone. Judged
            # only here: `BotCog._clean_up` is kept in memory on the cog, never in the
            # database the queue reads, so the change type's check cannot ask it again as the
            # change starts.
            await _refuse_on_host(
                interaction,
                "⛔ A factory reset is still cleaning up Discord. Run `/bot init` again once "
                "it has finished.",
            )
            return

        payload = {
            "server_id": server_id,
            "interaction_role_id": interaction_role.id,
            "interaction_role_name": interaction_role.name,
            "league_admin_role_id": league_admin_role.id,
            "league_admin_role_name": league_admin_role.name,
            "interaction_channel_id": interaction_channel.id,
            "log_channel_id": log_channel.id,
        }
        await self.bot.change_queue.ask(
            BOT_INIT, payload, interaction=interaction, what=describe(interaction),
        )

    # ------------------------------------------------------------------
    # The four settings, each a core `Setting` on the change queue
    # ------------------------------------------------------------------

    async def _ask_setting(
        self, interaction: discord.Interaction, kind: str, value: int
    ) -> None:
        """Ask the queue for one of the four `/bot` repair settings (`core/services/settings/bot_settings.py`).

        An unconfigured server is refused here, to the host's log alone, before the change is
        even asked for: there is no log channel of the league's to record a refusal in, and
        there being none is not itself a member's command to refuse through the queue. Every
        other refusal — a channel already doing another job, or already this setting — is
        `Setting.check`'s, run at the ask and again as the change starts, and recorded in the
        league's log channel as any other refusal is. The write, the audit entry and the line
        once it has run are `setting_change`'s and `bot_settings`'s: the change overtakes a stop
        elsewhere on the league's lane, so a stopped job there cannot lock a league out of
        repairing these.
        """
        if await self.bot.config_service.get_server_config() is None:
            await _refuse_on_host(
                interaction, "⛔ This server is not configured yet — run `/bot init` first."
            )
            return
        await self.bot.change_queue.ask(
            kind, {"value": value}, interaction=interaction, what=describe(interaction),
        )

    @group.command(
        name="log-channel",
        description="Change the channel the bot writes its calculation log to.",
    )
    @app_commands.describe(channel="The channel where calculation logs are posted.")
    @bot_setup_only
    async def handle_log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self._ask_setting(interaction, "core.setting.log_channel", channel.id)

    @group.command(
        name="interaction-channel",
        description="Change the channel the bot accepts commands in.",
    )
    @app_commands.describe(channel="The channel where bot commands are accepted.")
    @bot_setup_only
    async def handle_interaction_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ) -> None:
        await self._ask_setting(interaction, "core.setting.interaction_channel", channel.id)

    @group.command(
        name="interaction-role",
        description="Change the role allowed to use bot commands.",
    )
    @app_commands.describe(role="The role allowed to use bot commands.")
    @bot_setup_only
    async def handle_interaction_role(
        self, interaction: discord.Interaction, role: discord.Role
    ) -> None:
        await self._ask_setting(interaction, "core.setting.interaction_role", role.id)

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
        await self._ask_setting(interaction, "core.setting.admin_role", role.id)

    # ------------------------------------------------------------------
    # The league's two roles
    # ------------------------------------------------------------------

    async def _set_league_role(
        self,
        interaction: discord.Interaction,
        *,
        column: str,
        role: discord.Role,
        command: str,
        kind: str,
    ) -> None:
        """Shared body of `/bot base-role` and `/bot driver-role`: ask the change queue for one
        of `bot_settings`'s two league role `Setting`s.

        Not `_set_one`, whose commands repair the settings the guards read and so run from
        any channel on the Administrator permission. These are ordinary league manager
        commands, refused once a season's configuration is confirmed.

        The refusals judged here have nothing to do with a value already held, so they are
        judged before the change is even asked for, as `_ask_setting`'s unconfigured-server
        refusal is: the season's configuration being fixed without a gone role to replace
        (`configuration_fixed`), the server not yet configured, and, for the driver role, one
        Discord will not let the bot grant (`role_grant_refusal`). What *is* the queue's —
        the write, the audit entry, the line, and a role already held answered "Nothing
        changed" — is `setting_change`'s and `bot_settings`'s.

        **A role gone from the server may be replaced while it is fixed.** The roles are fixed
        so that no driver is left holding one the season's end will not revoke; nobody holds a
        deleted role, so that reason is spent, and refusing would leave a season unable to open
        a window until it ended. The ask judges this against the server only for its own
        refusal; the setting judges it again as the change starts, and reads the role it
        replaces as it saves, so the payload carries no judgement of the ask's.
        """
        from leaguebot.core.services.settings.bot_settings import season_fixed_refusal
        from leaguebot.core.services.seasons.season_lifecycle_service import configuration_fixed

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
            await refuse(
                interaction,
                season_fixed_refusal(command, season_number),
                what=describe(interaction),
            )
            return

        if config is None:
            await _refuse_on_host(
                interaction, "⛔ This server is not configured yet — run `/bot init` first."
            )
            return

        # The driver role is granted at every approval, and the `signup.approve` change
        # only logs a grant Discord refuses. The base role is granted by the league.
        if column == "driver_role_id":
            refusal = role_grant_refusal(
                role,
                stands_for=DRIVER_ROLE_STANDS_FOR,
                remedy="Choose a role of the drivers' own.",
            )
            if refusal is not None:
                await refuse(interaction, f"❌ {refusal}", what=describe(interaction))
                return

        # The raw id, not `guild_of(interaction).id`: the payload is JSON-encoded for the
        # queue's key, and `interaction.guild_id` is already the plain int discord.py gives.
        # The setting's own check reads it to judge the fixed season again, against the
        # server's state as the change starts rather than as it was asked.
        payload: dict[str, Any] = {
            "value": role.id,
            "role_name": role.name,
            "role_mention": role.mention,
            "guild_id": interaction.guild_id,
        }
        await self.bot.change_queue.ask(
            kind, payload, interaction=interaction, what=describe(interaction),
        )

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
            kind="core.setting.base_role_id",
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
            kind="core.setting.driver_role_id",
        )

    # ------------------------------------------------------------------
    # /bot hub-channel — the hub
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
        """Point the hub at *channel*: ask the change queue for `bot_settings.hub_channel_setting`.

        The channel already doing another job, or already the hub, is judged here first, so a
        manager re-running it for the hub's own value still reads "Nothing was changed" rather
        than a permission refusal that happens to be true as well. The missing-permission refusal
        has nothing to do with a value already held, so it is judged next, before the change is
        even asked for, as a driver role Discord will not let the bot grant is judged before
        `/bot driver-role` asks for its own. The channel-in-use and the unconfigured-server
        refusals are the setting's own `check` as well, run again as the change starts, since a
        change queued ahead of this one might have claimed the channel for something else since
        it was asked; what follows the save is `setting_change`'s and `bot_settings`'s.
        """
        from leaguebot.core.services.settings.channel_registry_service import ChannelUse, find_channel_use, refusal

        use = await find_channel_use(
            self.bot.db_path, channel.id, channel_uses_read=self.bot.channel_uses_read
        )
        if use is not None:
            await refuse(
                interaction,
                refusal(channel.mention, use, same_setting=(use == ChannelUse("hub"))),
                what=describe(interaction),
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
            await refuse(
                interaction,
                f"❌ The bot needs {' and '.join(f'**{m}**' for m in missing)} on "
                f"{channel.mention} to set who may see the hub. Nothing was changed.",
                what=describe(interaction),
            )
            return

        if await self.bot.config_service.get_server_config() is None:
            await _refuse_on_host(
                interaction, "⛔ This server is not configured yet — run `/bot init` first."
            )
            return

        await self.bot.change_queue.ask(
            "core.setting.hub_channel_id",
            {"value": channel.id, "guild_id": interaction.guild_id},
            interaction=interaction, what=describe(interaction),
        )

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
        refusals for a current season and for a job in the queue are therefore asked first,
        read-only, so that the log does not announce a pack that will not happen; the service
        asks again inside its own transaction, and the rare season or change set up between
        the two is recorded as a refusal.

        The line is written as the pack begins, so it says the pack is under way and what it
        will clear, never that it is done: once the pack has run there is no log channel to
        say so in. A fault inside the pack's transaction is rolled back with the
        configuration intact, so its failure line still reaches the log channel; a fault after
        the commit (cancelling the scheduler's jobs, clearing the in-memory state) finds no
        configuration and is recorded in the host's log alone.
        """
        if confirm != _CONFIRM_WORD:
            await refuse(
                interaction,
                f"❌ Nothing was changed. Pass `confirm:{_CONFIRM_WORD}` (case-sensitive) "
                f"to free this server.",
                what=describe(interaction),
            )
            return

        season, job = await pack_service.what_stops_a_pack(self.bot.db_path)
        if season is not None:
            await refuse(
                interaction, _current_season_refusal(*season), what=describe(interaction)
            )
            return
        if job is not None:
            await refuse(interaction, _queue_refusal(*job), what=describe(interaction))
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot pack | Pack under way\n"
            f"  Clearing the four bot settings, the base and driver roles, the team roles, "
            f"the hub and signup channels, the signup wizards, undelivered messages and "
            f"scheduled jobs. The bot will no longer serve this server; `/bot init` on "
            f"another claims it."
        )
        try:
            result = await pack_service.pack(
                self.bot.db_path,
                self.bot.scheduler_service,
                self.bot.in_memory_state_cleared,
                self.bot.change_queue,
                bot_packed=self.bot.bot_packed,
                actor_id=interaction.user.id,
                actor_name=str(interaction.user),
                now=self.bot.change_queue.now(),
            )
        except pack_service.PackRefused as refused:
            if refused.job_id is not None:
                reason = _queue_refusal(refused.job_id, refused.stopped, refused.lane)
            elif refused.season_number is not None:
                reason = _current_season_refusal(refused.season_number, refused.stage)
            else:
                raise
            await refuse(interaction, reason, what=describe(interaction))
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

    async def _refuse_factory_reset(self, interaction: discord.Interaction, reply: str) -> None:
        """Refuse a factory reset: recorded in the log channel where a server configuration
        exists, in the host's log alone where none does (a clean-up still running after a reset
        has wiped it, or a bot never set up). Decided from the configuration, never from
        `log_channel_id`, as `channel_guard` decides.
        """
        if await self.bot.config_service.get_server_config() is None:
            await _refuse_on_host(interaction, reply)
        else:
            await refuse(interaction, reply, what=describe(interaction))

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
        first. See `leaguebot.core.services.factory_reset_service`.

        Only the server the command is given in is cleaned. A bot that was packed and not
        yet claimed may still hold the ids of another server's channels, and the owner of
        this one has no say over that one.
        """
        if confirm != _CONFIRM_WORD:
            await self._refuse_factory_reset(
                interaction,
                f"❌ Nothing was changed. Pass `confirm:{_CONFIRM_WORD}` (case-sensitive) "
                f"to erase the league.",
            )
            return
        if self._clean_up is not None and not self._clean_up.done():
            await self._refuse_factory_reset(
                interaction,
                "⛔ A factory reset is still cleaning up Discord. Wait for it to finish.",
            )
            return

        await interaction.response.defer(ephemeral=True)
        db_path = self.bot.db_path
        scheduler = self.bot.scheduler_service

        try:
            with scheduler.paused():
                backup = factory_reset_service.take_backup(
                    db_path, scheduler.jobstore_path, now=self.bot.change_queue.now()
                )
        except backup_service.BackupFault as exc:
            # A copy, a write or a check that failed is a fault in the bot, not a refusal, and
            # nothing has been erased: the reply says so without naming the error.
            await report_failure(
                interaction,
                exc,
                what=describe(interaction),
                outcome="Nothing was erased: the backup could not be taken.",
            )
            return

        targets = await factory_reset_service.gather_targets(
            db_path, setting_channels_read=self.bot.setting_channels_read
        )
        # The wipe takes the configuration and with it any way to find the log channel, so
        # where the closing line goes is learned now.
        log_channel = await self.bot.output_router.log_destination()
        await factory_reset_service.wipe(
            db_path, scheduler, self.bot.in_memory_state_cleared, self.bot.change_queue
        )
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
        who = f"{interaction.user.display_name} (<@{interaction.user.id}>)"

        async def close(ending: str, detail: str) -> None:
            await self._post_closing_line(log_channel, who, ending, detail)

        self._clean_up = asyncio.create_task(
            _clean_up(
                interaction.guild,
                bot_user_id,
                targets,
                report,
                close,
                now=self.bot.change_queue.now(),
            )
        )

    async def _post_closing_line(
        self, log_channel: int | None, who: str, ending: str, detail: str
    ) -> None:
        """Leave the one line a factory reset leaves in the log channel, once its clean-up ends.

        Posted to *log_channel*, found before the wipe, with no retry row and no notice in the
        interaction channel (see `OutputRouter.post_log`): the fresh database belongs to a bot
        serving no server. Where there was no log channel, or it takes no line, the line goes to
        the host's log instead.
        """
        line = (
            f"{who} | /bot factory-reset | {ending}\n"
            "  Erased: the league's database and its scheduled jobs, after a backup was "
            f"taken.\n  {detail}"
        )
        if log_channel is not None and await self.bot.output_router.post_log(
            line, channel=log_channel
        ):
            return
        log.warning("factory reset: the closing line could not be posted:\n%s", line)


async def _clean_up(guild, bot_user_id: int, targets, report, close, *, now: datetime) -> None:
    """Run the clean-up, and say so where something it did not expect stops it.

    However it ends, *close(ending, detail)* is then awaited once, to leave the reset's closing
    line: after the clean-up, so that its deletions leave the line standing. *now* is the
    moment the clean-up judges a message's age by.

    A clean-up that stops part-way is caught whatever the fault, logged on the host in full, and
    said so in the last report and the closing line, which a reset that ends in silence would not.
    """
    try:
        outcome = await factory_reset_service.clean_discord(
            guild, bot_user_id, targets, report, now=now
        )
    except Exception as exc:  # noqa: BLE001 — the last report must say it stopped
        log.exception("factory reset: the Discord clean-up stopped")
        # The owner's private progress message keeps the text (a test pins it); the log
        # channel gets the kind of fault only, the detail staying in the host's log.
        await report(f"⛔ Factory reset: the Discord clean-up stopped: {exc}")
        await close(
            "Clean-up stopped part-way",
            f"Discord: the clean-up stopped: {describe_fault(exc)}.",
        )
        return
    detail = (
        f"Discord: {outcome.channels_deleted} channel(s) and "
        f"{outcome.messages_deleted} message(s) deleted."
    )
    if outcome.faults:
        detail += "\n  " + "\n  ".join(outcome.faults)
        await close("Clean-up finished with faults", detail)
    else:
        await close("Clean-up finished", detail)


async def _open_progress(user: discord.User | discord.Member) -> discord.Message | None:
    """The direct message the clean-up edits as it goes, or None where DMs are closed.

    Off the change queue, as the clean-up it reports on is (`clean_discord`): the message is the
    reply to the owner's own command, not a post to the league, and the reset empties the database
    the queue lives in, so there is nothing to hold a refusal for Retry and Discard. A refusal is
    logged on the host and the progress goes to the log alone.
    """
    try:
        return await user.send("🧹 Factory reset: starting the Discord clean-up.")
    except discord.HTTPException:
        log.warning("factory reset: could not message %s; progress goes to the log only", user)
        return None


def _queue_refusal(job_id: int, stopped: bool, lane: str = LEAGUE_LANE) -> str:
    """Why pack will not run while a lane holds a job, naming the job it would wait on and the
    lane it is on: the queue (the league's), or the turns in a conversation's channel."""
    where = lane_name(lane)
    if stopped:
        return (
            f"⛔ The bot does not leave a server while a lane holds a job: job #{job_id} on "
            f"{where} is stopped. A league manager or admin can press Retry on its notice in "
            f"the log channel, or a league admin Discard, and then pack again."
        )
    return (
        f"⛔ The bot does not leave a server while a lane holds a job: job #{job_id} on "
        f"{where} is still to run. Let it finish, then pack again."
    )


def _current_season_refusal(season_number: int, stage: str | None) -> str:
    """Why pack will not run while the league has a current season."""
    shown = (stage or "setup").replace("_", " ").lower()
    return (
        f"⛔ Season {season_number} is current (stage: {shown}). The bot does not leave a "
        f"server while a season is under way — complete it, or cancel or abort it, first."
    )
