"""Unit tests for rsvp_service — T024.

Covers:
  1. Distribution priority ordering (tier 1 NO_RSVP > tier 2 DECLINED > tier 3 TENTATIVE)
  2. Tie-breaking: fewest accepted; standings position; alphabetical team name
  3. accepted_at timestamp ordering for reserves (first-accepted = highest priority)
  4. Standby classification (reserves beyond available vacancies)
  5. No-op when no accepted reserves
  6. AttendanceService CRUD round-trips (bulk_insert, upsert, get, embed message)
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.rsvp_service import run_reserve_distribution


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_FUTURE = datetime(2025, 6, 8, 14, 0, 0, tzinfo=timezone.utc)


async def _make_db(tmp_path) -> str:
    """Create a minimal SQLite DB with all tables required by run_reserve_distribution."""
    path = str(tmp_path / "rsvp_test.db")
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.executescript(
            """
            CREATE TABLE seasons (
                id            INTEGER PRIMARY KEY,
                server_id     INTEGER NOT NULL DEFAULT 1,
                season_number INTEGER NOT NULL DEFAULT 1,
                status        TEXT    NOT NULL DEFAULT 'ACTIVE'
            );

            CREATE TABLE divisions (
                id        INTEGER PRIMARY KEY,
                season_id INTEGER NOT NULL,
                name      TEXT    NOT NULL DEFAULT 'Div1'
            );

            CREATE TABLE rounds (
                id              INTEGER PRIMARY KEY,
                division_id     INTEGER NOT NULL,
                round_number    INTEGER NOT NULL,
                format          TEXT    NOT NULL DEFAULT 'NORMAL',
                track_name      TEXT,
                scheduled_at    TEXT    NOT NULL
            );

            CREATE TABLE driver_profiles (
                id                INTEGER PRIMARY KEY,
                server_id         INTEGER NOT NULL DEFAULT 1,
                discord_user_id   TEXT    NOT NULL DEFAULT '0',
                test_display_name TEXT,
                is_test_driver    INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE team_instances (
                id          INTEGER PRIMARY KEY,
                division_id INTEGER NOT NULL,
                name        TEXT    NOT NULL,
                is_reserve  INTEGER NOT NULL DEFAULT 0,
                max_seats   INTEGER NOT NULL DEFAULT 2
            );

            CREATE TABLE team_seats (
                id                INTEGER PRIMARY KEY,
                team_instance_id  INTEGER NOT NULL,
                driver_profile_id INTEGER NOT NULL
            );

            CREATE TABLE driver_round_attendance (
                id                INTEGER PRIMARY KEY,
                round_id          INTEGER NOT NULL,
                division_id       INTEGER NOT NULL,
                driver_profile_id INTEGER NOT NULL,
                rsvp_status       TEXT    NOT NULL DEFAULT 'NO_RSVP',
                accepted_at       TEXT,
                assigned_team_id  INTEGER,
                is_standby        INTEGER NOT NULL DEFAULT 0,
                attended          INTEGER,
                points_awarded    INTEGER,
                total_points_after INTEGER,
                UNIQUE (round_id, division_id, driver_profile_id)
            );

            CREATE TABLE team_standings_snapshots (
                id                INTEGER PRIMARY KEY,
                team_role_id      INTEGER NOT NULL,
                round_id          INTEGER NOT NULL,
                division_id       INTEGER NOT NULL DEFAULT 10,
                standing_position INTEGER
            );

            CREATE TABLE team_role_configs (
                id          INTEGER PRIMARY KEY,
                server_id   INTEGER NOT NULL,
                team_name   TEXT    NOT NULL,
                role_id     INTEGER NOT NULL,
                UNIQUE(server_id, team_name)
            );
            """
        )
        await db.commit()
    return path


def _make_bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    return bot


async def _seed_base(db_path: str) -> None:
    """Insert one season, one division, and one round."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("INSERT INTO seasons (id, season_number) VALUES (1, 1)")
        await db.execute("INSERT INTO divisions (id, season_id) VALUES (10, 1)")
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at) VALUES (42, 10, 3, ?)",
            (_FUTURE.isoformat(),),
        )
        await db.commit()


async def _insert_driver(db: aiosqlite.Connection, dp_id: int, name: str) -> None:
    await db.execute(
        "INSERT INTO driver_profiles (id, test_display_name) VALUES (?, ?)",
        (dp_id, name),
    )


async def _insert_team(db: aiosqlite.Connection, team_id: int, div_id: int, name: str, is_reserve: int = 0) -> None:
    await db.execute(
        "INSERT INTO team_instances (id, division_id, name, is_reserve, max_seats) VALUES (?, ?, ?, ?, 2)",
        (team_id, div_id, name, is_reserve),
    )


