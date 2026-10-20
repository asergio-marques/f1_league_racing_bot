"""AdminReviewCog — admin signup review panel (Approve / Request Changes / Reject).

The cog captures the reason a manager types after Reject or Request Changes. A Reject reason is
asked of the change queue as the manager's `signup.end`, and a Request Changes reason as
`signup.request_changes`, each through the manager's stored press; the message is deleted by that
change's own jobs, not at once. The views and their
Discord interaction callbacks (T031 AdminReviewView, T035 CorrectionParameterView) are in
`signup/services/signup_views.py`, beside the jobs that post them; the pending reasons they park
are held there too. The heavy state-machine logic is delegated to WizardService.
"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from leaguebot.core.utils.interaction_errors import report_failure
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.core.utils.league_server import is_foreign_guild
from leaguebot.signup.services.review_changes import REQUEST
from leaguebot.signup.services.signup_views import _BUTTON_OF, _PENDING_REASONS, _review_button
from leaguebot.signup.services.wizard_changes import END, REJECTED

log = logging.getLogger(__name__)


class AdminReviewCog(commands.Cog):
    """Cog that holds the admin review views for signup approvals."""

    def __init__(self, bot: LeagueBot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Capture the admin's reason message for Request Changes / Reject.

        The reason completes the change the press asked for, so it counts as the button's own
        work: a fault in the service is reported through the press's stored interaction
        (`report_failure`) — the manager is told, one failure line is written — and never
        escapes to discord.py's event handler, which would tell nobody. The catch wraps only
        that call, hands the error over and returns (architecture.md, "Errors and failures").
        A signup that has moved on since the press is a refusal, not a fault.
        """
        if message.author.bot or not message.guild:
            return
        if await is_foreign_guild(self.bot, message.guild.id):
            return
        key = (message.channel.id, message.author.id)
        pending = _PENDING_REASONS.pop(key, None)
        if pending is None:
            return
        lapse = pending.get("lapse")
        if lapse is not None:
            lapse.cancel()

        reason = message.content.strip() or "No specific reason given."

        action = pending["action"]
        interaction: discord.Interaction = pending["interaction"]
        what = _review_button(interaction, pending["discord_user_id"], _BUTTON_OF[action])
        actor = pending["actor"]
        if action == "reject":
            kind = END
            payload = {
                "user_id": pending["discord_user_id"], "reason": REJECTED,
                "actor_id": actor.id, "actor_name": actor.display_name,
                "text": reason, "reason_message_id": message.id,
            }
        elif action == "request_changes":
            kind = REQUEST
            payload = {
                "user_id": pending["discord_user_id"],
                "actor_id": actor.id, "actor_name": actor.display_name,
                "text": reason, "reason_message_id": message.id,
            }
        else:
            return
        try:
            await self.bot.change_queue.ask(
                kind, payload, interaction=interaction, actor=actor, what=what,
            )
        except Exception as error:  # noqa: BLE001 — handed to report_failure, which tells the manager
            await report_failure(interaction, error, what=what)


async def setup(bot: LeagueBot) -> None:
    await bot.add_cog(AdminReviewCog(bot))
