"""The admin review panel — approving, rejecting and sending back a signup by button.

Issue #208. `admin_review_cog.py` was at 40.0%. The panel is posted into the driver's **own**
signup channel, which the driver can read, so the permission check on these three buttons is
the only thing standing between a driver and approving their own signup. The cog's own docstring
says as much: "It is not a formality."

**The permission check is tested first and hardest.** It used to admit Discord's Manage Guild
permission — a level the two-tier model has no room for — and read the interaction role by
hand; both are now one question asked of `is_league_manager`, so the buttons and the commands
agree by construction rather than by two implementations happening to match.

**The race guard is the second half of the same job.** Three managers can be looking at one
panel, and the buttons never disappear — so each press re-reads the driver's state and refuses
unless they are still `PENDING_ADMIN_APPROVAL`. Without it, two managers pressing Approve and
Reject a second apart would both succeed, and which one the driver ends up in would depend on
the order the callbacks happened to finish.

**Reject and Request Changes collect a reason out of band.** The button cannot open a modal
because the panel is persistent and may outlive a restart, so it parks an entry in
`_PENDING_REASONS` and the cog's `on_message` listener picks up the manager's next message in
that channel. That message is deleted afterwards: it is a private judgement about a driver,
typed into a channel the driver can read.

**An empty reason becomes a stated one.** `"No specific reason given."` is deliberate — the
driver is told something rather than being shown an empty **Reason:** that reads as a bug.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

import leaguebot.signup.cogs.admin_review_cog as arc
from leaguebot.signup.cogs.admin_review_cog import (
    AdminReviewCog,
    AdminReviewView,
    _PENDING_REASONS,
)
from leaguebot.core.models.driver_profile import DriverState

SERVER_ID = 10308
DRIVER_ID = "4242"
CHANNEL_ID = 770701
MANAGER_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_pending():
    """`_PENDING_REASONS` is module-level state; a leak between tests would make one
    test's parked reason answer another's message."""
    _PENDING_REASONS.clear()
    yield
    _PENDING_REASONS.clear()


def _bot(*, state=DriverState.PENDING_ADMIN_APPROVAL, wizard_user: str | None = DRIVER_ID):
    bot = MagicMock()
    bot.driver_service = MagicMock()
    bot.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=state) if state else None
    )
    bot.wizard_service = MagicMock()
    bot.wizard_service.approve_signup = AsyncMock(return_value=None)
    bot.wizard_service.reject_signup = AsyncMock(return_value=None)
    bot.wizard_service.request_changes = AsyncMock(return_value=None)
    bot.wizard_service.get_wizard_by_channel = AsyncMock(
        return_value=SimpleNamespace(discord_user_id=wizard_user) if wizard_user else None
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    return bot


def _interaction(bot, user_id: int = MANAGER_ID):
    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.channel_id = CHANNEL_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _permitted(monkeypatch, allowed: bool):
    monkeypatch.setattr(
        arc, "_may_review_signup", AsyncMock(return_value=allowed)
    )


BUTTONS = ["approve_button", "request_changes_button", "reject_button"]


# ---------------------------------------------------------------------------
# Who may press these
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("button", BUTTONS, ids=["approve", "request-changes", "reject"])
async def test_a_driver_cannot_action_their_own_signup(monkeypatch, button):
    """The panel sits in the driver's own channel, which they can read. This check is the
    only thing between them and approving themselves."""
    _permitted(monkeypatch, False)
    bot = _bot()
    view = AdminReviewView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert "Insufficient permissions" in _replied(interaction)
    bot.wizard_service.approve_signup.assert_not_awaited()
    bot.wizard_service.reject_signup.assert_not_awaited()
    assert _PENDING_REASONS == {}


@pytest.mark.parametrize("button", BUTTONS, ids=["approve", "request-changes", "reject"])
async def test_a_signup_already_actioned_is_refused(monkeypatch, button):
    """Three managers can be looking at one panel and the buttons never disappear. Without
    this, Approve and Reject a second apart would both succeed and the outcome would turn
    on which callback finished first."""
    _permitted(monkeypatch, True)
    bot = _bot(state=DriverState.UNASSIGNED)
    view = AdminReviewView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert "already been actioned" in _replied(interaction)
    bot.wizard_service.approve_signup.assert_not_awaited()


@pytest.mark.parametrize("button", BUTTONS, ids=["approve", "request-changes", "reject"])
async def test_a_driver_whose_profile_has_gone_is_refused(monkeypatch, button):
    """They withdrew, or left the server, between the panel being posted and pressed."""
    _permitted(monkeypatch, True)
    bot = _bot(state=None)
    view = AdminReviewView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert "already been actioned" in _replied(interaction)


async def test_a_panel_whose_channel_has_no_wizard_cannot_identify_the_driver(monkeypatch):
    """A panel rebuilt after a restart carries no stored driver and finds one by channel.
    Finding none must refuse rather than act on `None`."""
    _permitted(monkeypatch, True)
    bot = _bot(wizard_user=None)
    view = AdminReviewView()
    interaction = _interaction(bot)

    await type(view).approve_button(view, interaction, MagicMock())

    assert "Could not identify driver" in _replied(interaction)


async def test_a_panel_rebuilt_after_a_restart_finds_its_driver(monkeypatch):
    _permitted(monkeypatch, True)
    bot = _bot()
    view = AdminReviewView()
    interaction = _interaction(bot)

    await type(view).approve_button(view, interaction, MagicMock())

    bot.wizard_service.get_wizard_by_channel.assert_awaited_once()
    bot.wizard_service.approve_signup.assert_awaited_once()


# ---------------------------------------------------------------------------
# Approve
# ---------------------------------------------------------------------------


async def test_approving_approves_the_signup(monkeypatch):
    _permitted(monkeypatch, True)
    bot = _bot()
    view = AdminReviewView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await type(view).approve_button(view, interaction, MagicMock())

    bot.wizard_service.approve_signup.assert_awaited_once()
    args = bot.wizard_service.approve_signup.await_args.args
    assert args[0] == DRIVER_ID
    assert "approved" in _replied(interaction)


async def test_approving_needs_no_reason(monkeypatch):
    """Only the two negative outcomes collect one — a driver told why they were approved
    would be an odd thing to read."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = AdminReviewView(DRIVER_ID, bot)

    await type(view).approve_button(view, _interaction(bot), MagicMock())

    assert _PENDING_REASONS == {}


