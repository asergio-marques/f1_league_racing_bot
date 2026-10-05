"""The changes of a round's review, read together (#439, slice 2).

A round's review is carried out by seven kinds of change on the queue: opening it, putting its
appeals prompt back, closing a stale one, approving its reports, approving its appeals, and
approving an amendment's two stages. Several rules need to ask whether *any* of them is in hand for
a round or a division, a stopped one included, because nothing overtakes a stopped job:
restart recovery leaves such a round to the queue, and `/results rounds amend` is refused while
its division has one.

This module only names the kinds and reads them. It is imported by those that ask and imports the
change types for their names, so no change type imports it.
"""

from __future__ import annotations

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.change_queue import in_hand, unfinished
from leaguebot.results.services import (
    amendment_stage_changes,
    appeals_approval_change,
    report_approval_change,
    review_open_change,
)

__all__ = ["REVIEW_KINDS", "division_job_in_hand", "round_in_hand"]

#: Every kind of change that carries out part of a round's review.
REVIEW_KINDS: tuple[str, ...] = (
    review_open_change.KIND,
    report_approval_change.KIND,
    appeals_approval_change.KIND,
    amendment_stage_changes.REPORTS_KIND,
    amendment_stage_changes.APPEALS_KIND,
)


async def round_in_hand(db_path: str, round_id: int) -> bool:
    """Whether a change of any review kind naming *round_id* is queued, running or stopped."""
    return any(p.get("round_id") == round_id for p in await unfinished(db_path, REVIEW_KINDS))


async def division_job_in_hand(db_path: str, division_id: int) -> int | None:
    """The number of the first job a review change of *division_id* waits on, or None where the
    division has none in hand, the change that is nearest its turn first.

    A change belongs to the division where its payload names the division, or names a round of it.
    A change with every job done and only its close left gives 0, which still holds the division.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM rounds WHERE division_id = ?", (division_id,))
        rounds = {int(row["id"]) for row in await cursor.fetchall()}
    for payload, job in await in_hand(db_path, REVIEW_KINDS):
        if payload.get("division_id") == division_id or payload.get("round_id") in rounds:
            return job or 0
    return None
