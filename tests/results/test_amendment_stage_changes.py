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

import re

import json
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

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
    HEADING,
    LATER_ROUND_ID,
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
    verdict_headings,
)

REPORTS = "results.amendment.reports.approve"
APPEALS = "results.amendment.appeals.approve"

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


async def test_a_stopped_stage_retried_past_its_deadline_is_approved_but_not_carried_on(tmp_path):
    """The owner ruled (2026-10-05): an amendment's reports approval that lands after the
    amendment's half-hour, stuck on the queue and retried late, keeps the deadline. "The half-hour
    covers both steps and is never extended." The reply says the half-hour has passed, the
    amendment will be undone in the next few minutes, and to run /results rounds amend again,
    never that its appeals stage is posted below.

    The reports are recorded as approved; no appeals prompt is posted, as it could not be
    approved; and the next sweep undoes the amendment. Alex presses Approve ten minutes before
    the half-hour ends and the retry lands a minute after it, so that his reply, which the queue
    updates only for fourteen minutes, is still updated."""
    league = await _amend_league(tmp_path)
    _clock(league).advance(minutes=20)
    with points_fail():
        interaction = await _approve_reports(league)
        await run_queue(league.bot)
    _clock(league).advance(minutes=11)

    await retry_job(league.bot)

    assert await stopped_at(league) is None
    assert (await _amend_row(league))["reports_approved_at"] is not None
    assert await _record_drivers(league) == [LEWIS]
    assert _amend_appeals_prompts(league) == []
    reply = updated_reply(interaction)
    assert "/results rounds amend" in reply
    assert "posted below" not in reply
    assert re.search(r"half[- ](?:an )?hour", reply.lower()), (
        "the reply does not say the half-hour has passed"
    )
    assert "undone" in reply.lower(), "the reply does not say the amendment will be undone"

    assert await _sweep(league, after=timedelta(hours=2)) == 1

    row = await _amend_row(league)
    assert row is None or row["pre_amendment_state"] is None
    assert _amend_channel_deleted(league)


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


@pytest.mark.parametrize("stage", ["reports", "appeals"])
@pytest.mark.parametrize("lapsed", [False, True], ids=["inside", "past"])
async def test_a_discarded_stage_past_its_deadline_names_the_undoing_and_one_before_it_names_approve_again(
    tmp_path, stage, lapsed,
):
    """A stage whose points cannot be recalculated stops the queue at `apply`, and a league admin
    discards it: the report stage's sessions, or the appeals stage's cascade of standings, raise
    as a fault in the bot would. Inside the half-hour, nothing was changed and Alex may press Approve again; past
    it, the next sweep undoes the amendment, so the reply says so and names `/results rounds
    amend` instead. For the past case Alex presses Approve twenty minutes in and the discard lands
    eleven minutes later, so that his reply, which the queue updates for fourteen minutes, is
    still updated."""
    league = await _amend_league(tmp_path, reports_approved=stage == "appeals")
    if lapsed:
        _clock(league).advance(minutes=20)
    fault = points_fail() if stage == "reports" else patch(
        "leaguebot.results.services.amendment_stage_changes.cascade_recompute_from_round_on",
        new=AsyncMock(side_effect=RuntimeError("the standings could not be calculated")),
    )
    with fault:
        if stage == "reports":
            interaction = await _approve_reports(league)
        else:
            interaction = await _approve_appeals(league)
        await run_queue(league.bot)
    assert await stopped_at(league) == "apply"
    if lapsed:
        _clock(league).advance(minutes=11)

    await discard_job(league.bot)

    reply = updated_reply(interaction)
    assert "Nothing was changed" in reply
    if lapsed:
        assert re.search(r"half[- ](?:an )?hour", reply.lower()), (
            "the reply does not say the half-hour has passed"
        )
        assert "undone in the next few minutes" in reply
        assert "/results rounds amend" in reply
        assert "Press Approve" not in reply
    else:
        assert "Press Approve again" in reply
        if stage == "reports":
            assert "before it lapses" in reply
        assert "undone" not in reply.lower()


async def test_recovery_leaves_alone_an_amendment_whose_stage_is_in_hand(tmp_path):
    from leaguebot.__main__ import _recover_orphaned_amend_channels

    league = await _amend_league(tmp_path)
    await block_queue(league)
    await _approve_reports(league)

    await _recover_orphaned_amend_channels(league.bot)

    row = await _amend_row(league)
    assert row is not None and row["pre_amendment_state"] is not None
    assert not _amend_channel_deleted(league)


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


