"""Sacking a driver, and what is kept of them afterwards.

Issue #208 covered `PlacementService.sack_driver` for the first time; issue #220 changed what it
does. It is the most destructive thing a league does to one person's place in a season.

**Only a committed driver is sacked.** A driver whose placement is not yet confirmed is removed
with `/driver unassign`, and one who holds no placement is turned down with `/driver reject`.
The command allows it only while the season is ongoing.

**Nothing of the driver is deleted here.** A driver without the former-driver flag who reaches
Not Signed Up is *pending deletion*, and is deleted by the driver pass that ends the season. So
the profile stays, at Not Signed Up, with their attendance, their results, their standings and
their signups — the attendance sheet goes on listing a driver who held a seat in the division.

**Only an Unassigned or Assigned driver can be sacked.** Someone mid-signup has nothing to
revoke and someone banned is already out; sacking either would either do nothing or quietly
overwrite a ban with a milder state. The refusal names the state.

**Roles are revoked before the database changes.** The roles are the only part a driver can see,
and the seat assignments are what the revocation is computed from.

**A test driver has no roles to revoke**, and **a driver who has left the server is still
sacked**. **The audit records the state and the divisions as they were.**
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from leaguebot.core.db.database import get_connection, run_migrations  # noqa: E402
from leaguebot.core.models.driver_profile import DriverState  # noqa: E402
from leaguebot.core.services.placement_service import PlacementService  # noqa: E402
from tests.support.teams import seed_team_instances  # noqa: E402

SERVER_ID = 10708
SEASON_ID = 1
PRIOR_SEASON_ID = 2
DIVISION_ID = 11
PRIOR_DIVISION_ID = 12
ROUND_ID = 21
PROFILE_ID = 31
DISCORD_USER_ID = "4242"
ACTOR_ID = 77
DRIVER_ROLE = 555


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(
    tmp_path,
    *,
    name: str = "sack_driver",
    state: DriverState = DriverState.ASSIGNED,
    former_driver: bool = True,
    is_test_driver: bool = False,
    driver_role: int | None = DRIVER_ROLE,
) -> str:
    db_path = os.path.join(str(tmp_path), f"{name}.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 999)",
            (DIVISION_ID, SEASON_ID),
        )
        await seed_team_instances(db, DIVISION_ID, 3001)
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 1, '2026-02-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state, "
            "former_driver, is_test_driver) VALUES (?, ?, ?, ?, ?)",
            (
                PROFILE_ID,
                DISCORD_USER_ID,
                state.value,
                int(former_driver),
                int(is_test_driver),
            ),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id) VALUES (?, ?, ?)",
            (PROFILE_ID, SEASON_ID, DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, is_reserve) "
            "VALUES (?, 'Red', 'Red', 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, 1, ?)",
            (cursor.lastrowid, PROFILE_ID),
        )
        await db.execute(
            "INSERT INTO signup_records (discord_user_id, "
            "discord_username, nationality) VALUES (?, 'racer', 'GB')",
            (DISCORD_USER_ID,),
        )
        await db.execute(
            "INSERT INTO driver_round_attendance (driver_profile_id, round_id, "
            "division_id, rsvp_status) VALUES (?, ?, ?, 'ACCEPTED')",
            (PROFILE_ID, ROUND_ID, DIVISION_ID),
        )
        cursor = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status) "
            "VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE')",
            (ROUND_ID, DIVISION_ID),
        )
        session_result_id = cursor.lastrowid
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, driver_profile_id) "
            "VALUES (?, 4242, 3001, 1, ?)",
            (session_result_id, PROFILE_ID),
        )
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, driver_profile_id) "
            "VALUES (?, 4242, 3001, 1, ?)",
            (session_result_id, PROFILE_ID),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, "
            "driver_user_id, standing_position, driver_profile_id) "
            "VALUES (?, ?, 4242, 1, ?)",
            (ROUND_ID, DIVISION_ID, PROFILE_ID),
        )
        if driver_role is not None:
            await db.execute(
                "UPDATE server_configs SET driver_role_id = ?",
                (driver_role,),
            )
        await db.commit()
    return db_path


def _guild(*, member_missing: bool = False, fetch_fails: bool = False, has_role: bool = True):
    guild = MagicMock()
    role = MagicMock()
    role.id = DRIVER_ROLE
    member = MagicMock()
    member.id = int(DISCORD_USER_ID)
    member.roles = [role] if has_role else []
    member.remove_roles = AsyncMock()

    guild.get_member = MagicMock(return_value=None if member_missing else member)
    if fetch_fails:
        guild.fetch_member = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "gone")
        )
    else:
        guild.fetch_member = AsyncMock(return_value=member)
    guild.get_role = MagicMock(return_value=role)
    guild._member = member
    guild._role = role
    return guild


def _service(db_path: str) -> PlacementService:
    service = PlacementService(db_path)
    service.revoke_all_placement_roles = AsyncMock(return_value=None)
    service._revoke_roles = AsyncMock(return_value=None)
    service._refresh_lineup_post = AsyncMock(return_value=None)
    return service


async def _sack(service, guild, *, season_id: int | None = SEASON_ID):
    await service.sack_driver(
        PROFILE_ID,
        season_id,
        ACTOR_ID,
        "Manager#0001",
        guild,
        DISCORD_USER_ID,
    )


async def _count(db_path, sql, params=()) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(sql, params)
        return (await cursor.fetchone())[0]


async def _profile_state(db_path):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id = ?", (PROFILE_ID,)
        )
        row = await cursor.fetchone()
        return row["current_state"] if row else None


# ---------------------------------------------------------------------------
# What may be sacked
# ---------------------------------------------------------------------------


async def test_a_committed_driver_may_be_sacked(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_assigned")

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == DriverState.NOT_SIGNED_UP.value


async def test_a_driver_with_no_confirmed_placement_is_refused(tmp_path):
    """Issue #220: an uncommitted placement is unassigned, an Unassigned driver rejected."""
    db_path = await _make_db(tmp_path, name="sack_uncommitted")
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_season_assignments SET committed = 0")
        await db.commit()

    with pytest.raises(ValueError, match="placement is confirmed"):
        await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == DriverState.ASSIGNED.value
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 1


