"""The team-preference buttons, and why they resolve a name at the moment they are pressed.

Issue #208. `PreferredTeamsButtonView` and its three callbacks were uncovered. It is step 6 of
the signup wizard — one button per team the driver has not already picked, plus No Preference
and Cancel.

**A button carries an index, not a team name.** A persistent view survives a restart and is
re-registered with no arguments at all, so a name baked into the callback at construction would
be the list as it stood before the restart. Resolving the index against the *live* wizard state
at press time is what makes the button mean what its label says — and the label is redrawn with
the message. `test_a_button_resolves_its_team_from_the_live_wizard_state` is the one that holds
it, and the failure it guards against is silent: a driver picks "Ferrari" and the wizard records
"Mercedes", because the list shifted when they picked their first team.

**A team already picked is not offered again.** Each pick shrinks the available list, so the
index space shifts under the buttons every time — which is exactly why the resolution has to be
live. A stale button pointing past the end of the list is answered rather than crashing or
picking the wrong team.

**Only the driver whose wizard it is may press.** These sit in a private signup channel, but a
manager or another member can be added to one, and a wizard advanced by somebody else would put
another person's answers into a driver's signup.

**After a restart the owner is found by channel.** The view is re-registered without a driver
id, so the channel is the only thing tying the buttons to a person — and a channel with no
wizard is a stale one whose buttons must refuse rather than act on `None`.

**No Preference is an answer, not a skip.** It advances the wizard exactly as a team button
does, with `None` as the choice, so a driver who does not mind is recorded as not minding rather
than left unanswered.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.signup_cog import PreferredTeamsButtonView  # noqa: E402

SERVER_ID = 11208
DRIVER_ID = "4242"
CHANNEL_ID = 700
TEAMS = ["Ferrari", "Mercedes", "McLaren"]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(*, picks=None, team_names=None, snapshot: bool = True):
    return SimpleNamespace(
        discord_user_id=DRIVER_ID,
        draft_answers={"preferred_teams": list(picks or [])},
        config_snapshot=(
            SimpleNamespace(team_names=list(team_names if team_names is not None else TEAMS))
            if snapshot
            else None
        ),
    )


def _interaction(*, user_id: str = DRIVER_ID, wizard=None):
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.channel_id = CHANNEL_ID
    interaction.guild = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = int(user_id)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.wizard_service = MagicMock()
    bot.wizard_service.get_wizard_by_channel = AsyncMock(
        return_value=_wizard() if wizard is None else wizard
    )
    bot.wizard_service.handle_preferred_teams_button = AsyncMock(return_value=None)
    bot.wizard_service.withdraw = AsyncMock(return_value=None)
    interaction.client = bot
    return interaction


def _chosen(interaction) -> list:
    return [
        call.args[1]
        for call in interaction.client.wizard_service.handle_preferred_teams_button.await_args_list
    ]


def _replied(interaction) -> str:
    return "\n".join(
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
        if call.args
    )


async def _view(*, registered: bool = False, team_names=None, excluded=None):
    """*registered* is the restart path: no ids, no names, stub buttons only."""
    if registered:
        return PreferredTeamsButtonView()
    return PreferredTeamsButtonView(
        discord_user_id=DRIVER_ID,
        bot=MagicMock(),
        team_names=team_names if team_names is not None else TEAMS,
        excluded=excluded,
    )


def _labels(view) -> list[str]:
    return [item.label for item in view.children]


async def _press_team(view, index: int, interaction):
    await view._make_team_callback(index)(interaction)


# ---------------------------------------------------------------------------
# What the view offers
# ---------------------------------------------------------------------------


async def test_every_team_gets_a_button():
    view = await _view()

    assert _labels(view)[: len(TEAMS)] == TEAMS


async def test_a_team_already_picked_is_not_offered_again():
    """Each pick shrinks the list, which is why the index space shifts under the buttons
    and why the resolution below has to be live."""
    view = await _view(excluded=["Mercedes"])

    assert _labels(view)[:2] == ["Ferrari", "McLaren"]


async def test_no_preference_and_cancel_are_always_offered():
    """A driver must be able to decline to answer and to abandon the signup from the same
    screen — neither is reachable elsewhere once the wizard has the floor."""
    view = await _view()

    assert _labels(view)[-2:] == ["No Preference", "Cancel Signup"]


async def test_a_restarted_view_offers_stub_buttons_for_every_slot():
    """Registered with `bot.add_view` and no arguments, it cannot know the team names — the
    stubs exist so a press still routes to a callback that can look them up."""
    view = await _view(registered=True)

    assert len(view.children) == 22  # twenty slots, No Preference and Cancel


# ---------------------------------------------------------------------------
# Resolving a press
# ---------------------------------------------------------------------------


async def test_a_button_resolves_its_team_from_the_live_wizard_state():
    """The label is redrawn with the message; the index is all the button carries. A name
    baked in at construction would be the list as it stood before the last press."""
    view = await _view()
    interaction = _interaction()

    await _press_team(view, 1, interaction)

    assert _chosen(interaction) == ["Mercedes"]


async def test_a_press_after_an_earlier_pick_resolves_against_what_is_left():
    """The failure this guards is silent: a driver picks the second button and the wizard
    records a team they did not choose, because the list shifted when they picked first."""
    view = await _view()
    interaction = _interaction(wizard=_wizard(picks=["Ferrari"]))

    await _press_team(view, 1, interaction)

    assert _chosen(interaction) == ["McLaren"]


async def test_a_button_past_the_end_of_the_list_is_answered(tmp_path):
    """A stale screen from before a restart, or a second press of a button whose team has
    just been taken — either way it must not pick the wrong team or raise."""
    view = await _view()
    interaction = _interaction(wizard=_wizard(picks=["Ferrari", "Mercedes"]))

    await _press_team(view, 2, interaction)

    assert "no longer available" in _replied(interaction)
    assert _chosen(interaction) == []


async def test_a_press_in_a_channel_with_no_wizard_is_answered():
    """The channel outlived its wizard — acting on it would advance nothing and raise on
    the snapshot."""
    view = await _view()
    interaction = _interaction(wizard=None)
    interaction.client.wizard_service.get_wizard_by_channel = AsyncMock(return_value=None)

    await _press_team(view, 0, interaction)

    assert "Wizard session not found" in _replied(interaction)
    assert _chosen(interaction) == []


async def test_a_wizard_with_no_snapshot_is_answered():
    """There are no team names to resolve against, and reading `.team_names` off `None` is
    the crash this refusal replaces."""
    view = await _view()
    interaction = _interaction(wizard=_wizard(snapshot=False))

    await _press_team(view, 0, interaction)

    assert "Wizard session not found" in _replied(interaction)


async def test_a_driver_with_no_picks_yet_sees_the_whole_list():
    view = await _view()
    interaction = _interaction(wizard=_wizard(picks=None))

    await _press_team(view, 2, interaction)

    assert _chosen(interaction) == ["McLaren"]


# ---------------------------------------------------------------------------
# Whose buttons these are
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("press", ["team", "no_preference", "cancel"])
async def test_only_the_driver_whose_wizard_it_is_may_press(press):
    """A private channel can have a manager added to it, and a wizard advanced by somebody
    else puts another person's answers into a driver's signup."""
    view = await _view()
    interaction = _interaction(user_id="9999")

    if press == "team":
        await _press_team(view, 0, interaction)
    elif press == "no_preference":
        await view._no_preference_callback(interaction)
    else:
        await view._cancel_callback(interaction)

    assert "not for you" in _replied(interaction)
    assert _chosen(interaction) == []
    interaction.client.wizard_service.withdraw.assert_not_awaited()


async def test_after_a_restart_the_owner_is_found_by_channel():
    """The view is re-registered without a driver id, so the channel is the only thing
    tying the buttons to a person."""
    view = await _view(registered=True)
    interaction = _interaction()

    await _press_team(view, 0, interaction)

    assert _chosen(interaction) == ["Ferrari"]


async def test_a_restarted_view_in_a_channel_with_no_wizard_refuses():
    """There is nobody to attribute the press to, and `None` must not be treated as a
    matching user id."""
    view = await _view(registered=True)
    interaction = _interaction()
    interaction.client.wizard_service.get_wizard_by_channel = AsyncMock(return_value=None)

    await _press_team(view, 0, interaction)

    assert "not for you" in _replied(interaction)
    assert _chosen(interaction) == []


# ---------------------------------------------------------------------------
# No Preference, and Cancel
# ---------------------------------------------------------------------------


async def test_no_preference_is_recorded_as_an_answer():
    """Not a skip: a driver who does not mind is recorded as not minding rather than left
    unanswered, which is what lets the wizard advance."""
    view = await _view()
    interaction = _interaction()

    await view._no_preference_callback(interaction)

    assert _chosen(interaction) == [None]


async def test_no_preference_advances_the_wizard_the_same_way_a_team_does():
    """Same call, same arguments but the choice — so a later change to how a pick is
    handled cannot leave "no preference" behind."""
    view = await _view()
    team = _interaction()
    none = _interaction()

    await _press_team(view, 0, team)
    await view._no_preference_callback(none)

    team_call = team.client.wizard_service.handle_preferred_teams_button.await_args
    none_call = none.client.wizard_service.handle_preferred_teams_button.await_args
    assert team_call.args[0] == none_call.args[0]


async def test_cancelling_withdraws_the_signup():
    view = await _view()
    interaction = _interaction()

    await view._cancel_callback(interaction)

    interaction.client.wizard_service.withdraw.assert_awaited_once()
    assert "withdrawn" in _replied(interaction)


@pytest.mark.parametrize("press", ["team", "no_preference", "cancel"])
async def test_every_press_defers_before_working(press):
    """Advancing the wizard posts the next step and can edit the channel, which outruns
    Discord's three-second window."""
    view = await _view()
    interaction = _interaction()

    if press == "team":
        await _press_team(view, 0, interaction)
    elif press == "no_preference":
        await view._no_preference_callback(interaction)
    else:
        await view._cancel_callback(interaction)

    interaction.response.defer.assert_awaited_once()
