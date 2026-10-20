"""The views a signup's jobs post and the buttons they carry, beside the jobs that post them.

The sign-up button, the wizard's question buttons, the withdrawal button, and the admin review's
Approve / Request Changes / Reject and correction buttons live here, with the helpers and the
state they use, so a signup job imports no cog (docs/design/architecture.md, "How modules and
core fit together"). The cogs register them and the wizard posts them.

The heavy state-machine logic is delegated to WizardService.

T016: SignupButtonView; T027: WithdrawButtonView and the question buttons; T031: AdminReviewView
(Approve, Request Changes, Reject); T035: CorrectionParameterView (one button per collectable
parameter).
"""

from __future__ import annotations

import asyncio
import logging

from typing import TYPE_CHECKING

import discord

from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.utils.channel_guard import is_league_manager
from leaguebot.core.utils.league_bot import LeagueBot, bot_of
from leaguebot.core.utils.league_server import (
    CallbackButton,
    Handler,
    LeagueView,
    channel_id_of,
    guild_of,
)
from leaguebot.core.utils.log_lines import record_abandoned, refuse
from leaguebot.core.utils.member_names import interaction_member

if TYPE_CHECKING:
    from leaguebot.core.services.change_queue import ChangeQueue

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

    def __init__(
        self, discord_user_id: str | None = None, *, change_queue: ChangeQueue | None = None
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        # Handed the change queue when it is built, by whoever posts or registers the panel.
        self._queue = change_queue

    async def _resolve(self, interaction: discord.Interaction):
        """Return (bot, discord_user_id) resolving from channel when not stored."""
        _bot = bot_of(interaction)
        _user_id = self._discord_user_id
        if _user_id is None:
            wizard = await _bot.signup_module_service.get_wizard_by_channel(
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
        if self._queue is None:
            raise RuntimeError("a signup review panel was built with no change queue to ask")
        # Acknowledged privately and updated with what became of it; the state it was pressed in
        # is judged again, by the change's check, when the queue reaches it.
        await self._queue.ask(
            "signup.approve",
            {"user_id": _user_id, "actor_name": interaction.user.display_name},
            interaction=interaction,
            what=_review_button(interaction, _user_id, "Approve"),
        )

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
                        f"Correction requested: {label}"
                    )
                return callback

            btn = CallbackButton(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"correct_{param_key}",
                on_press=make_callback(param_key, label),
            )
            self.add_item(btn)


_MAX_TEAM_BUTTONS = 20  # upper bound for pteam_N stub handlers in registration mode


async def _resolve_view_context(
    interaction: discord.Interaction,
    stored_user_id: str | None,
) -> tuple:
    """Return (bot, discord_user_id) for a persistent-view callback.

    When the stored value is None (view registered for restart recovery via
    ``bot.add_view``), looks up the wizard by channel ID to identify the
    owning driver.
    """
    bot = bot_of(interaction)
    if stored_user_id is not None:
        return bot, stored_user_id
    wizard = await bot.wizard_service.get_wizard_by_channel(
        channel_id_of(interaction)
    )
    return bot, (wizard.discord_user_id if wizard else None)


def _wizard_owner(interaction: discord.Interaction, owner_id: str | None) -> str:
    """Whose signup wizard a button sits on, as the log channel names it: "Alex's", by the display
    name the league's server gives them, or their mention where the name is not to be had. Nobody
    (a channel whose wizard is gone) is "a"."""
    if owner_id is None:
        return "a"
    name = None
    if str(interaction.user.id) == owner_id:
        name = getattr(interaction.user, "display_name", None)
    else:
        guild = interaction.guild
        member = guild.get_member(int(owner_id)) if guild is not None else None
        name = getattr(member, "display_name", None)
    return f"{name if isinstance(name, str) else f'<@{owner_id}>'}'s"


def _pressed_label(interaction: discord.Interaction, fallback: str) -> str:
    """The label of the button that was pressed, read from its own message by its `custom_id`;
    *fallback* where the message does not carry it."""
    data = interaction.data
    custom_id = data.get("custom_id") if isinstance(data, dict) else None
    for row in getattr(interaction.message, "components", None) or []:
        for child in getattr(row, "children", None) or []:
            label = getattr(child, "label", None)
            if custom_id is not None and getattr(child, "custom_id", None) == custom_id and isinstance(label, str):
                return label
    return fallback


async def _refuse_wizard_button(
    interaction: discord.Interaction,
    owner_id: str | None,
    label: str,
    reply: str,
) -> None:
    """Turn a press on a signup wizard's button away with *reply*, and record it.

    The line names the button and whose wizard it sits on, "the “Steam” button of Alex's signup
    wizard", and the member who pressed it (`refuse`). *owner_id* is the wizard's driver, None
    where the channel holds no wizard.
    """
    await refuse(
        interaction,
        reply,
        what=f"the “{label}” button of {_wizard_owner(interaction, owner_id)} signup wizard",
    )


async def _not_for_you(
    interaction: discord.Interaction, owner_id: str | None, label: str
) -> None:
    """Refuse a press by somebody who does not own the wizard the button is on."""
    await _refuse_wizard_button(interaction, owner_id, label, "⛔ This button is not for you.")


async def _withdraw(
    interaction: discord.Interaction, queue: ChangeQueue | None, owner_id: str
) -> None:
    """Ask the queue to end the signup of the driver whose Cancel Signup button *interaction*
    pressed (`signup.end`, withdrawn).

    The press is acknowledged privately and the acknowledgement updated with what became of it.
    Where the signup has already ended, the change's check refuses it and the refusal is recorded,
    naming the button and the driver's signup wizard; a withdrawal is never claimed then. *queue*
    is the change queue the view was handed when it was built.
    """
    if queue is None:
        raise RuntimeError("a signup view was built with no change queue to ask")
    await queue.ask(
        "signup.end",
        {"user_id": owner_id, "reason": "withdrawn"},
        interaction=interaction,
        what="Withdrawing a signup",
        refusal_what=(
            f"the “Cancel Signup” button of {_wizard_owner(interaction, owner_id)} signup wizard"
        ),
    )


async def _answer(
    interaction: discord.Interaction,
    queue: ChangeQueue | None,
    owner_id: str,
    label: str,
    button: str,
    value: str | None,
) -> None:
    """Ask the queue to take the answer a wizard button names (`signup.answer`): the press is
    acknowledged privately and the acknowledgement updated with what became of it. The answer is
    judged, by the change's check, when the queue reaches it: a step already answered, a team
    already picked, and a wizard gone are refused then, the line naming the button and the
    driver's signup wizard. *queue* is the change queue the view was handed when it was built;
    *button* is the kind `wizard_answers.judge_button` knows and *value* what the button names."""
    if queue is None:
        raise RuntimeError("a signup view was built with no change queue to ask")
    named = f"the “{label}” button of {_wizard_owner(interaction, owner_id)} signup wizard"
    await queue.ask(
        "signup.answer",
        {
            "user_id": owner_id,
            "channel_id": channel_id_of(interaction),
            "button": button,
            "value": value,
        },
        interaction=interaction,
        what=named,
        refusal_what=named,
    )


#: The states a driver may stand in when they press Sign Up having already got a profile,
#: split by the refusal each earns. Between them they cover every state that is not
#: ``NOT_SIGNED_UP``, which is what lets the callback below use a bare ``else`` for the
#: approved arm: a state belonging to neither set gets a refusal rather than falling
#: through into the wizard and signing somebody up by accident. Add a state — a ban, when
#: the stewarding module brings one — and you must put it in one of these.
#:
#: ``APPROVED_STATES`` is deliberately not branched on: the ``else`` is what reads it, and
#: naming the set here is how that ``else`` says which states it believes it is catching.
#: It is **not** dead code — deleting it as unused takes the cover check in
#: ``tests/core/test_signup_button_driver_states.py`` with it, which is the only thing
#: standing between a new driver state and a silent, wrongly granted signup. That test pins
#: both the cover and the disjointness.
IN_PROGRESS_STATES = {
    DriverState.PENDING_SIGNUP_COMPLETION,
    DriverState.PENDING_ADMIN_APPROVAL,
    DriverState.AWAITING_CORRECTION_PARAMETER,
    DriverState.PENDING_DRIVER_CORRECTION,
}
APPROVED_STATES = {
    DriverState.UNASSIGNED,
    DriverState.ASSIGNED,
}


#: How the log channel names the Sign Up button in a refusal it records.
_SIGN_UP_BUTTON = "the “Sign Up” button"


class SignupButtonView(LeagueView):
    """Persistent signup button view (T016).

    Posted in the signup channel when signups are opened.  The button
    callback is fully implemented in T028 (wizard integration).
    """

    def __init__(self, queue: ChangeQueue) -> None:
        super().__init__(timeout=None)
        self._queue = queue

    @discord.ui.button(
        label="Sign Up",
        style=discord.ButtonStyle.primary,
        custom_id="signup_button",
    )
    async def signup_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """Ask the queue for the driver's signup: every refusal is that change's check."""
        if not interaction.guild:
            return
        await self._queue.ask(
            "signup.start",
            {"user_id": str(interaction.user.id)},
            interaction=interaction,
            what="Signing up",
            refusal_what=_SIGN_UP_BUTTON,
        )


class WithdrawButtonView(LeagueView):
    """Withdrawal button view — visible throughout all in-wizard driver states (T027).

    Posted in the private wizard channel immediately after the channel is
    created.  The driver can press Withdraw at any point while in any
    in-wizard state.
    """

    def __init__(
        self,
        discord_user_id: str | None = None,
        queue: ChangeQueue | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._queue = queue

    @discord.ui.button(
        label="Cancel Signup",
        style=discord.ButtonStyle.danger,
        custom_id="withdraw_button",
    )
    async def withdraw_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        """T041: Full implementation — verify user, call wizard_service.withdraw()."""
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "Cancel Signup")
            return
        await _withdraw(interaction, self._queue, _user_id)


class NoNotesButtonView(LeagueView):
    """Step 9 view — 'No Notes' shortcut alongside Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        queue: ChangeQueue | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._queue = queue

    @discord.ui.button(
        label="No Notes",
        style=discord.ButtonStyle.secondary,
        custom_id="no_notes_button",
    )
    async def no_notes_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "No Notes")
            return
        await _answer(interaction, self._queue, _user_id, "No Notes", "notes", None)

    @discord.ui.button(
        label="Cancel Signup",
        style=discord.ButtonStyle.danger,
        custom_id="no_notes_cancel_button",
    )
    async def cancel_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "Cancel Signup")
            return
        await _withdraw(interaction, self._queue, _user_id)


class PlatformButtonView(LeagueView):
    """Step 2 — one button per platform, plus Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        queue: ChangeQueue | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._queue = queue

    async def _pick(self, interaction: discord.Interaction, platform: str) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, platform)
            return
        await _answer(interaction, self._queue, _user_id, platform, "platform", platform)

    @discord.ui.button(label="Steam", style=discord.ButtonStyle.secondary, custom_id="plat_steam")
    async def steam(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Steam")

    @discord.ui.button(label="EA", style=discord.ButtonStyle.secondary, custom_id="plat_ea")
    async def ea(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "EA")

    @discord.ui.button(label="Xbox", style=discord.ButtonStyle.secondary, custom_id="plat_xbox")
    async def xbox(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Xbox")

    @discord.ui.button(label="PlayStation", style=discord.ButtonStyle.secondary, custom_id="plat_ps")
    async def playstation(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "PlayStation")

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="plat_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "Cancel Signup")
            return
        await _withdraw(interaction, self._queue, _user_id)


class DriverTypeButtonView(LeagueView):
    """Step 5 — Full-Time / Reserve buttons, plus Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        queue: ChangeQueue | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._queue = queue

    async def _pick(self, interaction: discord.Interaction, driver_type: str) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, driver_type)
            return
        await _answer(interaction, self._queue, _user_id, driver_type, "driver_type", driver_type)

    @discord.ui.button(label="Full-Time Driver", style=discord.ButtonStyle.primary, custom_id="dtype_fulltime")
    async def full_time(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Full-Time Driver")

    @discord.ui.button(label="Reserve Driver", style=discord.ButtonStyle.secondary, custom_id="dtype_reserve")
    async def reserve(self, i: discord.Interaction, b: discord.ui.Button) -> None:
        await self._pick(i, "Reserve Driver")

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="dtype_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "Cancel Signup")
            return
        await _withdraw(interaction, self._queue, _user_id)


class PreferredTeamsButtonView(LeagueView):
    """Step 6 — one button per available team, No Preference, and Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        queue: ChangeQueue | None = None,
        team_names: list[str] | None = None,
        excluded: list[str] | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._queue = queue

        if team_names is not None:
            available = [n for n in team_names if n not in (excluded or [])]
            for i, name in enumerate(available):
                btn = CallbackButton(
                    label=name,
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"pteam_{i}",
                    on_press=self._make_team_callback(i),
                )
                self.add_item(btn)
        else:
            # Registration-mode: create stub handlers for all possible team slots
            for i in range(_MAX_TEAM_BUTTONS):
                btn = CallbackButton(
                    label=str(i + 1),
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"pteam_{i}",
                    on_press=self._make_team_callback(i),
                )
                self.add_item(btn)

        no_pref = CallbackButton(
            label="No Preference",
            style=discord.ButtonStyle.secondary,
            custom_id="pteam_nopref",
            on_press=self._no_preference_callback,
        )
        self.add_item(no_pref)

        cancel_btn = CallbackButton(
            label="Cancel Signup",
            style=discord.ButtonStyle.danger,
            custom_id="pteam_cancel",
            on_press=self._cancel_callback,
        )
        self.add_item(cancel_btn)

    def _make_team_callback(self, i: int):
        """Create callback for team button at index i.

        **A team button records the team its own label names** (#482, D3). The pressed button
        is found on the message it sits on by its `custom_id`, and its label is the team, so a
        press on an earlier sub-step's message, where the buttons sit at other positions than
        they would among the teams left, records the team pressed, not another. A view
        re-registered after a restart knows nothing but the `custom_id`, so the message is the
        only place the name is. A button the message does not carry is refused.
        """
        async def callback(interaction: discord.Interaction) -> None:
            _bot, _user_id = await _resolve_view_context(
                interaction, self._discord_user_id
            )
            named = _pressed_label(interaction, "")
            label = named or f"Team {i + 1}"
            if _user_id is None or str(interaction.user.id) != _user_id:
                await _not_for_you(interaction, _user_id, label)
                return
            wizard = await _bot.wizard_service.get_wizard_by_channel(
                interaction.channel_id
            )
            if wizard is None or wizard.config_snapshot is None:
                await _refuse_wizard_button(
                    interaction, _user_id, label, "⛔ Wizard session not found."
                )
                return
            if not named:
                await _refuse_wizard_button(
                    interaction, _user_id, label, "⛔ That option is no longer available."
                )
                return
            await _answer(interaction, self._queue, _user_id, label, "team", named)
        return callback

    async def _no_preference_callback(self, interaction: discord.Interaction) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "No Preference")
            return
        await _answer(interaction, self._queue, _user_id, "No Preference", "team", None)

    async def _cancel_callback(self, interaction: discord.Interaction) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "Cancel Signup")
            return
        await _withdraw(interaction, self._queue, _user_id)


class NoPreferenceTeammateView(LeagueView):
    """Step 7 — No Preference shortcut plus Cancel Signup."""

    def __init__(
        self,
        discord_user_id: str | None = None,
        queue: ChangeQueue | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self._discord_user_id = discord_user_id
        self._queue = queue

    @discord.ui.button(label="No Preference", style=discord.ButtonStyle.secondary, custom_id="tmmate_nopref")
    async def no_preference(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "No Preference")
            return
        await _answer(interaction, self._queue, _user_id, "No Preference", "teammate", None)

    @discord.ui.button(label="Cancel Signup", style=discord.ButtonStyle.danger, custom_id="tmmate_cancel")
    async def cancel(self, interaction: discord.Interaction, b: discord.ui.Button) -> None:
        _bot, _user_id = await _resolve_view_context(
            interaction, self._discord_user_id
        )
        if _user_id is None or str(interaction.user.id) != _user_id:
            await _not_for_you(interaction, _user_id, "Cancel Signup")
            return
        await _withdraw(interaction, self._queue, _user_id)
