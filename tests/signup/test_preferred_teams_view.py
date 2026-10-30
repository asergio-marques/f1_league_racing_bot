"""The team-preference buttons, and why they resolve a name at the moment they are pressed.

Issue #208. `PreferredTeamsButtonView` and its three callbacks were uncovered. It is step 6 of
the signup wizard — one button per team the driver has not already picked, plus No Preference
and Cancel.

**A button records the team its label names** (#482, D3). A persistent view survives a restart
and is re-registered with no arguments at all, so the callback knows only its button's
`custom_id`. The pressed button is found by that id on the message it sits on, and its label is
the team. Resolving the index against the live picks instead recorded a different team from the
label whenever the driver pressed a button on an earlier sub-step's message: they picked Ferrari,
scrolled up, pressed Mercedes, and the wizard recorded McLaren.
`test_a_press_on_an_earlier_message_records_the_team_its_label_names` holds it.

**A team already picked is not offered again**, and pressing it again on an earlier message is
turned away by the step handler ("That team has already been picked."), which the button answers
and records. A button that cannot be found on its message is answered rather than crashing or
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

**On the change queue (#439, slice 6b, C82).** The view is handed the driver's account and the
change queue when it is built (A23), and a press asks `signup.answer` of the queue, acknowledged
privately and its acknowledgement updated with the outcome; the pick is saved, and judged (a team
already picked, a step already answered), when the queue reaches it. Cancel Signup asks
`signup.end` (C76). A press that is not the driver's, or that names no team, is refused at once
and asks nothing. These tests drive the view through a real queue: the league is
`tests/signup/test_wizard_advance.py`'s, Kimi (105) part-way through signing up, his wizard at
the team step offering Ferrari, Mercedes and McLaren, in his signup channel (6405).
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.signup.services.signup_views import PreferredTeamsButtonView
from tests.signup.test_wizard_advance import KIMI_CHANNEL, KIMI_LANE, _state, _wizard
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    member_interaction,
    run_queue,
    updated_reply,
)
from tests.support.review_league import channel
from tests.support.season_league import SIGNING_UP, SeasonLeague, setup_league, signing_up

TEAMS = ("Ferrari", "Mercedes", "McLaren")
TEAM_STEP = "COLLECTING_PREFERRED_TEAMS"
#: A channel holding no signup wizard.
NO_WIZARD_CHANNEL = 6499
#: A member who is not Kimi.
SOMEONE_ELSE = 9999

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _team_step(tmp_path: Any, *, picks: list[str] | None = None,
                     state: str = TEAM_STEP,
                     driver_state: str = "PENDING_SIGNUP_COMPLETION") -> SeasonLeague:
    """`setup_league` at Signups, the window open; Kimi at *driver_state* (part-way through
    signing up unless a test says otherwise), his wizard
    at *state* (the team step unless a test says otherwise) offering Ferrari, Mercedes and
    McLaren and holding *picks*; his signup channel standing and recording what it is sent.
    Today's wizard handler off the queue is a stand-in that does nothing, so that only what the
    press asks of the queue shows.

    The wizard row is saved through `save_wizard_on` in a save of the test's own, as a signup
    job's record saves it, and through today's committing `save_wizard` until that form exists,
    so the refusals the view makes at the press hold on either side of the change."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.signup.models.signup_module import (
        ConfigSnapshot,
        SignupWizardRecord,
        WizardState,
    )
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    os.makedirs(str(tmp_path), exist_ok=True)
    league = await setup_league(tmp_path, stage="SIGNUPS", signups_open=True)
    await signing_up(league, SIGNING_UP, state=driver_state)
    signups = SignupModuleService(league.db_path)
    wizard = SignupWizardRecord(
        id=-1,
        discord_user_id=str(SIGNING_UP),
        wizard_state=WizardState(state),
        signup_channel_id=KIMI_CHANNEL,
        config_snapshot=ConfigSnapshot(
            nationality_required=False,
            time_type="TIME_TRIAL",
            time_image_required=False,
            selected_track_ids=[],
            slots=[],
            team_names=list(TEAMS),
        ),
        draft_answers={"preferred_teams": list(picks or [])},
        current_lap_track_index=0,
        last_activity_at=league.clock.now.isoformat(),
    )
    save_on = getattr(signups, "save_wizard_on", None)
    if save_on is None:
        await signups.save_wizard(wizard)
    else:
        async with get_connection(league.db_path) as db:
            await save_on(db, wizard)
            await db.commit()
    made = league._recording(channel(KIMI_CHANNEL, league.events))
    made.guild = league.guild
    made.posted = []
    made.set_permissions = AsyncMock()
    league.channels[KIMI_CHANNEL] = made
    league.bot.wizard_service.get_wizard_by_channel = AsyncMock(
        side_effect=signups.get_wizard_by_channel
    )
    league.bot.wizard_service.handle_preferred_teams_button = AsyncMock(return_value=None)
    return league


