"""Confirming a round's penalty review, then its appeals review.

Issue #208. `finalize_penalty_review` and `finalize_appeals_review` were partly covered — the
paths with nothing staged ran, and the ones that actually change a championship did not.

**A settled round is never reopened.** The review views outlive the round: disabling the results
module closes every round still awaiting review, but a client already holding the message can
still press the button. Both status writes are guarded by the terminal states, so a stale press
cannot drag a FINAL or CANCELLED round back into an awaiting one (#167).

**A review that has moved on approves nothing, and one approval runs at a time** (#402). The
report approval is refused while a resubmission is collecting, once the reports are approved,
and from a review whose prompt has been replaced — each of which the review's buttons once
reached — and a second press while the first is still drawing the round's graphics is refused
rather than running it all again.

**The attendance pipeline runs only where attendance is enabled, and each step is independent.**
Attendance is recorded from the results, staged pardons are persisted, points distributed, the
sheet posted and sanctions enforced — and a failure in one must not stop the rest, because the
penalty verdicts are already published by the time it runs. Pardons are inserted idempotently,
so a recovered finalisation cannot grant one twice. A sanction that did not apply is told to the
approving manager and the log channel, with the `/attendance sync` that finishes it (#239).

**Approving appeals is what finishes a round, and so what finishes a division.** The round goes
to FINAL, the division's status is reconsidered — the last round's appeals are what lets
`/season complete` run (#154) — and the submission channel is closed. Upheld corrections are
recorded as appeal records and announced; an announcement that fails does not un-approve them.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.results.models.points_config import SessionType
from leaguebot.attendance.services.attendance_service import SanctionOutcome
from leaguebot.results.services.penalty_service import StagedPenalty
from leaguebot.results.services.penalty_wizard import PenaltyReviewState, StagedPardon
from leaguebot.results.services.results_post_service import ReplayOutcome
from leaguebot.results.services.result_submission_service import (
    finalize_appeals_review,
    finalize_penalty_review,
)
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
    APPROVAL,
    LATER_ROUND_ID,
    LEWIS,
    LEWIS_PROFILE,
    MAX,
    MAX_PROFILE,
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


async def _former(db_path, profile_id: int) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT former_driver FROM driver_profiles WHERE id = ?", (profile_id,)
        )
        return (await cursor.fetchone())["former_driver"]


def _penalty(driver: int = 101) -> StagedPenalty:
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=5,
        description="Corner cutting",
        justification="Turn 4, lap 12",
    )


def _state(db_path, *, staged=(), appeals=(), pardons=(), attendance_enabled=False):
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    bot.add_view = MagicMock()
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock()
    bot.module_service = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance_enabled)
    return PenaltyReviewState(
        round_id=ROUND_ID,
        division_id=DIVISION_ID,
        submission_channel_id=700,
        session_types_present=[SessionType.FEATURE_RACE],
        db_path=db_path,
        bot=bot,
        staged=list(staged),
        staged_appeals=list(appeals),
        staged_pardons=list(pardons),
        round_number=3,
        division_name="Pro",
    )


def _interaction(*, guild=True):
    """A press by the league manager Alex, answering as Discord's does — not done until it
    replies or defers — whose client reaches a log channel of its own."""
    answered = {"done": False}

    async def _answer(*_args, **_kwargs):
        answered["done"] = True

    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = STEWARD
    interaction.user.display_name = "Alex"
    interaction.client.output_router.post_log = AsyncMock(return_value=None)
    interaction.response = MagicMock()
    interaction.response.is_done = MagicMock(side_effect=lambda: answered["done"])
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    if guild:
        channel = MagicMock()
        message = MagicMock()
        message.id = 9900
        channel.send = AsyncMock(return_value=message)
        interaction.guild = MagicMock()
        interaction.guild.get_channel = MagicMock(return_value=channel)
    else:
        interaction.guild = None
    return interaction


def _patches(
    *, apply_result=None, announce_error=None, attendance_errors=None, sanction_outcome=None,
    repost_faults=None, subsequent_faults=None, verdict_faults=None,
):
    """*repost_faults* are the lines the results cascade could not post (#237).

    Both repost functions return a list of faults rather than ``None``, so the stubs must
    too: the approvals now add what comes back to what they report.
    """
    attendance_errors = attendance_errors or {}
    return {
        "snapshot": patch(
            "leaguebot.results.services.result_submission_service._snapshot_staged_drivers",
            new=AsyncMock(return_value=[]),
        ),
        "recompute": patch(
            "leaguebot.results.services.result_submission_service._recompute_session_points", new=AsyncMock()
        ),
        "apply": patch(
            "leaguebot.results.services.penalty_service.apply_penalties",
            new=AsyncMock(return_value=apply_result if apply_result is not None else [{}]),
        ),
        "repost": patch(
            "leaguebot.results.services.results_post_service.delete_and_repost_final_results",
            new=AsyncMock(return_value=list(repost_faults or [])),
        ),
        "subsequent": patch(
            "leaguebot.results.services.results_post_service.repost_subsequent_standings",
            new=AsyncMock(return_value=list(subsequent_faults or [])),
        ),
        "banner": patch(
            "leaguebot.results.services.verdict_announcement_service.banner_for_round", new=MagicMock()
        ),
        # Both return the verdicts they could not announce, so the stubs must too (#237):
        # a bare AsyncMock returns a truthy MagicMock, which would report a fault on every
        # approval that announced perfectly well.
        "penalty_announce": patch(
            "leaguebot.results.services.verdict_announcement_service.post_penalty_announcements",
            new=AsyncMock(
                side_effect=announce_error, return_value=list(verdict_faults or [])
            ),
        ),
        # The amendment's own rebuild (#345). Stubbed so the appeals finaliser can be driven
        # with `is_amendment=True` without reaching Discord or the attendance module.
        "replay": patch(
            "leaguebot.results.services.results_post_service.replay_division_channels",
            new=AsyncMock(return_value=ReplayOutcome([], frozenset({ROUND_ID}))),
        ),
        "amend_attendance": patch(
            "leaguebot.results.services.result_submission_service._repost_attendance_after_amendment",
            new=AsyncMock(return_value=[]),
        ),
        "cascade_standings": patch(
            "leaguebot.results.services.standings_service.cascade_recompute_from_round", new=AsyncMock()
        ),
        "appeal_announce": patch(
            "leaguebot.results.services.verdict_announcement_service.post_appeal_announcements",
            new=AsyncMock(
                side_effect=announce_error, return_value=list(verdict_faults or [])
            ),
        ),
        "record": patch(
            "leaguebot.attendance.services.attendance_service.record_attendance_from_results",
            new=AsyncMock(side_effect=attendance_errors.get("record")),
        ),
        "distribute": patch(
            "leaguebot.attendance.services.attendance_service.distribute_attendance_points",
            new=AsyncMock(side_effect=attendance_errors.get("distribute")),
        ),
        "sheet": patch(
            "leaguebot.attendance.services.attendance_service.post_attendance_sheet",
            new=AsyncMock(side_effect=attendance_errors.get("sheet")),
        ),
        "sanctions": patch(
            "leaguebot.attendance.services.attendance_service.enforce_attendance_sanctions",
            new=AsyncMock(
                side_effect=attendance_errors.get("sanctions"),
                return_value=sanction_outcome or SanctionOutcome(),
            ),
        ),
        "appeals_view": patch("leaguebot.results.services.penalty_wizard.AppealsReviewView", new=MagicMock()),
        "appeals_prompt": patch(
            "leaguebot.results.services.penalty_wizard._render_appeals_prompt_content",
            new=AsyncMock(return_value="appeals prompt"),
        ),
        "close": patch(
            "leaguebot.results.services.result_submission_service.close_submission_channel", new=AsyncMock()
        ),
        "refresh": patch(
            "leaguebot.core.services.season_service.SeasonService.refresh_division_status", new=AsyncMock()
        ),
    }


async def _run(fn, state, interaction=None, *, replay_override=False, **patch_kwargs):
    """*replay_override* leaves the replay unpatched so a caller can patch it themselves."""
    interaction = interaction or _interaction()
    patches = _patches(**patch_kwargs)
    if replay_override:
        patches.pop("replay", None)
    started = {key: p.start() for key, p in patches.items()}
    try:
        await fn(interaction, state)
    finally:
        for p in patches.values():
            p.stop()
    return started


async def _round_status(db_path) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (ROUND_ID,))
        return (await cursor.fetchone())["status"]


def _logged(state) -> str:
    return "\n".join(str(c.args[0]) for c in state.bot.output_router.post_log.await_args_list)


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


def _review_channel(state, *, fails: bool = False):
    """The submission channel as the review reaches it, holding its prompt and approval."""
    channel = MagicMock()
    message = MagicMock()
    message.delete = AsyncMock()
    channel.fetch_message = AsyncMock(
        side_effect=RuntimeError("gateway gone") if fails else None, return_value=message
    )
    state.bot.get_channel = MagicMock(return_value=channel)
    channel._message = message
    return channel


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


async def test_an_amendments_report_stage_takes_its_controls_down(tmp_path):
    """**The prompt as well as the approval**, as a first pass's. An amendment's pardons close with
    its reports (decided 2026-09-23), so nothing on the prompt is left to do."""
    db_path = await _make_db(tmp_path, name="amend_takes_down")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.prompt_message_id = 990001
    state.approval_message_id = 990002
    channel = _review_channel(state)

    await _run(finalize_penalty_review, state)

    assert sorted(c.args[0] for c in channel.fetch_message.await_args_list) == [990001, 990002]
    assert state.approval_message_id is None
    assert state.reports_approved is True


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
# An amendment rewrites the round's decisions rather than adding to them (#345)
# ---------------------------------------------------------------------------
#
# These drive the finaliser with `apply_penalties` **real** and count rows, because that is the
# only thing that catches the defect they exist for. `apply_penalties` only ever inserts, and
# adds to the stored penalty columns; replaying a round's reports over records still in place
# duplicated every one of them, and doubled the sanction again on a second amendment. The
# structural assertions in `test_amendment_replays_without_moving_the_round.py` cannot see any
# of that — an earlier version of that file asserted the defect and called it correct.


async def _verdict_count(db_path, table: str = "penalty_records") -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        return (await cursor.fetchone())["n"]


async def _pardon_count(db_path) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM attendance_pardons")
        return (await cursor.fetchone())["n"]


async def _seed_driver_row(db_path, driver: int = 101) -> None:
    """A race result row for the penalised driver, which `apply_penalties` attaches to.

    The shared fixture seeds a session header and no driver rows — enough for every test that
    stubs `apply_penalties`, and not enough for one that lets it write.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM session_results WHERE round_id = ? AND session_type = ?",
            (ROUND_ID, "FEATURE_RACE"),
        )
        row = await cursor.fetchone()
        if row is None:
            cursor = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (ROUND_ID, DIVISION_ID),
            )
            session_id = cursor.lastrowid
        else:
            session_id = row["id"]
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position) VALUES (?, ?, 3001, 1)",
            (session_id, driver),
        )
        await db.commit()


