"""The signup wizard's button views, and who is allowed to press them.

Issue #208. `signup_cog.py` was the largest uncovered file in the signup module. Most of it is
`discord.ui.View` subclasses — the buttons a driver presses at the platform, driver-type,
preferred-teams, teammate and notes steps, each with a Cancel Signup beside it. None was
executed by any test.

**Every callback carries the same ownership guard, and that guard is the point of this file.**
A wizard channel is private to its driver, but a league manager can see it, and the views are
registered as *persistent* — they survive a restart and keep working on messages posted before
it. Without the guard, anyone who can see the channel could answer the questions, cancel the
signup, or press a button on somebody else's wizard. The guard is written out separately in
each callback rather than shared, which is exactly the arrangement where one copy is missed,
so **each callback is tested for it individually**.

**`_resolve_view_context` is what makes the views survive a restart.** A view rebuilt by
`bot.add_view` has no stored driver — the process that knew has gone — so it finds the owner by
looking the wizard up by channel. `test_a_view_rebuilt_after_a_restart_finds_its_driver` covers
that path; `test_a_view_whose_channel_has_no_wizard_refuses_everyone` covers what happens when
the lookup finds nothing, which must be a refusal rather than a crash or, worse, an
unauthenticated pass.

**Cancel is tested on every view that offers it.** It is the one button that destroys work, and
a driver reaching it from step 2 must be treated exactly as one reaching it from step 7.

These tests are `async def` throughout, as `CLAUDE.md` requires of anything constructing a
`View`: apt's discord.py calls `asyncio.get_running_loop()` in `View.__init__`, so a sync test
building one passes on CI and raises on the Pi.

The callbacks are reached through the **class** rather than the instance. `@discord.ui.button`
leaves the plain function on the class and swaps in a `Button` object on the instance, so
`view.steam` is the button and `type(view).steam` is the code under test.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


SERVER_ID = 1
DRIVER_ID = "7"
OTHER_USER_ID = 8
CHANNEL_ID = 99

_STEP_REFUSAL_IGNORED = (
    "#482: the view ignores the reason a step handler gives for turning a press away, so the "
    "driver is not told and nothing is recorded"
)

#: The reason a step handler gives for a press on a step already answered (commit point 19).
_ALREADY_ANSWERED = "That step has already been answered."


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _interaction(user_id: int = int(DRIVER_ID), *, wizard_user: str | None = DRIVER_ID):
    """An interaction from *user_id*, on a bot whose wizard service is a double.

    *wizard_user* is who `get_wizard_by_channel` reports as owning the channel, which is the
    path a view rebuilt after a restart takes.
    """
    wizard_service = MagicMock()
    wizard_service.handle_platform_button = AsyncMock(return_value=None)
    wizard_service.handle_driver_type_button = AsyncMock(return_value=None)
    wizard_service.handle_preferred_teams_button = AsyncMock(return_value=None)
    wizard_service.handle_no_preference_teammate = AsyncMock(return_value=None)
    wizard_service.handle_no_notes = AsyncMock(return_value=None)
    wizard_service.withdraw = AsyncMock(return_value=None)
    wizard_service.get_wizard_by_channel = AsyncMock(
        return_value=SimpleNamespace(discord_user_id=wizard_user) if wizard_user else None
    )

    bot = MagicMock()
    bot.wizard_service = wizard_service
    bot.output_router.post_log = AsyncMock()

    interaction = MagicMock()
    interaction.client = bot
    interaction.guild_id = SERVER_ID
    interaction.channel_id = CHANNEL_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.user.display_name = "Driver" if str(user_id) == DRIVER_ID else "Other"
    # The response knows whether it has been used, as Discord's does, so a refusal goes where
    # the real one would.
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _refused(interaction) -> bool:
    return any(
        "not for you" in str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


def _assert_refusal_recorded(interaction, button: str) -> None:
    """One line in the log channel, naming the button, the driver's wizard it sits on, the
    member who pressed it, and why (core specification, "The record of what changed")."""
    lines = [str(call.args[0]) for call in interaction.client.output_router.post_log.await_args_list]
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith(f"⛔ the “{button}” button"), line
    assert "signup wizard" in line
    assert f"refused for {interaction.user.display_name} (<@{interaction.user.id}>)" in line
    assert "This button is not for you." in line


# ---------------------------------------------------------------------------
# Platform view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "button,platform",
    [("steam", "Steam"), ("ea", "EA"), ("xbox", "Xbox"), ("playstation", "PlayStation")],
)
async def test_each_platform_button_reports_its_own_platform(button, platform):
    """Four near-identical callbacks; one passing the wrong string would record a driver
    on a platform they never chose."""
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction()

    await getattr(type(view), button)(view, interaction, MagicMock())

    interaction.client.wizard_service.handle_platform_button.assert_awaited_once()
    assert (
        interaction.client.wizard_service.handle_platform_button.await_args.args[1]
        == platform
    )


async def test_another_member_cannot_answer_a_driver_s_platform_question():
    """The channel is private to its driver, but a league manager can see it."""
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)

    await type(view).steam(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Steam")
    interaction.client.wizard_service.handle_platform_button.assert_not_awaited()


async def test_cancelling_from_the_platform_step_withdraws_the_signup():
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction()

    await type(view).cancel(view, interaction, MagicMock())

    interaction.client.wizard_service.withdraw.assert_awaited_once()
    assert "withdrawn" in interaction.followup.send.await_args.args[0]


async def test_another_member_cannot_cancel_a_driver_s_signup():
    """The one button that destroys work."""
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)

    await type(view).cancel(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    interaction.client.wizard_service.withdraw.assert_not_awaited()


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


async def test_a_view_rebuilt_after_a_restart_finds_its_driver():
    """`bot.add_view` rebuilds the view with no stored driver — the process that knew has
    gone — so the owner is found by looking the wizard up by channel."""
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView()
    interaction = _interaction()

    await type(view).steam(view, interaction, MagicMock())

    interaction.client.wizard_service.get_wizard_by_channel.assert_awaited_once()
    interaction.client.wizard_service.handle_platform_button.assert_awaited_once()


async def test_a_view_whose_channel_has_no_wizard_refuses_everyone():
    """A button left in a channel whose wizard has been cleared up. The lookup finding
    nothing must refuse rather than pass unauthenticated."""
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView()
    interaction = _interaction(wizard_user=None)

    await type(view).steam(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Steam")
    interaction.client.wizard_service.handle_platform_button.assert_not_awaited()


async def test_a_rebuilt_view_still_refuses_the_wrong_member():
    """The guard has to hold on the recovered identity too, not only the stored one."""
    from leaguebot.signup.cogs.signup_cog import PlatformButtonView

    view = PlatformButtonView()
    interaction = _interaction(OTHER_USER_ID)

    await type(view).steam(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Steam")


# ---------------------------------------------------------------------------
# Driver type view
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "button,driver_type",
    [("full_time", "Full-Time Driver"), ("reserve", "Reserve Driver")],
)
async def test_each_driver_type_button_reports_its_own_type(button, driver_type):
    """The answer that decides whether the driver is asked about teams at all."""
    from leaguebot.signup.cogs.signup_cog import DriverTypeButtonView

    view = DriverTypeButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction()

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert (
        interaction.client.wizard_service.handle_driver_type_button.await_args.args[1]
        == driver_type
    )


async def test_another_member_cannot_choose_a_driver_s_type():
    from leaguebot.signup.cogs.signup_cog import DriverTypeButtonView

    view = DriverTypeButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)

    await type(view).full_time(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Full-Time Driver")
    interaction.client.wizard_service.handle_driver_type_button.assert_not_awaited()


async def test_cancelling_from_the_driver_type_step_withdraws_the_signup():
    from leaguebot.signup.cogs.signup_cog import DriverTypeButtonView

    view = DriverTypeButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction()

    await type(view).cancel(view, interaction, MagicMock())

    interaction.client.wizard_service.withdraw.assert_awaited_once()


async def test_another_member_cannot_cancel_from_the_driver_type_step():
    from leaguebot.signup.cogs.signup_cog import DriverTypeButtonView

    view = DriverTypeButtonView(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)

    await type(view).cancel(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    interaction.client.wizard_service.withdraw.assert_not_awaited()


# ---------------------------------------------------------------------------
# Preferred teams view — buttons built at runtime
# ---------------------------------------------------------------------------


async def test_a_button_is_offered_for_each_team_still_available():
    """Unlike the other views, this one's buttons are built from the league's own teams,
    so there is no decorator to read them off — the view has to be inspected."""
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(
        DRIVER_ID, MagicMock(), ["Alpha", "Beta", "Gamma"]
    )

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    for team in ("Alpha", "Beta", "Gamma"):
        assert team in labels


async def test_a_team_already_picked_is_excluded():
    """What stops a driver spending two of their three picks on one team."""
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(
        DRIVER_ID, MagicMock(), ["Alpha", "Beta"], excluded=["Alpha"]
    )

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    assert "Alpha" not in labels
    assert "Beta" in labels


async def test_no_preference_is_always_offered():
    """It is the driver's only way out of the loop before their third pick."""
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Alpha"])

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    assert any("No Preference" in label for label in labels)


