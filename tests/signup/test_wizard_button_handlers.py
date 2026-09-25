"""The signup wizard's buttons — platform, driver type, preferred teams and teammate.

Issue #208. Four of the wizard's steps are answered by pressing a button rather than typing,
and those four handlers were unexecuted. They are not thin wrappers around the typed handlers:
a button arrives from Discord with no message and no channel, so each one has to find the
driver's wizard, check it is still on the step the button belongs to, and find the channel
again before it can do anything.

**Every handler re-checks the step, and that check is the interesting part.** A wizard channel
keeps its old messages, so the buttons from step 2 are still sitting there when the driver
reaches step 6. Pressing one must do nothing at all. Without the check, a driver scrolling up
and pressing an old button would overwrite an answer they had already given and shunt the
wizard backwards. Each handler gets a test for that, because the check is copied between them
rather than shared.

**Preferred teams is a sub-step loop and the only one with real logic.** A driver picks up to
three teams, one button press at a time; each pick narrows the choices offered next; and
**No Preference** finishes early with however many picks have accumulated. Three things end
the loop — three picks taken, no teams left to offer, or No Preference — and all three are
pinned, because each ends it by a different route and a reader collapsing them would strand a
driver on a step with no way forward.

The sub-step counter lives in `draft_answers` under `_pref_teams_step` and is **removed** when
the loop ends. It is bookkeeping, not an answer, and leaving it behind would carry it into the
committed signup.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

SERVER_ID = 1
DRIVER_ID = "7"
CHANNEL_ID = 99


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wizard(state, **draft):
    from leaguebot.signup.models.signup_module import ConfigSnapshot, SignupWizardRecord

    return SignupWizardRecord(
        id=1,
        discord_user_id=DRIVER_ID,
        wizard_state=state,
        signup_channel_id=CHANNEL_ID,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=[],
            slots=[],
            team_names=["Alpha", "Beta", "Gamma", "Delta"],
        ),
        draft_answers=dict(draft),
        current_lap_track_index=0,
        last_activity_at=None,
    )


@pytest.fixture
def service():
    """A `WizardService` with its Discord edges stubbed, plus the channel and a record of
    every advance."""
    from leaguebot.signup.services.wizard_service import WizardService

    svc = WizardService.__new__(WizardService)
    advanced: list = []

    async def _advance_in_channel(wizard, channel, guild):
        advanced.append(wizard)

    svc._advance_wizard_in_channel = _advance_in_channel  # type: ignore[method-assign]
    svc._reset_inactivity_job = AsyncMock(return_value=None)  # type: ignore[method-assign]

    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock(return_value=None)

    guild = MagicMock(spec=discord.Guild)
    guild.get_channel = MagicMock(return_value=channel)

    signup_svc = MagicMock()
    signup_svc.save_wizard = AsyncMock(return_value=None)

    bot = MagicMock()
    bot.signup_module_service = signup_svc
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    svc._bot = bot

    return SimpleNamespace(
        svc=svc,
        advanced=advanced,
        channel=channel,
        guild=guild,
        signup_svc=signup_svc,
    )


def _serve(ctx, wizard) -> None:
    """Make `get_wizard` answer with *wizard*."""
    ctx.signup_svc.get_wizard = AsyncMock(return_value=wizard)


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


async def test_a_platform_button_records_the_platform_and_advances(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM)
    _serve(service, wizard)

    await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert wizard.draft_answers["platform"] == "Steam"
    assert service.advanced == [wizard]


async def test_a_platform_button_pressed_on_a_later_step_does_nothing(service):
    """The step-2 buttons are still in the channel when the driver reaches step 5.
    Pressing one must not overwrite an answer and shunt the wizard backwards."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_DRIVER_TYPE)
    _serve(service, wizard)

    await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert "platform" not in wizard.draft_answers
    assert service.advanced == []


async def test_a_button_from_a_driver_with_no_wizard_does_nothing(service):
    """The wizard channel is deleted when a signup completes, but a button could still be
    pressed from a cached view before the delete lands."""
    _serve(service, None)

    await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert service.advanced == []


async def test_a_button_whose_channel_is_gone_does_not_advance(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PLATFORM)
    _serve(service, wizard)
    service.guild.get_channel = MagicMock(return_value=None)

    await service.svc.handle_platform_button(DRIVER_ID, "Steam", service.guild)

    assert service.advanced == []


# ---------------------------------------------------------------------------
# Driver type
# ---------------------------------------------------------------------------


async def test_a_driver_type_button_records_the_type_and_advances(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_DRIVER_TYPE)
    _serve(service, wizard)

    await service.svc.handle_driver_type_button(
        DRIVER_ID, "Reserve Driver", service.guild
    )

    assert wizard.draft_answers["driver_type"] == "Reserve Driver"
    assert service.advanced == [wizard]