#: The deadline an open amendment carries in these tests: far enough off never to lapse.
_OPEN_DEADLINE = "2099-01-01T00:00:00+00:00"


async def _open_amendment(state, *, snapshot: str = "{}") -> None:
    """Mark *state* an amendment, and give it the open amendment it would have in life.

    Every stage of an amendment first claims the amendment's deadline (#345); with no
    `round_amend_channels` row there is nothing to claim, and the stage refuses to run — which
    would let a test asserting that something did *not* happen pass without the stage having
    run at all.

    *snapshot* is the ``pre_amendment_state`` stage one wrote. It carries ``profiles_before``
    for the former-driver recompute (#216), which is how a driver the amendment struck out is
    still reconsidered when they are in no result to be found by.
    """
    state.is_amendment = True
    async with get_connection(state.db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO round_amend_channels (round_id, channel_id, session_types, "
            "created_at, pre_amendment_state, expires_at) VALUES (?, 700, '[\"FEATURE_RACE\"]', "
            "'2026-02-02T00:00:00+00:00', ?, ?)",
            (state.round_id, snapshot, _OPEN_DEADLINE),
        )
        await db.commit()


async def _run_real_apply(fn, state, interaction=None, **patch_kwargs):
    """As `_run`, but with `apply_penalties` left real so its writes can be counted."""
    interaction = interaction or _interaction()
    patches = {k: v for k, v in _patches(**patch_kwargs).items() if k != "apply"}
    started = {key: p.start() for key, p in patches.items()}
    try:
        await fn(interaction, state)
    finally:
        for p in patches.values():
            p.stop()
    return started


async def test_an_amendment_does_not_duplicate_the_rounds_penalty_records(tmp_path):
    """**The defect the independent review found.**

    Stage two hydrates the round's existing reports into `state.staged` and approving re-applies
    them. With the old records still in place that left two rows for one incident — and a second
    amendment then hydrated both, applying twice the sanction the steward gave.
    """
    db_path = await _make_db(tmp_path, name="amend_no_dupe")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)
    first = await _verdict_count(db_path)

    # Replay it again, as a second amendment of the same round would.
    state_again = _state(db_path, staged=[_penalty()])
    await _open_amendment(state_again)
    await _run_real_apply(finalize_penalty_review, state_again)

    assert first == 1
    assert await _verdict_count(db_path) == 1


async def test_a_first_pass_still_records_and_applies_once(tmp_path):
    """The ordinary path is untouched — the guards must not have cost it its own behaviour."""
    db_path = await _make_db(tmp_path, name="first_pass_intact")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])

    await _run_real_apply(finalize_penalty_review, state)

    assert await _verdict_count(db_path) == 1