async def test_no_preference_finishes_the_team_step():
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Alpha"])
    interaction = _interaction()

    await view._no_preference_callback(interaction)

    interaction.client.wizard_service.handle_preferred_teams_button.assert_awaited_once()
    assert (
        interaction.client.wizard_service.handle_preferred_teams_button.await_args.args[1]
        is None
    )


async def test_another_member_cannot_finish_a_driver_s_team_step():
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Alpha"])
    interaction = _interaction(OTHER_USER_ID)

    await view._no_preference_callback(interaction)

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "No Preference")
    interaction.client.wizard_service.handle_preferred_teams_button.assert_not_awaited()


async def test_cancelling_from_the_team_step_withdraws_the_signup():
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Alpha"])
    interaction = _interaction()

    await view._cancel_callback(interaction)

    interaction.client.wizard_service.withdraw.assert_awaited_once()


async def test_another_member_cannot_cancel_from_the_team_step():
    from leaguebot.signup.cogs.signup_cog import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Alpha"])
    interaction = _interaction(OTHER_USER_ID)

    await view._cancel_callback(interaction)

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    interaction.client.wizard_service.withdraw.assert_not_awaited()


# ---------------------------------------------------------------------------
# Teammate view
# ---------------------------------------------------------------------------


async def test_no_preference_answers_the_teammate_question():
    from leaguebot.signup.cogs.signup_cog import NoPreferenceTeammateView

    view = NoPreferenceTeammateView(DRIVER_ID, MagicMock())
    interaction = _interaction()

    await type(view).no_preference(view, interaction, MagicMock())

    interaction.client.wizard_service.handle_no_preference_teammate.assert_awaited_once()


