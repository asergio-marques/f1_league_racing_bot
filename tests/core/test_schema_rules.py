"""The rules the schema itself holds: keys, cascades and uniqueness, an owning module for every
table, every column a module keeps on another's table, and no column stamping its own time from
SQLite's clock but those `KNOWN_CLOCK_DEFAULTS` lists.

Issue #254 squashed the migration chain into one baseline and removed the tests that proved
each historic migration carried data across. Several of those tests also pinned a rule of the
schema as it stands — a cascade, a unique key, a trigger — and those rules are kept here,
against the schema `run_migrations` raises today, so that removing the history took none of
them with it. Three are pinned where their rules are: the trigger recording a driver's
accounts in `test_driver_accounts.py`, the one matching a season's stage to its status in
`test_season_stage.py`, and the index holding one live season in
`test_one_live_season_per_server.py`.
"""
from __future__ import annotations

import os
import re
import sqlite3
from collections import Counter
from pathlib import Path

import pytest

from leaguebot.core.db.database import run_migrations
from tests.repository.test_architecture_rules import (
    KNOWN_CLOCK_DEFAULTS,
    OWN_COLUMNS,
    TABLE_OWNER,
    _SQL_CLOCK,
    _check,
)

LEAGUE = 2540


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "schema.db")
    await run_migrations(path)
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        INSERT INTO server_configs (server_id, interaction_role_id, interaction_channel_id,
                                    log_channel_id) VALUES ({LEAGUE}, 1, 2, 3);
        INSERT INTO seasons (id, start_date, status, season_number)
            VALUES (1, '2026-01-01', 'ACTIVE', 1);
        INSERT INTO divisions (id, season_id, name, mention_role_id) VALUES (10, 1, 'Pro', 5);
        """
    )
    db.commit()
    db.close()
    return path


def _connect(db_path: str) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.execute("PRAGMA foreign_keys = ON")
    return db


def _query(db_path: str, sql: str) -> list[tuple]:
    db = sqlite3.connect(db_path)
    try:
        return db.execute(sql).fetchall()
    finally:
        db.close()


# ── One league ────────────────────────────────────────────────────────────────


async def test_no_server_id_is_left_outside_server_configs(db_path):
    """One bot serves one league (#244): the server lives in `server_configs` and nowhere else."""
    tables = [
        row[0]
        for row in _query(
            db_path,
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' AND name != 'server_configs'",
        )
    ]
    for table in tables:
        columns = [c[1] for c in _query(db_path, f"PRAGMA table_info({table})")]
        assert "server_id" not in columns, table


async def test_the_configuration_can_be_deleted_with_audit_rows_standing(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO audit_entries (actor_id, actor_name, change_type, old_value, new_value, "
        "timestamp) VALUES (42, 'Toto', 'SEASON_APPROVE', 'SETUP', 'ACTIVE', '2026-09-18')"
    )
    db.execute("DELETE FROM server_configs")
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM audit_entries").fetchone() == (1,)
    db.close()


def _single_row_tables() -> list[str]:
    """Every table the schema holds to one row, read from the baseline rather than listed."""
    import asyncio
    import tempfile

    with tempfile.TemporaryDirectory() as scratch:
        path = os.path.join(scratch, "probe.db")
        asyncio.run(run_migrations(path))
        rows = _query(path, "SELECT name, sql FROM sqlite_master WHERE type = 'table'")
    return sorted(name for name, sql in rows if sql and "CHECK (id = 1)" in sql)


@pytest.mark.parametrize("table", _single_row_tables())
async def test_a_single_row_table_refuses_a_second_row(db_path, table):
    """The refusal must be the `id = 1` check itself, so every column that would otherwise
    refuse the row first — NOT NULL with no default — is given a value."""
    db = _connect(db_path)
    required = [
        c[1] for c in db.execute(f"PRAGMA table_info({table})")
        if c[3] and c[4] is None and c[1] != "id"
    ]
    columns = ", ".join(["id", *required])
    values = ", ".join(["2", *("0" for _ in required)])
    with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed: id = 1"):
        db.execute(f"INSERT INTO {table} ({columns}) VALUES ({values})")
    db.close()


# ── Every table is the bot's ──────────────────────────────────────────────────

_SRC = Path(__file__).resolve().parents[2] / "src"


async def test_every_table_the_baseline_creates_is_named_in_src(db_path):
    """A table enters the baseline with the code that reads or writes it, never ahead of it.

    Three stood in the schema with nothing to use them (#427): `signup_division_config`, whose
    only writer went with #248, and `track_records` and `lap_records`, raised for records that
    were never built and whose shape that build will settle (#159). An empty table still reads
    to the next person as something that matters, and once the bot is live, reshaping one costs
    a migration where adding it with its code would have cost nothing.

    The test asks only that some Python file under `src/`, the migrations aside, name the table
    as a whole word. That is a floor, not a proof — a table named only in a docstring passes —
    but it catches what happened here, a table that nothing mentions at all.
    """
    tables = [
        row[0]
        for row in _query(
            db_path,
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'",
        )
    ]
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(_SRC.rglob("*.py"))
        if "migrations" not in path.relative_to(_SRC).parts
    )
    unnamed = sorted(t for t in tables if not re.search(rf"\b{re.escape(t)}\b", source))
    assert unnamed == [], f"tables nothing in src/ names: {unnamed}"


# ── Cascades ──────────────────────────────────────────────────────────────────


async def test_no_module_table_cascades_from_a_core_table(db_path):
    """A module deletes its own rows when core deletes a season, division or round, on the hook
    core calls (architecture.md, "The database"): a key from a module's table to a core table
    takes no action of its own, neither deleting the module's row nor setting its column to NULL.
    A cascade stays within one owner's tables. Each breach is named as ``table.column -> table``.
    """
    db = sqlite3.connect(db_path)
    try:
        acting = sorted(
            f"{table}.{key[3]} -> {key[2]} ON DELETE {key[6]}"
            for table in _built_tables(db)
            if TABLE_OWNER[table] != "core"
            for key in db.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
            if TABLE_OWNER[key[2]] == "core" and key[6] != "NO ACTION"
        )
    finally:
        db.close()
    assert acting == []


async def test_a_points_configuration_takes_its_points_with_it(db_path):
    db = _connect(db_path)
    db.executescript(
        """
        PRAGMA foreign_keys = ON;
        INSERT INTO points_config_store (id, config_name) VALUES (7, 'Standard');
        INSERT INTO points_config_entries (config_id, session_type, position, points)
            VALUES (7, 'FEATURE_RACE', 1, 25);
        INSERT INTO points_config_fl (config_id, session_type, fl_points, fl_position_limit)
            VALUES (7, 'FEATURE_RACE', 1, 10);
        DELETE FROM points_config_store WHERE id = 7;
        """
    )
    db.commit()
    db.close()

    assert _query(db_path, "SELECT * FROM points_config_entries") == []
    assert _query(db_path, "SELECT * FROM points_config_fl") == []


async def test_a_signup_window_s_deletion_clears_its_records_window(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO signup_windows (opened_at, id, season_id, selected_tracks_json) VALUES ('2026-01-01T00:00:00+00:00', 4, 1, '[\"1\"]')"
    )
    db.execute(
        "INSERT INTO signup_records (created_at, updated_at, id, season_id, window_id, discord_user_id, platform) "
        "VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', 9, 1, 4, '11', 'Steam')"
    )
    db.execute("DELETE FROM signup_windows WHERE id = 4")
    db.commit()
    db.close()

    assert _query(db_path, "SELECT id, window_id FROM signup_records") == [(9, None)]


# ── Uniqueness ────────────────────────────────────────────────────────────────


async def test_a_points_configuration_name_is_unique(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO points_config_store (config_name) VALUES ('Standard')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO points_config_store (config_name) VALUES ('Standard')")
    db.close()


async def test_an_account_holds_one_signup_wizard(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO signup_wizard_records (created_at, last_activity_at, discord_user_id) VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '11')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO signup_wizard_records (created_at, last_activity_at, discord_user_id) VALUES ('2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00', '11')")
    db.close()


async def test_a_second_open_window_is_refused(db_path):
    """Season 1 has a signup window that closed and one still open. A second window opened
    beside the open one is refused by the schema: signup has one open window at a time, so the
    close time and the close are written to that window alone."""
    db = _connect(db_path)
    db.execute(
        "INSERT INTO signup_windows (season_id, opened_at, closed_at) "
        "VALUES (1, '2026-01-01T10:00:00+00:00', '2026-01-02T10:00:00+00:00')"
    )
    db.execute(
        "INSERT INTO signup_windows (season_id, opened_at) VALUES (1, '2026-01-03T10:00:00+00:00')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO signup_windows (season_id, opened_at) "
            "VALUES (1, '2026-01-04T10:00:00+00:00')"
        )
    db.close()


@pytest.mark.xfail(strict=True, reason="#286: signup_records still carries updated_at, which "
                   "nothing reads")
async def test_a_signup_record_keeps_when_it_was_sent_in_and_no_time_of_its_last_change(db_path):
    """The schema `run_migrations` raises keeps on each signup record the moment it was sent in
    (`created_at`), which the unassigned listings order by, and no time of its last change
    (`updated_at`), which nothing reads."""
    db = _connect(db_path)
    columns = {row[1] for row in db.execute("PRAGMA table_info(signup_records)")}
    db.close()
    assert "created_at" in columns
    assert "updated_at" not in columns


async def test_a_team_name_is_unique_in_the_league(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO default_teams (name, full_name) VALUES ('Alpha', 'Alpha')")
    db.execute("INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES ('Alpha', 1, '2031-03-04T05:06:07+00:00')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO default_teams (name, full_name) VALUES ('Alpha', 'Alpha')")
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO team_role_configs (team_name, role_id, updated_at) VALUES ('Alpha', 2, '2031-03-04T05:06:07+00:00')")
    db.close()


async def test_a_second_division_of_the_same_name_is_refused_by_the_schema(db_path):
    """The core specification's divisions: a division's name is unique within its season, without
    regard to case. Season 1 holds Pro. An insert of a second Pro into season 1, or of PRO, is
    refused by the schema itself, whatever a command checked first; Pro in season 2 is accepted.
    SQLite folds the case of ASCII letters alone: a name differing in an accented letter's case is
    the command's check to refuse (`test_division_add_duplicate.py`)."""
    db = _connect(db_path)
    for name in ("Pro", "PRO"):
        with pytest.raises(sqlite3.IntegrityError):
            db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id) VALUES (1, ?, 6)", (name,)
            )
    db.execute(
        "INSERT INTO seasons (id, start_date, status, season_number) "
        "VALUES (2, '2027-01-01', 'COMPLETED', 2)"
    )
    db.execute("INSERT INTO divisions (season_id, name, mention_role_id) VALUES (2, 'Pro', 6)")
    db.close()


async def test_an_account_belongs_to_one_driver_across_the_league(db_path):
    db = _connect(db_path)
    db.execute(
        "INSERT INTO driver_profiles (discord_user_id, current_state) VALUES ('2222', 'UNASSIGNED')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES ('2222', 'UNASSIGNED')"
        )
    db.close()


# ── Image module defaults ─────────────────────────────────────────────────────


async def test_the_image_directory_defaults_match_the_constants(db_path):
    """Written twice — as column defaults in SQL and in `ASSET_DIRECTORIES` — and must not drift:
    a row created by `create_with_defaults` carries the SQL defaults, and the rest of the module
    reads the constants."""
    from leaguebot.image.models.image_constants import ASSET_DIRECTORIES

    db = _connect(db_path)
    db.execute("INSERT INTO image_config (id) VALUES (1)")
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT * FROM image_config").fetchone()
    db.close()

    for column, (_command, default, _packaged) in ASSET_DIRECTORIES.items():
        assert row[column] == default, column
        assert default.startswith("resources/league/"), column


async def test_the_template_directory_stays_with_the_packaged_templates(db_path):
    """Templates have no packaged second tier, so a fresh install that pointed it at the
    league's empty folder could render nothing at all."""
    db = _connect(db_path)
    db.execute("INSERT INTO image_config (id) VALUES (1)")
    assert db.execute("SELECT template_directory FROM image_config").fetchone() == (
        "resources/defaults/templates",
    )
    db.close()


async def test_per_tier_colours_start_switched_off(db_path):
    db = _connect(db_path)
    db.execute("INSERT INTO image_config (id) VALUES (1)")
    assert db.execute("SELECT per_tier_colour_enabled FROM image_config").fetchone() == (0,)
    db.close()


async def test_a_tier_holds_one_colour_per_slot(db_path):
    """The primary key is what makes a second colour replace the first rather than add to it."""
    db = _connect(db_path)
    for colour in ("#111111", "#222222"):
        db.execute(
            "INSERT INTO image_tier_colour (division_slug, slot, colour) "
            "VALUES ('division_1', 'accent', ?) "
            "ON CONFLICT(division_slug, slot) DO UPDATE SET colour = excluded.colour",
            (colour,),
        )
    db.execute("INSERT INTO image_tier_colour VALUES ('division_2', 'accent', '#A78BFA')")
    db.commit()
    rows = db.execute(
        "SELECT division_slug, slot, colour FROM image_tier_colour ORDER BY division_slug"
    ).fetchall()
    db.close()

    assert rows == [("division_1", "accent", "#222222"), ("division_2", "accent", "#A78BFA")]


# ── The change queue (#439) ───────────────────────────────────────────────────

def _check_list(db_path: str, table: str, column: str) -> set[str]:
    """The values a closed `CHECK (<column> IN (...))` on *table* allows, read from the schema."""
    rows = _query(db_path, f"SELECT sql FROM sqlite_master WHERE type = 'table' AND name = '{table}'")
    assert rows, f"no table {table}"
    found = re.search(rf"CHECK\s*\(\s*{column}\s+IN\s*\(([^)]*)\)", rows[0][0])
    assert found, f"no closed CHECK list on {table}.{column}"
    return set(re.findall(r"'([^']*)'", found.group(1)))


def _queue_change(db: sqlite3.Connection, state: str = "QUEUED", kind: str = "dummy") -> int:
    cursor = db.execute(
        "INSERT INTO queued_changes (kind, dedup_key, origin, state, what) "
        "VALUES (?, ?, 'MEMBER', ?, '`/dummy`')",
        (kind, kind, state),
    )
    return int(cursor.lastrowid or 0)


async def test_a_change_s_states_match_the_enum(db_path):
    """Written twice, in the CHECK list and in `ChangeState`, and must not drift."""
    from leaguebot.core.models.change import ChangeState

    assert _check_list(db_path, "queued_changes", "state") == {s.value for s in ChangeState}


async def test_a_change_s_origins_match_the_enum(db_path):
    from leaguebot.core.models.change import ChangeOrigin

    assert _check_list(db_path, "queued_changes", "origin") == {o.value for o in ChangeOrigin}


async def test_a_division_s_and_a_round_s_statuses_match_their_enums(db_path):
    """Each written twice, in the CHECK list and in its enum, and must not drift."""
    from leaguebot.core.models.division import DivisionStatus
    from leaguebot.core.models.round import RoundStatus

    assert _check_list(db_path, "divisions", "status") == {s.value for s in DivisionStatus}
    assert _check_list(db_path, "rounds", "status") == {s.value for s in RoundStatus}


async def test_the_queue_holds_one_running_change(db_path):
    """One change runs at a time on a lane: an identity rule, so the schema holds it, not the
    worker. Here every change is on the league lane, which a change naming no lane is on."""
    db = _connect(db_path)
    _queue_change(db, "RUNNING")
    _queue_change(db, "DONE")
    _queue_change(db, "QUEUED")
    with pytest.raises(sqlite3.IntegrityError):
        _queue_change(db, "RUNNING")
    db.close()


async def test_the_queue_holds_one_running_change_per_lane(db_path):
    """Each lane runs one change at a time: a second RUNNING change on channel 7's lane is
    refused, while one RUNNING on the league lane and one on channel 8's stand beside it."""
    db = _connect(db_path)

    def _running(lane: str) -> None:
        db.execute(
            "INSERT INTO queued_changes (kind, dedup_key, origin, state, what, lane) "
            "VALUES ('dummy', ?, 'MEMBER', 'RUNNING', '`/dummy`', ?)",
            (lane, lane),
        )

    _running("league")
    _running("channel:7")
    _running("channel:8")
    with pytest.raises(sqlite3.IntegrityError):
        _running("channel:7")
    db.close()


async def test_a_change_s_steps_go_with_it(db_path):
    db = _connect(db_path)
    change_id = _queue_change(db)
    db.executemany(
        "INSERT INTO queued_change_steps (change_id, position, name) VALUES (?, ?, ?)",
        [(change_id, 0, "first"), (change_id, 1, "second")],
    )
    db.execute("DELETE FROM queued_changes WHERE id = ?", (change_id,))
    db.commit()
    assert db.execute("SELECT COUNT(*) FROM queued_change_steps").fetchone() == (0,)
    db.close()


# ── Which module owns each table, and the clock in the schema ─────────────────
#
# These read the schema `run_migrations` builds, never a migration file's text, so a table or a
# column a later migration adds, renames or drops is seen as it stands (architecture.md, "The
# database"). The maps they hold the schema to are the rules file's, which reads source and
# imports no bot code.


def _built_tables(db: sqlite3.Connection) -> list[str]:
    """The tables of the built schema, in name order, SQLite's own and the versions' table aside."""
    return [
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "AND name != 'schema_migrations' ORDER BY name"
        )
    ]


