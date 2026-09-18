"""`DriverService` — re-keying a profile, the former-driver flag, and what a departure leaves.

Issue #208. `tests/unit/test_driver_service_transitions.py` covers which transitions the state
machine allows. What it does not cover is what the allowed ones actually *do* to the database,
nor `reassign_user_id` and `set_former_driver` at all.

**Reaching `NOT_SIGNED_UP` destroys nothing** (issue #220, which reversed the deletion this
file was first written against). A driver without the former-driver flag is *pending
deletion* and is deleted by the pass that ends the season, not by the transition; a former
driver is kept outright, their signups kept whole as the season's history. The two branches
are pinned separately because they share a call site and a reader could easily make one do
the other's work.

**Re-keying is how a driver who lost their Discord account keeps their history.** Since
issue #243 it rewrites nothing: the profile's current account changes, the old one stays in
the driver's list of accounts, and every record keeps the account it was written under.
`test_a_re_key_rewrites_no_record_of_the_driver` compares the whole database to hold that.
`test_driver_reassign.py` pins what refuses it and what merges; the refusals kept here are
the ones about records, and each leaves everything as it was.

**Both write audit entries carrying the old and the new value.** They are the league's only
record of a manager re-keying or re-flagging a driver, which are the two commands that can
quietly rewrite who somebody is.

Everything runs against a real migrated database: the refusals reach the results through their
division, and the audit inserts are raw SQL — none of which a double would check.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.driver_profile import DriverState  # noqa: E402
from services.driver_service import DriverService  # noqa: E402

SERVER_ID = 9108
OLD_USER = "4242"
NEW_USER = "5353"
ACTOR_ID = 77

#: The league's season, division, round and session are all seeded under this id, so that
#: one number names the whole of its racing.
LEAGUE = 1


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "driver_service.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    await _seed_league(db_path, SERVER_ID, LEAGUE)
    return db_path


async def _seed_league(db_path: str, server_id: int, league: int) -> None:
    """Seed one league's season, division, round and race session, all under *league*.

    A standings snapshot and a session result carry no server of their own; they reach one
    through their division, so the tests need the chain to exist to be scoped by it at all.
    """
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT OR IGNORE INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (server_id,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', 'ACTIVE', 1, 'ONGOING')",
            (league,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (?, ?, 'Pro', 1001, 1, 'ACTIVE')",
            (league, league),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at, status) "
            "VALUES (?, ?, 1, 'NORMAL', '2026-09-20T18:00:00', 'FINAL')",
            (league, league),
        )
        await db.execute(
            "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
            "VALUES (?, ?, ?, 'RACE', 'ACTIVE')",
            (league, league, league),
        )
        await db.commit()


async def _seed_snapshot(
    db_path: str, user_id: str = OLD_USER, league: int = LEAGUE, *, points: int = 88
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_standings_snapshots "
            "(round_id, division_id, driver_user_id, standing_position, total_points) "
            "VALUES (?, ?, ?, 2, ?)",
            (league, league, user_id, points),
        )
        await db.commit()


async def _seed_results(db_path: str, user_id: str = OLD_USER, league: int = LEAGUE) -> None:
    """One race result and one qualifying result for *user_id* in *league*'s race session."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position, points_awarded) VALUES (?, ?, 501, 2, 18)",
            (league, user_id),
        )
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position) VALUES (?, ?, 501, 3)",
            (league, user_id),
        )
        await db.commit()


async def _seed_history(
    db_path: str, user_id: str = OLD_USER, *, server_id: int = SERVER_ID, season: int = 1
) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_history_entries (discord_user_id, season_number, "
            "division_name, division_tier, final_position, final_points) "
            "VALUES (?, ?, 'Pro', 1, 2, 88)",
            (user_id, season),
        )
        await db.commit()


