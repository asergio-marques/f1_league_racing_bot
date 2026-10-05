"""Confirming a round's penalty review, then its appeals review, and an amendment's two stages.

Issue #208, carried onto the change queue (#439): each approval is a change, asked here as its
Approve control asks it and carried out by the queue's real change types on
`tests.support.review_league`'s league (`results.reports.approve`, `results.appeals.approve`, and an
amendment's `results.amendment.reports.approve` and `results.amendment.appeals.approve`).

**A settled round is never reopened.** The review views outlive the round: disabling the results
module closes every round still awaiting review, but a client already holding the message can
still press the button. An approval is checked again as it starts, so a stale press cannot drag a
FINAL or CANCELLED round back into an awaiting one (#167).

**A review that has moved on approves nothing, and one approval runs at a time** (#402). The
report approval is refused while a resubmission is collecting, once the reports are approved,
from a review whose prompt has been replaced, and while another approval of the round is in hand
on the queue, a stopped one included.

**The attendance is saved with the penalties, and the sheet and each sanction are jobs of their
own.** Where attendance is on, the round's record and its pardons are written in the approval's
one save, so a fault in them fails the approval whole; the sheet and each driver's sanction follow
as jobs, and one that fails stops the queue until it is retried or discarded. A discarded sanction
is told to the approving manager and the log channel, with the `/attendance sync` that finishes it
(#239).

**Approving appeals is what finishes a round, and so what finishes a division.** The round goes
to FINAL, the division's status is reconsidered (the last round's appeals are what lets
`/season complete` run, #154) and the submission channel's row is closed, all in the corrections'
save; the channel itself is deleted after the posts. Upheld corrections are recorded as appeal
records and announced; an announcement that fails stops the queue and never un-approves them.

**An amendment rewrites the round's decisions rather than adding to them** (#345), publishes
nothing before its last stage, and is not undone because a stage's job failed: the job stops the
queue and is retried ("Retry like any job").
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.services.penalty_service import StagedPenalty
from tests.results.test_amendment_stage_changes import (
    AMEND_APPEALS_PROMPT,
    AMEND_APPROVAL,
    AMEND_PROMPT,
)
from tests.results.test_amendment_stage_changes import _amend_league as amend_league
from tests.results.test_amendment_stage_changes import _amend_row as amend_row
from tests.support.change_queue import (
    acknowledgement,
    discard_job,
    http_error,
    member_interaction,
    run_queue,
    tier_member,
    updated_reply,
)
from tests.support.review_league import (
    AMEND_CHANNEL as AMENDMENT_CHANNEL,
    APPROVAL,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
    NOW,
    OLD_RESULTS,
    PROMPT,
    RESULTS_CHANNEL,
    SUBMISSION_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    appeals_prompts,
    block_queue,
    candidate as league_candidate,
    changes_of,
    is_appeals_prompt,
    one,
    pardon as league_pardon,
    penalty as league_penalty,
    penalty_records,
    points_fail,
    race_rows,
    review_league,
    round_status,
    run_until_done,
    stopped_at,
    verdict_headings,
)
from tests.support.teams import seed_team_instances

SERVER_ID = 13008
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 21
STEWARD = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "finalize",
    round_status: str = "AWAITING_REPORT_VERDICTS",
    attendance_row: bool = False,
    results: list[tuple[int, int, str]] | None = None,
    resubmitting: int = 0,
    prompt_message_id: int | None = None,
):
    """*results* is (profile_id, driver_user_id, outcome), given a profile and a race row
    apiece, for the tests that watch the former-driver flag (#216).

    *prompt_message_id* is the review prompt the channel records, which a review is current
    only while it matches (#402). A state's defaults to None, which matches the default here."""
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "status) VALUES (?, ?, 3, '2026-02-01T18:00:00+00:00', 'NORMAL', ?)",
            (ROUND_ID, DIVISION_ID, round_status),
        )
        await db.execute(
            "INSERT INTO round_submission_channels (round_id, channel_id, created_at, "
            "resubmitting, prompt_message_id) "
            "VALUES (?, 700, '2026-02-01T00:00:00+00:00', ?, ?)",
            (ROUND_ID, resubmitting, prompt_message_id),
        )
        if attendance_row:
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (31, '101', 'ASSIGNED')"
            )
            await db.execute(
                "INSERT INTO driver_round_attendance (id, round_id, division_id, "
                "driver_profile_id, rsvp_status) VALUES (41, ?, ?, 31, 'NO_RSVP')",
                (ROUND_ID, DIVISION_ID),
            )
        if results:
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status, "
                "config_name) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard')",
                (ROUND_ID, DIVISION_ID),
            )
            for position, (profile_id, driver, outcome) in enumerate(results, start=1):
                await db.execute(
                    "INSERT OR IGNORE INTO driver_profiles (id, discord_user_id, "
                    "current_state, former_driver) VALUES (?, ?, 'ASSIGNED', 0)",
                    (profile_id, str(driver)),
                )
                await db.execute(
                    "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                    "team_instance_id, finishing_position, outcome, driver_profile_id) "
                    "VALUES (?, ?, 3001, ?, ?, ?)",
                    (session.lastrowid, driver, position, outcome, profile_id),
                )
        await db.commit()
    return db_path


# ---------------------------------------------------------------------------
# The penalty review, on the change queue (#439)
# ---------------------------------------------------------------------------
#
# Stage one is the change `results.reports.approve`, asked as the review's Approve control asks
# it and carried out by the queue's real change types on `tests.support.review_league`'s league:
# round 3 of division 11 (Pro), Lewis (101) and Max (102) in its Feature Race, its results and
# standings posted provisionally, its review prompt standing in the submission channel.

NOT_BUILT = "#439: the report approval is not yet a change on the queue"
REPORTS = "results.reports.approve"


def _alex() -> Any:
    return tier_member("manager", member_id=STEWARD, display_name="Alex", name="Alex#0001")


async def _ask_reports(league: ReviewLeague, *, staged: Any = (), pardons: Any = (),
                       prompt: int = PROMPT, approval: int | None = None) -> Any:
    """Alex presses Approve on round 3's penalty review; the queue is not yet run. Gives Alex's
    interaction."""
    interaction = member_interaction(league.bot, user=_alex())
    await league.bot.change_queue.ask(
        REPORTS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "staged": [item.to_payload() for item in staged],
            "pardons": [item.to_payload() for item in pardons],
            "prompt_message_id": prompt,
            "approval_message_id": approval,
        },
        interaction=interaction,
        what="✅ Approve on round 3's penalty review",
    )
    return interaction


async def _approve_reports(league: ReviewLeague, **payload: Any) -> Any:
    """Alex approves round 3's reports and the queue runs until it is clear or stopped."""
    interaction = await _ask_reports(league, **payload)
    await run_queue(league.bot)
    return interaction


async def _set(db_path: str, sql: str, *args: Any) -> None:
    async with get_connection(db_path) as db:
        await db.execute(sql, args)
        await db.commit()


def _refused_on_the_queue(league: ReviewLeague, interaction: Any, says: str) -> None:
    """Refused as it was asked: Alex told why, the refusal naming Alex in the log channel."""
    assert says in acknowledgement(interaction)
    assert f"refused for Alex (<@{STEWARD}>)" in league.log()


