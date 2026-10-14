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

**On the change queue (#439, slice 6b, C86).** The panel is handed the change queue when it is
built (A23), and a press asks `signup.correction.choose` of it, acknowledged privately and the
acknowledgement updated with its outcome; the choice's work (Kimi's state, the re-collect prompt)
is done when the queue reaches it. These tests drive the panel through a real queue: the league is
`tests/signup/test_review_changes.py`'s, Kimi (105) awaiting a correction parameter, the choices
posted a minute ago at manager 77's request, in his signup channel (6405).

Its class docstring still says "Restricted to tier-2 role or Manage Guild permission". The
Manage Guild half is stale — `_may_review_signup` stopped admitting it when the two-tier model
landed, and its own docstring records that. These tests hold the behaviour, which is the
two-tier check.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from leaguebot.signup.cogs.admin_review_cog import CorrectionParameterView
from tests.signup.test_review_changes import (
    CHOOSE_KIND,
    KIMI_CHANNEL,
    NO_CHOOSE_CHANGE,
    RECOLLECT_KIND,
    REVIEWER,
    _answered,
    _changes_of,
    _driver_state,
    _league,
    _manager,
    _wizard,
)
from tests.support.change_queue import member_interaction, run_queue
from tests.support.season_league import SIGNING_UP, SeasonLeague

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

#: The wizard step that re-collects each key.
COLLECTING = {key: f"COLLECTING_{key.upper()}" for _, key in PARAMETERS}
COLLECTING["lap_times"] = "COLLECTING_LAP_TIME"

#: A channel holding no signup wizard.
NO_WIZARD_CHANNEL = 6499

_ENDED = "This correction request has ended. Nothing was changed."

NO_QUEUE_AT_CONSTRUCTION = (
    "#439: the correction choices are built with the bot, not handed the change queue (A23)"
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _awaiting(tmp_path: Any, **kwargs: Any) -> SeasonLeague:
    """Kimi awaiting a correction parameter, the choices posted a minute ago by 77."""
    kwargs.setdefault("state", "AWAITING_CORRECTION_PARAMETER")
    kwargs.setdefault("offered_ago", timedelta(minutes=1))
    return await _league(tmp_path, **kwargs)


def _pressed_by(league: SeasonLeague, user_id: int, *, channel_id: int = KIMI_CHANNEL) -> Any:
    """A press by *user_id* in *channel_id*, now."""
    who = _manager(league) if user_id == REVIEWER else league._member(user_id)
    press = member_interaction(league.bot, user=who, created_at=league.clock.now)
    press.guild = league.guild
    press.channel_id = channel_id
    press.channel = league.channels.get(channel_id) or MagicMock()
    return press


async def _choose(
    league: SeasonLeague,
    label: str,
    *,
    by: int = REVIEWER,
    driver: str | None = str(SIGNING_UP),
    channel_id: int = KIMI_CHANNEL,
) -> Any:
    """*by* presses *label* on a panel built for *driver* (none: a panel rebuilt at a restart)."""
    view = CorrectionParameterView(driver, change_queue=league.bot.change_queue)
    press = _pressed_by(league, by, channel_id=channel_id)
    await _button(view, label).callback(press)
    return press


def _payload(change: dict[str, Any]) -> dict[str, Any]:
    payload = change["payload"]
    return json.loads(payload) if isinstance(payload, str) else dict(payload)


def _button(view, label: str):
    return next(c for c in view.children if getattr(c, "label", None) == label)


# ---------------------------------------------------------------------------
# The panel itself
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_QUEUE_AT_CONSTRUCTION)
async def test_a_button_is_offered_for_every_question_the_wizard_asks():
    """A question with no button could never be corrected, and the manager would have to
    reject the whole signup to get it changed."""
    view = CorrectionParameterView("4242", change_queue=MagicMock())

    labels = [c.label for c in view.children if getattr(c, "label", None)]
    for label, _ in PARAMETERS:
        assert label in labels