def _message(labels=None):
    """The message the team buttons sit on: one button per label, `pteam_0` onward, then No
    Preference and Cancel, as `PreferredTeamsButtonView` posts them."""
    teams = [
        SimpleNamespace(custom_id=f"pteam_{i}", label=label)
        for i, label in enumerate(TEAMS if labels is None else labels)
    ]
    return SimpleNamespace(components=[
        SimpleNamespace(children=teams),
        SimpleNamespace(children=[
            SimpleNamespace(custom_id="pteam_nopref", label="No Preference"),
            SimpleNamespace(custom_id="pteam_cancel", label="Cancel Signup"),
        ]),
    ])


def _press(league: SeasonLeague, *, by: int = SIGNING_UP, labels=None,
           channel_id: int = KIMI_CHANNEL) -> Any:
    """A press by *by* in *channel_id*, on the message carrying *labels* (every team by
    default)."""
    press = member_interaction(league.bot, user=league._member(by), created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = channel_id
    press.channel = league.channels.get(channel_id) or league.channel(KIMI_CHANNEL)
    press.message = _message(labels)
    press.data = {}
    return press


def _bare() -> Any:
    """A league stand-in carrying only a change queue, for the view's own layout."""
    return SimpleNamespace(bot=SimpleNamespace(change_queue=MagicMock()))


def _view(league: Any, *, registered: bool = False, team_names=TEAMS, excluded=None):
    """Built for Kimi and handed the change queue (A23); *registered* is the restart path, with
    no account and no names, stub buttons only."""
    if registered:
        return PreferredTeamsButtonView(None, league.bot.change_queue)
    return PreferredTeamsButtonView(
        str(SIGNING_UP), league.bot.change_queue,
        team_names=list(team_names), excluded=excluded,
    )


async def _press_team(view, index: int, press) -> Any:
    press.data = {"custom_id": f"pteam_{index}", "component_type": 2}
    await view._make_team_callback(index)(press)
    return press


def _labels(view) -> list[str]:
    return [item.label for item in view.children]


async def _picks(league: SeasonLeague) -> list[str]:
    import json

    wizard = await _wizard(league)
    assert wizard is not None
    return list(json.loads(wizard["draft_answers_json"]).get("preferred_teams") or [])


async def _changes(league: SeasonLeague) -> list[dict[str, Any]]:
    return list(await change_rows(league.db_path))


def _answered(press: Any) -> str:
    """All Kimi was told: the acknowledgement, then the reply as updated."""
    return "\n".join(part for part in (acknowledgement(press), updated_reply(press)) if part)


def _refused_line(league: SeasonLeague, reason: str) -> None:
    """One ⛔ line in the log channel giving *reason*."""
    lines = [line for line in league.log().splitlines() if line.startswith("⛔")]
    assert len(lines) == 1, league.log()
    assert reason in lines[0], lines[0]


async def _driver_state(league: SeasonLeague) -> str:
    [row] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE discord_user_id = ?", str(SIGNING_UP)
    )
    return str(row["current_state"])


# ---------------------------------------------------------------------------
# What the view offers
# ---------------------------------------------------------------------------


async def test_every_team_gets_a_button():
    """Built for Kimi with the teams Ferrari, Mercedes and McLaren and handed the change queue,
    the view offers one button per team, in that order."""
    view = _view(_bare())

    assert _labels(view)[: len(TEAMS)] == list(TEAMS)


async def test_a_team_already_picked_is_not_offered_again():
    """Each pick shrinks the list, which is why the index space shifts under the buttons
    and why the resolution below reads the button's label."""
    view = _view(_bare(), excluded=["Mercedes"])

    assert _labels(view)[:2] == ["Ferrari", "McLaren"]


async def test_no_preference_and_cancel_are_always_offered():
    """A driver must be able to decline to answer and to abandon the signup from the same
    screen — neither is reachable elsewhere once the wizard has the floor."""
    view = _view(_bare())

    assert _labels(view)[-2:] == ["No Preference", "Cancel Signup"]


async def test_a_restarted_view_offers_stub_buttons_for_every_slot():
    """Registered at start with no account and only the change queue, it cannot know the team
    names — the stubs exist so a press still routes to a callback that can look them up."""
    view = _view(_bare(), registered=True)

    assert len(view.children) == 22  # twenty slots, No Preference and Cancel


# ---------------------------------------------------------------------------
# Resolving a press
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("index, team", [(1, "Mercedes"), (2, "McLaren")],
                         ids=["second-button", "third-button"])