async def _nothing_approved(league: ReviewLeague) -> None:
    assert await changes_of(league.db_path, REPORTS) == []
    assert await penalty_records(league.db_path) == []
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert appeals_prompts(league) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_staged_penalties_are_applied(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert await stopped_at(league) is None
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert len(await penalty_records(league.db_path)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_round_moves_on_to_appeals(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league)

    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.parametrize("status", ["FINAL", "CANCELLED"])
@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_settled_round_is_not_reopened_by_a_stale_press(tmp_path, status):
    """#167: a client still holding the review message can press it after the round was
    closed, and an unguarded write would strand the season."""
    league = await review_league(tmp_path, round_status=status)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert await round_status(league.db_path) == status
    assert await penalty_records(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_reports_are_not_approved_while_the_results_are_being_resubmitted(tmp_path):
    """**#402, as reported.** Resubmit took the review prompt down and left the approval message,
    whose Approve finalised the round on the results the manager had just said were wrong:
    reposted them, awarded attendance from them, and moved the round on to appeals while the
    resubmission went on collecting."""
    league = await review_league(tmp_path)
    await _set(league.db_path, "UPDATE round_submission_channels SET resubmitting = 1")

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])

    _refused_on_the_queue(league, interaction, "being resubmitted")
    await _nothing_approved(league)
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_reports_are_not_approved_a_second_time(tmp_path):
    """**#402's second half.** The review prompt stayed up through the appeals stage, and its
    Approve ran the report approval again: the results reposted, the attendance pipeline run a
    second time, a second appeals prompt posted, and a log entry counting penalties the first
    approval had already applied — or had skipped."""
    league = await review_league(
        tmp_path, attendance=True, round_status="AWAITING_APPEAL_VERDICTS",
    )

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])

    _refused_on_the_queue(league, interaction, "already been approved")
    await _nothing_approved(league)
    assert league.attendance._calls("record_on") == []
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_review_replaced_after_a_cancelled_resubmission_approves_nothing(tmp_path):
    """Cancelling a resubmission posts a fresh review and left the old approval message standing
    beside it, still bound to the review from before. The round is back where it was, so only the
    prompt the channel records says which review is the round's."""
    league = await review_league(tmp_path)

    interaction = await _approve_reports(league, prompt=880001)

    _refused_on_the_queue(league, interaction, "replaced by a newer one")
    await _nothing_approved(league)
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_second_press_while_the_first_is_approving_is_refused(tmp_path):
    """**A double click ran the approval twice**, the second press finding the round exactly as
    the first had while the first drew its graphics. A second Approve pressed while the first is
    queued, and again while it is running, is refused and its refusal recorded (#482); the
    approval is carried out once."""
    from leaguebot.results.services.penalty_wizard import _BEING_APPROVED

    league = await review_league(tmp_path)
    await _ask_reports(league, staged=[league_penalty(LEWIS)])
    while_queued = await _ask_reports(league, staged=[league_penalty(LEWIS)])
    await run_until_done(league, "names")
    while_running = await _ask_reports(league, staged=[league_penalty(LEWIS)])
    await run_queue(league.bot)

    for interaction in (while_queued, while_running):
        assert acknowledgement(interaction) == _BEING_APPROVED
    assert league.log().count(f"refused for Alex (<@{STEWARD}>)") == 2
    assert len(await changes_of(league.db_path, REPORTS)) == 1
    assert len(await penalty_records(league.db_path)) == 1
    assert len(appeals_prompts(league)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_approving_the_reports_takes_the_prompt_and_the_approval_down(tmp_path):
    """**They stayed up through the appeals stage**, where every button on them still worked. They
    refuse now whether or not they come down; down, they are not there to be pressed."""
    league = await review_league(tmp_path)

    await _approve_reports(league, approval=APPROVAL)

    standing = league.channel(SUBMISSION_CHANNEL).messages
    assert PROMPT not in standing and APPROVAL not in standing
    assert len(appeals_prompts(league)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_take_down_that_fails_still_opens_the_appeals(tmp_path):
    """It is tidying: the round has already moved on. A take-down Discord refuses stops the queue
    like any job, and once a league admin discards it the appeals prompt behind it still comes."""
    league = await review_league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).messages[PROMPT].delete = AsyncMock(
        side_effect=http_error(discord.Forbidden, status=403, text="Missing Access")
    )

    await _approve_reports(league)
    assert await stopped_at(league) == "delete_message"
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert len(appeals_prompts(league)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_results_are_reposted_as_post_race_penalty_results(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    posted = league.channel(RESULTS_CHANNEL).messages
    assert any("Post-Race Penalty Results" in str(posted[mid].content)
               for mid in league.sent_to(RESULTS_CHANNEL))
    assert OLD_RESULTS not in posted


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_approval_is_logged_with_its_penalty_count(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[league_penalty(LEWIS), league_penalty(MAX)])

    assert "PENALTY_REVIEW_APPROVED" in league.log()
    assert "penalties: 2" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_review_with_no_penalties_says_none(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league)

    assert "penalties: none" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_failing_audit_log_does_not_stop_the_review(tmp_path):
    league = await review_league(tmp_path)
    league.bot.log_channel.send = AsyncMock(side_effect=RuntimeError("no log"))

    await _approve_reports(league)

    assert await stopped_at(league) is None
    assert len(appeals_prompts(league)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_applied_penalties_are_announced(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    (record,) = await penalty_records(league.db_path)
    assert record["announcement_message_id"] is not None
    assert int(record["announcement_message_id"]) in league.sent_to(VERDICTS_CHANNEL)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_failed_announcement_does_not_stop_the_review(tmp_path):
    """The penalties are applied and the round moved on by then. The verdict Discord refuses stops
    the queue; once a league admin discards it, the appeals prompt behind it still comes."""
    league = await review_league(tmp_path)
    league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")

    await _approve_reports(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == "announce_verdict"
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert len(appeals_prompts(league)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_appeals_prompt_is_posted_and_registered(tmp_path):
    league = await review_league(tmp_path)

    await _approve_reports(league)

    (prompt,) = appeals_prompts(league)
    assert any(type(call.args[0]).__name__ == "AppealsReviewView"
               for call in league.bot.add_view.call_args_list)
    assert await one(
        league.db_path, "SELECT appeals_prompt_message_id FROM round_submission_channels"
    ) == prompt


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_without_a_guild_the_round_still_moves_on(tmp_path):
    league = await review_league(tmp_path)
    league.bot.get_guild = MagicMock(return_value=None)

    await _approve_reports(league)

    assert league.sent_to(RESULTS_CHANNEL) == []
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_appeals_review_that_cannot_be_posted_is_reported(tmp_path):
    """The reports are approved, but Discord refuses the appeals prompt: it stops the queue like
    any job. Once a league admin discards it, Alex's reply says the reports are approved and the
    appeals review is being posted again, and the approval's line says it is incomplete."""
    league = await review_league(tmp_path)
    league.channel(SUBMISSION_CHANNEL).fail_when = is_appeals_prompt

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == "post_appeals_prompt"
    await discard_job(league.bot)

    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    said = updated_reply(interaction).lower()
    assert "approved" in said
    assert "appeals review" in said
    assert "posted again" in said
    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    assert "PENALTY_REVIEW_APPROVED | Success" not in league.log()


# ---------------------------------------------------------------------------
# The attendance pipeline inside it
# ---------------------------------------------------------------------------
#
# On the queue, attendance is reached through the hook the builder hands the change types, here
# review_league's double: the round's record is written in the approval's save (`record_on`), the
# sheet and each sanction are jobs of their own.


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_attendance_pipeline_runs_where_attendance_is_on(tmp_path):
    league = await review_league(tmp_path, attendance=True)

    await _approve_reports(league)

    assert league.attendance._calls("record_on") == [("record_on", ROUND_ID, DIVISION_ID)]
    assert [call[1] for call in league.attendance._calls("post_sheet")] == [DIVISION_ID]
    assert await one(league.db_path, "SELECT COUNT(*) FROM attendance_recorded") == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_attendance_pipeline_does_not_run_where_attendance_is_off(tmp_path):
    league = await review_league(tmp_path, attendance=False)

    await _approve_reports(league)

    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert league.attendance.calls == []
    assert await one(league.db_path, "SELECT COUNT(*) FROM attendance_recorded") == 0


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_sheet_and_the_sanctions_follow_the_cascade_to_its_last_round(tmp_path):
    """Issue #238. Each round's stored total is the driver's total as at that round, so
    approving round 3 while round 4 is already final leaves the division's current standing on
    round 4 — and it is the current standing the sheet must show and the thresholds must be read
    from."""
    league = await review_league(tmp_path, attendance=True)
    sheet = AsyncMock(wraps=league.attendance.post_sheet)
    owed = AsyncMock(wraps=league.attendance.sanction_candidates)
    league.attendance.post_sheet = sheet
    league.attendance.sanction_candidates = owed

    await _approve_reports(league)

    assert sheet.await_args.args[:2] == (LATER_ROUND_ID, DIVISION_ID)
    assert owed.await_args.args[:2] == (LATER_ROUND_ID, DIVISION_ID)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_staged_pardons_are_persisted(tmp_path):
    """The pardons staged on the review are handed to the round's attendance record, written in
    the approval's own save."""
    league = await review_league(tmp_path, attendance=True)

    await _approve_reports(league, pardons=[league_pardon()])

    assert await one(league.db_path, "SELECT pardons FROM attendance_recorded") == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_without_a_guild_nothing_is_posted_but_points_are_distributed(tmp_path):
    """The round's attendance is in the approval's save, which needs no server. What needs the
    server cannot be posted, and stops the queue rather than being skipped in silence (#239)."""
    league = await review_league(tmp_path, attendance=True)
    league.bot.get_guild = MagicMock(return_value=None)

    await _approve_reports(league)

    assert league.attendance._calls("record_on") == [("record_on", ROUND_ID, DIVISION_ID)]
    assert await stopped_at(league) is not None
    assert league.attendance._calls("post_sheet") == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_incomplete_sanctions_are_told_to_the_approving_manager(tmp_path):
    """#239. The approval used to report nothing of a sanction that did not apply. Max's
    autoreserve does not apply and stops the queue; once a league admin discards it, Alex's reply
    names Max's sanction as not applied, with the `/attendance sync` that finishes it."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {MAX_PROFILE: RuntimeError("no Reserve team")}

    interaction = await _approve_reports(league)
    assert await stopped_at(league) == "apply_sanction"
    await discard_job(league.bot)

    told = updated_reply(interaction)
    assert f"<@{MAX}>" in told or "Max" in told
    assert "/attendance sync" in told


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_sanction_run_that_raises_reaches_the_log_channel(tmp_path):
    """#239. A sanction run that failed outright never logged anything a league could read. A
    sanction that raises stops the queue, the stop notice reaching the log channel; once a league
    admin discards it, the approval's line names it with `/attendance sync`."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]
    league.attendance.apply_fails = {MAX_PROFILE: RuntimeError("database is locked")}

    await _approve_reports(league)
    assert await stopped_at(league) == "apply_sanction"
    await discard_job(league.bot)

    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    assert "/attendance sync" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_clean_sanction_run_tells_the_manager_nothing_more(tmp_path):
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]

    interaction = await _approve_reports(league)

    assert await stopped_at(league) is None
    told = updated_reply(interaction)
    assert "⚠️" not in told and "/attendance sync" not in told
    interaction.followup.send.assert_not_awaited()


# ---------------------------------------------------------------------------
# The appeals review, on the change queue (#439)
# ---------------------------------------------------------------------------
#
# Stage two is the change `results.appeals.approve`, asked as the appeals review's Approve asks
# it: round 3 of division 11 (Pro) awaits its appeal verdicts, its appeals prompt standing in the
# submission channel, division 11 the season's only division and round 3 its last still open.

APPEALS = "results.appeals.approve"
APPEALS_PROMPT = 8902
APPEALS_NOT_BUILT = "#439: the appeals approval is not yet a change on the queue"


async def _appeals_league(tmp_path: Any, *, round_status: str = "AWAITING_APPEAL_VERDICTS",
                          **options: Any) -> ReviewLeague:
    league = await review_league(
        tmp_path, round_status=round_status, other_division=False,
        appeals_prompt=APPEALS_PROMPT, **options,
    )
    league.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")
    return league


async def _ask_appeals(league: ReviewLeague, *, staged: Any = ()) -> Any:
    """Alex presses Approve on round 3's appeals review; the queue is not yet run. Gives Alex's
    interaction."""
    interaction = member_interaction(league.bot, user=_alex())
    await league.bot.change_queue.ask(
        APPEALS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "staged": [item.to_payload() for item in staged],
            "appeals_prompt_message_id": APPEALS_PROMPT,
        },
        interaction=interaction,
        what="✅ Approve on round 3's appeals review",
    )
    return interaction


async def _approve_appeals(league: ReviewLeague, **payload: Any) -> Any:
    """Alex approves round 3's appeals and the queue runs until it is clear or stopped."""
    interaction = await _ask_appeals(league, **payload)
    await run_queue(league.bot)
    return interaction


async def _profile_former(db_path: str, profile_id: int) -> int:
    return await one(
        db_path, "SELECT former_driver FROM driver_profiles WHERE id = ?", profile_id
    )


def _verdicts(league: ReviewLeague) -> list[int]:
    """The verdicts announced, the heading over them left out."""
    headings = verdict_headings(league)
    return [mid for mid in league.sent_to(VERDICTS_CHANNEL) if mid not in headings]


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_approving_appeals_finishes_the_round(tmp_path):
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "FINAL"


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_approving_appeals_marks_the_drivers_who_raced(tmp_path):
    """Where the former-driver flag is set, and the only place the first pass sets it (#216).

    The round becomes FINAL here, so this is the moment its results stop being provisional.
    Max's only entry is a did-not-start, so he did not race it.
    """
    league = await _appeals_league(tmp_path)
    await _set(league.db_path,
               "UPDATE race_session_results SET outcome = 'DNS' WHERE driver_user_id = ?", MAX)

    await _approve_appeals(league)

    assert await _profile_former(league.db_path, LEWIS_PROFILE) == 1
    assert await _profile_former(league.db_path, MAX_PROFILE) == 0


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_a_cancelled_round_marks_nobody(tmp_path):
    """A round that never became final has no final results to mark by."""
    league = await _appeals_league(tmp_path, round_status="CANCELLED")

    await _approve_appeals(league)

    assert await _profile_former(league.db_path, LEWIS_PROFILE) == 0


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_a_cancelled_round_is_not_raised_to_final(tmp_path):
    """By a view that outlived its cancellation."""
    league = await _appeals_league(tmp_path, round_status="CANCELLED")

    await _approve_appeals(league)

    assert await round_status(league.db_path) == "CANCELLED"


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_the_divisions_status_is_reconsidered(tmp_path):
    """#154: the last round's appeals are what finishes a division."""
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league)

    assert await one(
        league.db_path, "SELECT status FROM divisions WHERE id = ?", DIVISION_ID
    ) == "FINISHED"


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_the_submission_channel_is_closed(tmp_path):
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league)

    assert await one(league.db_path, "SELECT closed FROM round_submission_channels") == 1
    assert ("delete_channel", SUBMISSION_CHANNEL) in [
        (kind, cid) for kind, cid, _ in league.events
    ]


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_the_results_are_reposted_as_final(tmp_path):
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league)

    posted = league.channel(RESULTS_CHANNEL).messages
    assert any("Final Results" in str(posted[mid].content)
               for mid in league.sent_to(RESULTS_CHANNEL))
    assert OLD_RESULTS not in posted


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_upheld_corrections_are_applied_and_recorded(tmp_path):
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league, staged=[league_penalty(LEWIS)])

    assert (await race_rows(league.db_path))[LEWIS]["appeal_time_penalties_ms"] == 5000
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT status, penalty_type, time_seconds, description, submitted_by "
            "FROM appeal_records"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [
            ("UPHELD", "TIME", 5, "Corner cutting", str(STEWARD))
        ]


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_upheld_corrections_are_announced(tmp_path):
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league, staged=[league_penalty(LEWIS)])

    (verdict,) = _verdicts(league)
    assert f"<@{LEWIS}>" in str(league.channel(VERDICTS_CHANNEL).messages[verdict].content)


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_a_failed_appeal_announcement_does_not_un_approve_them(tmp_path):
    """The verdict Discord refuses stops the queue; the round is already final. Once a league
    admin discards it, the submission channel behind it is still deleted."""
    league = await _appeals_league(tmp_path)
    league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")

    await _approve_appeals(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == "announce_verdict"
    assert await round_status(league.db_path) == "FINAL"
    await discard_job(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "FINAL"
    assert ("delete_channel", SUBMISSION_CHANNEL) in [
        (kind, cid) for kind, cid, _ in league.events
    ]


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_an_appeals_review_with_no_corrections_applies_nothing(tmp_path):
    league = await _appeals_league(tmp_path)

    await _approve_appeals(league)

    assert await one(league.db_path, "SELECT COUNT(*) FROM appeal_records") == 0
    assert league.sent_to(VERDICTS_CHANNEL) == []
    assert "corrections: none" in league.log()


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_a_second_appeals_approval_while_the_first_runs_is_refused(tmp_path):
    """**D3.** The appeals approval reposts every graphic before the round's last jobs run, and a
    double click applied every correction twice. Round 3 (Pro) awaits its appeal verdicts with one
    correction staged; Alex approves, and presses again while the first is queued and while it is
    running. Each second press is refused, its refusal recorded, and the correction applied once."""
    from leaguebot.results.services.penalty_wizard import _APPEALS_BEING_APPROVED

    league = await _appeals_league(tmp_path)
    await _ask_appeals(league, staged=[league_penalty(LEWIS)])
    while_queued = await _ask_appeals(league, staged=[league_penalty(LEWIS)])
    await run_until_done(league, "names")
    while_running = await _ask_appeals(league, staged=[league_penalty(LEWIS)])
    await run_queue(league.bot)

    for interaction in (while_queued, while_running):
        assert acknowledgement(interaction) == _APPEALS_BEING_APPROVED
    assert league.log().count(f"refused for Alex (<@{STEWARD}>)") == 2
    assert len(await changes_of(league.db_path, APPEALS)) == 1
    assert await one(league.db_path, "SELECT COUNT(*) FROM appeal_records") == 1
    assert await round_status(league.db_path) == "FINAL"


async def _appeals_in_hand(league: ReviewLeague, how: str) -> None:
    """Round 3's appeals approval, one correction staged, left in hand: *queued* behind a job
    stopped before it, or *stopped* itself at the final results Discord refuses."""
    if how == "queued":
        await block_queue(league)
        await _ask_appeals(league, staged=[league_penalty(LEWIS)])
    else:
        league.channel(RESULTS_CHANNEL).send_fails = http_error(text="Discord is down")
        await _approve_appeals(league, staged=[league_penalty(LEWIS)])
        assert await stopped_at(league) == "post_session_results"


@pytest.mark.parametrize("how", ["queued", "stopped"])
@pytest.mark.parametrize(
    "kind, label",
    [
        ("review", "➕ Add Correction"),
        ("review", "No Changes / Confirm"),
        ("review", "✅ Approve"),
        ("review", "Remove #1"),
        ("clear", "Yes, clear and proceed with no corrections"),
    ],
)
@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_every_appeals_control_refuses_while_the_appeals_are_being_approved(
    tmp_path, kind, label, how
):
    """**D3.** Round 3's appeals (Pro) are being approved with one correction staged, queued
    behind a stopped job or stopped themselves, and the appeals prompt and its clear confirmation
    are still on screen. Alex, a league manager, presses one of their buttons meanwhile. It is
    refused as the approval under way (results spec, stage two: "Once the approval is under way
    or done"): nothing is staged, removed, cleared or asked a second time, and one line records
    the refusal, naming the button, the review, Alex and the reason."""
    from leaguebot.results.services.penalty_wizard import (
        _APPEALS_BEING_APPROVED,
        AppealsReviewView,
        PenaltyReviewState,
        _AppealsConfirmClearView,
        _appeals_review_moved_on,
    )

    league = await _appeals_league(tmp_path)
    state = PenaltyReviewState(
        round_id=ROUND_ID, division_id=DIVISION_ID, submission_channel_id=SUBMISSION_CHANNEL,
        session_types_present=[SessionType.FEATURE_RACE], db_path=league.db_path,
        bot=league.bot, staged_appeals=[league_penalty(LEWIS)],
        appeals_prompt_message_id=APPEALS_PROMPT, round_number=3, division_name="Pro",
    )
    assert await _appeals_review_moved_on(state) is None
    await _appeals_in_hand(league, how)
    assert await _appeals_review_moved_on(state) == _APPEALS_BEING_APPROVED
    view = (
        _AppealsConfirmClearView(state=state) if kind == "clear"
        else AppealsReviewView(state=state)
    )
    button = next(item for item in view.children if getattr(item, "label", None) == label)
    interaction = member_interaction(league.bot, user=_alex())

    with patch(
        "leaguebot.results.services.penalty_wizard._is_league_manager",
        new=AsyncMock(return_value=True),
    ), patch(
        "leaguebot.results.services.penalty_wizard._refresh_appeals_prompt", new=AsyncMock()
    ) as refresh_appeals:
        await button.callback(interaction)

    refresh_appeals.assert_not_awaited()
    assert len(state.staged_appeals) == 1
    assert len(await changes_of(league.db_path, APPEALS)) == 1
    (replied,) = [
        str(call.args[0])
        for call in interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    ]
    assert replied == _APPEALS_BEING_APPROVED
    assert league.log().count(
        f"⛔ the “{label}” button of the appeals review of round 3 (Pro) refused for "
        f"Alex (<@{STEWARD}>) — {_APPEALS_BEING_APPROVED.split(' ', 1)[1]}"
    ) == 1


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_a_failing_appeals_audit_does_not_stop_the_close(tmp_path):
    league = await _appeals_league(tmp_path)
    league.bot.log_channel.send = AsyncMock(side_effect=RuntimeError("no log"))

    await _approve_appeals(league)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "FINAL"
    assert ("delete_channel", SUBMISSION_CHANNEL) in [
        (kind, cid) for kind, cid, _ in league.events
    ]


# ---------------------------------------------------------------------------
# A repost that did not land reaches the league (#237)
#
# It used to be swallowed entirely: the approval logged `| Success` and the manager was told the
# same. On the queue a post Discord refuses stops it and is tried again; once a league admin
# discards it, the outcome and the approval's `| Incomplete` line name it, with the commands that
# finish the job.
# ---------------------------------------------------------------------------


async def _discard_until_clear(league: ReviewLeague) -> None:
    """Discard every job the queue stops at, until it runs clear."""
    for _ in range(20):
        if await stopped_at(league) is None:
            return
        await discard_job(league.bot)
    raise AssertionError("the queue never ran clear")


def _after(text: str, heading: str) -> str:
    """What *text* says from *heading* on."""
    assert heading in text, text
    return text.split(heading, 1)[1]


async def _results_post_discarded(league: ReviewLeague, approve: Any) -> Any:
    """Discord refuses round 3's results; the approval stops at the post, and a league admin
    discards it. Gives Alex's interaction."""
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    interaction = await approve(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == "post_session_results"
    await discard_job(league.bot)
    assert await stopped_at(league) is None
    return interaction


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_repost_that_could_not_post_tells_the_manager(tmp_path):
    league = await review_league(tmp_path)

    interaction = await _results_post_discarded(league, _approve_reports)

    said = updated_reply(interaction)
    assert "⚠️" in said
    assert "/results rounds sync" in said


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_repost_that_could_not_post_reaches_the_log_channel(tmp_path):
    league = await review_league(tmp_path)

    await _results_post_discarded(league, _approve_reports)

    assert "/results rounds sync" in _after(league.log(), "PENALTY_REVIEW_APPROVED | Incomplete")


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_approval_is_logged_as_incomplete_when_the_repost_failed(tmp_path):
    """The audit line says what the approval achieved, not what it attempted."""
    league = await review_league(tmp_path)

    await _results_post_discarded(league, _approve_reports)

    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    assert "PENALTY_REVIEW_APPROVED | Success" not in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_approval_is_logged_as_success_when_everything_posted(tmp_path):
    """The counterpart, so `| Incomplete` cannot be the answer to everything."""
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert "PENALTY_REVIEW_APPROVED | Success" in league.log()
    assert "| Incomplete" not in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_missing_guild_is_reported_rather_than_skipped(tmp_path):
    """`if guild:` used to skip both reposts without a word (#237). Without the server the posts
    stop the queue; once a league admin discards them, the approval's line is `| Incomplete`."""
    league = await review_league(tmp_path)
    league.bot.get_guild = MagicMock(return_value=None)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) is not None
    await _discard_until_clear(league)

    assert "PENALTY_REVIEW_APPROVED | Incomplete" in league.log()
    assert "PENALTY_REVIEW_APPROVED | Success" not in league.log()


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_the_appeals_approval_reports_an_unpostable_repost(tmp_path):
    """The same, on the approval that takes a round to FINAL."""
    league = await _appeals_league(tmp_path)

    interaction = await _results_post_discarded(league, _approve_appeals)

    assert "APPEALS_REVIEW_APPROVED | Incomplete" in league.log()
    said = updated_reply(interaction)
    assert "/results rounds sync" in said
    assert "/results standings sync" in said


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_fault_both_reposts_found_is_reported_once(tmp_path):
    """The manager repairs what is named, so reading the same thing twice is a false count of the
    problems in front of them (#237 review). Round 3's results post is discarded while its
    standings, and round 4's, go out: the reply and the approval's line name the results once."""
    league = await review_league(tmp_path)

    interaction = await _results_post_discarded(league, _approve_reports)

    assert updated_reply(interaction).count("/results rounds sync") == 1
    assert _after(
        league.log(), "PENALTY_REVIEW_APPROVED | Incomplete"
    ).count("/results rounds sync") == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_log_channel_that_refuses_still_tells_the_manager(tmp_path):
    """The log channel is the league's record and the reply is the manager's; a failure to write
    one must not swallow the other, or the approval goes back to being silent in exactly the way
    #237 is about."""
    league = await review_league(tmp_path)
    league.bot.log_channel.send = AsyncMock(side_effect=RuntimeError("no log channel"))

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert await stopped_at(league) is None
    assert "Round 3's reports are approved" in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_reply_that_fails_does_not_stop_the_approval(tmp_path):
    """The manager may have dismissed the interaction; the approval still stands."""
    league = await review_league(tmp_path)
    interaction = await _ask_reports(league, staged=[league_penalty(LEWIS)])
    interaction.edit_original_response = AsyncMock(side_effect=RuntimeError("unknown webhook"))
    interaction.followup.send = AsyncMock(side_effect=RuntimeError("unknown webhook"))

    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert "PENALTY_REVIEW_APPROVED | Success" in league.log()
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


# ---------------------------------------------------------------------------
# A verdict that was not announced, and attendance that was not recorded (#237)
# ---------------------------------------------------------------------------


async def _verdict_discarded(league: ReviewLeague, approve: Any) -> Any:
    """Discord refuses Lewis's verdict; the approval stops at it, and a league admin discards it.
    Gives Alex's interaction."""
    league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Permissions")
    interaction = await approve(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == "announce_verdict"
    await discard_job(league.bot)
    assert await stopped_at(league) is None
    return interaction


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_unannounced_verdict_reaches_the_manager_and_the_log(tmp_path):
    league = await review_league(tmp_path)

    interaction = await _verdict_discarded(league, _approve_reports)

    assert f"<@{LEWIS}>" in _after(league.log(), "PENALTY_REVIEW_APPROVED | Incomplete")
    said = updated_reply(interaction)
    assert "⚠️" in said
    assert "Lewis" in said or f"<@{LEWIS}>" in said


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_verdict_report_says_it_cannot_be_announced_again(tmp_path):
    """Telling a manager to re-run something would leave them believing it finished. The report
    directs the manager to post the verdict themselves (results spec, verdicts)."""
    league = await review_league(tmp_path)

    interaction = await _verdict_discarded(league, _approve_reports)

    said = updated_reply(interaction).lower()
    assert "post it" in said or "post the verdict" in said
    assert "/results rounds sync" not in said


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_approval_that_announced_everything_reports_no_verdict_fault(tmp_path):
    """The counterpart: every verdict announced leaves nothing to report."""
    league = await review_league(tmp_path)

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert await stopped_at(league) is None
    assert "| Incomplete" not in league.log()
    assert "⚠️" not in updated_reply(interaction)


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_an_appeal_verdict_that_was_not_announced_is_reported(tmp_path):
    league = await _appeals_league(tmp_path)

    await _verdict_discarded(league, _approve_appeals)

    assert f"<@{LEWIS}>" in _after(league.log(), "APPEALS_REVIEW_APPROVED | Incomplete")


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_attendance_that_was_not_recorded_is_reported(tmp_path):
    """The round's attendance is written in the approval's own save (\"Whole approval fails\"), so
    a fault in it stops the approval whole, its reason in the stop notice, and nothing is changed.
    Once a league admin discards it, Alex is told nothing was changed and the review is posted
    again."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.record_fails = RuntimeError("disk is full")

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == "apply"
    assert await penalty_records(league.db_path) == []
    assert await one(league.db_path, "SELECT COUNT(*) FROM attendance_recorded") == 0
    await discard_job(league.bot)

    assert await penalty_records(league.db_path) == []
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"
    assert "nothing was changed" in updated_reply(interaction).lower()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_posting_step_that_fails_is_not_reported_as_a_record_failure(tmp_path):
    """The sheet is a picture of the record, not the record: its failure stops the queue at the
    sheet, the round's attendance and penalties already saved."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.sheet_fails = [StepFailedOnDiscord("no attendance channel")]

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert await stopped_at(league) == "attendance_sheet"
    assert league.attendance._calls("record_on") == [("record_on", ROUND_ID, DIVISION_ID)]
    assert len(await penalty_records(league.db_path)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_sanctions_are_not_run_on_a_record_known_to_be_wrong(tmp_path):
    """An autosack takes a driver's seat, so it is never applied on unsound totals (#237). A
    record that cannot be written stops the approval at its save, and no driver is checked
    against a threshold."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]
    owed = AsyncMock(wraps=league.attendance.sanction_candidates)
    league.attendance.sanction_candidates = owed
    league.attendance.record_fails = RuntimeError("disk is full")

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert await stopped_at(league) == "apply"
    owed.assert_not_awaited()
    assert league.attendance._calls("apply_sanction") == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_sanctions_still_run_when_the_record_is_sound(tmp_path):
    """The counterpart: a sheet that could not be posted delays the sanctions behind it, never
    skips them. Once a league admin discards the sheet, Max's autoreserve is applied."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]
    league.attendance.sheet_fails = [StepFailedOnDiscord("no attendance channel")]

    await _approve_reports(league)
    assert await stopped_at(league) == "attendance_sheet"
    await discard_job(league.bot)

    assert league.attendance._calls("apply_sanction") == [("apply_sanction", MAX_PROFILE)]


# ---------------------------------------------------------------------------
# Nothing is approved while another round of the division is being amended (#345)
# ---------------------------------------------------------------------------
#
# Decided 2026-09-21. An amendment's first stage writes the corrected classification and
# recalculates the championship, publishing nothing; approving another round of the division
# posts standings, and would publish those corrections before they are approved — and leave them
# published if the amendment were then cancelled or lapsed. Both approvals are refused while one
# is open, and the review is left as it was for the manager to press again.

AMENDED_ROUND = 20
AMEND_CHANNEL = 8200


async def _amend_round_two(db_path, *, division_id=DIVISION_ID, ended=False):
    """An amendment of round 2, open in *division_id* — or *ended*, its channel left behind."""
    async with get_connection(db_path) as db:
        if division_id != DIVISION_ID:
            await db.execute(
                "INSERT OR IGNORE INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, 'Am', 2, 556)",
                (division_id, SEASON_ID),
            )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format, status) "
            "VALUES (?, ?, 2, '2026-01-25T18:00:00+00:00', 'NORMAL', 'FINAL')",
            (AMENDED_ROUND, division_id),
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "closed_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', '2026-02-01T00:00:00+00:00', ?)",
            (AMENDED_ROUND, AMEND_CHANNEL, "2026-02-01T00:20:00+00:00" if ended else None),
        )
        await db.commit()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_reports_are_not_approved_while_another_round_is_amended(tmp_path):
    """Round 2 of Pro is being amended. Alex presses Approve on round 3's review with Lewis's
    penalty staged: refused as it is asked, saying where and when to approve again, the refusal
    recorded, and nothing applied or posted."""
    league = await review_league(tmp_path)
    await _amend_round_two(league.db_path)

    interaction = await _approve_reports(league, staged=[league_penalty(LEWIS)])

    refusal = acknowledgement(interaction)
    assert f"Round 2 of this division is being amended in <#{AMEND_CHANNEL}>" in refusal
    assert "Approve the reports again then." in refusal
    _refused_on_the_queue(league, interaction, "being amended")
    await _nothing_approved(league)
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"


@pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)
async def test_the_appeals_are_not_approved_while_another_round_is_amended(tmp_path):
    league = await _appeals_league(tmp_path)
    await _amend_round_two(league.db_path)

    interaction = await _approve_appeals(league)

    _refused_on_the_queue(league, interaction, "Approve the appeals again then.")
    assert await changes_of(league.db_path, APPEALS) == []
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert await one(league.db_path, "SELECT closed FROM round_submission_channels") == 0


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_reports_are_approved_once_the_amendment_has_ended(tmp_path):
    """Refused, the review stands; pressed again after the amendment, it goes through."""
    league = await review_league(tmp_path)
    await _amend_round_two(league.db_path)
    await _approve_reports(league, staged=[league_penalty(LEWIS)])
    await _set(league.db_path, "DELETE FROM round_amend_channels")

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert len(await penalty_records(league.db_path)) == 1
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"


@pytest.mark.parametrize("where", ["another division", "ended"])
@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_amendment_elsewhere_or_ended_holds_nothing(tmp_path, where):
    """Only an amendment open in the round's own division holds it.

    One that has ended but could not delete its channel keeps its row, for restart recovery to
    find the channel by; it is not open, and holding approvals on it would hold them for ever.
    """
    league = await review_league(tmp_path)
    if where == "ended":
        await _amend_round_two(league.db_path, ended=True)
    else:
        await _amend_round_two(league.db_path, division_id=12)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert len(await penalty_records(league.db_path)) == 1


async def test_an_amendment_is_not_held_by_itself(tmp_path):
    """Its own report and appeal stages are the ones that end it."""
    from leaguebot.results.services.result_submission_service import held_by_amendment

    db_path = await _make_db(tmp_path, name="not_held_by_itself")
    await _amend_round_two(db_path)

    assert await held_by_amendment(db_path, AMENDED_ROUND, DIVISION_ID, then="") is None
    assert await held_by_amendment(db_path, ROUND_ID, DIVISION_ID, then="") is not None


async def test_the_wait_a_refusal_quotes_follows_the_deadline(tmp_path, monkeypatch):
    """Written out, a tuned deadline left every refusal quoting the old one; and "at most" was
    never true, the sweep running every few minutes."""
    import leaguebot.results.services.result_submission_service as rss

    db_path = await _make_db(tmp_path, name="held_wait")
    await _amend_round_two(db_path)
    monkeypatch.setattr(rss, "AMENDMENT_STAGE_TIMEOUT_SECONDS", 600)

    refusal = await rss.held_by_amendment(db_path, ROUND_ID, DIVISION_ID, then="")

    assert "once 10 minutes have passed since its corrections were entered" in refusal
    assert "at most" not in refusal


async def test_the_season_names_an_amendment_open_in_any_of_its_divisions(tmp_path):
    """What completing the season and approving a change to its points ask (#345)."""
    from leaguebot.results.services.result_submission_service import open_amendment_in_season

    db_path = await _make_db(tmp_path, name="season_held")
    await _amend_round_two(db_path, division_id=12)

    row = await open_amendment_in_season(db_path, SEASON_ID)

    assert (row["division_name"], row["round_number"], row["channel_id"]) == (
        "Am", 2, AMEND_CHANNEL,
    )


async def test_an_ended_amendment_or_another_season_leaves_the_season_free(tmp_path):
    from leaguebot.results.services.result_submission_service import open_amendment_in_season

    db_path = await _make_db(tmp_path, name="season_not_held")
    await _amend_round_two(db_path, ended=True)

    assert await open_amendment_in_season(db_path, SEASON_ID) is None
    assert await open_amendment_in_season(db_path, SEASON_ID + 1) is None


# ---------------------------------------------------------------------------
# An amendment's stages, on the change queue (#439)
# ---------------------------------------------------------------------------
#
# The stages of `/results rounds amend` after its classification stage are the changes
# `results.amendment.reports.approve` and `results.amendment.appeals.approve`, asked here as their
# Approve controls ask them, by Alex, on `test_amendment_stage_changes.py`'s league: round 3 of Pro,
# FINAL, under an amendment of its Feature Race, Max holding a report from the round's review.
#
# These let the stage's save write for real and count rows, because that is the only thing that
# catches the defect they were written for (#345): applying a penalty only ever inserts, and adds to
# the stored penalty columns, so replaying a round's reports over records still in place duplicated
# every one of them, and doubled the sanction again on a second amendment.

AMEND_REPORTS = "results.amendment.reports.approve"
AMEND_APPEALS = "results.amendment.appeals.approve"
AMEND_NOT_BUILT = "#439: an amendment's stages are not yet changes on the queue"


async def _ask_amend_reports(league: ReviewLeague, *, staged: Any = None,
                             sessions: tuple[str, ...] = ("FEATURE_RACE",)) -> Any:
    """Alex presses Approve on the amendment's report stage, Lewis's 5-second report kept (or
    *staged*); the queue is not yet run. Gives Alex's interaction."""
    staged = [league_penalty(LEWIS)] if staged is None else staged
    interaction = member_interaction(league.bot, user=_alex())
    await league.bot.change_queue.ask(
        AMEND_REPORTS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "session_types": list(sessions),
            "staged": [item.to_payload() for item in staged],
            "pardons": [],
            "prompt_message_id": AMEND_PROMPT,
            "approval_message_id": AMEND_APPROVAL,
        },
        interaction=interaction,
        what="✅ Approve on the report stage of round 3's amendment",
    )
    return interaction