def _columns(db: sqlite3.Connection, table: str) -> list[tuple]:
    """``PRAGMA table_info`` of *table*: each column as (cid, name, type, notnull, default, pk)."""
    return db.execute(f'PRAGMA table_info("{table}")').fetchall()


def _clock_defaults(db: sqlite3.Connection) -> list[str]:
    """Each ``table.column`` of the schema in *db* whose default is SQLite's clock, tables in name
    order and columns in the order they are declared. SQLite gives each default as its own text
    (``CURRENT_TIMESTAMP``, ``datetime('now')``), which is read as a statement's would be."""
    return [
        f"{table}.{column[1]}"
        for table in _built_tables(db)
        for column in _columns(db, table)
        if column[4] is not None and _SQL_CLOCK.search(column[4])
    ]


async def test_every_table_has_an_owner(db_path):
    """A table added to the schema is given its owner in `TABLE_OWNER`, so the rule on writes in
    `test_architecture_rules.py` covers it."""
    db = sqlite3.connect(db_path)
    try:
        assert set(TABLE_OWNER) == set(_built_tables(db))
    finally:
        db.close()


async def test_every_module_column_is_in_the_schema(db_path):
    """A module's own column that the schema renamed or dropped would drop out of the rule on
    writes without a word."""
    db = sqlite3.connect(db_path)
    try:
        missing = sorted(
            f"{table}.{column}"
            for columns_of in OWN_COLUMNS.values()
            for table, columns in columns_of.items()
            for column in columns
            if column not in {row[1] for row in _columns(db, table)}
        )
    finally:
        db.close()
    assert missing == []


