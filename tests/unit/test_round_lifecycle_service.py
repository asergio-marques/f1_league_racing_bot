"""Deleting, renumbering and cancelling a round at the service layer.

Issue #208, beneath `tests/unit/test_round_delete_cancel.py`, which covers the commands. These
are the service methods they call, and each carries a rule the command cannot express.

**Deleting renumbers what is left, by date.** The numbers are positions in a calendar, not
identities, so removing round 3 makes the old round 4 the new round 3 — and the renumbering is
ordered by `scheduled_at` rather than by the old numbers, so a round *inserted* out of order
lands in its right place too. `test_renumbering_follows_the_calendar_not_the_old_numbers` is
what pins that.

**Renumbering is what makes a job id collide**, which is why `scheduler_service`'s job suffix
carries the round id as well as its number — the two files are two halves of one rule, and this
is the half that causes the problem.

**Cancelling a round refuses on an archived season.** A COMPLETED or CANCELLED season is the
championship's record; a round inside it cannot be called off after the fact, and the refusal
comes from the service so every route to it agrees.

**Cancelling reconsiders the division.** Cancelling the last outstanding round is what finishes
a division, so the division's own status is refreshed here as well as on a result being
finalised — without it a league would cancel its final round and still be unable to complete the
season. `test_cancelling_the_last_round_finishes_the_division` holds it.

**`update_round_field` allows an explicit set of columns and no others.** It is a generic setter
reached from the amendment service with a field name that ultimately comes from a manager's
choice, so the allow-list is what stops that becoming an arbitrary write.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.round import RoundStatus  # noqa: E402
from services.season_service import SeasonImmutableError, SeasonService  # noqa: E402

SERVER_ID = 12808
SEASON_ID = 1
DIVISION_ID = 11
ACTOR_ID = 77


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, season_status: str = "ACTIVE", rounds=()) -> str:
    """Rounds as ``(id, round_number, days_out, status)``."""
    db_path = os.path.join(str(tmp_path), "round_lifecycle.db")
    await run_migrations(db_path)
    base = datetime.now(timezone.utc)
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
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id, status) "
            "VALUES (?, ?, 'Division 1', 1, 555, 'ACTIVE')",
            (DIVISION_ID, SEASON_ID),
        )
        for round_id, number, days_out, status in rounds:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) "
                "VALUES (?, ?, ?, 'NORMAL', 'Silverstone Circuit', ?, ?)",
                (
                    round_id,
                    DIVISION_ID,
                    number,
                    (base + timedelta(days=days_out)).isoformat(),
                    status,
                ),
            )
        await db.commit()
    return db_path


async def _numbers(db_path: str) -> list[tuple[int, int]]:
    """``(round_id, round_number)`` for the division, ordered by number."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, round_number FROM rounds WHERE division_id = ? ORDER BY round_number",
            (DIVISION_ID,),
        )
        return [(r["id"], r["round_number"]) for r in await cursor.fetchall()]


async def _status(db_path: str, round_id: int) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
        return (await cursor.fetchone())["status"]


async def _division_status(db_path: str) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT status FROM divisions WHERE id = ?", (DIVISION_ID,)
        )
        return (await cursor.fetchone())["status"]


NOT_RUN = RoundStatus.NOT_RUN.value
FINAL = RoundStatus.FINAL.value


# ---------------------------------------------------------------------------
# Deleting and renumbering
# ---------------------------------------------------------------------------


async def test_deleting_a_round_renumbers_the_rest(tmp_path):
    """The numbers are positions in a calendar, not identities."""
    db_path = await _make_db(
        tmp_path,
        rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN), (3, 3, 21, NOT_RUN)),
    )

    await SeasonService(db_path).delete_round(2)

    assert await _numbers(db_path) == [(1, 1), (3, 2)]


async def test_renumbering_follows_the_calendar_not_the_old_numbers(tmp_path):
    """Ordered by `scheduled_at`, so a round inserted out of order lands in its right
    place — numbering by the old numbers would leave the calendar reading backwards."""
    db_path = await _make_db(
        tmp_path,
        rounds=((1, 1, 7, NOT_RUN), (2, 2, 21, NOT_RUN), (3, 3, 14, NOT_RUN)),
    )

    await SeasonService(db_path).renumber_rounds(DIVISION_ID)

    assert await _numbers(db_path) == [(1, 1), (3, 2), (2, 3)]


async def test_deleting_the_last_round_leaves_the_others_untouched(tmp_path):
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN))
    )

    await SeasonService(db_path).delete_round(2)

    assert await _numbers(db_path) == [(1, 1)]


async def test_deleting_a_round_that_does_not_exist_is_a_no_op(tmp_path):
    """Two managers deleting the same round; the second must not renumber a division on
    the strength of a round that has already gone."""
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))

    await SeasonService(db_path).delete_round(999)

    assert await _numbers(db_path) == [(1, 1)]


