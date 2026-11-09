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

**The sack is saved before any role is taken** (#283). The roles to take are read inside the save,
from the seat assignments it removes; then a step of its own takes them, and a role Discord refuses
is reported while the sack stands.

**A test driver has no roles to revoke**, and **a driver who has left the server is still
sacked**. **The audit records the state and the divisions as they were.**
"""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.driver_profile import DriverState
from leaguebot.core.services.placement_service import PlacementService
from tests.core.test_driver_move import _end_the_races, _unconfirm_lewis
from tests.support.teams import seed_team_instances

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
    return await service.sack_driver(
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
    out — sacking either would do nothing but move a driver the season no longer holds. The
    refusal names the state it found: a manager who meets it needs to know which state they
    hit, not merely that they hit one — a driver still in review and one already gone call for
    different next steps."""
    db_path = await _make_db(tmp_path, name=f"refuse_{state.value}", state=state)

    with pytest.raises(ValueError, match=f"Unassigned or Assigned.*{state.value}"):
        await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == state.value


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


async def test_a_former_driver_is_returned_to_not_signed_up(tmp_path):
    """Which is what lets them sign up again next season without a manager unpicking
    anything. Their profile is kept: every historical result points at it — deleting it would
    either fail on a foreign key or rewrite a season's results to name nobody."""
    db_path = await _make_db(tmp_path, name="former_state", former_driver=True)

    await _sack(_service(db_path), _guild())

    assert await _profile_state(db_path) == DriverState.NOT_SIGNED_UP.value
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_profiles") == 1


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


async def test_the_sack_names_the_divisions_whose_lineup_is_owed(tmp_path):
    """The posted lineup is what a division reads to know who is racing, and a sacked
    driver left standing in it is the visible half of the sack not happening. The sack names
    the division they left, for its caller to ask the change queue for its lineup (#439)."""
    db_path = await _make_db(tmp_path, name="sack_lineup")
    service = _service(db_path)
    guild = _guild()

    assert await _sack(service, guild) == [DIVISION_ID]
    service._refresh_lineup_post.assert_not_awaited()


# ---------------------------------------------------------------------------
# A refused role change in attendance's sync's committing form; the sanction's own save (#439)
# ---------------------------------------------------------------------------


def _refusing_guild():
    """The league's server, whose member 4242 holds the division's role and the driver role,
    and from whom Discord refuses to take any role (Missing Permissions)."""
    guild = MagicMock()
    roles = {}
    for role_id in (999, DRIVER_ROLE):
        role = MagicMock()
        role.id = role_id
        roles[role_id] = role
    guild.get_role = MagicMock(side_effect=roles.get)
    member = MagicMock()
    member.id = int(DISCORD_USER_ID)
    member.guild = guild
    member.roles = list(roles.values())
    member.remove_roles = AsyncMock(
        side_effect=discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    )
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)
    guild._member = member
    return guild


async def test_a_role_discord_refuses_is_logged_and_the_sack_still_saved(tmp_path, caplog):
    """`sack_driver`, the committing form `/attendance sync` sacks through until attendance's
    own pass (#285), `/driver sack` being a change on the queue (#283): Discord refuses to take
    the driver's roles, the refusal is logged on the host, and the sack is saved all the same:
    the driver is back at Not Signed Up with no placement."""
    db_path = await _make_db(tmp_path, name="sack_refused_roles")
    service = PlacementService(db_path)
    service._refresh_lineup_post = AsyncMock(return_value=None)
    guild = _refusing_guild()

    with caplog.at_level("WARNING", logger="leaguebot.core.services.placement_service"):
        await _sack(service, guild)

    guild._member.remove_roles.assert_awaited()
    assert any("failed to remove role" in r.getMessage() for r in caplog.records)
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 0
    assert await _count(
        db_path, "SELECT COUNT(*) FROM driver_profiles WHERE id = ? AND current_state = ?",
        (PROFILE_ID, DriverState.NOT_SIGNED_UP.value),
    ) == 1


async def test_sack_on_reads_the_roles_before_it_frees_the_seats(tmp_path):
    """A sanction's save sacks the driver on the save it is handed and names the roles to take
    back, for a job of its own to take them: the driver of Pro, holding its role and the driver
    role, is sacked on that save. The result names Pro, the driver's account and both roles,
    read before the placement was removed; on that save the driver is placed nowhere, and since
    it commits nothing, rolled back they are still placed. No role is asked of Discord."""
    db_path = await _make_db(tmp_path, name="sack_on_roles")
    service = PlacementService(db_path)

    async with get_connection(db_path) as db:
        saved = await service.sack_on(
            db, driver_profile_id=PROFILE_ID, season_id=SEASON_ID, acting_user_id=ACTOR_ID,
            acting_user_name="Manager#0001",
        )
        placed = await (await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = ?",
            (PROFILE_ID,),
        )).fetchone()
        assert placed[0] == 0
        await db.rollback()

    assert saved.divisions == [DIVISION_ID]
    assert saved.user_id == int(DISCORD_USER_ID)
    assert saved.is_test_driver is False
    assert {999, DRIVER_ROLE} <= set(saved.revoke_role_ids)
    assert await _count(db_path, "SELECT COUNT(*) FROM driver_season_assignments") == 1