async def test_no_column_defaults_to_sqlites_clock(db_path):
    """A stored time is written from the `now` handed in (architecture.md, "The time is always
    passed in"), and a column defaulting to SQLite's clock stamps one as `datetime('now')` in a
    statement does. A breach is keyed by ``("schema", "table.column")`` and listed in the rules
    file's `KNOWN_CLOCK_DEFAULTS` with the issue that will fix it."""
    db = sqlite3.connect(db_path)
    try:
        found = Counter(("schema", column) for column in _clock_defaults(db))
    finally:
        db.close()
    _check("no statement reads SQLite's clock", found, KNOWN_CLOCK_DEFAULTS)


def test_the_clock_default_scan_reads_each_column_that_defaults_to_the_clock():
    """A column defaulting to the clock is named by its table and column, in either spelling and
    wherever it sits in the text (its own line, after a leading comma, a one-line table); a default
    that is not the clock is not."""
    schema = (
        'CREATE TABLE "a" (\n'
        "    id INTEGER PRIMARY KEY,\n"
        "    made TEXT NOT NULL DEFAULT (datetime('now')),\n"
        "    kept TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
        "    zero INTEGER NOT NULL DEFAULT 0\n"
        ");\n"
        "CREATE TABLE b (\n"
        "    stamped TEXT DEFAULT (date('now'))\n"
        ");\n"
        "CREATE TABLE c (id INTEGER, made_at TEXT DEFAULT CURRENT_TIMESTAMP);\n"
        "CREATE TABLE d (\n"
        "    id INTEGER PRIMARY KEY, -- the key, used for joins\n"
        "    note TEXT\n"
        ", stamped_at TEXT NOT NULL DEFAULT (datetime('now')), n INTEGER NOT NULL DEFAULT 0\n"
        ");\n"
    )
    db = sqlite3.connect(":memory:")
    try:
        db.executescript(schema)
        found = _clock_defaults(db)
    finally:
        db.close()

    assert found == ["a.made", "a.kept", "b.stamped", "c.made_at", "d.stamped_at"]
