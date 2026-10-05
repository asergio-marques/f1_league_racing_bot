"""Approving a round's appeals through the change queue: `results.appeals.approve` (#439, slice 2).

`results/services/appeals_approval_change.py` carries stage two of a round's penalty review as a
change: `names`, then `apply`, one save holding the corrections, the points, the appeal records,
the standings snapshots from this round on, the round made FINAL with its former drivers marked,
the division's status refreshed, the submission channel's row closed, and the season's wind-down
asked as a change of its own where the division is now done. After it come the republication under
"Final Results", one `announce_verdict` per correction, the appeals prompt's deletion, the
submission channel's deletion (`delete_channel`), and `close`, which writes
`APPEALS_REVIEW_APPROVED`.

The approval is asked for as the appeals review's Approve asks it:
`bot.change_queue.ask("results.appeals.approve", payload, ...)`, the payload carrying the staged
corrections as plain data (`to_payload`) and the appeals prompt pressed on.

The league, its fake channels and the attendance hook's double are `tests.support.review_league`'s:
round 3 of division 11 (Pro) awaits its appeal verdicts, the appeals prompt open in its submission
channel. Everything of the change type is imported inside a test, so this file collects while it
is unbuilt.
"""
from __future__ import annotations

import re

from typing import Any

import pytest

from tests.support.change_queue import (
    acknowledgement,
    discard_job,
    http_error,
    member_interaction,
    restart_queue,
    retry_job,
    run_queue,
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    DIVISION_ID,
    LEWIS,
    RESULTS_CHANNEL,
    ROUND_ID,
    SUBMISSION_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    appeals_prompts,
    changes_of,
    one,
    penalty,
    points_fail,
    review_league,
    round_status,
    run_until_done,
    stopped_at,
    verdict_headings,
)

KIND = "results.appeals.approve"
APPEALS_PROMPT = 8902
NOT_BUILT = "#439: the appeals approval is not yet a change on the queue"


async def _league(tmp_path: Any, **options: Any) -> ReviewLeague:
    """Round 3 awaiting its appeal verdicts, its appeals prompt standing in the submission
    channel; division 11 the season's only division, round 3 its last round still open."""
    league = await review_league(
        tmp_path, round_status="AWAITING_APPEAL_VERDICTS", other_division=False,
        appeals_prompt=APPEALS_PROMPT, **options,
    )
    league.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")
    return league


def _payload(*, staged: Any = None, prompt: int = APPEALS_PROMPT) -> dict[str, Any]:
    """One upheld appeal by default: Lewis's 5-second penalty taken back."""
    staged = [penalty(LEWIS, seconds=-5)] if staged is None else staged
    return {
        "round_id": ROUND_ID,
        "division_id": DIVISION_ID,
        "staged": [correction.to_payload() for correction in staged],
        "appeals_prompt_message_id": prompt,
    }


async def _approve(league: ReviewLeague, **payload: Any) -> Any:
    """Press Approve on round 3's appeals review as the league manager Alex; the queue is not
    yet run. Gives Alex's interaction."""
    interaction = member_interaction(
        league.bot, user=tier_member("manager", display_name="Alex", name="Alex#0001"),
    )
    await league.bot.change_queue.ask(
        KIND, _payload(**payload), interaction=interaction,
        what="✅ Approve on round 3's appeals review",
    )
    return interaction


async def _appeal_penalty(db_path: str) -> int:
    return await one(
        db_path,
        "SELECT appeal_time_penalties_ms FROM race_session_results WHERE driver_user_id = ?",
        LEWIS,
    )


async def _appeal_records(db_path: str) -> int:
    return await one(db_path, "SELECT COUNT(*) FROM appeal_records")


async def _closed(db_path: str) -> int:
    return await one(db_path, "SELECT closed FROM round_submission_channels WHERE round_id = ?",
                     ROUND_ID)


def _channel_deletions(league: ReviewLeague) -> list[int]:
    return [cid for kind, cid, _ in league.events if kind == "delete_channel"]


