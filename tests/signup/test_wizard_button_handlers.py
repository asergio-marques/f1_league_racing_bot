"""The signup wizard's preferred-teams loop, answered one button press at a time.

Issue #208. **Preferred teams is a sub-step loop and the only button step with real logic.** A
driver picks up to three teams, one press at a time; each pick narrows the choices offered next;
and **No Preference** finishes early with however many picks have accumulated. Three things end
the loop — three picks taken, no teams left to offer, or No Preference — and all three are
pinned, because each ends it by a different route and a reader collapsing them would strand a
driver on a step with no way forward.

The sub-step counter lives in `draft_answers` under `_pref_teams_step` and is **removed** when
the loop ends. It is bookkeeping, not an answer, and leaving it behind would carry it into the
committed signup.

**On the change queue (#439, slice 6b, C82).** A press asks `signup.answer` of the lane of the
driver's signup channel, and the pick is saved, judged and the next sub-step posted when the
queue reaches it. These tests press the team buttons on a view handed the queue and run it,
in `tests/signup/test_preferred_teams_view.py`'s league: Kimi (105) part-way through signing
up, his wizard at the team step offering Ferrari, Mercedes and McLaren, in his signup channel.

The other button steps moved with the views: platform and driver type
(`test_signup_wizard_views.py::test_each_platform_button_reports_its_own_platform`,
`::test_each_driver_type_button_reports_its_own_type`), No Preference for a teammate
(`::test_no_preference_answers_the_teammate_question`), a button on a step already answered
(`::test_a_button_on_a_step_already_answered_is_refused_and_recorded`), a team button pressed
once the wizard has moved on to the teammate or notes step
(`test_preferred_teams_view.py::test_a_team_pressed_on_a_step_already_answered_is_refused_and_recorded`),
a team picked twice
(`::test_a_team_pressed_again_on_an_earlier_message_is_refused_and_recorded`), a press with no
wizard (`::test_a_press_in_a_channel_with_no_wizard_is_answered`), a channel the
next question cannot be posted in
(`test_signup_answer_change.py::test_a_refused_next_question_stops_the_queue_with_the_answer_saved`),
the answer's line (`::test_a_button_answer_s_line_records_the_answer`) and the correction's
(`test_wizard_commit.py::test_a_button_answer_that_ends_a_correction_writes_only_correction_submitted`).
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from tests.signup.test_preferred_teams_view import (
    TEAM_STEP,
    _picks,
    _press,
    _press_team,
    _team_step,
    _view,
)
from tests.signup.test_wizard_advance import INACTIVITY_TIMER, KIMI_CHANNEL, _state, _wizard
from tests.support.change_queue import run_queue
from tests.support.season_league import SIGNING_UP, SeasonLeague


async def _reshape(league: SeasonLeague, *, team_names: list[str] | None = None,
                   draft: dict[str, Any] | None = None) -> None:
    """Give Kimi's wizard *team_names* to offer, and *draft* added to its answers, saved as
    `_team_step` saves it."""
    from leaguebot.core.db.database import get_connection
    from leaguebot.signup.services.signup_module_service import SignupModuleService

    signups = SignupModuleService(league.db_path)
    wizard = await signups.get_wizard_by_channel(KIMI_CHANNEL)
    assert wizard is not None and wizard.config_snapshot is not None
    if team_names is not None:
        wizard.config_snapshot.team_names = list(team_names)
    wizard.draft_answers.update(draft or {})
    save_on = getattr(signups, "save_wizard_on", None)
    if save_on is None:
        await signups.save_wizard(wizard)
    else:
        async with get_connection(league.db_path) as db:
            await save_on(db, wizard)
            await db.commit()


async def _draft(league: SeasonLeague) -> dict[str, Any]:
    wizard = await _wizard(league)
    assert wizard is not None
    return dict(json.loads(wizard["draft_answers_json"]))


async def _pick(league: SeasonLeague, label: str, *, offered: tuple[str, ...]) -> None:
    """Kimi presses *label* on the message offering *offered*, and the queue runs."""
    await _press_team(_view(league, excluded=None), offered.index(label),
                      _press(league, labels=offered))
    await run_queue(league.bot)


async def _no_preference(league: SeasonLeague) -> None:
    await _view(league)._no_preference_callback(_press(league))
    await run_queue(league.bot)


def _last_post(league: SeasonLeague) -> tuple[str, Any]:
    """The last message posted in Kimi's channel, and the view it carries."""
    made = league.channels[KIMI_CHANNEL]
    call = made.send.call_args
    return (call.args[0] if call.args else call.kwargs.get("content", "")), call.kwargs.get("view")


# ---------------------------------------------------------------------------
# The loop continues
# ---------------------------------------------------------------------------


