"""A driver's signups are theirs whichever of their accounts made them (issue #243, E13/E14).

A signup keeps the account it was made from; nothing moves it when the driver changes
account. Every reader of "the driver's signup" therefore matches across all their accounts —
placement's seeded list, the review's unsettled list and the graphics' nationality — and takes
the most recent of them, save that within a season an approved signup outranks a later one
that was not (decided 2026-09-18).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from services.image_verdict_post import _driver_nationality  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402
from services.signup_module_service import SignupModuleService  # noqa: E402

SERVER_ID = 2433
A, B = "7101", "7102"


async def _make_db(tmp_path) -> tuple[str, int]:
    """A driver who signed up on A and has since moved to B."""
    db_path = os.path.join(str(tmp_path), "signups.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        cursor = await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, 'UNASSIGNED')",
            (A,),
        )
        profile_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, server_display_name, "
            "nationality, total_lap_ms) VALUES (?, 'Racer', 'PT', 90000)",
            (A,),
        )
        await db.execute(
            "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?", (B, profile_id)
        )
        await db.commit()
    return db_path, profile_id


async def test_placement_still_finds_a_signup_made_on_a_past_account(tmp_path):
    db_path, _ = await _make_db(tmp_path)

    drivers = await PlacementService(db_path).get_unassigned_drivers_seeded()

    assert [(d["discord_user_id"], d["server_display_name"]) for d in drivers] == [
        (B, "Racer")
    ]


async def test_the_nationality_is_found_from_either_account(tmp_path):
    db_path, _ = await _make_db(tmp_path)

    assert await _driver_nationality(db_path, int(A)) == "PT"
    assert await _driver_nationality(db_path, int(B)) == "PT"


async def test_the_most_recent_signup_across_accounts_is_the_drivers(tmp_path):
    """E14: after the usual merge, the newer signup on B speaks for the driver."""
    db_path, _ = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, server_display_name, "
            "nationality, total_lap_ms) VALUES (?, 'Racer Now', 'BR', 91000)",
            (B,),
        )
        await db.commit()

    drivers = await PlacementService(db_path).get_unassigned_drivers_seeded()

    assert drivers[0]["server_display_name"] == "Racer Now"
    assert await _driver_nationality(db_path, int(A)) == "BR"


async def _newer_rejected_signup_on_b(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, server_display_name, "
            "nationality, total_lap_ms) VALUES (?, 'Rejected Answers', 'BR', 91000)",
            (B,),
        )
        await db.commit()


async def test_an_approved_signup_outranks_a_later_one_that_was_not(tmp_path):
    """The usual merge: A's signup approved, B's later one rejected. A's is the driver's."""
    db_path, _ = await _make_db(tmp_path)
    await SignupModuleService(db_path).mark_approved(A)
    await _newer_rejected_signup_on_b(db_path)

    drivers = await PlacementService(db_path).get_unassigned_drivers_seeded()

    assert drivers[0]["server_display_name"] == "Racer"
    assert await _driver_nationality(db_path, int(B)) == "PT"


async def test_a_turned_down_signup_no_longer_outranks_the_others(tmp_path):
    db_path, profile_id = await _make_db(tmp_path)
    service = SignupModuleService(db_path)
    await service.mark_approved(A)
    await _newer_rejected_signup_on_b(db_path)

    await service.withdraw_approval(profile_id)

    assert await _driver_nationality(db_path, int(A)) == "BR"
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT SUM(approved) FROM signup_records")
        assert (await cursor.fetchone())[0] == 0


async def test_approval_marks_only_the_latest_signup_of_the_account(tmp_path):
    db_path, _ = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, server_display_name) "
            "VALUES (?, 'Second Go')",
            (A,),
        )
        await db.commit()

    await SignupModuleService(db_path).mark_approved(A)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT server_display_name, approved FROM signup_records ORDER BY id"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [("Racer", 0), ("Second Go", 1)]
