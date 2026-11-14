"""AdminReviewCog — admin signup review panel (Approve / Request Changes / Reject).

The cog captures the reason a manager types after Reject or Request Changes. A Reject reason is
asked of the change queue as the manager's `signup.end`, and a Request Changes reason as
`signup.request_changes`, each through the manager's stored press; the message is deleted by that
change's own jobs, not at once; where the press is refused (the signup has moved on), the reason
is taken down by a change of its own on the driver's lane. The views and their
Discord interaction callbacks (T031 AdminReviewView, T035 CorrectionParameterView) are in
`signup/services/signup_views.py`, beside the jobs that post them; the pending reasons they park
are held there too. The review's work is the `signup.*` change types on the change queue.
"""

from __future__ import annotations

import discord
from discord.ext import commands

from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import is_foreign_guild
from leaguebot.signup.services.signup_views import take_reason


class AdminReviewCog(commands.Cog):
    """Cog that holds the admin review views for signup approvals."""

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Hand a message that may be a manager's reason for Request Changes / Reject to
        `take_reason`."""
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        await take_reason(self.bot, message, queue=self.bot.change_queue)
