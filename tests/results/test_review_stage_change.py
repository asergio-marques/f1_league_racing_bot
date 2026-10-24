"""Staging a penalty review, and its approval message, on the round's lane (#439, slice 6b,
C54–C56).

A round's penalty review runs in its submission channel, a conversation with a lane of its own
(`channel:<id>`). What a league manager stages there — a penalty, a pardon, an appeal correction,
a removal, a clear, Make Changes — is a change of its own, `results.review.stage`, asked with the
press or the form: the press is acknowledged privately at once, and when its turn comes the item
is applied to the review held in memory (as today, decision 21), any approval message standing is
withdrawn by a job, and the prompt is redrawn by another. No Penalties / Confirm asks
`results.review.confirm`, whose job posts the approval message and saves its id on the
submission's row, where a later staging reads it to withdraw it.

The refusals judged at the press (the review moved on, a driver not in the results) are
unchanged and are pinned where they were. These tests pin what the queue now does: nothing is
staged or redrawn until the change runs; a staging against a prompt since replaced is not due; a
refused redraw or approval message stops only the round's lane; a staging pressed while that lane
is stopped waits, and one pressed while the league is stopped is shown at once.

The league is `review_league`'s: round 3 of Pro in its report stage, its prompt 8900 and an approval
message 8901 standing in submission channel 702, Lewis (101) and Max (102) in its Feature Race.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock


from leaguebot.core.db.database import get_connection
from leaguebot.results.models.points_config import SessionType
from tests.support.change_queue import (
    acknowledgement,
    http_error,
    member_interaction,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    APPROVAL,
    DIVISION_ID,
    LEWIS,
    MAX,
    MAX_PROFILE,
    PROMPT,
    ROUND_ID,
    SUBMISSION_CHANNEL,
    ReviewLeague,
    block_queue,
    changes_of,
    one,
    penalty,
    review_league,
)

KIND = "results.review.stage"
CONFIRM_KIND = "results.review.confirm"
LANE = f"channel:{SUBMISSION_CHANNEL}"
APPEALS_PROMPT = 8902


def _state(league: ReviewLeague, *, staged: Any = (), approval: int | None = None,
           appeals: bool = False) -> Any:
    """Round 3's review as it is held in memory, its prompt the one the channel records."""
    from leaguebot.results.services.penalty_wizard import PenaltyReviewState

    return PenaltyReviewState(
        round_id=ROUND_ID, division_id=DIVISION_ID, submission_channel_id=SUBMISSION_CHANNEL,
        session_types_present=[SessionType.FEATURE_RACE], db_path=league.db_path,
        bot=league.bot, staged=list(staged), prompt_message_id=PROMPT,
        appeals_prompt_message_id=APPEALS_PROMPT if appeals else None,
        approval_message_id=approval, round_number=3, division_name="Pro",
    )


def _alex(league: ReviewLeague, *, message: int | None = None) -> Any:
    """A press by the league manager Alex, in round 3's submission channel."""
    interaction = member_interaction(
        league.bot, user=tier_member("manager", display_name="Alex", name="Alex#0001"),
    )
    interaction.channel_id = SUBMISSION_CHANNEL
    if message is not None:
        interaction.message.id = message
    return interaction


async def _add_penalty(state: Any, interaction: Any, *, driver: int = LEWIS,
                       appeals: bool = False) -> None:
    """Alex fills in Add Penalty (or Add Correction) for *driver*: +5s, and submits it."""
    from leaguebot.results.services.penalty_wizard import AddPenaltyModal

    modal = AddPenaltyModal(state, SessionType.FEATURE_RACE, use_appeals_staging=appeals)
    modal.driver_input._value = f"<@{driver}>"
    modal.penalty_input._value = "+5s"
    modal.description_input._value = "Corner cutting"
    modal.justification_input._value = "Turn 4, lap 12"
    await modal.on_submit(interaction)


async def _press(view: Any, label: str, interaction: Any) -> None:
    [button] = [item for item in view.children if getattr(item, "label", None) == label]
    await button.callback(interaction)


def _labels(league: ReviewLeague, message_id: int) -> list[str]:
    """The labels of the buttons on *message_id* as it now stands in the submission channel."""
    view = league.channel(SUBMISSION_CHANNEL).messages[message_id].view
    return [] if view is None else [getattr(item, "label", "") for item in view.children]


def _edited(league: ReviewLeague, message_id: int) -> bool:
    return ("edit", SUBMISSION_CHANNEL, message_id) in league.events


