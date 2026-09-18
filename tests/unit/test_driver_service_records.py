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

**Re-keying is how a driver who lost their Discord account keeps their history**, so every
record naming that driver by their account is carried with the profile: their signups, their
session results, their standings, a session's fastest-lap override and their history entries
(issue #222). Each is pinned on its own, and `_DRIVER_COLUMNS` at the foot of this file
guards the set against a table added later. Three things refuse it — no profile at the old
account, a profile already at the new one, or racing records of its own at the new one — and
each refusal leaves everything as it was.

**Both write audit entries carrying the old and the new value.** They are the league's only
record of a manager re-keying or re-flagging a driver, which are the two commands that can
quietly rewrite who somebody is.

Everything runs against a real migrated database: the re-key crosses seven tables and reaches
the results through their division, and the audit inserts are raw SQL — none of which a
double would check.
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

#: A second league on the same bot, for the tests that pin the scope of a re-key.
OTHER_SERVER = 9109

#: Each league's season, division, round and session are all seeded under this id, so that
#: one number names the whole of a league's racing and no two leagues share a row.
LEAGUE, OTHER_LEAGUE = 1, 2


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
            "INSERT INTO seasons (id, server_id, start_date, status, season_number, stage) "
            "VALUES (?, ?, '2026-09-17', 'ACTIVE', 1, 'ONGOING')",
            (league, server_id),
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
            "INSERT INTO driver_history_entries (server_id, discord_user_id, season_number, "
            "division_name, division_tier, final_position, final_points) "
            "VALUES (?, ?, ?, 'Pro', 1, 2, 88)",
            (server_id, user_id, season),
        )
        await db.commit()


async def _seed_wizard(db_path: str, user_id: str, *, state: str = "COLLECTING") -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_wizard_records (server_id, discord_user_id, wizard_state, "
            "draft_answers_json) VALUES (?, ?, ?, ?)",
            (SERVER_ID, user_id, state, '{"platform": "Steam"}'),
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
            "(server_id, discord_user_id, current_state, former_driver) VALUES (?, ?, ?, ?)",
            (server_id, user_id, state, int(former)),
        )
        await db.commit()
        return cursor.lastrowid


async def _seed_signup_record(db_path: str, user_id: str = OLD_USER) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records "
            "(server_id, discord_user_id, discord_username, server_display_name, platform) "
            "VALUES (?, ?, 'lewis', 'Lewis Hamilton', 'Steam')",
            (SERVER_ID, user_id),
        )
        await db.commit()


async def _profile_count(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM driver_profiles WHERE server_id = ?", (SERVER_ID,)
        )
        return (await cursor.fetchone())["n"]


