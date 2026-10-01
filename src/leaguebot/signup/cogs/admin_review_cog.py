"""AdminReviewCog — admin signup review panel (Approve / Request Changes / Reject).

Views and their Discord interaction callbacks are implemented here.
The heavy state-machine logic is delegated to WizardService.

T031: AdminReviewView (Approve, Request Changes, Reject buttons)
T035: CorrectionParameterView (one button per collectable parameter)
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import CallbackButton, Handler, LeagueView, channel_id_of, guild_of, is_foreign_guild
from leaguebot.core.utils.interaction_errors import report_failure
from leaguebot.core.utils.log_lines import interaction_member, record_abandoned, refuse

log = logging.getLogger(__name__)

# Maps (channel_id, admin_user_id) → pending action context.
# Used to capture the admin's reason message before executing the action.
#
# **A pending reason lapses after five minutes** (`_REASON_LAPSE_SECONDS`), and nothing is done:
# the manager is told, one lapse line is written, and the signup still awaits review. The
# lapse is an asyncio task kept in the entry under "lapse" — never a bare `create_task`, whose
# reference the loop does not hold, and never an APScheduler job, which would outlive a restart
# that drops the entry. It acts only while its own entry is the one pending, so a later press
# that has put another in its place is left to its own five minutes; a reason that arrives takes
# the entry and cancels the task. A restart drops a pending reason unrecorded: the store is in
# memory, and the press that began it has been logged. `clear_pending_reasons` drops every entry
# and cancels every lapse, so a pack or a factory reset leaves none to fire into a changed league.
_PENDING_REASONS: dict[tuple[int, int], dict] = {}

#: How long a manager has to type the reason after pressing Reject or Request Changes.
_REASON_LAPSE_SECONDS = 5 * 60

#: The button that parks each pending action, as the log channel names it.
_BUTTON_OF = {"reject": "Reject", "request_changes": "Request Changes"}

#: What did not happen, said where a pending reason lapses or arrives too late.
_NOTHING_DONE = {"reject": "Nothing was rejected", "request_changes": "No changes were requested"}


def clear_pending_reasons() -> None:
    """Drop every pending reason and cancel its five-minute lapse, so none fires after.

    Called by `clear_in_memory_state`, for `/bot pack` and `/bot factory-reset`.
    """
    for entry in _PENDING_REASONS.values():
        lapse = entry.get("lapse")
        if lapse is not None:
            lapse.cancel()
    _PENDING_REASONS.clear()


async def _lapse_pending_reason(key: tuple[int, int], entry: dict) -> None:
    """After `_REASON_LAPSE_SECONDS`, end the pending *entry* unanswered: tell the manager, record
    the lapse naming them, and leave the signup awaiting review.

    Acts only while *entry* is still the one pending under *key*: a reason that arrived has
    taken it, and a later press has replaced it.
    """
    await asyncio.sleep(_REASON_LAPSE_SECONDS)
    if _PENDING_REASONS.get(key) is not entry:
        return
    del _PENDING_REASONS[key]
    interaction: discord.Interaction = entry["interaction"]
    followup: discord.Webhook = entry["followup"]
    label = _BUTTON_OF[entry["action"]]
    nothing_done = _NOTHING_DONE[entry["action"]]
    try:
        await followup.send(
            f"⌛ No reason arrived within five minutes. {nothing_done}; the signup still awaits review.",
            ephemeral=True,
        )
    except Exception:  # noqa: BLE001 — the lapse is still recorded
        log.warning("could not tell the manager that their %s reason lapsed", label, exc_info=True)
    await record_abandoned(
        bot_of(interaction),
        entry["actor"],
        what=f"the “{label}” button of {_review_owner(interaction, entry['discord_user_id'])} signup review",
        lapsed=True,
        detail=f"{nothing_done}; the signup still awaits review. Press {label} again to give a reason.",
    )


def _review_owner(interaction: discord.Interaction, owner_id: str | None) -> str:
    """Whose signup review a button sits on, as the log channel names it: "Alex's", by the display
    name the league's server gives them, or their mention where the name is not to be had. Nobody
    (a panel whose driver cannot be found) is "a"."""
    if owner_id is None:
        return "a"
    guild = interaction.guild
    member = guild.get_member(int(owner_id)) if guild is not None else None
    name = getattr(member, "display_name", None)
    return f"{name if isinstance(name, str) else f'<@{owner_id}>'}'s"


def _review_button(interaction: discord.Interaction, owner_id: str | None, label: str) -> str:
    """A button of a signup review, as the log channel names it: "the “Approve” button of Alex's
    signup review"."""
    return f"the “{label}” button of {_review_owner(interaction, owner_id)} signup review"


