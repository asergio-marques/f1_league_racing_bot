"""Approving an amendment's report stage and its appeals stage through the change queue (#439,
slice 2).

`results/services/amendment_stage_changes.py` carries the two stages of `/results rounds amend`
after its classification stage as changes:

- `results.amendment.reports.approve`: `apply`, one save (the superseded announcements noted, the
  amended sessions' verdict records cleared, the approved reports applied and the points
  recalculated, `round_amend_channels.reports_approved_at` set while the amendment is still
  unclaimed), the take-down of the stage's prompt and approval message, the amendment's appeals
  prompt, and `close`, writing `AMEND_STAGE_2 | Recorded`;
- `results.amendment.appeals.approve`: `names`, then `apply`, one save (the upheld appeals, the
  points, the pardons, the former drivers, the cascade of standings snapshots, the attendance
  recalculation, the release and the amend row's `closed_at`), then the division's replay between
  the batch notices, the superseded announcements' take-downs, the amendment channel's deletion and
  `close`, writing `RESULT_AMENDED`.

Nothing is undone because a stage's job failed: it stops the queue and is retried ("Retry like any
job"). While a stage's job is in hand, stopped included, the amendment neither lapses nor can be
cancelled, and restart recovery leaves it alone; once a league admin discards it, the next sweep
undoes an amendment past its half-hour ("Leave it while stuck").

The league is `tests.support.review_league`'s, round 3 already FINAL and its submission channel
closed, with an amendment of its Feature Race open in its own channel: stage one written (its
snapshot taken) and the report stage's prompt and approval message standing. A stage is asked for
as its Approve control asks it, `bot.change_queue.ask(kind, payload, ...)`, the staged reports,
corrections and pardons carried as plain data (`to_payload`).

Everything of the change types is imported inside a test, so this file collects while it is
unbuilt.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from leaguebot.core.db.database import get_connection
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
    AMEND_CHANNEL,
    DIVISION_ID,
    LEWIS,
    MAX,
    NOW,
    OLD_RESULTS,
    RESULTS_CHANNEL,
    ROUND_ID,
    STANDINGS_CHANNEL,
    VERDICTS_CHANNEL,
    ReviewLeague,
    block_queue,
    changes_of,
    is_appeals_prompt,
    one,
    penalty,
    penalty_records,
    points_fail,
    race_rows,
    review_league,
    stopped_at,
)

REPORTS = "results.amendment.reports.approve"
APPEALS = "results.amendment.appeals.approve"
NOT_BUILT = "#439: an amendment's stages are not yet changes on the queue"

#: The amendment's half-hour, from "now" as the queue's clock gives it.
DEADLINE = (NOW + timedelta(minutes=30)).isoformat()
#: The report stage's prompt and approval message, and the appeals stage's prompt, in the
#: amendment channel.
AMEND_PROMPT = 8950
AMEND_APPROVAL = 8951
AMEND_APPEALS_PROMPT = 8952
#: The verdict announcement the amendment supersedes, standing in the verdicts channel.
OLD_VERDICT = 8960


async def _amend_league(
    tmp_path: Any, *, deadline: str = DEADLINE, reports_approved: bool = False,
    attendance: bool = False, season_stage: str | None = None,
) -> ReviewLeague:
    """Round 3 of Pro, FINAL, under an amendment of its Feature Race opened by member 77 in the
    amendment channel. Stage one is written (its snapshot taken), the half-hour runs to
    *deadline*, and the report stage's prompt and approval message stand.

    Lewis's report from the round's review has been announced as `OLD_VERDICT` in the verdicts
    channel, which the amendment noted for taking down; Max holds an earlier report of his own.
    Where *reports_approved*, the report stage is already approved: Lewis's report written back
    without its announcement, and the appeals stage's prompt standing instead."""
    league = await review_league(tmp_path, attendance=attendance, round_status="FINAL")
    superseded = [{"anchor": OLD_VERDICT, "chunks": None, "channel_id": VERDICTS_CHANNEL,
                   "driver_user_id": LEWIS}]
    async with get_connection(league.db_path) as db:
        await db.execute(
            "UPDATE round_submission_channels SET closed = 1, in_penalty_review = 0"
        )
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "pre_amendment_state, expires_at, superseded_announcements, started_by, "
            "reports_approved_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, ?, ?, ?, 77, ?)",
            (ROUND_ID, AMEND_CHANNEL, NOW.isoformat(),
             json.dumps({"sessions": [], "profiles_before": []}), deadline,
             json.dumps(superseded), NOW.isoformat() if reports_approved else None),
        )
        cursor = await db.execute(
            "SELECT driver_user_id, id FROM race_session_results"
        )
        rows = {row["driver_user_id"]: row["id"] for row in await cursor.fetchall()}
        for driver in ((LEWIS, MAX) if reports_approved else (MAX,)):
            await db.execute(
                "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
                "description, justification, applied_by, applied_at) "
                "VALUES (?, 'TIME', 5, 'Corner cutting', 'Turn 4, lap 12', '77', ?)",
                (rows[driver], NOW.isoformat()),
            )
        if season_stage is not None:
            await db.execute("UPDATE seasons SET stage = ?", (season_stage,))
        await db.commit()
    amend = league.channel(AMEND_CHANNEL)
    if reports_approved:
        amend.seed(AMEND_APPEALS_PROMPT, "the amendment's appeals")
    else:
        amend.seed(AMEND_PROMPT, "the amendment's reports")
        amend.seed(AMEND_APPROVAL, "approve these reports?")
    league.channel(VERDICTS_CHANNEL).seed(OLD_VERDICT, "Lewis: 5 seconds")
    return league


