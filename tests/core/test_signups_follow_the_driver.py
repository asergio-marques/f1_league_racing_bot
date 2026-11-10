"""A driver's signups are theirs whichever of their accounts made them (issue #243, E13/E14).

A signup keeps the account it was made from; nothing moves it when the driver changes
account. Every reader of "the driver's signup" therefore matches across all their accounts —
placement's seeded list, the review's unsettled list and the graphics' nationality — and takes
the most recent of them, save that within a season an approved signup outranks a later one
that was not (decided 2026-09-18).
"""
from __future__ import annotations

import os

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.image.services.image_verdict_post import _driver_nationality
from leaguebot.signup.services.signup_module_service import SignupModuleService

async def _mark_approved(svc, db_path, discord_user_id):
    """Mark the driver's latest signup approved in a save of the test's own, as Approve's does."""
    async with get_connection(db_path) as db:
        await svc.mark_approved_on(db, discord_user_id)
        await db.commit()


SERVER_ID = 2433

#: Why the seeded list's tests fail until the build: the list is still core's placement service's.
_LIST_IN_SIGNUP = "#283: the seeded list of unassigned drivers is still PlacementService's"
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


@pytest.mark.xfail(strict=True, reason=_LIST_IN_SIGNUP)
async def test_placement_still_finds_a_signup_made_on_a_past_account(tmp_path):
    db_path, _ = await _make_db(tmp_path)

    drivers = await SignupModuleService(db_path).get_unassigned_drivers_seeded()

    assert [(d["discord_user_id"], d["server_display_name"]) for d in drivers] == [
        (B, "Racer")
    ]


async def test_the_nationality_is_found_from_either_account(tmp_path):
    db_path, _ = await _make_db(tmp_path)

    assert await _driver_nationality(db_path, int(A)) == "PT"
    assert await _driver_nationality(db_path, int(B)) == "PT"


@pytest.mark.xfail(strict=True, reason=_LIST_IN_SIGNUP)
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

    drivers = await SignupModuleService(db_path).get_unassigned_drivers_seeded()

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


@pytest.mark.xfail(strict=True, reason=_LIST_IN_SIGNUP)
async def test_an_approved_signup_outranks_a_later_one_that_was_not(tmp_path):
    """The usual merge: A's signup approved, B's later one rejected. A's is the driver's."""
    db_path, _ = await _make_db(tmp_path)
    await _mark_approved(SignupModuleService(db_path), db_path, A)
    await _newer_rejected_signup_on_b(db_path)

    drivers = await SignupModuleService(db_path).get_unassigned_drivers_seeded()

    assert drivers[0]["server_display_name"] == "Racer"
    assert await _driver_nationality(db_path, int(B)) == "PT"