async def _amend_reports(league: ReviewLeague, **payload: Any) -> Any:
    """Alex approves the amendment's report stage and the queue runs until clear or stopped."""
    interaction = await _ask_amend_reports(league, **payload)
    await run_queue(league.bot)
    return interaction


async def _ask_amend_appeals(league: ReviewLeague, *, staged: Any = (), pardons: Any = ()) -> Any:
    """Alex presses Approve on the amendment's appeals stage; the queue is not yet run."""
    interaction = member_interaction(league.bot, user=_alex())
    await league.bot.change_queue.ask(
        AMEND_APPEALS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "session_types": ["FEATURE_RACE"],
            "staged": [item.to_payload() for item in staged],
            "pardons": [item.to_payload() for item in pardons],
            "appeals_prompt_message_id": AMEND_APPEALS_PROMPT,
        },
        interaction=interaction,
        what="✅ Approve on the appeals stage of round 3's amendment",
    )
    return interaction


async def _amend_appeals(league: ReviewLeague, **payload: Any) -> Any:
    """Alex approves the amendment's appeals stage and the queue runs until clear or stopped."""
    interaction = await _ask_amend_appeals(league, **payload)
    await run_queue(league.bot)
    return interaction


def _correction(driver: int = MAX, seconds: int = 10) -> StagedPenalty:
    """An upheld appeal's correction: *seconds* added to *driver*'s Feature Race time."""
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=seconds,
        description="Track limits",
        justification="Appeal upheld, lap 7",
    )