async def test_a_superseded_announcement_stays_where_a_replacement_was_discarded(tmp_path):
    league = await _amend_league(tmp_path, reports_approved=True)
    # The heading goes first and is a job of its own (owner, Gate 2): only Lewis's verdict, the
    # replacement of OLD_VERDICT, is refused, picked by its text, since a verdict starts
    # "**Season 1 Pro Round 3 — ..." as the heading does.
    league.channel(VERDICTS_CHANNEL).fail_when = (
        lambda content, _kwargs: f"**Driver**: <@{LEWIS}>" in content
    )
    interaction = await _approve_appeals(league)
    await run_queue(league.bot)
    assert await stopped_at(league) == "announce_verdict"

    await discard_job(league.bot)

    assert await stopped_at(league) is None
    assert OLD_VERDICT in league.channel(VERDICTS_CHANNEL).messages
    assert f"/{VERDICTS_CHANNEL}/{OLD_VERDICT}" in updated_reply(interaction)
    assert "RESULT_AMENDED | Incomplete" in league.log()


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


# ---------------------------------------------------------------------------
# What the rebuild posts again, and what it leaves alone
# ---------------------------------------------------------------------------

#: Round 2 at Bahrain, before the amended round; round 5 at Monza, cancelled; round 7 at Suzuka,
#: after round 4 but stored before it (a lower id).
EARLIER_ROUND_ID = 19
CANCELLED_ROUND_ID = 23
LAST_ROUND_ID = 18
#: What each of those rounds, and round 4, has standing in the results, standings and verdicts
#: channels.
OLD_ROUND_RESULTS = {EARLIER_ROUND_ID: 8970, LATER_ROUND_ID: 8971, CANCELLED_ROUND_ID: 8972,
                     LAST_ROUND_ID: 8973}
OLD_ROUND_STANDINGS = {EARLIER_ROUND_ID: 8974, LATER_ROUND_ID: 8975, LAST_ROUND_ID: 8976}
EARLIER_VERDICT = 8977
CANCELLED_VERDICT = 8978
LATER_REPORT = 8979
LATER_APPEAL = 8980
#: The banner heading round 3's verdicts, and round 4's.
OLD_BANNER = 8981
LATER_BANNER = 8982


async def _seed_round(
    league: ReviewLeague, round_id: int, number: int, track: str, *, status: str = "FINAL",
) -> dict[int, int]:
    """Round *number* of Pro at *track* (round 4 is already there), its Feature Race with Lewis
    first and Max second, its results posted, and, where it is not cancelled, its standings
    posted. A cancelled round's session is cancelled with it. Gives each driver's result id."""
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO rounds (id, division_id, round_number, scheduled_at, format, "
            "track_name, status) VALUES (?, ?, ?, '2026-02-01T18:00:00+00:00', 'NORMAL', ?, ?)",
            (round_id, DIVISION_ID, number, track, status),
        )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name, results_message_id, results_message_ids) "
            "VALUES (?, ?, 'FEATURE_RACE', ?, 'Standard', ?, ?)",
            (round_id, DIVISION_ID, "CANCELLED" if status == "CANCELLED" else "ACTIVE",
             OLD_ROUND_RESULTS[round_id], json.dumps([OLD_ROUND_RESULTS[round_id]])),
        )
        results: dict[int, int] = {}
        for position, driver in enumerate((LEWIS, MAX), start=1):
            cursor = await db.execute(
                "INSERT INTO race_session_results (session_result_id, driver_user_id, "
                "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded) "
                "VALUES (?, ?, 3001, ?, 'CLASSIFIED', ?, ?)",
                (session.lastrowid, driver, position, 3_600_000 + position * 1000,
                 25 if position == 1 else 18),
            )
            results[driver] = int(cursor.lastrowid or 0)
        if round_id in OLD_ROUND_STANDINGS:
            await db.execute(
                "DELETE FROM driver_standings_snapshots WHERE round_id = ?", (round_id,)
            )
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, total_points, standings_message_id, standings_message_ids) "
                "VALUES (?, ?, ?, 1, 25, ?, ?)",
                (round_id, DIVISION_ID, LEWIS, OLD_ROUND_STANDINGS[round_id],
                 json.dumps([OLD_ROUND_STANDINGS[round_id]])),
            )
        await db.commit()
    league.channel(RESULTS_CHANNEL).seed(OLD_ROUND_RESULTS[round_id], f"round {number} results")
    if round_id in OLD_ROUND_STANDINGS:
        league.channel(STANDINGS_CHANNEL).seed(
            OLD_ROUND_STANDINGS[round_id], f"round {number} standings"
        )
    return results


