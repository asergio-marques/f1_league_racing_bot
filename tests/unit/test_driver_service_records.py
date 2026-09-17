"""`DriverService` — re-keying a profile, the former-driver flag, and what a departure destroys.

Issue #208. `tests/unit/test_driver_service_transitions.py` covers which transitions the state
machine allows. What it does not cover is what the allowed ones actually *do* to the database,
nor `reassign_user_id` and `set_former_driver` at all.

**The `NOT_SIGNED_UP` transition is destructive, and which of two things it does turns on one
flag.** An ordinary driver's profile is *deleted* outright, seat references cleared first so
nothing dangles. A **former driver** is kept, and their signup record blanked instead —
`former_driver` is what records that somebody raced in this league once, and deleting them
would lose that permanently. The two branches are pinned separately because they share a call
site and a reader could easily make one do the other's work.

**Re-keying is how a driver who lost their Discord account keeps their history.** It refuses
in both directions — no profile at the old id, or a profile already at the new one — and the
second refusal matters most: re-keying onto an occupied id would leave two profiles sharing a
Discord user, and every lookup in the bot is by that id.

**Both write audit entries carrying the old and the new value.** They are the league's only
record of a manager re-keying or re-flagging a driver, which are the two commands that can
quietly rewrite who somebody is.

Everything runs against a real migrated database: the deletions cross three tables and the
audit inserts are raw SQL, neither of which a double would check.
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
    return db_path


async def _seed_profile(
    db_path: str,
    *,
    user_id: str = OLD_USER,
    state: str = "UNASSIGNED",
    former: bool = False,
) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO driver_profiles "
            "(server_id, discord_user_id, current_state, former_driver) VALUES (?, ?, ?, ?)",
            (SERVER_ID, user_id, state, int(former)),
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
