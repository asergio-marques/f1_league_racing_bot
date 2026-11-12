"""`ResultsCog`: the `/results` group, composed from one class for each of its subgroups.

The subgroups' commands live in `config_commands.py`, `points_amend_commands.py`,
`rounds_commands.py` (reserves, standings and rounds) and `channel_commands.py`, each under the
one `/results` group declared in `results_group.py`. The cog stays one cog for one group: the
class derives from them, which discord.py allows because it collects a cog's commands across the
classes the cog derives from, and a cog too large to read in one file is split by subgroup rather
than into a cog for each, as the architecture's cog bullet gives it (`docs/design/architecture.md`).

What is left here is what the subgroups share: the cog's bot, the listener that guards a round's
channels, and the gates the subgroups' commands call.
"""
from __future__ import annotations

import logging

import discord
from discord.ext import commands

from leaguebot.results.cogs.channel_commands import ChannelCommands
from leaguebot.results.cogs.config_commands import ConfigCommands
from leaguebot.results.cogs.points_amend_commands import PointsAmendCommands
from leaguebot.results.cogs.results_group import RESULTS_GROUP
from leaguebot.results.cogs.rounds_commands import RoundsCommands
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import is_foreign_guild
from leaguebot.core.utils.log_lines import refuse

log = logging.getLogger(__name__)


class ResultsCog(
    ConfigCommands, PointsAmendCommands, RoundsCommands, ChannelCommands, commands.Cog
):
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