async def _announced_verdict(
    league: ReviewLeague, result_id: int, anchor: int | None, *, description: str,
    table: str = "penalty_records", justification: str | None = None,
) -> None:
    """A 5-second verdict of *description* on *result_id*, announced as *anchor* in the verdicts
    channel (where one is given): a report in `penalty_records`, or an upheld appeal in
    `appeal_records`.

    *justification* defaults to "Lap 7" for a report and "Appeal upheld" for an appeal. The
    penalty row that upholding an appeal writes beside it is seeded with the appeal's own
    justification, "Appeal upheld": upholding copies the description and the justification into
    both rows, and `penalty_service.reports_only` pairs the two on all six fields, the text
    included, so a row with any other justification is a separate report."""
    async with get_connection(league.db_path) as db:
        if table == "penalty_records":
            await db.execute(
                "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
                "description, justification, applied_by, applied_at, announcement_message_id, "
                "announcement_message_ids, announcement_channel_id) "
                "VALUES (?, 'TIME', 5, ?, ?, '77', ?, ?, ?, ?)",
                (result_id, description, justification or "Lap 7", NOW.isoformat(), anchor,
                 json.dumps([anchor]) if anchor else None,
                 str(VERDICTS_CHANNEL) if anchor else None),
            )
        else:
            await db.execute(
                "INSERT INTO appeal_records (race_result_id, status, penalty_type, time_seconds, "
                "description, justification, submitted_by, submitted_at, "
                "announcement_message_id, announcement_message_ids, announcement_channel_id) "
                "VALUES (?, 'UPHELD', 'TIME', 5, ?, ?, '78', ?, ?, ?, ?)",
                (result_id, description, justification or "Appeal upheld", NOW.isoformat(),
                 anchor,
                 json.dumps([anchor]) if anchor else None,
                 str(VERDICTS_CHANNEL) if anchor else None),
            )
        await db.commit()
    if anchor is not None:
        league.channel(VERDICTS_CHANNEL).seed(anchor, description)


async def _banner(league: ReviewLeague, round_id: int, message_id: int, *,
                  heads_sanctions: bool = False) -> None:
    """The banner heading *round_id*'s verdicts, standing in the verdicts channel, and heading an
    attendance sanction card too where *heads_sanctions*."""
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at, "
            "heads_sanctions) VALUES (?, ?, ?, ?, ?)",
            (round_id, str(VERDICTS_CHANNEL), str(message_id), NOW.isoformat(),
             int(heads_sanctions)),
        )
        await db.commit()
    league.channel(VERDICTS_CHANNEL).seed(message_id, "a banner")


async def _banners_left(league: ReviewLeague) -> list[int]:
    async with get_connection(league.db_path) as db:
        cursor = await db.execute("SELECT message_id FROM verdict_banner_messages ORDER BY id")
        return [int(row[0]) for row in await cursor.fetchall()]


def _rounds_headed(league: ReviewLeague, cid: int) -> list[int]:
    """The round numbers headed in what the bot posted or edited in channel *cid*, in the order
    it did so, each once."""
    import re

    messages = league.channel(cid).messages
    seen: list[int] = []
    for kind, ch, mid in league.events:
        if ch != cid or kind not in ("send", "edit") or mid not in messages:
            continue
        for number in re.findall(r"Round (\d+)", str(messages[mid].content or "")):
            if int(number) not in seen:
                seen.append(int(number))
    return seen


def _sent_saying(league: ReviewLeague, text: str) -> list[int]:
    channel = league.channel(VERDICTS_CHANNEL)
    return [mid for mid in league.sent_to(VERDICTS_CHANNEL)
            if text in str(getattr(channel.messages.get(mid), "content", "") or "")]