async def _lewis_row(league: ReviewLeague) -> int:
    return await one(
        league.db_path,
        "SELECT id FROM race_session_results WHERE driver_user_id = ?", LEWIS,
    )


async def _seed_record(league: ReviewLeague, result_id: int, *, column: str = "race_result_id",
                       kind: str = "TIME", description: str = "Corner cutting") -> None:
    await _set(
        league.db_path,
        f"INSERT INTO penalty_records ({column}, penalty_type, time_seconds, description, "
        "justification, applied_by, applied_at) VALUES (?, ?, 5, ?, 'Old', '7', "
        "'2026-02-02T00:00:00+00:00')",
        result_id, kind, description,
    )


async def _seed_session(league: ReviewLeague, session_type: str, *, ms: int = 0) -> int:
    """Another session of round 3 with Lewis in it, carrying *ms* of post-race penalties. Gives
    Lewis's result row."""
    table = ("qualifying_session_results" if session_type.endswith("QUALIFYING")
             else "race_session_results")
    async with get_connection(league.db_path) as db:
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (ROUND_ID, DIVISION_ID, session_type),
        )
        extra = ", postrace_time_penalties_ms" if table == "race_session_results" else ""
        values = ", ?" if extra else ""
        cursor = await db.execute(
            f"INSERT INTO {table} (session_result_id, driver_user_id, team_instance_id, "
            f"finishing_position{extra}) VALUES (?, ?, 3001, 1{values})",
            (session.lastrowid, LEWIS, ms) if extra else (session.lastrowid, LEWIS),
        )
        await db.commit()
    return cursor.lastrowid