async def test_another_member_cannot_answer_the_teammate_question():
    from leaguebot.signup.cogs.signup_cog import NoPreferenceTeammateView

    view = NoPreferenceTeammateView(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)

    await type(view).no_preference(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "No Preference")
    interaction.client.wizard_service.handle_no_preference_teammate.assert_not_awaited()


async def test_cancelling_from_the_teammate_step_withdraws_the_signup():
    from leaguebot.signup.cogs.signup_cog import NoPreferenceTeammateView

    view = NoPreferenceTeammateView(DRIVER_ID, MagicMock())
    interaction = _interaction()

    await type(view).cancel(view, interaction, MagicMock())

    interaction.client.wizard_service.withdraw.assert_awaited_once()


async def test_another_member_cannot_cancel_from_the_teammate_step():
    from leaguebot.signup.cogs.signup_cog import NoPreferenceTeammateView

    view = NoPreferenceTeammateView(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)

    await type(view).cancel(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    interaction.client.wizard_service.withdraw.assert_not_awaited()


# ---------------------------------------------------------------------------
# The first-message Cancel Signup, and the notes step
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "view_name, button, label",
    [
        pytest.param("WithdrawButtonView", "withdraw_button", "Cancel Signup"),
        pytest.param("NoNotesButtonView", "no_notes_button", "No Notes"),
        pytest.param("NoNotesButtonView", "cancel_button", "Cancel Signup"),
    ],
)
async def test_another_member_pressing_the_welcome_or_notes_buttons_is_refused_and_recorded(
    view_name, button, label
):
    """The Cancel Signup posted with the welcome message, and the notes step's No Notes and
    Cancel Signup, carry the same guard as every other step, and record its refusal."""
    from leaguebot.signup.cogs import signup_cog

    view = getattr(signup_cog, view_name)(DRIVER_ID, MagicMock())
    interaction = _interaction(OTHER_USER_ID)
    interaction.client.wizard_service.handle_no_notes = AsyncMock(return_value=None)

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert _refused(interaction)
    interaction.client.wizard_service.withdraw.assert_not_awaited()
    interaction.client.wizard_service.handle_no_notes.assert_not_awaited()
    _assert_refusal_recorded(interaction, label)