async def test_the_rebuild_reposts_every_round_s_results_and_standings_in_round_order(tmp_path):
    """Results spec, amendment: "Every round of the division shall be reposted, in round order,
    and not the amended round alone", a repost being a new message. Round 2 comes before the
    amended round 3; round 4 and round 7 after it, round 7 stored first; round 5 is cancelled."""
    league = await _amend_league(tmp_path, reports_approved=True)
    await _seed_round(league, EARLIER_ROUND_ID, 2, "Bahrain")
    await _seed_round(league, LATER_ROUND_ID, 4, "Spa")
    await _seed_round(league, CANCELLED_ROUND_ID, 5, "Monza", status="CANCELLED")
    await _seed_round(league, LAST_ROUND_ID, 7, "Suzuka")
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert _rounds_headed(league, RESULTS_CHANNEL) == [2, 3, 4, 7]
    assert _rounds_headed(league, STANDINGS_CHANNEL) == [2, 3, 4, 7]
    assert OLD_ROUND_RESULTS[CANCELLED_ROUND_ID] in league.channel(RESULTS_CHANNEL).messages


async def test_every_verdict_from_the_amended_round_on_is_announced_again_under_its_own_round(
    tmp_path,
):
    """Results spec, amendment: "All of a round's verdicts shall be announced, in order, and not
    only those the amendment changed", every round from the amended one on. Round 4 holds a
    report for Max (pit lane speeding) and Lewis's upheld appeal (track limits), with the penalty
    row upholding it writes beside it; round 2's verdict for Max, before the amended round, and
    cancelled round 5's, stand."""
    league = await _amend_league(tmp_path, reports_approved=True)
    earlier = await _seed_round(league, EARLIER_ROUND_ID, 2, "Bahrain")
    later = await _seed_round(league, LATER_ROUND_ID, 4, "Spa")
    cancelled = await _seed_round(league, CANCELLED_ROUND_ID, 5, "Monza", status="CANCELLED")
    await _announced_verdict(league, earlier[MAX], EARLIER_VERDICT, description="Unsafe rejoin")
    await _announced_verdict(league, cancelled[MAX], CANCELLED_VERDICT, description="Blocking")
    await _announced_verdict(league, later[MAX], LATER_REPORT, description="Pit lane speeding")
    await _announced_verdict(league, later[LEWIS], LATER_APPEAL, description="Track limits",
                             table="appeal_records")
    await _announced_verdict(league, later[LEWIS], None, description="Track limits",
                             justification="Appeal upheld")
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert _rounds_headed(league, VERDICTS_CHANNEL) == [3, 4]
    [report] = _sent_saying(league, "Pit lane speeding")
    [appeal] = _sent_saying(league, "Track limits")
    said = league.channel(VERDICTS_CHANNEL).messages
    assert "Round 4" in said[report].content and "Round 3" not in said[report].content
    assert league.sent_to(VERDICTS_CHANNEL).index(report) < league.sent_to(
        VERDICTS_CHANNEL).index(appeal)
    # The column is TEXT, the message id written as its string.
    assert int(await one(
        league.db_path, "SELECT announcement_message_id FROM appeal_records"
    )) == appeal, "the upheld appeal was not announced as an appeal"
    assert LATER_REPORT not in said and LATER_APPEAL not in said
    assert EARLIER_VERDICT in said and CANCELLED_VERDICT in said


#: The written heading over a batch of round 4's verdicts, image generation being off.
ROUND_4_HEADING = "**Season 1 Pro Round 4**"


async def test_each_round_s_verdicts_are_announced_again_under_one_heading_of_its_own(tmp_path):
    """Image spec, verdict banner: "One banner is posted per approval", naming its round, and a
    batch of verdicts is headed however the league is configured. In the rebuild, round 3's
    reports and its upheld correction sit under one heading for round 3, and round 4's report
    under one for round 4. The headings the rebuild posts are new, so the take-down of the
    superseded banners leaves them standing. Round 3's amendment has its reports approved (Lewis's
    and Max's); round 4 holds Max's announced report; each round has its old banner."""
    from leaguebot.results.models.points_config import SessionType
    from leaguebot.results.services.penalty_service import StagedPenalty

    league = await _amend_league(tmp_path, reports_approved=True)
    later = await _seed_round(league, LATER_ROUND_ID, 4, "Spa")
    await _announced_verdict(league, later[MAX], LATER_REPORT, description="Pit lane speeding")
    await _banner(league, ROUND_ID, OLD_BANNER)
    await _banner(league, LATER_ROUND_ID, LATER_BANNER)
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
    channel = league.channel(VERDICTS_CHANNEL)
    sent = league.sent_to(VERDICTS_CHANNEL)
    said = [str(channel.messages[mid].content or "") for mid in sent]
    headings = [i for i, text in enumerate(said) if text in (HEADING, ROUND_4_HEADING)]
    assert [said[i] for i in headings] == [HEADING, ROUND_4_HEADING]
    round_3 = [i for i, text in enumerate(said)
               if "Corner cutting" in text or "Track limits" in text]
    [round_4] = [i for i, text in enumerate(said) if "Pit lane speeding" in text]
    assert len(round_3) == 3
    assert headings[0] < min(round_3) and max(round_3) < headings[1] < round_4
    new_headings = [sent[i] for i in headings]
    assert all(mid in channel.messages for mid in new_headings)
    assert OLD_BANNER not in channel.messages and LATER_BANNER not in channel.messages
    assert await _banners_left(league) == new_headings