async def _add_driver_to_team(db: aiosqlite.Connection, team_id: int, dp_id: int) -> None:
    await db.execute(
        "INSERT INTO team_seats (team_instance_id, driver_profile_id) VALUES (?, ?)",
        (team_id, dp_id),
    )


async def _insert_dra(
    db: aiosqlite.Connection,
    round_id: int,
    div_id: int,
    dp_id: int,
    status: str,
    accepted_at: str | None = None,
) -> int:
    cur = await db.execute(
        "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, rsvp_status, accepted_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (round_id, div_id, dp_id, status, accepted_at),
    )
    return cur.lastrowid  # type: ignore[return-value]


async def _get_dra(db: aiosqlite.Connection, dra_id: int) -> aiosqlite.Row:
    cur = await db.execute("SELECT * FROM driver_round_attendance WHERE id = ?", (dra_id,))
    row = await cur.fetchone()
    assert row is not None, f"DRA row {dra_id} not found"
    return row


# ---------------------------------------------------------------------------
# 1. No-op when no accepted reserves
# ---------------------------------------------------------------------------


class TestNoAcceptedReserves:
    @pytest.mark.asyncio
    async def test_no_assignments_made(self, tmp_path):
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await _insert_driver(db, 1, "Alice")
            await _insert_driver(db, 2, "Bob (reserve)")
            await _insert_team(db, 101, 10, "Alpha")
            await _insert_team(db, 102, 10, "Reserve", is_reserve=1)
            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            dra1 = await _insert_dra(db, 42, 10, 1, "NO_RSVP")
            dra2 = await _insert_dra(db, 42, 10, 2, "TENTATIVE")  # reserve but not ACCEPTED
            await db.commit()

        bot = _make_bot(db_path)
        # Should return without writing anything
        await run_reserve_distribution(42, 10, bot)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            row1 = await _get_dra(db, dra1)
            row2 = await _get_dra(db, dra2)

        assert row1["assigned_team_id"] is None
        assert row2["assigned_team_id"] is None
        assert row2["is_standby"] == 0


# ---------------------------------------------------------------------------
# 2. Priority ordering: tier 1 (NO_RSVP) fills before tier 2 (DECLINED)
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_tier1_team_gets_reserve_first(self, tmp_path):
        """Team with a NO_RSVP driver gets the reserve before a team with only DECLINED drivers."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            # Regular drivers
            await _insert_driver(db, 1, "Driver1 (NO_RSVP team)")
            await _insert_driver(db, 2, "Driver2 (DECLINED team)")
            # Reserve driver
            await _insert_driver(db, 3, "ReserveDriver")

            await _insert_team(db, 101, 10, "TeamA_NoRsvp")   # should get tier 1 priority
            await _insert_team(db, 102, 10, "TeamB_Declined")  # tier 2 priority
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 103, 3)

            await _insert_dra(db, 42, 10, 1, "NO_RSVP")
            await _insert_dra(db, 42, 10, 2, "DECLINED")
            reserve_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T10:00:00+00:00")
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            reserve_row = await _get_dra(db, reserve_dra)

        # Reserve should be assigned to TeamA_NoRsvp (tier 1) not TeamB_Declined (tier 2)
        assert reserve_row["assigned_team_id"] == 101
        assert reserve_row["is_standby"] == 0


# ---------------------------------------------------------------------------
# 3. Tie-breaking: standings position
# ---------------------------------------------------------------------------


class TestTiebreakerStandings:
    @pytest.mark.asyncio
    async def test_lower_standing_position_wins(self, tmp_path):
        """A team at a lower standings position (= better rank) should receive the reserve first."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        # Need a prior round to anchor the standings snapshot
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at) VALUES (41, 10, 2, ?)",
                (datetime(2025, 5, 25, tzinfo=timezone.utc).isoformat(),),
            )

            await _insert_driver(db, 1, "D1")
            await _insert_driver(db, 2, "D2")
            await _insert_driver(db, 3, "ReserveR")

            await _insert_team(db, 101, 10, "TeamBeta")   # standing_pos=2
            await _insert_team(db, 102, 10, "TeamAlpha")  # standing_pos=1 — should win
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 103, 3)

            # Both teams have a DECLINED driver (same tier 2)
            await _insert_dra(db, 42, 10, 1, "DECLINED")
            await _insert_dra(db, 42, 10, 2, "DECLINED")
            reserve_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T10:00:00+00:00")

            # Standings: TeamAlpha=pos1, TeamBeta=pos2 (snapshot anchored at round 41)
            await db.execute(
                "INSERT INTO team_role_configs (server_id, team_name, role_id) VALUES (1, 'TeamAlpha', 1002)"
            )
            await db.execute(
                "INSERT INTO team_role_configs (server_id, team_name, role_id) VALUES (1, 'TeamBeta', 1001)"
            )
            await db.execute(
                "INSERT INTO team_standings_snapshots (team_role_id, round_id, standing_position) VALUES (1002, 41, 1)"
            )
            await db.execute(
                "INSERT INTO team_standings_snapshots (team_role_id, round_id, standing_position) VALUES (1001, 41, 2)"
            )
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            reserve_row = await _get_dra(db, reserve_dra)

        assert reserve_row["assigned_team_id"] == 102  # TeamAlpha (pos 1)


