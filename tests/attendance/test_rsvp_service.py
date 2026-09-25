"""Unit tests for rsvp_service — T024.

Covers:
  1. Distribution priority ordering (tier 1 no FT drivers > tier 2 DECLINED > tier 3
     NO_RSVP > tier 4 partial allocation > tier 5 already served this round >
     tier 6 TENTATIVE)
  2. Tie-breaking: standings position (lowest-placed first, unranked last);
     alphabetical team name
  3. accepted_at timestamp ordering for reserves (first-accepted = highest priority)
  4. Standby classification (reserves beyond available vacancies)
  5. No-op when no accepted reserves
  6. AttendanceService CRUD round-trips (bulk_insert, upsert, get, embed message)
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.attendance.services.rsvp_service import run_reserve_distribution


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_FUTURE = datetime(2025, 6, 8, 14, 0, 0, tzinfo=timezone.utc)


async def _make_db(tmp_path) -> str:
    """A migrated database for run_reserve_distribution; _seed_base gives it a round."""
    path = str(tmp_path / "rsvp_test.db")
    await run_migrations(path)
    return path


def _make_bot(db_path: str) -> MagicMock:
    bot = MagicMock()
    bot.db_path = db_path
    # Distribution is gated on the attendance module (issue #114). These tests are about the
    # distribution algorithm, so the module is on throughout; the gate itself is pinned in
    # test_attendance_module_gate.py.
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=True)
    return bot


async def _seed_base(db_path: str) -> None:
    """Insert one active season, its division 10, and the division's round 42."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2025-05-01', 'ACTIVE', 1)"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) "
            "VALUES (10, 1, 'Div1', 3010)"
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
            "VALUES (42, 10, 3, 'NORMAL', ?)",
            (_FUTURE.isoformat(),),
        )
        await db.commit()


async def _insert_driver(db: aiosqlite.Connection, dp_id: int, name: str) -> None:
    """A driver whose Discord account is their profile id: each driver needs one of their own."""
    await db.execute(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, test_display_name) "
        "VALUES (?, ?, 'ASSIGNED', ?)",
        (dp_id, str(dp_id), name),
    )


async def _insert_team(db: aiosqlite.Connection, team_id: int, div_id: int, name: str, is_reserve: int = 0, max_seats: int = 2) -> None:
    await db.execute(
        "INSERT INTO team_instances (id, division_id, name, full_name, is_reserve, max_seats) VALUES (?, ?, ?, ?, ?, ?)",
        (team_id, div_id, name, name, is_reserve, max_seats),
    )


