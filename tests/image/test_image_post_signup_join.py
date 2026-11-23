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

A fourth site, ``image_verdict_post._driver_nationality``, carried the same phantom join
and was corrected in 2026-08 alongside test-driver nationality; it is covered here too,
for the same reason — only a real query against a real schema catches it.

The same tests now also pin where a **mock driver's** nationality comes from: its signup, keyed
by its synthetic id, as any driver's is. A mock driver that has not signed up has none.
"""
from __future__ import annotations

from types import SimpleNamespace

import aiosqlite
import pytest

from leaguebot.framework.db.database import get_connection, run_migrations

SERVER_ID = 5150
USER_ID = 9_100_000_000_000_001
#: A synthetic id in the range test_roster_service hands out.
TEST_USER_ID = 9_000_000_000_000_000_001


def _hook(db_path):
    """The hook signup answers how a driver is named, as the builder signs it up."""
    from tests.support.review_workflows import sign_up_driver_names_read

    holder = SimpleNamespace(db_path=db_path)
    sign_up_driver_names_read(holder)
    return holder.driver_names_read


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
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'ACTIVE')",
            (USER_ID,),
        )
        await db.execute(
            "INSERT INTO signup_records (created_at, discord_user_id, server_display_name, "
            "discord_username, nationality) VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, ?)",
            (str(USER_ID), name, "ada", nationality),
        )
        await db.commit()


async def _seed_test_driver(db_path, *, name: str = "Mock Alpha", nationality=None):
    """A mock driver as `/test-mode roster add` makes one, and, where *nationality* is given, its
    signup naming it by its test name and recording *nationality*; with none, no signup at all."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver, test_display_name) "
            "VALUES (?, 'ASSIGNED', 1, ?)",
            (str(TEST_USER_ID), name),
        )
        if nationality is not None:
            await db.execute(
                "INSERT INTO signup_records (created_at, discord_user_id, server_display_name, "
                "discord_username, nationality) VALUES ('2026-01-01T00:00:00+00:00', ?, ?, ?, ?)",
                (str(TEST_USER_ID), name, name, nationality),
            )
        await db.commit()