async def test_a_report_removed_in_stage_two_is_removed_from_the_record(tmp_path):
    """Delete-and-rewrite is what makes the stage editable at all.

    Approving with a report taken out has to leave it out; `INSERT OR IGNORE` semantics would
    have kept the row and made the Remove button decorative.
    """
    db_path = await _make_db(tmp_path, name="amend_removal")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    await _run_real_apply(finalize_penalty_review, state)
    assert await _verdict_count(db_path) == 1

    # The manager removes it and approves again.
    emptied = _state(db_path, staged=[])
    await _open_amendment(emptied)
    await _run_real_apply(finalize_penalty_review, emptied)

    assert await _verdict_count(db_path) == 0


async def test_an_amendment_does_not_post_the_attendance_sheet_itself(tmp_path):
    """The sheet and the sanctions belong to the final stage (#345).

    Running both posted two sheets — the first built on attended flags describing the round
    being replaced, because this stage's cascade does not rebuild them — and enforced the
    sanctions twice, so a driver could be sacked by a sheet the next stage was about to correct.
    """
    db_path = await _make_db(tmp_path, name="amend_no_sheet")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    await _open_amendment(state)

    stubs = await _run(finalize_penalty_review, state)

    stubs["sheet"].assert_not_awaited()
    stubs["sanctions"].assert_not_awaited()


async def test_a_first_pass_still_posts_the_attendance_sheet(tmp_path):
    """The counterpart, so the guard cannot become "never post a sheet"."""
    db_path = await _make_db(tmp_path, name="first_pass_sheet")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)

    stubs = await _run(finalize_penalty_review, state)

    stubs["sheet"].assert_awaited()


async def test_an_amendment_still_reaches_the_appeal_stage(tmp_path):
    """Leaving the report stage early must not strand the amendment.

    The classification is corrected and the reports approved; with no appeals prompt there is
    no route to the appeals, and none to the rebuild that follows them.
    """
    db_path = await _make_db(tmp_path, name="amend_reaches_appeals")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    await _open_amendment(state)

    await _run(finalize_penalty_review, state)

    assert state.appeals_prompt_message_id is not None


async def test_an_amendment_announces_each_appeal_verdict_once(tmp_path):
    """The rebuild announces the round's verdicts; announcing them again doubled them (#345).

    `replay_division_channels` re-announces every verdict of every round from the amended one
    forward — the appeals just written among them. Posting them a second time here gave the
    driver the same decision twice, and the second could not be removed: the superseded set was
    captured before either went up, so neither was in it.
    """
    db_path = await _make_db(tmp_path, name="amend_appeal_once")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    stubs = await _run(finalize_appeals_review, state)

    stubs["appeal_announce"].assert_not_awaited()


async def test_a_first_pass_still_announces_its_appeal_verdicts(tmp_path):
    """The counterpart: an ordinary round has no rebuild to announce them for it."""
    db_path = await _make_db(tmp_path, name="first_pass_appeal_announce")
    state = _state(db_path, appeals=[_penalty()])

    stubs = await _run(finalize_appeals_review, state)

    stubs["appeal_announce"].assert_awaited_once()


async def test_an_amendment_rebuilds_the_division_once_at_the_end(tmp_path):
    """The whole point of the third stage: every decision is in, so the channels go back in
    order — and the round-only repost a first pass uses is *not* also run."""
    db_path = await _make_db(tmp_path, name="amend_rebuild_once")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    stubs = await _run(finalize_appeals_review, state)

    stubs["replay"].assert_awaited_once()
    stubs["repost"].assert_not_awaited()
    # The attendance sheet is handed to the rebuild as a step rather than run after it, so that
    # it lands between the standings and the verdicts as the specification states (#345).
    assert stubs["replay"].await_args.kwargs["attendance_step"] is not None


async def test_a_first_pass_reposts_its_own_round_and_not_the_division(tmp_path):
    """Sending every round through the division-wide rebuild would repost the whole
    championship at the end of every ordinary race weekend."""
    db_path = await _make_db(tmp_path, name="first_pass_round_only")
    state = _state(db_path, appeals=[_penalty()])

    stubs = await _run(finalize_appeals_review, state)

    stubs["repost"].assert_awaited_once()
    stubs["replay"].assert_not_awaited()


async def test_an_amendment_does_not_double_an_unamended_sessions_penalties(tmp_path):
    """**The worst defect any review of this change found.**

    `apply_penalties` walks whatever session types the staged set names, and stage one re-inserts
    only the *amended* session's driver rows — at zero. A report hydrated from an unamended
    session was therefore added on top of the milliseconds already standing in that session's
    row: amend the feature race, and the sprint race's 5 s penalty silently became 10 s, taking
    the driver down the sprint classification and costing them points they were never penalised.
    Each further amendment added another 5 s.

    Fixed by scoping the replay to the session being amended, which is what the stage driver now
    puts in `session_types_present`.
    """
    db_path = await _make_db(tmp_path, name="amend_other_session")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        other = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'SPRINT_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, postrace_time_penalties_ms) "
            "VALUES (?, 101, 3001, 1, 5000)",
            (other.lastrowid,),
        )
        await db.commit()

    # The staged set an amendment of the feature race produces: that session only.
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.session_types_present = [SessionType.FEATURE_RACE]

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.postrace_time_penalties_ms AS ms FROM race_session_results r "
            "JOIN session_results sr ON sr.id = r.session_result_id "
            "WHERE sr.session_type = 'SPRINT_RACE'"
        )
        assert (await cursor.fetchone())["ms"] == 5000


async def test_the_other_sessions_verdict_records_survive_an_amendment(tmp_path):
    """Clearing the whole round would drop them, and nothing would write them back.

    The replay only re-approves the amended session's reports, so a record belonging to another
    session has no route back into the database once deleted.
    """
    db_path = await _make_db(tmp_path, name="amend_other_records")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        other = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'SPRINT_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position) VALUES (?, 101, 3001, 1)",
            (other.lastrowid,),
        )
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) VALUES (?, 'TIME', 5, "
            "'Sprint contact', 'At fault', '77', '2026-02-02T00:00:00+00:00')",
            (cursor.lastrowid,),
        )
        await db.commit()

    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.session_types_present = [SessionType.FEATURE_RACE]

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM penalty_records WHERE description = 'Sprint contact'"
        )
        assert (await cursor.fetchone())["n"] == 1


