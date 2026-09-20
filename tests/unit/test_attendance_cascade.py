"""`cascade_attendance_from_round` — carrying a round's totals forward (issue #238).

A driver's attendance total is the sum of their rounds, but a copy of the answer is stored on
every round's row. Amending round 3 of ten corrected only round 3's copy, and rounds 4 to 10
went on holding a total worked out from the old figure — which the season's **final** sheet
then published, that sheet being drawn against the last round with results.

The cascade is the other half: the round's points are awarded and every later finalised
round's total is worked out again, in one transaction. What it must **not** do is rebuild the
attended flags, which is the subject of its own test below.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection  # noqa: E402
from services import attendance_service  # noqa: E402
from services.attendance_service import (  # noqa: E402
    cascade_attendance_from_round,
    distribute_attendance_points,
)
from tests.unit.test_attendance_tracking import _awarded, _make_two_round_db  # noqa: E402


async def _total(db_file: str, round_id: int):
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT total_points_after FROM driver_round_attendance WHERE round_id = ?",
            (round_id,),
        )
        row = await cur.fetchone()
    return None if row is None else row["total_points_after"]


async def _attended(db_file: str, round_id: int):
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT attended FROM driver_round_attendance WHERE round_id = ?",
            (round_id,),
        )
        row = await cur.fetchone()
    return None if row is None else row["attended"]


async def _score_both_rounds(db_file: str, division_id: int, round_ids: list[int]) -> None:
    """Both rounds scored in order, as a season that ran them one after the other would be."""
    for round_id in round_ids:
        await distribute_attendance_points(db_file, round_id, division_id)


async def _set_attended(db_file: str, round_id: int, value: int) -> None:
    async with get_connection(db_file) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET attended = ? WHERE round_id = ?",
            (value, round_id),
        )
        await db.commit()


@pytest.mark.asyncio
async def test_a_later_round_s_total_is_corrected(tmp_path):
    """The whole of issue #238: amend the earlier round and the later one's stored total
    moves with it. Before the fix round 2 kept the total it was given when it was scored."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    await _score_both_rounds(db_file, division_id, round_ids)
    # No RSVP and absent, twice over: 2 + 1 a round.
    assert await _total(db_file, round_ids[0]) == 3
    assert await _total(db_file, round_ids[1]) == 6

    # The amendment shows the driver did race round 1, so only the missed check-in stands.
    await _set_attended(db_file, round_ids[0], 1)
    await cascade_attendance_from_round(db_file, round_ids[0], division_id)

    assert await _awarded(db_file, round_ids[0]) == 2
    assert await _total(db_file, round_ids[0]) == 2, (
        "the amended round holds the season's total rather than its own"
    )
    assert await _total(db_file, round_ids[1]) == 5, (
        "the later round still holds a total worked out from the amended round's old figure"
    )


@pytest.mark.asyncio
async def test_rounds_before_the_named_one_are_left_alone(tmp_path):
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    await _score_both_rounds(db_file, division_id, round_ids)

    await cascade_attendance_from_round(db_file, round_ids[1], division_id)

    assert await _awarded(db_file, round_ids[0]) == 3
    assert await _total(db_file, round_ids[0]) == 3


@pytest.mark.asyncio
async def test_the_cascade_never_revokes_a_driver_s_attendance(tmp_path, monkeypatch):
    """Attendance recorded from results is only ever upgraded, never revoked, because the
    driver was given no opportunity to justify themselves and the record errs in their
    favour. So the cascade must not reach the full recompute, which flips either way."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    # Recorded present, with no session results behind it — a full recompute would say absent.
    await _set_attended(db_file, round_ids[0], 1)

    full_recompute = AsyncMock()
    monkeypatch.setattr(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        full_recompute,
    )

    await cascade_attendance_from_round(db_file, round_ids[0], division_id)

    full_recompute.assert_not_awaited()
    assert await _attended(db_file, round_ids[0]) == 1


@pytest.mark.asyncio
async def test_the_cascade_lands_whole_or_not_at_all(tmp_path, monkeypatch):
    """One transaction, as the recalculation is (#187): a failure part-way through the
    propagation leaves the division scored to one rule up to a round and another after it,
    and nothing a league manager runs on this path puts that right."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    assert await _awarded(db_file, round_ids[0]) is None

    real = attendance_service.distribute_attendance_points
    calls: list[int] = []

    async def failing_after_the_first(db_path, round_id, division_id, *, db=None):
        calls.append(round_id)
        if len(calls) > 1:
            raise RuntimeError("the propagation failed part-way through")
        return await real(db_path, round_id, division_id, db=db)

    monkeypatch.setattr(
        attendance_service, "distribute_attendance_points", failing_after_the_first
    )

    with pytest.raises(RuntimeError):
        await cascade_attendance_from_round(db_file, round_ids[0], division_id)

    assert len(calls) == 2, "the propagation did not reach the second round"
    assert await _awarded(db_file, round_ids[0]) is None, (
        "the first round's points were committed despite the cascade failing"
    )


@pytest.mark.asyncio
async def test_it_returns_the_rounds_it_touched_in_order(tmp_path):
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)

    assert await cascade_attendance_from_round(
        db_file, round_ids[0], division_id
    ) == round_ids


@pytest.mark.asyncio
async def test_an_unknown_recompute_mode_is_refused(tmp_path):
    """The three callers ask for one of three named amounts of rebuilding, and a fourth
    spelling would otherwise rebuild nothing and look like it had worked."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)

    with pytest.raises(ValueError, match="unknown recompute mode"):
        await attendance_service._recalculate_forward(
            db_file, round_ids[0], division_id, recompute="everything"
        )

    assert await _awarded(db_file, round_ids[0]) is None


@pytest.mark.asyncio
async def test_a_round_scored_again_keeps_its_own_running_total(tmp_path):
    """A round's stored total is the driver's total **as at that round** (#238).

    The sum used to be taken over every *other* finalised round of the division, with
    nothing holding it to the ones before this one, so scoring round 1 a second time — with
    round 2 already scored — wrote the whole season's figure onto round 1. Each round's copy
    is kept precisely so a figure that looks wrong can be traced round by round, and a
    flattened one cannot be.
    """
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    await _score_both_rounds(db_file, division_id, round_ids)

    await distribute_attendance_points(db_file, round_ids[0], division_id)

    assert await _total(db_file, round_ids[0]) == 3
    assert await _total(db_file, round_ids[1]) == 6


@pytest.mark.asyncio
async def test_the_division_s_current_total_stands_on_the_last_round_touched(tmp_path):
    """Which is why the caller posts the sheet and enforces the sanctions against that
    round, and not against the amended one."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    await _score_both_rounds(db_file, division_id, round_ids)
    await _set_attended(db_file, round_ids[0], 1)

    touched = await cascade_attendance_from_round(db_file, round_ids[0], division_id)

    assert touched[-1] == round_ids[1]
    assert await _total(db_file, touched[-1]) == 5
