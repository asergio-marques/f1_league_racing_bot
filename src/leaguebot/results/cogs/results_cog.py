"""ResultsCog — /results config, /results amend, /results reserves, /results standings,
/results rounds and /results channel commands.

`/results rounds amend` sits beside `/results rounds sync`: it sat under core's `/round` until
#462 moved it, and only its name changed.
"""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.results.cogs.config_commands import ConfigCommands
from leaguebot.results.cogs.points_amend_commands import PointsAmendCommands
from leaguebot.results.cogs.results_group import RESULTS_GROUP
from leaguebot.results.cogs.rounds_commands import RoundsCommands
from leaguebot.results.services.module.channel_settings import (
    NO_ACCESS,
    NOT_ENABLED,
    RESULTS_CHANNEL,
    STANDINGS_CHANNEL,
    VERDICTS_CHANNEL,
    verdicts_division_not_found,
)
from leaguebot.core.services.settings.division_settings import NO_SEASON_REFUSAL, division_not_found
from leaguebot.core.utils.channel_guard import league_manager_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import is_foreign_guild
from leaguebot.core.utils.log_lines import refuse

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bulk modal classes (T013 / T014)
# ---------------------------------------------------------------------------

#: The kind of change each `/results channel` command asks of the queue.
_CHANNEL_KINDS = {
    "results": RESULTS_CHANNEL,
    "standings": STANDINGS_CHANNEL,
    "verdicts": VERDICTS_CHANNEL,
}


