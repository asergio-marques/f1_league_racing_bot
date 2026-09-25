"""Two profiles become one driver when a reassign names another's current account (issue #243).

A person who signed up on a new account before the league noticed holds two profiles. Making
that account the driver's current one merges them: the profile holding the live season's seat
or signup is kept (E28), the other's accounts (E34), links and former-driver flag (E29) join it,
and it is deleted. Refused, changing nothing, when both hold the live season, or when both took
part in one division — one season's division, not a name shared across seasons (E25, E26,
E27). An account no driver holds follows the same rule for its leftover results (E31, E32).
Another league's profile of the same person is never touched (E30), and a failure part-way
leaves the league as it was (E33).
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.services import driver_service  # noqa: E402
from leaguebot.core.services.driver_service import DriverService  # noqa: E402
from tests.support.teams import seed_team_instances  # noqa: E402

SERVER_ID = 2440
A, B, C = "6501", "6502", "6503"

#: Seasons 1 and 3 each hold a division called Pro; season 1 also holds Am.
S1_PRO, S1_AM, S3_PRO = 11, 12, 31


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "merge.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        for season, number, status in ((1, 1, "COMPLETED"), (3, 3, "ACTIVE")):
            await db.execute(
                "INSERT INTO seasons (id, start_date, status, season_number) "
                "VALUES (?, '2026-01-01', ?, ?)",
                (season, status, number),
            )
        for division, season, name in ((S1_PRO, 1, "Pro"), (S1_AM, 1, "Am"), (S3_PRO, 3, "Pro")):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier) "
                "VALUES (?, ?, ?, 1001, 1)",
                (division, season, name),
            )
            await seed_team_instances(db, division, 501)
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, scheduled_at) "
                "VALUES (?, ?, 1, 'NORMAL', '2026-01-20T18:00:00')",
                (division, division),
            )
            await db.execute(
                "INSERT INTO session_results (id, round_id, division_id, session_type, status) "
                "VALUES (?, ?, ?, 'FEATURE_RACE', 'ACTIVE')",
                (division, division, division),
            )
        await db.commit()
    return db_path


async def _profile(db_path: str, account: str, *, state: str = "NOT_SIGNED_UP",
                   former: bool = False) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "former_driver) VALUES (?, ?, ?)",
            (account, state, int(former)),
        )
        await db.commit()
        return cursor.lastrowid


async def _seated(db_path: str, profile_id: int, division: int) -> None:
    """A confirmed placement's lasting mark: a membership of the division."""
    season = 1 if division in (S1_PRO, S1_AM) else 3
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_division_memberships (season_id, division_id, driver_profile_id) "
            "VALUES (?, ?, ?)",
            (season, division, profile_id),
        )
        await db.commit()


async def _raced(db_path: str, account: str, division: int, profile_id: int | None = None) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, driver_profile_id) VALUES (?, ?, 501, 1, ?)",
            (division, int(account), profile_id),
        )
        await db.commit()


async def _history(db_path: str, profile_id: int, account: str, division_name: str = "Pro") -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_history_entries (discord_user_id, driver_profile_id, "
            "season_number, division_name) VALUES (?, ?, 1, ?)",
            (account, profile_id, division_name),
        )
        await db.commit()


async def _snapshot(db_path: str) -> dict[str, list[tuple]]:
    tables = (
        "driver_profiles", "driver_accounts", "driver_division_memberships",
        "driver_history_entries", "race_session_results",
    )
    out: dict[str, list[tuple]] = {}
    async with get_connection(db_path) as db:
        for table in tables:
            rows = await (await db.execute(f"SELECT * FROM {table}")).fetchall()
            out[table] = sorted((tuple(r) for r in rows), key=repr)
    return out


async def _reassign(db_path: str, old: str, new: str):
    return await DriverService(db_path).reassign_user_id(old, new, 77, "Manager")


async def _profiles(db_path: str) -> list[tuple]:
    async with get_connection(db_path) as db:
        rows = await (await db.execute(
            "SELECT id, discord_user_id, current_state, former_driver FROM driver_profiles "
            " ORDER BY id",
        )).fetchall()
    return [tuple(r) for r in rows]


async def _accounts_of(db_path: str, profile_id: int) -> list[str]:
    async with get_connection(db_path) as db:
        return await driver_service.accounts_of_profile(db, profile_id)


# ── Merges that go through ─────────────────────────────────────────────────