async def _descriptions(league: ReviewLeague) -> list[str]:
    return [record["description"] for record in await penalty_records(league.db_path)]


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_an_amendments_report_stage_takes_its_controls_down(tmp_path):
    """**The prompt as well as the approval**, as a first pass's. An amendment's pardons close with
    its reports (decided 2026-09-23), so nothing on the prompt is left to do; that the stage is
    approved is read from the amendment's row."""
    league = await amend_league(tmp_path)

    await _amend_reports(league)

    messages = league.channel(AMENDMENT_CHANNEL).messages
    assert AMEND_PROMPT not in messages and AMEND_APPROVAL not in messages
    assert (await amend_row(league))["reports_approved_at"] is not None


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_an_amendment_does_not_duplicate_the_rounds_penalty_records(tmp_path):
    """**The defect the independent review found.** Lewis's 5-second report from the round's review
    still stands when the amendment's report stage approves it again: one record, five seconds."""
    league = await amend_league(tmp_path)
    await _seed_record(league, await _lewis_row(league))

    await _amend_reports(league)

    assert await stopped_at(league) is None
    lewis = [r for r in await penalty_records(league.db_path)
             if r["race_result_id"] == await _lewis_row(league)]
    assert len(lewis) == 1
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_first_pass_still_records_and_applies_once(tmp_path):
    """The ordinary path is untouched: Alex approves round 3's review with Lewis's 5 seconds."""
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[league_penalty(LEWIS)])

    assert len(await penalty_records(league.db_path)) == 1
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_report_removed_in_stage_two_is_removed_from_the_record(tmp_path):
    """Delete-and-rewrite is what makes the stage editable at all: Max's report, removed from the
    stage, is gone from the record once the stage is approved with nothing staged."""
    league = await amend_league(tmp_path)

    await _amend_reports(league, staged=[])

    assert await stopped_at(league) is None
    assert await penalty_records(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_an_amendment_does_not_post_the_attendance_sheet_itself(tmp_path):
    """The sheet and the sanctions belong to the amendment's last stage (#345): the report stage,
    attendance on and Max over a threshold, posts no sheet and applies no sanction."""
    league = await amend_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]

    await _amend_reports(league)

    assert await stopped_at(league) is None
    assert league.attendance._calls("post_sheet") == []
    assert league.attendance._calls("apply_sanction") == []


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_the_report_stage_leaves_the_pardons_to_the_last_stage(tmp_path):
    league = await amend_league(tmp_path, attendance=True)

    await _amend_reports(league)

    assert league.attendance._calls("rewrite_pardons_on") == []


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_an_amendment_announces_each_appeal_verdict_once(tmp_path):
    """The rebuild announces the round's verdicts, the correction just written among them; it is
    not announced a second time beside them (#345)."""
    league = await amend_league(tmp_path, reports_approved=True)

    await _amend_appeals(league, staged=[_correction()])

    assert await stopped_at(league) is None
    channel = league.channel(VERDICTS_CHANNEL)
    corrections = [mid for mid in league.sent_to(VERDICTS_CHANNEL)
                   if "Track limits" in (channel.messages[mid].content or "")]
    assert len(corrections) == 1


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_an_amendment_does_not_double_an_unamended_sessions_penalties(tmp_path):
    """**The worst defect any review of this change found.** Lewis's 5 seconds in the Sprint Race,
    a session the amendment did not re-enter, stay 5 seconds when the Feature Race is amended."""
    league = await amend_league(tmp_path)
    await _seed_session(league, "SPRINT_RACE", ms=5000)

    await _amend_reports(league)

    assert await one(
        league.db_path,
        "SELECT r.postrace_time_penalties_ms FROM race_session_results r JOIN session_results s "
        "ON s.id = r.session_result_id WHERE s.session_type = 'SPRINT_RACE'",
    ) == 5000


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_the_other_sessions_verdict_records_survive_an_amendment(tmp_path):
    """Clearing the whole round would drop the Sprint Race's record, and nothing would write it
    back: the stage re-approves the amended session's reports only."""
    league = await amend_league(tmp_path)
    await _seed_record(league, await _seed_session(league, "SPRINT_RACE"),
                       description="Sprint contact")

    await _amend_reports(league)

    assert (await _descriptions(league)).count("Sprint contact") == 1


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_the_report_stage_rewrites_every_amended_session_and_no_other(tmp_path):
    """The amendment re-entered the Feature Qualifying and the Feature Race; its review keeps
    Lewis's race report and drops his qualifying disqualification. The Sprint Race, not amended,
    keeps its report."""
    league = await amend_league(tmp_path)
    await _set(league.db_path, "UPDATE round_amend_channels SET session_types = "
               "'[\"FEATURE_QUALIFYING\", \"FEATURE_RACE\"]'")
    await _seed_record(league, await _seed_session(league, "FEATURE_QUALIFYING"),
                       column="qual_result_id", kind="DSQ", description="Quali")
    await _seed_record(league, await _seed_session(league, "SPRINT_RACE"), description="Sprint")

    await _amend_reports(league, sessions=("FEATURE_QUALIFYING", "FEATURE_RACE"))

    assert sorted(await _descriptions(league)) == ["Corner cutting", "Sprint"]


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_stage_of_an_amendment_no_longer_open_changes_nothing(tmp_path):
    """The amendment lapsed or was cancelled before Alex's press ran: nothing is written."""
    league = await amend_league(tmp_path)
    await _set(league.db_path, "DELETE FROM round_amend_channels")

    interaction = await _amend_reports(league)

    assert "no longer open" in acknowledgement(interaction)
    assert await _descriptions(league) == ["Corner cutting"]
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 0


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_the_appeal_stage_of_an_amendment_no_longer_open_changes_nothing(tmp_path):
    league = await amend_league(tmp_path, reports_approved=True)
    await _set(league.db_path, "DELETE FROM round_amend_channels")

    interaction = await _amend_appeals(league, staged=[_correction()])

    assert "no longer open" in acknowledgement(interaction)
    assert await one(league.db_path, "SELECT COUNT(*) FROM appeal_records") == 0
    assert league.sent_to(RESULTS_CHANNEL) == []
    assert "RESULT_AMENDED" not in league.log()


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_kept_report_keeps_its_author_and_its_time(tmp_path):
    """**A verdict follows its driver with its justification, its author and its time.** Lewis's
    report, decided by 4242 on 1 February, is written back so, not stamped with Alex and now."""
    league = await amend_league(tmp_path)
    kept = league_penalty(LEWIS)
    kept.decided_by = "4242"
    kept.decided_at = "2026-02-01T20:00:00"

    await _amend_reports(league, staged=[kept])

    [record] = await penalty_records(league.db_path)
    assert (record["applied_by"], record["applied_at"]) == ("4242", "2026-02-01T20:00:00")


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_fresh_report_is_stamped_in_utc(tmp_path):
    """A report with no time of its own is stamped with "now" from the clock the change types are
    handed, timezone-aware (#160)."""
    league = await amend_league(tmp_path)

    await _amend_reports(league)

    [record] = await penalty_records(league.db_path)
    stamped = datetime.fromisoformat(record["applied_at"])
    assert stamped.utcoffset() == timedelta(0)
    assert stamped == NOW


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_report_added_during_the_amendment_names_who_approved_it(tmp_path):
    league = await amend_league(tmp_path)

    await _amend_reports(league)

    [record] = await penalty_records(league.db_path)
    assert record["applied_by"] == str(STEWARD)


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_kept_appeal_keeps_its_author_and_its_time(tmp_path):
    """Both rows an upheld appeal writes, its appeal record and the penalty row beside it."""
    league = await amend_league(tmp_path, reports_approved=True)
    kept = _correction()
    kept.decided_by = "4343"
    kept.decided_at = "2026-02-03T20:00:00"

    await _amend_appeals(league, staged=[kept])

    assert await one(league.db_path, "SELECT submitted_by || ' ' || submitted_at "
                     "FROM appeal_records") == "4343 2026-02-03T20:00:00"
    record = [r for r in await penalty_records(league.db_path) if r["description"] == "Track limits"]
    assert [(r["applied_by"], r["applied_at"]) for r in record] == [
        ("4343", "2026-02-03T20:00:00")
    ]


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_fresh_appeal_is_stamped_in_utc(tmp_path):
    league = await amend_league(tmp_path, reports_approved=True)

    await _amend_appeals(league, staged=[_correction()])

    stamped = datetime.fromisoformat(
        await one(league.db_path, "SELECT submitted_at FROM appeal_records")
    )
    assert stamped.utcoffset() == timedelta(0)
    assert stamped == NOW


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_committed_amendment_marks_the_drivers_who_raced(tmp_path):
    """The appeals stage settles the former-driver flag, the round already FINAL (#216): Lewis
    raced it, and Max, a did-not-start, did not."""
    league = await amend_league(tmp_path, reports_approved=True)
    await _set(league.db_path,
               "UPDATE race_session_results SET outcome = 'DNS' WHERE driver_user_id = ?", MAX)

    await _amend_appeals(league)

    assert await _profile_former(league.db_path, LEWIS_PROFILE) == 1
    assert await _profile_former(league.db_path, MAX_PROFILE) == 0


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_committed_amendment_clears_a_driver_it_struck_out(tmp_path):
    """The half of #216 nothing could do before: driver 103 was a former driver by this round, and
    the amendment left them out of it. With no other final round marking them, the flag comes
    down."""
    league = await amend_league(tmp_path, reports_approved=True)
    await _set(league.db_path, "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
               "former_driver) VALUES (33, '103', 'ASSIGNED', 1)")
    await _set(league.db_path, "UPDATE round_amend_channels SET pre_amendment_state = ?",
               '{"sessions": [], "profiles_before": [31, 32, 33]}')

    await _amend_appeals(league)

    assert await _profile_former(league.db_path, LEWIS_PROFILE) == 1
    assert await _profile_former(league.db_path, 33) == 0, "a driver struck out kept their flag"


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_committed_amendment_is_logged_as_result_amended(tmp_path):
    """The README tells a league to look for `RESULT_AMENDED`; the amendment is not logged as an
    ordinary appeals approval."""
    league = await amend_league(tmp_path, reports_approved=True)

    await _amend_appeals(league)

    line = _line_of(league, "RESULT_AMENDED")
    assert "RESULT_AMENDED | Success" in line
    assert "sessions: FEATURE_RACE" in line
    assert "APPEALS_REVIEW_APPROVED" not in league.log()


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_the_amendment_rewrites_the_rounds_pardons_at_its_last_stage(tmp_path):
    """The round's pardons are rewritten through attendance's hook in the appeals stage's save,
    once, after the snapshot that a revert would restore is released."""
    league = await amend_league(tmp_path, reports_approved=True, attendance=True)

    await _amend_appeals(league, pardons=[league_pardon()])

    assert await stopped_at(league) is None
    assert league.attendance._calls("rewrite_pardons_on") == [("rewrite_pardons_on", ROUND_ID)]


