"""`test_roster_service` — the fake driver roster of test mode.

The service had no test file of its own until the nationality of a mock driver was added
to it. These cover that addition and the seating rules it sits inside, which nothing else
asserts directly: the preview suite goes through the service but only ever to arrange a
league, so a failure there says nothing about which rule broke.

A mock driver's nationality is stored the way a real driver's is — the canonical
Title-Case adjective NATIONALITY_LOOKUP maps to — so that the country a flag is resolved
from is derived from it identically. That is the whole point of validating it here rather
than taking what was typed.
"""
from __future__ import annotations

import aiosqlite
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.test_roster_service import (
    add_test_driver,
    list_test_drivers,
    remove_test_driver,
)

SERVER_ID = 7373
DIVISION = "Division 1"


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "roster.db")
    await run_migrations(path)
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    await _seed_season(path)
    return path


async def _seed_season(db_path):
    """One SETUP season, one division, one two-seat team and a reserve team."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number) "
            "VALUES ('2026-03-01', 'SETUP', 1)"
        )
        season_id = cursor.lastrowid
        cursor = await db.execute(
            "INSERT INTO divisions (season_id, name, mention_role_id, status, tier) "
            "VALUES (?, ?, 1, 'ACTIVE', 1)",
            (season_id, DIVISION),
        )
        division_id = cursor.lastrowid
        for name, seats, reserve in (("Redline", 2, 0), ("Reserve", 0, 1)):
            cursor = await db.execute(
                "INSERT INTO team_instances (division_id, name, full_name, max_seats, is_reserve) "
                "VALUES (?, ?, ?, ?, ?)",
                (division_id, name, name, seats, reserve),
            )
            team_id = cursor.lastrowid
            for seat_number in range(1, seats + 1):
                await db.execute(
                    "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                    (team_id, seat_number),
                )
        await db.commit()


async def _add(db_path, name="Mock Alpha", team="Redline", **kwargs):
    return await add_test_driver(name, team, DIVISION, db_path, **kwargs)


async def _stored_nationality(db_path, profile_id: int):
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT test_nationality FROM driver_profiles WHERE id = ?", (profile_id,)
            )
        ).fetchone()
    return row["test_nationality"]


# ── The nationality a mock driver is created with ─────────────────────────


class TestTheNationalityIsRecorded:
    async def test_an_adjective_is_stored_canonically(self, db_path):
        result = await _add(db_path, nationality="british")

        assert result["nationality"] == "British"
        assert await _stored_nationality(db_path, result["profile_id"]) == "British"

    async def test_a_country_name_is_stored_as_the_adjective(self, db_path):
        """The wizard accepts either, and stores the one flags are resolved from."""
        result = await _add(db_path, nationality="United Kingdom")

        assert result["nationality"] == "British"

    async def test_other_is_a_value_and_not_an_absence(self, db_path):
        result = await _add(db_path, nationality="OTHER")

        assert result["nationality"] == "Other"

    async def test_omitting_it_records_none(self, db_path):
        result = await _add(db_path)

        assert result["nationality"] is None
        assert await _stored_nationality(db_path, result["profile_id"]) is None

    async def test_a_blank_string_records_none(self, db_path):
        result = await _add(db_path, nationality="   ")

        assert result["nationality"] is None


class TestAnUnusableNationalityIsRefused:
    async def test_an_unknown_word_is_refused(self, db_path):
        result = await _add(db_path, nationality="Martian")

        assert isinstance(result, str)
        assert "Martian" in result

    async def test_a_two_letter_code_is_refused(self, db_path):
        """The wizard rejects ISO codes, so the roster command must too."""
        result = await _add(db_path, nationality="gb")

        assert isinstance(result, str)

    async def test_no_driver_is_created_by_a_refused_command(self, db_path):
        await _add(db_path, nationality="Martian")

        async with get_connection(db_path) as db:
            row = await (
                await db.execute(
                    "SELECT COUNT(*) AS n FROM driver_profiles",
                )
            ).fetchone()
        assert row["n"] == 0

    async def test_it_is_refused_before_the_season_is_even_looked_for(self, db_path):
        """A typo is a typo whatever state the league is in, and says so plainly."""
        async with get_connection(db_path) as db:
            await db.execute("UPDATE seasons SET status = 'COMPLETED'")
            await db.commit()

        result = await _add(db_path, nationality="Martian")

        assert isinstance(result, str)
        assert "nationality" in result.lower()


# ── Reading the roster back ───────────────────────────────────────────────


class TestTheRosterListCarriesIt:
    async def test_each_driver_reports_its_own(self, db_path):
        await _add(db_path, name="Mock Alpha", nationality="Dutch")
        await _add(db_path, name="Mock Bravo", nationality="brazil")

        drivers = await list_test_drivers(DIVISION, db_path)

        assert [(d["display_name"], d["nationality"]) for d in drivers] == [
            ("Mock Alpha", "Dutch"),
            ("Mock Bravo", "Brazilian"),
        ]

    async def test_a_driver_created_without_one_reports_none(self, db_path):
        await _add(db_path)

        drivers = await list_test_drivers(DIVISION, db_path)

        assert drivers[0]["nationality"] is None

    async def test_a_removed_driver_takes_its_nationality_with_it(self, db_path):
        result = await _add(db_path, nationality="Dutch")

        await remove_test_driver(result["discord_user_id"], db_path)

        assert await list_test_drivers(DIVISION, db_path) == []


# ── The seating rules the nationality sits inside ─────────────────────────


class TestSeating:
    """Not new, but unasserted until now, and the nationality rides on all of it."""

    async def test_a_full_team_is_refused(self, db_path):
        await _add(db_path, name="Mock Alpha")
        await _add(db_path, name="Mock Bravo")

        result = await _add(db_path, name="Mock Charlie")

        assert isinstance(result, str)
        assert "No free seats" in result

    async def test_the_reserve_team_grows_a_seat_instead(self, db_path):
        first = await _add(db_path, name="Mock Alpha", team="Reserve", nationality="Dutch")
        second = await _add(db_path, name="Mock Bravo", team="Reserve")

        assert not isinstance(first, str)
        assert not isinstance(second, str)
        assert second["discord_user_id"] == first["discord_user_id"] + 1

    async def test_an_unknown_team_is_refused(self, db_path):
        result = await _add(db_path, team="Nowhere", nationality="Dutch")

        assert isinstance(result, str)
        assert "Nowhere" in result

    async def test_a_team_is_named_by_its_shorthand_in_any_case(self, db_path):
        result = await _add(db_path, team="REDLINE")

        assert not isinstance(result, str), result

    async def test_a_role_mention_is_refused_saying_to_use_the_shorthand(self, db_path):
        """A role is only for mentioning a team, never for naming one (#381)."""
        result = await _add(db_path, team="<@&123456789012345678>")

        assert isinstance(result, str)
        assert "Name a team by its shorthand." in result


# ── A mock driver's name is held to the rules every typed name is (#362) ──


class TestTheNameIsChecked:
    async def test_a_test_driver_named_with_a_role_mention_is_refused(self, db_path):
        """A mock driver's name is drawn on graphics as a real driver's is."""
        result = await _add(db_path, name="<@&987654321098765432> Alpha")

        assert isinstance(result, str)
        assert "driver name" in result

    async def test_a_test_driver_named_with_an_emoji_is_refused(self, db_path):
        result = await _add(db_path, name="Alpha \U0001F3CE\uFE0F")

        assert isinstance(result, str)


    async def test_a_test_driver_named_with_a_member_mention_is_refused(self, db_path):
        """The name is printed wherever the driver is, so the member would be notified each
        time (#388)."""
        result = await _add(db_path, name="<@123456789012345678> Alpha")

        assert isinstance(result, str)
        assert "driver name" in result
        assert "member" in result


# ── Removing one driver (#268) ────────────────────────────────────────────


class TestRemovingOneDriver:
    """`roster remove` deletes by the route `roster clear` and switching test mode off take."""

    async def test_a_driver_holding_attendance_and_standings_rows_is_deleted(self, db_path):
        """The command is refused outside Placements, before any round has run, so only a fake
        driver outliving a season whose switch-off failed holds either. The deletion `remove`
        once wrote by hand left both in place and was refused by a foreign key."""
        result = await _add(db_path)
        uid, pid = result["discord_user_id"], result["profile_id"]
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT id FROM divisions")
            division_id = (await cursor.fetchone())["id"]
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at) "
                "VALUES (?, 1, 'NORMAL', 'Silverstone Circuit', '2026-06-01T14:00:00')",
                (division_id,),
            )
            round_id = cursor.lastrowid
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id) "
                "VALUES (?, ?, ?)",
                (round_id, division_id, pid),
            )
            await db.execute(
                "INSERT INTO driver_standings_snapshots (round_id, division_id, driver_user_id, "
                "standing_position, driver_profile_id) VALUES (?, ?, ?, 1, ?)",
                (round_id, division_id, uid, pid),
            )
            await db.commit()

        removed = await remove_test_driver(uid, db_path)

        assert removed == {"display_name": "Mock Alpha", "team_name": "Redline"}
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM driver_profiles WHERE id = ?", (pid,))
            assert (await cursor.fetchone())[0] == 0
            cursor = await db.execute("SELECT COUNT(*) FROM driver_round_attendance")
            assert (await cursor.fetchone())[0] == 0
            cursor = await db.execute(
                "SELECT driver_user_id, driver_profile_id FROM driver_standings_snapshots"
            )
            assert [tuple(r) for r in await cursor.fetchall()] == [(uid, None)]

    async def test_their_history_is_kept_by_identifier(self, db_path):
        """As switching test mode off keeps it: a driver created again under the same
        identifier holds it as their own (#220)."""
        result = await _add(db_path)
        uid = str(result["discord_user_id"])
        async with get_connection(db_path) as db:
            await db.execute(
                "INSERT INTO driver_history_entries (discord_user_id, driver_profile_id, "
                "season_number, division_name) VALUES (?, ?, 1, ?)",
                (uid, result["profile_id"], DIVISION),
            )
            await db.commit()

        await remove_test_driver(result["discord_user_id"], db_path)

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT discord_user_id, driver_profile_id FROM driver_history_entries"
            )
            rows = [tuple(r) for r in await cursor.fetchall()]
        assert rows == [(uid, None)]