def _manager(league: ReviewLeague) -> Any:
    return member_interaction(
        league.bot, user=tier_member("manager", display_name="Alex", name="Alex#0001"),
    )


async def _approve_reports(league: ReviewLeague, *, staged: Any = None) -> Any:
    """Press Approve on the amendment's report stage as the league manager Alex, with Lewis's
    5-second report kept (or *staged*); the queue is not yet run. Gives Alex's interaction."""
    staged = [penalty(LEWIS)] if staged is None else staged
    interaction = _manager(league)
    await league.bot.change_queue.ask(
        REPORTS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "session_types": ["FEATURE_RACE"],
            "staged": [report.to_payload() for report in staged],
            "pardons": [],
            "prompt_message_id": AMEND_PROMPT,
            "approval_message_id": AMEND_APPROVAL,
        },
        interaction=interaction,
        what="✅ Approve on round 3's amendment reports",
    )
    return interaction


async def _approve_appeals(league: ReviewLeague) -> Any:
    """Press Approve on the amendment's appeals stage as Alex, with no correction staged; the
    queue is not yet run. Gives Alex's interaction."""
    interaction = _manager(league)
    await league.bot.change_queue.ask(
        APPEALS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "session_types": ["FEATURE_RACE"],
            "staged": [],
            "pardons": [],
            "appeals_prompt_message_id": AMEND_APPEALS_PROMPT,
        },
        interaction=interaction,
        what="✅ Approve on round 3's amendment appeals",
    )
    return interaction


async def _run_until_done(league: ReviewLeague, kind: str, job: str) -> None:
    """Run the queue one job at a time until *job* of the last change of *kind* is done."""
    for _ in range(40):
        change = (await changes_of(league.db_path, kind))[-1]
        rows = [row for row in await step_rows(league.db_path, change["id"])
                if row["name"] == job]
        if rows and rows[0]["done_at"] is not None:
            return
        await run_queue(league.bot, steps=1)
    raise AssertionError(f"{job} was never done")


async def _amend_row(league: ReviewLeague) -> dict[str, Any] | None:
    async with get_connection(league.db_path) as db:
        row = await (await db.execute(
            "SELECT * FROM round_amend_channels WHERE round_id = ?", (ROUND_ID,)
        )).fetchone()
    return None if row is None else dict(row)


