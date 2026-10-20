"""Choosing a parameter — sending a driver back to one question, on the change queue.

Issue #208. `tests/signup/test_correction_parameter_timeout.py` covers the window *lapsing*, and
`tests/signup/test_correction_parameter_view.py` the panel and that each button re-opens its own
step; this file holds what the choice does to the driver's signup once the queue reaches it. On
the queue (#439, slice 6b, C86) a press on the correction choices asks
`signup.correction.choose` on the league lane — Kimi's state, the step re-opened, a fresh 24-hour
deadline armed by a job of its own — which asks `signup.recollect` back of his lane for the
permission and the prompt.

**It re-opens exactly one question, and the wizard must not run on from there.** The
`_is_correction` flag is what the last answer's turn reads to commit instead of continuing, so
without it a driver asked to fix their platform would be walked through every subsequent question
again. `test_the_wizard_is_marked_as_a_correction` holds it.

**Two of the nine parameters need state reset beyond the wizard's step**, and both are the kind of
thing a reader trims: *lap times* go back to the first track, and *preferred teams* clears its
sub-step counter and accumulated picks.

**The write permission is set for the step being returned to, not merely restored.** A driver
returned to a typed question with typing revoked cannot answer at all.

**The reason is consumed.** It was written down when the manager asked for changes, and appears
exactly once — in the prompt the driver reads.

The league is `tests/signup/test_review_changes.py`'s: Kimi (105) awaiting a correction parameter,
the choices posted a minute ago at manager 77's request, in his signup channel (6405), which
records what it is sent and its permission edits. His wizard's configuration names two tracks
and two teams, and stands at the third lap-time track.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import pytest

from tests.signup.test_review_changes import (
    INACTIVITY_TIMER,
    KIMI_CHANNEL,
    _answered,
    _armed,
    _driver_state,
    _league,
    _press_choice,
    _texts,
    _wizard,
)
from tests.support.change_queue import run_queue, updated_reply
from tests.support.season_league import SIGNING_UP, SeasonLeague

_ENDED = "This correction request has ended. Nothing was changed."

#: The configuration Kimi's wizard started against.
SNAPSHOT = {
    "nationality_required": True,
    "time_type": "TIME_TRIAL",
    "time_image_required": False,
    "selected_track_ids": ["1", "2"],
    "team_names": ["Alpha", "Beta"],
    "slots": [],
}


async def _awaiting(tmp_path: Any, *, state: str = "AWAITING_CORRECTION_PARAMETER",
                    **draft: Any) -> SeasonLeague:
    """Kimi at *state* (awaiting a correction parameter unless given), the choices posted a
    minute ago by 77, his wizard at the third lap-time track holding *draft* beside the
    manager who asked."""
    league = await _league(tmp_path, state=state, offered_ago=timedelta(minutes=1))
    answers = {"_correction_requested_by": "77", **draft}
    await league.write(
        "UPDATE signup_wizard_records SET config_snapshot_json = ?, draft_answers_json = ?, "
        "current_lap_track_index = 2 WHERE discord_user_id = ?",
        json.dumps(SNAPSHOT), json.dumps(answers), str(SIGNING_UP),
    )
    return league


async def _choose(league: SeasonLeague, parameter: str) -> Any:
    """Manager 77 presses *parameter* among the choices; the queue then runs."""
    press = await _press_choice(league, parameter)
    await run_queue(league.bot)
    return press


async def _draft(league: SeasonLeague) -> dict[str, Any]:
    return dict(json.loads((await _wizard(league))["draft_answers_json"] or "{}"))


def _typing(league: SeasonLeague) -> list[bool]:
    """Each permission edit of Kimi's channel's typing, in order."""
    return [call.kwargs["send_messages"]
            for call in league.channel(KIMI_CHANNEL).set_permissions.call_args_list
            if "send_messages" in call.kwargs]


# ---------------------------------------------------------------------------
# A request that has ended
# ---------------------------------------------------------------------------


async def test_a_driver_with_no_wizard_is_left_alone(tmp_path):
    """Kimi's wizard is gone when manager 77 presses Platform. The choice is refused, 77 told
    why rather than that Kimi is being re-collected, and nothing changes: Kimi still awaits a
    correction parameter and nothing is posted in his channel."""
    league = await _awaiting(tmp_path)
    await league.write("DELETE FROM signup_wizard_records WHERE discord_user_id = ?",
                       str(SIGNING_UP))

    press = await _choose(league, "platform")

    assert "re-collect" not in (updated_reply(press) or "").lower()
    assert await _driver_state(league) == "AWAITING_CORRECTION_PARAMETER"
    assert _texts(league) == []


@pytest.mark.parametrize(
    "state",
    ["PENDING_ADMIN_APPROVAL", "PENDING_DRIVER_CORRECTION", "UNASSIGNED", "NOT_SIGNED_UP"],
    ids=["window-lapsed", "already-chosen", "approved", "rejected"],
)
async def test_a_request_that_has_ended_is_refused_and_changes_nothing(tmp_path, state):
    """Manager 77 presses Platform after the correction request has ended: its five minutes
    lapsed and Kimi is back awaiting review, another parameter was already chosen, or the signup
    was approved or rejected. The choice is refused with "This correction request has ended.
    Nothing was changed." and changes nothing: Kimi stays at *state*, his wizard's step is
    untouched, no 24-hour deadline is armed and nothing is posted in his channel (D2)."""
    league = await _awaiting(tmp_path, state=state)

    press = await _choose(league, "platform")

    assert _ENDED in _answered(press)
    assert await _driver_state(league) == state
    assert (await _wizard(league))["wizard_state"] == "UNENGAGED"
    assert _armed(league, INACTIVITY_TIMER) == []
    assert _texts(league) == []


# ---------------------------------------------------------------------------
# The correction itself
# ---------------------------------------------------------------------------


async def test_the_driver_is_asked_to_correct_rather_than_to_sign_up_again(tmp_path):
    """Manager 77 presses Platform; once the queue runs Kimi is Pending Driver Correction, not
    back at the start of signing up."""
    league = await _awaiting(tmp_path)

    await _choose(league, "platform")

    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"


async def test_the_wizard_is_marked_as_a_correction(tmp_path):
    """The last answer's turn reads this to commit instead of continuing. Without it a driver
    asked to fix one answer is walked through every later question again. Once the queue runs
    Kimi's wizard is marked as a correction."""
    league = await _awaiting(tmp_path)

    await _choose(league, "platform")

    assert (await _draft(league))["_is_correction"] is True