@pytest.mark.xfail(strict=True, reason=NO_QUEUE_AT_CONSTRUCTION)
async def test_each_button_carries_its_own_custom_id():
    """Persistent views are matched by custom id, so two buttons sharing one would make
    which parameter a press means depend on the order Discord matched them."""
    view = CorrectionParameterView("4242", change_queue=MagicMock())

    ids = [c.custom_id for c in view.children if getattr(c, "custom_id", None)]
    assert len(ids) == len(set(ids))
    assert "correct_nationality" in ids


# ---------------------------------------------------------------------------
# The closure
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
@pytest.mark.parametrize("label,key", PARAMETERS, ids=[k for _, k in PARAMETERS])
async def test_each_button_sends_back_its_own_parameter(tmp_path, label, key):
    """The nine callbacks are built in a loop. A loop variable captured by reference would
    give every button the *last* parameter, so pressing "Nationality" would ask the driver
    to re-enter their notes — and nothing else in the suite would notice. Manager 77 presses
    *label*; once the queue runs, Kimi's wizard is at the step that re-collects that answer."""
    league = await _awaiting(tmp_path)

    await _choose(league, label)
    await run_queue(league.bot)

    assert await _driver_state(league) == "PENDING_DRIVER_CORRECTION"
    assert (await _wizard(league))["wizard_state"] == COLLECTING[key]


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
@pytest.mark.parametrize("label,key", PARAMETERS, ids=[k for _, k in PARAMETERS])
async def test_the_driver_is_told_which_answer_to_give_again(tmp_path, label, key):
    """Named in the league's words rather than the stored key — "platform id", not
    `platform_id`: the manager's acknowledgement, updated once the queue has run the choice,
    names the answer re-collected."""
    league = await _awaiting(tmp_path)

    press = await _choose(league, label)
    await run_queue(league.bot)

    assert key.replace("_", " ") in _answered(press).lower()


# ---------------------------------------------------------------------------
# Who may press these
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_driver_cannot_choose_which_of_their_own_answers_to_re_open(tmp_path):
    """The panel lands in the driver's own channel, and re-opening an answer lets them
    change it. The check is easy to assume has already happened — a manager pressed a
    button to get here — but a different person can press this one. Kimi himself presses
    Nationality: he is told he has insufficient permissions, the refusal is recorded in the
    log channel, no choice is asked of the queue, and he still awaits a correction parameter."""
    league = await _awaiting(tmp_path)

    press = await _choose(league, "Nationality", by=SIGNING_UP)
    await run_queue(league.bot)

    assert "Insufficient permissions" in _answered(press)
    assert "⛔" in league.log() and "Insufficient permissions" in league.log()
    assert await _changes_of(league, CHOOSE_KIND) == []
    assert await _driver_state(league) == "AWAITING_CORRECTION_PARAMETER"


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_choice_pressed_after_the_request_ended_is_refused_and_recorded(tmp_path):
    """Manager 77 presses "Platform" on Kimi's correction panel after the request has ended:
    its five minutes lapsed, another parameter was already chosen, or the signup was approved
    or rejected — Kimi is back awaiting approval. The press answers "⛔ This correction request
    has ended. Nothing was changed.", records the refusal in the log channel, never says it is
    re-collecting anything (D2), and nothing is re-collected."""
    league = await _league(tmp_path, state="PENDING_ADMIN_APPROVAL")

    press = await _choose(league, "Platform")
    await run_queue(league.bot)

    answered = _answered(press)
    assert f"⛔ {_ENDED}" in answered, answered
    assert "✅" not in answered, answered
    assert _ENDED in league.log()
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert await _changes_of(league, RECOLLECT_KIND) == []


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_choice_that_loses_the_race_to_the_request_ending_is_refused(tmp_path):
    """Manager 77 presses "Platform" on Kimi's correction panel while the request is still
    open, and the choice is asked of the queue; before the queue reaches it the request ends
    (Kimi is returned to awaiting approval, as the lapse returns him). The choice is judged
    again when it runs: the press answers "⛔ This correction request has ended. Nothing was
    changed.", and Kimi's signup is left as it was: still awaiting approval, his wizard not
    moved to any re-collecting step, and no prompt to re-submit asked of his lane."""
    league = await _awaiting(tmp_path)

    press = await _choose(league, "Platform")
    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_ADMIN_APPROVAL' "
        "WHERE discord_user_id = ?", str(SIGNING_UP),
    )
    await run_queue(league.bot)

    answered = _answered(press)
    assert f"⛔ {_ENDED}" in answered, answered
    assert "✅" not in answered, answered
    assert await _driver_state(league) == "PENDING_ADMIN_APPROVAL"
    assert (await _wizard(league))["wizard_state"] == "UNENGAGED"
    assert await _changes_of(league, RECOLLECT_KIND) == []


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_choice_writes_one_line_naming_the_parameter(tmp_path):
    """Manager 77 presses "Platform" on Kimi's correction panel while the request is still
    open. Once the queue runs, Kimi is sent back to the platform question, and one line records
    the choice, naming the manager, the button and whose signup review it sits on, and the
    answer by its button's label, as the "Correction submitted" line names it (owner,
    2026-10-01, Gate 3)."""
    league = await _awaiting(tmp_path)

    await _choose(league, "Platform")
    await run_queue(league.bot)

    lines = [line for line in league.bot.log_channel.sent
             if "Correction requested: Platform" in line]
    assert len(lines) == 1, league.log()
    assert f"<@{REVIEWER}>" in lines[0]
    assert "the “Platform” button of" in lines[0] and "signup review" in lines[0]