@pytest.mark.parametrize(
    "state",
    [
        DriverState.NOT_SIGNED_UP,
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.PENDING_DRIVER_CORRECTION,
    ],
)
async def test_any_other_state_is_refused(tmp_path, state):
    """Someone mid-signup has nothing to revoke, and someone at Not Signed Up is already
    out — sacking either would do nothing but move a driver the season no longer holds."""
    db_path = await _make_db(tmp_path, name=f"refuse_{state.value}", state=state)

    with pytest.raises(ValueError, match="Unassigned or Assigned"):
        await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == state.value


async def test_the_refusal_names_the_state_it_found(tmp_path):
    """A manager who meets it needs to know which state they hit, not merely that they hit
    one — a driver still in review and one already gone call for different next steps."""
    db_path = await _make_db(
        tmp_path, name="refuse_names", state=DriverState.PENDING_ADMIN_APPROVAL
    )

    with pytest.raises(ValueError, match="PENDING_ADMIN_APPROVAL"):
        await _sack(_service(db_path), _guild())


# ---------------------------------------------------------------------------
# Nobody is deleted: a driver who never raced is pending deletion
# ---------------------------------------------------------------------------


async def test_a_driver_who_never_raced_is_kept_at_not_signed_up(tmp_path):
    """Issue #220: they are pending deletion, and the season's end deletes them."""
    db_path = await _make_db(tmp_path, name="sack_never_raced", former_driver=False)

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == DriverState.NOT_SIGNED_UP.value


async def test_a_driver_who_never_raced_keeps_their_attendance_and_results(tmp_path):
    """The attendance sheet goes on listing a driver who held a seat in the division."""
    db_path = await _make_db(tmp_path, name="sack_never_raced_rows", former_driver=False)

    await _sack(_service(db_path), _guild())

    assert await _count(db_path, "SELECT COUNT(*) FROM driver_round_attendance") == 1
    assert await _count(
        db_path, "SELECT COUNT(*) FROM race_session_results WHERE driver_profile_id IS NOT NULL"
    ) == 1


# ---------------------------------------------------------------------------
# A former driver: the profile stays
# ---------------------------------------------------------------------------


async def test_a_former_drivers_profile_is_kept(tmp_path):
    """Every historical result points at it — deleting it would either fail on a foreign
    key or rewrite a season's results to name nobody."""
    db_path = await _make_db(tmp_path, name="keep_former", former_driver=True)

    await _sack(_service(db_path), _guild())

    assert await _count(db_path, "SELECT COUNT(*) FROM driver_profiles") == 1


