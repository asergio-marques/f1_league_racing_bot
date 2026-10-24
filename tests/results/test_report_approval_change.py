"""Approving a round's reports through the change queue: `results.reports.approve` (#439, slice 2).

`results/services/report_approval_change.py` carries stage one of a round's penalty review as a
change: `names` resolves the drivers' display names, `apply` saves everything the approval writes
in one save (the penalties and their records, the points, the round's attendance, the standings
snapshots of this round and every later one, and the status), then the republication under
"Post-Race Penalty Results", the take-down of the prompt and the approval message, one
`announce_verdict` per penalty, the appeals prompt, the attendance sheet and the sanction jobs, and
`close`, which writes `PENALTY_REVIEW_APPROVED`.

These tests drive the real change types the builder registers (`register_change_types`) on a real
queue, on a database built by the migrations, with "now" pinned. The approval is asked for as the
review's Approve control asks it: `bot.change_queue.ask("results.reports.approve", payload, ...)`,
the payload carrying the staged penalties and pardons as plain data (`to_payload`).

The league, its fake channels and the attendance hook's double are `tests.support.review_league`'s.

What `test_repost_subsequent_standings.py::test_the_snapshots_are_recomputed_first` pinned is held
here by `test_the_snapshots_of_this_round_and_every_later_one_are_saved_with_the_penalties`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.results.models.points_config import SessionType
from tests.support.change_queue import (
    acknowledgement,
    discard_job,
    http_error,
    member_interaction,
    restart_queue,
    retry_job,
    run_queue,
    step_rows,
    stopped_job,
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    APPROVAL,
    AMEND_CHANNEL,
    DIVISION_ID,
    HEADING,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
    OLD_RESULTS,
    OTHER_DIVISION_ID,
    PROMPT,
    RESULTS_CHANNEL,
    ROUND_ID,
    SEASON_ID,
    SUBMISSION_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    appeals_prompts,
    block_queue,
    candidate,
    changes_of,
    is_appeals_prompt,
    one,
    pardon,
    penalty,
    penalty_records,
    points_fail,
    race_rows,
    review_league,
    round_status,
    run_until_done,
    stopped_at,
    verdict_headings,
)
from tests.results.test_review_open_change import _refuse_the_job

KIND = "results.reports.approve"

#: What is not yet true of a sanction's jobs (#439, Part C).
SANCTION_SAVE_UNBUILT = (
    "#439: a sanction is applied in an ACT through the hook's apply_sanction, which commits on its "
    "own connection, rather than in a SAVE on the queue's connection"
)
SANCTION_ROLE_JOBS_UNBUILT = (
    "#439: a sanction's roles are changed inside its apply, swallowing Discord's refusal, with no "
    "check of an autoreserve ahead of it and no role job of its own"
)


def _payload(*, staged: Any = None, pardons: Any = (), prompt: int = PROMPT,
             approval: int | None = None) -> dict[str, Any]:
    staged = [penalty(LEWIS), penalty(MAX)] if staged is None else staged
    return {
        "round_id": ROUND_ID,
        "division_id": DIVISION_ID,
        "staged": [penalty.to_payload() for penalty in staged],
        "pardons": [pardon.to_payload() for pardon in pardons],
        "prompt_message_id": prompt,
        "approval_message_id": approval,
    }


async def _approve(league: ReviewLeague, **payload: Any) -> Any:
    """Press Approve on round 3's review as the league manager Alex; the queue is not yet run.
    Gives Alex's interaction."""
    interaction = member_interaction(
        league.bot, user=tier_member("manager", display_name="Alex", name="Alex#0001"),
    )
    await league.bot.change_queue.ask(
        KIND, _payload(**payload), interaction=interaction,
        what="✅ Approve on round 3's penalty review",
    )
    return interaction


# ---------------------------------------------------------------------------
# Defect 7: a stop part-way through
# ---------------------------------------------------------------------------


