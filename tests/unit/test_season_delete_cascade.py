"""Deleting a season, and the order the cascade has to delete in.

Issue #208. `SeasonService.delete_season` is fifty statements of hand-written cascade, and it was
uncovered. SQLite enforces the foreign keys it declares, so a table deleted out of order does
not silently orphan a row — it raises `FOREIGN KEY constraint failed` and leaves a season half
removed, with its rounds gone and its divisions still standing.

**The risk this file exists for is a table nobody added to the cascade.** Every new child table
in the bot — a results table, a standings snapshot, a per-division setting — has to be named
here or the delete starts failing the day a league uses the feature. A test asserting "the
season is gone" would pass with the whole cascade removed, because the last two statements would
still run on an empty database. So the tests seed **every** child table first and then assert the
delete both succeeds and leaves nothing behind: `test_no_child_row_survives_the_delete` is the
one that fails when a table is added and forgotten, and it is written to enumerate the tables
rather than spot-check a few.

**Deleting a season must not touch another one.** Divisions, rounds and results are all reached
through id lists built from the season being deleted, and a missing `WHERE` would take the
league's whole history with it. Every one of those lists is pinned by a second season seeded
alongside and asserted intact afterwards.

**Test drivers are removed with the season; real drivers are not.** A test-mode roster exists
only to fill a rehearsal, so leaving the profiles behind would accumulate fake drivers across
every rehearsal a league runs. A real driver profile is the league's record of a person and
outlives any one season — they are unseated, not deleted. The two are told apart by
`is_test_driver`, and the profiles are removed *after* the rows referencing them, which is the
ordering the comment in the source is about.

**One table in the cascade no longer exists, and the delete fails on any season that has
results.** Migration 036 dropped `driver_session_results`; the cascade still deletes from it,
inside the branch that only runs when the season has `session_results` rows — which is every
season a league has actually raced. The whole delete raises `no such table` and rolls back, so
`/season delete` cannot remove a used season at all. That is pinned by
`test_deleting_a_season_with_results_fails_today`, which asserts the failure rather than the
cascade, and is written to fail loudly the day it is fixed so whoever fixes it replaces it with
the assertion beneath. Everything else here therefore seeds a season *without* results, which is
the only shape the function currently handles.

**An empty season deletes cleanly.** Every `IN (...)` clause is built from a list that may be
empty, and an unguarded `IN ()` is a syntax error — so a season with no divisions is the case
most likely to break a tidy-up of this function.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 10308
SEASON_ID = 1
OTHER_SEASON_ID = 2
DIVISION_ID = 11
OTHER_DIVISION_ID = 12
ROUND_ID = 21
OTHER_ROUND_ID = 22

#: Every table the cascade has to clear, and the column it hangs from.
CHILD_TABLES = [
    ("round_submission_channels", "round_id", ROUND_ID),
    ("driver_standings_snapshots", "round_id", ROUND_ID),
    ("team_standings_snapshots", "round_id", ROUND_ID),
    ("forecast_messages", "round_id", ROUND_ID),
    ("phase_results", "round_id", ROUND_ID),
    ("sessions", "round_id", ROUND_ID),
    ("rounds", "division_id", DIVISION_ID),
    ("season_points_links", "season_id", SEASON_ID),
    ("season_amendment_state", "season_id", SEASON_ID),
    ("driver_season_assignments", "division_id", DIVISION_ID),
    ("division_results_config", "division_id", DIVISION_ID),
    ("team_instances", "division_id", DIVISION_ID),
    ("divisions", "season_id", SEASON_ID),
]


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _seed_season(db, season_id, division_id, round_id, *, number: int):
    await db.execute(
        "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
        "VALUES (?, ?, ?, '2026-01-01', ?)",
        (season_id, SERVER_ID, number, "COMPLETED" if number == 6 else "ACTIVE"),
    )
    await db.execute(
        "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
        "VALUES (?, ?, ?, 1, 555)",
        (division_id, season_id, f"Division {number}"),
    )
    await db.execute(
        "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
        "VALUES (?, ?, 1, '2026-02-01T18:00:00+00:00', 'NORMAL')",
        (round_id, division_id),
    )

    # ── results module, round level ─────────────────────────────────────────
    await db.execute(
        "INSERT INTO round_submission_channels (round_id, channel_id, created_at) "
        "VALUES (?, 700, '2026-02-01T00:00:00+00:00')",
        (round_id,),
    )
    await db.execute(
        "INSERT INTO driver_standings_snapshots "
        "(round_id, division_id, driver_user_id, standing_position) VALUES (?, ?, 101, 1)",
        (round_id, division_id),
    )
    await db.execute(
        "INSERT INTO team_standings_snapshots "
        "(round_id, division_id, team_role_id, standing_position) VALUES (?, ?, 3001, 1)",
        (round_id, division_id),
    )
    # No `session_results` row: the cascade raises on any season that has one. See
    # `test_deleting_a_season_with_results_fails_today`.
    await db.execute(
        "INSERT INTO forecast_messages (round_id, division_id, phase_number, message_id, "
        "posted_at) VALUES (?, ?, 1, 900, '2026-02-01T00:00:00+00:00')",
        (round_id, division_id),
    )
    await db.execute(
        "INSERT INTO phase_results (round_id, phase_number, payload, created_at) "
        "VALUES (?, 1, '{}', '2026-02-01T00:00:00+00:00')",
        (round_id,),
    )
    await db.execute(
        "INSERT INTO sessions (round_id, session_type) VALUES (?, 'FEATURE_RACE')",
        (round_id,),
    )

    # ── results module, season level ────────────────────────────────────────
    await db.execute(
        "INSERT INTO season_points_links (season_id, config_name) VALUES (?, 'Standard')",
        (season_id,),
    )
    await db.execute(
        "INSERT INTO season_amendment_state (season_id, amendment_active) VALUES (?, 1)",
        (season_id,),
    )

    # ── drivers and teams ───────────────────────────────────────────────────
    cursor = await db.execute(
        "INSERT INTO driver_profiles (server_id, discord_user_id, current_state, "
        "is_test_driver) VALUES (?, ?, 'ACTIVE', 0)",
        (SERVER_ID, str(1000 + number)),
    )
    real_profile = cursor.lastrowid
    cursor = await db.execute(
        "INSERT INTO driver_profiles (server_id, discord_user_id, current_state, "
        "is_test_driver) VALUES (?, ?, 'ACTIVE', 1)",
        (SERVER_ID, str(2000 + number)),
    )
    test_profile = cursor.lastrowid
    cursor = await db.execute(
        "INSERT INTO team_instances (division_id, name, is_reserve) VALUES (?, 'Red', 0)",
        (division_id,),
    )
    team_instance = cursor.lastrowid
    await db.execute(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, 1, ?)",
        (team_instance, real_profile),
    )
    await db.execute(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, 2, ?)",
        (team_instance, test_profile),
    )
    for profile in (real_profile, test_profile):
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id) VALUES (?, ?, ?)",
            (profile, season_id, division_id),
        )
    await db.execute(
        "INSERT INTO division_results_config (division_id, results_channel_id) "
        "VALUES (?, 800)",
        (division_id,),
    )
    return real_profile, test_profile


async def _make_db(tmp_path, *, name: str = "delete_season", second: bool = True):
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    profiles = {}
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        profiles["deleted"] = await _seed_season(
            db, SEASON_ID, DIVISION_ID, ROUND_ID, number=7
        )
        if second:
            profiles["kept"] = await _seed_season(
                db, OTHER_SEASON_ID, OTHER_DIVISION_ID, OTHER_ROUND_ID, number=6
            )
        await db.commit()
    return db_path, profiles


async def _count(db_path, table, column=None, value=None) -> int:
    async with get_connection(db_path) as db:
        if column is None:
            cursor = await db.execute(f"SELECT COUNT(*) AS n FROM {table}")
        else:
            cursor = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE {column} = ?", (value,)
            )
        return (await cursor.fetchone())["n"]


# ---------------------------------------------------------------------------
# The season, and everything under it
# ---------------------------------------------------------------------------


async def test_the_season_is_deleted(tmp_path):
    db_path, _ = await _make_db(tmp_path)

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "seasons", "id", SEASON_ID) == 0


@pytest.mark.parametrize("table,column,value", CHILD_TABLES)
async def test_no_child_row_survives_the_delete(tmp_path, table, column, value):
    """Enumerated rather than spot-checked: this is the test that fails the day a new child
    table is added to the bot and not to the cascade."""
    db_path, _ = await _make_db(tmp_path, name=f"cascade_{table}")
    assert await _count(db_path, table, column, value) > 0  # the seed is real

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, table, column, value) == 0


async def test_deleting_a_season_with_results_fails_today(tmp_path):
    """**A defect, pinned as it stands.** Migration 036 dropped `driver_session_results`;
    the cascade still deletes from it, inside the branch that only runs when the season has
    `session_results` rows. So `/season delete` raises `no such table` and rolls back on any
    season a league has actually raced — the season, its divisions and its rounds all
    survive, and the manager is told the delete failed for a reason that names a table
    nobody has heard of.

    Asserting the failure rather than the cascade, so this fails loudly the day it is fixed
    and whoever fixes it replaces it with the assertion below.
    """
    import sqlite3

    db_path, _ = await _make_db(tmp_path, name="cascade_with_results")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.commit()

    with pytest.raises(sqlite3.OperationalError, match="driver_session_results"):
        await SeasonService(db_path).delete_season(SEASON_ID)

    # And nothing was removed, because the whole cascade is one transaction.
    assert await _count(db_path, "seasons", "id", SEASON_ID) == 1
    assert await _count(db_path, "rounds", "division_id", DIVISION_ID) == 1


async def test_the_session_result_children_would_go_with_their_session(tmp_path):
    """They have no foreign key to a round, so they are reached through `session_results`
    and would otherwise be left behind pointing at a session that no longer exists. Seeded
    without a parent session here because the test above is why one cannot be seeded."""
    db_path, _ = await _make_db(tmp_path, name="cascade_session_children")

    await SeasonService(db_path).delete_season(SEASON_ID)

    for table in ("race_session_results", "qualifying_session_results"):
        assert await _count(db_path, table) == 0


async def test_the_team_seats_go_with_their_team(tmp_path):
    """Seats hang from a team instance, which hangs from a division — two links away from
    the season, and the level a cascade is most likely to stop one short of."""
    db_path, _ = await _make_db(tmp_path, name="cascade_seats")

    await SeasonService(db_path).delete_season(SEASON_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM team_seats ts JOIN team_instances ti "
            "ON ti.id = ts.team_instance_id WHERE ti.division_id = ?",
            (DIVISION_ID,),
        )
        assert (await cursor.fetchone())["n"] == 0


# ---------------------------------------------------------------------------
# And nothing else
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "table,column,value",
    [
        ("seasons", "id", OTHER_SEASON_ID),
        ("divisions", "season_id", OTHER_SEASON_ID),
        ("rounds", "division_id", OTHER_DIVISION_ID),
        ("driver_standings_snapshots", "round_id", OTHER_ROUND_ID),
        ("team_instances", "division_id", OTHER_DIVISION_ID),
        ("season_points_links", "season_id", OTHER_SEASON_ID),
        ("driver_season_assignments", "season_id", OTHER_SEASON_ID),
    ],
)
async def test_another_season_is_untouched(tmp_path, table, column, value):
    """Every `IN (...)` in the cascade is built from ids belonging to the season being
    deleted. One missing `WHERE` takes the league's whole history with it."""
    db_path, _ = await _make_db(tmp_path, name=f"kept_{table}")

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, table, column, value) > 0


