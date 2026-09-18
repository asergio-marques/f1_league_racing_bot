"""When a division is finished, and what still holds a season open.

Issue #208. These three queries decide whether `/season complete` runs, and issue #154 was a
league stranded by them disagreeing with the rounds they summarise.

**Division status is stored rather than derived**, so that cancelling a division can record the
fact and `/season cancel` can tell the running divisions from the called-off ones. Stored state
can drift from the rounds beneath it, which is why `refresh_division_status` is called from every
place a round's outcome settles *and* again from the `/season complete` gate — the gate cannot
afford to strand a league on a stale row a second time.

**The `status = 'ACTIVE'` guard is what makes it safe to call anywhere.** A division still in
SETUP has not started and a CANCELLED one was called off deliberately; neither has *finished*,
and neither is ever touched. `test_a_cancelled_division_is_not_marked_finished` is the one that
matters — moving it to FINISHED would lose the fact that it was called off, and `/season cancel`
would then treat it as a division that had run.

**A round is outstanding until it reaches one of its two terminal states**, FINAL or CANCELLED.
Every other state is a round still waiting on somebody: for its date, for its results, for
report verdicts, or for appeal verdicts. The set is read from `_TERMINAL_SQL` so the three
queries cannot come to disagree about what "done" means.

**The completion gate asks about divisions, not rounds.** A division is the unit a league
finishes, and one that was cancelled never had to run its rounds at all — asking about rounds
would hold a season open on a division nobody intends to run.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.round import RoundStatus  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 12608
SEASON_ID = 1

#: The two states that mean a round is no longer waiting on anybody.
TERMINAL = (RoundStatus.FINAL.value, RoundStatus.CANCELLED.value)

#: Every other state a round can be in — each one waiting on something.
OUTSTANDING = sorted(
    s.value for s in RoundStatus if s.value not in TERMINAL
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, season_status: str = "ACTIVE") -> str:
    db_path = os.path.join(str(tmp_path), "division_status.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', ?)",
            (SEASON_ID, SERVER_ID, season_status),
        )
        await db.commit()
    return db_path


async def _add_division(
    db_path: str, division_id: int, *, name: str | None = None, status: str = "ACTIVE"
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                division_id,
                SEASON_ID,
                name or f"Division {division_id}",
                division_id,
                500 + division_id,
                status,
            ),
        )
        await db.commit()


async def _add_round(
    db_path: str,
    division_id: int,
    number: int,
    status: str,
    *,
    track: str = "Silverstone Circuit",
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO rounds (division_id, round_number, format, track_name, "
            "scheduled_at, status) "
            "VALUES (?, ?, 'NORMAL', ?, '2026-06-01', ?)",
            (division_id, number, track, status),
        )
        await db.commit()


async def _status(db_path: str, division_id: int) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status FROM divisions WHERE id = ?", (division_id,)
        )
        return (await cursor.fetchone())["status"]


# ---------------------------------------------------------------------------
# Refreshing a division's status
# ---------------------------------------------------------------------------


async def test_a_division_whose_rounds_are_all_done_finishes(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11)
    await _add_round(db_path, 11, 1, RoundStatus.FINAL.value)
    await _add_round(db_path, 11, 2, RoundStatus.CANCELLED.value)

    moved = await SeasonService(db_path).refresh_division_status(11)

    assert moved is True
    assert await _status(db_path, 11) == "FINISHED"


@pytest.mark.parametrize("status", OUTSTANDING)
async def test_any_round_still_waiting_holds_the_division_open(tmp_path, status):
    """Read from `RoundStatus` itself, so a state added to the enum and not to the
    terminal set fails here rather than finishing a division mid-season."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11)
    await _add_round(db_path, 11, 1, RoundStatus.FINAL.value)
    await _add_round(db_path, 11, 2, status)

    moved = await SeasonService(db_path).refresh_division_status(11)

    assert moved is False
    assert await _status(db_path, 11) == "ACTIVE"


async def test_a_division_with_no_rounds_finishes(tmp_path):
    """Nothing is outstanding. A division added and never scheduled must not hold a
    season open for ever."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11)

    assert await SeasonService(db_path).refresh_division_status(11) is True


async def test_refreshing_a_finished_division_reports_no_change(tmp_path):
    """The return value says whether *this* call moved it, which is what lets a caller
    avoid announcing a division finishing twice."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="FINISHED")
    await _add_round(db_path, 11, 1, RoundStatus.FINAL.value)
    service = SeasonService(db_path)

    assert await service.refresh_division_status(11) is False


async def test_a_cancelled_division_is_not_marked_finished(tmp_path):
    """It was called off deliberately, and moving it to FINISHED would lose that —
    `/season cancel` would then treat it as a division that had run."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="CANCELLED")
    await _add_round(db_path, 11, 1, RoundStatus.FINAL.value)

    moved = await SeasonService(db_path).refresh_division_status(11)

    assert moved is False
    assert await _status(db_path, 11) == "CANCELLED"


async def test_a_division_still_in_setup_is_not_marked_finished(tmp_path):
    """It has not started. Finishing it would let a season be completed before it ran."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="SETUP")

    moved = await SeasonService(db_path).refresh_division_status(11)

    assert moved is False
    assert await _status(db_path, 11) == "SETUP"


