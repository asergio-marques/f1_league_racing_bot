"""`_apply_points_from_config` — where a league's points table becomes championship points.

Issue #208. This is the join between two halves that are separately well covered: the points
configuration a league typed in, and the scoring rules in `standings_service`. It reads the
season's *copy* of the configuration, computes each driver's points, and writes them back.

**It reads the season's copy, not the server's.** `season_points_entries` is snapshotted when a
season is approved precisely so that editing the server-level configuration mid-season cannot
rescore rounds already run. A league that changed its points table in September must not find
its June results rewritten.

**Qualifying and races write different columns.** A race carries a fastest-lap bonus and a
qualifying session does not, so the two branches update different sets of columns — and writing
a bonus into a qualifying row would award points for a lap nobody set.
`test_qualifying_points_are_written_without_a_bonus` sits on that.

**No configuration at all leaves the points alone.** A round submitted before its season had a
points table attached scores zero, and zero is what the rows already hold — so the function
returns rather than writing a row of zeroes over results that may since have been corrected.

**The fastest-lap override travels with the session**, not with the request: a steward naming
the holder by hand writes it onto `session_results`, and every recomputation from then on has to
honour it. Recomputing without it would quietly hand the bonus back to whoever the submitted
times say was quickest — undoing a steward's decision with nobody told.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.results.models.points_config import SessionType  # noqa: E402
from leaguebot.results.services.result_submission_service import (  # noqa: E402
    _apply_points_from_config,
    _apply_points_in_tx,
)
from tests.support.teams import seed_team_instances  # noqa: E402

SERVER_ID = 12708
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
SESSION_RESULT_ID = 1
CONFIG = "100%"
DRIVER_A = 4001
DRIVER_B = 4002


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    session_type: SessionType = SessionType.FEATURE_RACE,
    drivers=((DRIVER_A, 1, "CLASSIFIED", "1:22.000"), (DRIVER_B, 2, "CLASSIFIED", "1:23.000")),
    entries=((1, 25), (2, 18)),
    fl=(1, None),
    fl_override: int | None = None,
) -> str:
    db_path = os.path.join(str(tmp_path), "apply_points.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 0)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO session_results "
            "(id, round_id, division_id, session_type, status, fl_driver_override) "
            "VALUES (?, ?, ?, ?, 'ACTIVE', ?)",
            (SESSION_RESULT_ID, ROUND_ID, DIVISION_ID, session_type.value, fl_override),
        )

        for position, points in entries:
            await db.execute(
                "INSERT INTO season_points_entries "
                "(season_id, config_name, session_type, position, points) "
                "VALUES (?, ?, ?, ?, ?)",
                (SEASON_ID, CONFIG, session_type.value, position, points),
            )
        if fl is not None:
            await db.execute(
                "INSERT INTO season_points_fl "
                "(season_id, config_name, session_type, fl_points, fl_position_limit) "
                "VALUES (?, ?, ?, ?, ?)",
                (SEASON_ID, CONFIG, session_type.value, fl[0], fl[1]),
            )

        table = (
            "qualifying_session_results"
            if session_type.is_qualifying
            else "race_session_results"
        )
        for index, (user_id, position, outcome, fastest_lap) in enumerate(drivers, start=1):
            if session_type.is_qualifying:
                await db.execute(
                    f"INSERT INTO {table} "  # noqa: S608
                    "(id, session_result_id, driver_user_id, team_instance_id, "
                    " finishing_position, outcome) VALUES (?, ?, ?, 0, ?, ?)",
                    (index, SESSION_RESULT_ID, user_id, position, outcome),
                )
            else:
                await db.execute(
                    f"INSERT INTO {table} "  # noqa: S608
                    "(id, session_result_id, driver_user_id, team_instance_id, "
                    " finishing_position, outcome, fastest_lap) "
                    "VALUES (?, ?, ?, 0, ?, ?, ?)",
                    (index, SESSION_RESULT_ID, user_id, position, outcome, fastest_lap),
                )
        await db.commit()
    return db_path


async def _apply(db_path: str, session_type: SessionType = SessionType.FEATURE_RACE) -> None:
    await _apply_points_from_config(
        db_path, SESSION_RESULT_ID, SEASON_ID, CONFIG, session_type
    )


async def _points(db_path: str, session_type=SessionType.FEATURE_RACE) -> dict[int, tuple]:
    table = (
        "qualifying_session_results"
        if session_type.is_qualifying
        else "race_session_results"
    )
    columns = (
        "driver_user_id, points_awarded"
        if session_type.is_qualifying
        else "driver_user_id, points_awarded, fastest_lap_bonus"
    )
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT {columns} FROM {table} WHERE session_result_id = ?",  # noqa: S608
            (SESSION_RESULT_ID,),
        )
        rows = await cursor.fetchall()
    if session_type.is_qualifying:
        return {r["driver_user_id"]: (r["points_awarded"],) for r in rows}
    return {
        r["driver_user_id"]: (r["points_awarded"], r["fastest_lap_bonus"]) for r in rows
    }


# ---------------------------------------------------------------------------
# Scoring a race
# ---------------------------------------------------------------------------


async def test_each_position_is_scored_from_the_season_s_table(tmp_path):
    db_path = await _make_db(tmp_path)

    await _apply(db_path)

    points = await _points(db_path)
    assert points[DRIVER_A][0] == 25
    assert points[DRIVER_B][0] == 18


async def test_a_position_outside_the_table_scores_nothing(tmp_path):
    """Most of a grid finishes outside the points, and a missing entry means zero rather
    than an error."""
    db_path = await _make_db(
        tmp_path,
        drivers=((DRIVER_A, 1, "CLASSIFIED", "1:22.000"), (DRIVER_B, 15, "CLASSIFIED", None)),
    )

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_B][0] == 0


async def test_the_fastest_lap_bonus_is_written(tmp_path):
    db_path = await _make_db(tmp_path)

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_A][1] == 1


async def test_a_league_with_no_fastest_lap_bonus_awards_none(tmp_path):
    db_path = await _make_db(tmp_path, fl=None)

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_A][1] == 0


async def test_the_eligibility_limit_is_honoured(tmp_path):
    """A driver holding the quickest lap outside the eligible positions scores their
    finishing points and no bonus."""
    db_path = await _make_db(
        tmp_path,
        drivers=(
            (DRIVER_A, 15, "CLASSIFIED", "1:20.000"),
            (DRIVER_B, 1, "CLASSIFIED", "1:23.000"),
        ),
        entries=((1, 25), (15, 0)),
        fl=(1, 10),
    )

    await _apply(db_path)

    points = await _points(db_path)
    assert points[DRIVER_A][1] == 0
    assert points[DRIVER_B][1] == 0


# ---------------------------------------------------------------------------
# The fastest-lap override
# ---------------------------------------------------------------------------


async def test_a_steward_s_override_decides_the_bonus(tmp_path):
    """It travels with the session, so every recomputation honours it — recomputing
    without it would quietly hand the bonus back to whoever the submitted times say was
    quickest, undoing a steward's decision with nobody told."""
    db_path = await _make_db(tmp_path, fl_override=DRIVER_B)

    await _apply(db_path)

    points = await _points(db_path)
    assert points[DRIVER_A][1] == 0
    assert points[DRIVER_B][1] == 1


