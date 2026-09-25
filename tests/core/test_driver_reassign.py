"""`/driver reassign` makes another account a driver's current one (issue #243).

The new account becomes current and the one it replaces joins the driver's past accounts.
Nothing is rewritten — `test_driver_service_records.py` compares the whole database to hold
that. What this file pins is which reassigns are allowed and which are refused, each refusal
changing nothing:

- any account of the driver's names them, a past one as well as the current (E16);
- one of the driver's own past accounts may be made current again (E2);
- the current account is refused as the new one (E15), as is an account no driver holds (E17);
- a past account of another driver is refused (E18) — an account belongs to one driver;
- a test-mode driver on either side is refused (E19);
- a signup in progress on either side is refused (E20).

It also pins that an open placements review expires when a reassign lands (E41). The review
names the driver by the account being replaced, so a reassign is a change to what would be
confirmed, and the season fingerprint catching it is right rather than a fault to fix.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.services.driver_service import DriverService  # noqa: E402

SERVER_ID = 2437
A, B, C, D = "6201", "6202", "6203", "6204"
ACTOR = 77


async def _make_db(tmp_path) -> str:
    db_path = os.path.join(str(tmp_path), "reassign.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.commit()
    return db_path


async def _profile(db_path: str, account: str, *, state: str = "UNASSIGNED",
                   test: bool = False) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state, "
            "is_test_driver) VALUES (?, ?, ?)",
            (account, state, int(test)),
        )
        await db.commit()
        return cursor.lastrowid


async def _state_of_the_league(db_path: str) -> tuple:
    async with get_connection(db_path) as db:
        profiles = await (await db.execute(
            "SELECT id, discord_user_id, current_state FROM driver_profiles ORDER BY id"
        )).fetchall()
        accounts = await (await db.execute(
            "SELECT driver_profile_id, discord_user_id FROM driver_accounts "
            "ORDER BY driver_profile_id, discord_user_id"
        )).fetchall()
        audit = await (await db.execute("SELECT COUNT(*) FROM audit_entries")).fetchone()
    return ([tuple(r) for r in profiles], [tuple(r) for r in accounts], audit[0])


async def _reassign(db_path: str, old: str, new: str):
    return await DriverService(db_path).reassign_user_id(old, new, ACTOR, "Manager")


async def _refused(db_path: str, old: str, new: str, match: str) -> None:
    before = await _state_of_the_league(db_path)
    with pytest.raises(ValueError, match=match):
        await _reassign(db_path, old, new)
    assert await _state_of_the_league(db_path) == before


# ── What goes through ──────────────────────────────────────────────────────


async def test_a_new_account_becomes_current_and_the_old_one_is_kept(tmp_path):
    db_path = await _make_db(tmp_path)
    pid = await _profile(db_path, A)

    outcome = await _reassign(db_path, A, B)

    assert outcome.profile.id == pid
    assert outcome.profile.discord_user_id == B
    assert outcome.replaced_account == A
    assert outcome.accounts == [A, B]
    assert outcome.switched_back is False


async def test_a_driver_is_named_by_a_past_account(tmp_path):
    """E16: the driver moved A → B; naming them by A moves them on to C, replacing B."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)

    outcome = await _reassign(db_path, A, C)

    assert outcome.replaced_account == B
    assert outcome.accounts == [A, B, C]


async def test_a_past_account_may_be_made_current_again(tmp_path):
    """E2: A → B → A. The list stays {A, B}."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)

    outcome = await _reassign(db_path, B, A)

    assert outcome.switched_back is True
    assert outcome.replaced_account == B
    assert outcome.accounts == [A, B]
    assert (await DriverService(db_path).get_profile(A)).id == outcome.profile.id


async def test_a_reassign_is_audited_with_the_account_replaced(tmp_path):
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)
    await _reassign(db_path, A, C)  # named by the past account, replacing B

    async with get_connection(db_path) as db:
        rows = await (await db.execute(
            "SELECT change_type, old_value, new_value FROM audit_entries ORDER BY id"
        )).fetchall()
    assert [tuple(r) for r in rows] == [
        ("DRIVER_USER_ID_REASSIGN", A, B),
        ("DRIVER_USER_ID_REASSIGN", B, C),
    ]


# ── What is refused ────────────────────────────────────────────────────────


async def test_an_account_no_driver_holds_is_refused(tmp_path):
    """E17."""
    db_path = await _make_db(tmp_path)
    await _refused(db_path, A, B, "No driver profile")


async def test_the_current_account_is_refused_as_the_new_one(tmp_path):
    """E15."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _reassign(db_path, A, B)

    await _refused(db_path, A, B, "already this driver's current account")


async def test_another_drivers_past_account_is_refused(tmp_path):
    """E18: C's driver once held D; D belongs to them."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A)
    await _profile(db_path, D)
    await _reassign(db_path, D, C)

    await _refused(db_path, A, D, "past account of another driver")


@pytest.mark.parametrize("test_side", ["driver", "new account"])
async def test_a_test_mode_driver_on_either_side_is_refused(tmp_path, test_side):
    """E19."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, test=test_side == "driver")
    await _profile(db_path, B, test=test_side == "new account")

    await _refused(db_path, A, B, "test-mode driver")


@pytest.mark.parametrize("state", [
    "PENDING_SIGNUP_COMPLETION",
    "PENDING_ADMIN_APPROVAL",
    "AWAITING_CORRECTION_PARAMETER",
    "PENDING_DRIVER_CORRECTION",
])
async def test_a_signup_in_progress_on_the_driver_is_refused(tmp_path, state):
    """E20."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, state=state)

    await _refused(db_path, A, B, "This driver has a signup in progress")


async def test_a_signup_in_progress_on_the_new_account_is_refused(tmp_path):
    """E20, and E35's first half: the usual case waits for the new account's signup."""
    db_path = await _make_db(tmp_path)
    await _profile(db_path, A, state="ASSIGNED")
    await _profile(db_path, B, state="PENDING_ADMIN_APPROVAL")

    await _refused(db_path, A, B, f"<@{B}> has a signup in progress")


# ── An open review ─────────────────────────────────────────────────────────


async def test_a_reassign_expires_an_open_placements_review(tmp_path):
    """E41: the review names the driver by the account the reassign replaces."""
    from leaguebot.core.services.season_fingerprint_service import take_fingerprint

    db_path = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number) "
            "VALUES (1, '2026-09-17', 'SETUP', 1)"
        )
        await db.commit()
    await _profile(db_path, A)
    bot = SimpleNamespace(
        db_path=db_path,
        image_config_service=SimpleNamespace(get_config=AsyncMock(return_value=None)),
    )
    before = await take_fingerprint(bot, 1)

    await _reassign(db_path, A, B)

    assert "the unsettled signups" in (await take_fingerprint(bot, 1)).differs_from(
        before
    )