def _amend_appeals_prompts(league: ReviewLeague) -> list[int]:
    return [
        mid for mid, message in league.channel(AMEND_CHANNEL).messages.items()
        if type(message.view).__name__ == "AppealsReviewView"
    ]


def _amend_channel_deleted(league: ReviewLeague) -> bool:
    return ("delete_channel", AMEND_CHANNEL, AMEND_CHANNEL) in league.events


async def _record_drivers(league: ReviewLeague) -> list[int]:
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "SELECT r.driver_user_id FROM penalty_records p "
            "JOIN race_session_results r ON r.id = p.race_result_id ORDER BY r.driver_user_id"
        )
        return [row["driver_user_id"] for row in await cursor.fetchall()]


def _clock(league: ReviewLeague) -> Any:
    return getattr(league.bot, "_change_queue_test_setup").clock


async def _sweep(league: ReviewLeague, *, after: timedelta) -> int:
    from leaguebot.results.services.result_submission_service import sweep_expired_amendments

    return await sweep_expired_amendments(league.bot, now=NOW + after)


# ---------------------------------------------------------------------------
# The report stage
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_report_stage_is_approved_once_across_a_restart(tmp_path):
    league = await _amend_league(tmp_path)
    await _approve_reports(league)
    await _run_until_done(league, REPORTS, "apply")

    await restart_queue(league.bot)
    await run_queue(league.bot)
    again = await _approve_reports(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert (await _amend_row(league))["reports_approved_at"] is not None
    assert await _record_drivers(league) == [LEWIS]
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert len(_amend_appeals_prompts(league)) == 1
    assert "already approved" in acknowledgement(again)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_report_stage_writes_the_reports_the_manager_approved(tmp_path):
    league = await _amend_league(tmp_path)
    await _approve_reports(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await _record_drivers(league) == [LEWIS]
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 5000
    assert (await _amend_row(league))["reports_approved_at"] is not None
    assert (await _amend_row(league))["expires_at"] == DEADLINE
    messages = league.channel(AMEND_CHANNEL).messages
    assert AMEND_PROMPT not in messages and AMEND_APPROVAL not in messages
    assert len(_amend_appeals_prompts(league)) == 1
    for published in (RESULTS_CHANNEL, STANDINGS_CHANNEL, VERDICTS_CHANNEL):
        assert league.sent_to(published) == []
    assert "AMEND_STAGE_2 | Recorded" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_report_stage_whose_points_cannot_be_recalculated_writes_nothing_and_stays_open(
    tmp_path,
):
    league = await _amend_league(tmp_path)
    with points_fail():
        await _approve_reports(league)
        await run_queue(league.bot)

    assert await stopped_at(league) == "apply"
    assert await _record_drivers(league) == [MAX]
    assert (await race_rows(league.db_path))[LEWIS]["postrace_time_penalties_ms"] == 0
    row = await _amend_row(league)
    assert row["reports_approved_at"] is None
    assert row["pre_amendment_state"] is not None
    assert row["expires_at"] == DEADLINE
    assert AMEND_PROMPT in league.channel(AMEND_CHANNEL).messages
    assert not _amend_channel_deleted(league)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_report_stage_apply_leaves_its_prompt_to_approve_again(tmp_path):
    league = await _amend_league(tmp_path)
    with points_fail():
        first = await _approve_reports(league)
        await run_queue(league.bot)
    await discard_job(league.bot)

    messages = league.channel(AMEND_CHANNEL).messages
    assert AMEND_PROMPT in messages and AMEND_APPROVAL in messages
    assert "Approve" in updated_reply(first)

    await _approve_reports(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert (await _amend_row(league))["reports_approved_at"] is not None
    assert AMEND_PROMPT not in league.channel(AMEND_CHANNEL).messages


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_amendment_appeals_prompt_says_so_in_the_reply(tmp_path):
    league = await _amend_league(tmp_path)
    league.channel(AMEND_CHANNEL).fail_when = is_appeals_prompt
    interaction = await _approve_reports(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_appeals_prompt"

    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "⚠️" in reply
    assert "appeals" in reply.lower()
    assert "/results rounds amend" in reply
    assert _amend_appeals_prompts(league) == []


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_amendment_checks_read_the_handed_clock(tmp_path):
    league = await _amend_league(tmp_path, deadline="2099-01-01T00:00:00+00:00")
    _clock(league).now = datetime(2100, 1, 1, tzinfo=timezone.utc)

    interaction = await _approve_reports(league)
    await run_queue(league.bot)

    assert "no longer open" in acknowledgement(interaction)
    assert await _record_drivers(league) == [MAX]
    assert (await _amend_row(league))["reports_approved_at"] is None


# ---------------------------------------------------------------------------
# A stage stuck on the queue ("Leave it while stuck")
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stopped_report_stage_left_past_its_deadline_is_not_swept_while_in_hand(tmp_path):
    league = await _amend_league(tmp_path)
    with points_fail():
        await _approve_reports(league)
        await run_queue(league.bot)
    assert await stopped_at(league) == "apply"

    assert await _sweep(league, after=timedelta(hours=2)) == 0

    row = await _amend_row(league)
    assert row["pre_amendment_state"] is not None
    assert not _amend_channel_deleted(league)
    assert "put back" not in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stopped_stage_retried_past_its_deadline_is_approved(tmp_path):
    league = await _amend_league(tmp_path)
    with points_fail():
        await _approve_reports(league)
        await run_queue(league.bot)
    _clock(league).advance(hours=2)

    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert (await _amend_row(league))["reports_approved_at"] is not None
    assert await _record_drivers(league) == [LEWIS]
    assert len(_amend_appeals_prompts(league)) == 1


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_stage_past_its_deadline_is_undone_at_the_next_sweep(tmp_path):
    league = await _amend_league(tmp_path)
    with points_fail():
        await _approve_reports(league)
        await run_queue(league.bot)
    _clock(league).advance(hours=2)
    await discard_job(league.bot)

    assert await _sweep(league, after=timedelta(hours=2)) == 1

    row = await _amend_row(league)
    assert row is None or row["pre_amendment_state"] is None
    assert _amend_channel_deleted(league)
    assert "put back" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_recovery_leaves_alone_an_amendment_whose_stage_is_in_hand(tmp_path):
    from leaguebot.__main__ import _recover_orphaned_amend_channels

    league = await _amend_league(tmp_path)
    await block_queue(league)
    await _approve_reports(league)

    await _recover_orphaned_amend_channels(league.bot)

    row = await _amend_row(league)
    assert row is not None and row["pre_amendment_state"] is not None
    assert not _amend_channel_deleted(league)


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_cancel_is_refused_while_a_stage_is_in_hand(tmp_path):
    from leaguebot.results.services.result_submission_service import cancel_amendment

    league = await _amend_league(tmp_path)
    await block_queue(league)
    await _approve_reports(league)

    assert await cancel_amendment(league.bot, ROUND_ID, cancelled_by=77) is False

    row = await _amend_row(league)
    assert row["pre_amendment_state"] is not None
    assert row["expires_at"] == DEADLINE
    assert not _amend_channel_deleted(league)


# ---------------------------------------------------------------------------
# The appeals stage, which commits the amendment
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_stop_after_the_last_stage_is_saved_finishes_the_rebuild_on_restart(tmp_path):
    league = await _amend_league(tmp_path, reports_approved=True, attendance=True)
    await _approve_appeals(league)
    await _run_until_done(league, APPEALS, "apply")

    await restart_queue(league.bot)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await one(
        league.db_path,
        "SELECT COUNT(*) FROM driver_standings_snapshots WHERE round_id = ? AND driver_user_id = ?",
        ROUND_ID, MAX,
    ) == 1
    assert len(league.attendance._calls("recalculate_on")) == 1
    assert OLD_VERDICT not in league.channel(VERDICTS_CHANNEL).messages
    assert OLD_RESULTS not in league.channel(RESULTS_CHANNEL).messages
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert _amend_channel_deleted(league)
    assert "RESULT_AMENDED | Success" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_superseded_announcement_stays_where_a_replacement_was_discarded(tmp_path):
    league = await _amend_league(tmp_path, reports_approved=True)
    league.channel(VERDICTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    interaction = await _approve_appeals(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "announce_verdict"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert OLD_VERDICT in league.channel(VERDICTS_CHANNEL).messages
    assert f"/{VERDICTS_CHANNEL}/{OLD_VERDICT}" in updated_reply(interaction)
    assert "RESULT_AMENDED | Incomplete" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_last_stage_s_reposts_are_retried(tmp_path):
    league = await _amend_league(tmp_path, reports_approved=True)
    results = league.channel(RESULTS_CHANNEL)
    results.send_fails = http_error(status=403, text="Missing Access")
    await _approve_appeals(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_session_results"
    assert OLD_RESULTS in results.messages

    results.send_fails = None
    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert OLD_RESULTS not in results.messages
    assert len(league.sent_to(RESULTS_CHANNEL)) == 1
    assert _amend_channel_deleted(league)
    assert "RESULT_AMENDED | Success" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_amendment_reply_names_what_was_discarded(tmp_path):
    league = await _amend_league(tmp_path, reports_approved=True)
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    interaction = await _approve_appeals(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_session_results"

    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "⚠️" in reply
    assert "/results rounds sync" in reply
    assert OLD_RESULTS in league.channel(RESULTS_CHANNEL).messages
    assert "RESULT_AMENDED | Incomplete" in league.log()


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_a_discarded_repost_during_pending_completion_names_results_rounds_amend(tmp_path):
    league = await _amend_league(
        tmp_path, reports_approved=True, season_stage="PENDING_COMPLETION",
    )
    league.channel(RESULTS_CHANNEL).send_fails = http_error(status=403, text="Missing Access")
    interaction = await _approve_appeals(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "post_session_results"

    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "/results rounds amend" in reply
    assert "/results rounds sync" not in reply


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_rebuild_goes_in_the_order_a_league_reads_it(tmp_path):
    """Results spec, amendment: the division's channels are rebuilt "in the order a league reads
    them: the results, the standings, the attendance sheet, the round's report verdicts, then its
    appeal verdicts". Round 3's amendment, its reports approved (Lewis's and Max's) and attendance
    on, has its appeals stage approved by Alex with a 10-second correction for Max upheld."""
    from leaguebot.results.services.penalty_service import StagedPenalty
    from leaguebot.results.models.points_config import SessionType

    league = await _amend_league(tmp_path, reports_approved=True, attendance=True)
    correction = StagedPenalty(
        driver_user_id=MAX, session_type=SessionType.FEATURE_RACE, penalty_type="TIME",
        penalty_seconds=10, description="Track limits", justification="Appeal upheld, lap 7",
    )
    await league.bot.change_queue.ask(
        APPEALS,
        {
            "round_id": ROUND_ID,
            "division_id": DIVISION_ID,
            "session_types": ["FEATURE_RACE"],
            "staged": [correction.to_payload()],
            "pardons": [],
            "appeals_prompt_message_id": AMEND_APPEALS_PROMPT,
        },
        interaction=_manager(league),
        what="✅ Approve on round 3's amendment appeals",
    )
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    [change] = await changes_of(league.db_path, APPEALS)
    jobs = [row["name"] for row in await step_rows(league.db_path, change["id"])]
    first = {name: jobs.index(name) for name in
             ("post_session_results", "post_standings", "attendance_sheet", "announce_verdict")}
    assert (first["post_session_results"] < first["post_standings"] < first["attendance_sheet"]
            < first["announce_verdict"])
    channel = league.channel(VERDICTS_CHANNEL)
    said = [channel.messages[mid].content or "" for mid in league.sent_to(VERDICTS_CHANNEL)]
    reports = [i for i, text in enumerate(said) if "Corner cutting" in text]
    appeals = [i for i, text in enumerate(said) if "Track limits" in text]
    assert len(reports) == 2 and len(appeals) == 1
    assert max(reports) < min(appeals)
