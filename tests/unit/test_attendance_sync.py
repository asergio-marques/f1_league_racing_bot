"""`sync_attendance` — what `/attendance sync` does (decided 2026-09-18, issue #239).

A sanction that did not apply used to be recoverable only by hand, through `/driver sack` or
`/driver move`, which post no verdict and mark no sheet. The sync is the recovery: every
finalised round from the one named on is recomputed from its results in one transaction, the
division's sheet is posted for the latest of them, and the sanctions are enforced against that
latest round, where the running totals stand.

**Safe to run twice** is the property the whole recovery rests on, and is pinned through the
real placement service rather than a double: a driver already sacked holds no seat and so is
no longer a candidate, which a double that recorded calls would never show.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection  # noqa: E402
from services import attendance_service  # noqa: E402
from services.attendance_service import SanctionOutcome, sync_attendance  # noqa: E402
from tests.unit.test_attendance_sanctions import (  # noqa: E402
    DIVISION_ID,
    FULL_TIME_PROFILE,
    ROUND_ID,
    SEASON_ID,
    _make_bot,
    _make_db,
    _seed_totals,
)
from tests.unit.test_attendance_tracking import _awarded, _make_two_round_db  # noqa: E402


async def _add_rounds(db_path: str, statuses: dict[int, str]) -> None:
    """Rounds 2 onwards of the division, by number, beside the fixture's round 1."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE rounds SET status = 'FINAL' WHERE id = ?", (ROUND_ID,)
        )
        for number, status in statuses.items():
            await db.execute(
                "INSERT INTO rounds "
                "(id, division_id, round_number, format, track_name, scheduled_at, status) "
                "VALUES (?, ?, ?, 'NORMAL', 'Monza', '2026-06-01T18:00:00', ?)",
                (number, DIVISION_ID, number, status),
            )
        await db.commit()


@pytest.fixture
def pipeline():
    """Stub the recalculation, the sheet and the sanctions, so what reaches each is tested."""
    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ) as recompute, patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ) as distribute, patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ) as sheet, patch.object(
        attendance_service,
        "enforce_attendance_sanctions",
        new=AsyncMock(return_value=SanctionOutcome()),
    ) as sanctions:
        yield recompute, distribute, sheet, sanctions


async def _sync(bot, db_path: str, from_round_id: int = ROUND_ID) -> SanctionOutcome:
    return await sync_attendance(
        bot, MagicMock(), db_path, DIVISION_ID, from_round_id, SEASON_ID
    )


async def test_sync_recalculates_from_the_input_round_forward(tmp_path, pipeline):
    """Every round from the one named on is recomputed from its results, not merely
    redistributed — a later round whose recording failed is repaired too."""
    recompute, distribute, _, _ = pipeline
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL", 3: "AWAITING_APPEAL_VERDICTS"})

    await _sync(_make_bot(db_path), db_path)

    assert [c.args[1] for c in recompute.await_args_list] == [1, 2, 3]
    assert [c.args[1] for c in distribute.await_args_list] == [1, 2, 3]


async def test_sync_leaves_earlier_rounds_and_unfinished_ones_alone(tmp_path, pipeline):
    recompute, distribute, _, _ = pipeline
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL", 3: "FINAL", 4: "NOT_RUN"})

    await _sync(_make_bot(db_path), db_path, from_round_id=2)

    assert [c.args[1] for c in recompute.await_args_list] == [2, 3]
    assert [c.args[1] for c in distribute.await_args_list] == [2, 3]


async def test_sync_posts_the_sheet_and_enforces_against_the_latest_round(
    tmp_path, pipeline
):
    """The running totals are carried forward, so the latest round is where they stand —
    and the channel holds one sheet, which is that round's."""
    _, _, sheet, sanctions = pipeline
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {2: "FINAL", 3: "FINAL"})

    await _sync(_make_bot(db_path), db_path)

    assert sheet.await_args.args[3] == 3
    assert sanctions.await_args.args[3] == 3


async def test_sync_returns_what_the_sanctions_did(tmp_path, pipeline):
    _, _, _, sanctions = pipeline
    outcome = SanctionOutcome(failed=[("<@5>", "autosack", "no")])
    sanctions.return_value = outcome
    db_path = await _make_db(tmp_path, autosack=20)
    await _add_rounds(db_path, {})

    assert await _sync(_make_bot(db_path), db_path) is outcome


async def test_a_second_sync_applies_nothing_twice(tmp_path):
    """The recovery is to run it again, so running it again must be harmless: the driver
    sacked by the first run is not attempted by the second, and nothing is announced."""
    from services.placement_service import PlacementService

    db_path = await _make_db(tmp_path, autoreserve=None, autosack=20)
    await _add_rounds(db_path, {})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?",
            (FULL_TIME_PROFILE,),
        )
        await db.commit()
    await _seed_totals(db_path, {FULL_TIME_PROFILE: 25})
    bot = _make_bot(db_path)
    placement = PlacementService(db_path)
    placement._refresh_lineup_post = AsyncMock(return_value=None)
    bot.placement_service = placement

    with patch.object(
        attendance_service,
        "record_attendance_from_results_full_recompute",
        new=AsyncMock(return_value=None),
    ), patch.object(
        attendance_service, "distribute_attendance_points", new=AsyncMock(return_value=None)
    ), patch.object(
        attendance_service, "post_attendance_sheet", new=AsyncMock(return_value=None)
    ), patch(
        "services.verdict_announcement_service.post_autosanction_announcement",
        new=AsyncMock(return_value=None),
    ) as announce, patch(
        "services.verdict_announcement_service.banner_for_round",
        new=MagicMock(return_value=None),
    ):
        first = await _sync(bot, db_path)
        second = await _sync(bot, db_path)

    assert [sanction for _, sanction in first.applied] == ["autosack"]
    assert second.applied == [] and second.complete
    announce.assert_awaited_once()


async def test_sync_is_one_transaction(tmp_path, monkeypatch):
    """#187's rule holds for the sync as for an amendment: a failure part-way through the
    rounds writes nothing, rather than leaving the division scored to two rules."""
    db_file, division_id, round_ids = await _make_two_round_db(tmp_path)
    real = attendance_service.record_attendance_from_results_full_recompute
    calls: list[int] = []

    async def failing_on_the_second(db_path, round_id, division_id, *, db=None):
        calls.append(round_id)
        if len(calls) > 1:
            raise RuntimeError("the recompute failed part-way through")
        return await real(db_path, round_id, division_id, db=db)

    monkeypatch.setattr(
        attendance_service, "record_attendance_from_results_full_recompute",
        failing_on_the_second,
    )
    monkeypatch.setattr(attendance_service, "post_attendance_sheet", AsyncMock())
    monkeypatch.setattr(attendance_service, "enforce_attendance_sanctions", AsyncMock())

    with pytest.raises(RuntimeError):
        await sync_attendance(None, None, db_file, division_id, round_ids[0], 1)

    assert calls == round_ids
    assert await _awarded(db_file, round_ids[0]) is None
