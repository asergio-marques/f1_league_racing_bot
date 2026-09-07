"""BackupCog — /backup save, lock, restore and status.

**For driving a test season, and gated so it can be nothing else.** Every command here
requires the server to be in **test mode** and the member to hold Discord's
**Administrator** permission. The commands copy and replace the whole database file, which
holds every server the bot serves, so the test-mode requirement is what stands between this
and a real league's history: test mode itself refuses to switch on while a real driver
stands in a live season, so a server that can run these commands is a server with nothing
real to lose (decided 2026-09-07).

The copying, the checking and the staging all live in `services.backup_service`, which
knows nothing of Discord. What is here is the gate, the confirmation and the wording.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from services import backup_service
from utils.channel_guard import channel_guard, server_admin_only

log = logging.getLogger(__name__)


def _jobstore_path(bot) -> str:
    """Where the scheduler keeps its jobs, asked of the scheduler rather than guessed."""
    scheduler = getattr(bot, "scheduler_service", None)
    path = getattr(scheduler, "_jobstore_path", None)
    if path:
        return str(path)
    from services.scheduler_service import default_jobstore_path

    return str(default_jobstore_path(bot.db_path))


class BackupCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    backup = app_commands.Group(
        name="backup",
        description="Save and restore the database while testing.",
    )

    async def _refuse_outside_test_mode(self, interaction: discord.Interaction) -> bool:
        """Reply and return True where the server is not in test mode.

        Read at the moment of the command rather than trusted from earlier: the flag can
        be turned off between one command and the next, and a restore is not something to
        run on the strength of a stale reading.
        """
        config = await self.bot.config_service.get_server_config(  # type: ignore[attr-defined]
            interaction.guild_id
        )
        if config is not None and config.test_mode_active:
            return False
        await interaction.followup.send(
            "⛔ The backup commands run only while the server is in **test mode**. They "
            "copy and replace the whole database, which is not something to do to a "
            "league that is running. Turn test mode on with `/test-mode toggle` first.",
            ephemeral=True,
        )
        return True

    # ── save ──────────────────────────────────────────────────────────────

    @backup.command(
        name="save",
        description="Save the current database and scheduler as a backup.",
    )
    @channel_guard
    @server_admin_only
    async def backup_save(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        db_path = self.bot.db_path  # type: ignore[attr-defined]
        scheduler = getattr(self.bot, "scheduler_service", None)

        # Paused around the copy. APScheduler writes its jobstore on the event-loop
        # thread, so a job added mid-copy would be caught half-written; the backup API
        # makes that unlikely and this makes it impossible.
        paused = False
        try:
            if scheduler is not None and getattr(scheduler, "_scheduler", None) is not None:
                if scheduler._scheduler.running:
                    scheduler._scheduler.pause()
                    paused = True
            backup_service.save(db_path, _jobstore_path(self.bot))
        except backup_service.BackupError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            return
        except Exception:
            log.exception("backup save: failed for server %s", interaction.guild_id)
            await interaction.followup.send(
                "⛔ The backup could not be taken. The log channel has the detail.",
                ephemeral=True,
            )
            return
        finally:
            if paused:
                scheduler._scheduler.resume()

        state = backup_service.state(db_path)
        await interaction.followup.send(
            f"✅ Saved. The backup holds {state.size_bytes // 1024} KB and replaces "
            f"whatever was there before.\n"
            f"Lock it with `/backup lock` if you want to keep this one.",
            ephemeral=True,
        )
        log.info(
            "backup save: server=%s by %s", interaction.guild_id, interaction.user
        )

    # ── lock ──────────────────────────────────────────────────────────────

    @backup.command(
        name="lock",
        description="Lock or unlock the saved backup, so a save cannot overwrite it.",
    )
    @channel_guard
    @server_admin_only
    async def backup_lock(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        db_path = self.bot.db_path  # type: ignore[attr-defined]
        if not backup_service.state(db_path).exists:
            await interaction.followup.send(
                "⛔ There is no saved backup to lock. Take one with `/backup save`.",
                ephemeral=True,
            )
            return

        locked = backup_service.set_lock(db_path, who=interaction.user.display_name)
        await interaction.followup.send(
            "🔒 Locked. `/backup save` will refuse to overwrite it until you run this "
            "again."
            if locked
            else "🔓 Unlocked. `/backup save` will overwrite it from now on.",
            ephemeral=True,
        )

    # ── status ────────────────────────────────────────────────────────────

    @backup.command(
        name="status",
        description="Show whether a backup exists, when it was taken, and if it is locked.",
    )
    @channel_guard
    @server_admin_only
    async def backup_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        state = backup_service.state(self.bot.db_path)  # type: ignore[attr-defined]
        if not state.exists:
            await interaction.followup.send(
                "📭 There is no saved backup. Take one with `/backup save`.",
                ephemeral=True,
            )
            return

        lines = [
            "📦 **Saved backup**",
            f"  Taken: {discord.utils.format_dt(state.taken_at, 'F')}",
            f"  Size: {state.size_bytes // 1024} KB",
            f"  Readable: {'yes' if state.readable else '**no — it cannot be restored**'}",
            f"  Locked: {state.locked_by if state.locked else 'no'}",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ── restore ───────────────────────────────────────────────────────────

    @backup.command(
        name="restore",
        description="Replace the database with the saved backup. Needs a restart.",
    )
    @channel_guard
    @server_admin_only
    async def backup_restore(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if await self._refuse_outside_test_mode(interaction):
            return

        state = backup_service.state(self.bot.db_path)  # type: ignore[attr-defined]
        if not state.exists:
            await interaction.followup.send(
                "⛔ There is no saved backup to restore. Take one with `/backup save`.",
                ephemeral=True,
            )
            return
        if not state.readable:
            await interaction.followup.send(
                "⛔ The saved backup is not a readable database, so it will not be "
                "restored. Take a fresh one with `/backup save`.",
                ephemeral=True,
            )
            return

        # Confirmed rather than done: everything the bot currently holds is about to be
        # replaced by a snapshot of some earlier moment, and the command that does it is
        # one word from `/backup save`.
        view = _ConfirmRestoreView(self, interaction.user.id)
        await interaction.followup.send(
            f"⚠️ **Restore the backup taken "
            f"{discord.utils.format_dt(state.taken_at, 'R')}?**\n"
            f"Everything the bot holds now — seasons, rounds, drivers, results and every "
            f"scheduled job — is replaced by what that backup holds. A copy of the current "
            f"database is kept beside it first.\n"
            f"**The bot must be restarted afterwards** to pick it up.",
            view=view,
            ephemeral=True,
        )


class _ConfirmRestoreView(discord.ui.View):
    """The confirmation on a restore, and the staging behind it."""

    def __init__(self, cog: BackupCog, requester_id: int) -> None:
        super().__init__(timeout=120)
        self._cog = cog
        self._requester_id = requester_id

    @discord.ui.button(label="♻️ Restore", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if interaction.user.id != self._requester_id:
            await interaction.response.send_message(
                "⛔ Only the person who ran the command can confirm it.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        bot = self._cog.bot
        try:
            backup_service.stage_restore(bot.db_path, _jobstore_path(bot))  # type: ignore[attr-defined]
        except backup_service.BackupError as exc:
            await interaction.followup.send(f"⛔ {exc}", ephemeral=True)
            self.stop()
            return
        except Exception:
            log.exception("backup restore: staging failed")
            await interaction.followup.send(
                "⛔ The restore could not be prepared. Nothing has been changed.",
                ephemeral=True,
            )
            self.stop()
            return

        await interaction.followup.send(
            "✅ The backup is staged. **Restart the bot** and it will come back on the "
            "restored database — under a service it will restart itself; from a terminal, "
            "stop it and run it again.\n"
            "The database being replaced was copied to `bot.prerestore.db` first, so this "
            "can be walked back by hand if it was not what you wanted.",
            ephemeral=True,
        )
        log.info("backup restore: staged by %s", interaction.user)
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_message(
            "Nothing has been changed.", ephemeral=True
        )
        self.stop()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BackupCog(bot))