def _refuse_edits_of(league: ReviewLeague, message_id: int) -> Any:
    """Discord refuses every edit of *message_id* until the returned undo is called."""
    message = league.channel(SUBMISSION_CHANNEL).messages[message_id]
    working = message.edit
    message.edit = AsyncMock(side_effect=http_error(status=403, text="Missing Access"))

    def undo() -> None:
        message.edit = working

    return undo


def _lane(change: dict[str, Any]) -> str:
    return change.get("lane") or "league"


async def _approval_row(db_path: str) -> Any:
    return await one(
        db_path, "SELECT approval_message_id FROM round_submission_channels WHERE round_id = ?",
        ROUND_ID,
    )


async def test_a_penalty_added_is_staged_and_the_prompt_redrawn_when_its_turn_comes(tmp_path):
    """Alex adds a five-second penalty for Lewis in round 3's review. He is answered at once that
    it is under way, and nothing is redrawn yet; the staging is a change on round 3's lane. When
    the queue runs it, the prompt is redrawn with the penalty's Remove button and his answer is
    updated with what was staged."""
    league = await review_league(tmp_path)
    state = _state(league)
    interaction = _alex(league)

    await _add_penalty(state, interaction)

    assert acknowledgement(interaction).startswith("⏳")
    assert not _edited(league, PROMPT)
    [change] = await changes_of(league.db_path, KIND)
    assert _lane(change) == LANE

    await run_queue(league.bot)

    [change] = await changes_of(league.db_path, KIND)
    assert change["state"] == "DONE"
    assert _edited(league, PROMPT)
    assert "Remove #1" in _labels(league, PROMPT)
    assert "Staged Penalty" in updated_reply(interaction)


async def test_a_staging_against_a_replaced_prompt_is_not_due(tmp_path):
    """Round 3's lane is stopped when Alex adds a penalty for Lewis, so his staging waits. Before
    it runs, the review's prompt is replaced (as a restart puts the review back with a fresh
    prompt, 8950). When the stop clears, the staging is found no longer due: the old prompt is not
    redrawn and nothing is left stopped."""
    league = await review_league(tmp_path)
    holder = await block_queue(league, lane=LANE)
    await _add_penalty(_state(league), _alex(league))
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET prompt_message_id = 8950")
        await db.commit()

    holder["fail"] = False
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert not _edited(league, PROMPT)
    assert "Remove #1" not in _labels(league, PROMPT)
    [change] = await changes_of(league.db_path, KIND)
    assert change["state"] in ("DONE", "REFUSED")


async def test_a_refused_redraw_stops_the_queue_with_the_item_staged(tmp_path):
    """Discord refuses the edit of round 3's prompt when Alex's penalty for Lewis is staged.
    Round 3's lane stops at the redraw; the league lane is not stopped. Retry redraws the prompt
    with the one penalty staged, not two."""
    league = await review_league(tmp_path)
    undo = _refuse_edits_of(league, PROMPT)

    await _add_penalty(_state(league), _alex(league))
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, LANE)
    assert stopped is not None and stopped["name"] == "redraw"
    assert await stopped_job(league.db_path, "league") is None

    undo()
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    labels = _labels(league, PROMPT)
    assert "Remove #1" in labels
    assert "Remove #2" not in labels


async def test_a_staging_while_the_queue_is_stopped_is_kept_and_applied_when_it_clears(tmp_path):
    """Round 3's lane is stopped at another job when Alex adds a penalty for Lewis. He is
    answered that it is under way and the prompt is left as it is; once the stop clears, the
    staging runs and the prompt is redrawn with the penalty."""
    league = await review_league(tmp_path)
    holder = await block_queue(league, lane=LANE)
    interaction = _alex(league)

    await _add_penalty(_state(league), interaction)
    await run_queue(league.bot)

    assert acknowledgement(interaction).startswith("⏳")
    assert not _edited(league, PROMPT)

    holder["fail"] = False
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert "Remove #1" in _labels(league, PROMPT)
    [change] = await changes_of(league.db_path, KIND)
    assert change["state"] == "DONE"


async def test_a_staging_is_shown_while_the_league_is_stopped(tmp_path):
    """The league lane is stopped at a job of its own when Alex adds a penalty for Lewis. The
    staging runs on round 3's lane all the same: the prompt is redrawn with the penalty while the
    league stays stopped."""
    league = await review_league(tmp_path)
    await block_queue(league)

    await _add_penalty(_state(league), _alex(league))
    await run_queue(league.bot)

    [change] = await changes_of(league.db_path, KIND)
    assert _lane(change) == LANE
    assert change["state"] == "DONE"
    assert "Remove #1" in _labels(league, PROMPT)
    assert await stopped_job(league.db_path, "league") is not None
    assert await stopped_job(league.db_path, LANE) is None