async def test_a_report_the_same_size_as_an_upheld_appeal_is_announced_as_a_report(tmp_path):
    """Results spec, amendment: "All of a round's verdicts shall be announced". Round 4 holds
    Lewis's upheld appeal (5 seconds, track limits), with the penalty row upholding it writes
    beside it, and a separate 5-second report for Lewis (unsafe rejoin), neither yet announced.
    The upheld appeal is announced once, as an appeal; the report of the same size is no copy of
    it, and is announced as a report."""
    league = await _amend_league(tmp_path, reports_approved=True)
    later = await _seed_round(league, LATER_ROUND_ID, 4, "Spa")
    # The report is seeded first, as production writes it (the report stage before the appeals
    # stage), so it holds the lower id: a pairing on the driver, session, type and seconds alone
    # would then claim it rather than the upheld appeal's own row, and this test would fail.
    await _announced_verdict(league, later[LEWIS], None, description="Unsafe rejoin")
    await _announced_verdict(league, later[LEWIS], None, description="Track limits",
                             table="appeal_records")
    await _announced_verdict(league, later[LEWIS], None, description="Track limits",
                             justification="Appeal upheld")
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    [appeal] = _sent_saying(league, "Track limits")
    [report] = _sent_saying(league, "Unsafe rejoin")
    # The column is TEXT, the message id written as its string.
    assert int(await one(
        league.db_path, "SELECT announcement_message_id FROM appeal_records"
    )) == appeal
    assert int(await one(
        league.db_path,
        "SELECT announcement_message_id FROM penalty_records WHERE description = ?",
        "Unsafe rejoin",
    )) == report


# ---------------------------------------------------------------------------
# The superseded announcements and their banners
# ---------------------------------------------------------------------------


async def test_a_round_s_old_banner_comes_down_once_its_verdicts_are_announced_again(tmp_path):
    """Results spec, amendment: a round's superseded announcements, "and the banner heading
    them", are removed only once every one of that round's replacements has been posted. Round 3's
    old banner heads Lewis's old verdict."""
    league = await _amend_league(tmp_path, reports_approved=True)
    await _banner(league, ROUND_ID, OLD_BANNER)
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    replacements = _sent_saying(league, "Corner cutting")
    assert len(replacements) == 2
    taken_down = league.events.index(("delete", VERDICTS_CHANNEL, OLD_BANNER))
    assert taken_down > max(
        league.events.index(("send", VERDICTS_CHANNEL, mid)) for mid in replacements
    )
    assert OLD_VERDICT not in league.channel(VERDICTS_CHANNEL).messages
    assert OLD_BANNER not in await _banners_left(league)


async def test_a_round_left_with_no_verdict_loses_its_old_announcement_and_banner(tmp_path):
    """Results spec, amendment: "A round left with no verdict at all shall have its superseded
    announcements and their banner removed likewise." The report stage approved no report for
    round 3, Lewis's old verdict and its banner standing."""
    league = await _amend_league(tmp_path, reports_approved=True)
    async with get_connection(league.db_path) as db:
        await db.execute("DELETE FROM penalty_records")
        await db.commit()
    await _banner(league, ROUND_ID, OLD_BANNER)
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert _sent_saying(league, "Corner cutting") == []
    assert OLD_VERDICT not in league.channel(VERDICTS_CHANNEL).messages
    assert OLD_BANNER not in league.channel(VERDICTS_CHANNEL).messages
    assert await _banners_left(league) == []


async def test_a_banner_heading_a_sanction_card_is_kept(tmp_path):
    """Results spec, amendment: "A banner that also heads an attendance sanction card shall be
    kept, the card being no verdict and staying where it is." Round 3's old banner heads Lewis's
    old verdict and a sanction card. The rebuild's heading for round 3 is a job of its own (owner,
    Gate 2), recorded as it is posted, so the table holds it beside the kept banner."""
    league = await _amend_league(tmp_path, reports_approved=True)
    await _banner(league, ROUND_ID, OLD_BANNER, heads_sanctions=True)
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    said = league.channel(VERDICTS_CHANNEL).messages
    assert OLD_VERDICT not in said
    assert OLD_BANNER in said
    [heading] = verdict_headings(league)
    assert heading in said
    assert await _banners_left(league) == [OLD_BANNER, heading]


