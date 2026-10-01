"""`CorrectionParameterView` — the nine buttons that choose which answer to send back.

Issue #208, completing `tests/signup/test_admin_review_panel.py`. When a manager presses **Request
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

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import leaguebot.signup.cogs.admin_review_cog as arc
from leaguebot.signup.cogs.admin_review_cog import CorrectionParameterView

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


def _league_server():
    """The league's server, on which the driver is Alex (id 4242)."""
    guild = MagicMock()
    guild.id = SERVER_ID
    guild.get_member = MagicMock(
        side_effect=lambda uid: SimpleNamespace(id=int(uid), display_name="Alex", mention=f"<@{uid}>")
        if str(uid) == DRIVER_ID else None
    )
    return guild


def _bot(*, wizard_user: str | None = DRIVER_ID):
    bot = MagicMock()
    bot.wizard_service = MagicMock()
    bot.wizard_service.select_correction_parameter = AsyncMock(return_value=None)
    bot.wizard_service.get_wizard_by_channel = AsyncMock(
        return_value=SimpleNamespace(discord_user_id=wizard_user) if wizard_user else None
    )
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.get_guild = MagicMock(return_value=_league_server())
    bot.output_router.post_log = AsyncMock()
    return bot


def _interaction(bot):
    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.channel_id = CHANNEL_ID
    interaction.guild = bot.get_guild.return_value
    interaction.user = MagicMock()
    interaction.user.id = MANAGER_ID
    interaction.user.display_name = "Manager"
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    # Answered once the interaction has been replied to or deferred, as Discord's is.
    interaction.response.is_done = lambda: bool(
        interaction.response.send_message.await_count + interaction.response.defer.await_count
    )
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _lines(bot) -> list[str]:
    """Every line written to the league's log channel."""
    return [str(call.args[0]) for call in bot.output_router.post_log.await_args_list]


def _assert_refusal_recorded(bot, label: str, reason: str) -> None:
    """One line in the log channel, naming the button, the member who pressed it, and why, and
    no failure line (core specification, "The record of what changed")."""
    lines = _lines(bot)
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith(f"⛔ the “{label}” button"), line
    assert "signup review" in line
    assert f"refused for Manager (<@{MANAGER_ID}>)" in line
    assert line.endswith(f"— {reason}"), line


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
    _assert_refusal_recorded(bot, "Nationality", "Insufficient permissions.")
    bot.wizard_service.select_correction_parameter.assert_not_awaited()


_ENDED = "This correction request has ended. Nothing was changed."


async def test_a_choice_pressed_after_the_request_ended_is_refused_and_recorded(monkeypatch):
    """A manager presses "Platform" on Alex's correction panel after the request has ended: its
    five minutes lapsed, another parameter was already chosen, or the signup was approved or
    rejected. The service refuses and says why; the button answers "⛔ This correction request
    has ended. Nothing was changed.", writes one refusal line and no failure line, and never
    says it is re-collecting anything (D2)."""
    _permitted(monkeypatch, True)
    bot = _bot()
    bot.wizard_service.select_correction_parameter = AsyncMock(return_value=_ENDED)
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, "Platform").callback(interaction)

    replied = _replied(interaction)
    assert f"⛔ {_ENDED}" in replied, replied
    assert "✅" not in replied, replied
    _assert_refusal_recorded(bot, "Platform", _ENDED)


async def test_a_choice_that_loses_the_race_to_the_request_ending_is_refused(monkeypatch):
    """A manager presses "Platform" on Alex's correction panel at the moment the request ends:
    the five minutes lapse, or a second manager's choice lands first, just after Alex has been
    read as still awaiting a correction parameter, so the state change that follows is refused.
    Through the real service, the button answers "⛔ This correction request has ended. Nothing
    was changed.", writes one refusal line and no failure line, and Alex's signup is left as it
    was: no wizard saved, no deadline armed and no prompt posted."""
    from leaguebot.core.models.driver_profile import DriverState
    from leaguebot.signup.services.wizard_service import WizardService

    _permitted(monkeypatch, True)
    bot = _bot()
    svc = WizardService.__new__(WizardService)
    svc._correction_tasks = {}
    svc._bot = bot
    svc._arm_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    bot.wizard_service = svc
    bot.driver_service.get_profile = AsyncMock(
        return_value=SimpleNamespace(current_state=DriverState.AWAITING_CORRECTION_PARAMETER)
    )
    bot.driver_service.transition = AsyncMock(
        side_effect=ValueError("Illegal transition: PENDING_ADMIN_APPROVAL -> PENDING_DRIVER_CORRECTION")
    )
    bot.signup_module_service.get_wizard = AsyncMock(
        return_value=SimpleNamespace(discord_user_id=DRIVER_ID, signup_channel_id=CHANNEL_ID)
    )
    bot.signup_module_service.save_wizard = AsyncMock()
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, "Platform").callback(interaction)

    replied = _replied(interaction)
    assert f"⛔ {_ENDED}" in replied, replied
    assert "✅" not in replied, replied
    _assert_refusal_recorded(bot, "Platform", _ENDED)
    bot.driver_service.transition.assert_awaited_once()
    bot.signup_module_service.save_wizard.assert_not_awaited()
    svc._arm_inactivity_job.assert_not_awaited()
    interaction.guild.get_channel.assert_not_called()


async def test_a_choice_writes_one_line_naming_the_parameter(monkeypatch):
    """Manager presses "Platform" on Alex's correction panel while the request is still open.
    Alex is sent back to the platform question, and one line records the choice, naming the
    manager, the button and whose signup review it sits on."""
    _permitted(monkeypatch, True)
    bot = _bot()
    view = CorrectionParameterView(DRIVER_ID, bot)
    interaction = _interaction(bot)

    await _button(view, "Platform").callback(interaction)

    assert "✅ Re-collecting **platform**." in _replied(interaction)
    assert _lines(bot) == [
        f"Manager (<@{MANAGER_ID}>) | the “Platform” button of Alex's signup review "
        "| Correction requested: platform"
    ]


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
    _assert_refusal_recorded(bot, "Platform", "Could not identify driver for this correction.")
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