async def test_staging_withdraws_a_standing_approval_message_as_a_job(tmp_path):
    """Round 3's approval message 8901 stands when Alex adds a penalty for Lewis. Nothing is
    deleted at the press; when the staging runs, a job of its own withdraws the approval message,
    before the prompt is redrawn."""
    league = await review_league(tmp_path)
    state = _state(league, approval=APPROVAL)

    await _add_penalty(state, _alex(league))

    assert APPROVAL in league.channel(SUBMISSION_CHANNEL).messages

    await run_queue(league.bot)

    assert APPROVAL not in league.channel(SUBMISSION_CHANNEL).messages
    deleted = league.events.index(("delete", SUBMISSION_CHANNEL, APPROVAL))
    redrawn = league.events.index(("edit", SUBMISSION_CHANNEL, PROMPT))
    assert deleted < redrawn
    [change] = await changes_of(league.db_path, KIND)
    names = [row["name"] for row in await step_rows(league.db_path)
             if row["change_id"] == change["id"]]
    assert "withdraw_approval" in names


async def test_a_pardon_and_an_appeal_correction_are_staged_alike(tmp_path):
    """Attendance is on and Max (102) gave no answer to round 3's check-in. Alex stages a NO_RSVP
    pardon for him: it is a staging change on round 3's lane, and the prompt is redrawn with its
    Remove Pardon button when it runs. In a second league whose round 3 awaits its appeal
    verdicts, Alex adds a correction for Lewis: it too is a staging change on the round's lane,
    and the appeals prompt is redrawn when it runs."""
    from leaguebot.results.services.penalty_wizard import AddPardonModal

    league = await review_league(tmp_path, attendance=True)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status) VALUES (?, ?, ?, 'NO_RSVP')",
            (ROUND_ID, DIVISION_ID, MAX_PROFILE),
        )
        await db.commit()
    modal = AddPardonModal(_state(league))
    modal.driver_id_input._value = str(MAX)
    modal.pardon_type_input._value = "NO_RSVP"
    modal.justification_input._value = "Told us in advance"

    await modal.on_submit(_alex(league))

    assert not _edited(league, PROMPT)
    [change] = await changes_of(league.db_path, KIND)
    assert _lane(change) == LANE
    await run_queue(league.bot)
    assert "Remove Pardon #1" in _labels(league, PROMPT)

    (tmp_path / "appeals").mkdir()
    appeals = await review_league(tmp_path / "appeals", round_status="AWAITING_APPEAL_VERDICTS",
                                  appeals_prompt=APPEALS_PROMPT)
    appeals.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")

    await _add_penalty(_state(appeals, appeals=True), _alex(appeals), appeals=True)

    assert not _edited(appeals, APPEALS_PROMPT)
    [change] = await changes_of(appeals.db_path, KIND)
    assert _lane(change) == LANE
    await run_queue(appeals.bot)
    assert _edited(appeals, APPEALS_PROMPT)
    assert "Remove #1" in _labels(appeals, APPEALS_PROMPT)


async def test_remove_clear_and_make_changes_are_staged_and_redrawn_as_jobs(tmp_path):
    """Round 3's review holds penalties for Lewis and Max, its approval message 8901 standing.
    Alex presses Make Changes on the approval message, then Remove #2 on the prompt, then clears
    the list and confirms. Each press is a staging change on round 3's lane, acknowledged at once,
    and changes nothing until it runs: Make Changes withdraws the approval message and redraws
    the prompt; Remove #2 leaves only Remove #1; the clear leaves no Remove button and the
    approval question is posted again."""
    from leaguebot.results.services.penalty_wizard import (
        ApprovalView,
        PenaltyReviewView,
        _ConfirmClearView,
    )

    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET approval_message_id = ?",
                         (APPROVAL,))
        await db.commit()
    state = _state(league, staged=[penalty(LEWIS), penalty(MAX)], approval=APPROVAL)

    make_changes = _alex(league, message=APPROVAL)
    await _press(ApprovalView(state), "✏️ Make Changes", make_changes)
    assert acknowledgement(make_changes).startswith("⏳")
    assert APPROVAL in league.channel(SUBMISSION_CHANNEL).messages
    await run_queue(league.bot)
    assert APPROVAL not in league.channel(SUBMISSION_CHANNEL).messages
    assert {"Remove #1", "Remove #2"} <= set(_labels(league, PROMPT))

    remove = _alex(league, message=PROMPT)
    await _press(PenaltyReviewView(state), "Remove #2", remove)
    assert acknowledgement(remove).startswith("⏳")
    assert "Remove #2" in _labels(league, PROMPT)
    await run_queue(league.bot)
    labels = _labels(league, PROMPT)
    assert "Remove #1" in labels and "Remove #2" not in labels

    clear = _alex(league, message=PROMPT)
    view = _ConfirmClearView(state=state, posted=clear)
    await type(view).confirm_btn(view, clear, view.confirm_btn)
    assert "Remove #1" in _labels(league, PROMPT)
    await run_queue(league.bot)
    assert not [label for label in _labels(league, PROMPT) if label.startswith("Remove")]
    approvals = [
        mid for mid, message in league.channel(SUBMISSION_CHANNEL).messages.items()
        if type(message.view).__name__ == "ApprovalView"
    ]
    assert len(approvals) == 1
    assert len(await changes_of(league.db_path, KIND)) == 3
    assert all(_lane(change) == LANE for change in await changes_of(league.db_path, KIND))