async def test_a_first_team_pick_is_recorded_and_the_next_is_offered(tmp_path):
    """The loop continues rather than advancing: a driver picking one team has not yet said
    they are finished. Kimi presses Ferrari; once the queue runs it is saved, the counter
    reads 1, he is still on the team step, and one message offering the next pick is posted."""
    league = await _team_step(tmp_path)

    await _pick(league, "Ferrari", offered=("Ferrari", "Mercedes", "McLaren"))

    draft = await _draft(league)
    assert draft["preferred_teams"] == ["Ferrari"]
    assert draft["_pref_teams_step"] == 1
    assert await _state(league) == TEAM_STEP
    assert len(league.channels[KIMI_CHANNEL].texts) == 1


async def test_the_next_prompt_names_the_ordinal_and_the_picks_so_far(tmp_path):
    """The driver is several presses into a list they cannot see; the prompt is the only
    record of what they have already chosen."""
    league = await _team_step(tmp_path)

    await _pick(league, "Ferrari", offered=("Ferrari", "Mercedes", "McLaren"))

    prompt, _ = _last_post(league)
    assert "2nd" in prompt
    assert "Ferrari" in prompt


async def test_a_team_already_picked_is_not_offered_again(tmp_path):
    """`excluded` is what stops a driver naming the same team as their first and second
    choice, which would waste a pick: the next sub-step's buttons leave Ferrari out."""
    league = await _team_step(tmp_path)

    await _pick(league, "Ferrari", offered=("Ferrari", "Mercedes", "McLaren"))

    _, view = _last_post(league)
    labels = [child.label for child in view.children if getattr(child, "label", None)]
    assert "Ferrari" not in labels
    assert {"Mercedes", "McLaren"} <= set(labels)


async def test_a_continuing_loop_saves_the_wizard_and_resets_the_timeout(tmp_path):
    """Each press is activity. Without the reset a driver working through three picks could
    be timed out mid-choice: his 24 hours are armed again from now, and his last activity is
    saved as now."""
    league = await _team_step(tmp_path)
    league.bot.scheduler_service.arm_once.reset_mock()

    await _pick(league, "Ferrari", offered=("Ferrari", "Mercedes", "McLaren"))

    armed = [call.args for call in league.bot.scheduler_service.arm_once.call_args_list
             if call.args and call.args[0] == INACTIVITY_TIMER]
    assert armed == [(INACTIVITY_TIMER, str(SIGNING_UP), league.clock.now + timedelta(hours=24))]
    wizard = await _wizard(league)
    assert wizard is not None and wizard["last_activity_at"] == league.clock.now.isoformat()


# ---------------------------------------------------------------------------
# The three ways the loop ends
# ---------------------------------------------------------------------------


async def test_a_third_pick_ends_the_loop(tmp_path):
    """Three is the limit, so the third press moves the wizard on to the teammate question
    rather than offering a fourth."""
    league = await _team_step(tmp_path, picks=["Ferrari", "Mercedes"])
    await _reshape(league, draft={"_pref_teams_step": 2})

    await _pick(league, "McLaren", offered=("McLaren",))

    assert await _picks(league) == ["Ferrari", "Mercedes", "McLaren"]
    assert await _state(league) == "COLLECTING_PREFERRED_TEAMMATE"


async def test_running_out_of_teams_ends_the_loop(tmp_path):
    """A league with two teams cannot offer a third pick. Without this the driver would be
    shown a prompt with no buttons on it and no way forward: Kimi's second pick, Mercedes,
    moves him on to the teammate question."""
    league = await _team_step(tmp_path, picks=["Ferrari"])
    await _reshape(league, team_names=["Ferrari", "Mercedes"], draft={"_pref_teams_step": 1})

    await _pick(league, "Mercedes", offered=("Mercedes",))

    assert await _picks(league) == ["Ferrari", "Mercedes"]
    assert await _state(league) == "COLLECTING_PREFERRED_TEAMMATE"


async def test_no_preference_ends_the_loop_keeping_the_picks_so_far(tmp_path):
    """The third way out. A driver who wanted only one team says so by pressing No
    Preference, and that first pick must survive — discarding it would lose the one
    preference they expressed."""
    league = await _team_step(tmp_path, picks=["Ferrari"])
    await _reshape(league, draft={"_pref_teams_step": 1})

    await _no_preference(league)

    assert await _picks(league) == ["Ferrari"]
    assert await _state(league) == "COLLECTING_PREFERRED_TEAMMATE"


@pytest.mark.parametrize("ends_by", ["third-pick", "no-preference"])
async def test_the_sub_step_counter_is_cleared_when_the_loop_ends(tmp_path, ends_by):
    """It is bookkeeping, not an answer. Left in `draft_answers` it would be carried into
    the committed signup as though the driver had told the league something."""
    league = await _team_step(tmp_path, picks=["Ferrari", "Mercedes"])
    await _reshape(league, draft={"_pref_teams_step": 2})

    if ends_by == "third-pick":
        await _pick(league, "McLaren", offered=("McLaren",))
    else:
        await _no_preference(league)

    assert "_pref_teams_step" not in await _draft(league)