async def test_a_fresh_inactivity_deadline_is_armed(tmp_path):
    """Kimi now has 24 hours to answer, the same as any other step: once the queue runs his
    24-hour inactivity timer is armed, a day from now."""
    league = await _awaiting(tmp_path)

    await _choose(league, "platform")

    [armed] = _armed(league, INACTIVITY_TIMER)
    assert armed[1] == str(SIGNING_UP)
    assert armed[2] == league.clock.now + timedelta(hours=24)


# ---------------------------------------------------------------------------
# The two parameters needing more than a step change
# ---------------------------------------------------------------------------


async def test_correcting_lap_times_starts_again_from_the_first_track(tmp_path):
    """Kimi's wizard stands at the third lap-time track. Left there, he would be asked about the
    track after the last one he answered, or about none at all. Once Lap Times is chosen and the
    queue runs, his wizard is back at the first track."""
    league = await _awaiting(tmp_path)

    await _choose(league, "lap_times")

    assert (await _wizard(league))["current_lap_track_index"] == 0


async def test_correcting_preferred_teams_clears_the_previous_picks(tmp_path):
    """Kimi had picked Alpha and Beta, two picks into the loop. The loop appends, so without the
    reset his new choices would join the ones the league rejected: once Preferred Teams is chosen
    and the queue runs, his picks are empty and the loop's counter is gone."""
    league = await _awaiting(tmp_path, preferred_teams=["Alpha", "Beta"], _pref_teams_step=2)

    await _choose(league, "preferred_teams")

    draft = await _draft(league)
    assert draft["preferred_teams"] == []
    assert "_pref_teams_step" not in draft


async def test_correcting_another_parameter_leaves_the_lap_index_alone(tmp_path):
    """The reset is specific to lap times; Kimi correcting his notes has not unanswered his lap
    times, and his wizard stays at the third track."""
    league = await _awaiting(tmp_path)

    await _choose(league, "notes")

    assert (await _wizard(league))["current_lap_track_index"] == 2


# ---------------------------------------------------------------------------
# The prompt, and the write permission
# ---------------------------------------------------------------------------


async def test_the_driver_is_prompted_in_their_channel_and_named(tmp_path):
    """Manager 77 presses Platform ID; once the queue runs one prompt in Kimi's channel mentions
    him and asks for his platform id."""
    league = await _awaiting(tmp_path)

    await _choose(league, "platform_id")

    [prompt] = _texts(league)
    assert f"<@{SIGNING_UP}>" in prompt
    assert "platform id" in prompt.lower()


async def test_the_reason_the_manager_gave_reaches_the_driver(tmp_path):
    """It is the whole point of asking rather than rejecting: the reason 77 gave, "Your platform
    ID does not match your account", is in Kimi's prompt."""
    league = await _awaiting(
        tmp_path, _correction_reason="Your platform ID does not match your account"
    )

    await _choose(league, "platform_id")

    assert "does not match your account" in "\n".join(_texts(league))


async def test_the_reason_is_consumed_so_a_later_correction_cannot_reuse_it(tmp_path):
    """Popped, not read: once the queue has run the choice, Kimi's wizard holds no reason, so a
    second correction cannot tell him to fix what he already fixed."""
    league = await _awaiting(tmp_path, _correction_reason="Wrong platform")

    await _choose(league, "platform_id")

    assert "_correction_reason" not in await _draft(league)


async def test_no_reason_gives_no_reason_line(tmp_path):
    """With no reason given, Kimi's prompt carries no "Reason:" line."""
    league = await _awaiting(tmp_path)

    await _choose(league, "platform_id")

    assert "**Reason:**" not in "\n".join(_texts(league))


async def test_returning_to_a_button_step_revokes_typing(tmp_path):
    """Platform is answered by a button, so once the queue runs Kimi's typing in his channel is
    revoked, and never granted."""
    league = await _awaiting(tmp_path)

    await _choose(league, "platform")

    assert _typing(league) == [False]


async def test_returning_to_a_typed_step_restores_typing(tmp_path):
    """The case that matters. Notes are typed, so once the queue runs Kimi's typing in his
    channel is granted, and never revoked: with it revoked he could not answer at all, and the
    correction would stall until his 24 hours ended his signup."""
    league = await _awaiting(tmp_path)

    await _choose(league, "notes")

    assert _typing(league) == [True]


async def test_a_missing_channel_still_records_the_correction(tmp_path):
    """Manager 77 presses Platform and Kimi's signup channel is deleted before the queue reaches
    the choice. The driver's state is the league's record, the prompt only how he learns of it:
    once the queue runs Kimi is Pending Driver Correction, though no prompt reached him."""
    league = await _awaiting(tmp_path)

    await _press_choice(league, "platform")
    league.remove_channel(KIMI_CHANNEL)
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