async def test_a_button_records_the_team_its_label_names(tmp_path, index, team):
    """Kimi, with no picks yet, sees the whole list and presses the second button, labelled
    Mercedes, or the third, labelled McLaren. At once he is answered privately that it is under
    way and nothing is saved; once the queue runs, the team the button names is his pick."""
    league = await _team_step(tmp_path)

    press = await _press_team(_view(league), index, _press(league))

    assert acknowledgement(press).startswith("⏳")
    assert await _picks(league) == []
    await run_queue(league.bot)
    assert await _picks(league) == [team]


async def test_a_press_on_an_earlier_message_records_the_team_its_label_names(tmp_path):
    """Kimi picked Ferrari, scrolled up to the first sub-step's message, where Ferrari,
    Mercedes and McLaren are all still shown, and pressed Mercedes. Once the queue runs his
    picks are Ferrari then Mercedes, not McLaren, which is what the second of the teams left
    used to resolve to."""
    league = await _team_step(tmp_path, picks=["Ferrari"])

    await _press_team(_view(league), 1, _press(league))
    await run_queue(league.bot)

    assert await _picks(league) == ["Ferrari", "Mercedes"]


async def test_a_team_pressed_again_on_an_earlier_message_is_refused_and_recorded(tmp_path):
    """Kimi picked Ferrari, scrolled up to the first sub-step's message and pressed Ferrari
    again. The pick is judged when the queue reaches it: Kimi is told "That team has already
    been picked.", one ⛔ line records the refusal, and his picks stay Ferrari alone."""
    league = await _team_step(tmp_path, picks=["Ferrari"])

    press = await _press_team(_view(league), 0, _press(league))
    await run_queue(league.bot)

    assert "That team has already been picked." in _answered(press)
    _refused_line(league, "That team has already been picked.")
    assert await _picks(league) == ["Ferrari"]


async def test_a_button_past_the_end_of_the_list_is_answered(tmp_path):
    """A stale screen: the third team button is pressed, but the message it was pressed on
    carries only two team buttons, so there is no label to name a team. It must not pick a
    team or raise: Kimi is told "That option is no longer available.", the refusal is
    recorded, and nothing is asked of the queue."""
    league = await _team_step(tmp_path)

    press = await _press_team(_view(league), 2, _press(league, labels=["Ferrari", "Mercedes"]))

    assert "no longer available" in _answered(press)
    _refused_line(league, "That option is no longer available.")
    assert await _changes(league) == []
    assert await _picks(league) == []


async def test_a_press_in_a_channel_with_no_wizard_is_answered(tmp_path):
    """Kimi's view, pressed in a channel that outlived its wizard (6499): acting on it would
    advance nothing. He is told "Wizard session not found.", the refusal is recorded, and
    nothing is asked of the queue."""
    league = await _team_step(tmp_path)

    press = await _press_team(_view(league), 0, _press(league, channel_id=NO_WIZARD_CHANNEL))

    assert "Wizard session not found" in _answered(press)
    _refused_line(league, "Wizard session not found.")
    assert await _changes(league) == []


async def test_a_wizard_with_no_snapshot_is_answered(tmp_path):
    """The channel's wizard carries no configuration snapshot, so there are no team names to
    resolve against: reading `.team_names` off `None` is the crash this refusal replaces. Kimi
    is told "Wizard session not found.", the refusal is recorded, and nothing is asked."""
    from unittest.mock import AsyncMock

    league = await _team_step(tmp_path)
    league.bot.wizard_service.get_wizard_by_channel = AsyncMock(return_value=SimpleNamespace(
        discord_user_id=str(SIGNING_UP), draft_answers={}, config_snapshot=None,
    ))

    press = await _press_team(_view(league), 0, _press(league))

    assert "Wizard session not found" in _answered(press)
    _refused_line(league, "Wizard session not found.")
    assert await _changes(league) == []


@pytest.mark.parametrize("state", ["COLLECTING_PREFERRED_TEAMMATE", "COLLECTING_NOTES"],
                         ids=["teammate-step", "notes-step"])
async def test_a_team_pressed_on_a_step_already_answered_is_refused_and_recorded(tmp_path, state):
    """Kimi has moved on to the teammate question, or on to his notes, and scrolls up to press
    a team button on the team step's old message. The pick is judged when the queue reaches it:
    it changes nothing, nothing is recorded, his wizard stays on the step he had reached, Kimi
    is told "That step has already been answered.", and one ⛔ line records the refusal."""
    league = await _team_step(tmp_path, state=state)

    press = await _press_team(_view(league), 0, _press(league))
    await run_queue(league.bot)

    assert "That step has already been answered." in _answered(press)
    _refused_line(league, "That step has already been answered.")
    assert await _state(league) == state
    assert await _picks(league) == []


