"""Integration tests for round lifecycle: results in → report verdicts → appeal verdicts.

Tests cover:
- result_status transitions in the DB
- Zero-staged-penalties still advances to AWAITING_APPEAL_VERDICTS (FR-009)
- Zero-staged-corrections still advances to FINAL (FR-010)
- channel-close only at FINAL (round_submission_channels.closed = 1), in the appeals approval's
  save
- penalty_records and appeal_records rows created when staged lists are non-empty
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from tests.support.teams import seed_team_instances


# ---------------------------------------------------------------------------
# Shared bootstrap helpers
# ---------------------------------------------------------------------------


async def _bootstrap(db_path: str) -> tuple[int, int, int]:
    """Create server → season → division → round. Returns (season_id, division_id, round_id)."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs "
            "(server_id, interaction_role_id, interaction_channel_id, log_channel_id) "
            "VALUES (1, 10, 20, 30)"
        )
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-01-01', 'ACTIVE', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, forecast_channel_id) "
            "VALUES (?, 'Main', 777, 888)",
            (season_id,),
        )
        division_id = cursor.lastrowid
        await seed_team_instances(db, division_id, 100, 200)
        cursor = await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, status, scheduled_at) "
            "VALUES (?, 1, 'STANDARD', 'AWAITING_RESULTS', '2026-01-01T18:00:00')",
            (division_id,),
        )
        round_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO division_results_config (division_id) VALUES (?)",
            (division_id,),
        )
        # Points config
        for pos, pts in [(1, 25), (2, 18)]:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, 'STD', 'FEATURE_RACE', ?, ?)",
                (season_id, pos, pts),
            )
        await db.commit()
    return season_id, division_id, round_id


