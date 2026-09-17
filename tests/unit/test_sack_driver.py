"""Sacking a driver, and what is kept of them afterwards.

Issue #208. `PlacementService.sack_driver` is ninety statements and was uncovered. It is the
most destructive thing a league manager can do to one person's record, and what it destroys
depends on a single flag.

**A former driver keeps their profile.** `former_driver` marks
someone who has raced in this league before, and their profile row is what every historical
result points at — deleting it would either fail on a foreign key or rewrite a season's results
to name nobody. So a former driver is transitioned to Not Signed Up with their signup record
blanked. That path works and is covered here in full.

**Anyone else loses their profile.** A driver who has never raced has no results the league
needs attributed to them, so the profile is deleted, and everything pointing at it has to go
or let go first or the foreign keys refuse the deletion. Their attendance history goes with
them, and so do their assignments and history entries from *every* season — a driver who sat
out a completed season and stayed on the roster carries both. The result and standings rows they appear in stay, naming nobody by profile, because
those belong to the round rather than to the driver. Issue #211: this path once cleared a
column `signup_records` has never had, so every such sack raised and rolled back, leaving the
driver stripped of their roles but still seated. Nothing had ever run it.

**Only an Unassigned or Assigned driver can be sacked.** Someone mid-signup has nothing to
revoke and someone banned is already out; sacking either would either do nothing or quietly
overwrite a ban with a milder state. The refusal names the state, because a manager who meets it
needs to know which one they hit.

**Roles are revoked before the database changes.** The roles are the only part a driver can see,
and the seat assignments are what the revocation is computed from — doing it afterwards would
revoke nothing, because the assignments are gone by then. It is also why a failure in the
database step is not harmless: the roles are already gone when it happens.

**A test driver has no roles to revoke.** They are a rehearsal's fiction with no Discord member
behind them, and asking Discord about them would be a fetch that fails on every sack of a
test-mode roster.

**A driver who has left the server is still sacked.** The member lookup fails, the roles go with
them, and the database still has to be put right — refusing would leave a departed driver
holding a seat nobody could free.

**The audit records the state and the divisions as they were.** Afterwards there is nothing left
to read them from, which is the whole reason they are captured before the deletion rather than
after.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.driver_profile import DriverState  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402

SERVER_ID = 10708
SEASON_ID = 1
PRIOR_SEASON_ID = 2
DIVISION_ID = 11
PRIOR_DIVISION_ID = 12
ROUND_ID = 21
PROFILE_ID = 31
DISCORD_USER_ID = "4242"
ACTOR_ID = 77
SIGNED_UP_ROLE = 555


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
    signed_up_role: int | None = SIGNED_UP_ROLE,
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
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 999)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, scheduled_at, format) "
            "VALUES (?, ?, 1, '2026-02-01T18:00:00+00:00', 'NORMAL')",
            (ROUND_ID, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_profiles (id, server_id, discord_user_id, current_state, "
            "former_driver, is_test_driver) VALUES (?, ?, ?, ?, ?, ?)",
            (
                PROFILE_ID,
                SERVER_ID,
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
            "INSERT INTO team_instances (division_id, name, is_reserve) "
            "VALUES (?, 'Red', 0)",
            (DIVISION_ID,),
        )
        await db.execute(
            "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
            "VALUES (?, 1, ?)",
            (cursor.lastrowid, PROFILE_ID),
        )
        await db.execute(
            "INSERT INTO signup_records (server_id, discord_user_id, "
            "discord_username, nationality) VALUES (?, ?, 'racer', 'GB')",
            (SERVER_ID, DISCORD_USER_ID),
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
            "team_role_id, finishing_position, driver_profile_id) "
            "VALUES (?, 4242, 3001, 1, ?)",
            (session_result_id, PROFILE_ID),
        )
        await db.execute(
            "INSERT INTO qualifying_session_results (session_result_id, driver_user_id, "
            "team_role_id, finishing_position, driver_profile_id) "
            "VALUES (?, 4242, 3001, 1, ?)",
            (session_result_id, PROFILE_ID),
        )
        await db.execute(
            "INSERT INTO driver_standings_snapshots (round_id, division_id, "
            "driver_user_id, standing_position, driver_profile_id) "
            "VALUES (?, ?, 4242, 1, ?)",
            (ROUND_ID, DIVISION_ID, PROFILE_ID),
        )
        if signed_up_role is not None:
            await db.execute(
                "INSERT INTO signup_module_config (server_id, signed_up_role_id) "
                "VALUES (?, ?)",
                (SERVER_ID, signed_up_role),
            )
        await db.commit()
    return db_path


def _guild(*, member_missing: bool = False, fetch_fails: bool = False, has_role: bool = True):
    guild = MagicMock()
    role = MagicMock()
    role.id = SIGNED_UP_ROLE
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
        SERVER_ID,
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


@pytest.mark.parametrize("state", [DriverState.UNASSIGNED, DriverState.ASSIGNED])
async def test_an_unassigned_or_assigned_driver_may_be_sacked(tmp_path, state):
    db_path = await _make_db(tmp_path, name=f"sack_{state.value}", state=state)

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == DriverState.NOT_SIGNED_UP.value


@pytest.mark.parametrize(
    "state",
    [
        DriverState.NOT_SIGNED_UP,
        DriverState.PENDING_ADMIN_APPROVAL,
        DriverState.PENDING_DRIVER_CORRECTION,
        DriverState.SEASON_BANNED,
        DriverState.LEAGUE_BANNED,
    ],
)
async def test_any_other_state_is_refused(tmp_path, state):
    """Someone mid-signup has nothing to revoke and someone banned is already out —
    sacking either would do nothing or quietly overwrite a ban with a milder state."""
    db_path = await _make_db(tmp_path, name=f"refuse_{state.value}", state=state)

    with pytest.raises(ValueError, match="Unassigned or Assigned"):
        await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == state.value


async def test_the_refusal_names_the_state_it_found(tmp_path):
    """A manager who meets it needs to know which state they hit, not merely that they hit
    one — a banned driver and a half-signed-up one call for different next steps."""
    db_path = await _make_db(
        tmp_path, name="refuse_names", state=DriverState.LEAGUE_BANNED
    )

    with pytest.raises(ValueError, match="LEAGUE_BANNED"):
        await _sack(_service(db_path), _guild())


async def test_a_profile_from_another_server_is_not_found(tmp_path):
    """Profiles are per server, and sacking across one would let a manager reach into
    another league's roster."""
    db_path = await _make_db(tmp_path, name="refuse_server")
    service = _service(db_path)

    with pytest.raises(ValueError, match="not found"):
        await service.sack_driver(
            99999, PROFILE_ID, SEASON_ID, ACTOR_ID, "Manager", _guild(), DISCORD_USER_ID
        )


