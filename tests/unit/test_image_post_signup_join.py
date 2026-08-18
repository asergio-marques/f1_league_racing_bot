"""Regression cover for the two `signup_records` faults fixed on 2026-08-18.

Both were invisible to the existing suites because those exercise ``resolve_drawing``,
which is handed its rows and never issues a query. These tests go the other way: they run
the queries the posting paths actually issue, against a migrated database, so that a
column or table that does not exist fails here rather than on a league's server.

The two faults were:

* three sites joining ``signup_records`` on a ``driver_profile_id`` column that has never
  existed, which raised ``OperationalError`` and made four image types unrenderable;
* ``_nationality_collected`` reading a ``signup_config`` table that no migration creates,
  inside a bare ``except`` that returned True, so the suppression switch reached nothing.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import aiosqlite
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 5150
USER_ID = 9_100_000_000_000_001


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "signup_join.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


@pytest.fixture
async def bot(db_path):
    return SimpleNamespace(db_path=db_path)


async def _seed_driver(db_path, *, name: str = "Ada Lovelace", nationality: str = "British"):
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
            "VALUES (?, ?, 'ACTIVE')",
            (SERVER_ID, USER_ID),
        )
        await db.execute(
            "INSERT INTO signup_records (server_id, discord_user_id, server_display_name, "
            "discord_username, nationality) VALUES (?, ?, ?, ?, ?)",
            (SERVER_ID, str(USER_ID), name, "ada", nationality),
        )
        await db.commit()


# ── The join ──────────────────────────────────────────────────────────────


class TestSignupRecordsJoin:
    """`signup_records` is keyed by (server_id, discord_user_id) and holds no profile id."""

    async def test_the_table_holds_no_driver_profile_id(self, db_path):
        """The premise. If this ever fails, the join may legitimately be rewritten."""
        async with get_connection(db_path) as db:
            columns = {
                row[1]
                for row in await (
                    await db.execute("PRAGMA table_info(signup_records)")
                ).fetchall()
            }
        assert "driver_profile_id" not in columns
        assert {"server_id", "discord_user_id"} <= columns

    async def test_nationalities_reads_a_seeded_driver(self, bot, db_path):
        """`_nationalities` is imported by the attendance and verdict paths too."""
        from services.image_results_post import _nationalities

        await _seed_driver(db_path, nationality="Dutch")

        assert await _nationalities(bot, [USER_ID]) == {USER_ID: "Dutch"}

    async def test_nationalities_yields_none_where_the_driver_stated_one_not(
        self, bot, db_path
    ):
        from services.image_results_post import _nationalities

        await _seed_driver(db_path, nationality=None)

        assert await _nationalities(bot, [USER_ID]) == {USER_ID: None}

    async def test_driver_names_reads_a_seeded_driver(self, bot, db_path):
        """The second corrected site, in the results path."""
        from services.image_results_post import _driver_names

        await _seed_driver(db_path, name="Ada Lovelace")

        names = await _driver_names(bot, None, [USER_ID])

        assert names.get(USER_ID) == "Ada Lovelace"

    async def test_the_lineup_seat_query_runs_and_finds_its_driver(self, db_path):
        """The third corrected site. Issued verbatim as `build_drawing` issues it."""
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "INSERT INTO seasons (server_id, start_date, status, season_number) "
                "VALUES (?, '2026-03-01', 'ACTIVE', 1)",
                (SERVER_ID,),
            )
            season_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, 'D1', 1, 'ACTIVE', 1)",
                (season_id,),
            )
            division_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, max_seats, is_reserve) "
                "VALUES (?, 'Redline', 1, 0)",
                (division_id,),
            )
            team_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
                (team_id,),
            )
            seat_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ACTIVE')",
                (SERVER_ID, USER_ID),
            )
            profile_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO signup_records (server_id, discord_user_id, "
                "server_display_name, discord_username, nationality) "
                "VALUES (?, ?, 'Ada Lovelace', 'ada', 'British')",
                (SERVER_ID, str(USER_ID)),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, current_position, current_points, points_gap_to_first, "
                "team_seat_id) VALUES (?, ?, ?, 0, 0, 0, ?)",
                (profile_id, season_id, division_id, seat_id),
            )
            await db.commit()

            rows = await (
                await db.execute(
                    "SELECT ts.seat_number, dp.discord_user_id, dp.is_test_driver, "
                    "       dp.test_display_name, sr.server_display_name, "
                    "       sr.discord_username, sr.nationality "
                    "FROM team_seats ts "
                    "LEFT JOIN driver_season_assignments dsa "
                    "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ? "
                    "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    "LEFT JOIN signup_records sr "
                    "       ON sr.server_id = dp.server_id "
                    "      AND sr.discord_user_id = CAST(dp.discord_user_id AS TEXT) "
                    "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                    (division_id, team_id),
                )
            ).fetchall()

        assert len(rows) == 1
        assert rows[0]["server_display_name"] == "Ada Lovelace"
        assert rows[0]["nationality"] == "British"


# ── The suppression switch ────────────────────────────────────────────────


class TestNationalityCollected:
    """The switch lives in `signup_module_settings`; `signup_config` never existed."""

    async def test_a_league_that_switched_collection_off_is_observed(self, db_path):
        from services.image_results_post import _nationality_collected

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (server_id, nationality_required, "
                "time_type, time_image_required) VALUES (?, 0, 'TIME_TRIAL', 1)",
                (SERVER_ID,),
            )
            await db.commit()

        assert await _nationality_collected(db_path, SERVER_ID) is False

    async def test_a_league_that_collects_is_observed(self, db_path):
        from services.image_results_post import _nationality_collected

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (server_id, nationality_required, "
                "time_type, time_image_required) VALUES (?, 1, 'TIME_TRIAL', 1)",
                (SERVER_ID,),
            )
            await db.commit()

        assert await _nationality_collected(db_path, SERVER_ID) is True

    async def test_a_league_with_no_row_collects(self, db_path):
        """The documented default, and what a league without the signup module gets."""
        from services.image_results_post import _nationality_collected

        assert await _nationality_collected(db_path, SERVER_ID) is True
