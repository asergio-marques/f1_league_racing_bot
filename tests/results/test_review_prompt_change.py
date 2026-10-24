"""The review's prompt posted through the round's lane: `results.review.prompt` (#439, slice 6b, C44a).

A round's submission channel is a conversation with a lane of its own (`channel:<id>`). When the
conversation hands its review over to the league (a last paste, a resubmission's swap or Cancel),
`results.review.open` runs on the league lane, putting out the interim results, and asks the
round's lane, as a follow-on in its save, for `results.review.prompt`, which posts the stage prompt
in the channel. A refused prompt stops only that round's lane, and a discarded one asks for the
review again, as the prompt's discard did before. A review put back by the start-up is a command's
work: it runs whole on the league lane, posting its prompt itself.

The league, its channels and its queue are `test_review_open_change.py`'s, round 3 of Pro with its
submission channel 702.
"""
from __future__ import annotations

from typing import Any

import pytest

from leaguebot.core.models.round import RoundStatus
from tests.results.test_review_open_change import (
    DIVISION_ID,
    KIND,
    ROUND_ID,
    SUBMISSION_CHANNEL,
    _channel_row,
    _league,
    _open,
    _prompts,
    _recover,
    _refuse_the_job,
)
from tests.support.change_queue import (
    acknowledgement,
    change_rows,
    discard_job,
    retry_job,
    run_queue,
    stopped_job,
)
from tests.support.review_league import PROMPT, changes_of

PROMPT_KIND = "results.review.prompt"
ROUND_LANE = f"channel:{SUBMISSION_CHANNEL}"


def _lane(change: dict[str, Any]) -> str:
    """The lane a change was saved on; one saved with none is the league's."""
    return change.get("lane") or "league"


def _refuse_the_prompt(league: Any) -> None:
    league.channel(SUBMISSION_CHANNEL).fail_when = (
        lambda _content, kwargs: type(kwargs.get("view")).__name__ == "PenaltyReviewView"
    )


async def _kind_of(db_path: str, change_id: int) -> str:
    [change] = [row for row in await change_rows(db_path) if row["id"] == change_id]
    return change["kind"]


async def test_a_review_opened_from_a_last_paste_posts_its_prompt_on_the_round_s_lane(tmp_path):
    """Round 3 awaits its results; the league manager Alex's last paste asks for its review. The
    review opens on the league lane, and its prompt is asked of round 3's lane, which posts it in
    the submission channel and saves its id."""
    league = await _league(tmp_path, name="prompt_on_lane")

    await _open(league)

    [opened] = await changes_of(league.db_path, KIND)
    assert _lane(opened) == "league"
    [prompt_change] = await changes_of(league.db_path, PROMPT_KIND)
    assert _lane(prompt_change) == ROUND_LANE
    assert prompt_change["state"] == "DONE"
    [prompt] = _prompts(league)
    assert (await _channel_row(league.db_path))["prompt_message_id"] == prompt


async def test_a_refused_prompt_stops_only_the_round_s_lane(tmp_path):
    """Discord refuses round 3's review prompt. Round 3's lane stops at the prompt's post; the
    league lane is not stopped and the review's opening there has finished. Retry on the round's
    lane posts the prompt."""
    league = await _league(tmp_path, name="prompt_refused_on_lane")
    _refuse_the_prompt(league)

    await _open(league)

    stopped = await stopped_job(league.db_path, ROUND_LANE)
    assert stopped is not None
    assert await _kind_of(league.db_path, stopped["change_id"]) == PROMPT_KIND
    assert await stopped_job(league.db_path, "league") is None
    [opened] = await changes_of(league.db_path, KIND)
    assert opened["state"] == "DONE"
    assert _prompts(league) == []

    league.channel(SUBMISSION_CHANNEL).fail_when = None
    await retry_job(league.bot, lane=ROUND_LANE)

    assert await stopped_job(league.db_path) is None
    [prompt] = _prompts(league)
    assert (await _channel_row(league.db_path))["prompt_message_id"] == prompt


async def test_a_discarded_prompt_asks_the_review_again(tmp_path):
    """Discord refuses round 3's review prompt and a league admin discards it on the round's lane.
    The review is asked for again, as the bot, and once Discord takes posts again the fresh request
    puts one prompt up, nothing left stopped."""
    league = await _league(tmp_path, name="prompt_discarded_on_lane")
    _refuse_the_prompt(league)
    await _open(league)

    await discard_job(league.bot, run=False, lane=ROUND_LANE)
    league.channel(SUBMISSION_CHANNEL).fail_when = None
    await run_queue(league.bot)

    asked = await changes_of(league.db_path, KIND)
    assert len(asked) == 2
    assert asked[1]["origin"] == "BOT"
    assert await stopped_job(league.db_path) is None
    [prompt] = _prompts(league)
    assert (await _channel_row(league.db_path))["prompt_message_id"] == prompt