async def _add_driver_to_team(db: aiosqlite.Connection, team_id: int, dp_id: int) -> None:
    """Seat the driver in the team's next seat."""
    await db.execute(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "SELECT ?, COALESCE(MAX(seat_number), 0) + 1, ? FROM team_seats "
        "WHERE team_instance_id = ?",
        (team_id, dp_id, team_id),
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
        async with get_connection(db_path) as db:
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

        async with get_connection(db_path) as db:
            row1 = await _get_dra(db, dra1)
            row2 = await _get_dra(db, dra2)

        assert row1["assigned_team_id"] is None
        assert row2["assigned_team_id"] is None
        assert row2["is_standby"] == 0


# ---------------------------------------------------------------------------
# 2. Priority ordering: tier 2 (DECLINED) fills before tier 3 (NO_RSVP)
# ---------------------------------------------------------------------------


class TestPriorityOrdering:
    @pytest.mark.asyncio
    async def test_declined_team_gets_reserve_before_no_rsvp_team(self, tmp_path):
        """Team with a DECLINED driver gets the reserve before a team with only NO_RSVP drivers."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with get_connection(db_path) as db:
            # Regular drivers
            await _insert_driver(db, 1, "Driver1 (NO_RSVP team)")
            await _insert_driver(db, 2, "Driver2 (DECLINED team)")
            # Reserve driver
            await _insert_driver(db, 3, "ReserveDriver")

            await _insert_team(db, 101, 10, "TeamA_NoRsvp")   # tier 3 priority
            await _insert_team(db, 102, 10, "TeamB_Declined")  # should get tier 2 priority
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

        async with get_connection(db_path) as db:
            reserve_row = await _get_dra(db, reserve_dra)

        # Reserve should be assigned to TeamB_Declined (tier 2) not TeamA_NoRsvp (tier 3)
        assert reserve_row["assigned_team_id"] == 102
        assert reserve_row["is_standby"] == 0


# ---------------------------------------------------------------------------
# 2b. Priority ordering: tier 3 (partial) fills before tier 5 (TENTATIVE)
# ---------------------------------------------------------------------------


class TestPriorityPartialAllocation:
    @pytest.mark.asyncio
    async def test_partial_team_gets_reserve_before_tentative_team(self, tmp_path):
        """Team with an empty seat (partial allocation) gets a reserve before a team with only
        a TENTATIVE driver and no vacant seats."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with get_connection(db_path) as db:
            # Partial team: 1 accepted FT driver, 1 seat physically vacant (max_seats=2)
            await _insert_driver(db, 1, "PartialAccepted")
            # Tentative team: 2 FT drivers both tentative, fully staffed
            await _insert_driver(db, 2, "Tentative1")
            await _insert_driver(db, 3, "Tentative2")
            # Reserve
            await _insert_driver(db, 4, "ReserveDriver")

            await _insert_team(db, 101, 10, "TeamPartial")   # tier 3: 1 accepted, 1 empty seat
            await _insert_team(db, 102, 10, "TeamTentative") # tier 5: 2 tentative, full
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)   # one FT driver in a 2-seat team
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 102, 3)
            await _add_driver_to_team(db, 103, 4)

            await _insert_dra(db, 42, 10, 1, "ACCEPTED")          # partial team FT driver OK
            await _insert_dra(db, 42, 10, 2, "TENTATIVE")         # tentative team
            await _insert_dra(db, 42, 10, 3, "TENTATIVE")
            reserve_dra = await _insert_dra(db, 42, 10, 4, "ACCEPTED", "2025-06-01T10:00:00+00:00")
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            reserve_row = await _get_dra(db, reserve_dra)

        # Reserve should go to the partially-staffed team (tier 3), not the tentative team (tier 5)
        assert reserve_row["assigned_team_id"] == 101
        assert reserve_row["is_standby"] == 0


# ---------------------------------------------------------------------------
# 2c. Priority ordering: tier 4 (no FT drivers) fills before tier 5 (TENTATIVE)
# ---------------------------------------------------------------------------