# ---------------------------------------------------------------------------
# /driver sack, on the change queue (#283)
# ---------------------------------------------------------------------------
#
# The command asks the change queue for a change of its own on the league's lane, the driver
# named at the ask by their id. The queue's acknowledgement is its reply. One save, stamped with
# the queue's clock, sacks the driver (`sack_on`) and writes the `DRIVER_SACK` audit entry and the
# log channel's line; then a step takes the member's roles, which ends on a fault rather than
# stopping the lane (architecture.md, "A job that fails stops its lane"), a role Discord refuses
# being reported in the reply and the line; then the lineup of each division left is posted.
# These run on `ongoing_league`'s season (see `test_driver_move`'s section of the same name),
# the league's driver role (820) configured and held by Lewis and Max beside Pro's and Ferrari's.


async def _ongoing_sack(tmp_path):
    """`ongoing_league`'s season, the driver role configured, and Lewis and Max holding it with
    Pro's role and Ferrari's."""
    from tests.core.test_driver_move import _ongoing
    from tests.support.season_league import DRIVER_ROLE as LEAGUE_DRIVER_ROLE, LEWIS, MAX

    league = await _ongoing(tmp_path)
    await league.write("UPDATE server_configs SET driver_role_id = ?", LEAGUE_DRIVER_ROLE)
    for user_id in (LEWIS, MAX):
        league.roles_held[user_id].add(LEAGUE_DRIVER_ROLE)
    return league


async def _ask_sack(league, user_id):
    """`/driver sack` of the driver *user_id*, as the league admin Admin (77) runs it: the
    interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver sack",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.sack)(DriverCog(league.bot), asked, league._member(user_id))
    return asked


def _sacking(name: str) -> str:
    """How the queue words `/driver sack` of *name*."""
    return f"Sacking **{name}**"


def _sacked(name: str) -> str:
    """`/driver sack`'s reply once saved, word for word, as today."""
    return f"✅ **{name}** has been sacked. All roles and season assignments removed."


def _sack_line(name: str, user_id: int) -> str:
    """`/driver sack`'s line in the log channel, word for word: today's, the member asking named
    as every change on the queue names them."""
    from tests.core.test_driver_move import _ADMIN

    return f"{_ADMIN} | /driver sack | Success\n  user: {name} (`<@{user_id}>`)"


def _sack_refusal(reply: str) -> str:
    from leaguebot.core.utils.log_lines import reply_reason
    from tests.core.test_driver_move import _ADMIN

    return f"⛔ `/driver sack` refused for {_ADMIN} — {reply_reason(reply)}"


def _sack_lines(lines):
    return [line for line in lines if "/driver sack" in line]


async def _state(league, user_id):
    from tests.support.season_league import profile_id

    [row] = await league.rows(
        "SELECT current_state FROM driver_profiles WHERE id = ?", profile_id(user_id)
    )
    return row["current_state"]


