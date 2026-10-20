"""AdminReviewCog — admin signup review panel (Approve / Request Changes / Reject).

The cog captures the reason a manager types after Reject or Request Changes. The views and their
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
from leaguebot.signup.services.signup_views import (
    _BUTTON_OF,
    _NOTHING_DONE,
    _PENDING_REASONS,
    _refuse_review_button,
    _review_button,
)

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

        try:
            await message.delete()
        except discord.HTTPException:
            pass

        action = pending["action"]
        followup: discord.Webhook = pending["followup"]
        interaction: discord.Interaction = pending["interaction"]
        service = {
            "request_changes": self.bot.wizard_service.request_changes,
            "reject": self.bot.wizard_service.reject_signup,
        }.get(action)
        if service is None:
            return
        done = "✅ Correction requested." if action == "request_changes" else "✅ Signup rejected."
        try:
            refused = await service(
                pending["discord_user_id"], pending["guild"], pending["actor"], reason=reason,
            )
        except Exception as error:  # noqa: BLE001 — handed to report_failure, which tells the manager
            await report_failure(
                interaction,
                error,
                what=_review_button(interaction, pending["discord_user_id"], _BUTTON_OF[action]),
            )
            return
        if refused is not None:
            # The signup moved on while the reason was being typed (#492): nothing was done,
            # and the driver keeps the status they have.
            button = _BUTTON_OF[action]
            await _refuse_review_button(
                interaction,
                pending["discord_user_id"],
                button,
                f"⛔ This signup has moved on since you pressed {button}. {_NOTHING_DONE[action]}.",
            )
            return
        await followup.send(done, ephemeral=True)


async def setup(bot: LeagueBot) -> None:
    await bot.add_cog(AdminReviewCog(bot))