# ---------------------------------------------------------------------------
# A driver who never raced: the profile goes
# ---------------------------------------------------------------------------


async def test_a_driver_who_never_raced_is_deleted(tmp_path):
    """Before round 1 is final nobody in the league has raced, so this is the ordinary case
    and not an edge — issue #211, where it raised and left the driver seated."""
    db_path = await _make_db(tmp_path, name="sack_delete", former_driver=False)

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) is None
    assert (
        await _count(
            db_path, "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id IS NOT NULL"
        )
        == 0
    )
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 0


async def test_a_driver_who_never_raced_is_still_stripped_of_their_roles(tmp_path):
    """Deletion is the database half; the roles are the half the driver sees."""
    db_path = await _make_db(tmp_path, name="sack_delete_roles", former_driver=False)
    service = _service(db_path)

    await _sack(service, _guild())

    service.revoke_all_placement_roles.assert_awaited_once()
    service._revoke_roles.assert_awaited_once()


async def test_a_driver_who_never_raced_loses_their_attendance_history(tmp_path):
    """It references the profile with no cascade, so it has to go first or the deletion is
    refused — and a driver who is deleted keeps no record to attach it to."""
    db_path = await _make_db(tmp_path, name="sack_delete_attendance", former_driver=False)

    await _sack(_service(db_path), _guild())

    assert await _count(db_path, "SELECT COUNT(*) FROM driver_round_attendance") == 0


async def test_results_and_standings_outlive_a_deleted_driver(tmp_path):
    """They belong to the round, and deleting them would change what every other driver in
    it scored and where they stood. They let go of the profile instead."""
    db_path = await _make_db(tmp_path, name="sack_delete_results", former_driver=False)

    await _sack(_service(db_path), _guild())

    for table in (
        "race_session_results",
        "qualifying_session_results",
        "driver_standings_snapshots",
    ):
        assert await _count(db_path, f"SELECT COUNT(*) FROM {table}") == 1, table
        assert (
            await _count(
                db_path, f"SELECT COUNT(*) FROM {table} WHERE driver_profile_id IS NOT NULL"
            )
            == 0
        ), table


async def test_a_deleted_drivers_deletion_is_audited_as_such(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_delete_audit", former_driver=False)

    await _sack(_service(db_path), _guild())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT new_value FROM audit_entries WHERE server_id = ?", (SERVER_ID,)
        )
        row = await cursor.fetchone()
    assert json.loads(row["new_value"])["former_driver"] is False