@pytest.mark.parametrize(
    "stage, kind",
    [("reports", AMEND_REPORTS), ("appeals", AMEND_APPEALS)],
)
@pytest.mark.xfail(strict=True, reason="#439: the amendment's Approve controls do not yet ask the "
                   "change queue")
async def test_each_approve_control_of_an_amendment_asks_its_stage_of_the_queue(
    tmp_path, stage, kind
):
    """Alex, a league manager, presses Approve on the report stage of round 3 (Pro)'s amendment,
    Lewis's 5-second report staged, or on its appeals stage, a 10-second correction for Max staged.
    The press asks the queue for that stage's change, with the round, the division, the amended
    sessions and what is staged in its payload, and Alex is answered at once that it is under way,
    with its job number."""
    from leaguebot.results.services.penalty_wizard import (
        AppealsReviewView,
        ApprovalView,
        PenaltyReviewState,
    )

    league = await amend_league(tmp_path, reports_approved=stage == "appeals")
    state = PenaltyReviewState(
        round_id=ROUND_ID, division_id=DIVISION_ID, submission_channel_id=AMENDMENT_CHANNEL,
        session_types_present=[SessionType.FEATURE_RACE], db_path=league.db_path,
        bot=league.bot, round_number=3, division_name="Pro", is_amendment=True,
    )
    if stage == "reports":
        state.staged = [league_penalty(LEWIS)]
        state.prompt_message_id = AMEND_PROMPT
        state.approval_message_id = AMEND_APPROVAL
        view: Any = ApprovalView(state=state)
        staged = [league_penalty(LEWIS).to_payload()]
    else:
        state.staged_appeals = [_correction()]
        state.appeals_prompt_message_id = AMEND_APPEALS_PROMPT
        view = AppealsReviewView(state=state)
        staged = [_correction().to_payload()]
    interaction = member_interaction(league.bot, user=_alex())

    with patch(
        "leaguebot.results.services.penalty_wizard._is_league_manager",
        new=AsyncMock(return_value=True),
    ):
        await view.approve_btn.callback(interaction)

    [change] = await changes_of(league.db_path, kind)
    payload = json.loads(change["payload"])
    assert payload["round_id"] == ROUND_ID
    assert payload["division_id"] == DIVISION_ID
    assert payload["session_types"] == ["FEATURE_RACE"]
    assert payload["staged"] == staged
    answered = acknowledgement(interaction)
    assert answered.startswith("⏳")
    assert "job #" in answered