async def test_a_driver_type_button_pressed_on_a_later_step_does_nothing(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_driver_type_button(
        DRIVER_ID, "Reserve Driver", service.guild
    )

    assert "driver_type" not in wizard.draft_answers
    assert service.advanced == []


# ---------------------------------------------------------------------------
# Preferred teams — the sub-step loop
# ---------------------------------------------------------------------------


async def test_a_first_team_pick_is_recorded_and_the_next_is_offered(service):
    """The loop continues rather than advancing: a driver picking one team has not yet
    said they are finished."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == ["Alpha"]
    assert wizard.draft_answers["_pref_teams_step"] == 1
    assert service.advanced == []
    service.channel.send.assert_awaited_once()


async def test_the_next_prompt_names_the_ordinal_and_the_picks_so_far(service):
    """The driver is several presses into a list they cannot see; the prompt is the only
    record of what they have already chosen."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    prompt = service.channel.send.await_args.args[0]
    assert "2nd" in prompt
    assert "Alpha" in prompt


async def test_a_team_already_picked_is_not_offered_again(service):
    """`excluded` is what stops a driver naming the same team as their first and second
    choice, which would waste a pick."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    view = service.channel.send.await_args.kwargs["view"]
    labels = [child.label for child in view.children if getattr(child, "label", None)]
    assert "Alpha" not in labels


async def test_a_third_pick_ends_the_loop(service):
    """Three is the limit, so the third press advances rather than offering a fourth."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(
        WizardState.COLLECTING_PREFERRED_TEAMS,
        preferred_teams=["Alpha", "Beta"],
        _pref_teams_step=2,
    )
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Gamma", service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == ["Alpha", "Beta", "Gamma"]
    assert service.advanced == [wizard]


async def test_running_out_of_teams_ends_the_loop(service):
    """A league with two teams cannot offer a third pick. Without this the driver would be
    shown a prompt with no buttons on it and no way forward."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS, preferred_teams=["Alpha"])
    wizard.config_snapshot.team_names = ["Alpha", "Beta"]
    wizard.draft_answers["_pref_teams_step"] = 1
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Beta", service.guild
    )

    assert service.advanced == [wizard]


async def test_no_preference_ends_the_loop_keeping_the_picks_so_far(service):
    """The third way out. A driver who wanted only one team says so by pressing No
    Preference, and that first pick must survive — discarding it would lose the one
    preference they expressed."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(
        WizardState.COLLECTING_PREFERRED_TEAMS,
        preferred_teams=["Alpha"],
        _pref_teams_step=1,
    )
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, None, service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == ["Alpha"]
    assert service.advanced == [wizard]


async def test_no_preference_with_no_picks_at_all_stores_an_empty_list(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, None, service.guild
    )

    assert wizard.draft_answers["preferred_teams"] == []
    assert service.advanced == [wizard]


@pytest.mark.parametrize("team", ["Gamma", None], ids=["third-pick", "no-preference"])
async def test_the_sub_step_counter_is_cleared_when_the_loop_ends(service, team):
    """It is bookkeeping, not an answer. Left in `draft_answers` it would be carried into
    the committed signup as though the driver had told the league something."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(
        WizardState.COLLECTING_PREFERRED_TEAMS,
        preferred_teams=["Alpha", "Beta"],
        _pref_teams_step=2,
    )
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, team, service.guild
    )

    assert "_pref_teams_step" not in wizard.draft_answers


async def test_a_continuing_loop_saves_the_wizard_and_resets_the_timeout(service):
    """Each press is activity. Without the reset a driver working through three picks
    could be timed out mid-choice."""
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMS)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    service.signup_svc.save_wizard.assert_awaited_once()
    service.svc._reset_inactivity_job.assert_awaited_once()
    assert wizard.last_activity_at is not None


async def test_a_team_button_pressed_on_a_later_step_does_nothing(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NOTES)
    _serve(service, wizard)

    await service.svc.handle_preferred_teams_button(
        DRIVER_ID, "Alpha", service.guild
    )

    assert "preferred_teams" not in wizard.draft_answers
    assert service.advanced == []


# ---------------------------------------------------------------------------
# Preferred teammate
# ---------------------------------------------------------------------------


async def test_no_preference_for_a_teammate_records_none_and_advances(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_PREFERRED_TEAMMATE)
    _serve(service, wizard)

    await service.svc.handle_no_preference_teammate(DRIVER_ID, service.guild)

    assert wizard.draft_answers["preferred_teammate"] is None
    assert service.advanced == [wizard]


async def test_the_teammate_button_pressed_on_a_later_step_does_nothing(service):
    from leaguebot.signup.models.signup_module import WizardState

    wizard = _wizard(WizardState.COLLECTING_NOTES)
    _serve(service, wizard)

    await service.svc.handle_no_preference_teammate(DRIVER_ID, service.guild)

    assert "preferred_teammate" not in wizard.draft_answers
    assert service.advanced == []