async def test_a_stop_after_the_penalties_are_saved_finishes_the_approval_on_restart(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    await _approve(league)
    await run_until_done(league, "apply")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    records = await penalty_records(league.db_path)
    assert len(records) == 2
    announced = {int(record["announcement_message_id"]) for record in records}
    assert len(announced) == 2
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert sorted(sent[1:]) == sorted(announced)
    assert len(league.attendance._calls("record_on")) == 1
    assert len(appeals_prompts(league)) == 1
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


async def test_a_stop_before_the_save_leaves_nothing_applied_and_the_approval_runs_whole_on_restart(
    tmp_path,
):
    league = await review_league(tmp_path)
    await _approve(league)
    await run_until_done(league, "names")

    assert await penalty_records(league.db_path) == []
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 0
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert len(await penalty_records(league.db_path)) == 2
    race = await race_rows(league.db_path)
    assert race[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert race[MAX]["postrace_time_penalties_ms"] == 5000
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


async def test_a_stop_part_way_through_the_reposts_finishes_them_and_announces_each_verdict_once(
    tmp_path,
):
    league = await review_league(tmp_path)
    await _approve(league)
    await run_until_done(league, "post_session_results")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    results = league.sent_to(RESULTS_CHANNEL)
    assert len(results) == 1
    assert set(league.channel(RESULTS_CHANNEL).messages) == set(results)
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 3
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000


async def test_a_penalty_approval_posts_one_banner_across_its_verdicts_and_its_sanctions(
    tmp_path,
):
    """Image spec, the verdict banner: one heading per approval, heading the attendance
    sanctions as it heads the verdicts. Each later job reads the heading back from its row, so a
    restart between two verdicts posts no second one."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_until_done(league, "announce_verdict")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 3
    assert league.attendance._calls("announce_sanction") == [("announce_sanction", MAX_PROFILE)]
    banners = await one(
        league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages WHERE round_id = ?",
        ROUND_ID,
    )
    assert banners == 1
    assert await one(
        league.db_path, "SELECT message_id FROM verdict_banner_messages WHERE round_id = ?",
        ROUND_ID,
    ) == str(sent[0])


async def test_a_restart_between_the_attendance_sheet_and_the_sanctions_posts_one_sheet_and_sanctions_each_once(
    tmp_path,
):
    """Lewis and Max are each over the autoreserve threshold in Pro. The approval stops just
    after Pro's attendance sheet is posted, before either sanction, and the bot restarts. Once
    the queue runs, the sheet before the sanctions is not posted again: Pro's sheet is posted
    twice in all, once before the sanctions and once after them, and each driver is sanctioned
    and announced once."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_until_done(league, "attendance_sheet")
    assert league.attendance._calls("apply_sanction") == []

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert [call[1] for call in league.attendance._calls("post_sheet")] == [
        DIVISION_ID, DIVISION_ID,
    ]
    assert league.attendance._calls("apply_sanction") == [
        ("apply_sanction", LEWIS_PROFILE), ("apply_sanction", MAX_PROFILE),
    ]
    assert sorted(league.attendance._calls("announce_sanction")) == [
        ("announce_sanction", LEWIS_PROFILE), ("announce_sanction", MAX_PROFILE),
    ]


async def test_a_restart_between_two_sanctions_applies_each_once(tmp_path):
    """Lewis and Max are each over the autoreserve threshold in Pro. The approval stops just
    after Lewis's sanction is applied, before Max's, and the bot restarts. Once the queue runs,
    Lewis is not sanctioned again, Max is sanctioned once, and each is announced once."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_until_done(league, "apply_sanction")
    assert league.attendance._calls("apply_sanction") == [("apply_sanction", LEWIS_PROFILE)]

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance._calls("apply_sanction") == [
        ("apply_sanction", LEWIS_PROFILE), ("apply_sanction", MAX_PROFILE),
    ]
    assert sorted(league.attendance._calls("announce_sanction")) == [
        ("announce_sanction", LEWIS_PROFILE), ("announce_sanction", MAX_PROFILE),
    ]


async def test_an_approval_announcing_no_verdict_posts_no_heading(tmp_path):
    """Image spec, the verdict banner: a heading is posted only where a verdict follows it."""
    league = await review_league(tmp_path)
    await _approve(league, staged=[], pardons=[pardon()])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert league.sent_to(VERDICTS_CHANNEL) == []
    assert await one(
        league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages WHERE round_id = ?",
        ROUND_ID,
    ) == 0


async def test_penalties_and_attendance_are_saved_in_one_save(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    async with get_connection(league.db_path) as db:
        columns = [row["name"] for row in await (
            await db.execute("PRAGMA table_info(round_submission_channels)")
        ).fetchall()]
    assert "staged_penalties" not in columns

    league.attendance.record_fails = RuntimeError("the attendance could not be recorded")
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "apply"
    assert await penalty_records(league.db_path) == []
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 0
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"

    league.attendance.record_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert len(await penalty_records(league.db_path)) == 2
    assert await one(league.db_path, "SELECT COUNT(*) FROM attendance_recorded") == 1


async def test_the_snapshots_of_this_round_and_every_later_one_are_saved_with_the_penalties(
    tmp_path,
):
    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        # The Feature Race's "Standard" scale, so the penalty really moves the points:
        # without one, the points are left as seeded (25 to Lewis, 18 to Max).
        await db.executemany(
            "INSERT INTO season_points_entries (season_id, config_name, session_type, "
            "position, points) VALUES (?, 'Standard', 'FEATURE_RACE', ?, ?)",
            [(SEASON_ID, 1, 25), (SEASON_ID, 2, 18)],
        )
        await db.commit()
    await _approve(league, staged=[penalty(LEWIS, seconds=30)])
    await run_until_done(league, "apply")

    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT round_id, driver_user_id, standing_position FROM driver_standings_snapshots "
            "ORDER BY round_id, standing_position"
        )
        rows = [tuple(row) for row in await cursor.fetchall()]
    for round_id in (ROUND_ID, LATER_ROUND_ID):
        leaders = [driver for rid, driver, position in rows if rid == round_id and position == 1]
        assert leaders == [MAX], f"round {round_id}'s standings were not recomputed in the save"


# ---------------------------------------------------------------------------
# Defect 3: points that cannot be recalculated
# ---------------------------------------------------------------------------


async def test_a_session_whose_points_cannot_be_recalculated_stops_the_queue_and_changes_nothing(
    tmp_path,
):
    league = await review_league(tmp_path)
    with points_fail():
        await _approve(league)
        await run_queue(league.bot)

    assert await stopped_at(league) == "apply"
    assert await penalty_records(league.db_path) == []
    race = await race_rows(league.db_path)
    assert race[LEWIS]["postrace_time_penalties_ms"] == 0
    assert race[LEWIS]["points_awarded"] == 25
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert league.sent_to(RESULTS_CHANNEL) == []


async def test_a_discarded_apply_says_nothing_was_changed_and_the_review_is_still_open(tmp_path):
    league = await review_league(tmp_path)
    with points_fail():
        interaction = await _approve(league)
        await run_queue(league.bot)
    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "Nothing was changed" in reply
    assert await penalty_records(league.db_path) == []
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert await one(
        league.db_path, "SELECT in_penalty_review FROM round_submission_channels"
    ) == 1


async def test_a_session_with_no_points_configuration_is_skipped_not_failed(tmp_path):
    league = await review_league(tmp_path, config_name=None)
    with points_fail() as scored:
        await _approve(league)
        await run_queue(league.bot)

    assert await stopped_at(league) is None
    scored.assert_not_awaited()
    race = await race_rows(league.db_path)
    assert race[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert race[LEWIS]["points_awarded"] == 25
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


# ---------------------------------------------------------------------------
# Defect 4: the attendance sheet
# ---------------------------------------------------------------------------


async def test_an_attendance_sheet_discord_refuses_stops_the_queue_and_is_retried(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    interaction = await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "attendance_sheet"
    notice = league.log()
    assert "attendance" in notice.lower() and "Pro" in notice, (
        "the stop notice does not name the sheet and its division"
    )
    assert "/attendance sync" not in notice, "sync was named while the bot still tries the sheet"
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    sheets = league.attendance._calls("post_sheet")
    assert [call[1] for call in sheets] == [DIVISION_ID, DIVISION_ID]
    assert sheets[-1][2] is True, "the retry did not post the sheet as text"
    assert "✅" in updated_reply(interaction)


async def test_a_discarded_sheet_is_named_with_attendance_sync_and_the_sanctions_still_run(
    tmp_path,
):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "attendance_sheet"
    assert league.attendance._calls("apply_sanction") == [], (
        "a sanction ran while the sheet in front of it was stopped"
    )

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert ("apply_sanction", MAX_PROFILE) in league.attendance.calls
    reply = updated_reply(interaction)
    assert "attendance sheet" in reply.lower()
    assert "/attendance sync" in reply
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()


async def test_the_sheet_is_not_put_on_the_old_retry_queue(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "attendance_sheet"
    assert await one(league.db_path, "SELECT COUNT(*) FROM pending_messages") == 0


async def test_the_sheet_after_a_review_replaces_the_old_one_by_a_delete_job(tmp_path):
    """Attendance is on, with its real hook (the placement service a double: no sanction is
    owed). Pro's attendance channel 707 holds its sheet 8805, recorded as the live one. Alex
    approves round 3's reports: the `attendance_sheet` job posts the new sheet and its save
    records it, the old one still standing; then a `delete_message` job of the review deletes
    the old sheet."""
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview
    from tests.support.change_queue import attach_queue
    from tests.support.review_league import NOW, channel

    sheet_channel, old_sheet = 707, 8805
    league = await review_league(tmp_path, attendance=True)
    league.channels[sheet_channel] = channel(sheet_channel, league.events)
    league.channels[sheet_channel].guild = league.guild
    league.channel(sheet_channel).seed(old_sheet, "Pro's attendance sheet")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id, "
            "attendance_message_id) VALUES (?, ?, ?)",
            (DIVISION_ID, str(sheet_channel), str(old_sheet)),
        )
        await db.commit()
    placement = MagicMock()
    for name in ("sack_driver", "move_driver", "refresh_lineup"):
        setattr(placement, name, AsyncMock(return_value=None))
    league.bot.attendance_after_review = AttendanceAfterReview(league.bot, placement)
    attach_queue(league.bot, league.db_path, now=NOW)

    await _approve(league, staged=[penalty(LEWIS)])
    await run_until_done(league, "attendance_sheet")

    [new] = league.sent_to(sheet_channel)
    assert old_sheet in league.channel(sheet_channel).messages, "the old sheet went too soon"
    assert await one(
        league.db_path,
        "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = ?",
        DIVISION_ID,
    ) == str(new)

    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert [(kind, mid) for kind, cid, mid in league.events if cid == sheet_channel] == [
        ("send", new), ("delete", old_sheet),
    ]
    deletes = [row for row in await step_rows(league.db_path) if row["name"] == "delete_message"
               and str(old_sheet) in (row["payload"] or "")]
    assert len(deletes) == 1


# ---------------------------------------------------------------------------
# Defect 11: a repost Discord refuses
# ---------------------------------------------------------------------------


async def test_a_repost_discord_refuses_is_retried_and_the_approval_finishes_once_it_lands(
    tmp_path,
):
    league = await review_league(tmp_path)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(text="Discord is down")
    interaction = await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "post_session_results"
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages

    league.channel(RESULTS_CHANNEL).send_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert OLD_RESULTS not in league.channel(RESULTS_CHANNEL).messages
    assert (await changes_of(league.db_path, KIND))[0]["state"] == "DONE"
    assert "Round 3's reports are approved" in updated_reply(interaction)


# ---------------------------------------------------------------------------
# Discards
# ---------------------------------------------------------------------------


async def test_a_discarded_verdict_is_named_incomplete_and_the_manager_is_told_to_post_it(
    tmp_path,
):
    league = await review_league(tmp_path)
    # The heading over the verdicts goes out, so that the verdict is the job that stops.
    league.channel(VERDICTS_CHANNEL).fail_when = lambda content, _kwargs: content != HEADING
    interaction = await _approve(league, staged=[penalty(LEWIS)])
    await run_queue(league.bot)
    assert await stopped_at(league) == "announce_verdict"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    reply = updated_reply(interaction)
    assert "Lewis" in reply or f"<@{LEWIS}>" in reply
    assert "verdict" in reply.lower()
    assert "post" in reply.lower()
    assert (await penalty_records(league.db_path))[0]["announcement_message_id"] is None


async def test_a_discarded_apply_reopens_the_review_with_a_fresh_prompt(tmp_path):
    league = await review_league(tmp_path)
    with points_fail():
        interaction = await _approve(league)
        await run_queue(league.bot)
    await discard_job(league.bot)
    await run_queue(league.bot)

    reopened = await changes_of(league.db_path, "results.review.open")
    assert len(reopened) == 1
    assert reopened[0]["origin"] == "BOT"
    assert PROMPT not in league.channel(SUBMISSION_CHANNEL).messages
    fresh = await one(league.db_path, "SELECT prompt_message_id FROM round_submission_channels")
    assert fresh != PROMPT and fresh in league.channel(SUBMISSION_CHANNEL).messages
    reply = updated_reply(interaction)
    assert "Nothing was changed" in reply
    assert "Approve" in reply

    await _approve(league, prompt=fresh)
    await run_queue(league.bot)
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert len(await penalty_records(league.db_path)) == 2


async def test_a_discarded_appeals_prompt_is_posted_again_at_once(tmp_path):
    league = await review_league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).fail_when = is_appeals_prompt
    await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_appeals_prompt"

    league.channel(SUBMISSION_CHANNEL).fail_when = None
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert len(await changes_of(league.db_path, "results.appeals.open")) == 1
    prompts = appeals_prompts(league)
    assert len(prompts) == 1
    assert await one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompts[0]


APPROVAL_CLOSE_UNDISCARDABLE = "#439: the reports approval's closing job may still be discarded"


@pytest.mark.parametrize("case, follows", [
    pytest.param(
        "approved", "round 3 in **Pro** could be left waiting for its appeals with no prompt, "
        "never final, until the bot restarts", id="approved",
        marks=pytest.mark.xfail(strict=True, reason=APPROVAL_CLOSE_UNDISCARDABLE),
    ),
    pytest.param(
        "apply discarded", "round 3 in **Pro** could be left in its penalty review with no "
        "prompt, never final, until the bot restarts", id="apply discarded",
        marks=pytest.mark.xfail(strict=True, reason=APPROVAL_CLOSE_UNDISCARDABLE),
    ),
])
async def test_the_approval_s_closing_job_cannot_be_discarded(tmp_path, case, follows):
    """Alex approves round 3's reports in Pro. Either the approval goes through, or its save
    fails and a league admin discards it. Then the save of the approval's last job, which
    records it and asks for the next prompt, fails and stops the queue at `close`. A league
    admin's Discard on it is refused, saying, where the approval went through, that round 3 in
    Pro could be left waiting for its appeals with no prompt, and, where it was discarded, that
    it could be left in its penalty review with no prompt, never final, until the bot restarts.
    The queue stays stopped there and no prompt is asked for. Once the fault is lifted, a Retry
    finishes it: the appeals prompt is posted and the approval logged, or the penalty review is
    opened again."""
    league = await review_league(tmp_path)
    await _refuse_the_job(league.db_path, KIND, True)
    if case == "approved":
        await _approve(league)
        await run_queue(league.bot)
    else:
        with points_fail():
            await _approve(league)
            await run_queue(league.bot)
        assert await stopped_at(league) == "apply"
        await discard_job(league.bot)
    stopped = await stopped_job(league.db_path, "league")
    assert stopped is not None and stopped["name"] == "close"

    refused = await discard_job(league.bot)

    assert acknowledgement(refused) == (
        f"⛔ This job can't be discarded: without it {follows}. Fix what stopped it and press "
        "**Retry**."
    )
    assert (await stopped_job(league.db_path, "league"))["id"] == stopped["id"]
    assert appeals_prompts(league) == []
    assert await changes_of(league.db_path, "results.review.open") == []

    await _refuse_the_job(league.db_path, KIND, False)
    await retry_job(league.bot)

    assert await stopped_job(league.db_path) is None
    if case == "approved":
        assert len(appeals_prompts(league)) == 1
        assert "PENALTY_REVIEW_APPROVED | Success" in league.log()
    else:
        assert len(await changes_of(league.db_path, "results.review.open")) == 1


async def test_a_discarded_batch_notice_is_named_and_the_republication_goes_ahead(tmp_path):
    league = await review_league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).fail_when = (
        lambda content, _kwargs: "Updating" in content
    )
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_batch_notice"

    await discard_job(league.bot)

    assert await stopped_at(league) is None, "the notice's take-down stopped the queue"
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert OLD_RESULTS not in league.channel(RESULTS_CHANNEL).messages
    assert "notice" in updated_reply(interaction).lower()
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()


# ---------------------------------------------------------------------------
# Sanctions, one job per driver
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_each_driver_over_a_threshold_is_sanctioned_and_announced_in_jobs_of_their_own(
    tmp_path,
):
    """Lewis and Max are each owed an autoreserve in Pro. Alex approves round 3's reports: each
    driver's sanction is saved, their roles changed and their card posted, each a job of its own,
    and Pro's lineup is posted once."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    steps = await step_rows(league.db_path)
    assert len([row for row in steps if row["name"] == "apply_sanction"]) == 2
    assert len([row for row in steps if row["name"] == "announce_sanction"]) == 2
    assert [c for c in league.attendance.calls if c[0] == "apply_sanction"] == [
        ("apply_sanction", LEWIS_PROFILE), ("apply_sanction", MAX_PROFILE),
    ]
    assert len(league.attendance._calls("announce_sanction")) == 2
    assert league.attendance._calls("refresh_lineup") == [("refresh_lineup", DIVISION_ID)]
    assert len([row for row in steps if row["name"] == "sanction_roles"]) == 2
    assert league.attendance.role_changes == [
        (LEWIS_PROFILE, [5502], [5503]), (MAX_PROFILE, [5502], [5503]),
    ]


#: Round 4's banner, heading its own verdicts in the verdicts channel since it went final.
LATER_BANNER = 8970


async def test_an_approval_whose_cascade_reaches_a_later_round_heads_its_sanction_cards_with_one_banner(
    tmp_path,
):
    """Round 3's reports are approved while round 4 is already final, its verdicts standing
    under a banner of its own. The attendance cascade carries the totals to round 4, where Max
    is over a threshold, but his sanction follows round 3's verdicts: its card goes beneath the
    banner posted for them, and round 4's banner is left as it was. The double's announcement
    posts the card as the real hook does, through `announce_sanction`, beneath the banner of the
    round it is handed."""
    from leaguebot.results.services import verdict_announcement_service

    league = await review_league(tmp_path, attendance=True)
    league.channel(VERDICTS_CHANNEL).seed(LATER_BANNER, "**Season 1 Pro Round 4**")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at) "
            "VALUES (?, ?, ?, '2026-02-08T21:00:00+00:00')",
            (LATER_ROUND_ID, str(VERDICTS_CHANNEL), str(LATER_BANNER)),
        )
        await db.commit()
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    headed_by: list[int] = []
    recorded = league.attendance.announce_sanction

    async def _announce(round_id: int, division_id: int, owed: dict[str, Any], *,
                        as_text: bool) -> None:
        headed_by.append(round_id)
        await recorded(round_id, division_id, owed, as_text=as_text)
        await verdict_announcement_service.announce_sanction(
            league.bot, league.db_path, round_id, owed["driver_user_id"], "Max",
            owed["sanction"], 10, as_text=as_text,
        )

    league.attendance.announce_sanction = _announce
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert headed_by == [ROUND_ID]
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 4, "the two verdicts and the card follow one banner, and no other is posted"
    assert LATER_BANNER in league.channel(VERDICTS_CHANNEL).messages
    banners = await one(
        league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages WHERE round_id = ?",
        ROUND_ID,
    )
    assert banners == 1
    assert await one(
        league.db_path,
        "SELECT heads_sanctions FROM verdict_banner_messages WHERE message_id = ?",
        str(sent[0]),
    ) == 1
    assert await one(
        league.db_path,
        "SELECT heads_sanctions FROM verdict_banner_messages WHERE message_id = ?",
        str(LATER_BANNER),
    ) == 0