class TestPriorityNoFtDrivers:
    @pytest.mark.asyncio
    async def test_unstaffed_team_gets_reserve_before_tentative_team(self, tmp_path):
        """Team with no full-time drivers assigned at all (tier 4) receives a reserve before
        a fully-staffed team where all drivers are TENTATIVE (tier 5)."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with get_connection(db_path) as db:
            # Tentative team: 2 FT drivers both tentative
            await _insert_driver(db, 1, "Tentative1")
            await _insert_driver(db, 2, "Tentative2")
            # Reserve
            await _insert_driver(db, 3, "ReserveDriver")

            # Unstaffed team: no FT drivers at all (no team_seats rows for FT drivers)
            await _insert_team(db, 101, 10, "TeamEmpty")     # tier 4: 0 FT drivers
            await _insert_team(db, 102, 10, "TeamTentative") # tier 5: 2 tentative FT drivers
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            # No FT seats in TeamEmpty — intentionally not calling _add_driver_to_team for it
            await _add_driver_to_team(db, 102, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 103, 3)

            await _insert_dra(db, 42, 10, 1, "TENTATIVE")
            await _insert_dra(db, 42, 10, 2, "TENTATIVE")
            reserve_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T10:00:00+00:00")
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            reserve_row = await _get_dra(db, reserve_dra)

        # Reserve should go to the fully-empty team (tier 4), not the tentative team (tier 5)
        assert reserve_row["assigned_team_id"] == 101
        assert reserve_row["is_standby"] == 0


# ---------------------------------------------------------------------------
# 3. Tie-breaking: standings position
# ---------------------------------------------------------------------------


class TestTiebreakerStandings:
    @pytest.mark.asyncio
    async def test_worst_standing_position_wins(self, tmp_path):
        """The team lowest in the constructors' table should receive the reserve first."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        # Need a prior round to anchor the standings snapshot
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (41, 10, 2, 'NORMAL', ?)",
                (datetime(2025, 5, 25, tzinfo=timezone.utc).isoformat(),),
            )

            await _insert_driver(db, 1, "D1")
            await _insert_driver(db, 2, "D2")
            await _insert_driver(db, 3, "ReserveR")

            await _insert_team(db, 101, 10, "TeamBeta")   # standing_pos=2 — should win
            await _insert_team(db, 102, 10, "TeamAlpha")  # standing_pos=1
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
                "INSERT INTO team_standings_snapshots "
                "(team_instance_id, round_id, division_id, standing_position) "
                "VALUES (102, 41, 10, 1)"
            )
            await db.execute(
                "INSERT INTO team_standings_snapshots "
                "(team_instance_id, round_id, division_id, standing_position) "
                "VALUES (101, 41, 10, 2)"
            )
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            reserve_row = await _get_dra(db, reserve_dra)

        assert reserve_row["assigned_team_id"] == 101  # TeamBeta (pos 2)

    @pytest.mark.asyncio
    async def test_unranked_team_sorts_last(self, tmp_path):
        """A team with no standings snapshot yet loses to any ranked team."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        # Need a prior round to anchor the standings snapshot
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (41, 10, 2, 'NORMAL', ?)",
                (datetime(2025, 5, 25, tzinfo=timezone.utc).isoformat(),),
            )

            await _insert_driver(db, 1, "D1")
            await _insert_driver(db, 2, "D2")
            await _insert_driver(db, 3, "ReserveR")

            await _insert_team(db, 101, 10, "TeamRanked")    # standing_pos=1 — should win
            await _insert_team(db, 102, 10, "TeamUnranked")  # no snapshot
            await _insert_team(db, 103, 10, "Reserve", is_reserve=1)

            await _add_driver_to_team(db, 101, 1)
            await _add_driver_to_team(db, 102, 2)
            await _add_driver_to_team(db, 103, 3)

            # Both teams have a DECLINED driver (same tier 2)
            await _insert_dra(db, 42, 10, 1, "DECLINED")
            await _insert_dra(db, 42, 10, 2, "DECLINED")
            reserve_dra = await _insert_dra(db, 42, 10, 3, "ACCEPTED", "2025-06-01T10:00:00+00:00")

            # Only TeamRanked carries a snapshot; TeamUnranked has none
            await db.execute(
                "INSERT INTO team_standings_snapshots "
                "(team_instance_id, round_id, division_id, standing_position) "
                "VALUES (101, 41, 10, 1)"
            )
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            reserve_row = await _get_dra(db, reserve_dra)

        assert reserve_row["assigned_team_id"] == 101  # TeamRanked, despite being top of the table


# ---------------------------------------------------------------------------
# 4. Tie-breaking: alphabetical team name fallback
# ---------------------------------------------------------------------------


class TestTiebreakerAlphabetical:
    @pytest.mark.asyncio
    async def test_alphabetical_fallback(self, tmp_path):
        """When tier and standings are identical, team name alphabetical order wins."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with get_connection(db_path) as db:
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

        async with get_connection(db_path) as db:
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
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (41, 10, 2, 'NORMAL', ?)",
                (datetime(2025, 5, 25, tzinfo=timezone.utc).isoformat(),),
            )

            await _insert_driver(db, 1, "D1")
            await _insert_driver(db, 2, "D2")
            await _insert_driver(db, 3, "Reserve_Early")   # accepted first
            await _insert_driver(db, 4, "Reserve_Late")    # accepted later

            await _insert_team(db, 101, 10, "TeamTop", max_seats=1)    # standing pos=1
            await _insert_team(db, 102, 10, "TeamBottom", max_seats=1) # standing pos=2 (best vacancy)
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
                "INSERT INTO team_standings_snapshots "
                "(team_instance_id, round_id, division_id, standing_position) "
                "VALUES (101, 41, 10, 1)"
            )
            await db.execute(
                "INSERT INTO team_standings_snapshots "
                "(team_instance_id, round_id, division_id, standing_position) "
                "VALUES (102, 41, 10, 2)"
            )
            await db.commit()

        bot = _make_bot(db_path)
        await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            early_row = await _get_dra(db, early_dra)
            late_row  = await _get_dra(db, late_dra)

        assert early_row["assigned_team_id"] == 102  # TeamBottom — best available
        assert late_row["assigned_team_id"]  == 101  # TeamTop


# ---------------------------------------------------------------------------
# 6. Standby classification
# ---------------------------------------------------------------------------