#: The reason a mock driver's nationality is not yet read from its signup.
_FROM_ITS_SIGNUP = (
    "#600: a mock driver's nationality is still read from its profile, not its signup"
)


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
        assert "discord_user_id" in columns

    @pytest.mark.parametrize(
        ("seed", "user_id", "nationality"),
        [
            pytest.param(_seed_driver, USER_ID, "Dutch", id="seeded"),
            pytest.param(_seed_driver, USER_ID, None, id="seeded-none-stated"),
            pytest.param(
                _seed_test_driver, TEST_USER_ID, "Brazilian", id="mock",
                marks=pytest.mark.xfail(strict=True, reason=_FROM_ITS_SIGNUP),
            ),
            pytest.param(_seed_test_driver, TEST_USER_ID, None, id="mock-none-given"),
        ],
    )
    async def test_nationalities_reads_each_drivers_own(
        self, bot, db_path, seed, user_id, nationality
    ):
        """`_nationalities` is imported by the attendance and verdict paths too.

        A mock driver's nationality is its signup's, and none where it has not signed up.
        """
        from leaguebot.core.services.drivers.driver_names import read_driver_nationalities

        await seed(db_path, nationality=nationality)

        assert await read_driver_nationalities(db_path, _hook(db_path), [user_id]) == {user_id: nationality}

    @pytest.mark.xfail(strict=True, reason=_FROM_ITS_SIGNUP)
    async def test_the_verdict_lookup_reads_both_kinds_of_driver(self, db_path):
        """The fourth site, which joined the phantom column until this was written. A real
        driver's nationality and a mock driver's are each their signup's."""
        from leaguebot.image.services.aspects.image_verdict_post import _driver_nationality

        await _seed_driver(db_path, nationality="Dutch")
        await _seed_test_driver(db_path, nationality="Italian")

        assert await _driver_nationality(db_path, _hook(db_path), USER_ID) == "Dutch"
        assert await _driver_nationality(db_path, _hook(db_path), TEST_USER_ID) == "Italian"

    @pytest.mark.xfail(strict=True, reason=_FROM_ITS_SIGNUP)
    async def test_a_mock_driver_with_no_signup_to_the_season_is_read_from_its_latest(
        self, db_path
    ):
        """D17. Season 1 is completed and season 2 is under way, with division D1. Mock Alpha, a
        mock driver, signed up to season 1 alone, as "Mock Alpha", giving Brazilian. Read for D1,
        in season 2, to which it made no signup, it is named "Mock Alpha" and its nationality is
        Brazilian, from its latest signup, as any driver's is."""
        from leaguebot.core.services.drivers.driver_names import (
            read_driver_names,
            read_driver_nationalities,
        )

        async with get_connection(db_path) as db:
            earlier = (await db.execute(
                "INSERT INTO seasons (start_date, status, season_number) "
                "VALUES ('2025-03-01', 'COMPLETED', 1)"
            )).lastrowid
            current = (await db.execute(
                "INSERT INTO seasons (start_date, status, season_number) "
                "VALUES ('2026-03-01', 'ACTIVE', 2)"
            )).lastrowid
            division_id = (await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, 'D1', 1, 'ACTIVE', 1)",
                (current,),
            )).lastrowid
            await db.execute(
                "INSERT INTO driver_profiles (discord_user_id, current_state, "
                "is_test_driver, test_display_name) VALUES (?, 'UNASSIGNED', 1, 'Mock Alpha')",
                (str(TEST_USER_ID),),
            )
            await db.execute(
                "INSERT INTO signup_records (created_at, season_id, discord_user_id, "
                "server_display_name, discord_username, nationality) "
                "VALUES ('2025-02-01T00:00:00+00:00', ?, ?, 'Mock Alpha', 'Mock Alpha', "
                "'Brazilian')",
                (earlier, str(TEST_USER_ID)),
            )
            await db.commit()

        hook = _hook(db_path)
        assert await read_driver_nationalities(
            db_path, hook, [TEST_USER_ID], division_id=division_id
        ) == {TEST_USER_ID: "Brazilian"}
        assert await read_driver_names(
            db_path, hook, None, [TEST_USER_ID], division_id=division_id
        ) == {TEST_USER_ID: "Mock Alpha"}

    async def test_the_verdict_lookup_yields_none_for_an_unknown_driver(self, db_path):
        from leaguebot.image.services.aspects.image_verdict_post import _driver_nationality

        assert await _driver_nationality(db_path, _hook(db_path), USER_ID) is None

    async def test_driver_names_reads_a_seeded_driver(self, bot, db_path):
        """The second corrected site, in the results path."""
        from leaguebot.core.services.drivers.driver_names import read_driver_names

        await _seed_driver(db_path, name="Ada Lovelace")

        names = await read_driver_names(db_path, _hook(db_path), None, [USER_ID])

        assert names.get(USER_ID) == "Ada Lovelace"

    async def test_the_lineup_seat_query_runs_and_finds_its_driver(self, db_path):
        """The lineup picture's seat query, `seated_teams`, run against the migrated schema as
        `build_drawing` runs it. Season 1 is active, with division D1 holding Redline, a one-seat
        team; Ada (USER_ID) signed up as 'Ada Lovelace', British, is Active and holds its seat,
        her placement confirmed. D1's lineup is Redline alone, its one seat drawn as Ada, under
        her signup's name and flag."""
        from leaguebot.image.services.aspects.image_lineup_post import seated_teams

        await _seed_driver(db_path, name="Ada Lovelace", nationality="British")
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "INSERT INTO seasons (start_date, status, season_number) "
                "VALUES ('2026-03-01', 'ACTIVE', 1)"
            )
            season_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
                "VALUES (?, 'D1', 1, 'ACTIVE', 1)",
                (season_id,),
            )
            division_id = cursor.lastrowid
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, 'Redline', 'Redline', 1, 0)",
                (division_id,),
            )
            cursor = await db.execute(
                "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 1)",
                (cursor.lastrowid,),
            )
            seat_id = cursor.lastrowid
            cursor = await db.execute(
                "SELECT id FROM driver_profiles WHERE discord_user_id = ?", (USER_ID,)
            )
            [profile_id] = await cursor.fetchone()
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id, committed) VALUES (?, ?, ?, ?, 1)",
                (profile_id, season_id, division_id, seat_id),
            )
            await db.commit()

        assert division_id is not None
        [team] = await seated_teams(db_path, _hook(db_path), division_id, season_id)

        assert team.name == "Redline"
        [seat] = team.seats
        assert seat.seat_number == 1
        assert seat.profile_id == profile_id
        assert seat.discord_user_id == str(USER_ID)
        assert seat.server_display_name == "Ada Lovelace"
        assert seat.nationality == "British"