def _cards_posted_for_real(league: ReviewLeague) -> None:
    """Make the double's announcement post the card as the real hook does, through
    `announce_sanction`, beneath the banner of the round it is handed, and post nothing while
    attendance is off."""
    from leaguebot.results.services import verdict_announcement_service

    recorded = league.attendance.announce_sanction

    async def _announce(round_id: int, division_id: int, owed: dict[str, Any], *,
                        as_text: bool) -> None:
        await recorded(round_id, division_id, owed, as_text=as_text)
        if not league.attendance_on:
            return
        await verdict_announcement_service.announce_sanction(
            league.bot, league.db_path, round_id, owed["driver_user_id"], None,
            owed["sanction"], 10, as_text=as_text,
        )

    league.attendance.announce_sanction = _announce


async def test_a_sanction_card_s_own_heading_discord_refuses_stops_the_queue_until_discarded(
    tmp_path,
):
    """A clean round: no penalty applied, so no verdict and no heading over verdicts, but Lewis
    and Max are over a threshold. Their cards are headed by a job of its own, planned once the
    first sanction applies, and where the verdicts channel refuses the written heading the queue
    stops there, no card posted ahead of it (owner, 2026-10-06, "Yes, it stops too"). Once a
    league admin discards it, both cards go out beneath none, no second heading is tried, and the
    reply and the approval's line name the heading as not posted."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    _cards_posted_for_real(league)
    league.channel(VERDICTS_CHANNEL).fail_when = lambda content, _kwargs: content == HEADING
    interaction = await _approve(league, staged=[])
    await run_queue(league.bot)

    assert await stopped_at(league) == "announce_heading"
    assert LEWIS_PROFILE in league.attendance.applied
    assert league.sent_to(VERDICTS_CHANNEL) == []
    assert league.attendance._calls("announce_sanction") == []

    # The channel would take the heading now, so a build that tried it again behind the
    # discarded job would show here.
    league.channel(VERDICTS_CHANNEL).fail_when = None
    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert verdict_headings(league) == []
    cards = league.sent_to(VERDICTS_CHANNEL)
    assert len(cards) == 2
    assert f"<@{LEWIS}>" in (league.channel(VERDICTS_CHANNEL).messages[cards[0]].content or "")
    assert f"<@{MAX}>" in (league.channel(VERDICTS_CHANNEL).messages[cards[1]].content or "")
    steps = await step_rows(league.db_path)
    assert len([row for row in steps if row["name"] == "announce_heading"]) == 1
    assert await one(league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages") == 0
    reply = updated_reply(interaction)
    assert "heading" in reply.lower() and "not posted" in reply.lower(), (
        "the reply does not name the heading that was not posted"
    )
    log = league.log()
    approved = log[log.index("PENALTY_REVIEW_APPROVED | Incomplete"):]
    assert "heading" in approved and "not posted" in approved


async def test_a_sanction_card_s_own_heading_is_posted_once_and_recorded(tmp_path):
    """A clean round with two drivers over a threshold: one heading goes up, ahead of both
    cards, and is recorded as heading the round's sanctions, so a replay keeps it."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    _cards_posted_for_real(league)
    await _approve(league, staged=[])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 3, "one heading over the two cards, and no other is posted"
    steps = await step_rows(league.db_path)
    assert len([row for row in steps if row["name"] == "announce_heading"]) == 1
    assert await one(
        league.db_path,
        "SELECT COUNT(*) FROM verdict_banner_messages WHERE round_id = ? AND message_id = ? "
        "AND heads_sanctions = 1",
        ROUND_ID, str(sent[0]),
    ) == 1
    assert await one(league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages") == 1


async def test_a_sanction_card_after_a_discarded_verdicts_heading_stands_beneath_none(tmp_path):
    """This approval announces verdicts, and their heading is discarded: the verdicts go out
    beneath none. Max's sanction then applies, and its card goes out beneath none too, as the
    verdicts do: one heading per approval, so a discarded one is not raised again over the cards.
    No second heading is planned, and none is posted."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    _cards_posted_for_real(league)
    league.channel(VERDICTS_CHANNEL).fail_when = lambda content, _kwargs: content == HEADING
    await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "announce_heading"

    # The channel would take a heading now, so a build that planned one over the cards shows.
    league.channel(VERDICTS_CHANNEL).fail_when = None
    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert MAX_PROFILE in league.attendance.applied
    assert league.attendance._calls("announce_sanction") == [("announce_sanction", MAX_PROFILE)]
    assert verdict_headings(league) == []
    assert len(league.sent_to(VERDICTS_CHANNEL)) == 3, "two verdicts and the card, no heading"
    steps = await step_rows(league.db_path)
    assert len([row for row in steps if row["name"] == "announce_heading"]) == 1
    assert await one(league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages") == 0


def _named(steps: list[dict[str, Any]], name: str, profile: int | None = None) -> list[int]:
    """The places in *steps* of each job called *name*, of *profile*'s candidate where given."""
    import json

    return [
        index for index, row in enumerate(steps)
        if row["name"] == name and (
            profile is None
            or json.loads(row["payload"]).get("candidate", {}).get("driver_profile_id") == profile
        )
    ]


async def test_a_sanction_card_s_heading_is_planned_by_the_first_sanction_that_applies(tmp_path):
    """A clean round, Lewis and Max over a threshold. Lewis's sanction does not apply and is
    discarded; Max's applies. The one heading is planned by Max's `apply_sanction`, after it and
    ahead of his card, not when the sanctions are planned: a heading planned there would stand
    ahead of a sanction that might never apply."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {LEWIS_PROFILE: ValueError("Pro has no reserve team")}
    _cards_posted_for_real(league)
    await _approve(league, staged=[])
    await run_queue(league.bot)
    assert await stopped_at(league) == "apply_sanction"
    assert _named(await step_rows(league.db_path), "announce_heading") == [], (
        "a heading was planned before any sanction applied"
    )

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    steps = await step_rows(league.db_path)
    [heading] = _named(steps, "announce_heading")
    [applied] = _named(steps, "apply_sanction", MAX_PROFILE)
    [card] = _named(steps, "announce_sanction", MAX_PROFILE)
    assert applied < heading < card
    sent = league.sent_to(VERDICTS_CHANNEL)
    assert verdict_headings(league) == sent[:1]
    assert len(sent) == 2, "the heading and Max's card"


async def test_no_sanction_card_heading_is_planned_where_no_sanction_applies(tmp_path):
    """A clean round, Lewis and Max over a threshold. Lewis's sanction does not apply and is
    discarded, and Max's is dropped, `/attendance sync` having sanctioned him meanwhile. No
    sanction of this approval applied, so no card follows and no heading is planned."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {LEWIS_PROFILE: ValueError("Pro has no reserve team")}
    _cards_posted_for_real(league)
    await _approve(league, staged=[])
    await run_queue(league.bot)
    assert await stopped_at(league) == "apply_sanction"

    league.attendance.applied.add(MAX_PROFILE)  # `/attendance sync` sanctioned Max meanwhile
    await discard_job(league.bot)

    assert await stopped_at(league) is None
    steps = await step_rows(league.db_path)
    [max_apply] = _named(steps, "apply_sanction", MAX_PROFILE)
    assert (steps[max_apply]["result"] or {}).get("dropped") is True
    assert _named(steps, "announce_heading") == []
    assert league.sent_to(VERDICTS_CHANNEL) == []


async def test_a_sanction_card_s_heading_retried_after_attendance_is_turned_off_is_dropped(
    tmp_path,
):
    """A clean round with Max over a threshold: his sanction applies and its heading is planned,
    but the verdicts channel has been deleted and the heading stops the queue. While it is
    stopped, a league admin turns attendance off, which the queue does not carry, and then the
    channel is set again. On Retry the heading is no longer due, since the card it would head is
    attendance's and attendance posts nothing while it is off: it is dropped, and nothing is
    posted in the verdicts channel, so no heading stands over no card."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    _cards_posted_for_real(league)
    verdicts = league.channels.pop(VERDICTS_CHANNEL)
    await _approve(league, staged=[])
    await run_queue(league.bot)
    assert await stopped_at(league) == "announce_heading"
    assert MAX_PROFILE in league.attendance.applied

    await league.switch_attendance(False)
    league.channels[VERDICTS_CHANNEL] = verdicts
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert league.sent_to(VERDICTS_CHANNEL) == [], "a heading was posted over no card"
    [heading] = [row for row in await step_rows(league.db_path)
                 if row["name"] == "announce_heading"]
    assert heading["done_at"] is not None
    assert (heading["result"] or {}).get("dropped") is True
    assert await one(league.db_path, "SELECT COUNT(*) FROM verdict_banner_messages") == 0


def _apply_ctx(league: ReviewLeague) -> Any:
    """A job's context for Max's autoreserve in Pro, on a round with no banner recorded."""
    ctx = MagicMock()
    ctx.db_path = league.db_path
    ctx.steps = ()
    ctx.tries = 0
    ctx.step_payload = {"round_id": ROUND_ID, "division_id": DIVISION_ID, "division": "Pro",
                        "candidate": candidate(MAX_PROFILE, MAX),
                        "hint": "Repair the cause, then run `/attendance sync`."}
    return ctx


@pytest.mark.xfail(strict=True, reason=SANCTION_SAVE_UNBUILT)
@pytest.mark.parametrize("applied, then", [
    (None, []),
    ({"line": "ATTENDANCE_AUTORESERVE | x",
      "roles": {"user_id": MAX, "revoke": [5502], "grant": [5503]}},
     ["sanction_roles", "announce_heading"]),
    ({"line": "ATTENDANCE_AUTORESERVE | x", "roles": None}, ["announce_heading"]),
])
async def test_a_sanction_the_hook_did_not_apply_plans_no_heading(tmp_path, applied, then):
    """Max's autoreserve is saved on the save the queue opens. The hook answers
    `apply_sanction_on` with None only where attendance is off and nothing was applied: no role
    job and no card follow, so the job plans nothing. Where it applied, on a clean round with no
    banner recorded, the job plans the role change the hook named, then the heading over the
    cards; a sanction naming no roles (a test driver's) plans the heading alone."""
    from leaguebot.core.models.change import StepKind
    from leaguebot.results.services import review_verdicts
    from tests.support.review_league import NOW

    league = await review_league(tmp_path, attendance=True)
    hook = MagicMock()
    hook.apply_sanction_on = AsyncMock(return_value=applied)
    step = review_verdicts.verdict_steps(lambda: NOW, hook)[review_verdicts.APPLY_SANCTION]
    assert step.kind is StepKind.SAVE, "the sanction is not applied on the queue's save"

    async with get_connection(league.db_path) as db:
        result = await step.run(db, _apply_ctx(league))

    assert [planned.name for planned in result.then] == then


@pytest.mark.xfail(strict=True, reason=SANCTION_SAVE_UNBUILT)
async def test_a_fault_working_out_the_cards_heading_rolls_the_sanction_back(
    tmp_path, monkeypatch,
):
    """Max's autoreserve is saved on the save the queue opens, the hook writing it on that save,
    and reading the round's banners for the cards' heading then fails. The job fails having
    committed nothing, so the save is rolled back whole and the sanction is not applied: on Retry
    it is still owed, and its card is not lost."""
    from leaguebot.core.models.change import StepKind
    from leaguebot.results.services import review_verdicts
    from leaguebot.results.services import verdict_announcement_service
    from tests.support.review_league import NOW

    league = await review_league(tmp_path, attendance=True)
    async with get_connection(league.db_path) as db:
        await db.execute("CREATE TABLE sanction_written (driver_profile_id INTEGER)")
        await db.commit()

    async def _apply_on(db: Any, _round_id: int, _division_id: int,
                        owed: dict[str, Any]) -> dict[str, Any]:
        await db.execute("INSERT INTO sanction_written VALUES (?)", (owed["driver_profile_id"],))
        return {"line": "ATTENDANCE_AUTORESERVE | x", "roles": None}

    async def _unreadable(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("database is locked")

    for module, name in ((verdict_announcement_service, "_banners_of"),
                         (verdict_announcement_service, "_banners_of_on"),
                         (review_verdicts, "_sanction_heading_on")):
        monkeypatch.setattr(module, name, _unreadable, raising=False)
    hook = MagicMock()
    hook.apply_sanction_on = AsyncMock(side_effect=_apply_on)
    hook.sync_hint = AsyncMock(return_value="Repair the cause, then run `/attendance sync`.")
    step = review_verdicts.verdict_steps(lambda: NOW, hook)[review_verdicts.APPLY_SANCTION]
    assert step.kind is StepKind.SAVE, "the sanction is not applied on the queue's save"

    async with get_connection(league.db_path) as db:
        with pytest.raises(Exception):
            await step.run(db, _apply_ctx(league))
        await db.rollback()

    assert await one(league.db_path, "SELECT COUNT(*) FROM sanction_written") == 0


#: What a league admin is told where the job working out who is owed a sanction was discarded.
SANCTIONS_PLAN_DISCARDED = (
    "⚠️ The attendance sanctions of Pro were not worked out, so none was applied. Repair the "
    "cause, then run `/attendance sync division:Pro round:4`."
)


async def test_a_failed_sanctions_plan_keeps_the_sync_hint_for_its_discard(tmp_path):
    """A fault reading which drivers are owed a sanction fails the `plan_sanctions` job as the
    sheet's and a sanction's do: the failure keeps the hook's `attendance sync` hint on its kept
    result, for the line a Discard writes, and the fault stays its cause, which the stop notice
    names by its type."""
    from leaguebot.results.services import review_verdicts

    league = await review_league(tmp_path, attendance=True)
    hook = MagicMock()
    fault = RuntimeError("database is locked")
    hook.sanction_candidates = AsyncMock(side_effect=fault)
    hook.sync_hint = AsyncMock(
        return_value="Repair the cause, then run `/attendance sync division:Pro round:3`."
    )
    ctx = MagicMock()
    ctx.db_path = league.db_path
    ctx.steps = ()
    ctx.step_payload = {"round_id": ROUND_ID, "division_id": DIVISION_ID, "division": "Pro"}

    with pytest.raises(StepFailedOnDiscord) as raised:
        await review_verdicts._plan_sanctions(ctx, hook)

    assert raised.value.result["hint"] == (
        "Repair the cause, then run `/attendance sync division:Pro round:3`."
    )
    assert raised.value.__cause__ is fault


async def test_a_discarded_sanctions_plan_makes_the_penalty_approval_incomplete_naming_attendance_sync(
    tmp_path,
):
    """A league admin discards the job working out which drivers of Pro are owed an attendance
    sanction, after the reports were approved with attendance on. No sanction was weighed, so
    the approval is `Incomplete`, its reply and log line naming the sanctions not worked out and
    the `attendance sync` command that finishes them, never `Success` (attendance
    specification, "A run of the sanctions that cannot begin at all")."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.candidates_fail = RuntimeError("database is locked")
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "plan_sanctions"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance._calls("apply_sanction") == []
    reply = updated_reply(interaction)
    assert SANCTIONS_PLAN_DISCARDED in reply
    log = league.log()
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in log
    assert "PENALTY_REVIEW_APPROVED | Success" not in log
    approved = log[log.index("PENALTY_REVIEW_APPROVED | Incomplete"):]
    assert SANCTIONS_PLAN_DISCARDED in approved


async def test_a_sanction_that_does_not_apply_stops_the_queue(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {MAX_PROFILE: ValueError("Pro has no reserve team")}
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "apply_sanction"
    assert league.attendance._calls("announce_sanction") == []
    notice = league.log()
    assert str(MAX) in notice, "the stop notice does not name the driver"
    assert "reserve" in notice.lower(), "the stop notice does not name the sanction"
    assert "/attendance sync" not in notice, (
        "sync was named while the bot still tries the sanction"
    )


async def test_a_discarded_sanction_is_named_with_attendance_sync_and_the_others_go_ahead(
    tmp_path,
):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {LEWIS_PROFILE: ValueError("Pro has no reserve team")}
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "apply_sanction"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert MAX_PROFILE in league.attendance.applied
    assert LEWIS_PROFILE not in league.attendance.applied
    reply = updated_reply(interaction)
    assert "Lewis" in reply or f"<@{LEWIS}>" in reply
    assert "/attendance sync" in reply
    assert "/attendance sync" in league.log()


async def test_a_retried_sanction_run_applies_only_what_is_owed(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS),
                                    candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {MAX_PROFILE: ValueError("Discord refused the role change")}
    await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "apply_sanction"

    league.attendance.applied.add(MAX_PROFILE)  # `/attendance sync` sanctioned Max meanwhile
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    applied = [c[1] for c in league.attendance.calls if c[0] == "apply_sanction"]
    assert applied == [LEWIS_PROFILE, MAX_PROFILE], "a sanction no longer owed was applied again"


# ---------------------------------------------------------------------------
# A sanction's check, save and roles, each a job (#439, Part C)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_a_sanction_s_roles_are_a_job_of_their_own_between_its_save_and_its_card(tmp_path):
    """Max is owed an autoreserve in Pro. Alex approves round 3's reports: Max's sanction is saved
    by one job, his roles are changed by the next (his team's role taken, the Reserve team's
    given), and only then is his card posted (attendance spec :184)."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    steps = await step_rows(league.db_path)
    [saved] = _named(steps, "apply_sanction", MAX_PROFILE)
    [roles] = _named(steps, "sanction_roles", MAX_PROFILE)
    [card] = _named(steps, "announce_sanction", MAX_PROFILE)
    assert saved < roles < card
    assert league.attendance.role_changes == [(MAX_PROFILE, [5502], [5503])]


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_a_role_change_discord_refuses_stops_the_queue_with_the_sanction_saved_and_its_card_waiting(
    tmp_path,
):
    """Max is owed an autoreserve in Pro, and Discord refuses his role change. The queue stops at
    the role job, named in its notice; his sanction stands saved and his card is not posted. A
    league manager presses Retry: his roles are changed and his card posted, and he is
    sanctioned once."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.roles_fails = {
        MAX_PROFILE: StepFailedOnDiscord("Discord refused the role change"),
    }
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "sanction_roles"
    assert league.attendance._calls("apply_sanction") == [("apply_sanction", MAX_PROFILE)]
    assert league.attendance._calls("announce_sanction") == []
    assert f"changing <@{MAX}>'s roles for their autoreserve in **Pro**" in league.log()

    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance.role_changes == [(MAX_PROFILE, [5502], [5503])] * 2
    assert league.attendance._calls("apply_sanction") == [("apply_sanction", MAX_PROFILE)]
    assert league.attendance._calls("announce_sanction") == [("announce_sanction", MAX_PROFILE)]


#: What the reply and the log name for each sanction whose role job a league admin discarded.
ROLES_NOT_TAKEN = (
    f"⚠️ The autosack of <@{LEWIS}> was applied but their roles were not taken back. "
    "Take <@&5501> and <@&5502> from them by hand."
)
ROLES_NOT_CHANGED = (
    f"⚠️ The autoreserve of <@{MAX}> was applied but their roles were not changed. "
    "Take <@&5502> from them and give them <@&5503> by hand."
)


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_a_discarded_role_change_names_the_roles_to_change_by_hand_and_the_card_still_posts(
    tmp_path,
):
    """Lewis is owed an autosack and Max an autoreserve in Pro, and Discord refuses both role
    changes. A league admin discards each role job as it stops the queue. Both sanctions stand,
    both cards are posted, and the reply and the log name, for each driver, the roles to take or
    give by hand, as role mentions."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS, "AUTOSACK"),
                                    candidate(MAX_PROFILE, MAX)]
    refused = StepFailedOnDiscord("Discord refused the role change")
    league.attendance.roles_fails = {LEWIS_PROFILE: refused, MAX_PROFILE: refused}
    interaction = await _approve(league)
    await run_queue(league.bot)
    for _ in range(2):
        assert await stopped_at(league) == "sanction_roles"
        await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert sorted(league.attendance._calls("announce_sanction")) == [
        ("announce_sanction", LEWIS_PROFILE), ("announce_sanction", MAX_PROFILE),
    ]
    reply = updated_reply(interaction)
    for line in (ROLES_NOT_TAKEN, ROLES_NOT_CHANGED):
        assert line in reply
        assert line in league.log()


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_an_autoreserve_is_checked_before_it_is_saved(tmp_path):
    """Lewis is owed an autosack and Max an autoreserve in Pro. Alex approves round 3's reports:
    Max's move to the Reserve team is checked by a job of its own ahead of the job that saves it;
    Lewis's autosack has no check."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(LEWIS_PROFILE, LEWIS, "AUTOSACK"),
                                    candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    steps = await step_rows(league.db_path)
    [checked] = _named(steps, "check_sanction", MAX_PROFILE)
    [saved] = _named(steps, "apply_sanction", MAX_PROFILE)
    assert checked < saved
    assert _named(steps, "check_sanction", LEWIS_PROFILE) == []
    assert league.attendance.checks == [MAX_PROFILE]


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_a_discarded_check_leaves_the_autoreserve_unapplied_and_named_with_attendance_sync(
    tmp_path,
):
    """Max is owed an autoreserve in Pro, and its check finds Pro has no Reserve team, stopping
    the queue at the check. A league admin discards it: Max is not sanctioned, no role is changed
    and no card posted, and the reply and the log say his autoreserve was not applied, naming
    `/attendance sync`."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.check_fails = {
        MAX_PROFILE: StepFailedOnDiscord("Pro has no Reserve team"),
    }
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "check_sanction"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance._calls("apply_sanction") == []
    assert league.attendance.role_changes == []
    assert league.attendance._calls("announce_sanction") == []
    reply = updated_reply(interaction)
    assert f"The autoreserve of <@{MAX}> was not applied" in reply
    assert "/attendance sync" in reply
    assert f"The autoreserve of <@{MAX}> was not applied" in league.log()


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_a_test_driver_s_sanction_plans_no_role_job(tmp_path):
    """Max, a test driver with no roles to change, is owed an autoreserve in Pro. Alex approves
    round 3's reports: his sanction is saved and his card posted, and no role job is planned."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.no_roles = {MAX_PROFILE}
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    steps = await step_rows(league.db_path)
    assert _named(steps, "check_sanction", MAX_PROFILE), "the autoreserve was not checked"
    assert _named(steps, "sanction_roles", MAX_PROFILE) == []
    assert league.attendance.role_changes == []
    assert league.attendance._calls("announce_sanction") == [("announce_sanction", MAX_PROFILE)]


@pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)
async def test_a_restart_between_a_sanction_s_save_and_its_roles_changes_the_roles_from_the_job(
    tmp_path,
):
    """Max is owed an autoreserve in Pro. The approval stops just after his sanction is saved,
    before his roles are changed, and the bot restarts. Once the queue runs, his roles are changed
    from the role job, as the save named them, his card is posted once and he is not sanctioned
    again."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    await _approve(league)
    await run_until_done(league, "apply_sanction")
    assert _named(await step_rows(league.db_path), "sanction_roles", MAX_PROFILE), (
        "the save planned no role job"
    )
    assert league.attendance.role_changes == []

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance.role_changes == [(MAX_PROFILE, [5502], [5503])]
    assert league.attendance._calls("apply_sanction") == [("apply_sanction", MAX_PROFILE)]
    assert league.attendance._calls("announce_sanction") == [("announce_sanction", MAX_PROFILE)]


#: Round 5 of the other division (Am), final, where its attendance totals were last recorded.
OTHER_ROUND_ID = 25


@pytest.mark.parametrize("case", [
    "scored",
    "discarded",
    "unscored",
])
async def test_a_sacked_driver_s_other_division_sheet_is_posted_again_as_a_job(tmp_path, case):
    """Max, over the autosack threshold, also sat in division 12 (Am); Lewis is owed an
    autoreserve in Pro alone. Pro's sheet is posted before the sanctions and again after them,
    and Am's is posted again once Max is sacked, drawn as at Am's own latest scored round
    (round 5), not at the round the sack was decided at, whose number means nothing there.

    Where Max's sack is discarded, Am's sheet is not posted: its only change was his, though
    Lewis's sanction applied and Pro's sheet is posted again, marking Lewis alone as having
    reached the point limit, not Max, whose sack never happened. Where Am has no scored round of its
    own, it has no sheet yet, so none is planned: two sheets, not three (owner, 2026-10-06)."""
    league = await review_league(tmp_path, attendance=True)
    if case != "unscored":
        async with get_connection(league.db_path) as db:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
                "track_name, status) VALUES (?, ?, 5, '2026-03-01T18:00:00+00:00', 'NORMAL', "
                "'Monza', 'FINAL')",
                (OTHER_ROUND_ID, OTHER_DIVISION_ID),
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status, attended, total_points_after) VALUES (?, ?, ?, 'ACCEPTED', 1, 4)",
                (OTHER_ROUND_ID, OTHER_DIVISION_ID, MAX_PROFILE),
            )
            await db.commit()
    league.attendance.candidates = [
        candidate(LEWIS_PROFILE, LEWIS),
        candidate(MAX_PROFILE, MAX, "AUTOSACK", other_divisions=(OTHER_DIVISION_ID,)),
    ]
    if case == "discarded":
        league.attendance.apply_fails = {MAX_PROFILE: ValueError("Discord refused the sack")}
    drawn: list[tuple[int, int]] = []
    marked: list[tuple[int, set[int]]] = []
    recorded = league.attendance.post_sheet

    async def _post_sheet(round_id: int, division_id: int, *, sanctioned: Any,
                          as_text: bool) -> None:
        drawn.append((division_id, round_id))
        marked.append((division_id, set(sanctioned)))
        await recorded(round_id, division_id, sanctioned=sanctioned, as_text=as_text)

    league.attendance.post_sheet = _post_sheet
    await _approve(league)
    await run_queue(league.bot)
    if case == "discarded":
        assert await stopped_at(league) == "apply_sanction"
        await discard_job(league.bot)

    assert await stopped_at(league) is None
    sheets = [row for row in await step_rows(league.db_path) if row["name"] == "attendance_sheet"]
    divisions = [division for division, _round in drawn]
    assert divisions[0] == DIVISION_ID
    assert marked[0] == (DIVISION_ID, set()), "the sheet before the sanctions marks nobody"
    if case == "scored":
        assert len(sheets) == 3
        assert sorted(divisions[1:]) == sorted([DIVISION_ID, OTHER_DIVISION_ID])
        assert (OTHER_DIVISION_ID, OTHER_ROUND_ID) in drawn
        assert (OTHER_DIVISION_ID, {MAX_PROFILE}) in marked[1:], "Am's sheet marks Max's sack"
    elif case == "discarded":
        assert divisions == [DIVISION_ID, DIVISION_ID]
        assert marked[1] == (DIVISION_ID, {LEWIS_PROFILE}), (
            "Pro's sheet after the sanctions marks only the sanction that applied"
        )
    else:
        assert len(sheets) == 2
        assert divisions == [DIVISION_ID, DIVISION_ID]


