"""`test_roster_service` — the fake driver roster of test mode.

A fake driver is created with an ID and a name alone, at Not Signed Up: its division, team and
seat are decided as any driver's, by `/driver assign` once its signup is approved, and its
nationality by its signup. These cover the creation, the name it is held to, and its removal.
"""
from __future__ import annotations

import aiosqlite
import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support.delete_hooks import DRIVER_HOOKS, delete_hooks


SERVER_ID = 7373
DIVISION = "Division 1"

_CREATE_ONLY = "#600: a fake driver is still created seated, from a team and a division"


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


async def _add(db_path, name="Mock Alpha"):
    """Create the fake driver *name* through the roster's writer, on a connection of the test's
    own, committed as a change's save commits it."""
    from leaguebot.core.services.test_mode.test_roster_service import add_test_driver_on

    async with get_connection(db_path) as db:
        result = await add_test_driver_on(db, name)
        await db.commit()
    return result


async def _remove(db_path, discord_user_id: int):
    """Remove the fake driver *discord_user_id* through the roster's writer, on a connection of
    the test's own, committed as a change's save commits it."""
    from leaguebot.core.services.test_mode.test_roster_service import remove_test_driver_on

    async with get_connection(db_path) as db:
        result = await remove_test_driver_on(
            db, discord_user_id, **delete_hooks(*DRIVER_HOOKS)
        )
        await db.commit()
    return result


# ── A fake driver is created, and nothing more ───────────────────────────


@pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
async def test_each_driver_takes_the_next_synthetic_id_and_no_seat(db_path):
    """Mock Alpha and then Mock Bravo are created in a season whose team has two free seats. Each
    takes the next synthetic ID, Mock Bravo's one past Mock Alpha's; both are at Not Signed Up,
    and both of the team's seats are still free."""
    first = await _add(db_path, name="Mock Alpha")
    second = await _add(db_path, name="Mock Bravo")

    assert second["discord_user_id"] == first["discord_user_id"] + 1
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT current_state FROM driver_profiles ORDER BY id")
        assert [r[0] for r in await cursor.fetchall()] == ["NOT_SIGNED_UP"] * 2
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        assert (await cursor.fetchone())[0] == 0


# ── A mock driver's name is held to the rules every typed name is (#362) ──


class TestTheNameIsChecked:
    @pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
    async def test_a_test_driver_named_with_a_role_mention_is_refused(self, db_path):
        """A mock driver's name is drawn on graphics as a real driver's is."""
        result = await _add(db_path, name="<@&987654321098765432> Alpha")

        assert isinstance(result, str)
        assert "driver name" in result

    @pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
    async def test_a_test_driver_named_with_an_emoji_is_refused(self, db_path):
        result = await _add(db_path, name="Alpha \U0001F3CE\uFE0F")

        assert isinstance(result, str)


    @pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
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

    @pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
    async def test_a_driver_holding_attendance_and_standings_rows_is_deleted(self, db_path):
        """A fake driver holding an attendance row and a standings snapshot is removed: its profile
        and attendance row go and the snapshot is kept by its ID, unlinked. The deletion `remove`
        once wrote by hand left both in place and was refused by a foreign key."""
        result = await _add(db_path)
        uid, pid = result["discord_user_id"], result["profile_id"]
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT id FROM divisions")
            division_id = (await cursor.fetchone())["id"]
            cursor = await db.execute(
                "INSERT INTO rounds (division_id, round_number, format, track_name, scheduled_at) "
                "VALUES (?, 1, 'NORMAL', 'Silverstone Circuit', '2026-06-01T14:00:00+00:00')",
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

        removed = await _remove(db_path, uid)

        assert removed["display_name"] == "Mock Alpha"
        async with get_connection(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM driver_profiles WHERE id = ?", (pid,))
            assert (await cursor.fetchone())[0] == 0
            cursor = await db.execute("SELECT COUNT(*) FROM driver_round_attendance")
            assert (await cursor.fetchone())[0] == 0
            cursor = await db.execute(
                "SELECT driver_user_id, driver_profile_id FROM driver_standings_snapshots"
            )
            assert [tuple(r) for r in await cursor.fetchall()] == [(uid, None)]

    @pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
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

        await _remove(db_path, result["discord_user_id"])

        async with get_connection(db_path) as db:
            cursor = await db.execute(
                "SELECT discord_user_id, driver_profile_id FROM driver_history_entries"
            )
            rows = [tuple(r) for r in await cursor.fetchall()]
        assert rows == [(uid, None)]


# ── A profile that cannot be written (#442) ───────────────────────────────


@pytest.mark.xfail(strict=True, reason=_CREATE_ONLY)
async def test_a_test_driver_profile_that_cannot_be_written_raises(db_path):
    """A fault in the database is the bot's, not a refusal the manager can act on: it is
    raised, so the command's failure path reports it, and nothing is written."""
    import sqlite3

    async with get_connection(db_path) as db:
        await db.execute(
            "CREATE TRIGGER refuse_the_profile BEFORE INSERT ON driver_profiles "
            "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
        )
        await db.commit()

    with pytest.raises(sqlite3.Error):
        await _add(db_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) FROM driver_profiles")
        assert (await cursor.fetchone())[0] == 0