# ---------------------------------------------------------------------------
# Whose buttons these are
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("press", ["team", "no_preference", "cancel"])
async def test_only_the_driver_whose_wizard_it_is_may_press(tmp_path, press):
    """A private channel can have a manager added to it, and a wizard advanced by somebody
    else puts another person's answers into a driver's signup. Member 9999 presses a button
    on Kimi's team step: he is told the button is not for him, the refusal is recorded, and
    nothing is asked of the queue."""
    league = await _team_step(tmp_path)
    view = _view(league)
    interaction = _press(league, by=SOMEONE_ELSE)

    if press == "team":
        await _press_team(view, 0, interaction)
    elif press == "no_preference":
        await view._no_preference_callback(interaction)
    else:
        await view._cancel_callback(interaction)

    assert "not for you" in _answered(interaction)
    _refused_line(league, "This button is not for you.")
    assert await _changes(league) == []
    assert await _driver_state(league) == "PENDING_SIGNUP_COMPLETION"


async def test_after_a_restart_the_owner_is_found_by_channel(tmp_path):
    """The view is re-registered at start with no account, only the change queue, so the
    channel is the only thing tying the buttons to a person: Kimi presses Ferrari on it, the
    pick is asked of the queue as his, and once it runs Ferrari is his pick."""
    league = await _team_step(tmp_path)

    await _press_team(_view(league, registered=True), 0, _press(league))
    await run_queue(league.bot)

    assert await _picks(league) == ["Ferrari"]


async def test_a_restarted_view_in_a_channel_with_no_wizard_refuses(tmp_path):
    """A re-registered view pressed in a channel with no wizard: there is nobody to attribute
    the press to, and `None` must not be treated as a matching account. The press is told the
    button is not for them, recorded, and asks nothing."""
    league = await _team_step(tmp_path)

    press = await _press_team(_view(league, registered=True), 0,
                              _press(league, channel_id=NO_WIZARD_CHANNEL))

    assert "not for you" in _answered(press)
    _refused_line(league, "This button is not for you.")
    assert await _changes(league) == []


# ---------------------------------------------------------------------------
# No Preference, and Cancel
# ---------------------------------------------------------------------------


async def test_no_preference_is_recorded_as_an_answer(tmp_path):
    """Not a skip: Kimi presses No Preference with no picks, and once the queue runs his picks
    are recorded as none and his wizard moves on to the teammate question."""
    league = await _team_step(tmp_path)

    await _view(league)._no_preference_callback(_press(league))
    await run_queue(league.bot)

    assert await _picks(league) == []
    assert await _state(league) == "COLLECTING_PREFERRED_TEAMMATE"


async def test_no_preference_advances_the_wizard_the_same_way_a_team_does(tmp_path):
    """In two leagues Kimi presses Ferrari in one and No Preference in the other. Each asks the
    same change, signup.answer, on the lane of his signup channel — so a later change to how a
    pick is handled cannot leave "no preference" behind."""
    team = await _team_step(tmp_path / "team")
    none = await _team_step(tmp_path / "none")

    await _press_team(_view(team), 0, _press(team))
    await _view(none)._no_preference_callback(_press(none))

    [asked_by_team] = await _changes(team)
    [asked_by_none] = await _changes(none)
    for asked in (asked_by_team, asked_by_none):
        assert asked["kind"] == "signup.answer"
        assert asked.get("lane") == KIMI_LANE


async def test_cancelling_withdraws_the_signup(tmp_path):
    """Kimi presses Cancel Signup on the team step. It asks signup.end of the league lane, and
    once the queue runs he is Not Signed Up."""
    league = await _team_step(tmp_path)

    await _view(league)._cancel_callback(_press(league))

    assert [(asked["kind"], asked.get("lane") or "league")
            for asked in await _changes(league)] == [("signup.end", "league")]
    await run_queue(league.bot)
    assert await _driver_state(league) == "NOT_SIGNED_UP"


@pytest.mark.parametrize("press", [
    pytest.param("team"),
    pytest.param("no_preference"),
    "cancel",
])
async def test_every_press_is_acknowledged_privately_as_under_way(tmp_path, press):
    """Each of Kimi's presses on the team step (a team, No Preference, Cancel Signup) is
    answered at once, privately, that it is under way (decision 8), before anything is done."""
    league = await _team_step(tmp_path)
    view = _view(league)
    interaction = _press(league)

    if press == "team":
        await _press_team(view, 0, interaction)
    elif press == "no_preference":
        await view._no_preference_callback(interaction)
    else:
        await view._cancel_callback(interaction)

    assert acknowledgement(interaction).startswith("⏳")
    assert interaction.response.send_message.await_args.kwargs.get("ephemeral") is True
    assert await _picks(league) == []
