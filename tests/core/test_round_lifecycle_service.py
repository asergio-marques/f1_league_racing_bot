"""Deleting, renumbering and cancelling a round at the service layer.

Issue #208, beneath `tests/core/test_round_delete_cancel.py`, which covers the commands. These
are the service methods they call, and each carries a rule the command cannot express.

**Deleting renumbers what is left, by date.** The numbers are positions in a calendar, not
identities, so removing round 3 makes the old round 4 the new round 3 — and the renumbering is
ordered by `scheduled_at` rather than by the old numbers, so a round *inserted* out of order
lands in its right place too. `test_renumbering_follows_the_calendar_not_the_old_numbers` is
what pins that.

**Renumbering is what makes a job id collide**, which is why `scheduler_service`'s job suffix
carries the round id as well as its number — the two files are two halves of one rule, and this
is the half that causes the problem.

**Cancelling a round leaves an archived season alone.** A COMPLETED or CANCELLED season is the
championship's record; a round inside it cannot be called off after the fact. `cancel_round_on`
writes on the save it is handed, as the round's cancellation on the change queue (#439) gives it,
and where the round may no longer be cancelled it writes nothing and gives `None`, so the change
names the refusal rather than the save raising.
`test_season_service_on_forms.py::test_a_round_no_longer_cancellable_is_left_and_gives_none`
holds it.

**Cancelling reconsiders the division.** Cancelling the last outstanding round is what finishes
a division, so the division's own status is refreshed here as well as on a result being
finalised — without it a league would cancel its final round and still be unable to complete the
season. `test_season_service_on_forms.py::test_cancelling_the_last_round_finishes_the_division_and_moves_the_season_on_in_the_same_save`
holds it.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.round import RoundStatus
from leaguebot.core.services.season_service import SeasonService

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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', ?)",
            (SEASON_ID, season_status),
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

# The moment the cancellation is recorded at, pinned.
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


async def _cancel(db_path: str, round_id: int) -> str | None:
    """Cancel *round_id* on one connection with `cancel_round_on`, as the round's cancellation
    does in its save, and commit it, which `cancel_round_on` does not. Gives what it gives: the
    round's old status, or `None` where it moved nothing."""
    from leaguebot.core.services.season_service import cancel_round_on

    async with get_connection(db_path) as db:
        old = await cancel_round_on(
            db, round_id, actor_id=ACTOR_ID, actor_name="Manager", now=NOW
        )
        await db.commit()
    return old


# ---------------------------------------------------------------------------
# Deleting and renumbering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rounds, expected",
    [
        pytest.param(
            ((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN), (3, 3, 21, NOT_RUN)), [(1, 1), (3, 2)],
            id="a middle round",
        ),
        pytest.param(
            ((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN)), [(1, 1)],
            id="the last round",
        ),
    ],
)
async def test_deleting_a_round_renumbers_the_rest(tmp_path, rounds, expected):
    """The numbers are positions in a calendar, not identities. Deleting the last round leaves
    the others untouched."""
    db_path = await _make_db(tmp_path, rounds=rounds)

    await SeasonService(db_path).delete_round(2)

    assert await _numbers(db_path) == expected


async def test_renumbering_follows_the_calendar_not_the_old_numbers(tmp_path):
    """Ordered by `scheduled_at`, so a round inserted out of order lands in its right
    place — numbering by the old numbers would leave the calendar reading backwards."""
    db_path = await _make_db(
        tmp_path,
        rounds=((1, 1, 7, NOT_RUN), (2, 2, 21, NOT_RUN), (3, 3, 14, NOT_RUN)),
    )

    await SeasonService(db_path).renumber_rounds(DIVISION_ID)

    assert await _numbers(db_path) == [(1, 1), (3, 2), (2, 3)]


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

    await _cancel(db_path, 1)

    assert await _status(db_path, 1) == RoundStatus.CANCELLED.value


async def test_a_cancelled_round_keeps_its_number(tmp_path):
    """It happened — it is on the calendar and the drivers planned around it — so unlike
    a deletion it does not renumber."""
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN))
    )

    await _cancel(db_path, 1)

    assert await _numbers(db_path) == [(1, 1), (2, 2)]


async def test_cancelling_is_audited_against_its_division(tmp_path):
    db_path = await _make_db(tmp_path, rounds=((1, 1, 7, NOT_RUN),))

    await _cancel(db_path, 1)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, division_id FROM audit_entries "
            "",
        )
        row = await cursor.fetchone()
    assert row["change_type"] == "round.status"
    assert row["new_value"] == "CANCELLED"
    assert row["division_id"] == DIVISION_ID


async def test_cancelling_one_of_several_rounds_leaves_the_division_running(tmp_path):
    db_path = await _make_db(
        tmp_path, rounds=((1, 1, 7, NOT_RUN), (2, 2, 14, NOT_RUN))
    )

    await _cancel(db_path, 1)

    assert await _division_status(db_path) == "ACTIVE"