async def test_deleting_a_round_takes_its_children_with_it(tmp_path):
    """Forecast messages, phase results and sessions all key on the round; left behind
    they would point at nothing."""
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO sessions (round_id, session_type) VALUES (1, 'FULL_RACE')"
        )
        await db.commit()

    await SeasonService(db_path).delete_round(1)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM sessions WHERE round_id = 1")
        assert (await cursor.fetchone())["n"] == 0


async def test_renumbering_an_empty_division_is_not_an_error(tmp_path):
    db_path = await _make_db(tmp_path)

    await SeasonService(db_path).renumber_rounds(DIVISION_ID)

    assert await _numbers(db_path) == []


# ---------------------------------------------------------------------------
# Cancelling
# ---------------------------------------------------------------------------


async def test_a_round_is_marked_cancelled(tmp_path):
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN))
    )

    await SeasonService(db_path).cancel_round(1, SERVER_ID, ACTOR_ID, "Manager")

    assert await _status(db_path, 1) == RoundStatus.CANCELLED.value


async def test_a_cancelled_round_keeps_its_number(tmp_path):
    """It happened — it is on the calendar and the drivers planned around it — so unlike
    a deletion it does not renumber."""
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN))
    )

    await SeasonService(db_path).cancel_round(1, SERVER_ID, ACTOR_ID, "Manager")

    assert await _numbers(db_path) == [(1, 1), (2, 2)]


async def test_cancelling_is_audited_against_its_division(tmp_path):
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))

    await SeasonService(db_path).cancel_round(1, SERVER_ID, ACTOR_ID, "Manager")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id FROM audit_entries "
            "WHERE server_id = ?",
            (SERVER_ID,),
        )
        row = await cursor.fetchone()
    assert row["change_type"] == "round.status"
    assert row["new_value"] == "CANCELLED"
    assert row["division_id"] == DIVISION_ID


@pytest.mark.parametrize("status", ["COMPLETED", "CANCELLED"])
async def test_a_round_in_an_archived_season_cannot_be_cancelled(tmp_path, status):
    """The season is the championship's record; a round inside it cannot be called off
    after the fact, and the refusal lives here so every route to it agrees."""
    db_path = await _make_db(
        tmp_path, season_status=status, rounds=((1, 1, 7, NOT_RUN),)
    )

    with pytest.raises(SeasonImmutableError, match="archived season"):
        await SeasonService(db_path).cancel_round(1, SERVER_ID, ACTOR_ID, "Manager")


async def test_a_refused_cancellation_changes_nothing(tmp_path):
    db_path = await _make_db(
        tmp_path, season_status="COMPLETED", rounds=((1, 1, 7, NOT_RUN),)
    )

    with pytest.raises(SeasonImmutableError):
        await SeasonService(db_path).cancel_round(1, SERVER_ID, ACTOR_ID, "Manager")

    assert await _status(db_path, 1) == NOT_RUN
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM audit_entries")
        assert (await cursor.fetchone())["n"] == 0


async def test_cancelling_the_last_round_finishes_the_division(tmp_path):
    """Cancelling the last outstanding round is what finishes a division — without the
    refresh a league would cancel its final round and still be unable to complete the
    season."""
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, FINAL), (2, 2, 14, NOT_RUN))
    )

    await SeasonService(db_path).cancel_round(2, SERVER_ID, ACTOR_ID, "Manager")

    assert await _division_status(db_path) == "FINISHED"


async def test_cancelling_one_of_several_rounds_leaves_the_division_running(tmp_path):
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN))
    )

    await SeasonService(db_path).cancel_round(1, SERVER_ID, ACTOR_ID, "Manager")

    assert await _division_status(db_path) == "ACTIVE"


# ---------------------------------------------------------------------------
# The generic field setter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("track_name", "Monza"),
        ("format", "SPRINT"),
        ("phase1_done", 1),
        ("phase2_done", 1),
        ("phase3_done", 1),
    ],
)
async def test_each_permitted_field_is_updatable(tmp_path, field, value):
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))

    await SeasonService(db_path).update_round_field(1, field, value)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {field} FROM rounds WHERE id = 1"  # noqa: S608
        )
        assert (await cursor.fetchone())[field] == value


@pytest.mark.parametrize(
    "field", ["status", "division_id", "round_number", "id", "; DROP TABLE rounds --"]
)
async def test_a_field_outside_the_allow_list_is_refused(tmp_path, field):
    """The field name ultimately comes from a manager's choice in the amendment flow, so
    the allow-list is what stops a generic setter becoming an arbitrary write — `status`
    in particular has its own commands and its own rules."""
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))

    with pytest.raises(ValueError, match="not updatable"):
        await SeasonService(db_path).update_round_field(1, field, "anything")


async def test_a_refused_field_writes_nothing(tmp_path):
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))

    with pytest.raises(ValueError):
        await SeasonService(db_path).update_round_field(1, "status", "FINAL")

    assert await _status(db_path, 1) == NOT_RUN