# ---------------------------------------------------------------------------
# 4. Tie-breaking: alphabetical team name fallback
# ---------------------------------------------------------------------------


class TestTiebreakerAlphabetical:
    @pytest.mark.asyncio
    async def test_alphabetical_fallback(self, tmp_path):
        """When tier and standings are identical, team name alphabetical order wins."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await _insert_driver(db, 1, "D1")
            await _insert_driver(db, 2, "D2")
            await _insert_driver(db, 3, "ReserveR")

            await _insert_team(db, 101, 10, "Zeta")   # alphabetically last
            await _insert_team(db, 102, 10, "Alpha")   # alphabetically first — should win
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 103, 3)

            await _insert_dra(db, 42, 10, 1, "DECLINED")
            await _insert_dra(db, 42, 10, 2, "DECLINED")
            reserve_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T10:00:00+00:00")
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            reserve_row = await _get_dra(db, reserve_dra)

        assert reserve_row["assigned_team_id"] == 102  # Alpha


# ---------------------------------------------------------------------------
# 5. accepted_at timestamp ordering
# ---------------------------------------------------------------------------


class TestAcceptedAtOrdering:
    @pytest.mark.asyncio
    async def test_earlier_accepted_gets_higher_priority_team(self, tmp_path):
        """Reserve who accepted earlier should be assigned to the higher-priority team."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        # Need a prior round for standings
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, scheduled_at) VALUES (41, 10, 2, ?)",
                (datetime(2025, 5, 25, tzinfo=timezone.utc).isoformat(),),
            )

            await _insert_driver(db, 1, "D1")
            await _insert_driver(db, 2, "D2")
            await _insert_driver(db, 3, "Reserve_Early")   # accepted first
            await _insert_driver(db, 4, "Reserve_Late")    # accepted later

            await _insert_team(db, 101, 10, "TeamTop")    # standing pos=1 (best vacancy)
            await _insert_team(db, 102, 10, "TeamBottom") # standing pos=2
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 103, 3)
            await _add_driver_to_team(db, 103, 4)

            # Both teams DECLINED (same tier 2)
            await _insert_dra(db, 42, 10, 1, "DECLINED")
            await _insert_dra(db, 42, 10, 2, "DECLINED")
            early_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T09:00:00+00:00")
            late_dra  = await _insert_dra(db, 42, 10, 4, "ACCEPTED", "2025-06-01T11:00:00+00:00")

            await db.execute(
                "INSERT INTO team_role_configs (server_id, team_name, role_id) VALUES (1, 'TeamTop', 1001)"
            )
            await db.execute(
                "INSERT INTO team_role_configs (server_id, team_name, role_id) VALUES (1, 'TeamBottom', 1002)"
            )
            await db.execute(
                "INSERT INTO team_standings_snapshots (team_role_id, round_id, standing_position) VALUES (1001, 41, 1)"
            )
            await db.execute(
                "INSERT INTO team_standings_snapshots (team_role_id, round_id, standing_position) VALUES (1002, 41, 2)"
            )
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            early_row = await _get_dra(db, early_dra)
            late_row  = await _get_dra(db, late_dra)

        assert early_row["assigned_team_id"] == 101  # TeamTop — best available
        assert late_row["assigned_team_id"]  == 102  # TeamBottom


# ---------------------------------------------------------------------------
# 6. Standby classification
# ---------------------------------------------------------------------------