async def _audit(db_path: str) -> list[dict]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, old_value, new_value, actor_id FROM audit_entries "
            "WHERE server_id = ? ORDER BY id",
            (SERVER_ID,),
        )
        return [dict(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# Reading a profile
# ---------------------------------------------------------------------------


async def test_a_user_with_no_profile_reads_as_none(tmp_path):
    """An absent profile is implicitly NOT_SIGNED_UP, so the absence is meaningful rather
    than an error."""
    service = DriverService(await _make_db(tmp_path))

    assert await service.get_profile(SERVER_ID, OLD_USER) is None


async def test_another_server_s_driver_is_not_visible(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    service = DriverService(db_path)

    assert await service.get_profile(SERVER_ID + 1, OLD_USER) is None


# ---------------------------------------------------------------------------
# The destructive transition
# ---------------------------------------------------------------------------


async def test_an_ordinary_driver_is_kept_pending_deletion(tmp_path):
    """Issue #220: nothing is deleted on reaching Not Signed Up. A driver who never raced is
    pending deletion, and the season's end deletes them."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, state="UNASSIGNED", former=False)
    service = DriverService(db_path)

    result = await service.transition(SERVER_ID, OLD_USER, DriverState.NOT_SIGNED_UP)

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

    result = await service.transition(SERVER_ID, OLD_USER, DriverState.NOT_SIGNED_UP)

    assert result is not None
    assert await _profile_count(db_path) == 1
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT platform FROM signup_records WHERE server_id = ? AND discord_user_id = ?",
            (SERVER_ID, OLD_USER),
        )
        row = await cursor.fetchone()
    assert row is not None and row["platform"] is not None


async def test_a_transition_from_no_profile_creates_one(tmp_path):
    """An absent profile is NOT_SIGNED_UP, so a driver starting a signup has one made for
    them rather than being refused."""
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    profile = await service.transition(
        SERVER_ID, OLD_USER, DriverState.PENDING_SIGNUP_COMPLETION
    )

    assert profile is not None
    assert profile.current_state == DriverState.PENDING_SIGNUP_COMPLETION
    assert await _profile_count(db_path) == 1


async def test_a_disallowed_transition_from_no_profile_names_what_is_allowed(tmp_path):
    """The caller is usually a command, and the message reaches a manager."""
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="Allowed targets"):
        await service.transition(SERVER_ID, OLD_USER, DriverState.ASSIGNED)


# ---------------------------------------------------------------------------
# Re-keying a profile
# ---------------------------------------------------------------------------


async def test_a_profile_is_re_keyed_to_the_new_account(tmp_path):
    """How a driver who lost their Discord account keeps their history."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    service = DriverService(db_path)

    profile = await service.reassign_user_id(
        SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    assert profile.discord_user_id == NEW_USER
    assert await service.get_profile(SERVER_ID, NEW_USER) is not None
    assert await service.get_profile(SERVER_ID, OLD_USER) is None


async def test_re_keying_carries_every_signup_to_the_new_account(tmp_path):
    """Issue #220: signups are kept under the account, so they must move with the profile."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_signup_record(db_path)
    await _seed_signup_record(db_path)
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, COUNT(*) AS n FROM signup_records GROUP BY discord_user_id"
        )
        rows = {row["discord_user_id"]: row["n"] for row in await cursor.fetchall()}
    assert rows == {NEW_USER: 2}


async def test_re_keying_carries_the_driver_s_standings_to_the_new_account(tmp_path):
    """Issue #222. Keyed by the abandoned account, a driver's points join to no profile: the
    standings draw them as a raw snowflake and the season's end reads their final standing
    as nothing at all."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_snapshot(db_path)
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    assert await _standings_users(db_path) == [(LEAGUE, int(NEW_USER))]


async def test_re_keying_carries_both_session_result_tables_to_the_new_account(tmp_path):
    """A driver's results are the source of the standings and of every statistic, so both
    tables move or the standings are rebuilt under the old account at the next round."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_results(db_path)
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    assert await _result_users(db_path, "race_session_results") == [(LEAGUE, int(NEW_USER))]
    assert await _result_users(db_path, "qualifying_session_results") == [
        (LEAGUE, int(NEW_USER))
    ]


async def test_re_keying_carries_every_history_entry_to_the_new_account(tmp_path):
    """A history entry names its driver by identifier so that it outlives the profile, which
    is exactly why a re-key has to move it — and only in the league that ordered it."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_history(db_path, season=1)
    await _seed_history(db_path, season=2)
    await _seed_history(db_path, server_id=OTHER_SERVER)
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT server_id, discord_user_id, season_number FROM driver_history_entries "
            "ORDER BY server_id, season_number"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]
    assert rows == [
        (SERVER_ID, NEW_USER, 1),
        (SERVER_ID, NEW_USER, 2),
        (OTHER_SERVER, OLD_USER, 1),
    ]


async def test_re_keying_carries_a_part_finished_signup_to_the_new_account(tmp_path):
    """The driver's answers so far are held against the account, and the review panel reads
    the driver from the wizard record, so the record moves with the profile."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_wizard(db_path, OLD_USER)
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, draft_answers_json FROM signup_wizard_records"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]
    assert rows == [(NEW_USER, '{"platform": "Steam"}')]


async def test_an_abandoned_wizard_on_the_new_account_is_replaced(tmp_path):
    """The new account holds no profile, so a record standing on it is an abandoned draft and
    the live one being carried wins. Left in place it would collide, the account and the
    server being unique together."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    await _seed_wizard(db_path, OLD_USER)
    await _seed_wizard(db_path, NEW_USER, state="UNENGAGED")
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_user_id, wizard_state FROM signup_wizard_records"
        )
        rows = [tuple(r) for r in await cursor.fetchall()]
    assert rows == [(NEW_USER, "COLLECTING")]


async def test_re_keying_carries_the_fastest_lap_override_to_the_new_account(tmp_path):
    """The bonus is recomputed from the override whenever a penalty, an appeal verdict or an
    amendment lands, so an override left on the old account awards it to nobody."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE session_results SET fl_driver_override = ? WHERE id = ?",
            (OLD_USER, LEAGUE),
        )
        await db.commit()
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT fl_driver_override FROM session_results WHERE id = ?", (LEAGUE,)
        )
        assert (await cursor.fetchone())["fl_driver_override"] == int(NEW_USER)


async def test_re_keying_leaves_another_league_s_racing_alone(tmp_path):
    """Neither table holds a server of its own, so an unscoped re-key would move the same
    person's results in every other league this bot serves."""
    db_path = await _make_db(tmp_path)
    await _seed_league(db_path, OTHER_SERVER, OTHER_LEAGUE)
    await _seed_profile(db_path)
    await _seed_profile(db_path, server_id=OTHER_SERVER)
    for league in (LEAGUE, OTHER_LEAGUE):
        await _seed_snapshot(db_path, league=league)
        await _seed_results(db_path, league=league)
    service = DriverService(db_path)

    await service.reassign_user_id(SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager")

    assert await _standings_users(db_path) == [
        (LEAGUE, int(NEW_USER)),
        (OTHER_LEAGUE, int(OLD_USER)),
    ]
    assert await _result_users(db_path, "race_session_results") == [
        (LEAGUE, int(NEW_USER)),
        (OTHER_LEAGUE, int(OLD_USER)),
    ]


async def test_re_keying_a_user_with_no_profile_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="No driver profile"):
        await service.reassign_user_id(
            SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_re_keying_onto_an_occupied_account_is_refused(tmp_path):
    """Two profiles sharing one Discord id would break every lookup in the bot, all of
    which are by that id."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_profile(db_path, user_id=NEW_USER)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="already has a driver profile"):
        await service.reassign_user_id(
            SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_a_re_key_onto_an_account_that_raced_is_refused(tmp_path):
    """The new account holds no profile but holds racing of its own — a driver deleted for
    never having raced a round in full. Carrying a second person's results onto it would
    merge two drivers' records with nothing to separate them again."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_results(db_path, user_id=NEW_USER)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="results, standings or history of their own"):
        await service.reassign_user_id(
            SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_a_re_key_onto_an_account_holding_history_is_refused(tmp_path):
    """History outlives the profile, so an account with none may still hold seasons of it."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_history(db_path, NEW_USER)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="results, standings or history of their own"):
        await service.reassign_user_id(
            SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )


async def test_another_league_s_racing_does_not_refuse_a_re_key(tmp_path):
    """The refusal is the league's own: a person who races in another league on this bot is
    still free to be re-keyed here."""
    db_path = await _make_db(tmp_path)
    await _seed_league(db_path, OTHER_SERVER, OTHER_LEAGUE)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_results(db_path, user_id=NEW_USER, league=OTHER_LEAGUE)
    await _seed_history(db_path, NEW_USER, server_id=OTHER_SERVER)
    service = DriverService(db_path)

    profile = await service.reassign_user_id(
        SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
    )

    assert profile.discord_user_id == NEW_USER


async def test_a_refused_re_key_over_racing_records_changes_nothing(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, user_id=OLD_USER)
    await _seed_snapshot(db_path, user_id=NEW_USER)
    await _seed_snapshot(db_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError):
        await service.reassign_user_id(
            SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )

    assert await service.get_profile(SERVER_ID, OLD_USER) is not None
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
            SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
        )

    assert await service.get_profile(SERVER_ID, OLD_USER) is not None
    assert await _audit(db_path) == []


async def test_a_re_key_is_audited_with_both_accounts(tmp_path):
    """The league's only record that a driver's history was moved between accounts."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path)

    await DriverService(db_path).reassign_user_id(
        SERVER_ID, OLD_USER, NEW_USER, ACTOR_ID, "Manager"
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
        SERVER_ID, OLD_USER, True, ACTOR_ID, "Manager"
    )

    assert (old, new) == (False, True)
    profile = await service.get_profile(SERVER_ID, OLD_USER)
    assert profile.former_driver is True


async def test_the_former_driver_flag_can_be_cleared_again(tmp_path):
    """Set by mistake, it would otherwise keep a profile alive through every departure."""
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, former=True)
    service = DriverService(db_path)

    old, new = await service.set_former_driver(
        SERVER_ID, OLD_USER, False, ACTOR_ID, "Manager"
    )

    assert (old, new) == (True, False)
    profile = await service.get_profile(SERVER_ID, OLD_USER)
    assert profile.former_driver is False


async def test_setting_the_flag_for_a_user_with_no_profile_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    service = DriverService(db_path)

    with pytest.raises(ValueError, match="No driver profile"):
        await service.set_former_driver(
            SERVER_ID, OLD_USER, True, ACTOR_ID, "Manager"
        )


async def test_setting_the_flag_is_audited(tmp_path):
    db_path = await _make_db(tmp_path)
    await _seed_profile(db_path, former=False)

    await DriverService(db_path).set_former_driver(
        SERVER_ID, OLD_USER, True, ACTOR_ID, "Manager"
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
    await service.set_former_driver(SERVER_ID, OLD_USER, True, ACTOR_ID, "Manager")

    await service.transition(SERVER_ID, OLD_USER, DriverState.NOT_SIGNED_UP)

    assert await _profile_count(db_path) == 1


# ---------------------------------------------------------------------------
# Every column that names a driver by their Discord account
# ---------------------------------------------------------------------------
#
# Issue #222 was a hand-written list of tables going stale: the re-key moved two of them and
# nobody noticed the other six. The guard below reads the schema instead of trusting a list,
# so a table added later cannot quietly reintroduce the same defect — it fails here until
# somebody decides, in writing, whether a re-key carries it.


def _names_a_driver(column: str) -> bool:
    """Whether *column* could name a driver: by account, by profile, or by override.

    Deliberately wider than the columns a re-key moves. The point is to catch the next one
    somebody adds, so a match here is a question to answer rather than a fault.
    """
    return "user_id" in column or (
        "driver" in column and (column.endswith("_id") or column.endswith("_override"))
    )


#: Every column the schema has that could name a driver, and what a re-key does with it.
#:
#: `CARRIED` means the re-key moves it, and each one has a test of its own above. Any other
#: value is the reason it is left alone, and reads as a decision rather than an oversight.
CARRIED = "carried by a re-key"

_DRIVER_COLUMNS: dict[tuple[str, str], str] = {
    ("driver_profiles", "discord_user_id"): CARRIED,
    ("signup_records", "discord_user_id"): CARRIED,
    ("driver_history_entries", "discord_user_id"): CARRIED,
    ("signup_wizard_records", "discord_user_id"): CARRIED,
    ("driver_standings_snapshots", "driver_user_id"): CARRIED,
    ("race_session_results", "driver_user_id"): CARRIED,
    ("qualifying_session_results", "driver_user_id"): CARRIED,
    ("session_results", "fl_driver_override"): CARRIED,
    ("driver_portraits", "discord_user_id"): (
        "a cache of that account's own profile picture, so the row and its file are removed "
        "rather than carried: the new account has a picture of its own"
    ),
    ("lap_records", "driver_id"): (
        "migration 029 raised the table as a structural prerequisite and nothing writes it, "
        "so it holds no rows to carry. Whoever populates it answers this question then"
    ),
    ("track_records", "driver_id"): (
        "as lap_records — created by migration 029 and written by nothing"
    ),
    ("driver_history_entries", "driver_profile_id"): (
        "keyed by the profile, which a re-key does not replace"
    ),
    ("driver_standings_snapshots", "driver_profile_id"): "keyed by the profile",
    ("race_session_results", "driver_profile_id"): "keyed by the profile",
    ("qualifying_session_results", "driver_profile_id"): "keyed by the profile",
    ("driver_division_memberships", "driver_profile_id"): "keyed by the profile",
    ("driver_round_attendance", "driver_profile_id"): "keyed by the profile",
    ("driver_season_assignments", "driver_profile_id"): "keyed by the profile",
    ("team_seats", "driver_profile_id"): "keyed by the profile",
    ("driver_accounts", "discord_user_id"): (
        "the list of the driver's accounts itself (issue #243): the migration 059 triggers "
        "add the new account to it, and the old one stays listed"
    ),
    ("driver_accounts", "driver_profile_id"): "keyed by the profile",
}


async def test_every_column_naming_a_driver_is_accounted_for_by_the_re_key(tmp_path):
    """A table added later that names a driver fails here until somebody decides about it.

    Read the failure as a question: does a re-key have to carry this column? Add it to
    `_DRIVER_COLUMNS` as `CARRIED`, with a test beside the others above, or with the reason
    it is left alone. Do not simply add the name to silence the test.
    """
    db_path = await _make_db(tmp_path)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
        tables = [row["name"] for row in await cursor.fetchall()]
        found: set[tuple[str, str]] = set()
        for table in tables:
            cursor = await db.execute(f"PRAGMA table_info('{table}')")
            for row in await cursor.fetchall():
                if _names_a_driver(row["name"]):
                    found.add((table, row["name"]))

    assert sorted(found) == sorted(_DRIVER_COLUMNS)


async def test_the_columns_a_re_key_carries_are_named_as_such(tmp_path):
    """The eight of them, so that dropping one from the service is dropping it from here."""
    carried = sorted(key for key, why in _DRIVER_COLUMNS.items() if why == CARRIED)

    assert carried == [
        ("driver_history_entries", "discord_user_id"),
        ("driver_profiles", "discord_user_id"),
        ("driver_standings_snapshots", "driver_user_id"),
        ("qualifying_session_results", "driver_user_id"),
        ("race_session_results", "driver_user_id"),
        ("session_results", "fl_driver_override"),
        ("signup_records", "discord_user_id"),
        ("signup_wizard_records", "discord_user_id"),
    ]
