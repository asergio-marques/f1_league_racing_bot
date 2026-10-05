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

Everything of the change type is imported inside a test, so this file collects while it is unbuilt.
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
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    APPROVAL,
    AMEND_CHANNEL,
    DIVISION_ID,
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
)

KIND = "results.reports.approve"
NOT_BUILT = "#439: the report approval is not yet a change on the queue"


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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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
    assert sorted(league.sent_to(VERDICTS_CHANNEL)) == sorted(announced)
    assert len(league.attendance._calls("record_on")) == 1
    assert len(appeals_prompts(league)) == 1
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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
    assert len(league.sent_to(VERDICTS_CHANNEL)) == 2
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_sheet_is_named_with_attendance_sync_and_the_sanctions_still_run(
    tmp_path,
):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [candidate(MAX_PROFILE, MAX)]
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    interaction = await _approve(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "attendance_sheet"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert ("apply_sanction", MAX_PROFILE) in league.attendance.calls
    reply = updated_reply(interaction)
    assert "attendance sheet" in reply.lower()
    assert "/attendance sync" in reply
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_sheet_is_not_put_on_the_old_retry_queue(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.sheet_fails = [StepFailedOnDiscord("Missing Access")]
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) == "attendance_sheet"
    assert await one(league.db_path, "SELECT COUNT(*) FROM pending_messages") == 0


# ---------------------------------------------------------------------------
# Defect 11: a repost Discord refuses
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_verdict_is_named_incomplete_and_the_manager_is_told_to_post_it(
    tmp_path,
):
    league = await review_league(tmp_path)
    league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


# ---------------------------------------------------------------------------
# Sanctions, one job per driver
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_each_driver_over_a_threshold_is_sanctioned_and_announced_in_jobs_of_their_own(
    tmp_path,
):
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_sacked_driver_s_other_division_sheet_is_posted_again_as_a_job(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [
        candidate(MAX_PROFILE, MAX, "AUTOSACK", other_divisions=(OTHER_DIVISION_ID,)),
    ]
    await _approve(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    sheets = [row for row in await step_rows(league.db_path) if row["name"] == "attendance_sheet"]
    assert len(sheets) == 3
    divisions = [call[1] for call in league.attendance._calls("post_sheet")]
    assert divisions[0] == DIVISION_ID
    assert sorted(divisions[1:]) == sorted([DIVISION_ID, OTHER_DIVISION_ID])


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_approval_while_the_first_is_queued_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await review_league(tmp_path)
    await _approve(league)
    second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    assert len(await changes_of(league.db_path, KIND)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_approval_while_the_first_is_running_is_refused(tmp_path):
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await review_league(tmp_path)
    await _approve(league)
    await run_until_done(league, "names")
    second = await _approve(league)

    assert acknowledgement(second) == _BEING_APPROVED
    await run_queue(league.bot)
    assert len(await penalty_records(league.db_path)) == 2


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_prompt_and_approval_message_are_taken_down_after_the_reposts(tmp_path):
    league = await review_league(tmp_path)
    await _approve(league, approval=APPROVAL)
    await run_queue(league.bot)

    submission = league.channel(SUBMISSION_CHANNEL).messages
    assert PROMPT not in submission and APPROVAL not in submission
    events = league.events
    posted = events.index(("send", RESULTS_CHANNEL, league.sent_to(RESULTS_CHANNEL)[0]))
    assert events.index(("delete", SUBMISSION_CHANNEL, PROMPT)) > posted
    assert events.index(("delete", SUBMISSION_CHANNEL, APPROVAL)) > posted


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_appeals_prompt_id_is_saved(tmp_path):
    league = await review_league(tmp_path)
    await _approve(league)
    await run_queue(league.bot)

    prompts = appeals_prompts(league)
    assert len(prompts) == 1
    assert await one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompts[0]


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
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