async def test_the_deadline_is_cleared_before_the_rebuild_begins(tmp_path):
    """Or the sweep reverts the round from under a rebuild that is still posting (#345).

    A division-wide rebuild throttles a second between postings and renders graphics, so it can
    outlast the stage timeout. Approving the appeals is the commitment.
    """
    db_path = await _make_db(tmp_path, name="amend_deadline_cleared")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, "
            "created_at, pre_amendment_state, expires_at) "
            "VALUES (?, 700, '[\"FEATURE_RACE\"]', '2026-02-02T00:00:00+00:00', '{}', "
            "'2026-02-02T00:30:00+00:00')",
            (ROUND_ID,),
        )
        await db.commit()
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    seen: dict = {}

    async def _replay(*_a, **_kw):
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT expires_at FROM round_amend_channels WHERE round_id = ?", (ROUND_ID,)
            )
            row = await cursor.fetchone()
            seen["expires_at"] = row["expires_at"] if row else "gone"
        return ReplayOutcome([], frozenset({ROUND_ID}))

    with patch(
        "leaguebot.results.services.results_post_service.replay_division_channels",
        new=AsyncMock(side_effect=_replay),
    ):
        await _run(finalize_appeals_review, state, replay_override=True)

    assert seen["expires_at"] is None


# ---------------------------------------------------------------------------
# The amendment's stages publish nothing before the last, and run once (#345)
# ---------------------------------------------------------------------------


async def _deadline(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT expires_at FROM round_amend_channels")
        row = await cursor.fetchone()
        return row["expires_at"] if row else "gone"


async def _postrace_ms(db_path, driver: int = 101) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT postrace_time_penalties_ms AS ms FROM race_session_results "
            "WHERE driver_user_id = ?",
            (driver,),
        )
        return (await cursor.fetchone())["ms"]


async def test_the_amendments_report_stage_publishes_nothing(tmp_path):
    """**Nothing is published until the last stage.** It reposted the round as Post-Race Penalty
    Results and announced every penalty — which an amendment reverted afterwards left standing,
    the revert restoring the round and not the channels."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_quiet")
    state = _state(db_path, staged=[_penalty()], attendance_enabled=True)
    await _open_amendment(state)

    stubs = await _run(finalize_penalty_review, state)

    stubs["repost"].assert_not_awaited()
    stubs["subsequent"].assert_not_awaited()
    stubs["penalty_announce"].assert_not_awaited()
    stubs["record"].assert_not_awaited()
    assert await _round_status(db_path) == "AWAITING_REPORT_VERDICTS"


async def test_the_report_stage_hands_its_deadline_back(tmp_path):
    """The claim is released once the stage is done, or the sweep would never revert an
    amendment abandoned at the appeals.

    **The same deadline, not a fresh one** (decided 2026-09-21): the half hour covers both review
    stages from the moment stage one wrote, and a league is expected to arrive prepared."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_rearmed")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run(finalize_penalty_review, state)

    assert await _deadline(db_path) == _OPEN_DEADLINE


async def test_approving_the_report_stage_twice_applies_the_reports_once(tmp_path):
    """`apply_penalties` adds to the penalty columns, and stage one wrote them at zero. A second
    press cleared the records and applied every report again — on top of the milliseconds the
    first had already added — doubling the sanction with a single, correct-looking record."""
    db_path = await _make_db(tmp_path, name="amend_double_press")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)
    await _run_real_apply(finalize_penalty_review, state)

    assert await _verdict_count(db_path) == 1
    assert await _postrace_ms(db_path) == 5000


async def test_a_stage_of_an_amendment_no_longer_open_changes_nothing(tmp_path):
    """Lapsed, cancelled, or already being approved by another press: nothing is written."""
    db_path = await _make_db(tmp_path, name="amend_not_open")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    state.is_amendment = True  # no open amendment to claim
    interaction = _interaction()

    await _run_real_apply(finalize_penalty_review, state, interaction)

    assert await _verdict_count(db_path) == 0
    assert "no longer open" in str(interaction.followup.send.await_args.args[0])


async def test_a_report_stage_that_fails_part_way_is_undone(tmp_path):
    """The records are cleared before the reports are written back, so a failure between the two
    would otherwise leave the session carrying none of its decisions. The manager is told the
    plain kind of fault; the notice names its type, and neither names its message."""
    db_path = await _make_db(tmp_path, name="amend_stage_two_fails")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert, patch(
        "leaguebot.results.services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ) as close, patch(
        "leaguebot.results.services.penalty_service.apply_penalties",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        await _run_real_apply(finalize_penalty_review, state, interaction)

    # With the bot, so the standings put back settle a full tie by name.
    revert.assert_awaited_once_with(db_path, ROUND_ID, state.bot)
    close.assert_awaited_once()
    logged = _logged(state)
    assert "AMEND_FAILED" in logged
    assert "RuntimeError" in logged
    assert "disk full" not in logged
    reply = str(interaction.followup.send.await_args.args[0])
    assert "put back as it was" in reply
    assert "the bot hit an internal fault" in reply
    assert "disk full" not in reply
    assert state.appeals_prompt_message_id is None


async def test_a_failed_amendment_stage_says_to_re_run_results_rounds_amend(tmp_path):
    """The AMEND_FAILED notice, and the reply beside it, send the manager to the command they
    now type. The reply names the plain kind of fault before it."""
    db_path = await _make_db(tmp_path, name="amend_stage_fails_rerun")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ), patch(
        "leaguebot.results.services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ), patch(
        "leaguebot.results.services.penalty_service.apply_penalties",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        await _run_real_apply(finalize_penalty_review, state, interaction)

    notice = next(
        str(call.args[0])
        for call in state.bot.output_router.post_log.await_args_list
        if "AMEND_FAILED" in str(call.args[0])
    )
    assert notice.splitlines()[-1] == (
        "  The round was put back as it was. Re-run /results rounds amend to try again."
    )
    reply = str(interaction.followup.send.await_args.args[0])
    assert "the bot hit an internal fault" in reply
    assert reply.endswith("Re-run `/results rounds amend` to try again.")


async def test_a_kept_report_keeps_its_author_and_its_time(tmp_path):
    """**A verdict follows its driver with its justification, its author and its time.** The
    report stage writes the session's decisions out again, and stamping each with the admin who
    amended the round — at the moment they did — would lose the audit the amendment exists to
    keep."""
    db_path = await _make_db(tmp_path, name="amend_provenance")
    await _seed_driver_row(db_path)
    kept = _penalty()
    kept.decided_by = "4242"
    kept.decided_at = "2026-02-01T20:00:00"
    state = _state(db_path, staged=[kept])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT applied_by, applied_at FROM penalty_records")
        row = await cursor.fetchone()
    assert (row["applied_by"], row["applied_at"]) == ("4242", "2026-02-01T20:00:00")


async def test_a_fresh_report_is_stamped_in_utc(tmp_path):
    """A verdict with no time of its own is stamped now, timezone-aware, as every other
    timestamp the bot writes is — not with the naive, deprecated ``utcnow()`` (#160)."""
    db_path = await _make_db(tmp_path, name="fresh_report_utc")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT applied_at FROM penalty_records")
        row = await cursor.fetchone()
    assert datetime.fromisoformat(row["applied_at"]).utcoffset() == timedelta(0)


async def test_a_report_added_during_the_amendment_names_who_approved_it(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_new_report")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT applied_by FROM penalty_records")
        assert (await cursor.fetchone())["applied_by"] == str(STEWARD)


async def test_the_appeal_stage_of_an_amendment_no_longer_open_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_appeals_not_open")
    state = _state(db_path, appeals=[_penalty()])
    state.is_amendment = True
    interaction = _interaction()

    stubs = await _run(finalize_appeals_review, state, interaction)

    stubs["apply"].assert_not_awaited()
    stubs["replay"].assert_not_awaited()
    stubs["close"].assert_not_awaited()


async def test_a_committed_amendment_settles_a_full_tie_by_name(tmp_path):
    """The standings the rebuild stores are ordered as it posts them, full ties by name
    (decided 2026-09-15). The path this replaced recomputed with the names; stored by user id
    instead, the next round's movement arrows show a driver moving who did not."""
    db_path = await _make_db(tmp_path, name="amend_names")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    names = {101: "Alice"}

    with patch(
        "leaguebot.results.services.results_post_service.standings_display_names",
        new=AsyncMock(return_value=names),
    ):
        stubs = await _run(finalize_appeals_review, state)

    assert stubs["cascade_standings"].await_args.args[3] == names


async def test_a_committed_amendment_marks_the_drivers_who_raced(tmp_path):
    """Stage three settles the flag, and the round was already FINAL (#216)."""
    db_path = await _make_db(
        tmp_path,
        name="amend_former_marks",
        round_status="FINAL",
        results=[(31, 101, "CLASSIFIED"), (32, 102, "DNS")],
    )
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 0


async def test_a_committed_amendment_clears_a_driver_it_struck_out(tmp_path):
    """The half of #216 nothing could do before: taking a flag back down.

    Driver 32 was a former driver by this round, and the amendment left them out of it. With
    no other final round marking them their profile is deletable again — which is the whole
    reason the spec makes the flag two-way.
    """
    db_path = await _make_db(
        tmp_path,
        name="amend_former_clears",
        round_status="FINAL",
        results=[(31, 101, "CLASSIFIED")],
    )
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, former_driver) "
            "VALUES (32, '102', 'ASSIGNED', 1)"
        )
        await db.commit()
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state, snapshot='{"profiles_before": [31, 32]}')

    await _run(finalize_appeals_review, state)

    assert await _former(db_path, 31) == 1
    assert await _former(db_path, 32) == 0, "a driver struck out kept their flag"


