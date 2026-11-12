"""review_prompts.py — The penalty review's prompts and the appeals records: the prompt
posted for the review, the appeals prompt, and the appeal records inserted and applied."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import aiosqlite
import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.seasons import season_service
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.core.services.posting.owned_channel_handler import post_in_owned_channel
from leaguebot.results.models.points_config import SessionType
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.services.points.scoring import _recompute_session_points_on

if TYPE_CHECKING:
    from leaguebot.core.services.posting.output_router import OutputRouter
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview
    from leaguebot.results.services.review.review_state import PenaltyReviewState, ReadServerConfig


async def is_channel_in_penalty_review(db_path: str, channel_id: int) -> bool:
    """Return True if *channel_id* belongs to an open submission channel in
    penalty-review state (all sessions submitted/cancelled, round not yet finalized).

    A channel resubmitting is not: the round is still in review, but the manager is pasting
    its results again and the guard would delete every paste.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT round_id
            FROM round_submission_channels
            WHERE channel_id = ?
              AND closed = 0
              AND in_penalty_review = 1
              AND resubmitting = 0
            """,
            (channel_id,),
        )
        round_ids = [row["round_id"] for row in await cursor.fetchall()]
        for round_id in round_ids:
            facts = await season_service.round_facts_on(db, round_id)
            if facts is not None and facts.status != RoundStatus.FINAL:
                return True
    return False


async def _build_penalty_review_state(
    bot: LeagueBot,
    queue: ChangeQueue,
    server_config: ReadServerConfig,
    router: OutputRouter,
    attendance: AttendanceAfterReview,
    round_id: int,
    division_id: int,
    submission_channel_id: int,
) -> "PenaltyReviewState":
    """Reconstruct a :class:`~leaguebot.results.services.review.review_state.PenaltyReviewState` from the DB.

    Used by the changes that put a review's prompt up: the review-open and verdict changes
    (`review_open_change`, `review_verdicts`), which a restart also asks to put a review back,
    the appeals phase's included (``status = 'AWAITING_APPEAL_VERDICTS'``).
    """
    from leaguebot.results.services.review.review_state import PenaltyReviewState

    db_path: str = bot.db_path

    async with get_connection(db_path) as db:
        facts = await season_service.round_facts_on(db, round_id)

        sr_cursor = await db.execute(
            "SELECT session_type FROM session_results WHERE round_id = ? AND status = 'ACTIVE'",
            (round_id,),
        )
        sr_rows = await sr_cursor.fetchall()

    round_number: int = facts.round_number if facts else 0
    division_name: str = facts.division_name if facts else ""

    _stype_order = list(SessionType)
    session_types_present = sorted(
        [SessionType(r["session_type"]) for r in sr_rows],
        key=lambda s: _stype_order.index(s),
    )

    return PenaltyReviewState(
        round_id=round_id,
        division_id=division_id,
        submission_channel_id=submission_channel_id,
        session_types_present=session_types_present,
        db_path=db_path,
        bot=bot,
        queue=queue,
        server_config=server_config,
        router=router,
        attendance=attendance,
        round_number=round_number,
        division_name=division_name,
    )


@posts_in_a_job
async def send_review_prompt(
    bot: LeagueBot, sub_channel: discord.TextChannel, state: "PenaltyReviewState"
) -> int:
    """Post the penalty review prompt of *state* in *sub_channel*, notifying nobody, and register
    its view, so that its buttons keep working across a restart. Gives the message's id; *state*
    learns it too.

    The one place the prompt is sent, which the review's `post_review_prompt` job calls (#439).
    """
    from leaguebot.results.services.review.penalty_review_views import (
        PenaltyReviewView,
        _render_prompt_content,
    )

    view = PenaltyReviewView(state=state)
    content = await _render_prompt_content(state)
    message_id = await post_in_owned_channel(
        sub_channel, content, mentions=discord.AllowedMentions.none(), view=view
    )
    state.prompt_message_id = message_id
    bot.add_view(view, message_id=message_id)
    return message_id


@posts_in_a_job
async def send_appeals_prompt(
    bot: LeagueBot, sub_channel: discord.TextChannel, state: "PenaltyReviewState"
) -> int:
    """Post the appeals review prompt of *state* in *sub_channel*, notifying nobody, and register
    its view, so that its buttons keep working across a restart. Gives the message's id; *state*
    learns it too.

    The one place the appeals prompt is sent, which `_post_appeals_prompt` and the queue's
    `post_appeals_prompt` job both call (#439).
    """
    from leaguebot.results.services.review.appeals_review_views import (
        AppealsReviewView,
        _render_appeals_prompt_content,
    )

    appeals_view = AppealsReviewView(state=state)
    content = await _render_appeals_prompt_content(state)
    message_id = await post_in_owned_channel(
        sub_channel, content, mentions=discord.AllowedMentions.none(), view=appeals_view
    )
    state.appeals_prompt_message_id = message_id
    bot.add_view(appeals_view, message_id=message_id)
    return message_id


async def _insert_appeal_records_on(
    db: aiosqlite.Connection,
    staged_appeals: list,
    applied_pr: list[dict],
    actor_id: int,
    *,
    now: datetime,
) -> list[dict]:
    """Write one ``appeal_records`` row per upheld correction on *db*, committing nothing.

    *applied_pr* are the penalty records the corrections were applied as, in the order the
    corrections were staged. Each appeal keeps its own author and time where it was read back
    from the round (#345), and is given *now* otherwise. Returns the records written, shaped as
    the verdict announcement reads them.
    """
    records: list[dict] = []
    now_str = now.isoformat()
    for sp, pr in zip(staged_appeals, applied_pr):
        race_result_id = pr.get("race_result_id")
        qual_result_id = pr.get("qual_result_id")
        cursor = await db.execute(
            """
            INSERT INTO appeal_records (
                race_result_id, qual_result_id,
                status, penalty_type, time_seconds,
                description, justification, submitted_by, submitted_at,
                announcement_channel_id
            ) VALUES (?, ?, 'UPHELD', ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                race_result_id,
                qual_result_id,
                sp.penalty_type,
                sp.penalty_seconds,
                sp.description,
                sp.justification,
                # An appeal read back from the round keeps its own author and time (#345).
                sp.decided_by or str(actor_id),
                sp.decided_at or now_str,
            ),
        )
        records.append(
            {
                "id": cursor.lastrowid,
                "race_result_id": race_result_id,
                "qual_result_id": qual_result_id,
                "driver_user_id": sp.driver_user_id,
                "penalty_type": sp.penalty_type,
                "time_seconds": sp.penalty_seconds,
                "description": sp.description,
                "justification": sp.justification,
                "submitted_by": sp.decided_by or str(actor_id),
                "announcement_channel_id": None,
            }
        )
    return records


async def _apply_staged_appeals_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    staged_appeals: list,
    actor_id: int,
    *,
    now: datetime,
) -> list[dict]:
    """Apply a round's upheld appeal corrections on *db* and record each as an appeal record.

    What `_apply_staged_appeals` does, written on the connection it is handed and committing
    nothing, so the corrections, the points they move and the appeal records are one save with
    the rest of the stage. The points are recalculated by `_recompute_session_points_on`, which
    **raises** on a session it cannot score: the save is rolled back and the change stops,
    where the old form logged the failure and published the old points (defect 3).

    Shared by the first pass and the amendment's appeal stage. Returns the appeal records
    written, shaped as the verdict announcement reads them, in the order the corrections were
    staged.
    """
    from leaguebot.results.services.review import penalty_service as _ps

    if not staged_appeals:
        return []

    applied_pr = await _ps.apply_penalties_on(
        db, round_id, division_id, staged_appeals, actor_id, now=now, _phase="APPEAL",
    )
    await _recompute_session_points_on(db, round_id)
    return await _insert_appeal_records_on(db, staged_appeals, applied_pr, actor_id, now=now)