async def test_a_driver_sack_and_its_audit_entry_and_line_are_one_save_then_roles_and_lineups(
    tmp_path,
):
    """The core specification's sacking and its record of what changed. The season is ongoing;
    Lewis sits at Ferrari in Pro, confirmed, holding Pro's role, Ferrari's and the driver role.
    The league admin Admin (77) runs `/driver sack` of Lewis. They are answered at once with the
    queue's acknowledgement, "Sacking **Lewis**", naming its first job, and nothing is saved, no
    role touched and no lineup posted until the queue runs the change, of kind `core.driver_sack`.
    Once it has, Lewis holds no seat and is at Not Signed Up, every other driver where they sat;
    one audit entry, `DRIVER_SACK`, by Admin, stamped with the queue's clock, records him Assigned
    in Pro before and Not Signed Up after; the log channel holds today's success line once; Lewis
    has Pro's role, Ferrari's and the driver role taken; Pro's lineup is posted once in its lineup
    channel and Am's not at all; and the acknowledgement is updated to today's reply, word for
    word."""
    from tests.core.test_driver_move import _STANDING, _seated
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import (
        AM, DIVISIONS, DRIVER_ROLE as LEAGUE_DRIVER_ROLE, FERRARI_ROLE, LEWIS, PRO, PRO_ROLE,
    )
    from tests.support.settings_league import audit_rows, last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _ongoing_sack(tmp_path)
    asked = await _ask_sack(league, LEWIS)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_sacking('Lewis'))} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.driver_sack"]
    assert await _seated(league) == _STANDING
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    await run_queue(league.bot)

    assert await _seated(league) == {k: v for k, v in _STANDING.items() if k != LEWIS}
    assert await _state(league, LEWIS) == DriverState.NOT_SIGNED_UP.value
    [entry] = [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_SACK"
    ]
    assert (entry["change_type"], entry["actor_id"]) == ("DRIVER_SACK", 77)
    assert entry["old_value"] == {"state": "ASSIGNED", "divisions": [PRO]}
    assert entry["new_value"]["new_state"] == DriverState.NOT_SIGNED_UP.value
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _sack_lines(await log_lines(league)) == [_sack_line("Lewis", LEWIS)]
    assert sorted(league.revoked[LEWIS]) == [PRO_ROLE, FERRARI_ROLE, LEAGUE_DRIVER_ROLE]
    assert LEWIS not in league.granted
    assert len(league.texts(DIVISIONS[PRO][3].lineup)) == 1
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    assert last_said(asked) == _sacked("Lewis")


async def test_the_roles_go_after_the_sack_is_saved_and_a_refused_one_is_reported(tmp_path):
    """The sack is saved before any role is taken, and a role Discord refuses ends its own step
    rather than stopping the lane (architecture.md, "A job that fails stops its lane"). The season
    is ongoing; Lewis and Max sit at Ferrari in Pro. The league admin runs `/driver sack` of Lewis,
    then of Max, before the queue runs; Discord refuses to take any role from Lewis (403 Missing
    Permissions). The queue runs both. Lewis's sack stands, saved before his roles were asked of
    Discord: he holds no seat and is at Not Signed Up, and he still holds every role. His reply
    drops today's claim that every role went, reading "✅ **Lewis** has been sacked. Season
    assignments removed.", then "⚠️ Done, but on Discord:" and a bullet naming the roles refused,
    then the kind of fault in plain words: "the roles <@&801>, <@&811> and <@&820> could not be
    taken from <@101>: Discord refused or failed a request from the bot"; his success line carries a
    "not done:" line in the same words, its mentions written in code as the log channel writes every
    mention. The lane is not stopped, and Max's sack, asked after, is made in full: his three roles
    taken, his reply today's alone."""
    import discord

    from tests.core.test_driver_move import _as_logged, _seated
    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.season_league import (
        DRIVER_ROLE as LEAGUE_DRIVER_ROLE, FERRARI_ROLE, LEWIS, MAX, PRO_ROLE,
    )
    from tests.support.settings_league import last_said, log_lines

    league = await _ongoing_sack(tmp_path)
    league.revoke_fails[LEWIS] = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    lewis = await _ask_sack(league, LEWIS)
    max_ = await _ask_sack(league, MAX)
    await run_queue(league.bot)

    problem = (
        f"the roles <@&{PRO_ROLE}>, <@&{FERRARI_ROLE}> and <@&{LEAGUE_DRIVER_ROLE}> could not be "
        f"taken from <@{LEWIS}>: Discord refused or failed a request from the bot"
    )
    seated = await _seated(league)
    assert LEWIS not in seated and MAX not in seated
    assert await _state(league, LEWIS) == DriverState.NOT_SIGNED_UP.value
    assert league.roles_held[LEWIS] == {PRO_ROLE, FERRARI_ROLE, LEAGUE_DRIVER_ROLE}
    assert last_said(lewis) == (
        "✅ **Lewis** has been sacked. Season assignments removed.\n"
        f"⚠️ Done, but on Discord:\n• {problem}"
    )
    assert _sack_lines(await log_lines(league)) == [
        f"{_sack_line('Lewis', LEWIS)}\n  not done: {_as_logged(problem)}", _sack_line("Max", MAX),
    ]
    assert await stopped_job(league.db_path) is None
    assert sorted(league.revoked[MAX]) == [PRO_ROLE, FERRARI_ROLE, LEAGUE_DRIVER_ROLE]
    assert last_said(max_) == _sacked("Max")