async def _seed_wizard(db_path: str, user_id: str, *, state: str = "COLLECTING") -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_wizard_records (discord_user_id, wizard_state, "
            "draft_answers_json) VALUES (?, ?, ?)",
            (user_id, state, '{"platform": "Steam"}'),
        )
        await db.commit()


async def _standings_users(db_path: str) -> list[tuple[int, int]]:
    """Every standings snapshot as (division, driver), sorted — never in insertion order."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT division_id, driver_user_id FROM driver_standings_snapshots "
            "ORDER BY division_id, driver_user_id"
        )
        return [(r["division_id"], r["driver_user_id"]) for r in await cursor.fetchall()]


async def _result_users(db_path: str, table: str) -> list[tuple[int, int]]:
    """Every row of *table* as (session, driver), sorted."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT session_result_id, driver_user_id FROM {table} "
            "ORDER BY session_result_id, driver_user_id"
        )
        return [(r["session_result_id"], r["driver_user_id"]) for r in await cursor.fetchall()]


async def _seed_profile(
    db_path: str,
    *,
    user_id: str = OLD_USER,
    state: str = "UNASSIGNED",
    former: bool = False,
    server_id: int = SERVER_ID,
) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles "
            "(discord_user_id, current_state, former_driver) VALUES (?, ?, ?)",
            (user_id, state, int(former)),
        )
        await db.commit()
        return cursor.lastrowid


async def _seed_signup_record(db_path: str, user_id: str = OLD_USER) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records "
            "(discord_user_id, discord_username, server_display_name, platform) "
            "VALUES (?, 'lewis', 'Lewis Hamilton', 'Steam')",
            (user_id,),
        )
        await db.commit()


async def _profile_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_profiles"
        )
        return (await cursor.fetchone())["n"]


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_id FROM audit_entries "
            " ORDER BY id",
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Reading a profile
# ---------------------------------------------------------------------------


async def test_a_user_with_no_profile_reads_as_none(tmp_path):
    """An absent profile is implicitly NOT_SIGNED_UP, so the absence is meaningful rather
    than an error."""
    service = DriverService(await _make_db(tmp_path))

    assert await service.get_profile(OLD_USER) is None


# ---------------------------------------------------------------------------
# The destructive transition
# ---------------------------------------------------------------------------


async def test_an_ordinary_driver_is_kept_pending_deletion(tmp_path):
    """Issue #220: nothing is deleted on reaching Not Signed Up. A driver who never raced is
    pending deletion, and the season's end deletes them."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, state="UNASSIGNED", former=False)
    service = DriverService(db_path)

    result = await service.transition(OLD_USER, DriverState.NOT_SIGNED_UP)

    assert result is not None
    assert result.current_state is DriverState.NOT_SIGNED_UP
    assert await _profile_count(db_path) == 1


async def test_a_former_driver_is_kept_with_their_signup(tmp_path):
    """`former_driver` records that somebody raced in this league once, so the profile is
    kept — and their signups with it, those being season history (issue #220)."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, state="UNASSIGNED", former=True)
    await _seed_signup_record(db_path)
    service = DriverService(db_path)

    result = await service.transition(OLD_USER, DriverState.NOT_SIGNED_UP)

    assert result is not None
    assert await _profile_count(db_path) == 1
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT platform FROM signup_records WHERE discord_user_id = ?",
            (OLD_USER,),
        )
        row = await cursor.fetchone()
    assert row is not None and row["platform"] is not None


async def test_a_transition_from_no_profile_creates_one(tmp_path):
    """An absent profile is NOT_SIGNED_UP, so a driver starting a signup has one made for
    them rather than being refused."""
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    profile = await service.transition(
        OLD_USER, DriverState.PENDING_SIGNUP_COMPLETION
    )

    assert profile is not None
    assert profile.current_state == DriverState.PENDING_SIGNUP_COMPLETION
    assert await _profile_count(db_path) == 1


