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

**On the change queue (#439, slice 6b, C76, C82).** Each view is handed the driver's account and
the change queue when it is built (A23). A press that passes the guard asks `signup.answer` of the
queue (Cancel Signup asks `signup.end`), acknowledged privately and the acknowledgement updated
with its outcome; the answer is saved, and judged (a step already answered, a signup already
ended), when the queue reaches it. The guard's refusals stay at the press and are tested here on
doubles; what a press does once it passes is tested through a real queue, on the league of
`tests/signup/test_preferred_teams_view.py`: Kimi (105) part-way through signing up, his signup
channel (6405) standing.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.signup.test_preferred_teams_view import _answered, _changes, _driver_state, _team_step
from tests.signup.test_wizard_advance import KIMI_LANE, _state
from tests.support.change_queue import acknowledgement, member_interaction, run_queue
from tests.support.season_league import SIGNING_UP, SeasonLeague

SERVER_ID = 1
DRIVER_ID = "7"
OTHER_USER_ID = 8
CHANNEL_ID = 99

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
    # Today's handlers off the queue, stand-ins that do nothing; a refused press reaches none of
    # them, nor the queue.
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


async def test_another_member_cannot_answer_a_driver_s_platform_question():
    """The channel is private to its driver, but a league manager can see it."""
    from leaguebot.signup.services.signup_views import PlatformButtonView

    queue = MagicMock()
    view = PlatformButtonView(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await type(view).steam(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Steam")
    queue.ask.assert_not_called()


async def test_another_member_cannot_cancel_a_driver_s_signup():
    """The one button that destroys work."""
    from leaguebot.signup.services.signup_views import PlatformButtonView

    queue = MagicMock()
    view = PlatformButtonView(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await type(view).cancel(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    queue.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


async def test_a_view_whose_channel_has_no_wizard_refuses_everyone():
    """A button left in a channel whose wizard has been cleared up. The lookup finding
    nothing must refuse rather than pass unauthenticated."""
    from leaguebot.signup.services.signup_views import PlatformButtonView

    view = PlatformButtonView()
    interaction = _interaction(wizard_user=None)

    await type(view).steam(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Steam")


async def test_a_rebuilt_view_still_refuses_the_wrong_member():
    """The guard has to hold on the recovered identity too, not only the stored one."""
    from leaguebot.signup.services.signup_views import PlatformButtonView

    view = PlatformButtonView()
    interaction = _interaction(OTHER_USER_ID)

    await type(view).steam(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Steam")


# ---------------------------------------------------------------------------
# Driver type view
# ---------------------------------------------------------------------------


async def test_another_member_cannot_choose_a_driver_s_type():
    from leaguebot.signup.services.signup_views import DriverTypeButtonView

    queue = MagicMock()
    view = DriverTypeButtonView(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await type(view).full_time(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Full-Time Driver")
    queue.ask.assert_not_called()


async def test_another_member_cannot_cancel_from_the_driver_type_step():
    from leaguebot.signup.services.signup_views import DriverTypeButtonView

    queue = MagicMock()
    view = DriverTypeButtonView(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await type(view).cancel(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    queue.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Preferred teams view — buttons built at runtime
# ---------------------------------------------------------------------------


async def test_a_button_is_offered_for_each_team_still_available():
    """Unlike the other views, this one's buttons are built from the league's own teams,
    so there is no decorator to read them off — the view has to be inspected."""
    from leaguebot.signup.services.signup_views import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(
        DRIVER_ID, MagicMock(), ["Alpha", "Beta", "Gamma"]
    )

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    for team in ("Alpha", "Beta", "Gamma"):
        assert team in labels


async def test_a_team_already_picked_is_excluded():
    """What stops a driver spending two of their three picks on one team."""
    from leaguebot.signup.services.signup_views import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(
        DRIVER_ID, MagicMock(), ["Alpha", "Beta"], excluded=["Alpha"]
    )

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    assert "Alpha" not in labels
    assert "Beta" in labels


async def test_no_preference_is_always_offered():
    """It is the driver's only way out of the loop before their third pick."""
    from leaguebot.signup.services.signup_views import PreferredTeamsButtonView

    view = PreferredTeamsButtonView(DRIVER_ID, MagicMock(), ["Alpha"])

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    assert any("No Preference" in label for label in labels)


async def test_another_member_cannot_finish_a_driver_s_team_step():
    from leaguebot.signup.services.signup_views import PreferredTeamsButtonView

    queue = MagicMock()
    view = PreferredTeamsButtonView(DRIVER_ID, queue, ["Alpha"])
    interaction = _interaction(OTHER_USER_ID)

    await view._no_preference_callback(interaction)

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "No Preference")
    queue.ask.assert_not_called()


async def test_another_member_cannot_cancel_from_the_team_step():
    from leaguebot.signup.services.signup_views import PreferredTeamsButtonView

    queue = MagicMock()
    view = PreferredTeamsButtonView(DRIVER_ID, queue, ["Alpha"])
    interaction = _interaction(OTHER_USER_ID)

    await view._cancel_callback(interaction)

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    queue.ask.assert_not_called()


# ---------------------------------------------------------------------------
# Teammate view
# ---------------------------------------------------------------------------


async def test_another_member_cannot_answer_the_teammate_question():
    from leaguebot.signup.services.signup_views import NoPreferenceTeammateView

    queue = MagicMock()
    view = NoPreferenceTeammateView(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await type(view).no_preference(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "No Preference")
    queue.ask.assert_not_called()


async def test_another_member_cannot_cancel_from_the_teammate_step():
    from leaguebot.signup.services.signup_views import NoPreferenceTeammateView

    queue = MagicMock()
    view = NoPreferenceTeammateView(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await type(view).cancel(view, interaction, MagicMock())

    assert _refused(interaction)
    _assert_refusal_recorded(interaction, "Cancel Signup")
    queue.ask.assert_not_called()


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
    from leaguebot.signup.services import signup_views

    queue = MagicMock()
    view = getattr(signup_views, view_name)(DRIVER_ID, queue)
    interaction = _interaction(OTHER_USER_ID)

    await getattr(type(view), button)(view, interaction, MagicMock())

    assert _refused(interaction)
    queue.ask.assert_not_called()
    _assert_refusal_recorded(interaction, label)


# ---------------------------------------------------------------------------
# What a press does once it passes the guard, on the change queue
# ---------------------------------------------------------------------------

#: Every view that offers Cancel Signup, and the button on it.
CANCELS = [
    ("WithdrawButtonView", "withdraw_button"),
    ("NoNotesButtonView", "cancel_button"),
    ("PlatformButtonView", "cancel"),
    ("DriverTypeButtonView", "cancel"),
    ("PreferredTeamsButtonView", "_cancel_callback"),
    ("NoPreferenceTeammateView", "cancel"),
]

#: The reason a press of Cancel Signup is refused where the signup has already ended (S5-A6).
_ALREADY_ENDED = "This signup has already ended. Nothing was changed."


async def _kimi_at(tmp_path: Any, state: str, **kwargs: Any) -> SeasonLeague:
    """Kimi's wizard at *state*, today's wizard handlers off the queue stand-ins that do
    nothing, so that only what the press asks of the queue shows."""
    league = await _team_step(tmp_path, state=state, **kwargs)
    wizards = league.bot.wizard_service
    for handler in ("handle_platform_button", "handle_driver_type_button",
                    "handle_no_preference_teammate", "handle_no_notes"):
        setattr(wizards, handler, AsyncMock(return_value=None))
    return league


async def _press_on(league: SeasonLeague, view_name: str, button: str, *,
                    registered: bool = False) -> Any:
    """Kimi presses *button* on *view_name*, built for him and handed the change queue (A23),
    or re-registered at start with the queue alone; gives his press."""
    from leaguebot.signup.services import signup_views
    from tests.signup.test_preferred_teams_view import KIMI_CHANNEL, TEAMS

    press = member_interaction(league.bot, user=league._member(SIGNING_UP),
                               created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = KIMI_CHANNEL
    press.channel = league.channel(KIMI_CHANNEL)
    account = None if registered else str(SIGNING_UP)
    if view_name == "PreferredTeamsButtonView":
        view = signup_views.PreferredTeamsButtonView(account, league.bot.change_queue, list(TEAMS))
        await getattr(view, button)(press)
        return press
    view = getattr(signup_views, view_name)(account, league.bot.change_queue)
    await getattr(type(view), button)(view, press, MagicMock())
    return press


async def _draft(league: SeasonLeague) -> dict[str, Any]:
    import json

    from tests.signup.test_wizard_advance import _wizard

    wizard = await _wizard(league)
    assert wizard is not None
    return dict(json.loads(wizard["draft_answers_json"]))


def _one_refusal(league: SeasonLeague, label: str, reason: str) -> None:
    """One ⛔ line in the log channel, naming *label*'s button on Kimi's signup wizard and
    giving *reason*."""
    lines = [line for line in league.log().splitlines() if line.startswith("⛔")]
    assert len(lines) == 1, league.log()
    assert lines[0].startswith(f"⛔ the “{label}” button"), lines[0]
    assert "signup wizard" in lines[0]
    assert reason in lines[0], lines[0]


@pytest.mark.parametrize(
    "button,platform",
    [("steam", "Steam"), ("ea", "EA"), ("xbox", "Xbox"), ("playstation", "PlayStation")],
)
async def test_each_platform_button_reports_its_own_platform(tmp_path, button, platform):
    """Four near-identical callbacks; one passing the wrong string would record a driver on a
    platform they never chose. Kimi, at the platform question, presses each in turn (one
    league each): he is answered at once that it is under way, and once the queue runs his
    platform is the one the button names."""
    league = await _kimi_at(tmp_path, "COLLECTING_PLATFORM")

    press = await _press_on(league, "PlatformButtonView", button)

    assert acknowledgement(press).startswith("⏳")
    await run_queue(league.bot)
    assert (await _draft(league))["platform"] == platform


async def test_a_view_rebuilt_after_a_restart_finds_its_driver(tmp_path):
    """The view is re-registered at start with no stored driver — the process that knew has
    gone — and only the change queue, so the owner is found by looking the wizard up by
    channel: Kimi presses Steam on it, the answer is asked of the queue as his, and once it
    runs Steam is his platform."""
    league = await _kimi_at(tmp_path, "COLLECTING_PLATFORM")

    await _press_on(league, "PlatformButtonView", "steam", registered=True)
    await run_queue(league.bot)

    assert (await _draft(league))["platform"] == "Steam"


@pytest.mark.parametrize(
    "button,driver_type",
    [("full_time", "Full-Time Driver"), ("reserve", "Reserve Driver")],
)
async def test_each_driver_type_button_reports_its_own_type(tmp_path, button, driver_type):
    """The answer that decides whether the driver is asked about teams at all. Kimi, at the
    driver type question, presses each (one league each); once the queue runs his driver type
    is the one the button names."""
    league = await _kimi_at(tmp_path, "COLLECTING_DRIVER_TYPE")

    await _press_on(league, "DriverTypeButtonView", button)
    await run_queue(league.bot)

    assert (await _draft(league))["driver_type"] == driver_type


async def test_no_preference_answers_the_teammate_question(tmp_path):
    """Kimi, at the teammate question, presses No Preference: the press asks signup.answer on
    the lane of his signup channel, and once the queue runs his wizard has moved past the
    teammate question with no teammate recorded."""
    league = await _kimi_at(tmp_path, "COLLECTING_PREFERRED_TEAMMATE")

    await _press_on(league, "NoPreferenceTeammateView", "no_preference")

    [asked] = await _changes(league)
    assert (asked["kind"], asked.get("lane")) == ("signup.answer", KIMI_LANE)
    await run_queue(league.bot)
    assert await _state(league) != "COLLECTING_PREFERRED_TEAMMATE"
    assert (await _draft(league)).get("preferred_teammate") is None


@pytest.mark.parametrize("view_name, button", CANCELS)
async def test_cancelling_from_any_step_withdraws_the_signup(tmp_path, view_name, button):
    """Cancel Signup is the one button that destroys work, and a driver reaching it from the
    welcome message, the platform, driver type, team, teammate or notes step is treated the
    same. Kimi presses it (one league each): it asks signup.end of the league lane, and once
    the queue runs he is Not Signed Up."""
    league = await _kimi_at(tmp_path, "COLLECTING_PLATFORM")

    await _press_on(league, view_name, button)

    assert [(asked["kind"], asked.get("lane") or "league")
            for asked in await _changes(league)] == [("signup.end", "league")]
    await run_queue(league.bot)
    assert await _driver_state(league) == "NOT_SIGNED_UP"


@pytest.mark.parametrize(
    "view_name, button, label",
    [
        ("PlatformButtonView", "steam", "Steam"),
        ("DriverTypeButtonView", "full_time", "Full-Time Driver"),
        ("PreferredTeamsButtonView", "_no_preference_callback", "No Preference"),
        ("NoPreferenceTeammateView", "no_preference", "No Preference"),
        ("NoNotesButtonView", "no_notes_button", "No Notes"),
    ],
)
async def test_a_button_on_a_step_already_answered_is_refused_and_recorded(
    tmp_path, view_name, button, label
):
    """Kimi has submitted his signup (awaiting approval, his wizard parked) and scrolls up to
    press a button from a step he has already answered. The answer is judged when the queue
    reaches it: nothing changes, Kimi is told privately "That step has already been answered.",
    and one ⛔ line names the button, his signup wizard and the reason."""
    league = await _kimi_at(tmp_path, "UNENGAGED", driver_state="PENDING_ADMIN_APPROVAL")

    press = await _press_on(league, view_name, button)
    await run_queue(league.bot)

    assert _ALREADY_ANSWERED in _answered(press)
    _one_refusal(league, label, _ALREADY_ANSWERED)
    assert await _state(league) == "UNENGAGED"
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"


@pytest.mark.parametrize("view_name, button", CANCELS)
async def test_a_cancel_signup_press_after_the_signup_ended_is_refused_and_recorded(
    tmp_path, view_name, button
):
    """Kimi presses a Cancel Signup left in his channel after his signup ended (Not Signed Up,
    his wizard parked). The press is judged as the change runs: he is told "⛔ This signup has
    already ended. Nothing was changed.", never that it was withdrawn, and one ⛔ line names
    the Cancel Signup button of his signup wizard and the reason."""
    league = await _kimi_at(tmp_path, "UNENGAGED", driver_state="NOT_SIGNED_UP")

    press = await _press_on(league, view_name, button)
    await run_queue(league.bot)

    answered = _answered(press)
    assert f"⛔ {_ALREADY_ENDED}" in answered, answered
    assert "withdrawn" not in answered, answered
    _one_refusal(league, "Cancel Signup", _ALREADY_ENDED.split(".")[0])