async def test_a_discarded_sanction_announcement_names_the_driver_as_applied_but_not_announced(
    tmp_path,
):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.announce_fails = {
        MAX_PROFILE: StepFailedOnDiscord("Missing Access"),
    }
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "announce_sanction"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert MAX_PROFILE in league.attendance.applied, "the sanction itself was undone"
    reply = updated_reply(interaction)
    assert "Max" in reply or f"<@{MAX}>" in reply
    assert "applied" in reply.lower()
    assert "not announced" in reply.lower()
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()


async def test_a_discarded_lineup_refresh_says_it_is_posted_with_the_next_change_to_the_drivers(
    tmp_path,
):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.lineup_fails = [StepFailedOnDiscord("Missing Access")]
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "refresh_lineup"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert MAX_PROFILE in league.attendance.applied
    reply = updated_reply(interaction)
    assert "lineup" in reply.lower()
    assert "next change" in reply.lower()
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()


async def test_a_lineup_refused_after_a_sanction_is_retried_as_text(tmp_path):
    """Max is owed an autoreserve; Discord refuses Pro's lineup once. The queue stops at
    `refresh_lineup`, and Retry asks for the lineup as text, as any job tried again does
    (Constitution XIV, rule 8)."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.lineup_fails = [StepFailedOnDiscord("Missing Access")]
    await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "refresh_lineup"

    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance.lineup_as_text == [False, True]


async def _lewis_owed_an_autoreserve(league: ReviewLeague) -> None:
    """Division 11 sets an autoreserve threshold of one point and has a Reserve team with a free
    seat. Lewis, in the seat of team 3001, raced round 4, the last final round and so where the
    division's totals stand, without an RSVP, which puts him on the threshold."""
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE attendance_config SET autoreserve_threshold = 1")
        seat = await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (3001, 1, ?)",
            (LEWIS_PROFILE,),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
            "team_seat_id) VALUES (?, 1, ?, ?)",
            (LEWIS_PROFILE, DIVISION_ID, seat.lastrowid),
        )
        reserve = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, 'Reserve', 'Reserve', 2, 1)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
            (reserve.lastrowid,),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
            "rsvp_status, attended) VALUES (?, ?, ?, 'NO_RSVP', 1)",
            (LATER_ROUND_ID, DIVISION_ID, LEWIS_PROFILE),
        )
        await db.commit()