async def test_a_committed_amendment_is_logged_as_result_amended(tmp_path):
    """The README tells a league to look for `RESULT_AMENDED`; the three-stage rebuild had
    stopped writing it anywhere, so a completed amendment left no record of itself."""
    db_path = await _make_db(tmp_path, name="amend_logged")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    logged = _logged(state)
    assert "RESULT_AMENDED | Success" in logged
    assert "sessions: FEATURE_RACE" in logged
    assert "APPEALS_REVIEW_APPROVED" not in logged


async def test_what_the_rebuild_could_not_post_is_named_in_result_amended(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_logged_faults")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["the standings channel refused"], frozenset({ROUND_ID}))),
    ):
        await _run(finalize_appeals_review, state, interaction, replay_override=True)

    logged = _logged(state)
    assert "RESULT_AMENDED | Incomplete" in logged
    assert "the standings channel refused" in logged
    assert "the standings channel refused" in _replied_text(interaction)


def _replied_text(interaction) -> str:
    return "\n".join(
        str(c.args[0]) for c in interaction.followup.send.await_args_list if c.args
    )


async def test_the_amendment_rewrites_the_rounds_pardons_at_its_last_stage(tmp_path):
    """A pardon removed in the report stage is removed from the round; one kept keeps the time
    it was granted. Written at the last stage, after the snapshot is released, because the revert
    restores the classification and not the attendance record."""
    db_path = await _make_db(tmp_path, name="amend_pardons", attendance_row=True)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'Old', '5', '2026-02-01T21:00:00')"
        )
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'NO_RSVP', 'Removed', '5', "
            "'2026-02-01T21:00:00')"
        )
        await db.commit()
    kept = StagedPardon(
        driver_user_id=101, driver_profile_id=31, attendance_id=41, pardon_type="ABSENT",
        justification="Old", grantor_id=5, granted_at="2026-02-01T21:00:00",
    )
    state = _state(db_path, pardons=[kept], attendance_enabled=True)
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT pardon_type, granted_at FROM attendance_pardons")
        rows = [tuple(r) for r in await cursor.fetchall()]
    assert rows == [("ABSENT", "2026-02-01T21:00:00")]


async def test_the_pardons_are_left_alone_with_attendance_off(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_pardons_off", attendance_row=True)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'Old', '5', '2026-02-01T21:00:00')"
        )
        await db.commit()
    state = _state(db_path, pardons=[], attendance_enabled=False)
    await _open_amendment(state)

    await _run(finalize_appeals_review, state)

    assert await _pardon_count(db_path) == 1


async def test_the_report_stage_leaves_the_pardons_to_the_last_stage(tmp_path):
    db_path = await _make_db(tmp_path, name="amend_pardons_wait", attendance_row=True)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (41, 'ABSENT', 'Old', '5', '2026-02-01T21:00:00')"
        )
        await db.commit()
    state = _state(db_path, staged=[_penalty()], pardons=[], attendance_enabled=True)
    await _open_amendment(state)

    await _run(finalize_penalty_review, state)

    assert await _pardon_count(db_path) == 1