async def test_a_failed_driver_sack_sacks_nothing(tmp_path):
    """The season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver sack`
    of Lewis, and as the queue runs it the database fails writing the audit entry. Nothing of the
    sack is saved, the sack, its audit entry and its line being one save: every driver sits where
    they sat, Lewis still Assigned, no audit entry is written, the log channel holds no success
    line, no role is touched on Discord and no lineup posted. The league's lane stops at the job.
    Once a league admin discards the stopped job, the reply says "Nobody was sacked: **Lewis**
    keeps their seats. Run `/driver sack` again."; and the lane is clear."""
    from tests.core.test_driver_move import _STANDING, _seated
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, LEWIS, PRO
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _ongoing_sack(tmp_path)
    asked = await _ask_sack(league, LEWIS)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _seated(league) == _STANDING
    assert await _state(league, LEWIS) == DriverState.ASSIGNED.value
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nobody was sacked: **Lewis** keeps their seats. Run `/driver sack` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply, lewis_after",
    [
        pytest.param(
            _end_the_races,
            "⛔ `/driver sack` is available only while the season is ongoing.",
            ("Pro", "Ferrari", True), id="the-races-ended",
        ),
        pytest.param(
            _unconfirm_lewis,
            "⛔ Only a driver whose placement is confirmed can be sacked. Remove an unconfirmed "
            "placement with `/driver unassign`, and turn down an Unassigned driver with "
            "`/driver reject`.",
            ("Pro", "Ferrari", False), id="the-placement-unconfirmed",
        ),
    ],
)
async def test_a_driver_sack_is_judged_again_as_it_starts(tmp_path, meanwhile, reply, lewis_after):
    """The season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver sack`
    of Lewis and is acknowledged; before the queue runs it, the season's races end (Pending
    completion), or Lewis's placement in Pro is made unconfirmed. The queue runs. The sack is
    refused as it starts, in the words it would meet at the ask, its acknowledgement updated to
    them. Lewis's Pro placement stands as the meanwhile left it, he is still Assigned, no audit
    entry is written, the log channel holds the refusal alone, no role is touched, no lineup
    posted, and the queue is not stopped."""
    from tests.core.test_driver_move import _seated
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, LEWIS, PRO
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _ongoing_sack(tmp_path)
    refused = await _ask_sack(league, LEWIS)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert (await _seated(league))[LEWIS] == lewis_after
    assert await _state(league, LEWIS) == DriverState.ASSIGNED.value
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [_sack_refusal(reply)]
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert await stopped_job(league.db_path) is None


async def test_a_driver_sack_is_refused_at_the_ask(tmp_path):
    """The core specification's sacking. The season is ongoing; Lewis sits at Ferrari in Pro,
    his placement unconfirmed. The league admin runs `/driver sack` of Lewis. They are refused at
    once, in today's words: "Only a driver whose placement is confirmed can be sacked. Remove an
    unconfirmed placement with `/driver unassign`, and turn down an Unassigned driver with
    `/driver reject`.", and the log channel takes the refusal as its one line. Nothing is asked
    of the queue, Lewis keeps his seat and is still Assigned, no audit entry is written and no
    role touched. Takes the place of `test_driver_cog_refusals.py`'s "sack refused by the
    service" case, which mocked the service."""
    from tests.core.test_driver_move import _seated, _unconfirm_lewis
    from tests.support.change_queue import change_rows
    from tests.support.season_league import LEWIS
    from tests.support.settings_league import audit_rows, last_said, log_lines

    reply = (
        "⛔ Only a driver whose placement is confirmed can be sacked. Remove an unconfirmed "
        "placement with `/driver unassign`, and turn down an Unassigned driver with "
        "`/driver reject`."
    )
    league = await _ongoing_sack(tmp_path)
    await _unconfirm_lewis(league)
    asked = await _ask_sack(league, LEWIS)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_sack_refusal(reply)]
    assert await change_rows(league.db_path) == []
    assert (await _seated(league))[LEWIS] == ("Pro", "Ferrari", False)
    assert await _state(league, LEWIS) == DriverState.ASSIGNED.value
    assert await audit_rows(league.db_path) == []
    assert league.revoked == {} and league.granted == {}


async def test_a_driver_sack_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver sack` of Lewis
    and is acknowledged; before the queue runs it, they run it again. The second is refused at
    once, in the queue's words, and the log channel takes that refusal as a line; only the one
    change is queued. The queue runs it: Lewis is at Not Signed Up, with one audit entry, and the
    log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import LEWIS
    from tests.support.settings_league import audit_rows, log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _ongoing_sack(tmp_path)
    first, repeat = await _ask_sack(league, LEWIS), await _ask_sack(league, LEWIS)

    assert said(first)[0].startswith(acknowledged(_sacking("Lewis")))
    assert said(repeat) == [repeated(_sacking("Lewis"))]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _state(league, LEWIS) == DriverState.NOT_SIGNED_UP.value
    assert len([
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_SACK"
    ]) == 1
    assert _sack_lines(await log_lines(league)) == [
        _sack_refusal(repeated(_sacking("Lewis"))), _sack_line("Lewis", LEWIS),
    ]