async def _in_two_seasons(db_path: str) -> None:
    """Put A's signup in season 1, since completed, and B's in season 2, in setup, renamed Racer
    Now and still pending."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, start_date, status) "
            "VALUES (1, '2025-01-01', 'COMPLETED'), (2, '2026-01-01', 'SETUP')"
        )
        await db.execute("UPDATE signup_records SET season_id = 1 WHERE discord_user_id = ?", (A,))
        await db.execute(
            "UPDATE signup_records SET season_id = 2, server_display_name = 'Racer Now' "
            "WHERE discord_user_id = ?",
            (B,),
        )
        await db.commit()


@pytest.mark.parametrize(
    "approved, two_seasons, name",
    [
        pytest.param(True, False, "Racer", id="the-approved-one"),
        pytest.param(False, False, "Rejected Answers", id="the-latest-where-none-is-approved"),
        pytest.param(True, True, "Racer Now", id="the-later-season-s-over-an-earlier-approved-one"),
    ],
)
@pytest.mark.xfail(
    strict=True, reason="#283: signup has no reader of a driver's name across their accounts"
)
async def test_signup_names_a_driver_from_their_signup_across_accounts(
    tmp_path, approved, two_seasons, name
):
    """Signup's reader of the name each driver gave in their signup, the one core's hook a
    driver's name is read by asks. The driver signed up on their past account A as Racer, and
    later on their current account B as Rejected Answers. With A's signup approved, the
    approved one outranks the later: signup names them Racer. With neither approved, the
    latest speaks for them: Rejected Answers. With A's signup approved in season 1, since
    completed, and B's, as Racer Now, pending in season 2, the later season's signup speaks for
    them, approved or not: Racer Now."""
    from leaguebot.signup.services.signup_module_service import driver_names

    db_path, profile_id = await _make_db(tmp_path)
    if approved:
        await _mark_approved(SignupModuleService(db_path), db_path, A)
    await _newer_rejected_signup_on_b(db_path)
    if two_seasons:
        await _in_two_seasons(db_path)

    assert await driver_names(SignupModuleService(db_path), [profile_id]) == {profile_id: name}


async def _signup_on(db_path: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute("UPDATE server_configs SET signup_module_enabled = 1")
        await db.commit()


async def _approvals(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT SUM(approved) FROM signup_records")
        return (await cursor.fetchone())[0]


async def test_a_turned_down_signup_no_longer_outranks_the_others(tmp_path):
    """The signup specification: a signup ceases to be approved when the approved driver is turned
    down. The signup module is on; the driver's signup on their past account A is approved, and a
    later one on their current account B was rejected. Signup's part of core's
    driver-turned-down hook, `driver_turned_down_on`, is called on a connection, as core's save
    would call it, and the connection commits. A's signup no longer outranks B's: the
    driver's nationality is read from B's (BR), and no signup of theirs is approved."""
    from leaguebot.signup.services.signup_module_service import driver_turned_down_on

    db_path, profile_id = await _make_db(tmp_path)
    await _signup_on(db_path)
    await _mark_approved(SignupModuleService(db_path), db_path, A)
    await _newer_rejected_signup_on_b(db_path)

    async with get_connection(db_path) as db:
        await driver_turned_down_on(db, profile_id)
        await db.commit()

    assert await _driver_nationality(db_path, int(A)) == "BR"
    assert await _approvals(db_path) == 0


async def _rolled_back(db_path: str, profile_id: int) -> None:
    """The save the hook is called in fails after it: signup's part, then a rollback."""
    from leaguebot.signup.services.signup_module_service import driver_turned_down_on

    await _signup_on(db_path)
    async with get_connection(db_path) as db:
        await driver_turned_down_on(db, profile_id)
        await db.rollback()


async def _while_signup_is_off(db_path: str, profile_id: int) -> None:
    """The signup module off: signup's part, in a save that commits."""
    from leaguebot.signup.services.signup_module_service import driver_turned_down_on

    async with get_connection(db_path) as db:
        await driver_turned_down_on(db, profile_id)
        await db.commit()


@pytest.mark.parametrize(
    "turn_down",
    [
        pytest.param(_rolled_back, id="the-save-rolled-back"),
        pytest.param(_while_signup_is_off, id="signup-off"),
    ],
)
async def test_a_turned_down_signup_keeps_its_approval_unless_the_save_holds_and_signup_is_on(
    tmp_path, turn_down
):
    """Signup's part of a driver turned down writes on the connection of core's save, committing
    nothing, and does nothing while the signup module is off, the hook being called whatever the
    modules' switches say. The driver's signup on A is approved. Signup's part is called in a save
    that then rolls back, the module on; or in a save that commits, the module off. Either way the
    signup stays approved."""
    db_path, profile_id = await _make_db(tmp_path)
    await _mark_approved(SignupModuleService(db_path), db_path, A)

    await turn_down(db_path, profile_id)

    assert await _approvals(db_path) == 1


async def test_approval_marks_only_the_latest_signup_of_the_account(tmp_path):
    db_path, _ = await _make_db(tmp_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, server_display_name) "
            "VALUES (?, 'Second Go')",
            (A,),
        )
        await db.commit()

    await _mark_approved(SignupModuleService(db_path), db_path, A)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT server_display_name, approved FROM signup_records ORDER BY id"
        )
        assert [tuple(r) for r in await cursor.fetchall()] == [("Racer", 0), ("Second Go", 1)]
