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
`is_test_driver`, and the test drivers are deleted by `delete_driver_profiles`, the route test
mode itself takes, which lets go of every row holding them wherever it stands and keeps their
history (#268).

**Results are seeded too, though no league can reach a season that has them.** The only caller,
`/season abort`, deletes a season still in setup, which has no results, and the method refuses
any other season (issue #153). But a cascade statement fails only on the rows that reach it, so
a seed without results cannot see one that is broken: the delete once raised `no such table` on
any season carrying a `session_results` row, because it still named a table the schema had
dropped (issue #214). So every season carries a qualifying and a race result, and
`test_a_season_with_results_is_deleted` is named for the defect.

**Only a season in setup can be deleted.** A season's number is committed once it leaves SETUP,
and removing it would leave a gap in the league's history (issue #153). So the season deleted
here is seeded in SETUP, whatever it carries beneath it, and
`test_a_season_whose_number_is_committed_is_refused` pins the refusal.

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
    ("session_results", "round_id", ROUND_ID),
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
        "INSERT INTO seasons (id, season_number, start_date, status) "
        "VALUES (?, ?, '2026-01-01', ?)",
        (season_id, number, "COMPLETED" if number == 6 else "SETUP"),
    )
    await db.execute(
        "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
        "VALUES (?, ?, ?, 1, 555)",
        (division_id, season_id, f"Division {number}"),
    )
    # The division's team, created first so its results can name it (#375).
    cursor = await db.execute(
        "INSERT INTO team_instances (division_id, name, full_name, is_reserve) VALUES (?, 'Red', 'Red', 0)",
        (division_id,),
    )
    team_instance = cursor.lastrowid
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
        "(round_id, division_id, team_instance_id, standing_position) VALUES (?, ?, ?, 1)",
        (round_id, division_id, team_instance),
    )
    for session_type in ("FULL_QUALIFYING", "FULL_RACE"):
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, ?, 'ACTIVE')",
            (round_id, division_id, session_type),
        )
        child = (
            "qualifying_session_results"
            if session_type == "FULL_QUALIFYING"
            else "race_session_results"
        )
        await db.execute(
            f"INSERT INTO {child} (session_result_id, driver_user_id, team_instance_id, "
            "finishing_position) VALUES (?, 101, ?, 1)",
            (cursor.lastrowid, team_instance),
        )
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
        "INSERT INTO driver_profiles (discord_user_id, current_state, "
        "is_test_driver) VALUES (?, 'ACTIVE', 0)",
        (str(1000 + number),),
    )
    real_profile = cursor.lastrowid
    cursor = await db.execute(
        "INSERT INTO driver_profiles (discord_user_id, current_state, "
        "is_test_driver) VALUES (?, 'ACTIVE', 1)",
        (str(2000 + number),),
    )
    test_profile = cursor.lastrowid
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


async def test_a_season_with_results_is_deleted(tmp_path):
    """Issue #214. The cascade once deleted from `driver_session_results`, a table the schema
    dropped, inside the branch that runs only when the season has `session_results` rows — so
    the whole delete raised `no such table` and rolled back on any season carrying a result.
    Every season seeded here carries results, so this is the plain case, named for the defect
    it would bring back."""
    db_path, _ = await _make_db(tmp_path, name="cascade_with_results")
    assert await _count(db_path, "session_results", "round_id", ROUND_ID) > 0  # the seed is real

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "seasons", "id", SEASON_ID) == 0
    assert await _count(db_path, "session_results", "round_id", ROUND_ID) == 0


async def test_the_session_result_children_go_with_their_session(tmp_path):
    """They hang from `session_results`, not a round, so they sit one link further from the
    season than anything `CHILD_TABLES` counts by round. Counted by the deleted season's own
    session ids, because the other season's results survive."""
    db_path, _ = await _make_db(tmp_path, name="cascade_session_children")
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM session_results WHERE round_id = ?", (ROUND_ID,)
        )
        session_ids = [r["id"] for r in await cursor.fetchall()]
    assert session_ids  # the seed is real

    await SeasonService(db_path).delete_season(SEASON_ID)

    ph = ",".join("?" * len(session_ids))
    async with get_connection(db_path) as db:
        for table in ("race_session_results", "qualifying_session_results"):
            cursor = await db.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE session_result_id IN ({ph})",
                session_ids,
            )
            assert (await cursor.fetchone())["n"] == 0, table


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
        ("session_results", "round_id", OTHER_ROUND_ID),
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


async def test_a_test_driver_holding_a_row_beyond_the_season_is_deleted_all_the_same(tmp_path):
    """The season's own rows go with its rounds, but not a row elsewhere naming the driver.
    No flow puts one there, the abort having switched test mode off and deleted every fake
    driver first, so it stands in for a table that comes to hold a driver. The deletion once
    written here by hand was refused by a foreign key (#268)."""
    db_path, profiles = await _make_db(tmp_path, name="drivers_beyond")
    _, test_profile = profiles["deleted"]
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id) "
            "VALUES (?, ?, ?)",
            (OTHER_ROUND_ID, OTHER_DIVISION_ID, test_profile),
        )
        await db.commit()

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "seasons", "id", SEASON_ID) == 0
    assert await _count(db_path, "driver_profiles", "id", test_profile) == 0
    assert await _count(db_path, "driver_round_attendance") == 0


async def test_a_test_drivers_history_is_kept_by_identifier(tmp_path):
    """As switching test mode off keeps it: a driver created again under the same identifier
    holds it as their own (#220)."""
    db_path, profiles = await _make_db(tmp_path, name="drivers_history")
    _, test_profile = profiles["deleted"]
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_history_entries (discord_user_id, driver_profile_id, "
            "season_number, division_name) VALUES ('2007', ?, 5, 'Division 5')",
            (test_profile,),
        )
        await db.commit()

    await SeasonService(db_path).delete_season(SEASON_ID)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, driver_profile_id FROM driver_history_entries"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [("2007", None)]


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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
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
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'SETUP')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.commit()

    await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "divisions") == 0


@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED", "CANCELLED"])
async def test_a_season_whose_number_is_committed_is_refused(tmp_path, status):
    """Its number is how the league names that season, and nothing is removed."""
    db_path, _ = await _make_db(tmp_path, name=f"refuse_{status}")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE seasons SET status = ? WHERE id = ?", (status, SEASON_ID))
        await db.commit()

    with pytest.raises(ValueError, match="committed"):
        await SeasonService(db_path).delete_season(SEASON_ID)

    assert await _count(db_path, "seasons", "id", SEASON_ID) == 1
    assert await _count(db_path, "divisions", "season_id", SEASON_ID) == 1
    assert await _count(db_path, "rounds", "division_id", DIVISION_ID) == 1


async def test_deleting_a_season_that_is_not_there_is_not_an_error(tmp_path):
    """A delete racing another that already removed the season must not raise, or it would
    report a failure for work that is already done."""
    db_path, _ = await _make_db(tmp_path, name="delete_missing")

    await SeasonService(db_path).delete_season(404)

    assert await _count(db_path, "seasons", "id", SEASON_ID) == 1
