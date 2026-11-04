"""Attendance's opening sheet, posted when a season is approved (2026-09-08).

The opening classification brackets a season's start: the attendance sheet a league already
reads after every round, under the opening's heading and with no message text (see
``leaguebot.core.models.classification_occasion.ClassificationOccasion``). Attendance posts its
own, one division at a time, each a job of the season's approval on the change queue, signed up
to the hook core declares for placements confirmed (architecture.md, "How modules and core fit
together"). Each posting reads the division's first round as it runs, and raises where it
cannot post, for the queue to stop on.

What matters here is which sheet is posted for which division, against which round, and that a
posting raises where it cannot be made; what the sheet *says* is covered by
``test_attendance_sheet_posting``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from tests.support.season_classification import (
    bot_on,
    classification_posts,
    first_round_id,
    guild_double,
    seed_season,
)

_NOT_ATTENDANCE_YET = "#283: the opening sheet is not yet posted by attendance's own module"


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "classification.db")
    await run_migrations(path)
    return path


@pytest.mark.xfail(strict=True, reason=_NOT_ATTENDANCE_YET)
async def test_the_opening_sheet_is_posted_for_every_division(db_path):
    """Div A and Div B, one round and one seated driver each: each division's opening
    attendance sheet is posted once, under the opening's heading."""
    from leaguebot.attendance.services import sheet_posting as service

    _season_id, division_ids = await seed_season(db_path, divisions=("Div A", "Div B"))
    attendance = AsyncMock()

    first, second, third = classification_posts(attendance=attendance)
    with first, second, third:
        for division_id in division_ids:
            await service.post_opening_sheet(
                bot_on(db_path), guild_double(), db_path, division_id, as_text=False
            )

    assert attendance.await_count == 2
    assert all(
        call.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING
        for call in attendance.await_args_list
    )


@pytest.mark.xfail(strict=True, reason=_NOT_ATTENDANCE_YET)
async def test_the_opening_sheet_is_drawn_against_the_division_s_first_round(db_path):
    """Not because it stands after it — the grid of rounds is read from the calendar."""
    from leaguebot.attendance.services import sheet_posting as service

    _season_id, division_ids = await seed_season(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, scheduled_at) "
            "VALUES (?, 2, 'NORMAL', '2026-03-01T18:00:00')",
            (division_ids[0],),
        )
        await db.commit()
    opener = await first_round_id(db_path, division_ids[0])
    attendance = AsyncMock()

    first, second, third = classification_posts(attendance=attendance)
    with first, second, third:
        await service.post_opening_sheet(
            bot_on(db_path), guild_double(), db_path, division_ids[0], as_text=False
        )

    assert attendance.await_args.args[3] == opener


@pytest.mark.xfail(strict=True, reason=_NOT_ATTENDANCE_YET)
async def test_a_division_with_no_rounds_posts_no_opening_sheet(db_path):
    """Div A's only round deleted: its opening sheet posts nothing."""
    from leaguebot.attendance.services import sheet_posting as service

    _season_id, division_ids = await seed_season(db_path)
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM rounds WHERE division_id = ?", (division_ids[0],))
        await db.commit()
    attendance = AsyncMock()

    first, second, third = classification_posts(attendance=attendance)
    with first, second, third:
        await service.post_opening_sheet(
            bot_on(db_path), guild_double(), db_path, division_ids[0], as_text=False
        )

    assert attendance.await_count == 0


@pytest.mark.xfail(strict=True, reason=_NOT_ATTENDANCE_YET)
async def test_the_opening_sheet_raises_where_it_cannot_be_posted(db_path):
    """Discord refuses Div A's opening attendance sheet: what the sheet's posting raises (it
    always raises, so it is asked for no `raise_on_failure`) reaches the job, for the queue to
    stop on."""
    from leaguebot.attendance.services import sheet_posting as service
    from leaguebot.core.models.change import StepFailedOnDiscord

    _season_id, division_ids = await seed_season(db_path)
    attendance = AsyncMock(side_effect=StepFailedOnDiscord("the sheet could not be posted"))

    first, second, third = classification_posts(attendance=attendance)
    with first, second, third, pytest.raises(StepFailedOnDiscord):
        await service.post_opening_sheet(
            bot_on(db_path), guild_double(), db_path, division_ids[0], as_text=False
        )

    assert "raise_on_failure" not in attendance.await_args.kwargs
    assert attendance.await_args.kwargs["occasion"] is ClassificationOccasion.SEASON_OPENING