async def _snapshot_staged_drivers_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    staged,
) -> list[dict]:
    """Return current ``finishing_position``, ``post_race_time_penalties``, and
    ``total_points`` for every driver referenced in *staged*, read on *db*. Used for audit log.

    Read on the connection it is handed, so a change's one save can take the picture before and
    after what it writes (#439).
    """
    from leaguebot.results.services.standings.standings_service import compute_driver_standings_on

    if not staged:
        return []

    driver_ids = list({sp.driver_user_id for sp in staged})

    placeholders = ",".join("?" * len(driver_ids))
    cursor = await db.execute(
        f"""
        SELECT driver_user_id, finishing_position, postrace_time_penalties_ms
        FROM race_session_results rsr
        JOIN session_results sr ON sr.id = rsr.session_result_id
        WHERE sr.round_id = ? AND rsr.driver_user_id IN ({placeholders})
        UNION ALL
        SELECT driver_user_id, finishing_position, 0 AS postrace_time_penalties_ms
        FROM qualifying_session_results qsr
        JOIN session_results sr ON sr.id = qsr.session_result_id
        WHERE sr.round_id = ? AND qsr.driver_user_id IN ({placeholders})
        """,
        (round_id, *driver_ids, round_id, *driver_ids),
    )
    dsr_rows = await cursor.fetchall()
    # The standings are keyed by the account a driver uses now; a staged penalty by the
    # one its result stands under (issue #243).
    from leaguebot.core.services.drivers.driver_service import current_account_map_for_division

    current_of = await current_account_map_for_division(db, division_id)

    # Get total_points from latest standings snapshot
    driver_snaps = await compute_driver_standings_on(db, division_id, round_id)
    pts_map = {snap.driver_user_id: snap.total_points for snap in driver_snaps}

    result = []
    for r in dsr_rows:
        uid = r["driver_user_id"]
        result.append(
            {
                "driver_user_id": uid,
                "finishing_position": r["finishing_position"],
                "total_points": pts_map.get(current_of.get(uid, uid), 0),
                "post_race_time_penalties": r["postrace_time_penalties_ms"],
            }
        )
    return result