# ---------------------------------------------------------------------------
# A failed stage names the kind of fault, and every stage refusal is recorded (#442)
# ---------------------------------------------------------------------------
#
# Nothing is undone because a stage's job failed ("Retry like any job"): it stops the queue, and the
# stop notice names the kind of fault and never its message.


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_failed_amendment_report_stage_names_the_kind_of_fault(tmp_path):
    """The report stage's save meets a database fault: the queue stops at it, the notice naming
    the fault's type and not its message; discarded, the reply leads Alex back to Approve and
    names no message either."""
    import sqlite3

    league = await amend_league(tmp_path)
    with patch(
        "leaguebot.results.services.result_submission_service._apply_points_in_tx",
        new=AsyncMock(side_effect=sqlite3.OperationalError("database is locked")),
    ):
        interaction = await _amend_reports(league)
    assert await stopped_at(league) == "apply"
    notice = league.log()
    assert "OperationalError" in notice
    assert "database is locked" not in notice

    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "Approve" in reply
    assert "database is locked" not in reply
    assert await _descriptions(league) == ["Corner cutting"]


@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_a_failed_amendment_appeals_stage_names_the_kind_of_fault(tmp_path):
    """The appeals stage's points cannot be recalculated: the queue stops at its save, the notice
    naming the fault's type and not its message, and the amendment is neither undone nor
    committed."""
    league = await amend_league(tmp_path, reports_approved=True)
    with points_fail():
        await _amend_appeals(league, staged=[_correction()])

    assert await stopped_at(league) == "apply"
    notice = league.log()
    assert "RuntimeError" in notice
    assert "the points could not be calculated" not in notice
    row = await amend_row(league)
    assert row is not None and row["pre_amendment_state"] is not None
    assert "RESULT_AMENDED" not in notice


