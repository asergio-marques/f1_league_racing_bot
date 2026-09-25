"""A driver owns every Discord account they have raced under (issue #243).

`driver_accounts` lists a profile's accounts, the current one included, and is written by
triggers on `driver_profiles` (migration 059) so that no path creating a profile can miss it.
These tests pin the triggers — including that they refuse an account another driver already
lists — and each helper in `leaguebot.core.services.driver_service` the rest of the bot resolves accounts
through.

Everything runs against a real migrated database, since the triggers and the cascade are the
subject.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.models.driver_profile import DriverState  # noqa: E402
from leaguebot.core.services.driver_service import (  # noqa: E402
    DriverService,
    accounts_of,
    accounts_of_profile,
    current_account_map,
    current_account_map_for_division,
    current_account_of,
    resolve_driver_profile_id,
)
from leaguebot.core.services.season_lifecycle_service import delete_driver_profiles  # noqa: E402

SERVER_ID = 2430
A, B, C = "1111", "2222", "3333"


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "accounts.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


async def _profile(db_path: str, account: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'UNASSIGNED')",
            (account,),
        )
        await db.commit()
        return cursor.lastrowid


async def _make_current(db_path: str, profile_id: int, account: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?", (account, profile_id)
        )
        await db.commit()


async def _listed(db_path: str) -> list[tuple]:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT driver_profile_id, discord_user_id FROM driver_accounts "
            "ORDER BY driver_profile_id, discord_user_id"
        )
        return [tuple(r) for r in await cursor.fetchall()]


# ---------------------------------------------------------------------------
# The triggers
# ---------------------------------------------------------------------------


async def test_creating_a_profile_lists_its_account(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    assert await _listed(db_path) == [(pid, A)]


async def test_a_profile_created_by_the_service_lists_its_account(tmp_path):
    """The service's own creation path, which a raw INSERT above does not exercise."""
    db_path = await _make_db(tmp_path)
    profile = await DriverService(db_path).transition(
        A, DriverState.PENDING_SIGNUP_COMPLETION
    )
    assert await _listed(db_path) == [(profile.id, A)]


async def test_changing_the_current_account_keeps_the_old_one_listed(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    assert await _listed(db_path) == [(pid, A), (pid, B)]


async def test_switching_back_to_a_past_account_adds_nothing(tmp_path):
    """E2: A → B → A leaves the list at {A, B}, with A current again."""
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    await _make_current(db_path, pid, A)
    assert await _listed(db_path) == [(pid, A), (pid, B)]
    async with get_connection(db_path) as db:
        assert await current_account_of(db, B) == A


async def test_a_profile_cannot_be_created_on_another_drivers_past_account(tmp_path):
    """E1: the account already has an owner, and the database refuses a second."""
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    with pytest.raises(sqlite3.IntegrityError):
        await _profile(db_path, A)
    assert await _listed(db_path) == [(pid, A), (pid, B)]


async def test_a_profile_cannot_take_another_drivers_past_account_as_current(tmp_path):
    db_path = await _make_db(tmp_path)
    first = await _profile(db_path, A)
    await _make_current(db_path, first, B)
    second = await _profile(db_path, C)
    with pytest.raises(sqlite3.IntegrityError):
        await _make_current(db_path, second, A)


async def test_deleting_a_profile_frees_every_account_it_held(tmp_path):
    """E3: the driver pass deletes a profile, and its accounts go with it."""
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    async with get_connection(db_path) as db:
        await delete_driver_profiles(db, [pid], keep_history=False)
        await db.commit()
    assert await _listed(db_path) == []
    await _profile(db_path, A)  # free again


async def test_deleting_profiles_returns_every_account_they_held(tmp_path):
    """Read before the cascade takes them: a portrait is keyed by account, and the driver
    pass discards the portraits of every account a deleted driver held (issue #235)."""
    db_path = await _make_db(tmp_path)
    first = await _profile(db_path, A)
    await _make_current(db_path, first, B)
    second = await _profile(db_path, C)
    kept = await _profile(db_path, "4444")
    async with get_connection(db_path) as db:
        deleted = await delete_driver_profiles(db, [first, second], keep_history=False)
        await db.commit()
    assert deleted == [A, B, C]
    assert await _listed(db_path) == [(kept, "4444")]


async def test_deleting_no_profile_returns_no_account(tmp_path):
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        assert await delete_driver_profiles(db, [], keep_history=False) == []


# ---------------------------------------------------------------------------
# The helpers
# ---------------------------------------------------------------------------


async def test_resolve_driver_profile_id_finds_the_driver_by_any_account(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    async with get_connection(db_path) as db:
        assert await resolve_driver_profile_id(int(A), db) == pid
        assert await resolve_driver_profile_id(int(B), db) == pid
        assert await resolve_driver_profile_id(int(C), db) is None


async def test_accounts_of_lists_the_whole_driver_from_any_account(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    async with get_connection(db_path) as db:
        assert await accounts_of(db, A) == [A, B]
        assert await accounts_of(db, int(B)) == [A, B]
        assert await accounts_of_profile(db, pid) == [A, B]


async def test_accounts_of_an_account_nobody_holds_is_that_account(tmp_path):
    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        assert await accounts_of(db, C) == [C]
        assert await current_account_of(db, C) == C


async def test_current_account_map_carries_only_past_accounts(tmp_path):
    db_path = await _make_db(tmp_path)
    moved = await _profile(db_path, A)
    await _make_current(db_path, moved, B)
    await _profile(db_path, C)
    async with get_connection(db_path) as db:
        assert await current_account_map(db) == {int(A): int(B)}


async def test_current_account_map_for_division_reaches_its_server(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (1, '2026-09-17', 'ACTIVE', 1, 'ONGOING')"
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
            "VALUES (7, 1, 'Pro', 1001, 1, 'ACTIVE')"
        )
        await db.commit()
        assert await current_account_map_for_division(db, 7) == {int(A): int(B)}
        assert await current_account_map_for_division(db, 999) == {}


async def test_get_profile_answers_only_to_the_current_account(tmp_path):
    """A past account is mapped to the current one by the caller first (`current_account_of`).

    Were `get_profile` to answer to a past account, a path such as the member-leave handler
    would treat a driver's old account leaving the server as the driver leaving (E4).
    """
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)
    await _make_current(db_path, pid, B)
    svc = DriverService(db_path)
    assert (await svc.get_profile(B)).id == pid
    assert await svc.get_profile(A) is None