async def test_a_former_driver_is_returned_to_not_signed_up(tmp_path):
    """Which is what lets them sign up again next season without a manager unpicking
    anything."""
    db_path = await _make_db(tmp_path, name="former_state", former_driver=True)

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == DriverState.NOT_SIGNED_UP.value


async def test_a_former_drivers_signup_details_are_kept(tmp_path):
    """A former driver's signups are season history and are kept whole (issue #220)."""
    db_path = await _make_db(tmp_path, name="former_blanked", former_driver=True)

    await _sack(_service(db_path), _guild())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT discord_username, nationality FROM signup_records "
            "WHERE discord_user_id = ?",
            (DISCORD_USER_ID,),
        )
        row = await cursor.fetchone()
    assert row["discord_username"] is not None
    assert row["nationality"] is not None


async def test_a_former_drivers_attendance_history_is_kept(tmp_path):
    """It is part of the record their profile exists to hold — and the flag is the only
    thing that distinguishes this from the deletion path."""
    db_path = await _make_db(tmp_path, name="former_attendance", former_driver=True)

    await _sack(_service(db_path), _guild())

    assert await _count(db_path, "SELECT COUNT(*) FROM driver_round_attendance") == 1


# ---------------------------------------------------------------------------
# Seats, assignments and roles
# ---------------------------------------------------------------------------


async def test_every_seat_is_freed(tmp_path):
    """A seat still holding a sacked driver is a seat the signup wizard cannot offer, and
    it is how a division silently runs a driver short."""
    db_path = await _make_db(tmp_path, name="sack_seats")

    await _sack(_service(db_path), _guild())

    assert (
        await _count(
            db_path, "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        == 0
    )


async def test_a_seat_in_a_completed_season_is_left_as_the_archive_holds_it(tmp_path):
    """Issue #220: a sack reaches the season being raced, and never the archive."""
    db_path = await _make_db(tmp_path, name="sack_archive_seat")
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 0, '2025-01-01', 'COMPLETED')",
            (PRIOR_SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 998)",
            (PRIOR_DIVISION_ID, PRIOR_SEASON_ID),
        )
        cursor = await db.execute(
            "INSERT INTO team_instances (division_id, name, full_name, is_reserve) VALUES (?, 'Red', 'Red', 0)",
            (PRIOR_DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, 1, ?)",
            (cursor.lastrowid, PROFILE_ID),
        )
        await db.commit()

    await _sack(_service(db_path), _guild())

    assert await _count(
        db_path,
        "SELECT COUNT(*) FROM team_seats ts JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE ti.division_id = ? AND ts.driver_profile_id = ?",
        (PRIOR_DIVISION_ID, PROFILE_ID),
    ) == 1
    assert await _count(
        db_path,
        "SELECT COUNT(*) FROM team_seats ts JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE ti.division_id = ? AND ts.driver_profile_id IS NOT NULL",
        (DIVISION_ID,),
    ) == 0