class TestStandbyClassification:
    @pytest.mark.asyncio
    async def test_excess_reserve_is_standby(self, tmp_path):
        """When there are more accepted reserves than vacancies, extras become standby."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            await _insert_driver(db, 1, "D1")         # the only non-reserve driver
            await _insert_driver(db, 2, "ReserveA")
            await _insert_driver(db, 3, "ReserveB")   # this one should end up standby

            await _insert_team(db, 101, 10, "SoloTeam")  # one vacancy
            await _insert_team(db, 102, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 102, 3)

            await _insert_dra(db, 42, 10, 1, "DECLINED")  # one vacancy
            first_dra  = await _insert_dra(db, 42, 10, 2, "ACCEPTED", "2025-06-01T10:00:00+00:00")
            second_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T11:00:00+00:00")
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            first_row  = await _get_dra(db, first_dra)
            second_row = await _get_dra(db, second_dra)

        assert first_row["is_standby"] == 0
        assert first_row["assigned_team_id"] == 101
        assert second_row["is_standby"] == 1
        assert second_row["assigned_team_id"] is None


# ---------------------------------------------------------------------------
# 7. AttendanceService CRUD round-trips
# ---------------------------------------------------------------------------


async def _make_attendance_db(tmp_path) -> str:
    """Create DB with attendance tables (mirrors migration 031_attendance_rsvp.sql)."""
    path = str(tmp_path / "att_crud.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE driver_round_attendance (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id          INTEGER NOT NULL,
                division_id       INTEGER NOT NULL,
                driver_profile_id INTEGER NOT NULL,
                rsvp_status       TEXT    NOT NULL DEFAULT 'NO_RSVP',
                accepted_at       TEXT,
                assigned_team_id  INTEGER,
                is_standby        INTEGER NOT NULL DEFAULT 0,
                attended          INTEGER,
                points_awarded    INTEGER,
                total_points_after INTEGER,
                UNIQUE (round_id, division_id, driver_profile_id)
            );

            CREATE TABLE rsvp_embed_messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                round_id    INTEGER NOT NULL,
                division_id INTEGER NOT NULL,
                message_id  TEXT    NOT NULL,
                channel_id  TEXT    NOT NULL,
                posted_at   TEXT    NOT NULL,
                last_notice_msg_id  TEXT,
                distribution_msg_id TEXT,
                UNIQUE (round_id, division_id)
            );
            """
        )
        await db.commit()
    return path


class TestAttendanceServiceCrud:
    @pytest.mark.asyncio
    async def test_bulk_insert_and_get(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.bulk_insert_attendance_rows(
            round_id=1, division_id=10, driver_profile_ids=[100, 200, 300]
        )
        rows = await svc.get_attendance_rows(round_id=1, division_id=10)
        assert len(rows) == 3
        assert all(r.rsvp_status == "NO_RSVP" for r in rows)

    @pytest.mark.asyncio
    async def test_bulk_insert_idempotent(self, tmp_path):
        """INSERT OR IGNORE: calling twice should not raise or duplicate."""
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.bulk_insert_attendance_rows(1, 10, [100, 200])
        await svc.bulk_insert_attendance_rows(1, 10, [100, 200])  # idempotent
        rows = await svc.get_attendance_rows(round_id=1, division_id=10)
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_upsert_sets_accepted_at_for_accepted(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.bulk_insert_attendance_rows(1, 10, [100])
        await svc.upsert_rsvp_status(1, 10, 100, "ACCEPTED")
        row = await svc.get_attendance_row_for_driver(1, 10, 100)
        assert row is not None
        assert row.rsvp_status == "ACCEPTED"
        assert row.accepted_at is not None

    @pytest.mark.asyncio
    async def test_upsert_clears_accepted_at_for_declined(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.bulk_insert_attendance_rows(1, 10, [100])
        await svc.upsert_rsvp_status(1, 10, 100, "ACCEPTED")
        await svc.upsert_rsvp_status(1, 10, 100, "DECLINED")
        row = await svc.get_attendance_row_for_driver(1, 10, 100)
        assert row is not None
        assert row.rsvp_status == "DECLINED"
        assert row.accepted_at is None

    @pytest.mark.asyncio
    async def test_get_attendance_row_for_driver_returns_none_when_missing(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        row = await svc.get_attendance_row_for_driver(99, 10, 999)
        assert row is None

    @pytest.mark.asyncio
    async def test_insert_and_get_embed_message(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.insert_embed_message(1, 10, "999000111", "555000222")
        em = await svc.get_embed_message(round_id=1, division_id=10)
        assert em is not None
        assert em.message_id == "999000111"
        assert em.channel_id == "555000222"

    @pytest.mark.asyncio
    async def test_insert_embed_message_upserts(self, tmp_path):
        """Re-inserting same (round, division) should update message_id, not raise."""
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.insert_embed_message(1, 10, "first_msg", "111")
        await svc.insert_embed_message(1, 10, "second_msg", "111")  # upsert
        em = await svc.get_embed_message(round_id=1, division_id=10)
        assert em is not None
        assert em.message_id == "second_msg"

    @pytest.mark.asyncio
    async def test_get_embed_message_returns_none_when_missing(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        em = await svc.get_embed_message(round_id=99, division_id=10)
        assert em is None

    @pytest.mark.asyncio
    async def test_get_all_embed_messages(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.insert_embed_message(1, 10, "msg_a", "ch1")
        await svc.insert_embed_message(2, 10, "msg_b", "ch1")
        all_msgs = await svc.get_all_embed_messages()
        assert len(all_msgs) == 2
        mids = {m.message_id for m in all_msgs}
        assert "msg_a" in mids
        assert "msg_b" in mids