async def _refuse_review_button(
    interaction: discord.Interaction, owner_id: str | None, label: str, reply: str
) -> None:
    """Turn a press on a signup review's button away with *reply*, and record it.

    The line names the button and whose review it sits on, "the “Approve” button of Alex's signup
    review", and the member who pressed it (`refuse`). *owner_id* is the signup's driver, None
    where it is not known.
    """
    await refuse(
        interaction,
        reply,
        what=_review_button(interaction, owner_id, label),
    )


async def _may_review_signup(interaction: discord.Interaction) -> bool:
    """Whether the presser holds the league manager tier, or better.

    This panel is posted publicly into the driver's own signup channel, which the driver
    themselves can read — so this check is the only thing standing between a driver and
    approving their own signup. It is not a formality.

    It used to admit Discord's Manage Guild permission, a level the two-tier model has no
    room for, and it read the interaction role by hand. Both are now one question asked of
    `is_league_manager`, so the button and the commands agree by construction rather than by
    two implementations happening to match.

    The bare `except` is kept deliberately: a config-service failure must not become a
    silent *grant*, so it falls through to False.
    """
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return False
    bot = bot_of(interaction)
    try:
        server_cfg = await bot.config_service.get_server_config()
    except Exception:  # noqa: BLE001 — see the docstring: a fault refuses, never grants
        log.warning("signup review: the server configuration could not be read", exc_info=True)
        return False
    if server_cfg is None:
        return False
    return is_league_manager(server_cfg, interaction.user)


class AdminReviewView(LeagueView):
    """Approve / Request Changes / Reject buttons for admin signup review (T031).

    Restricted to the league manager tier — the interaction role, or the league admin role.
    First action wins; subsequent interactions receive an ephemeral error.
    FR-039, A-004.
    """

    def __init__(self, discord_user_id: str | None = None, bot: LeagueBot | None = None) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._bot = bot

    async def _resolve(self, interaction: discord.Interaction):
        """Return (bot, discord_user_id) resolving from channel when not stored."""
        _bot = self._bot or bot_of(interaction)
        _user_id = self._discord_user_id
        if _user_id is None:
            wizard = await _bot.wizard_service.get_wizard_by_channel(
                channel_id_of(interaction)
            )
            _user_id = wizard.discord_user_id if wizard else None
        return _bot, _user_id

    async def _guard(self, interaction: discord.Interaction, label: str):
        """Check permissions and race-condition guard.  Returns (True, bot, user_id) to proceed.

        Each refusal is answered to the presser and recorded in the log channel (`refuse`), as
        the button *label* of the signup review it sits on.
        """
        if not await _may_review_signup(interaction):
            await _refuse_review_button(
                interaction, self._discord_user_id, label, "⛔ Insufficient permissions."
            )
            return False, None, None
        _bot, _user_id = await self._resolve(interaction)
        if _user_id is None:
            await _refuse_review_button(
                interaction, None, label, "⛔ Could not identify driver for this signup."
            )
            return False, None, None
        # Race-condition guard: driver must still be in PENDING_ADMIN_APPROVAL
        profile = await _bot.driver_service.get_profile(
            _user_id
        )
        if profile is None or profile.current_state != DriverState.PENDING_ADMIN_APPROVAL:
            await _refuse_review_button(
                interaction, _user_id, label, "⛔ This signup has already been actioned."
            )
            return False, None, None
        return True, _bot, _user_id

    @discord.ui.button(label="Approve", style=discord.ButtonStyle.success, custom_id="admin_approve")
    async def approve_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _user_id = await self._guard(interaction, "Approve")
        if not ok:
            return
        await interaction.response.defer(ephemeral=True)
        role_note = await _bot.wizard_service.approve_signup(
            _user_id, interaction.guild, interaction.user
        )
        reply = "✅ Signup approved."
        if role_note:
            reply += f"\n⚠️ {role_note}"
        await interaction.followup.send(reply, ephemeral=True)

    async def _ask_for_reason(
        self,
        interaction: discord.Interaction,
        user_id: str,
        label: str,
        action: str,
        prompt: str,
    ) -> None:
        """Park the press, which the manager's next message in this channel completes, and say so.

        The entry keeps the press's own interaction, through which the reason step answers the
        manager and records what became of it, and the press is written to the log channel
        ("Manager (<@id>) | the “Reject” button of Alex's signup review | Asked for a reason").
        The reason lapses after five minutes (`_PENDING_REASONS`).
        """
        await interaction.response.defer(ephemeral=True)
        key = (channel_id_of(interaction), interaction.user.id)
        entry: dict = {
            "action": action,
            "discord_user_id": user_id,
            "actor": interaction.user,
            "guild": interaction.guild,
            "interaction": interaction,
            "followup": interaction.followup,
        }
        _PENDING_REASONS[key] = entry
        await interaction.followup.send(prompt, ephemeral=True)
        await bot_of(interaction).output_router.post_log(
            f"{interaction_member(interaction)} | "
            f"the “{label}” button of {_review_owner(interaction, user_id)} signup review | "
            "Asked for a reason"
        )
        # Started last, so the lapse's line can never come before the press's own.
        if _PENDING_REASONS.get(key) is entry:
            entry["lapse"] = asyncio.create_task(_lapse_pending_reason(key, entry))

    @discord.ui.button(label="Request Changes", style=discord.ButtonStyle.secondary, custom_id="admin_request_changes")
    async def request_changes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _user_id = await self._guard(interaction, "Request Changes")
        if not ok:
            return
        await self._ask_for_reason(
            interaction, _user_id, "Request Changes", "request_changes",
            "Please type the reason for requesting changes in this channel. "
            "Your message will be automatically deleted.",
        )

    @discord.ui.button(label="Reject", style=discord.ButtonStyle.danger, custom_id="admin_reject")
    async def reject_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        ok, _bot, _user_id = await self._guard(interaction, "Reject")
        if not ok:
            return
        await self._ask_for_reason(
            interaction, _user_id, "Reject", "reject",
            "Please type the reason for rejecting this signup in this channel. "
            "Your message will be automatically deleted.",
        )