async def test_a_kept_appeal_keeps_its_author_and_its_time(tmp_path):
    """Both rows an upheld appeal writes — its appeal record and the penalty row beside it."""
    db_path = await _make_db(tmp_path, name="amend_appeal_provenance")
    await _seed_driver_row(db_path)
    kept = _penalty()
    kept.decided_by = "4343"
    kept.decided_at = "2026-02-03T20:00:00"
    state = _state(db_path, appeals=[kept])
    await _open_amendment(state)

    await _run_real_apply(finalize_appeals_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT submitted_by, submitted_at FROM appeal_records")
        appeal = tuple(await cursor.fetchone())
        cursor = await db.execute("SELECT applied_by, applied_at FROM penalty_records")
        penalty = tuple(await cursor.fetchone())
    assert appeal == ("4343", "2026-02-03T20:00:00")
    assert penalty == ("4343", "2026-02-03T20:00:00")


async def test_a_fresh_appeal_is_stamped_in_utc(tmp_path):
    """An upheld appeal with no time of its own is stamped now, timezone-aware (#160)."""
    db_path = await _make_db(tmp_path, name="fresh_appeal_utc")
    await _seed_driver_row(db_path)
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    await _run_real_apply(finalize_appeals_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT submitted_at FROM appeal_records")
        row = await cursor.fetchone()
    assert datetime.fromisoformat(row["submitted_at"]).utcoffset() == timedelta(0)


async def test_an_amendment_whose_appeal_stage_cannot_open_is_undone(tmp_path):
    """There is no route to the last stage, so leaving it would strand the round until the sweep
    reverted it half an hour later with the manager told nothing. The manager is told the bot
    could not reach the amendment's channel."""
    db_path = await _make_db(tmp_path, name="amend_no_stage_three")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction(guild=False)  # no guild, so no channel to post the stage in

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert, patch(
        "leaguebot.results.services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ) as close:
        await _run(finalize_penalty_review, state, interaction)

    revert.assert_awaited_once()
    close.assert_awaited_once()
    assert "AMEND_FAILED" in _logged(state)
    assert (
        "the bot could not reach the amendment's channel to open the appeals stage"
        in str(interaction.followup.send.await_args.args[0])
    )
    # The deadline was never handed back, so nothing else could act on the amendment while it
    # was being undone — the claim is the caller's to hold until it is done with it.
    assert await _deadline(db_path) is None


async def test_a_rebuild_that_raises_still_closes_the_amendment(tmp_path):
    """**Nothing after the snapshot is released may leave the amendment half-closed** (#345).

    The snapshot and the deadline are gone by then, so the sweep cannot reach the row and
    `cancel_amendment` refuses it — and the cog's duplicate check would refuse the manager a
    second attempt for as long as it stood. The fault is reported and the channel closed.
    """
    db_path = await _make_db(tmp_path, name="amend_rebuild_raises")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.results_post_service.replay_division_channels",
        new=AsyncMock(side_effect=RuntimeError("gateway closed")),
    ):
        stubs = await _run(finalize_appeals_review, state, interaction, replay_override=True)

    stubs["close"].assert_awaited_once()
    logged = _logged(state)
    assert "RESULT_AMENDED | Incomplete" in logged
    assert "gateway closed" in logged


async def test_an_appeal_stage_that_raises_while_opening_is_undone_too(tmp_path):
    """Not only an unreachable channel: a send that fails, a prompt that will not render, a view
    that will not register. Any of them leaves the amendment with no route to its last stage,
    and the claim is still held — so it is undone here rather than left claimed for ever,
    invisible to the sweep and refused by Cancel."""
    db_path = await _make_db(tmp_path, name="amend_stage_three_raises")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.result_submission_service._post_appeals_prompt",
        new=AsyncMock(side_effect=RuntimeError("gateway closed")),
    ), patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(return_value=True),
    ) as revert, patch(
        "leaguebot.results.services.result_submission_service._close_amendment_channel", new=AsyncMock()
    ):
        await _run(finalize_penalty_review, state, interaction)

    revert.assert_awaited_once()
    notice = _failed_notice(state)
    assert "RuntimeError" in notice
    assert "gateway closed" not in notice
    assert await _deadline(db_path) is None


async def test_the_old_announcements_are_left_standing_where_nothing_replaced_them(tmp_path):
    """**A verdict deleted from a channel is in no channel at all.**

    The take-down follows the republish that replaced them, so a rebuild that never ran — for
    want of a guild, or because it raised — leaves them where they are. The results they belong
    to were not reposted either, so the league reads the round exactly as it did before, and the
    log says what could not be done (#345).
    """
    db_path = await _make_db(tmp_path, name="amend_takedown_skipped")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "leaguebot.results.services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state, _interaction(guild=False))

    taken_down.assert_not_awaited()


async def test_the_old_announcements_come_down_once_their_replacements_are_up(tmp_path):
    """Produce-then-destroy across the two stages: the rebuild announces the round's verdicts
    afresh, and only then are the announcements they replace removed."""
    db_path = await _make_db(tmp_path, name="amend_takedown_runs")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "leaguebot.results.services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state)

    taken_down.assert_awaited_once()


async def test_the_old_announcements_stay_where_the_verdicts_were_not_re_announced(tmp_path):
    """The rebuild reports a verdict that could not be announced as a fault line rather than
    raising, so the fault list alone cannot say whether the amended round was rebuilt — which
    is why the rebuild names the rounds it did rebuild (#345)."""
    db_path = await _make_db(tmp_path, name="amend_verdicts_failed")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "leaguebot.results.services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["the verdicts were not announced"], frozenset())),
    ), patch(
        "leaguebot.results.services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state, replay_override=True)

    taken_down.assert_not_awaited()


async def test_another_rounds_rebuild_does_not_license_the_take_down(tmp_path):
    """The amended round is the one whose old announcements are at stake; a later round being
    rebuilt says nothing about whether its replacements went up."""
    db_path = await _make_db(tmp_path, name="amend_other_round_rebuilt")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)

    with patch(
        "leaguebot.results.services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["round 3 failed"], frozenset({ROUND_ID + 1}))),
    ), patch(
        "leaguebot.results.services.result_submission_service.take_down_superseded_announcements",
        new=AsyncMock(return_value=[]),
    ) as taken_down:
        await _run(finalize_appeals_review, state, replay_override=True)

    taken_down.assert_not_awaited()


async def test_announcements_kept_for_want_of_a_replacement_are_named_with_links(tmp_path):
    """Their ids live only on the amendment's row, which closes with the channel — so where they
    are kept, the league is told which, with a way to each, or nothing could ever find them."""
    db_path = await _make_db(tmp_path, name="amend_kept_named")
    state = _state(db_path, appeals=[_penalty()])
    await _open_amendment(state)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE round_amend_channels SET superseded_announcements = ? WHERE round_id = ?",
            ('[{"anchor": 8101, "chunks": "[8101]", "channel_id": "6100", '
             '"driver_user_id": 101}]', ROUND_ID),
        )
        await db.commit()
    interaction = _interaction()
    interaction.guild.id = 555

    with patch(
        "leaguebot.results.services.results_post_service.replay_division_channels",
        new=AsyncMock(return_value=ReplayOutcome(["one verdict failed"], frozenset())),
    ):
        await _run(finalize_appeals_review, state, interaction, replay_override=True)

    logged = _logged(state)
    assert "left standing" in logged
    assert "https://discord.com/channels/555/6100/8101" in logged