async def test_the_profile_holding_the_live_season_is_kept(tmp_path):
    """E28, E29: A raced season 1 and is a former driver; B has signed up for season 3."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A, former=True)
    await _seated(db_path, a, S1_PRO)
    await _raced(db_path, A, S1_PRO, a)
    await _history(db_path, a, A)
    b = await _profile(db_path, B, state="UNASSIGNED")

    outcome = await _reassign(db_path, A, B)

    assert outcome.profile.id == b
    assert outcome.merged_accounts == [B]
    assert await _profiles(db_path) == [(b, B, "UNASSIGNED", 1)]
    assert await _accounts_of(db_path, b) == [A, B]
    async with get_connection(db_path) as db:
        assert [tuple(r) for r in await (await db.execute(
            "SELECT division_id, driver_profile_id FROM driver_division_memberships"
        )).fetchall()] == [(S1_PRO, b)]
        assert [tuple(r) for r in await (await db.execute(
            "SELECT discord_user_id, driver_profile_id FROM driver_history_entries"
        )).fetchall()] == [(A, b)]
        assert [tuple(r) for r in await (await db.execute(
            "SELECT driver_user_id, driver_profile_id FROM race_session_results"
        )).fetchall()] == [(int(A), b)]


async def test_the_usual_case_keeps_the_seated_driver_on_the_new_account(tmp_path):
    """E35: A is seated in the live season; B signed up and was rejected."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A, state="ASSIGNED")
    await _seated(db_path, a, S3_PRO)
    await _profile(db_path, B, state="NOT_SIGNED_UP")

    outcome = await _reassign(db_path, A, B)

    assert outcome.profile.id == a
    assert outcome.replaced_account == A
    assert await _profiles(db_path) == [(a, B, "ASSIGNED", 0)]
    assert await _accounts_of(db_path, a) == [A, B]


async def test_the_absorbed_profiles_past_accounts_come_with_it(tmp_path):
    """E34: B's profile once held C."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A, state="ASSIGNED")
    await _profile(db_path, C)
    await _reassign(db_path, C, B)

    outcome = await _reassign(db_path, A, B)

    assert outcome.merged_accounts == [B, C]
    assert await _accounts_of(db_path, a) == [A, B, C]


async def test_the_same_division_name_in_different_seasons_is_no_obstacle(tmp_path):
    """E26: Pro in season 1 on A, Pro in season 3 on B — the ordinary case."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A)
    await _seated(db_path, a, S1_PRO)
    b = await _profile(db_path, B, state="ASSIGNED")
    await _seated(db_path, b, S3_PRO)

    outcome = await _reassign(db_path, A, B)

    assert outcome.profile.id == b


async def test_different_divisions_of_one_season_are_allowed(tmp_path):
    """E27: as for a driver moved mid-season, one driver with two entries."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A)
    await _seated(db_path, a, S1_PRO)
    await _history(db_path, a, A, "Pro")
    b = await _profile(db_path, B)
    await _seated(db_path, b, S1_AM)
    await _history(db_path, b, B, "Am")

    outcome = await _reassign(db_path, A, B)

    async with get_connection(db_path) as db:
        rows = await (await db.execute(
            "SELECT division_name, driver_profile_id FROM driver_history_entries "
            "ORDER BY division_name"
        )).fetchall()
    assert [tuple(r) for r in rows] == [("Am", outcome.profile.id), ("Pro", outcome.profile.id)]


async def test_leftover_results_elsewhere_become_the_drivers(tmp_path):
    """E32: B holds no profile, only results in season 1 Am; the driver raced season 1 Pro."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A)
    await _seated(db_path, a, S1_PRO)
    await _raced(db_path, B, S1_AM)

    outcome = await _reassign(db_path, A, B)

    assert outcome.accounts == [A, B]
    async with get_connection(db_path) as db:
        assert await driver_service.resolve_driver_profile_id(int(B), db) == a


# ── Merges refused ─────────────────────────────────────────────────────────


async def _refused(db_path: str, match: str) -> None:
    before = await _snapshot(db_path)
    with pytest.raises(ValueError, match=match):
        await _reassign(db_path, A, B)
    assert await _snapshot(db_path) == before


async def test_both_holding_the_live_season_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, state="ASSIGNED")
    await _profile(db_path, B, state="UNASSIGNED")

    await _refused(db_path, "both hold a seat or a signup")


async def test_both_seated_in_one_seasons_division_is_refused(tmp_path):
    """E25: by seat alone, neither having raced."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A)
    await _seated(db_path, a, S1_PRO)
    b = await _profile(db_path, B)
    await _seated(db_path, b, S1_PRO)

    await _refused(db_path, "both took part in Season 1 Pro")


async def test_one_seated_and_the_other_racing_in_one_division_is_refused(tmp_path):
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A)
    await _seated(db_path, a, S1_PRO)
    await _profile(db_path, B)
    await _raced(db_path, B, S1_PRO)

    await _refused(db_path, "both took part in Season 1 Pro")


async def test_leftover_results_in_a_shared_division_are_refused(tmp_path):
    """E31: B holds no profile, but raced season 1 Pro, as did the driver."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A)
    await _raced(db_path, A, S1_PRO, a)
    await _raced(db_path, B, S1_PRO)

    await _refused(db_path, "both took part in Season 1 Pro")


async def test_a_failure_part_way_through_a_merge_changes_nothing(tmp_path, monkeypatch):
    """E33: one transaction. A table that cannot be written stands in for any fault."""
    db_path = await _make_db(tmp_path)
    a = await _profile(db_path, A, state="ASSIGNED")
    await _seated(db_path, a, S3_PRO)
    b = await _profile(db_path, B)
    await _history(db_path, b, B, "Am")
    monkeypatch.setattr(
        driver_service, "_PROFILE_COLUMNS",
        (*driver_service._PROFILE_COLUMNS[:4], "no_such_table", *driver_service._PROFILE_COLUMNS[4:]),
    )
    before = await _snapshot(db_path)

    with pytest.raises(Exception, match="no_such_table"):
        await _reassign(db_path, A, B)

    assert await _snapshot(db_path) == before