async def test_without_an_override_the_times_decide(tmp_path):
    db_path = await _make_db(tmp_path, fl_override=None)

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_A][1] == 1


# ---------------------------------------------------------------------------
# Qualifying
# ---------------------------------------------------------------------------


async def test_qualifying_points_are_written_without_a_bonus(tmp_path):
    """A qualifying row has no fastest-lap column — writing a bonus there would award
    points for a lap nobody set, and the two branches update different columns for
    exactly that reason."""
    db_path = await _make_db(
        tmp_path,
        session_type=SessionType.FEATURE_QUALIFYING,
        drivers=((DRIVER_A, 1, "CLASSIFIED", None), (DRIVER_B, 2, "CLASSIFIED", None)),
        entries=((1, 3), (2, 2)),
        fl=None,
    )

    await _apply(db_path, SessionType.FEATURE_QUALIFYING)

    points = await _points(db_path, SessionType.FEATURE_QUALIFYING)
    assert points[DRIVER_A] == (3,)
    assert points[DRIVER_B] == (2,)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", ["DNS", "DSQ"])
async def test_a_driver_who_did_not_race_scores_nothing(tmp_path, outcome):
    db_path = await _make_db(
        tmp_path,
        drivers=((DRIVER_A, 1, outcome, None), (DRIVER_B, 2, "CLASSIFIED", "1:23.000")),
    )

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_A] == (0, 0)


