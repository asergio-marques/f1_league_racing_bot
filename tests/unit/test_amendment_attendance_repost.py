"""Recomputing and reposting a division's attendance after an amendment (#345).

Correcting a round's classification changes who attended it, which changes their attendance
points, which carries forward through every later round — each round's row stores the driver's
total *as at that round*.

Three rules this holds to, none of them obvious.

**The sheet is not a sequence.** A division keeps one live sheet in one slot. Unlike results,
standings and verdicts there is nothing to reorder, so it is posted once rather than rebuilt
round by round.

**It is posted against the latest round, not the amended one.** That is where the running
totals now stand, and it is the sheet a league reads.

**Sanctions are enforced there and nowhere earlier.** The past is not rewritten: a sack that
was warranted at round 5 is not undone by a correction to round 3, and one that becomes
warranted only now takes effect now. This is already how `/attendance sync` behaves.

**And the recompute mode is `"round"`, not `"none"`.** `cascade_attendance_from_round` hardcodes
`"none"`, which carries totals forward without rebuilding the amended round's attended flags —
so the round would keep describing the classification it replaced. `"round"` is the amendment's
own mode (FR-030).
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.result_submission_service import (  # noqa: E402
    _repost_attendance_after_amendment,
)

SEASON_ID = 51
DIVISION_ID = 61
AMENDED_ROUND = 3
LATEST_ROUND = 7


async def _db(tmp_path, name: str) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 6, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()
    return db_path


def _bot(*, attendance=True):
    bot = MagicMock()
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    return bot


def _outcome(*, complete=True, lines=()):
    return SimpleNamespace(
        complete=complete, failure_lines=MagicMock(return_value=list(lines))
    )


async def _run(db_path, *, bot=None, touched=(AMENDED_ROUND, 5, LATEST_ROUND),
               outcome=None, recalc_error=None):
    calls: dict = {}

    async def _recalculate(_db, round_id, division_id, *, recompute):
        if recalc_error is not None:
            raise recalc_error
        calls["recompute"] = recompute
        calls["from_round"] = round_id
        return list(touched)

    async def _sheet(_bot, _guild, _db, round_id, division_id):
        calls["sheet_round"] = round_id

    async def _sanctions(_bot, _guild, _db, round_id, division_id, season_id):
        calls["sanction_round"] = round_id
        calls["season_id"] = season_id
        return outcome if outcome is not None else _outcome()

    with patch(
        "services.attendance_service._recalculate_forward",
        new=AsyncMock(side_effect=_recalculate),
    ), patch(
        "services.attendance_service.post_attendance_sheet",
        new=AsyncMock(side_effect=_sheet),
    ), patch(
        "services.attendance_service.enforce_attendance_sanctions",
        new=AsyncMock(side_effect=_sanctions),
    ):
        faults = await _repost_attendance_after_amendment(
            db_path, AMENDED_ROUND, DIVISION_ID, bot or _bot(), MagicMock()
        )
    return faults, calls


async def test_the_recompute_rebuilds_the_amended_rounds_attendance(tmp_path):
    """`"round"`, not `"none"` — or the round keeps the attendance of the classification
    it replaced.

    **Deliberately in both directions** (decided 2026-09-21). `"round"` is a full recompute, so
    a driver the correction removes is marked absent and charged; the first pass's rule that a
    recorded attendance is never taken back does not hold for an amendment. Do not "fix" this to
    the upgrade-only recording — the league chose the correction over the driver's favour."""
    db_path = await _db(tmp_path, "att_mode")

    _, calls = await _run(db_path)

    assert calls["recompute"] == "round"
    assert calls["from_round"] == AMENDED_ROUND


async def test_the_sheet_is_posted_against_the_latest_round(tmp_path):
    """Where the running totals now stand, and what a league reads."""
    db_path = await _db(tmp_path, "att_sheet")

    _, calls = await _run(db_path)

    assert calls["sheet_round"] == LATEST_ROUND


async def test_sanctions_are_enforced_against_the_latest_round(tmp_path):
    """**The past is not rewritten.**

    Enforcing against the amended round would apply a threshold as it stood weeks ago, sacking
    a driver for a total they have since recovered from — or undoing one that was right when it
    was made.
    """
    db_path = await _db(tmp_path, "att_sanctions")

    _, calls = await _run(db_path)

    assert calls["sanction_round"] == LATEST_ROUND
    assert calls["season_id"] == SEASON_ID


async def test_nothing_happens_with_the_attendance_module_off(tmp_path):
    """A league that does not track attendance has no sheet to rebuild."""
    db_path = await _db(tmp_path, "att_off")

    faults, calls = await _run(db_path, bot=_bot(attendance=False))

    assert faults == []
    assert calls == {}


async def test_a_sanction_that_did_not_apply_is_reported(tmp_path):
    """The amendment stands; what could not be carried out is named rather than swallowed."""
    db_path = await _db(tmp_path, "att_incomplete")

    faults, _ = await _run(
        db_path,
        outcome=_outcome(complete=False, lines=["<@101> could not be moved to Reserve"]),
    )

    assert faults == ["<@101> could not be moved to Reserve"]


async def test_a_failed_recompute_is_reported_not_raised(tmp_path):
    """The results are already committed and reposted; the sheet is downstream of them.

    Raising here would turn a corrected classification into a failed amendment.
    """
    db_path = await _db(tmp_path, "att_raise")

    faults, calls = await _run(db_path, recalc_error=RuntimeError("locked"))

    assert len(faults) == 1
    assert "still shows the round as it was" in faults[0]
    assert "sheet_round" not in calls


async def test_a_recompute_that_touched_nothing_falls_back_to_the_round(tmp_path):
    """An empty list would index-error; the amended round is the honest answer."""
    db_path = await _db(tmp_path, "att_empty")

    _, calls = await _run(db_path, touched=())

    assert calls["sheet_round"] == AMENDED_ROUND