async def test_the_report_stage_rewrites_every_amended_session_and_no_other(tmp_path):
    """**The reports of the sessions an amendment re-entered are reviewed together** (#345,
    decided 2026-09-21), and approving rewrites exactly those — while a session it left alone
    keeps its records, which were never shown and so could not be written back."""
    db_path = await _make_db(tmp_path, name="amend_many_reports")
    await _seed_driver_row(db_path)
    async with get_connection(db_path) as db:
        rows = {}
        for session_type in ("FEATURE_QUALIFYING", "SPRINT_RACE"):
            session = await db.execute(
                "INSERT INTO session_results (round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, 'ACTIVE')",
                (ROUND_ID, DIVISION_ID, session_type),
            )
            table = (
                "qualifying_session_results" if session_type.endswith("QUALIFYING")
                else "race_session_results"
            )
            cursor = await db.execute(
                f"INSERT INTO {table} (session_result_id, driver_user_id, team_instance_id, "
                "finishing_position) VALUES (?, 101, 3001, 1)",
                (session.lastrowid,),
            )
            rows[session_type] = cursor.lastrowid
        await db.execute(
            "INSERT INTO penalty_records (qual_result_id, penalty_type, description, "
            "justification, applied_by, applied_at) VALUES (?, 'DSQ', 'Quali', 'Old', '7', "
            "'2026-02-02T00:00:00')",
            (rows["FEATURE_QUALIFYING"],),
        )
        await db.execute(
            "INSERT INTO penalty_records (race_result_id, penalty_type, time_seconds, "
            "description, justification, applied_by, applied_at) VALUES (?, 'TIME', 5, "
            "'Sprint', 'Untouched', '7', '2026-02-02T00:00:00')",
            (rows["SPRINT_RACE"],),
        )
        await db.commit()

    # The feature qualifying and race are amended; their review holds one race report and
    # drops the qualifying one. The sprint race is not part of the amendment.
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    state.session_types_present = [SessionType.FEATURE_QUALIFYING, SessionType.FEATURE_RACE]

    await _run_real_apply(finalize_penalty_review, state)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT description FROM penalty_records ORDER BY id")
        assert [r[0] for r in await cursor.fetchall()] == ["Sprint", "Corner cutting"]


# ---------------------------------------------------------------------------
# A failed stage names the kind of fault, and every stage refusal is recorded (#442)
# ---------------------------------------------------------------------------

PLAIN_DATABASE = "the bot could not read or write its database"
PLAIN_INTERNAL = "the bot hit an internal fault"
RE_RUN = "Re-run `/results rounds amend` to try again."


def _undone():
    """The revert and the channel's close, both succeeding."""
    return (
        patch(
            "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "leaguebot.results.services.result_submission_service._close_amendment_channel",
            new=AsyncMock(),
        ),
    )


def _failed_notice(state) -> str:
    return next(
        str(c.args[0])
        for c in state.bot.output_router.post_log.await_args_list
        if "AMEND_FAILED" in str(c.args[0])
    )


async def test_a_failed_amendment_report_stage_names_the_kind_of_fault(tmp_path):
    """The report stage stops on a database fault: the manager is told it is the bot's, the
    plain kind, that the round was put back, and to re-run; the notice names the type alone."""
    import sqlite3

    db_path = await _make_db(tmp_path, name="amend_stage_two_kind")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()
    revert, close = _undone()

    with revert, close, patch(
        "leaguebot.results.services.penalty_service.apply_penalties",
        new=AsyncMock(side_effect=sqlite3.OperationalError("database is locked")),
    ):
        await _run_real_apply(finalize_penalty_review, state, interaction)

    reply = str(interaction.followup.send.await_args.args[0])
    assert "stopped on a fault in the bot, not on anything you entered" in reply
    assert PLAIN_DATABASE in reply
    assert "put back as it was" in reply
    assert reply.endswith(RE_RUN)
    assert "database is locked" not in reply
    notice = _failed_notice(state)
    assert "OperationalError" in notice
    assert "database is locked" not in notice


@pytest.mark.parametrize(
    "case", ["opening-raises", "channel-unreachable", "the-appeals-stage-fails"]
)
async def test_a_failed_amendment_appeals_stage_names_the_kind_of_fault(tmp_path, case):
    """The appeals stage cannot be opened — on a fault, or because its channel cannot be
    reached — or fails once approved. Each tells the manager, in plain words, what kind of
    fault it was and that the round was put back; the notice names no exception message."""
    db_path = await _make_db(tmp_path, name=f"amend_appeals_{case.replace('-', '_')}")
    if case == "the-appeals-stage-fails":
        state = _state(db_path, appeals=[_penalty()])
        stage = finalize_appeals_review
        fault = patch(
            "leaguebot.results.services.result_submission_service._apply_staged_appeals",
            new=AsyncMock(side_effect=RuntimeError("disk full")),
        )
    else:
        state = _state(db_path, staged=[_penalty()])
        stage = finalize_penalty_review
        fault = patch(
            "leaguebot.results.services.result_submission_service._post_appeals_prompt",
            new=AsyncMock(
                side_effect=RuntimeError("render failed") if case == "opening-raises" else None,
                return_value=False,
            ),
        )
    await _open_amendment(state)
    interaction = _interaction()
    revert, close = _undone()

    with revert, close, fault:
        await _run(stage, state, interaction)

    reply = str(interaction.followup.send.await_args.args[0])
    assert "stopped on a fault in the bot, not on anything you entered" in reply
    if case == "channel-unreachable":
        assert "the bot could not reach the amendment's channel to open the appeals stage" in reply
    else:
        assert PLAIN_INTERNAL in reply
    assert "put back as it was" in reply
    assert reply.endswith(RE_RUN)
    notice = _failed_notice(state)
    assert "render failed" not in notice and "disk full" not in notice
    assert "render failed" not in reply and "disk full" not in reply