async def test_a_sanction_posts_each_lineup_it_touches_once(tmp_path):
    """Attendance is on, with its real hook and the real placement service (roles not the
    subject, so granted and revoked by doubles). Lewis is owed an autoreserve in Pro. Alex
    approves round 3's reports: Lewis is moved to Pro's Reserve team, and Pro's lineup is posted
    once, by the review's own job, not by the move as well (core spec: "posted once")."""
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview
    from leaguebot.core.services.placement_service import LineupPosted, PlacementService
    from tests.support.change_queue import attach_queue
    from tests.support.review_league import NOW, channel

    league = await review_league(tmp_path, attendance=True)
    await _lewis_owed_an_autoreserve(league)
    # Pro's attendance channel is set, as a missing one now stops the queue at the sheet.
    league.channels[707] = channel(707, league.events)
    league.channels[707].guild = league.guild
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id) "
            "VALUES (?, ?)",
            (DIVISION_ID, "707"),
        )
        await db.commit()
    league.bot.user = MagicMock(id=9000)
    league.bot.user.__str__.return_value = "LeagueBot"
    placement = PlacementService(league.db_path, league.bot)
    placement._grant_roles = AsyncMock()
    placement._revoke_roles = AsyncMock()
    placement.grant_roles = AsyncMock()
    placement.revoke_roles = AsyncMock()
    # Pro has no lineup channel of its own here: the post is stood in for, as one posted.
    placement._refresh_lineup_post = AsyncMock(return_value=LineupPosted(
        message_id=9100, channel_id=9101, previous=None, division_name="Pro", form="text",
    ))
    league.bot.attendance_after_review = AttendanceAfterReview(league.bot, placement)
    attach_queue(league.bot, league.db_path, now=NOW)

    await _approve(league, staged=[penalty(LEWIS)])
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await one(
        league.db_path,
        "SELECT ti.is_reserve FROM driver_season_assignments dsa "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = ?", LEWIS_PROFILE,
    ) == 1, "Lewis was not moved to the Reserve team"
    posted = [call.args[1] for call in placement._refresh_lineup_post.await_args_list]
    assert posted == [DIVISION_ID]