async def test_a_review_put_back_at_a_restart_posts_its_prompt_on_the_league_lane(tmp_path):
    """Round 3 was in its report stage when the bot stopped, its interim results out and its
    prompt 8900 standing. The start-up puts the review back: one change on the league lane posts
    the fresh prompt itself and deletes the old one; nothing is asked of the round's lane."""
    league = await _league(tmp_path, name="prompt_put_back", in_review=True, results_posted=True,
                           prompt=PROMPT,
                           round_status=RoundStatus.AWAITING_REPORT_VERDICTS.value)

    await _recover(league)

    [opened] = await changes_of(league.db_path, KIND)
    assert _lane(opened) == "league"
    assert opened["origin"] == "BOT"
    assert await changes_of(league.db_path, PROMPT_KIND) == []
    [prompt] = _prompts(league)
    assert prompt != PROMPT
    assert PROMPT not in league.channel(SUBMISSION_CHANNEL).messages


PROMPT_CLOSE_UNDISCARDABLE = "#439: the review prompt's closing job may still be discarded"


async def _put_up_the_appeals_prompt(league: Any) -> None:
    """Ask round 3's lane for the appeals stage's prompt, as the reports approval asks it in its
    last save, then run the queue."""
    from leaguebot.core.models.change import ChangeOrigin

    await league.bot.change_queue.ask(
        PROMPT_KIND,
        {"stage": "appeals", "round_id": ROUND_ID, "division_id": DIVISION_ID,
         "channel_id": SUBMISSION_CHANNEL, "takedowns": [], "round_number": 3,
         "division_name": "Pro"},
        what="the appeals review prompt, with the penalty review taken down",
        origin=ChangeOrigin.BOT,
    )
    await run_queue(league.bot)


@pytest.mark.parametrize("stage, review", [
    pytest.param("review", "penalty review", id="review",
                 marks=pytest.mark.xfail(strict=True, reason=PROMPT_CLOSE_UNDISCARDABLE)),
    pytest.param("appeals", "appeals review", id="appeals",
                 marks=pytest.mark.xfail(strict=True, reason=PROMPT_CLOSE_UNDISCARDABLE)),
])
async def test_the_prompt_s_closing_job_cannot_be_discarded(tmp_path, stage, review):
    """Round 3's prompt is put up on its lane: for the penalty review after Alex's last paste, or
    for the appeals review once its reports were approved. The prompt is posted, then the save of
    the change's last job fails, stopping round 3's lane at `close`. A league admin's Discard on it
    is refused, saying round 3 in Pro could be left in its penalty (or appeals) review with no
    prompt, never final, until the bot restarts; the lane stays stopped there. Once the fault is
    lifted, a Retry finishes the change and nothing is left stopped."""
    if stage == "appeals":
        league = await _league(tmp_path, name="appeals_close_undiscardable", in_review=True,
                               results_posted=True, prompt=PROMPT,
                               round_status=RoundStatus.AWAITING_APPEAL_VERDICTS.value)
        await _refuse_the_job(league.db_path, PROMPT_KIND, True)
        await _put_up_the_appeals_prompt(league)
    else:
        league = await _league(tmp_path, name="prompt_close_undiscardable")
        await _refuse_the_job(league.db_path, PROMPT_KIND, True)
        await _open(league)
    stopped = await stopped_job(league.db_path, ROUND_LANE)
    assert stopped is not None and stopped["name"] == "close"

    refused = await discard_job(league.bot, lane=ROUND_LANE)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it round 3 in **Pro** could be left in its "
        f"{review} with no prompt, never final, until the bot restarts. Fix what stopped it and "
        "press **Retry**."
    )
    assert (await stopped_job(league.db_path, ROUND_LANE))["id"] == stopped["id"]

    await _refuse_the_job(league.db_path, PROMPT_KIND, False)
    await retry_job(league.bot, lane=ROUND_LANE)

    assert await stopped_job(league.db_path) is None
    [prompt_change] = await changes_of(league.db_path, PROMPT_KIND)
    assert prompt_change["state"] == "DONE"