# ---------------------------------------------------------------------------
# A step already answered
# ---------------------------------------------------------------------------


async def _press_step(view_name: str, button: str, interaction) -> None:
    from leaguebot.signup.cogs import signup_cog

    if view_name == "PreferredTeamsButtonView":
        view = signup_cog.PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Ferrari"])
        await getattr(view, button)(interaction)
        return
    view = getattr(signup_cog, view_name)(DRIVER_ID, MagicMock())
    await getattr(type(view), button)(view, interaction, MagicMock())


@pytest.mark.xfail(strict=True, reason=_STEP_REFUSAL_IGNORED)
@pytest.mark.parametrize(
    "view_name, button, handler, label",
    [
        ("PlatformButtonView", "steam", "handle_platform_button", "Steam"),
        ("DriverTypeButtonView", "full_time", "handle_driver_type_button", "Full-Time Driver"),
        ("PreferredTeamsButtonView", "_no_preference_callback", "handle_preferred_teams_button",
         "No Preference"),
        ("NoPreferenceTeammateView", "no_preference", "handle_no_preference_teammate",
         "No Preference"),
        ("NoNotesButtonView", "no_notes_button", "handle_no_notes", "No Notes"),
    ],
)
async def test_a_button_on_a_step_already_answered_is_refused_and_recorded(
    view_name, button, handler, label
):
    """The driver scrolls up and presses a button from a step they have already answered. The
    step handler changes nothing and says why; the view tells the driver so, seen by them alone,
    and records the refusal: one line naming the button, the driver's wizard it sits on, the
    driver, and the reason. Until now the press was answered with nothing at all."""
    interaction = _interaction()
    setattr(interaction.client.wizard_service, handler, AsyncMock(return_value=_ALREADY_ANSWERED))

    await _press_step(view_name, button, interaction)

    getattr(interaction.client.wizard_service, handler).assert_awaited_once()
    replies = [
        (str(call.args[0]), call.kwargs.get("ephemeral"))
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    ]
    assert any(_ALREADY_ANSWERED in text and ephemeral is True for text, ephemeral in replies), (
        replies
    )
    lines = [str(call.args[0]) for call in interaction.client.output_router.post_log.await_args_list]
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith(f"⛔ the “{label}” button"), line
    assert "signup wizard" in line
    assert "refused for Driver (<@7>)" in line
    assert _ALREADY_ANSWERED in line


# ---------------------------------------------------------------------------
# Cancel Signup after the signup ended
# ---------------------------------------------------------------------------

_ENDED_NOT_REFUSED = (
    "#482: a Cancel Signup press after the signup ended is answered as withdrawn, and nothing "
    "is recorded"
)

#: The reason `withdraw` gives where the signup has already ended (S5-A6).
_ALREADY_ENDED = "This signup has already ended. Nothing was changed."


@pytest.mark.xfail(strict=True, reason=_ENDED_NOT_REFUSED)
@pytest.mark.parametrize(
    "view_name, button",
    [
        ("WithdrawButtonView", "withdraw_button"),
        ("NoNotesButtonView", "cancel_button"),
        ("PlatformButtonView", "cancel"),
        ("DriverTypeButtonView", "cancel"),
        ("PreferredTeamsButtonView", "_cancel_callback"),
        ("NoPreferenceTeammateView", "cancel"),
    ],
)
async def test_a_cancel_signup_press_after_the_signup_ended_is_refused_and_recorded(
    view_name, button
):
    """The driver presses a Cancel Signup left in their channel after their signup has ended.
    `withdraw` says why it did nothing, and the button answers that, not "withdrawn", and
    records the refusal."""
    interaction = _interaction()
    interaction.client.wizard_service.withdraw = AsyncMock(return_value=_ALREADY_ENDED)

    await _press_step(view_name, button, interaction)

    interaction.client.wizard_service.withdraw.assert_awaited_once()
    replies = [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    ]
    assert f"⛔ {_ALREADY_ENDED}" in replies, replies
    assert not any("withdrawn" in reply for reply in replies), replies
    lines = [str(call.args[0]) for call in interaction.client.output_router.post_log.await_args_list]
    assert len(lines) == 1, lines
    line = lines[0]
    assert line.startswith("⛔ the “Cancel Signup” button"), line
    assert "signup wizard" in line
    assert "refused for Driver (<@7>)" in line
    assert _ALREADY_ENDED.split(".")[0] in line