# ---------------------------------------------------------------------------
# Reject and Request Changes — the parked reason
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "button,action",
    [("request_changes_button", "request_changes"), ("reject_button", "reject")],
    ids=["request-changes", "reject"],
)
async def test_the_negative_buttons_ask_for_a_reason_first(monkeypatch, button, action):
    """The panel is persistent and may outlive a restart, so it cannot open a modal — it
    parks an entry and the listener picks up the manager's next message."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = AdminReviewView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert _PENDING_REASONS[(CHANNEL_ID, MANAGER_ID)]["action"] == action
    assert "type the reason" in _replied(interaction)
    bot.wizard_service.reject_signup.assert_not_awaited()
    bot.wizard_service.request_changes.assert_not_awaited()


async def test_the_parked_entry_is_keyed_to_the_manager_who_pressed(monkeypatch):
    """Two managers in one channel must not answer each other's prompt."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = AdminReviewView(DRIVER_ID, bot)

    await type(view).reject_button(view, _interaction(bot, user_id=MANAGER_ID), MagicMock())
    await type(view).reject_button(view, _interaction(bot, user_id=MANAGER_ID + 1), MagicMock())

    assert (CHANNEL_ID, MANAGER_ID) in _PENDING_REASONS
    assert (CHANNEL_ID, MANAGER_ID + 1) in _PENDING_REASONS


# ---------------------------------------------------------------------------
# The listener that collects it
# ---------------------------------------------------------------------------


