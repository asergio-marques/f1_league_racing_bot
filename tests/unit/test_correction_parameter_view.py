"""`CorrectionParameterView` — the nine buttons that choose which answer to send back.

Issue #208, completing `tests/unit/test_admin_review_panel.py`. When a manager presses **Request
Changes** and types a reason, this panel is what they see next: one button per question the
wizard asks, so the driver is sent back to exactly one of them.

**The nine buttons are built in a loop, and every one gets its own callback.** That is the
arrangement where a closure bug bites: a loop variable captured by reference rather than by
value would give all nine buttons the *last* parameter, so pressing "Nationality" would ask the
driver to re-enter their notes. The loop passes the key into `make_callback` for exactly that
reason, and `test_each_button_sends_back_its_own_parameter` drives all nine to hold it.

**The permission check is repeated here.** The panel lands in the driver's own channel, as the
approval panel does, so a driver could otherwise choose which of their own answers to re-open —
and re-opening one lets them change it. This view is reached only after a manager has already
pressed a button on the approval panel, which makes it easy to assume the check has been done;
it has not, because a different person can press this one.

**A rebuilt panel finds its driver by channel**, the same recovery path the approval panel uses,
and refuses when the lookup finds nothing rather than acting on `None`.

Its class docstring still says "Restricted to tier-2 role or Manage Guild permission". The
Manage Guild half is stale — `_may_review_signup` stopped admitting it when the two-tier model
landed, and its own docstring records that. These tests hold the behaviour, which is the
two-tier check.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import cogs.admin_review_cog as arc  # noqa: E402
from cogs.admin_review_cog import CorrectionParameterView  # noqa: E402

SERVER_ID = 10608
DRIVER_ID = "4242"
CHANNEL_ID = 770901
MANAGER_ID = 77

#: Every question the wizard asks, as label and stored key.
PARAMETERS = [
    ("Nationality", "nationality"),
    ("Platform", "platform"),
    ("Platform ID", "platform_id"),
    ("Availability", "availability"),
    ("Driver Type", "driver_type"),
    ("Preferred Teams", "preferred_teams"),
    ("Preferred Teammate", "preferred_teammate"),
    ("Lap Times", "lap_times"),
    ("Notes", "notes"),
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _bot(*, wizard_user: str | None = DRIVER_ID):
    bot = MagicMock()
    bot.wizard_service = MagicMock()
    bot.wizard_service.select_correction_parameter = AsyncMock(return_value=None)
    bot.wizard_service.get_wizard_by_channel = AsyncMock(
        return_value=SimpleNamespace(discord_user_id=wizard_user) if wizard_user else None
    )
    return bot


def _interaction(bot):
    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.channel_id = CHANNEL_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = MANAGER_ID
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
    monkeypatch.setattr(arc, "_may_review_signup", AsyncMock(return_value=allowed))


def _button(view, label: str):
    return next(c for c in view.children if getattr(c, "label", None) == label)


# ---------------------------------------------------------------------------
# The panel itself
# ---------------------------------------------------------------------------


async def test_a_button_is_offered_for_every_question_the_wizard_asks():
    """A question with no button could never be corrected, and the manager would have to
    reject the whole signup to get it changed."""
    view = CorrectionParameterView(DRIVER_ID, _bot())

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    for label, _ in PARAMETERS:
        assert label in labels


async def test_each_button_carries_its_own_custom_id():
    """Persistent views are matched by custom id, so two buttons sharing one would make
    which parameter a press means depend on the order Discord matched them."""
    view = CorrectionParameterView(DRIVER_ID, _bot())

    ids = [c.custom_id for c in view.children if getattr(c, "custom_id", None)]
    assert len(ids) == len(set(ids))
    assert "correct_nationality" in ids


# ---------------------------------------------------------------------------
# The closure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label,key", PARAMETERS, ids=[k for _, k in PARAMETERS])
async def test_each_button_sends_back_its_own_parameter(monkeypatch, label, key):
    """The nine callbacks are built in a loop. A loop variable captured by reference would
    give every button the *last* parameter, so pressing "Nationality" would ask the driver
    to re-enter their notes — and nothing else in the suite would notice."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, label).callback(interaction)

    bot.wizard_service.select_correction_parameter.assert_awaited_once()
    assert bot.wizard_service.select_correction_parameter.await_args.args[1] == key


@pytest.mark.parametrize("label,key", PARAMETERS, ids=[k for _, k in PARAMETERS])
async def test_the_driver_is_told_which_answer_to_give_again(monkeypatch, label, key):
    """Named in the league's words rather than the stored key — "platform id", not
    `platform_id`."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, label).callback(interaction)

    assert key.replace("_", " ") in _replied(interaction)


# ---------------------------------------------------------------------------
# Who may press these
# ---------------------------------------------------------------------------


async def test_a_driver_cannot_choose_which_of_their_own_answers_to_re_open(monkeypatch):
    """The panel lands in the driver's own channel, and re-opening an answer lets them
    change it. The check is easy to assume has already happened — a manager pressed a
    button to get here — but a different person can press this one."""
    _permitted(monkeypatch, False)
    bot = _bot()
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, "Nationality").callback(interaction)

    assert "Insufficient permissions" in _replied(interaction)
    bot.wizard_service.select_correction_parameter.assert_not_awaited()


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


async def test_a_panel_rebuilt_after_a_restart_finds_its_driver(monkeypatch):
    """The five-minute window can outlive a restart, so the panel has to work without the
    driver it was built with."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = CorrectionParameterView()
    interaction = _interaction(bot)

    await _button(view, "Platform").callback(interaction)

    bot.wizard_service.get_wizard_by_channel.assert_awaited_once()
    assert bot.wizard_service.select_correction_parameter.await_args.args[0] == DRIVER_ID


async def test_a_panel_whose_channel_has_no_wizard_refuses(monkeypatch):
    """Acting on `None` would reach the service with no driver and fail somewhere the
    manager could not interpret."""
    _permitted(monkeypatch, True)
    bot = _bot(wizard_user=None)
    view = CorrectionParameterView()
    interaction = _interaction(bot)

    await _button(view, "Platform").callback(interaction)

    assert "Could not identify driver" in _replied(interaction)
    bot.wizard_service.select_correction_parameter.assert_not_awaited()


async def test_a_stored_driver_is_preferred_over_the_lookup(monkeypatch):
    """While the process that posted the panel is still alive there is no need to ask the
    database, and asking would make the panel depend on a wizard row that a withdrawal may
    already have removed."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, "Notes").callback(interaction)

    bot.wizard_service.get_wizard_by_channel.assert_not_awaited()