async def test_a_disallowed_transition_from_no_profile_names_what_is_allowed(tmp_path):
    """The caller is usually a command, and the message reaches a manager."""
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="Allowed targets"):
        await service.transition(OLD_USER, DriverState.ASSIGNED)


# ---------------------------------------------------------------------------
# Re-keying a profile
# ---------------------------------------------------------------------------


async def test_a_profile_is_re_keyed_to_the_new_account(tmp_path):
    """How a driver who lost their Discord account keeps their history."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    service = DriverService(db_path)

    outcome = await service.reassign_user_id(
        OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    assert outcome.profile.discord_user_id == NEW_USER
    assert await service.get_profile(NEW_USER) is not None
    assert await service.get_profile(OLD_USER) is None


async def test_re_keying_a_user_with_no_profile_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="No driver profile"):
        await service.reassign_user_id(
            OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_two_drivers_both_in_the_live_season_are_not_merged(tmp_path):
    """The new account is a driver of its own, and both are signed up for the live season.
    Merging them is the one thing an account holding a profile could mean (issue #243), and
    two live signups cannot be one driver."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_profile(db_path, user_id=NEW_USER)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="both hold a seat or a signup"):
        await service.reassign_user_id(
            OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_an_account_whose_leftover_results_share_a_division_is_refused(tmp_path):
    """The new account holds no profile but results of its own — a driver deleted for never
    having raced a round in full — in the very division the driver raced. Joined, one person
    would stand twice in that season's standings (issue #243)."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_results(db_path, user_id=OLD_USER)
    await _seed_results(db_path, user_id=NEW_USER)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="both took part in Season 1 Pro"):
        await service.reassign_user_id(
            OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_an_account_holding_only_history_of_its_own_is_accepted(tmp_path):
    """Nothing is rewritten, so leftover history under the new account simply becomes the
    driver's — it shares no division with anything of theirs (issue #243)."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_history(db_path, NEW_USER)
    service = DriverService(db_path)

    outcome = await service.reassign_user_id(
        OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    assert outcome.profile.discord_user_id == NEW_USER


async def test_a_refused_re_key_over_racing_records_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_snapshot(db_path, user_id=NEW_USER)
    await _seed_snapshot(db_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError):
        await service.reassign_user_id(
            OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )

    assert await service.get_profile(OLD_USER) is not None
    assert await _standings_users(db_path) == [
        (LEAGUE, int(OLD_USER)),
        (LEAGUE, int(NEW_USER)),
    ]
    assert await _audit(db_path) == []


async def test_a_refused_re_key_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_profile(db_path, user_id=NEW_USER)
    service = DriverService(db_path)

    with pytest.raises(ValueError):
        await service.reassign_user_id(
            OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )

    assert await service.get_profile(OLD_USER) is not None
    assert await _audit(db_path) == []


async def test_a_re_key_is_audited_with_both_accounts(tmp_path):
    """The league's only record that a driver's history was moved between accounts."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)

    await DriverService(db_path).reassign_user_id(
        OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    entry = (await _audit(db_path))[0]
    assert entry["change_type"] == "DRIVER_USER_ID_REASSIGN"
    assert entry["old_value"] == OLD_USER
    assert entry["new_value"] == NEW_USER
    assert entry["actor_id"] == ACTOR_ID


# ---------------------------------------------------------------------------
# The former-driver flag
# ---------------------------------------------------------------------------


async def test_the_former_driver_flag_is_set_and_reports_both_values(tmp_path):
    """The return value is what the command echoes back, so a manager can see it changed
    rather than merely that the command ran."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, former=False)
    service = DriverService(db_path)

    old, new = await service.set_former_driver(
        OLD_USER, True, ACTOR_ID, "Manager"
    )

    assert (old, new) == (False, True)
    profile = await service.get_profile(OLD_USER)
    assert profile.former_driver is True


async def test_the_former_driver_flag_can_be_cleared_again(tmp_path):
    """Set by mistake, it would otherwise keep a profile alive through every departure."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, former=True)
    service = DriverService(db_path)

    old, new = await service.set_former_driver(
        OLD_USER, False, ACTOR_ID, "Manager"
    )

    assert (old, new) == (True, False)
    profile = await service.get_profile(OLD_USER)
    assert profile.former_driver is False


async def test_setting_the_flag_for_a_user_with_no_profile_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="No driver profile"):
        await service.set_former_driver(
            OLD_USER, True, ACTOR_ID, "Manager"
        )


async def test_setting_the_flag_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, former=False)

    await DriverService(db_path).set_former_driver(
        OLD_USER, True, ACTOR_ID, "Manager"
    )

    entry = (await _audit(db_path))[0]
    assert entry["change_type"] == "TEST_FORMER_DRIVER_FLAG_SET"
    assert entry["old_value"] != entry["new_value"]


async def test_the_flag_decides_whether_a_departure_destroys_the_profile(tmp_path):
    """The two halves of this file meeting: setting the flag changes what the destructive
    transition does to the same driver."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, state="UNASSIGNED", former=False)
    service = DriverService(db_path)
    await service.set_former_driver(OLD_USER, True, ACTOR_ID, "Manager")

    await service.transition(OLD_USER, DriverState.NOT_SIGNED_UP)

    assert await _profile_count(db_path) == 1


async def test_the_flag_is_set_through_a_past_account(tmp_path):
    """Any account names the driver (issue #243), the test-mode command included."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    service = DriverService(db_path)
    await service.reassign_user_id(OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    assert await service.set_former_driver(
        OLD_USER, True, ACTOR_ID, "Manager"
    ) == (False, True)


# ---------------------------------------------------------------------------
# A re-key rewrites nothing (issue #243)
# ---------------------------------------------------------------------------
#
# Issue #222 carried every record naming a driver onto their new account, and kept a
# hand-written list of those columns so a table added later could not be missed. Issue #243
# withdrew the carrying: a record keeps the account it was written under, and the bot maps a
# past account to the current one wherever it reads. The guard is now the opposite one, and
# needs no list — it compares the whole database, so a table added later is covered already.

#: The only tables a re-key writes: the profile's current account, the list of its accounts,
#: and the audit entry recording that it happened.
_WRITTEN_BY_A_RE_KEY = {"driver_profiles", "driver_accounts", "audit_entries"}


async def _every_other_row(db_path: str) -> dict[str, list[tuple]]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [r["name"] for r in await cursor.fetchall()]
        rows: dict[str, list[tuple]] = {}
        for table in tables:
            if table in _WRITTEN_BY_A_RE_KEY:
                continue
            cursor = await db.execute(f"SELECT * FROM '{table}'")
            rows[table] = sorted(
                (tuple(r) for r in await cursor.fetchall()), key=repr
            )
    return rows


async def test_a_re_key_rewrites_no_record_of_the_driver(tmp_path):
    """Results, standings, signups, history, a part-finished signup and the fastest-lap
    override all stand under the old account, and every one of them is left exactly so."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_signup_record(db_path)
    await _seed_snapshot(db_path)
    await _seed_results(db_path)
    await _seed_history(db_path)
    await _seed_wizard(db_path, OLD_USER)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE session_results SET fl_driver_override = ? WHERE id = ?",
            (OLD_USER, LEAGUE),
        )
        await db.commit()
    before = await _every_other_row(db_path)

    await DriverService(db_path).reassign_user_id(
        OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    assert await _every_other_row(db_path) == before


async def test_a_re_key_keeps_the_old_account_listed(tmp_path):
    db_path = await _make_db(tmp_path)
    profile_id = await _seed_profile(db_path)

    await DriverService(db_path).reassign_user_id(
        OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id, discord_user_id FROM driver_accounts "
            "ORDER BY discord_user_id"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [
            (profile_id, OLD_USER),
            (profile_id, NEW_USER),
        ]