async def _carry_over_from_a_completed_season(db_path) -> None:
    """Give the driver a season they sat through without racing: completing a season keeps
    its assignments and writes a history entry for every driver assigned in it, whether or
    not they ever turned up."""
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO seasons (id, server_id, season_number, start_date, status) "
            "VALUES (?, ?, 0, '2025-01-01', 'COMPLETED')",
            (PRIOR_SEASON_ID, SERVER_ID),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Pro', 1, 999)",
            (PRIOR_DIVISION_ID, PRIOR_SEASON_ID),
        )
        await db.execute(
            "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
            "division_id) VALUES (?, ?, ?)",
            (PROFILE_ID, PRIOR_SEASON_ID, PRIOR_DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO driver_history_entries (driver_profile_id, season_number, "
            "division_name, division_tier, final_position, final_points, "
            "points_gap_to_winner, cancelled) VALUES (?, 0, 'Pro', 1, 0, 0, 0, 0)",
            (PROFILE_ID,),
        )
        await db.commit()


async def test_a_driver_carried_over_from_a_completed_season_who_never_raced_is_deleted(
    tmp_path,
):
    """Last season's assignment and history entry both reference the profile with no
    cascade. Clearing only this season's assignment left the deletion refused on a foreign
    key, for every driver who sat out a whole season and stayed on the roster."""
    db_path = await _make_db(tmp_path, name="sack_carried_over", former_driver=False)
    await _carry_over_from_a_completed_season(db_path)

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) is None
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 0
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_history_entries") == 0


async def test_a_driver_who_never_raced_is_deleted_between_seasons(tmp_path):
    """With no season to scope to, the assignments are left alone for a former driver — but
    a profile being deleted cannot keep any."""
    db_path = await _make_db(tmp_path, name="sack_delete_noseason", former_driver=False)

    await _sack(_service(db_path), _guild(), season_id=None)

    assert await _profile_state(db_path) is None
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 0


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
            "WHERE server_id = ? AND discord_user_id = ?",
            (SERVER_ID, DISCORD_USER_ID),
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


async def test_the_signed_up_role_is_revoked(tmp_path):
    """Granted at approval and not covered by the placement roles, so it would otherwise
    mark a sacked driver as signed up indefinitely."""
    db_path = await _make_db(tmp_path, name="sack_signedup")
    service = _service(db_path)

    await _sack(service, _guild())

    service._revoke_roles.assert_awaited_once()
    assert service._revoke_roles.await_args.args[1] == SIGNED_UP_ROLE


async def test_a_driver_without_the_signed_up_role_is_not_asked_to_lose_it(tmp_path):
    """Discord refuses a removal of a role the member does not hold, and it would be an
    API call on every sack of a driver who never had one."""
    db_path = await _make_db(tmp_path, name="sack_norole")
    service = _service(db_path)

    await _sack(service, _guild(has_role=False))

    service._revoke_roles.assert_not_awaited()


async def test_a_league_with_no_signed_up_role_configured_revokes_nothing_extra(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_noconfig", signed_up_role=None)
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
# Sacking outside a season
# ---------------------------------------------------------------------------


async def test_a_sack_with_no_season_leaves_the_assignments_alone(tmp_path):
    """A former driver may be sacked between seasons, when there is no season to unassign
    them from — and deleting every assignment they ever had would erase last season's record."""
    db_path = await _make_db(tmp_path, name="sack_noseason")

    await _sack(_service(db_path), _guild(), season_id=None)

    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 1


async def test_a_sack_with_no_season_revokes_no_placement_roles(tmp_path):
    """There is no placement to revoke; only the signed-up role applies."""
    db_path = await _make_db(tmp_path, name="sack_noseason_roles")
    service = _service(db_path)

    await _sack(service, _guild(), season_id=None)

    service.revoke_all_placement_roles.assert_not_awaited()
    service._revoke_roles.assert_awaited_once()


# ---------------------------------------------------------------------------
# The audit, and the lineup
# ---------------------------------------------------------------------------


async def test_the_sack_is_audited(tmp_path):
    db_path = await _make_db(tmp_path, name="sack_audit")

    await _sack(_service(db_path), _guild())

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT change_type, actor_id, old_value, new_value FROM audit_entries "
            "WHERE server_id = ?",
            (SERVER_ID,),
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
            "SELECT old_value, new_value FROM audit_entries WHERE server_id = ?",
            (SERVER_ID,),
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
            "SELECT new_value FROM audit_entries WHERE server_id = ?", (SERVER_ID,)
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


async def test_a_driver_in_no_division_refreshes_no_lineup(tmp_path):
    """An unassigned driver has no lineup to appear in, and refreshing one would be a post
    edited for no reason."""
    db_path = await _make_db(
        tmp_path, name="sack_nolineup", state=DriverState.UNASSIGNED
    )
    async with get_connection(db_path) as db:
        await db.execute("DELETE FROM driver_season_assignments")
        await db.commit()
    service = _service(db_path)

    await _sack(service, _guild())

    service._refresh_lineup_post.assert_not_awaited()