class ResultsCog(ConfigCommands, PointsAmendCommands, RoundsCommands, commands.Cog):
    results_group = RESULTS_GROUP

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Guard: block messages while a round is in penalty review (T014)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        from leaguebot.results.services.review.review_prompts import is_channel_in_penalty_review

        if await is_channel_in_penalty_review(self.bot.db_path, message.channel.id):
            # Deleted and warned by jobs on the round's lane, not at once (#439).
            from leaguebot.results.services.review.review_lock_change import ask_lock

            await ask_lock(self.bot.change_queue, message)
            return
        # The opener's message in an open amendment's channel is a paste too: a turn on the
        # amendment's lane (#439).
        from leaguebot.results.services.round_amendment.amend_turn_change import take_amended_paste

        if await take_amended_paste(self.bot, message, queue=self.bot.change_queue):
            return
        # A league manager's message in a round's open submission is a paste: marked at once and
        # taken as a turn on the round's lane, or turned away while an earlier one waits (#439).
        from leaguebot.results.services.submission.submission_turn_change import take_paste

        await take_paste(
            self.bot, message, queue=self.bot.change_queue,
            config=await self.bot.config_service.get_server_config(),
        )

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    async def _module_gate(
        self, interaction: discord.Interaction, *, record: bool = True
    ) -> bool:
        """Whether the results module is on; where it is not, refuse and return False.

        The refusal is recorded in the log channel, as every refusal of a command that acts
        is, unless *record* is False — which the views and lists pass, since they change
        nothing and record nothing.
        """
        if not await self.bot.module_service.is_results_enabled():
            reply = "\u274c The Results & Standings module is not enabled on this server."
            if record:
                await refuse(interaction, reply, what=describe(interaction))
            else:
                await interaction.response.send_message(reply, ephemeral=True)
            return False
        return True

    async def _answer_nothing_changed(
        self,
        interaction: discord.Interaction,
        command: str,
        reply: str,
        *details: str,
    ) -> None:
        """Answer a request that could have been carried out but changes nothing, and record it.

        The reply is *reply* under an information mark, and the log gets one line in the success
        form, ``Name (<@id>) | /<command> | Nothing changed``, with *details* beneath it, as
        "The record of what changed" asks of a no-op (#482). No audit entry is written: the
        caller has changed nothing to audit. A request that could not be carried out is a
        refusal and never comes here.
        """
        await interaction.followup.send(f"\u2139\ufe0f {reply}", ephemeral=True)
        await self.bot.output_router.post_log(
            f"{interaction.user.display_name} (<@{interaction.user.id}>) | /{command} | "
            "Nothing changed" + "".join(f"\n  {detail}" for detail in details),
        )

    async def _sync_gate(
        self, interaction: discord.Interaction, div, *, what: str
    ) -> bool:
        """Refuse a sync of a division with an amendment open (#345, decided 2026-09-21).

        An amendment's first stage writes its corrections and recalculates the division,
        publishing nothing until its last stage is approved. A sync reposts the division from
        the same database, so it would publish them unapproved — and leave them published if
        the amendment were then cancelled or lapsed, its revert posting nothing. It waits, as a
        submission of another of the division's rounds does.

        The wait is a refusal and is recorded as one (#482), *what* being the caller's own name
        of the command as the log channel should read it.
        """
        from leaguebot.results.services.round_amendment.amendment_writes import sync_held_by_amendment

        reply = await sync_held_by_amendment(self.bot.db_path, div.id)
        if reply is None:
            return True
        await refuse(interaction, reply, what=what)
        return False

    # ------------------------------------------------------------------
    # /results channel group
    # ------------------------------------------------------------------

    channel_group = app_commands.Group(
        name="channel",
        description="Set the channels a division's results are posted to",
        parent=results_group,
    )

    async def _set_division_channel(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
        channel_type: str,  # "results" | "standings" | "verdicts"
    ) -> None:
        """Ask the change queue to set a division's results, standings or verdicts channel, for
        the three commands below.

        Results' own commands, under results' own group. Each checks its module first in its own
        words, not `_module_gate`'s.

        **The live season's division**: a division's channels belong to the season being built or
        raced, and an archived one's no longer matter. Pending completion is live, so a channel
        lost before the season completes may be repaired. The command finds the division from
        the name typed, since the change is for it by its id (`results_channel_settings`).

        **A channel does one job** (`channel_registry_service`, judged by the setting's `check`),
        as the queue is asked and again as the change starts, so a refusal leaves the
        configuration exactly as it stood — the value the setting already holds included. The
        queue's acknowledgement is the command's response, so nothing is deferred; the queue
        writes the channel and its audit entry in one save and answers and logs after it.

        **Verdicts' channel is one the bot must be able to post in**, refused before the season
        is read, and its division is refused in words of its own.

        Every refusal is recorded: `refuse` answers and writes one line naming the command,
        which is read from the interaction.
        """
        if not await self.bot.module_service.is_results_enabled():
            await refuse(interaction, NOT_ENABLED, what=describe(interaction))
            return

        if channel_type == "verdicts":
            guild = interaction.guild
            if guild is None or not channel.permissions_for(guild.me).send_messages:
                await refuse(interaction, NO_ACCESS, what=describe(interaction))
                return

        season = await self.bot.season_service.get_setup_or_active_season()
        if season is None:
            await refuse(interaction, NO_SEASON_REFUSAL, what=describe(interaction))
            return

        divisions = await self.bot.season_service.get_divisions(season.id)
        div = next((d for d in divisions if d.name.lower() == name.lower()), None)
        if div is None:
            not_found = (
                verdicts_division_not_found if channel_type == "verdicts" else division_not_found
            )
            await refuse(interaction, not_found(name), what=describe(interaction))
            return

        await self.bot.change_queue.ask(
            _CHANNEL_KINDS[channel_type],
            {
                "division_id": div.id,
                "division_name": name,
                "value": channel.id,
                "channel_name": channel.name,
            },
            interaction=interaction,
            what=describe(interaction),
        )

    @channel_group.command(
        name="results",
        description="Set the results posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Results channel")
    @league_manager_only
    async def channel_results(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "results")

    @channel_group.command(
        name="standings",
        description="Set the standings posting channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Standings channel")
    @league_manager_only
    async def channel_standings(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        await self._set_division_channel(interaction, name, channel, "standings")

    @channel_group.command(
        name="verdicts",
        description="Set the verdicts (penalty announcement) channel for a division.",
    )
    @app_commands.describe(name="Division name", channel="Verdicts announcement channel")
    @league_manager_only
    async def channel_verdicts(
        self,
        interaction: discord.Interaction,
        name: str,
        channel: discord.TextChannel,
    ) -> None:
        """Set the channel a division's verdicts are announced in, by `_set_division_channel`."""
        await self._set_division_channel(interaction, name, channel, "verdicts")