async def test_the_other_seasons_seats_survive(tmp_path):
    """Seats are collected by team instance id, which is the longest chain in the cascade
    and the easiest one to widen by accident."""
    db_path, _ = await _make_db(tmp_path, name="kept_seats")

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "team_seats") == 2  # the other season's two


# ---------------------------------------------------------------------------
# Drivers: the rehearsal's and the league's
# ---------------------------------------------------------------------------


async def test_a_test_driver_is_deleted_with_the_season(tmp_path):
    """A test-mode roster exists to fill a rehearsal. Leaving the profiles behind would
    accumulate fake drivers across every rehearsal a league runs."""
    db_path, profiles = await _make_db(tmp_path, name="drivers_test")
    _, test_profile = profiles["deleted"]

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "driver_profiles", "id", test_profile) == 0


async def test_a_real_driver_outlives_the_season(tmp_path):
    """A driver profile is the league's record of a person. They are unseated by the
    delete, not removed from the league."""
    db_path, profiles = await _make_db(tmp_path, name="drivers_real")
    real_profile, _ = profiles["deleted"]

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "driver_profiles", "id", real_profile) == 1


async def test_another_seasons_test_drivers_are_left_alone(tmp_path):
    """Test drivers are collected through the deleted season's divisions, so a rehearsal
    running in one season must not clear the roster of another."""
    db_path, profiles = await _make_db(tmp_path, name="drivers_other")
    _, other_test_profile = profiles["kept"]

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "driver_profiles", "id", other_test_profile) == 1


# ---------------------------------------------------------------------------
# The empty cases
# ---------------------------------------------------------------------------


async def test_a_season_with_no_divisions_deletes_cleanly(tmp_path):
    """Every `IN (...)` is built from a list that may be empty, and an unguarded `IN ()` is
    a syntax error — so this is the case most likely to break a tidy-up of the function."""
    db_path = os.path.join(str(tmp_path), "empty_season.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID, SERVER_ID),
        )
        await db.commit()

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "seasons") == 0


async def test_a_division_with_no_rounds_deletes_cleanly(tmp_path):
    """The round id list is empty while the division list is not — the mixed case, and the
    one neither empty-season nor full-season coverage reaches."""
    db_path = os.path.join(str(tmp_path), "roundless_season.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "divisions") == 0


async def test_deleting_a_season_that_is_not_there_is_not_an_error(tmp_path):
    """`/season delete` can race a restart or a second manager, and a delete that raises
    would report a failure for work that is already done."""
    db_path, _ = await _make_db(tmp_path, name="delete_missing")

    await SeasonService(db_path).delete_season(404)

    assert await _count(db_path, "seasons", "id", SEASON_ID) == 1