def _message(content: str, *, author_id: int = MANAGER_ID, is_bot: bool = False):
    message = MagicMock()
    message.content = content
    message.author = MagicMock()
    message.author.id = author_id
    message.author.bot = is_bot
    message.guild = MagicMock()
    message.guild.id = SERVER_ID
    message.channel = MagicMock()
    message.channel.id = CHANNEL_ID
    message.delete = AsyncMock(return_value=None)
    return message


def _cog_with_pending(action: str):
    bot = _bot()
    followup = MagicMock()
    followup.send = AsyncMock(return_value=None)
    _PENDING_REASONS[(CHANNEL_ID, MANAGER_ID)] = {
        "action": action,
        "discord_user_id": DRIVER_ID,
        "actor": MagicMock(),
        "guild": MagicMock(),
        "followup": followup,
    }
    cog = AdminReviewCog(bot)
    return cog, followup


async def test_a_rejection_reason_reaches_the_driver():
    cog, followup = _cog_with_pending("reject")

    await cog.on_message(_message("Lap time could not be verified."))

    cog.bot.wizard_service.reject_signup.assert_awaited_once()
    assert (
        cog.bot.wizard_service.reject_signup.await_args.kwargs["reason"]
        == "Lap time could not be verified."
    )
    followup.send.assert_awaited_once()


async def test_a_correction_reason_reaches_the_driver():
    cog, followup = _cog_with_pending("request_changes")

    await cog.on_message(_message("Your platform ID looks wrong."))

    assert (
        cog.bot.wizard_service.request_changes.await_args.kwargs["reason"]
        == "Your platform ID looks wrong."
    )


async def test_an_empty_reason_becomes_a_stated_one():
    """The driver is told something rather than shown an empty **Reason:** that reads as
    a bug."""
    cog, _ = _cog_with_pending("reject")

    await cog.on_message(_message("   "))

    assert (
        cog.bot.wizard_service.reject_signup.await_args.kwargs["reason"]
        == "No specific reason given."
    )


async def test_the_reason_message_is_deleted():
    """It is a private judgement about a driver, typed into a channel the driver reads."""
    cog, _ = _cog_with_pending("reject")
    message = _message("Not fast enough.")

    await cog.on_message(message)

    message.delete.assert_awaited_once()


async def test_a_reason_that_cannot_be_deleted_still_counts():
    """The action matters more than the tidying; refusing to act because the message could
    not be deleted would leave the signup pending forever."""
    cog, _ = _cog_with_pending("reject")
    message = _message("Not fast enough.")
    message.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "no perms"))

    await cog.on_message(message)

    cog.bot.wizard_service.reject_signup.assert_awaited_once()


async def test_the_parked_entry_is_consumed():
    """Popped, not read. Left behind, the manager's next message in that channel would
    reject the driver a second time."""
    cog, _ = _cog_with_pending("reject")

    await cog.on_message(_message("Reason."))

    assert _PENDING_REASONS == {}


async def test_a_message_with_nothing_parked_is_ignored():
    """Ordinary conversation in a signup channel must not be swallowed."""
    bot = _bot()
    cog = AdminReviewCog(bot)

    await cog.on_message(_message("just chatting"))

    bot.wizard_service.reject_signup.assert_not_awaited()


async def test_the_bot_s_own_messages_are_ignored():
    """The bot posts into this channel constantly, and one of its own messages answering
    a prompt would reject a driver with the bot's text as the reason."""
    cog, _ = _cog_with_pending("reject")

    await cog.on_message(_message("Welcome to the signup wizard!", is_bot=True))

    cog.bot.wizard_service.reject_signup.assert_not_awaited()
    assert _PENDING_REASONS != {}


async def test_another_member_s_message_does_not_answer_the_prompt():
    """The entry is keyed to the manager, so the driver typing in their own channel while
    a manager is composing a reason must not supply it."""
    cog, _ = _cog_with_pending("reject")

    await cog.on_message(_message("wait, why?", author_id=MANAGER_ID + 1))

    cog.bot.wizard_service.reject_signup.assert_not_awaited()
    assert _PENDING_REASONS != {}
