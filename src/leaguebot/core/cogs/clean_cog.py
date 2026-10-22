"""CleanCog — /clean-bot command.

Deletes the most recent messages in the current channel that were sent by the bot, as the
`core.clean` change of the queue (`core/services/clean_change.py`): the command checks the channel
is a text channel, asks the change, and the queue chooses and deletes when it gets to it.
Requires the bot to have the Manage Messages permission in that channel.

**How many is asked for, and capped at ten** (2026-09-07). The command took no parameter
and swept five hundred messages, which is a great deal of channel to lose to a slip of the
finger — and irreversibly, since Discord keeps no undo. Ten is enough for the job it is
actually for: clearing the tail of a multi-message command that did not clear itself. An
approved or expired `/season placements-review` now deletes its own report, which was the case the
five hundred was reaching for.
"""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from leaguebot.core.services.clean_change import KIND, MAX_DELETIONS
from leaguebot.core.utils.channel_guard import league_admin_only
from leaguebot.core.utils.interaction_errors import describe
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.log_lines import refuse


class CleanCog(commands.Cog):
    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    @app_commands.command(
        name="clean-bot",
        description="Delete the most recent bot messages in this channel.",
    )
    @app_commands.describe(
        count=f"How many of the bot's most recent messages to delete (1–{MAX_DELETIONS})."
    )
    @league_admin_only
    async def clean_bot(
        self,
        interaction: discord.Interaction,
        # The bound is the literal 10 and not `MAX_DELETIONS`: `from __future__ import
        # annotations` makes this a string, which discord.py evaluates while the class body
        # is still being read, before the module's own names are bound. A test asserts the
        # two agree, since nothing else here can.
        count: app_commands.Range[int, 1, 10],
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await refuse(
                interaction,
                "⛔ This command can only be used in a text channel.",
                what=describe(interaction),
            )
            return

        await self.bot.change_queue.ask(
            KIND,
            {"channel_id": channel.id, "count": count},
            interaction=interaction,
            what=f"deleting the bot's {count} newest message(s) in <#{channel.id}>",
            refusal_what=describe(interaction),
        )