async def test_refreshing_one_division_leaves_its_neighbours_alone(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11)
    await _add_division(db_path, 12)
    await _add_round(db_path, 11, 1, RoundStatus.FINAL.value)
    await _add_round(db_path, 12, 1, RoundStatus.AWAITING_RESULTS.value)

    await SeasonService(db_path).refresh_division_status(11)

    assert await _status(db_path, 11) == "FINISHED"
    assert await _status(db_path, 12) == "ACTIVE"


# ---------------------------------------------------------------------------
# The completion gate
# ---------------------------------------------------------------------------


async def test_a_season_whose_divisions_have_all_finished_may_complete(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="FINISHED")
    await _add_division(db_path, 12, status="FINISHED")

    assert await SeasonService(db_path).all_divisions_finished(SERVER_ID) is True


async def test_a_cancelled_division_does_not_hold_a_season_open(tmp_path):
    """One that was cancelled never had to run its rounds at all."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="FINISHED")
    await _add_division(db_path, 12, status="CANCELLED")

    assert await SeasonService(db_path).all_divisions_finished(SERVER_ID) is True


async def test_an_active_division_holds_the_season_open(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="FINISHED")
    await _add_division(db_path, 12, status="ACTIVE")

    assert await SeasonService(db_path).all_divisions_finished(SERVER_ID) is False


async def test_the_gate_asks_about_divisions_not_rounds(tmp_path):
    """A cancelled division with an unfinished round still lets the season complete —
    asking about rounds would hold it open on a division nobody intends to run."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="CANCELLED")
    await _add_round(db_path, 11, 1, RoundStatus.AWAITING_RESULTS.value)

    assert await SeasonService(db_path).all_divisions_finished(SERVER_ID) is True


# ---------------------------------------------------------------------------
# The detail behind a refusal
# ---------------------------------------------------------------------------


async def test_every_outstanding_round_is_reported(tmp_path):
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, name="Division 1")
    await _add_round(db_path, 11, 1, RoundStatus.FINAL.value)
    await _add_round(db_path, 11, 2, RoundStatus.AWAITING_RESULTS.value, track="Monza")

    rounds = await SeasonService(db_path).get_outstanding_rounds(SERVER_ID)

    assert len(rounds) == 1
    assert rounds[0]["division"] == "Division 1"
    assert rounds[0]["round_number"] == 2
    assert rounds[0]["track_name"] == "Monza"


async def test_a_cancelled_round_is_not_outstanding(tmp_path):
    """It is not waiting on anybody."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11)
    await _add_round(db_path, 11, 1, RoundStatus.CANCELLED.value)

    assert await SeasonService(db_path).get_outstanding_rounds(SERVER_ID) == []


async def test_a_cancelled_division_s_rounds_are_not_outstanding(tmp_path):
    """The division was called off, so its rounds are nobody's to finalise — reporting
    them would name rounds a manager cannot act on."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, status="CANCELLED")
    await _add_round(db_path, 11, 1, RoundStatus.AWAITING_RESULTS.value)

    assert await SeasonService(db_path).get_outstanding_rounds(SERVER_ID) == []


async def test_outstanding_rounds_are_ordered_for_reading(tmp_path):
    """The refusal prints these directly, so the order is what a manager works through."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11, name="Beta")
    await _add_division(db_path, 12, name="Alpha")
    await _add_round(db_path, 11, 2, RoundStatus.AWAITING_RESULTS.value)
    await _add_round(db_path, 11, 1, RoundStatus.AWAITING_RESULTS.value)
    await _add_round(db_path, 12, 1, RoundStatus.AWAITING_RESULTS.value)

    rounds = await SeasonService(db_path).get_outstanding_rounds(SERVER_ID)

    assert [(r["division"], r["round_number"]) for r in rounds] == [
        ("Alpha", 1),
        ("Beta", 1),
        ("Beta", 2),
    ]


async def test_a_season_not_running_reports_no_outstanding_rounds(tmp_path):
    """A season in setup has not started; nothing in it is late."""
    db_path = await _make_db(tmp_path, season_status="SETUP")
    await _add_division(db_path, 11)
    await _add_round(db_path, 11, 1, RoundStatus.AWAITING_RESULTS.value)

    assert await SeasonService(db_path).get_outstanding_rounds(SERVER_ID) == []


@pytest.mark.parametrize("status", OUTSTANDING)
async def test_each_waiting_state_counts_as_outstanding(tmp_path, status):
    """The same terminal set the division refresh reads, so the gate and its explanation
    cannot come to disagree about what "done" means."""
    db_path = await _make_db(tmp_path)
    await _add_division(db_path, 11)
    await _add_round(db_path, 11, 1, status)

    assert len(await SeasonService(db_path).get_outstanding_rounds(SERVER_ID)) == 1