#: The banner heading round 2's verdicts, before the amended round.
EARLIER_BANNER = 8983


async def test_an_earlier_round_s_banner_is_left_alone(tmp_path):
    """Results spec, amendment: the verdicts are announced again from the amended round on, so
    only those rounds' superseded announcements and banners come down. Round 2, before the
    amended round 3, has Max's announced verdict under its own banner; round 3's old banner heads
    Lewis's old verdict."""
    league = await _amend_league(tmp_path, reports_approved=True)
    earlier = await _seed_round(league, EARLIER_ROUND_ID, 2, "Bahrain")
    await _announced_verdict(league, earlier[MAX], EARLIER_VERDICT, description="Unsafe rejoin")
    await _banner(league, EARLIER_ROUND_ID, EARLIER_BANNER)
    await _banner(league, ROUND_ID, OLD_BANNER)
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    said = league.channel(VERDICTS_CHANNEL).messages
    assert EARLIER_BANNER in said and EARLIER_VERDICT in said
    assert OLD_BANNER not in said
    assert EARLIER_BANNER in await _banners_left(league)
    assert OLD_BANNER not in await _banners_left(league)
    assert _sent_saying(league, "Unsafe rejoin") == []


async def test_a_later_round_s_replacements_do_not_license_the_amended_round_s_take_down(
    tmp_path,
):
    """Results spec, amendment: a round's superseded announcements come down only once every
    one of *that round's* replacements is posted. Discord refuses round 3's verdict cards, and a
    league admin discards each; round 4's report for Max is announced again. Each round's heading
    is a job of its own (owner, Gate 2), so round 3's stands over no verdict, and both new
    headings are recorded beside the old banner kept for round 3."""
    league = await _amend_league(tmp_path, reports_approved=True)
    later = await _seed_round(league, LATER_ROUND_ID, 4, "Spa")
    await _announced_verdict(league, later[MAX], LATER_REPORT, description="Pit lane speeding")
    await _banner(league, ROUND_ID, OLD_BANNER)
    await _banner(league, LATER_ROUND_ID, LATER_BANNER)
    league.channel(VERDICTS_CHANNEL).fail_when = (
        lambda content, _kwargs: "Round 3" in content and "Corner cutting" in content
    )
    await _approve_appeals(league)
    await run_queue(league.bot)
    for _ in range(6):
        if await stopped_at(league) != "announce_verdict":
            break
        await discard_job(league.bot)

    assert await stopped_at(league) is None
    said = league.channel(VERDICTS_CHANNEL).messages
    assert len(_sent_saying(league, "Pit lane speeding")) == 1
    assert LATER_REPORT not in said and LATER_BANNER not in said
    assert OLD_VERDICT in said and OLD_BANNER in said
    [round_3_heading] = verdict_headings(league)
    [round_4_heading] = _sent_saying(league, ROUND_4_HEADING)
    assert round_3_heading in said
    assert _sent_saying(league, "Corner cutting") == []
    assert await _banners_left(league) == [OLD_BANNER, round_3_heading, round_4_heading]


# ---------------------------------------------------------------------------
# The attendance sheet and the sanctions
# ---------------------------------------------------------------------------


async def test_the_sheet_and_the_sanctions_follow_the_running_totals_to_the_last_round(tmp_path):
    """Results spec, amendment: "The attendance sheet shall be reposted once, against the round
    the running totals stand at". Round 4 is final after the amended round 3, so the sheet is
    drawn once, and the sanction thresholds read, against round 4; no driver is over one."""
    from unittest.mock import AsyncMock

    league = await _amend_league(tmp_path, reports_approved=True, attendance=True)
    sheet = AsyncMock(wraps=league.attendance.post_sheet)
    owed = AsyncMock(wraps=league.attendance.sanction_candidates)
    league.attendance.post_sheet = sheet
    league.attendance.sanction_candidates = owed
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert sheet.await_count == 1
    assert sheet.await_args.args[:2] == (LATER_ROUND_ID, DIVISION_ID)
    assert owed.await_args.args[:2] == (LATER_ROUND_ID, DIVISION_ID)