async def _insert_submission_channel(db_path: str, round_id: int, channel_id: int = 555) -> None:
    """Mark a round as in_penalty_review in round_submission_channels.

    The round goes to AWAITING_REPORT_VERDICTS with it, as the review's opening save moves it:
    a penalty review is approved only while its round awaits one (#402).
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO round_submission_channels "
            "(round_id, channel_id, created_at, in_penalty_review, closed) "
            "VALUES (?, ?, '2026-01-01T18:00:00', 1, 0)",
            (round_id, channel_id),
        )
        await db.execute(
            "UPDATE rounds SET status = 'AWAITING_REPORT_VERDICTS' WHERE id = ?", (round_id,)
        )
        await db.commit()


# ---------------------------------------------------------------------------
# The two approvals, on the change queue (#439)
#
# Approving a round's reports and its appeals are changes on the queue
# (`results.reports.approve`, `results.appeals.approve`), asked as the review's Approve controls
# ask them and carried out by running the queue. These run on the review league of
# `tests.support.review_league`: round 3 of division 11 (Pro), Lewis (101) and Max (102) in its
# Feature Race, its submission channel open, and round 4 already final.
# ---------------------------------------------------------------------------

NOT_BUILT = "#439: a round's approvals are not yet changes on the queue"
APPEALS_PROMPT = 8902


async def _appeals_league(tmp_path, **options):
    """Round 3 awaiting its appeal verdicts, its appeals prompt standing."""
    from tests.support.review_league import SUBMISSION_CHANNEL, review_league

    league = await review_league(
        tmp_path, round_status="AWAITING_APPEAL_VERDICTS", other_division=False,
        appeals_prompt=APPEALS_PROMPT, **options,
    )
    league.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")
    return league


async def _approve_reports(league, staged=()) -> None:
    """A league manager presses Approve on round 3's penalty review with *staged*, and the queue
    runs."""
    from tests.support.change_queue import member_interaction, run_queue, tier_member
    from tests.support.review_league import DIVISION_ID, PROMPT, ROUND_ID

    await league.bot.change_queue.ask(
        "results.reports.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "staged": [item.to_payload() for item in staged], "pardons": [],
            "prompt_message_id": PROMPT, "approval_message_id": None,
        },
        interaction=member_interaction(league.bot, user=tier_member("manager")),
        what="✅ Approve on round 3's penalty review",
    )
    await run_queue(league.bot)


async def _approve_appeals(league, staged=()) -> None:
    """A league manager presses Approve on round 3's appeals review, the one the channel
    records, with *staged*; the queue is not yet run."""
    from tests.support.change_queue import member_interaction, tier_member
    from tests.support.review_league import DIVISION_ID, ROUND_ID, one

    prompt = await one(
        league.db_path,
        "SELECT appeals_prompt_message_id FROM round_submission_channels WHERE round_id = ?",
        ROUND_ID,
    )
    await league.bot.change_queue.ask(
        "results.appeals.approve",
        {
            "round_id": ROUND_ID, "division_id": DIVISION_ID,
            "staged": [item.to_payload() for item in staged],
            "appeals_prompt_message_id": prompt,
        },
        interaction=member_interaction(league.bot, user=tier_member("manager")),
        what="✅ Approve on round 3's appeals review",
    )


async def _closed(league) -> bool:
    from tests.support.review_league import ROUND_ID, one

    return bool(await one(
        league.db_path, "SELECT closed FROM round_submission_channels WHERE round_id = ?",
        ROUND_ID,
    ))


async def test_zero_penalties_advances_to_post_race_penalty(tmp_path):
    """Nothing staged: the round advances to AWAITING_APPEAL_VERDICTS (FR-009), channel open."""
    from tests.support.review_league import review_league, round_status, stopped_at

    league = await review_league(tmp_path, other_division=False)
    await _approve_reports(league)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert not await _closed(league)


async def test_zero_corrections_advances_to_final(tmp_path):
    """Nothing staged: the round becomes FINAL (FR-010), its channel row closed."""
    from tests.support.change_queue import run_queue
    from tests.support.review_league import round_status, stopped_at

    league = await _appeals_league(tmp_path)
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "FINAL"
    assert await _closed(league)


# ---------------------------------------------------------------------------
# A review pressed after the round has ended — issue #167
# ---------------------------------------------------------------------------


async def test_a_penalty_review_cannot_reopen_a_round_that_has_ended(tmp_path):
    """Switching the results module off closes every round still awaiting a review.

    The review view lives in the submission channel and outlives the round: the channel is
    deleted, but a client already holding the message can still press the button. Unguarded,
    that write dragged the closed round back to AWAITING_APPEAL_VERDICTS — its division would
    un-finish, `/season complete` would refuse again, and the league would be back in the dead
    end the disable had just got it out of. The approval is refused at its check; nothing is
    applied.
    """
    from tests.support.review_league import (
        LEWIS, penalty, penalty_records, review_league, round_status,
    )

    league = await review_league(tmp_path, round_status="FINAL", other_division=False)
    await _approve_reports(league, [penalty(LEWIS)])

    assert await round_status(league.db_path) == "FINAL"
    assert await penalty_records(league.db_path) == []


async def test_an_appeals_review_cannot_reopen_a_cancelled_round(tmp_path):
    """The same guard the other side of it: a cancelled round must not become FINAL."""
    from tests.support.change_queue import run_queue
    from tests.support.review_league import SUBMISSION_CHANNEL, review_league, round_status

    league = await review_league(
        tmp_path, round_status="CANCELLED", other_division=False, appeals_prompt=APPEALS_PROMPT,
    )
    league.channel(SUBMISSION_CHANNEL).seed(APPEALS_PROMPT, "appeals review")
    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await round_status(league.db_path) == "CANCELLED"


async def test_approving_the_last_rounds_appeals_finishes_the_division(tmp_path):
    """The end-to-end link that lets a season be completed at all (issue #154).

    Approving a round's appeals is the only place a round becomes FINAL, and so the only place a
    division can finish by racing. Until this was wired up, `/season complete` gated on a column
    nothing wrote and no season could ever be completed. This starts from the division a league
    actually has — ACTIVE, its last round in appeals and every other round final — and asserts
    the whole chain.
    """
    from tests.support.change_queue import run_queue
    from tests.support.review_league import DIVISION_ID, one, round_status, stopped_at

    league = await _appeals_league(tmp_path)
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE divisions SET status = 'ACTIVE' WHERE id = ?", (DIVISION_ID,))
        await db.commit()

    await _approve_appeals(league)
    await run_queue(league.bot)

    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "FINAL"
    assert await one(
        league.db_path, "SELECT status FROM divisions WHERE id = ?", DIVISION_ID,
    ) == "FINISHED"

    # and with its only division finished, the season is now completable
    from leaguebot.core.services.season_service import SeasonService
    assert await SeasonService(league.db_path).all_divisions_finished() is True


# ---------------------------------------------------------------------------
# The full lifecycle: awaiting report verdicts → awaiting appeal verdicts → final
# ---------------------------------------------------------------------------


async def test_full_lifecycle_states(tmp_path):
    """Walk the lifecycle from results-in to final, verifying each transition: the round is
    awaiting report verdicts, then appeal verdicts once its reports are approved, then final once
    its appeals are."""
    from tests.support.change_queue import run_queue
    from tests.support.review_league import review_league, round_status, stopped_at

    league = await review_league(tmp_path, other_division=False)
    assert await round_status(league.db_path) == "AWAITING_REPORT_VERDICTS"

    await _approve_reports(league)
    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "AWAITING_APPEAL_VERDICTS"
    assert not await _closed(league)

    await _approve_appeals(league)
    await run_queue(league.bot)
    assert await stopped_at(league) is None
    assert await round_status(league.db_path) == "FINAL"
    assert await _closed(league)


# ---------------------------------------------------------------------------
# penalty_records rows created when staged penalties are non-empty
# ---------------------------------------------------------------------------


async def test_penalty_records_inserted_when_staged(tmp_path):
    """A 5-second penalty staged for Lewis produces one penalty record, on his race result."""
    from tests.support.review_league import LEWIS, one, penalty, review_league, stopped_at

    league = await review_league(tmp_path, other_division=False)
    race_result_id = await one(
        league.db_path, "SELECT id FROM race_session_results WHERE driver_user_id = ?", LEWIS,
    )
    await _approve_reports(league, [penalty(LEWIS)])

    assert await stopped_at(league) is None
    assert await one(
        league.db_path, "SELECT COUNT(*) FROM penalty_records WHERE race_result_id = ?",
        race_result_id,
    ) == 1


# ---------------------------------------------------------------------------
# The channel row closes in the appeals approval's save, and not before
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NOT_BUILT)
async def test_the_channel_row_is_closed_in_the_appeals_approval_s_save(tmp_path):
    """The reports approved, the submission channel's row stays open while the appeals review is
    in progress. The appeals approved, the row is closed by its `apply` save, before the channel
    itself is deleted (defect 6)."""
    from tests.support.review_league import (
        SUBMISSION_CHANNEL, review_league, run_until_done, stopped_at,
    )

    league = await review_league(tmp_path, other_division=False)
    await _approve_reports(league)
    assert await stopped_at(league) is None
    assert not await _closed(league)

    await _approve_appeals(league)
    await run_until_done(league, "apply")

    assert await _closed(league)
    assert ("delete_channel", SUBMISSION_CHANNEL, SUBMISSION_CHANNEL) not in league.events


# ---------------------------------------------------------------------------
# T028-6: result_status_check helper — is_channel_in_penalty_review
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_is_channel_in_penalty_review_false_after_final(tmp_path):
    """Once result_status = FINAL, is_channel_in_penalty_review must return False."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_submission_channel(db_path, round_id, channel_id=777)

    # Advance to FINAL
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'FINAL' WHERE id = ?", (round_id,)
        )
        await db.commit()

    from leaguebot.results.services.result_submission_service import is_channel_in_penalty_review
    assert not await is_channel_in_penalty_review(db_path, 777)


@pytest.mark.asyncio
async def test_is_channel_in_penalty_review_true_at_post_race_penalty(tmp_path):
    """is_channel_in_penalty_review returns True when result_status = POST_RACE_PENALTY."""
    db_path = str(tmp_path / "test.db")
    await run_migrations(db_path)
    _, division_id, round_id = await _bootstrap(db_path)
    await _insert_submission_channel(db_path, round_id, channel_id=888)

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'AWAITING_APPEAL_VERDICTS' WHERE id = ?", (round_id,)
        )
        await db.commit()

    from leaguebot.results.services.result_submission_service import is_channel_in_penalty_review
    assert await is_channel_in_penalty_review(db_path, 888)