@pytest.mark.parametrize(
    "where",
    [
        pytest.param(where, id=where.replace(" ", "-"))
        for where in ("reply", "log line")
    ],
)
async def test_an_amendment_stage_not_yet_put_back_says_to_run_it_again_once_it_has_been(
    tmp_path, where
):
    """A report stage fails and the round cannot be put back straight away: the amendment is
    left for the sweep, which retries within minutes, and a re-run is refused until then. So
    the reply ends on running the command again once the round has been put back, and so does
    the `AMEND_FAILED` line, for whoever reads the log rather than the reply."""
    db_path = await _make_db(tmp_path, name=f"amend_stage_not_put_back_{where.replace(' ', '_')}")
    state = _state(db_path, staged=[_penalty()])
    await _open_amendment(state)
    interaction = _interaction()

    with patch(
        "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
        new=AsyncMock(side_effect=RuntimeError("still locked")),
    ), patch(
        "leaguebot.results.services.result_submission_service._close_amendment_channel",
        new=AsyncMock(),
    ), patch(
        "leaguebot.results.services.penalty_service.apply_penalties",
        new=AsyncMock(side_effect=RuntimeError("disk full")),
    ):
        await _run_real_apply(finalize_penalty_review, state, interaction)

    once_put_back = "Run `/results rounds amend` again once it has been put back."
    if where == "reply":
        reply = str(interaction.followup.send.await_args.args[0])
        assert reply.endswith(once_put_back)
        return
    [notice] = [
        str(c.args[0])
        for c in state.bot.output_router.post_log.await_args_list
        if "AMEND_FAILED" in str(c.args[0])
    ]
    assert f"<@{STEWARD}>" in notice.splitlines()[0]
    assert "RuntimeError" in notice
    assert "disk full" not in notice and "still locked" not in notice
    # In code or in plain text, as the line's other steps are: the words are what is pinned.
    last = notice.splitlines()[-1]
    assert last.startswith("  ")
    assert last.replace("`", "").endswith(once_put_back.replace("`", ""))


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            "reports-already-approved",
        ),
        pytest.param(
            "report-stage-not-open",
        ),
        pytest.param(
            "appeals-stage-not-open",
        ),
        "a-first-pass-refusal",
    ],
)
async def test_every_results_rounds_amend_refusal_reaches_the_log_channel(tmp_path, case):
    """The stages of an amendment refuse a press they cannot act on, and each refusal writes
    one line in the standard refusal form, naming `/results rounds amend` or the stage it
    refused. The ordinary first-pass review's refusals are no part of the amendment: each is
    recorded as the review's own Approve button."""
    first_pass = case == "a-first-pass-refusal"
    db_path = await _make_db(
        tmp_path, name=f"amend_stage_refused_{case.replace('-', '_')}",
        resubmitting=1 if first_pass else 0,
    )
    state = _state(db_path, staged=[_penalty()], appeals=[_penalty()])
    if case == "reports-already-approved":
        await _open_amendment(state)
        state.reports_approved = True
    elif not first_pass:
        state.is_amendment = True  # no open amendment to claim
    interaction = _interaction()
    interaction.client = state.bot
    interaction.user.display_name = "Steward"
    interaction.command = None
    stage = finalize_appeals_review if case == "appeals-stage-not-open" else finalize_penalty_review

    await _run(stage, state, interaction)

    lines = _logged(state)
    if first_pass:
        # The first pass's own refusal, recorded as the review's Approve button and not as the
        # amendment (#482).
        assert lines.startswith("⛔ the “✅ Approve” button of the penalty review of round 3")
        assert "/results rounds amend" not in lines
        return
    [line] = [str(c.args[0]) for c in state.bot.output_router.post_log.await_args_list]
    assert line.startswith("⛔ ")
    assert f"refused for Steward (<@{STEWARD}>)" in line
    what = line.split(" refused for ", 1)[0]
    assert "/results rounds amend" in what or "stage" in what.lower(), (
        "the line does not name what was refused"
    )


# ---------------------------------------------------------------------------
# Who approved (#482): each line an approval writes names the member who pressed, by
# display name and mention, and an approval writes one line of its own, not two.
# ---------------------------------------------------------------------------

_ALEX = f"Alex (<@{STEWARD}>)"


def _line_under(state, heading: str) -> str:
    """The one line the log channel was given under *heading*."""
    lines = [
        str(c.args[0]) for c in state.bot.output_router.post_log.await_args_list
        if heading in str(c.args[0])
    ]
    assert len(lines) == 1, lines
    return lines[0]


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


async def _amendment_stage(tmp_path, token: str):
    """Alex takes round 3 (Pro)'s amendment through one stage with one report staged: the report
    stage approved, the appeals stage approved, or the report stage failing part-way."""
    db_path = await _make_db(tmp_path, name=f"amend_named_{token}")
    await _seed_driver_row(db_path)
    state = _state(db_path, staged=[_penalty()], appeals=[_penalty()])
    await _open_amendment(state)
    if token == "AMEND_STAGE_2":
        await _run_real_apply(finalize_penalty_review, state)
    elif token == "RESULT_AMENDED":
        await _run(finalize_appeals_review, state)
    else:
        with patch(
            "leaguebot.results.services.result_submission_service.revert_abandoned_amendment",
            new=AsyncMock(return_value=True),
        ), patch(
            "leaguebot.results.services.result_submission_service._close_amendment_channel",
            new=AsyncMock(),
        ), patch(
            "leaguebot.results.services.penalty_service.apply_penalties",
            new=AsyncMock(side_effect=RuntimeError("disk full")),
        ):
            await _run_real_apply(finalize_penalty_review, state)
    return state


@pytest.mark.parametrize(
    "token, outcome",
    [
        ("AMEND_STAGE_2", "Recorded"),
        ("RESULT_AMENDED", "Success"),
        ("AMEND_FAILED", "Notice"),
    ],
)
async def test_each_stage_of_an_amendment_names_the_member_who_pressed(tmp_path, token, outcome):
    """Alex approves the report stage of round 3 (Pro)'s amendment, approves its appeals stage,
    or sees its report stage fail part-way: the line each writes reads
    "Alex (<@77>) | <TOKEN> | <outcome>"."""
    state = await _amendment_stage(tmp_path, token)

    assert _line_under(state, token).startswith(f"{_ALEX} | {token} | {outcome}")


def _correction(driver: int = 102, seconds: int = 10) -> StagedPenalty:
    """An upheld appeal's correction: *seconds* added to *driver*'s Feature Race time."""
    return StagedPenalty(
        driver_user_id=driver,
        session_type=SessionType.FEATURE_RACE,
        penalty_type="TIME",
        penalty_seconds=seconds,
        description="Track limits",
        justification="Appeal upheld, lap 7",
    )


@pytest.mark.parametrize(
    "token, carries",
    [
        pytest.param("AMEND_STAGE_2", "applied: +5s for <@101> in FEATURE_RACE", id="amend-reports"),
        pytest.param(
            "RESULT_AMENDED", "appeals applied: +10s for <@102> in FEATURE_RACE", id="amend-appeals"
        ),
        pytest.param(
            "APPEALS_REVIEW_APPROVED", "applied: +10s for <@102> in FEATURE_RACE", id="appeals"
        ),
    ],
)
async def test_amend_stage_two_and_appeals_lines_carry_what_was_applied(tmp_path, token, carries):
    """With `apply_penalties` writing no line of its own, the line of the stage that applied
    the verdicts says what was applied. Alex approves the report stage of round 3 (Pro)'s
    amendment with +5s for driver 101 in the Feature Race staged, then its appeals stage with a
    +10s correction for driver 102; or approves the appeals of round 3 (Pro), not an amendment,
    with that correction staged. Each apply runs for real, and the stage's one line names what
    it applied: the penalty or correction, the driver and the session."""
    if token == "APPEALS_REVIEW_APPROVED":
        db_path = await _make_db(
            tmp_path, name="applied_appeals", round_status="AWAITING_APPEAL_VERDICTS"
        )
        await _seed_driver_row(db_path, 102)
        state = _state(db_path, appeals=[_correction()])
        await _run_real_apply(finalize_appeals_review, state)
    else:
        db_path = await _make_db(tmp_path, name=f"applied_{token}")
        await _seed_driver_row(db_path)
        await _seed_driver_row(db_path, 102)
        state = _state(db_path, staged=[_penalty()], appeals=[_correction()])
        await _open_amendment(state)
        await _run_real_apply(finalize_penalty_review, state)
        if token == "RESULT_AMENDED":
            await _run_real_apply(finalize_appeals_review, state)

    assert carries in _line_under(state, token)