async def test_the_season_assignments_are_removed(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_assignments")

    await _sack(_service(db_path), _guild())

    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 0


async def test_the_placement_roles_are_revoked(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_roles")
    service = _service(db_path)

    await _sack(service, _guild())

    service.revoke_all_placement_roles.assert_awaited_once()


async def test_the_roles_go_before_the_assignments_do(tmp_path):
    """The assignments are what the revocation is computed from; revoking afterwards would
    revoke nothing, and the driver would keep every role they had."""
    db_path = await _make_db(tmp_path, name="sack_role_order")
    service = _service(db_path)
    seen: dict[str, int] = {}

    async def _revoke(*_args, **_kwargs):
        seen["assignments"] = await _count(
            db_path, "SELECT COUNT(*) FROM driver_season_assignments"
        )

    service.revoke_all_placement_roles = AsyncMock(side_effect=_revoke)

    await _sack(service, _guild())

    assert seen["assignments"] == 1  # still there when the roles were revoked


async def test_the_driver_role_is_revoked(tmp_path):
    """Granted at approval and not covered by the placement roles, so it would otherwise
    mark a sacked driver as signed up indefinitely."""
    db_path = await _make_db(tmp_path, name="sack_signedup")
    service = _service(db_path)

    await _sack(service, _guild())

    service._revoke_roles.assert_awaited_once()
    assert service._revoke_roles.await_args.args[1] == DRIVER_ROLE


async def test_a_driver_without_the_driver_role_is_not_asked_to_lose_it(tmp_path):
    """Discord refuses a removal of a role the member does not hold, and it would be an
    API call on every sack of a driver who never had one."""
    db_path = await _make_db(tmp_path, name="sack_norole")
    service = _service(db_path)

    await _sack(service, _guild(has_role=False))

    service._revoke_roles.assert_not_awaited()


async def test_a_league_with_no_driver_role_configured_revokes_nothing_extra(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_noconfig", driver_role=None)
    service = _service(db_path)

    await _sack(service, _guild())

    service._revoke_roles.assert_not_awaited()


async def test_a_test_driver_has_no_roles_to_revoke(tmp_path):
    """They are a rehearsal's fiction with no Discord member behind them, and asking
    Discord about them would fail on every sack of a test-mode roster."""
    db_path = await _make_db(tmp_path, name="sack_test_driver", is_test_driver=True)
    service = _service(db_path)

    await _sack(service, _guild())

    service.revoke_all_placement_roles.assert_not_awaited()
    service._revoke_roles.assert_not_awaited()


async def test_a_test_driver_is_still_unseated(tmp_path):
    """Which is the whole point of sacking one — the rehearsal's roster has to be able to
    shrink, and the seat has to come back."""
    db_path = await _make_db(tmp_path, name="sack_test_db", is_test_driver=True)

    await _sack(_service(db_path), _guild())

    assert (
        await _count(
            db_path, "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        == 0
    )


async def test_a_driver_who_left_the_server_is_still_sacked(tmp_path):
    """Refusing would leave a departed driver holding a seat nobody could free."""
    db_path = await _make_db(tmp_path, name="sack_departed")
    service = _service(db_path)

    await _sack(service, _guild(member_missing=True, fetch_fails=True))

    assert await _profile_state(db_path) == DriverState.NOT_SIGNED_UP.value
    assert (
        await _count(
            db_path, "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        == 0
    )
    service.revoke_all_placement_roles.assert_not_awaited()


async def test_a_member_not_in_cache_is_fetched(tmp_path):
    """A member the bot has not seen this session is ordinary, and treating a cache miss as
    a departure would silently leave their roles on."""
    db_path = await _make_db(tmp_path, name="sack_fetched")
    service = _service(db_path)
    guild = _guild(member_missing=True)

    await _sack(service, guild)

    guild.fetch_member.assert_awaited_once()
    service.revoke_all_placement_roles.assert_awaited_once()


# ---------------------------------------------------------------------------
# The audit, and the lineup
# ---------------------------------------------------------------------------


async def test_the_sack_is_audited(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_audit")

    await _sack(_service(db_path), _guild())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, actor_id, old_value, new_value FROM audit_entries "
            "",
        )
        rows = [dict(r) for r in await cursor.fetchall()]
    assert [r["change_type"] for r in rows] == ["DRIVER_SACK"]
    assert rows[0]["actor_id"] == ACTOR_ID


async def test_the_audit_records_the_state_and_divisions_as_they_were(tmp_path):
    """Afterwards there is nothing left to read them from, which is why they are captured
    before the deletion rather than after."""
    db_path = await _make_db(tmp_path, name="sack_audit_before")

    await _sack(_service(db_path), _guild())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT old_value, new_value FROM audit_entries",
        )
        row = await cursor.fetchone()
    old = json.loads(row["old_value"])
    assert old == {"state": "ASSIGNED", "divisions": [DIVISION_ID]}


async def test_the_audit_records_whether_the_profile_was_kept(tmp_path):
    """The two paths leave the database in very different shapes, and the flag is the only
    thing that says which one ran."""
    db_path = await _make_db(tmp_path, name="sack_audit_former", former_driver=True)

    await _sack(_service(db_path), _guild())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT new_value FROM audit_entries"
        )
        row = await cursor.fetchone()
    assert json.loads(row["new_value"])["former_driver"] is True


async def test_the_lineup_post_is_refreshed_for_the_division_they_left(tmp_path):
    """The posted lineup is what a division reads to know who is racing, and a sacked
    driver left standing in it is the visible half of the sack not happening."""
    db_path = await _make_db(tmp_path, name="sack_lineup")
    service = _service(db_path)
    guild = _guild()

    await _sack(service, guild)

    service._refresh_lineup_post.assert_awaited_once_with(guild, DIVISION_ID)