@pytest.mark.parametrize("case", [
    pytest.param("applied", marks=pytest.mark.xfail(strict=True, reason=SANCTION_ROLE_JOBS_UNBUILT)),
    "discarded",
])
async def test_an_autosack_s_other_division_has_its_lineup_posted_as_a_job(tmp_path, case):
    """Max, over the autosack threshold, also sat in division 12 (Am). Once his sack applies, his
    division's and team's roles are taken back by a job of its own, and the review posts the
    lineup of Pro and of Am, each as a job of its own, since the sack itself no longer posts them.
    Where his sack is discarded, nothing changed in either division: no role is changed and
    neither lineup is posted."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [
        candidate(MAX_PROFILE, MAX, "AUTOSACK", other_divisions=(OTHER_DIVISION_ID,)),
    ]
    if case == "discarded":
        league.attendance.apply_fails = {MAX_PROFILE: ValueError("Discord refused the sack")}
    await _approve(league)
    await run_queue(league.bot)
    if case == "discarded":
        assert await stopped_at(league) == "apply_sanction"
        await discard_job(league.bot)

    assert await stopped_at(league) is None
    lineups = [row for row in await step_rows(league.db_path) if row["name"] == "refresh_lineup"]
    if case == "applied":
        assert league.attendance._calls("refresh_lineup") == [
            ("refresh_lineup", DIVISION_ID), ("refresh_lineup", OTHER_DIVISION_ID),
        ]
        assert len(lineups) == 2
        assert league.attendance.role_changes == [(MAX_PROFILE, [5501, 5502], [])]
    else:
        assert league.attendance._calls("refresh_lineup") == []
        assert league.attendance.role_changes == []


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


async def test_a_second_approval_while_the_first_is_queued_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await review_league(tmp_path)
    await _approve(league)
    second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    assert len(await changes_of(league.db_path, KIND)) == 1


async def test_a_second_approval_while_the_first_is_running_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await review_league(tmp_path)
    await _approve(league)
    await run_until_done(league, "names")
    second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    await run_queue(league.bot)
    assert len(await penalty_records(league.db_path)) == 2


async def test_a_second_approval_while_the_first_is_stopped_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await review_league(tmp_path)
    with points_fail():
        await _approve(league)
        await run_queue(league.bot)
        assert await stopped_at(league) == "apply"
        second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    assert len(await changes_of(league.db_path, KIND)) == 1


async def test_every_review_control_refuses_while_the_approval_is_in_hand(tmp_path):
    from leaguebot.results.services.penalty_wizard import (
        _BEING_APPROVED,
        PenaltyReviewState,
        _review_moved_on,
    )

    league = await review_league(tmp_path)
    state = PenaltyReviewState(
        round_id=ROUND_ID, division_id=DIVISION_ID, submission_channel_id=SUBMISSION_CHANNEL,
        session_types_present=[SessionType.FEATURE_RACE], db_path=league.db_path,
        bot=league.bot, prompt_message_id=PROMPT, round_number=3, division_name="Pro",
    )
    assert await _review_moved_on(state) is None

    with points_fail():
        await _approve(league)
        assert await _review_moved_on(state) == _BEING_APPROVED
        await run_queue(league.bot)
    assert await stopped_at(league) == "apply"
    assert await _review_moved_on(state) == _BEING_APPROVED


async def test_an_approval_of_a_review_moved_on_is_refused_when_it_runs(tmp_path):
    league = await review_league(tmp_path)
    holder = await block_queue(league)
    interaction = await _approve(league)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET prompt_message_id = 8950")
        await db.commit()

    holder["fail"] = False
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert (await changes_of(league.db_path, KIND))[0]["state"] == "REFUSED"
    assert await penalty_records(league.db_path) == []
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert "replaced by a newer one" in updated_reply(interaction)


async def test_a_reply_with_only_pardons_names_them_and_one_with_nothing_staged_says_so(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    pardoned = await _approve(league, staged=[], pardons=[pardon()])
    await run_queue(league.bot)

    reply = updated_reply(pardoned)
    assert "Round 3's reports are approved (Pro)" in reply
    assert "pardon" in reply.lower()
    assert "0 penalties" not in reply

    (tmp_path / "nothing").mkdir()
    other = await review_league(tmp_path / "nothing")
    nothing = await _approve(other, staged=[])
    await run_queue(other.bot)

    reply = updated_reply(nothing)
    assert "Round 3's reports are approved (Pro)" in reply
    assert "0 penalties" not in reply
    assert "nothing" in reply.lower() or "no penalties" in reply.lower()


async def test_an_approval_queued_while_the_division_is_being_amended_is_refused_when_it_runs(
    tmp_path,
):
    league = await review_league(tmp_path)
    holder = await block_queue(league)
    interaction = await _approve(league)
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "expires_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-10-05T11:55:00+00:00', "
            "'2026-10-05T12:25:00+00:00')",
            (LATER_ROUND_ID, AMEND_CHANNEL),
        )
        await db.commit()

    holder["fail"] = False
    await retry_job(league.bot)

    assert (await changes_of(league.db_path, KIND))[0]["state"] == "REFUSED"
    assert await penalty_records(league.db_path) == []
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert "being amended" in updated_reply(interaction)


# ---------------------------------------------------------------------------
# What the manager and the log see
# ---------------------------------------------------------------------------


async def test_the_manager_is_told_the_approval_is_under_way_and_then_its_outcome(tmp_path):
    league = await review_league(tmp_path)
    interaction = await _approve(league)

    first = acknowledgement(interaction)
    assert first.startswith("⏳")
    assert "job #" in first

    await run_queue(league.bot)
    reply = updated_reply(interaction)
    assert "✅ Round 3's reports are approved (Pro): 2 penalties applied." in reply
    assert "appeals review is posted below" in reply


async def test_the_approval_writes_penalty_review_approved_with_its_audit_body(tmp_path):
    league = await review_league(tmp_path)
    await _approve(league)
    await run_queue(league.bot)

    lines = [line for line in league.bot.log_channel.sent if "PENALTY_REVIEW_APPROVED" in line]
    assert len(lines) == 1
    line = lines[0]
    assert "PENALTY_REVIEW_APPROVED | Success" in line
    assert "Alex" in line
    assert "round: 3 (Pro)" in line
    assert "penalties: 2" in line
    assert "old=" in line and "new=" in line
    assert "AWAITING_APPEAL_VERDICTS" in line


async def test_the_prompt_and_approval_message_are_taken_down_after_the_reposts(tmp_path):
    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET approval_message_id = ?",
                         (APPROVAL,))
        await db.commit()
    await _approve(league, approval=APPROVAL)
    await run_queue(league.bot)

    submission = league.channel(SUBMISSION_CHANNEL).messages
    assert PROMPT not in submission and APPROVAL not in submission
    events = league.events
    posted = events.index(("send", RESULTS_CHANNEL, league.sent_to(RESULTS_CHANNEL)[0]))
    assert events.index(("delete", SUBMISSION_CHANNEL, PROMPT)) > posted
    assert events.index(("delete", SUBMISSION_CHANNEL, APPROVAL)) > posted


async def test_the_appeals_prompt_id_is_saved(tmp_path):
    league = await review_league(tmp_path)
    await _approve(league)
    await run_queue(league.bot)

    prompts = appeals_prompts(league)
    assert len(prompts) == 1
    assert await one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompts[0]


async def test_attendance_switched_off_before_its_jobs_drops_them(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    interaction = await _approve(league)
    await run_until_done(league, "apply")
    assert len(league.attendance._calls("record_on")) == 1

    await league.switch_attendance(False)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert league.attendance._calls("post_sheet") == []
    assert league.attendance._calls("apply_sanction") == []
    assert (await changes_of(league.db_path, KIND))[0]["state"] == "DONE"
    assert "✅" in updated_reply(interaction)


async def test_the_save_awaits_nothing_but_its_connection(tmp_path):
    league = await review_league(tmp_path)
    await _approve(league)
    await run_until_done(league, "names")

    names = [row for row in await step_rows(league.db_path) if row["name"] == "names"][0]
    assert "Lewis" in str(names["result"]) and "Max" in str(names["result"])

    asked = AssertionError("the save asked Discord for a name")
    league.guild.get_member = MagicMock(side_effect=asked)
    league.guild.fetch_member = AsyncMock(side_effect=asked)
    await run_until_done(league, "apply")

    assert await stopped_at(league) is None
    assert len(await penalty_records(league.db_path)) == 2


# ---------------------------------------------------------------------------
# The appeals prompt through the round's lane (#439, slice 6b, C44b)
# ---------------------------------------------------------------------------


async def test_the_appeals_prompt_is_posted_on_the_round_s_lane(tmp_path):
    """Round 3's reports are approved by Alex while Discord refuses the appeals prompt. The
    approval finishes on the league lane, the round moved on to its appeals; round 3's lane, not
    the league's, stops at the appeals prompt's post. Retry on the round's lane posts it and saves
    its id."""
    lane = f"channel:{SUBMISSION_CHANNEL}"
    league = await review_league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).fail_when = is_appeals_prompt

    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_job(league.db_path, "league") is None
    stopped = await stopped_job(league.db_path, lane)
    assert stopped is not None
    [approval] = await changes_of(league.db_path, KIND)
    assert approval["state"] == "DONE"
    assert stopped["change_id"] != approval["id"]
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert appeals_prompts(league) == []

    league.channel(SUBMISSION_CHANNEL).fail_when = None
    await retry_job(league.bot, lane=lane)

    assert await stopped_job(league.db_path) is None
    [prompt] = appeals_prompts(league)
    assert await one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompt


# ---------------------------------------------------------------------------
# Approve waits for the staging (#439, slice 6b, C56a; decision 26)
# ---------------------------------------------------------------------------

async def test_approve_is_refused_while_a_staging_is_in_hand(tmp_path):
    """Round 3's lane is stopped, so the penalty for Lewis Alex has just added is still waiting
    to be shown on the prompt. Alex presses Approve on the review: it is refused at the press,
    telling him a change to the review is still being shown and to press Approve once it appears;
    nothing is applied and round 3 still awaits its report verdicts."""
    from tests.results.test_review_stage_change import LANE, _add_penalty, _alex, _state

    league = await review_league(tmp_path)
    await block_queue(league, lane=LANE)
    await _add_penalty(_state(league), _alex(league))

    interaction = await _approve(league)
    await run_queue(league.bot)

    # Refused at the press, so no change of the approval's is saved at all.
    assert await changes_of(league.db_path, KIND) == []
    said = acknowledgement(interaction) + updated_reply(interaction)
    assert "still being shown" in said
    assert await penalty_records(league.db_path) == []
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"


async def test_an_approve_on_the_prompt_goes_through_after_a_discarded_withdrawal(tmp_path):
    """Alex staged a penalty after No Penalties / Confirm had posted approval message 8901, so
    the review's state forgot it; Discord refused taking 8901 down and an admin discarded that,
    leaving the row naming 8901. Alex presses Approve on the prompt, which carries no approval
    message: no newer one stands, so it is not refused as replaced, and the reports are approved."""
    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET approval_message_id = ?",
                         (APPROVAL,))
        await db.commit()

    interaction = await _approve(league, staged=[], approval=None)
    await run_queue(league.bot)

    [approval] = await changes_of(league.db_path, KIND)
    assert approval["state"] != "REFUSED"
    assert "replaced by a newer one" not in updated_reply(interaction)
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


async def test_an_approval_whose_message_was_replaced_is_refused_as_it_runs(tmp_path):
    """Alex presses Approve on round 3's approval message 8901 while the league lane is stopped,
    so his approval waits. Before it runs, the review's approval message is replaced (the row now
    names 8960). When the stop clears his approval is refused: nothing applied, round 3 still in
    its report stage, and his answer says why."""
    league = await review_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET approval_message_id = ?",
                         (APPROVAL,))
        await db.commit()
    holder = await block_queue(league)
    interaction = await _approve(league, staged=[], approval=APPROVAL)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE round_submission_channels SET approval_message_id = 8960")
        await db.commit()

    holder["fail"] = False
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    [approval] = await changes_of(league.db_path, KIND)
    assert approval["state"] == "REFUSED"
    assert await penalty_records(league.db_path) == []
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert updated_reply(interaction) != ""