class TestStandbyClassification:
    @pytest.mark.asyncio
    async def test_excess_reserve_is_standby(self, tmp_path):
        """When there are more accepted reserves than vacancies, extras become standby."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)

        async with get_connection(db_path) as db:
            await _insert_driver(db, 1, "D1")         # the only non-reserve driver
            await _insert_driver(db, 2, "ReserveA")
            await _insert_driver(db, 3, "ReserveB")   # this one should end up standby

            await _insert_team(db, 101, 10, "SoloTeam", max_seats=1)  # exactly one vacancy
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

        async with get_connection(db_path) as db:
            first_row  = await _get_dra(db, first_dra)
            second_row = await _get_dra(db, second_dra)

        assert first_row["is_standby"] == 0
        assert first_row["assigned_team_id"] == 101
        assert second_row["is_standby"] == 1
        assert second_row["assigned_team_id"] is None


# ---------------------------------------------------------------------------
# 6b. A later distribution of the same round starts from nothing (#429)
# ---------------------------------------------------------------------------
#
# A round's distribution can run more than once: again after a call is posted again by an
# amendment, and again at a restart where its announcement never posted. Each run is the whole
# answer. A reserve the earlier run seated and this one does not must not keep the team, since
# every reader takes a team to mean the reserve was sent to race — scoring charges them a no-show.


async def _seed_one_seat_one_reserve(db_path: str, full_timer_answer: str) -> int:
    """One single-seat team and one reserve who accepted; returns the reserve's row id."""
    async with get_connection(db_path) as db:
        await _insert_driver(db, 1, "Full Timer")
        await _insert_driver(db, 2, "Stand In")
        await _insert_team(db, 101, 10, "SoloTeam", max_seats=1)
        await _insert_team(db, 102, 10, "Reserve", is_reserve=1)
        await _add_driver_to_team(db, 101, 1)
        await _add_driver_to_team(db, 102, 2)
        await _insert_dra(db, 42, 10, 1, full_timer_answer)
        reserve = await _insert_dra(db, 42, 10, 2, "ACCEPTED", "2025-06-01T10:00:00+00:00")
        await db.commit()
    return reserve


async def _answer(db_path: str, driver_profile_id: int, status: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET rsvp_status = ? "
            "WHERE round_id = 42 AND driver_profile_id = ?",
            (status, driver_profile_id),
        )
        await db.commit()


