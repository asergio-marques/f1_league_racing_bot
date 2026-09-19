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

`/bot pack` is a league admin's command and is given in the interaction channel like any
other: it releases the settings rather than repairing them. `/bot factory-reset` is the server
owner's alone, from any channel; see `utils.channel_guard.server_owner_only`.
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from db.database import get_connection
from models.server_config import ServerConfig
from services import backup_service, factory_reset_service, pack_service
from utils.channel_guard import bot_setup_only, league_admin_only, server_owner_only

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
    def __init__(self, bot: commands.Bot) -> None:
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

        cfg = ServerConfig(
            server_id=server_id,
            interaction_role_id=interaction_role.id,
            league_admin_role_id=league_admin_role.id,
            interaction_channel_id=interaction_channel.id,
            log_channel_id=log_channel.id,
        )
        created = await self.bot.config_service.save_server_config(cfg)
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

        # Seed default F1 teams + Reserve for this server if none exist yet
        await self.bot.team_service.seed_default_teams_if_empty()  # type: ignore[attr-defined]

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
    # The three settings, each written on its own
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
    ) -> None:
        """Shared body of the three setting commands.

        Writes a single column, so nothing else in the row — test mode, the module flags,
        the other two settings — can be carried over stale from a half-built model.
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

        changed = await self.bot.config_service.set_core_setting(column, value)
        if not changed:
            await interaction.response.send_message(
                "⛔ This server is not configured yet — run `/bot init` first.",
                ephemeral=True,
            )
            return

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

        async with get_connection(self.bot.db_path) as db:  # type: ignore[attr-defined]
            season = await pack_service.current_season(db)
        if season is not None:
            await interaction.response.send_message(
                _current_season_refusal(*season), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        await self.bot.output_router.post_log(  # type: ignore[attr-defined]
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /bot pack | Success\n"
            f"  The bot no longer serves this server. `/bot init` on another claims it."
        )
        try:
            result = await pack_service.pack(
                self.bot.db_path,  # type: ignore[attr-defined]
                self.bot.scheduler_service,  # type: ignore[attr-defined]
                self.bot,
            )
        except pack_service.PackRefused as refused:
            await self.bot.output_router.post_log(  # type: ignore[attr-defined]
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
            f"Cleared: the four bot settings, **{result.team_roles}** team role(s), the "
            f"signup channel and roles, **{result.wizards}** signup wizard(s), "
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
        db_path = self.bot.db_path  # type: ignore[attr-defined]
        scheduler = self.bot.scheduler_service  # type: ignore[attr-defined]

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

        bot_user_id = self.bot.user.id  # type: ignore[union-attr]
        self._clean_up = asyncio.create_task(
            _clean_up(interaction.guild, bot_user_id, targets, report)
        )


async def _clean_up(guild, bot_user_id: int, targets, report) -> None:
    """Run the clean-up, and say so where something it did not expect stops it."""
    try:
        await factory_reset_service.clean_discord(guild, bot_user_id, targets, report)
    except Exception as exc:  # noqa: BLE001 — the last report must say it stopped
        log.exception("factory reset: the Discord clean-up stopped")
        await report(f"⛔ Factory reset: the Discord clean-up stopped: {exc}")


async def _open_progress(user: discord.abc.User) -> discord.Message | None:
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