@pytest.mark.parametrize(
    "case",
    [
        *(pytest.param(case, marks=pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT))
          for case in ("reports-already-approved", "report-stage-not-open",
                       "appeals-stage-not-open")),
        pytest.param("a-first-pass-refusal",
                     marks=pytest.mark.xfail(strict=True, reason=NOT_BUILT)),
    ],
)
async def test_every_results_rounds_amend_refusal_reaches_the_log_channel(tmp_path, case):
    """A stage of an amendment refuses a press it cannot act on: its report stage already
    approved, or the amendment no longer open at either stage. Each refusal writes one ⛔ line
    naming the stage refused and Alex, who pressed. The first pass's own refusal, a press while its
    results are being resubmitted, is no part of the amendment and does not name it."""
    if case == "a-first-pass-refusal":
        league = await review_league(tmp_path)
        await _set(league.db_path, "UPDATE round_submission_channels SET resubmitting = 1")
        await _ask_reports(league, staged=[league_penalty(LEWIS)])
    else:
        league = await amend_league(tmp_path,
                                    reports_approved=case != "report-stage-not-open")
        if case != "reports-already-approved":
            await _set(league.db_path, "DELETE FROM round_amend_channels")
        if case == "appeals-stage-not-open":
            await _ask_amend_appeals(league)
        else:
            await _ask_amend_reports(league)
    await run_queue(league.bot)

    [line] = [str(line) for line in league.bot.log_channel.sent if str(line).startswith("⛔")]
    assert f"refused for Alex (<@{STEWARD}>)" in line
    if case == "a-first-pass-refusal":
        assert "penalty review" in line
        assert "amendment" not in line and "/results rounds amend" not in line
    else:
        assert "stage of round 3's amendment" in line


# ---------------------------------------------------------------------------
# Who approved (#482): each line an approval writes names the member who pressed, by
# display name and mention, and an approval writes one line of its own, not two.
# ---------------------------------------------------------------------------

_ALEX = f"Alex (<@{STEWARD}>)"


def _line_of(league: ReviewLeague, heading: str) -> str:
    """The one line the league's log channel was given under *heading*."""
    lines = [str(line) for line in league.bot.log_channel.sent if heading in str(line)]
    assert len(lines) == 1, lines
    return lines[0]


@pytest.mark.parametrize(
    "stage, token",
    [
        pytest.param("reports", "PENALTY_REVIEW_APPROVED", id="reports",
                     marks=pytest.mark.xfail(strict=True, reason=NOT_BUILT)),
        pytest.param("appeals", "APPEALS_REVIEW_APPROVED", id="appeals",
                     marks=pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT)),
    ],
)
async def test_the_approval_names_the_member_who_approved(tmp_path, stage, token):
    """Alex approves the reports (or the appeals) of round 3 (Pro) with one penalty staged:
    the approval's line reads "Alex (<@77>) | <TOKEN> | Success"."""
    if stage == "reports":
        league = await review_league(tmp_path)
        await _approve_reports(league, staged=[league_penalty(LEWIS)])
    else:
        league = await _appeals_league(tmp_path)
        await _approve_appeals(league, staged=[league_penalty(LEWIS)])

    assert _line_of(league, token).startswith(f"{_ALEX} | {token} | Success")


@pytest.mark.parametrize("discarded", ["post_session_results", "announce_verdict", "apply_sanction"])
@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_what_the_approval_could_not_do_names_the_member_who_approved(tmp_path, discarded):
    """Alex's approval of round 3 (Pro)'s reports, one penalty staged and attendance on with Max
    owed an autoreserve, stops at the results post, Lewis's verdict or Max's sanction, and a
    league admin discards it. The approval's line names Alex, who pressed, not the admin:
    "Alex (<@77>) | PENALTY_REVIEW_APPROVED | Incomplete"."""
    league = await review_league(tmp_path, attendance=True)
    league.attendance.candidates = [league_candidate(MAX_PROFILE, MAX)]
    if discarded == "post_session_results":
        league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    elif discarded == "announce_verdict":
        league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    else:
        league.attendance.apply_fails = {MAX_PROFILE: RuntimeError("no Reserve team")}

    await _approve_reports(league, staged=[league_penalty(LEWIS)])
    assert await stopped_at(league) == discarded
    await discard_job(league.bot)
    assert await stopped_at(league) is None

    assert _line_of(league, "PENALTY_REVIEW_APPROVED").startswith(
        f"{_ALEX} | PENALTY_REVIEW_APPROVED | Incomplete"
    )


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_an_approval_with_penalties_writes_one_line(tmp_path):
    """Alex approves round 3 (Pro)'s reports with Lewis disqualified from the Feature Race, the
    penalty applied for real: the log channel gets the approval's one line, written as it
    closes, which counts the penalty, and no PENALTIES_APPLIED line beside it."""
    dsq = StagedPenalty(
        driver_user_id=LEWIS,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="DSQ",
        penalty_seconds=None,
        description="Unsafe release",
        justification="Pit lane, lap 20",
    )
    league = await review_league(tmp_path)

    await _approve_reports(league, staged=[dsq])

    line = _line_of(league, "PENALTY_REVIEW_APPROVED")
    assert "PENALTY_REVIEW_APPROVED | Success" in line
    assert "penalties: 1" in line
    assert "PENALTIES_APPLIED" not in league.log()


async def _amendment_stage(tmp_path: Any, token: str, outcome: str) -> ReviewLeague:
    """Alex takes round 3 (Pro)'s amendment through one stage, Lewis's report staged: the report
    stage approved, or the appeals stage approved, cleanly or with its results post refused and
    discarded by a league admin."""
    if token == "AMEND_STAGE_2":
        league = await amend_league(tmp_path)
        await _amend_reports(league)
        return league
    league = await amend_league(tmp_path, reports_approved=True)
    if outcome == "Incomplete":
        league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    await _amend_appeals(league)
    if outcome == "Incomplete":
        assert await stopped_at(league) == "post_session_results"
        await _discard_until_clear(league)
    return league


@pytest.mark.parametrize(
    "token, outcome",
    [
        ("AMEND_STAGE_2", "Recorded"),
        ("RESULT_AMENDED", "Success"),
        ("RESULT_AMENDED", "Incomplete"),
    ],
)
@pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)
async def test_each_stage_of_an_amendment_names_the_member_who_pressed(tmp_path, token, outcome):
    """Alex approves the report stage of round 3 (Pro)'s amendment, or its appeals stage, which
    runs clean or has its results post discarded by a league admin: the line each writes reads
    "Alex (<@77>) | <TOKEN> | <outcome>", naming Alex who pressed and not the admin."""
    league = await _amendment_stage(tmp_path, token, outcome)

    assert _line_of(league, token).startswith(f"{_ALEX} | {token} | {outcome}")


@pytest.mark.parametrize(
    "token, carries",
    [
        pytest.param("AMEND_STAGE_2", "applied: +5s for <@101> in FEATURE_RACE", id="amend-reports",
                     marks=pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT)),
        pytest.param(
            "RESULT_AMENDED", "appeals applied: +10s for <@102> in FEATURE_RACE", id="amend-appeals",
            marks=pytest.mark.xfail(strict=True, reason=AMEND_NOT_BUILT),
        ),
        pytest.param(
            "APPEALS_REVIEW_APPROVED", "applied: +10s for <@102> in FEATURE_RACE", id="appeals",
            marks=pytest.mark.xfail(strict=True, reason=APPEALS_NOT_BUILT),
        ),
    ],
)
async def test_amend_stage_two_and_appeals_lines_carry_what_was_applied(tmp_path, token, carries):
    """The line of the stage that applied the verdicts says what was applied. Alex approves the
    report stage of round 3 (Pro)'s amendment with +5s for Lewis (101) in the Feature Race, or its
    appeals stage with a +10s correction for Max (102); or approves the appeals of round 3 (Pro),
    not an amendment, with that correction. The stage's one line names the penalty or correction,
    the driver and the session."""
    if token == "APPEALS_REVIEW_APPROVED":
        league = await _appeals_league(tmp_path)
        await _approve_appeals(league, staged=[_correction()])
    elif token == "AMEND_STAGE_2":
        league = await amend_league(tmp_path)
        await _amend_reports(league)
    else:
        league = await amend_league(tmp_path, reports_approved=True)
        await _amend_appeals(league, staged=[_correction()])

    assert carries in _line_of(league, token)