async def test_a_retirement_scores_no_position_points(tmp_path):
    """DNF keeps its fastest-lap eligibility but loses the finishing points — the two are
    decided separately and a reader collapsing them would score a retirement."""
    db_path = await _make_db(
        tmp_path,
        drivers=((DRIVER_A, 1, "DNF", "1:20.000"), (DRIVER_B, 2, "CLASSIFIED", "1:23.000")),
    )

    await _apply(db_path)

    points = await _points(db_path)
    assert points[DRIVER_A][0] == 0
    assert points[DRIVER_A][1] == 1


# ---------------------------------------------------------------------------
# No configuration
# ---------------------------------------------------------------------------


async def test_a_season_with_no_points_table_leaves_the_rows_alone(tmp_path):
    """The rows already hold zero, so writing a row of zeroes over them would be a write
    for no reason — and one that could overwrite a correction made since."""
    db_path = await _make_db(tmp_path, entries=(), fl=None)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE race_session_results SET points_awarded = 99 WHERE id = 1"
        )
        await db.commit()

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_A][0] == 99


async def test_a_fastest_lap_bonus_alone_is_enough_to_compute(tmp_path):
    """A league that scores no positions but awards the bonus is unusual and legal, and
    the early return has to allow for it."""
    db_path = await _make_db(tmp_path, entries=(), fl=(1, None))

    await _apply(db_path)

    points = await _points(db_path)
    assert points[DRIVER_A][1] == 1


async def test_another_season_s_table_is_not_used(tmp_path):
    """The season's copy is snapshotted at approval precisely so a later edit cannot
    rescore rounds already run."""
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (2, 2, '2027-01-01', 'COMPLETED')"
        )
        await db.execute(
            "INSERT INTO season_points_entries "
            "(season_id, config_name, session_type, position, points) "
            "VALUES (2, ?, ?, 1, 99)",
            (CONFIG, SessionType.FEATURE_RACE.value),
        )
        await db.commit()

    await _apply(db_path)

    assert (await _points(db_path))[DRIVER_A][0] == 25


# ---------------------------------------------------------------------------
# Inside a caller's transaction
# ---------------------------------------------------------------------------


async def test_scoring_inside_a_transaction_writes_nothing_until_it_commits(tmp_path):
    """`replace_round_results` scores the sessions it inserts in the same transaction as the
    delete of the results they replace (issue #210). Scoring that committed on its own would
    let a crash leave the round half-replaced."""
    db_path = await _make_db(tmp_path)

    async with get_connection(db_path) as db:
        written = await _apply_points_in_tx(
            db, SESSION_RESULT_ID, SEASON_ID, CONFIG, SessionType.FEATURE_RACE
        )
        await db.rollback()

    assert written is True
    assert await _points(db_path) == {DRIVER_A: (0, 0), DRIVER_B: (0, 0)}


async def test_scoring_inside_a_transaction_says_when_there_was_nothing_to_score(tmp_path):
    db_path = await _make_db(tmp_path, entries=(), fl=None)

    async with get_connection(db_path) as db:
        written = await _apply_points_in_tx(
            db, SESSION_RESULT_ID, SEASON_ID, CONFIG, SessionType.FEATURE_RACE
        )

    assert written is False