# ── The suppression switch ────────────────────────────────────────────────


class TestNationalityCollected:
    """The switch lives in `signup_module_settings`; `signup_config` never existed."""

    @pytest.mark.parametrize(
        ("nationality_required", "expected"), [(0, False), (1, True)], ids=["off", "on"]
    )
    async def test_the_signup_switch_is_observed(self, db_path, nationality_required, expected):
        from leaguebot.core.services.drivers.driver_names import read_nationality_collected

        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO signup_module_settings (id, nationality_required, "
                "time_type, time_image_required) VALUES (?, ?, 'TIME_TRIAL', 1)",
                (1, nationality_required),
            )
            await db.commit()

        assert await read_nationality_collected(db_path, _hook(db_path)) is expected

    async def test_a_league_with_no_row_collects(self, db_path):
        """The documented default, and what a league without the signup module gets."""
        from leaguebot.core.services.drivers.driver_names import read_nationality_collected

        assert await read_nationality_collected(db_path, _hook(db_path)) is True

    async def test_an_unreadable_switch_collects(self, db_path):
        """A broken reader is not a reason to fail a render."""
        from leaguebot.core.services.drivers.driver_names import read_nationality_collected

        assert await read_nationality_collected("no/such/database.db", _hook(db_path)) is True


class TestTheTestModeSwitchStandsIn:
    """While test mode is active, `/test-mode nationality` is the switch that governs.

    A maintainer may then see the graphics of a server under test both with flags and
    without them without disturbing the setting their real signups run on.
    """

    async def _set(self, db_path, *, test_mode: int, test_nationality: int, signup: int):
        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE server_configs SET test_mode_active = ?, "
                "test_mode_nationality_required = ? WHERE server_id = ?",
                (test_mode, test_nationality, SERVER_ID),
            )
            await db.execute(
                "INSERT INTO signup_module_settings (id, nationality_required, "
                "time_type, time_image_required) VALUES (?, ?, 'TIME_TRIAL', 1)",
                (1, signup),
            )
            await db.commit()

    @pytest.mark.parametrize(
        ("test_mode", "test_nationality", "signup", "expected"),
        [(1, 0, 1, False), (1, 1, 0, True), (0, 0, 1, True)],
        ids=["test-mode-wins", "test-mode-wins-the-other-way", "signup-outside-test-mode"],
    )
    async def test_the_test_mode_switch_governs_only_while_test_mode_is_on(
        self, db_path, test_mode, test_nationality, signup, expected
    ):
        from leaguebot.core.services.drivers.driver_names import read_nationality_collected

        await self._set(
            db_path, test_mode=test_mode, test_nationality=test_nationality, signup=signup
        )

        assert await read_nationality_collected(db_path, _hook(db_path)) is expected

    async def test_the_switch_defaults_on_for_a_server_already_configured(self, db_path):
        """Migration 042 fills the existing rows in place, so no wipe is needed."""
        from leaguebot.core.services.drivers.driver_names import read_nationality_collected

        async with get_connection(db_path) as db:
            await db.execute(
                "UPDATE server_configs SET test_mode_active = 1 WHERE server_id = ?",
                (SERVER_ID,),
            )
            await db.commit()

        assert await read_nationality_collected(db_path, _hook(db_path)) is True

    async def test_the_preview_reads_it_through_the_same_reader(self, bot, db_path):
        """The preview's switch is core's reader's, not a second implementation."""
        from leaguebot.image.services.preview.image_preview_service import (
            PreviewContext,
            _load_teams_and_drivers,
        )

        await self._set(db_path, test_mode=1, test_nationality=0, signup=1)
        context = PreviewContext(
            season_number=1, division_id=1, division_name="Pro", division_tier=1
        )

        await _load_teams_and_drivers(bot, context, _hook(db_path))

        assert context.nationality_collected is False