class CorrectionParameterView(LeagueView):
    """One button per collectable wizard parameter; admin selects which to re-collect (T035).

    Restricted to the league manager tier or above, through `_may_review_signup`.
    Calls WizardService.select_correction_parameter() with the chosen parameter label.
    FR-042.
    """

    _PARAMETERS = [
        ("Nationality",         "nationality"),
        ("Platform",            "platform"),
        ("Platform ID",         "platform_id"),
        ("Availability",        "availability"),
        ("Driver Type",         "driver_type"),
        ("Preferred Teams",     "preferred_teams"),
        ("Preferred Teammate",  "preferred_teammate"),
        ("Lap Times",           "lap_times"),
        ("Notes",               "notes"),
    ]

    def __init__(self, discord_user_id: str | None = None, bot: LeagueBot | None = None) -> None:
        super().__init__(timeout=None)  # persistent — logical timeout enforced by asyncio task
        self._discord_user_id = discord_user_id
        self._bot = bot

        for label, param_key in self._PARAMETERS:
            def make_callback(p: str, label: str) -> Handler:
                async def callback(inter: discord.Interaction) -> None:
                    _bot = self._bot or bot_of(inter)
                    _user_id = self._discord_user_id
                    if not await _may_review_signup(inter):
                        await _refuse_review_button(
                            inter, _user_id, label, "⛔ Insufficient permissions."
                        )
                        return
                    if _user_id is None:
                        wizard = await _bot.wizard_service.get_wizard_by_channel(
                            channel_id_of(inter)
                        )
                        _user_id = wizard.discord_user_id if wizard else None
                    if _user_id is None:
                        await _refuse_review_button(
                            inter, None, label, "⛔ Could not identify driver for this correction."
                        )
                        return
                    await inter.response.defer(ephemeral=True)
                    # The service acts only while the request is open (D2) and says why where
                    # it is not: a choice made after the window lapsed, after another was
                    # chosen or after the signup was decided is refused, not a fault.
                    ended = await _bot.wizard_service.select_correction_parameter(
                        _user_id, p, guild_of(inter)
                    )
                    if ended is not None:
                        await _refuse_review_button(inter, _user_id, label, f"⛔ {ended}")
                        return
                    await inter.followup.send(
                        f"✅ Re-collecting **{p.replace('_', ' ')}**.", ephemeral=True
                    )
                    await _bot.output_router.post_log(
                        f"{interaction_member(inter)} | "
                        f"{_review_button(inter, _user_id, label)} | "
                        f"Correction requested: {label.lower()}"
                    )
                return callback

            btn = CallbackButton(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"correct_{param_key}",
                on_press=make_callback(param_key, label),
            )
            self.add_item(btn)


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