# ---------------------------------------------------------------------------
# Restart recovery
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_panel_rebuilt_after_a_restart_finds_its_driver(tmp_path):
    """The five-minute window can outlive a restart, so the panel has to work without the
    driver it was built with: a panel rebuilt with no driver, pressed in Kimi's channel, finds
    Kimi by the channel's wizard, and once the queue runs his platform is re-collected."""
    league = await _awaiting(tmp_path)

    await _choose(league, "Platform", driver=None)
    await run_queue(league.bot)

    [choose] = await _changes_of(league, CHOOSE_KIND)
    assert str(SIGNING_UP) in json.dumps(_payload(choose))
    assert (await _wizard(league))["wizard_state"] == "COLLECTING_PLATFORM"


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_panel_whose_channel_has_no_wizard_refuses(tmp_path):
    """Acting on `None` would reach the service with no driver and fail somewhere the
    manager could not interpret. A panel rebuilt with no driver is pressed in a channel
    holding no signup wizard: the manager is told the driver could not be identified, the
    refusal is recorded, and nothing is asked of the queue."""
    league = await _awaiting(tmp_path)

    press = await _choose(league, "Platform", driver=None, channel_id=NO_WIZARD_CHANNEL)
    await run_queue(league.bot)

    assert "Could not identify driver" in _answered(press)
    assert "Could not identify driver" in league.log()
    assert await _changes_of(league, CHOOSE_KIND) == []


@pytest.mark.xfail(strict=True, reason=NO_CHOOSE_CHANGE)
async def test_a_stored_driver_is_preferred_over_the_lookup(tmp_path):
    """While the process that posted the panel is still alive there is no need to ask the
    database, and asking would make the panel depend on a wizard row that a withdrawal may
    already have removed. A panel built for Kimi, pressed where no wizard row names the
    channel, still asks Kimi's choice of the queue."""
    league = await _awaiting(tmp_path)

    press = await _choose(league, "Notes", channel_id=NO_WIZARD_CHANNEL)

    assert "Could not identify driver" not in _answered(press)
    [choose] = await _changes_of(league, CHOOSE_KIND)
    assert str(SIGNING_UP) in json.dumps(_payload(choose))
