"""A standing season review prompt, written as a test's seed into whichever shape its table has.

#482 re-keys `season_review_prompts`: from one row held at `id = 1` to one row per standing prompt,
keyed by the prompt message's id, with the review it belongs to (`review`, its command) and the ids
of its report messages (`report_message_ids`, a JSON list) beside it. The tests that only need a
prompt standing are written before that edit and must pass on either side of it, so the row is
written into the columns the table has. Once the old shape is gone, the `id` branch is dead and may
go with any later change to this file.
"""
from __future__ import annotations

import json
from collections.abc import Iterable


async def store_review_prompt(
    db,
    *,
    season_id: int,
    channel_id: int,
    message_id: int,
    reviewer_id: int,
    posted_at: str = "2026-09-07T12:00:00+00:00",
    review: str = "/season placements-review",
    report_message_ids: Iterable[int] = (),
) -> None:
    """Insert one standing prompt through *db*, an open connection; the caller commits."""
    cursor = await db.execute("PRAGMA table_info(season_review_prompts)")
    columns = {row[1] for row in await cursor.fetchall()}
    values: dict[str, object] = {
        "season_id": season_id,
        "channel_id": channel_id,
        "message_id": message_id,
        "reviewer_id": reviewer_id,
        "posted_at": posted_at,
    }
    if "id" in columns:
        values["id"] = 1
    if "review" in columns:
        values["review"] = review
    if "report_message_ids" in columns:
        values["report_message_ids"] = json.dumps(list(report_message_ids))
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    await db.execute(
        f"INSERT INTO season_review_prompts ({names}) VALUES ({marks})", tuple(values.values())
    )