class TestRedistribution:
    @pytest.mark.asyncio
    async def test_a_later_distribution_leaves_a_reserve_it_puts_on_standby_without_a_team(
        self, tmp_path
    ):
        """The full-time driver declined, so the reserve took the seat; then they came back,
        and the next run had no seat to give. The reserve is on standby and holds no team."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)
        reserve = await _seed_one_seat_one_reserve(db_path, "DECLINED")
        bot = _make_bot(db_path)

        await run_reserve_distribution(42, 10, bot)
        async with get_connection(db_path) as db:
            assert (await _get_dra(db, reserve))["assigned_team_id"] == 101

        await _answer(db_path, 1, "ACCEPTED")
        await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            row = await _get_dra(db, reserve)
        assert row["is_standby"] == 1
        assert row["assigned_team_id"] is None, "a reserve on standby kept an earlier run's team"

    @pytest.mark.asyncio
    async def test_a_later_distribution_leaves_a_reserve_no_longer_accepted_without_a_team(
        self, tmp_path
    ):
        """A reserve seated by one run who has since declined is placed by nobody. The next run
        finds no reserve accepted at all, and must still take the earlier seat away."""
        db_path = await _make_db(tmp_path)
        await _seed_base(db_path)
        reserve = await _seed_one_seat_one_reserve(db_path, "DECLINED")
        bot = _make_bot(db_path)

        await run_reserve_distribution(42, 10, bot)
        await _answer(db_path, 2, "DECLINED")
        placed = await run_reserve_distribution(42, 10, bot)

        async with get_connection(db_path) as db:
            row = await _get_dra(db, reserve)
        assert placed is False
        assert row["is_standby"] == 0
        assert row["assigned_team_id"] is None, "a reserve no longer accepted kept a team"


# ---------------------------------------------------------------------------
# 7. AttendanceService CRUD round-trips
# ---------------------------------------------------------------------------


async def _make_attendance_db(tmp_path) -> str:
    """A migrated database holding what the CRUD round-trips name: rounds 1 and 2 of
    division 10, and drivers 100, 200 and 300."""
    path = str(tmp_path / "att_crud.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) VALUES (1, '2025-05-01', 'ACTIVE')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id) "
            "VALUES (10, 1, 'Div1', 3010)"
        )
        await db.executemany(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
            "VALUES (?, 10, ?, 'NORMAL', ?)",
            [(1, 1, "2025-06-01T18:00:00"), (2, 2, "2025-06-08T18:00:00")],
        )
        for dp_id in (100, 200, 300):
            await _insert_driver(db, dp_id, f"Driver {dp_id}")
        await db.commit()
    return path


class TestAttendanceServiceCrud:
    @pytest.mark.asyncio
    async def test_bulk_insert_and_get(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
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
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.bulk_insert_attendance_rows(1, 10, [100, 200])
        await svc.bulk_insert_attendance_rows(1, 10, [100, 200])  # idempotent
        rows = await svc.get_attendance_rows(round_id=1, division_id=10)
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_upsert_sets_accepted_at_for_accepted(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
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
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.bulk_insert_attendance_rows(1, 10, [100])
        await svc.upsert_rsvp_status(1, 10, 100, "ACCEPTED")
        await svc.upsert_rsvp_status(1, 10, 100, "DECLINED")
        row = await svc.get_attendance_row_for_driver(1, 10, 100)
        assert row is not None
        assert row.rsvp_status == "DECLINED"
        assert row.accepted_at is None

    @pytest.mark.asyncio
    async def test_upsert_creates_the_row_when_none_exists(self, tmp_path):
        """No `bulk_insert_attendance_rows` first, which is issue #209 exactly: only
        `run_rsvp_notice` opens those rows, and only for the roster as it stood when the call
        was posted. A driver placed into the division afterwards answers a call with no row of
        their own, and this used to be two bare UPDATEs that discarded the answer in silence."""
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.upsert_rsvp_status(1, 10, 100, "ACCEPTED")
        row = await svc.get_attendance_row_for_driver(1, 10, 100)
        assert row is not None
        assert row.rsvp_status == "ACCEPTED"
        assert row.accepted_at is not None

    @pytest.mark.asyncio
    async def test_upsert_creating_a_declined_row_leaves_accepted_at_null(self, tmp_path):
        """A row created at answer time takes the ordinary `accepted_at` rule, not a special
        case of it — the reserve distribution orders by that column and a row born with a
        timestamp it never earned would jump the queue."""
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.upsert_rsvp_status(1, 10, 100, "DECLINED")
        row = await svc.get_attendance_row_for_driver(1, 10, 100)
        assert row is not None
        assert row.rsvp_status == "DECLINED"
        assert row.accepted_at is None

    @pytest.mark.asyncio
    async def test_upsert_reports_whether_it_wrote(self, tmp_path):
        """Both paths report True. The return value exists so `handle_rsvp_button` can tell a
        driver the truth instead of assuming, which is the half of issue #209 that let a
        success message stand over a write that changed nothing."""
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        assert await svc.upsert_rsvp_status(1, 10, 100, "ACCEPTED") is True   # inserted
        assert await svc.upsert_rsvp_status(1, 10, 100, "DECLINED") is True   # updated

    @pytest.mark.asyncio
    async def test_get_attendance_row_for_driver_returns_none_when_missing(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        row = await svc.get_attendance_row_for_driver(99, 10, 999)
        assert row is None

    @pytest.mark.asyncio
    async def test_insert_and_get_embed_message(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
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
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.insert_embed_message(1, 10, "first_msg", "111")
        await svc.insert_embed_message(1, 10, "second_msg", "111")  # upsert
        em = await svc.get_embed_message(round_id=1, division_id=10)
        assert em is not None
        assert em.message_id == "second_msg"

    @pytest.mark.asyncio
    async def test_get_embed_message_returns_none_when_missing(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)
        em = await svc.get_embed_message(round_id=99, division_id=10)
        assert em is None

    @pytest.mark.asyncio
    async def test_get_all_embed_messages(self, tmp_path):
        db_path = await _make_attendance_db(tmp_path)
        from leaguebot.attendance.services.attendance_service import AttendanceService
        svc = AttendanceService(db_path)

        await svc.insert_embed_message(1, 10, "msg_a", "ch1")
        await svc.insert_embed_message(2, 10, "msg_b", "ch1")
        all_msgs = await svc.get_all_embed_messages()
        assert len(all_msgs) == 2
        mids = {m.message_id for m in all_msgs}
        assert "msg_a" in mids
        assert "msg_b" in mids