async def test_confirm_posts_the_approval_message_as_a_job_and_keeps_its_id(tmp_path):
    """Nothing is staged in round 3's review when Alex presses No Penalties / Confirm. He is
    answered at once and nothing is posted yet; the confirmation is a change on round 3's lane,
    whose job posts the approval message and saves its id on the submission's row."""
    from leaguebot.results.services.penalty_wizard import PenaltyReviewView

    league = await review_league(tmp_path)
    state = _state(league)
    interaction = _alex(league, message=PROMPT)
    before = set(league.channel(SUBMISSION_CHANNEL).messages)

    await _press(PenaltyReviewView(state), "No Penalties / Confirm", interaction)

    assert acknowledgement(interaction).startswith("⏳")
    assert set(league.channel(SUBMISSION_CHANNEL).messages) == before
    [change] = await changes_of(league.db_path, CONFIRM_KIND)
    assert _lane(change) == LANE

    await run_queue(league.bot)

    [posted] = set(league.channel(SUBMISSION_CHANNEL).messages) - before
    assert type(league.channel(SUBMISSION_CHANNEL).messages[posted].view).__name__ == (
        "ApprovalView"
    )
    assert await _approval_row(league.db_path) == posted


async def test_a_refused_approval_message_stops_the_queue(tmp_path):
    """Discord refuses the approval message Alex's No Penalties / Confirm posts in round 3's
    submission channel. Round 3's lane stops at that post, the league lane running on, and no id
    is saved; Retry posts it and saves its id."""
    from leaguebot.results.services.penalty_wizard import PenaltyReviewView

    league = await review_league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).fail_when = (
        lambda _content, kwargs: type(kwargs.get("view")).__name__ == "ApprovalView"
    )

    await _press(PenaltyReviewView(_state(league)), "No Penalties / Confirm",
                 _alex(league, message=PROMPT))
    await run_queue(league.bot)

    stopped = await stopped_job(league.db_path, LANE)
    assert stopped is not None and stopped["name"] == "post_approval"
    assert await stopped_job(league.db_path, "league") is None
    assert await _approval_row(league.db_path) is None

    league.channel(SUBMISSION_CHANNEL).fail_when = None
    await retry_job(league.bot, lane=LANE)

    assert await stopped_job(league.db_path) is None
    assert await _approval_row(league.db_path) is not None


async def test_the_approval_message_s_id_is_saved_on_the_submission_row_and_read_by_a_later_staging(
    tmp_path,
):
    """Alex confirms round 3's review with nothing staged, and the approval message is posted,
    its id saved on the submission's row. A second review of round 3, built afresh in memory with
    no approval message of its own, then stages a penalty for Lewis: its staging reads the
    approval message from the row and withdraws it, and the row no longer names it."""
    from leaguebot.results.services.penalty_wizard import PenaltyReviewView

    league = await review_league(tmp_path)
    await _press(PenaltyReviewView(_state(league)), "No Penalties / Confirm",
                 _alex(league, message=PROMPT))
    await run_queue(league.bot)
    posted = await _approval_row(league.db_path)
    assert posted is not None

    await _add_penalty(_state(league), _alex(league))
    await run_queue(league.bot)

    assert posted not in league.channel(SUBMISSION_CHANNEL).messages
    assert await _approval_row(league.db_path) is None