# ---------------------------------------------------------------------------
# Defect 1: appeals applied twice
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_appeals_approved_then_asked_again_after_a_restart_are_applied_once(tmp_path):
    from leaguebot.results.services.penalty_wizard import _APPEALS_BEING_APPROVED

    league = await _league(tmp_path)
    await _approve(league)
    await run_queue(league.bot)
    assert await _appeal_penalty(league.db_path) == -5000

    await restart_queue(league.bot)
    again = await _approve(league)
    await run_queue(league.bot)

    assert acknowledgement(again) != ""
    assert _APPEALS_BEING_APPROVED not in acknowledgement(again)
    assert len(await changes_of(league.db_path, KIND)) == 1
    assert await _appeal_penalty(league.db_path) == -5000
    assert await _appeal_records(league.db_path) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_press_after_a_stopped_approval_does_not_apply_the_corrections_twice(tmp_path):
    from leaguebot.results.services.penalty_wizard import _APPEALS_BEING_APPROVED

    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(text="Discord is down")
    await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_session_results"

    second = await _approve(league)
    assert acknowledgement(second) == _APPEALS_BEING_APPROVED

    league.channel(RESULTS_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert await _appeal_penalty(league.db_path) == -5000
    assert await _appeal_records(league.db_path) == 1


# ---------------------------------------------------------------------------
# Defect 6: a stop part-way through
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stop_after_the_corrections_are_saved_announces_the_appeals_and_deletes_the_channel_on_restart(
    tmp_path,
):
    league = await _league(tmp_path)
    await _approve(league)
    await run_until_done(league, "apply")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 2
    assert APPEALS_PROMPT not in league.channel(SUBMISSION_CHANNEL).messages
    assert _channel_deletions(league) == [SUBMISSION_CHANNEL]
    assert appeals_prompts(league) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_final_round_leaves_no_review_to_restore(tmp_path):
    league = await _league(tmp_path)
    await _approve(league)
    await run_until_done(league, "apply")

    assert await round_status(league.db_path) == "FINAL"
    assert await _closed(league.db_path) == 1
    assert _channel_deletions(league) == []


# ---------------------------------------------------------------------------
# Defect 3 and defect 11
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_correction_whose_points_cannot_be_recalculated_changes_nothing(tmp_path):
    league = await _league(tmp_path)
    with points_fail():
        await _approve(league)
        await run_queue(league.bot)

    assert await stopped_at(league) == "apply"
    assert await _appeal_penalty(league.db_path) == 0
    assert await _appeal_records(league.db_path) == 0
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert await _closed(league.db_path) == 0


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_final_results_repost_discord_refuses_is_retried(tmp_path):
    league = await _league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(text="Discord is down")
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_session_results"

    league.channel(RESULTS_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    posted = league.sent_to(RESULTS_CHANNEL)
    assert len(posted) == 1
    assert "Final Results" in league.channel(RESULTS_CHANNEL).messages[posted[0]].content
    assert "The round is final" in updated_reply(interaction)


# ---------------------------------------------------------------------------
# Discards and stops
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_channel_deletion_names_the_channel_for_deletion_by_hand(tmp_path):
    league = await _league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).delete_fails = http_error(status=403, text="Missing Access")
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "delete_channel"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    reply = updated_reply(interaction)
    assert f"<#{SUBMISSION_CHANNEL}>" in reply
    assert "by hand" in reply
    assert "APPEALS_REVIEW_APPROVED | Incomplete" in league.log()
    assert await round_status(league.db_path) == "FINAL"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_division_with_no_verdicts_channel_stops_the_queue_at_its_verdict(tmp_path):
    league = await _league(tmp_path, verdicts_channel=False)
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_verdict"
    assert await round_status(league.db_path) == "FINAL"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_appeals_apply_reopens_the_appeals_review(tmp_path):
    league = await _league(tmp_path)
    with points_fail():
        interaction = await _approve(league)
        await run_queue(league.bot)
    assert await stopped_at(league) == "apply"

    await discard_job(league.bot)
    await run_queue(league.bot)

    reopened = await changes_of(league.db_path, "results.appeals.open")
    assert len(reopened) == 1
    assert reopened[0]["origin"] == "BOT"
    assert APPEALS_PROMPT not in league.channel(SUBMISSION_CHANNEL).messages
    prompts = appeals_prompts(league)
    assert len(prompts) == 1
    assert await one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompts[0]
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert "Nothing was changed" in updated_reply(interaction)


# ---------------------------------------------------------------------------
# The round finished
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_round_is_final_and_its_division_status_refreshed_in_the_same_save(tmp_path):
    league = await _league(tmp_path)
    await _approve(league)
    await run_until_done(league, "apply")

    assert await round_status(league.db_path) == "FINAL"
    assert await one(
        league.db_path, "SELECT status FROM divisions WHERE id = ?", DIVISION_ID
    ) == "FINISHED"
    assert await _appeal_penalty(league.db_path) == -5000
    assert league.sent_to(RESULTS_CHANNEL) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_season_is_wound_down_as_a_change_of_its_own_when_the_division_finishes(
    tmp_path,
):
    league = await _league(tmp_path)
    await _approve(league)
    await run_until_done(league, "apply")

    wind_downs = await changes_of(league.db_path, "season.wind_down")
    assert len(wind_downs) == 1
    assert wind_downs[0]["origin"] == "BOT"
    approvals = await changes_of(league.db_path, KIND)
    assert wind_downs[0]["id"] > approvals[0]["id"]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_no_changes_with_nothing_staged_finishes_the_round_through_the_queue(tmp_path):
    league = await _league(tmp_path)
    interaction = await _approve(league, staged=[])
    await run_queue(league.bot)

    assert len(await changes_of(league.db_path, KIND)) == 1
    assert await round_status(league.db_path) == "FINAL"
    assert await _appeal_records(league.db_path) == 0
    assert _channel_deletions(league) == [SUBMISSION_CHANNEL]
    reply = updated_reply(interaction)
    assert "The round is final" in reply
    assert "0 corrections" not in reply


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_appeals_approval_while_the_first_is_in_hand_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _APPEALS_BEING_APPROVED

    league = await _league(tmp_path)
    await _approve(league)
    second = await _approve(league)

    assert acknowledgement(second) == _APPEALS_BEING_APPROVED
    await run_queue(league.bot)
    assert len(await changes_of(league.db_path, KIND)) == 1
    assert await _appeal_penalty(league.db_path) == -5000


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_manager_is_told_the_round_is_final(tmp_path):
    league = await _league(tmp_path)
    interaction = await _approve(league)
    assert acknowledgement(interaction).startswith("⏳")
    assert re.search(r"job #\d+", acknowledgement(interaction)), (
        "the appeals approval's acknowledgement does not name its job"
    )

    await run_queue(league.bot)

    assert (
        "✅ Round 3's appeals are approved (Pro): 1 correction applied. The round is final."
        in updated_reply(interaction)
    )
    assert "APPEALS_REVIEW_APPROVED | Success" in league.log()
