"""Attendance's share of a round's review, as the hook the builder hands results' change types
(#439, slice 2, plan 2.6).

`AttendanceAfterReview`, in `leaguebot.attendance.services.attendance_after_review`, is built once
by the entry point and handed the placement service. Results' change types call it; it calls
attendance's own functions. These tests run the real hook against a database built by the
migrations, where the queued tests of the approvals (`tests/results/test_report_approval_change.py`,
`test_amendment_stage_changes.py`) drive a double of it.

Four rules this holds to.

**Each method checks attendance's switch itself, and does nothing while it is off**
(architecture.md, "How modules and core fit together"). The writers read it on the connection
they are handed.

**The writers write on the save they are handed and commit nothing**, so the round's attendance
lands with the penalties and points or not at all ("Whole approval fails"). An amendment's
recalculation rebuilds the amended round's attended flags in both directions (FR-028): a driver the
correction removes is marked absent. The league chose the correction over the driver's favour; do
not "fix" this to the upgrade-only recording.

**A sanction is one driver's, and one that does not apply raises** ("Each sanction is a job"):
autosack supersedes autoreserve (FR-025), a driver already signed off or already in the reserve
team is not owed one (attendance spec), and a division with no reserve team is a failure of the
autoreserve, not a sanction passed over. The placement service is the one handed in, never the
bot's.

**A sheet Discord refuses raises**, for the queue to stop on and retry, rather than being put on
the old retry queue (defect 4).

**A sanction is checked, saved and has its roles changed in three calls** (#439 slice 8, Part C):
an autoreserve's move is checked before it is saved; the save writes on the connection handed and
names the roles to change; the role change raises where Discord refuses or cannot fetch the
driver, passes over a driver who left, changes only what the driver is no longer or now entitled
to, and acts even once attendance is off (owner, check 1).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.results.services.penalty_wizard import StagedPardon
from tests.support.change_queue import http_error, in_a_job

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
SEASON_ID = 51
DIVISION_ID = 61
OTHER_DIVISION_ID = 62
AMENDED_ROUND = 3
LATEST_ROUND = 7
ATTENDANCE_CHANNEL = 7400
OLD_SHEET = 7401
#: Full-time drivers of team Alpha, and the one in the reserve team; each profile's Discord id
#: is its own number.
LEWIS, MAX, STAND_IN = 101, 103, 102
BOT_USER_ID = 9999
#: The roles of division Pro, of team Alpha and of the Reserve team.
PRO_ROLE, ALPHA_ROLE, RESERVE_ROLE = 555, 700, 701
#: Why the sanction tests fail until the build: the hook has no check of an autoreserve, no
#: sanction saved on the caller's save, and no role change of its own.
SANCTION_JOBS_UNBUILT = (
    "#439: the hook cannot check a sanction, save it on the caller's save, or change its roles"
)


async def _db(tmp_path, *, attendance: bool = True, autoreserve: int | None = 10,
              autosack: int | None = 20, reserve_team: bool = True) -> str:
    """Season 51's division 61 (Pro): team Alpha seats Lewis and Max full-time, the Reserve team
    (where *reserve_team*) seats a stand-in. Rounds 3 and 7 are final; round 3's Feature Race
    classified Max alone. The attendance channel holds the sheet posted earlier. Every
    placement is confirmed, and team Alpha and the Reserve team each have a role."""
    db_path = os.path.join(str(tmp_path), "after_review.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO attendance_config (id, module_enabled, autoreserve_threshold, "
            "autosack_threshold) VALUES (1, ?, ?, ?)",
            (int(attendance), autoreserve, autosack),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 6, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        for division_id, name, tier in ((DIVISION_ID, "Pro", 1), (OTHER_DIVISION_ID, "Am", 2)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
                "VALUES (?, ?, ?, ?, 555)",
                (division_id, SEASON_ID, name, tier),
            )
        await db.execute(
            "INSERT INTO attendance_division_config (division_id, attendance_channel_id, "
            "attendance_message_id) VALUES (?, ?, ?)",
            (DIVISION_ID, str(ATTENDANCE_CHANNEL), str(OLD_SHEET)),
        )
        for round_id, number in ((AMENDED_ROUND, 3), (LATEST_ROUND, 7)):
            await db.execute(
                "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
                "scheduled_at, status) VALUES (?, ?, ?, 'NORMAL', 'Silverstone', "
                "'2026-02-01T18:00:00+00:00', 'FINAL')",
                (round_id, DIVISION_ID, number),
            )
        await db.execute(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (10, ?, 'Alpha', 'Alpha', 2, 0)",
            (DIVISION_ID,),
        )
        if reserve_team:
            await db.execute(
                "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, "
                "is_reserve) VALUES (11, ?, 'Reserve', 'Reserve', 6, 1)",
                (DIVISION_ID,),
            )
        seats = [(20, 10, 1, LEWIS), (21, 10, 2, MAX)]
        if reserve_team:
            seats.append((22, 11, 1, STAND_IN))
        for seat_id, team_id, number, profile in seats:
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile, str(profile)),
            )
            await db.execute(
                "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
                "VALUES (?, ?, ?, ?)",
                (seat_id, team_id, number, profile),
            )
            await db.execute(
                "INSERT INTO driver_season_assignments (driver_profile_id, season_id, "
                "division_id, team_seat_id, committed) VALUES (?, ?, ?, ?, 1)",
                (profile, SEASON_ID, DIVISION_ID, seat_id),
            )
        for team, role in (("Alpha", ALPHA_ROLE), ("Reserve", RESERVE_ROLE)):
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)", (team, role)
            )
        session = await db.execute(
            "INSERT INTO session_results (round_id, division_id, session_type, status, "
            "config_name) VALUES (?, ?, 'FEATURE_RACE', 'ACTIVE', 'Standard')",
            (AMENDED_ROUND, DIVISION_ID),
        )
        await db.execute(
            "INSERT INTO race_session_results (session_result_id, driver_user_id, "
            "team_instance_id, finishing_position, outcome, base_time_ms, points_awarded, "
            "driver_profile_id) VALUES (?, ?, 10, 1, 'CLASSIFIED', 3600000, 25, ?)",
            (session.lastrowid, MAX, MAX),
        )
        await db.commit()
    return db_path


async def _totals(db_path: str, round_id: int, totals: dict[int, int]) -> None:
    """Each profile in *totals* attended *round_id* with that running total."""
    async with get_connection(db_path) as db:
        for profile, total in totals.items():
            await db.execute(
                "INSERT INTO driver_round_attendance (round_id, division_id, driver_profile_id, "
                "rsvp_status, attended, total_points_after) VALUES (?, ?, ?, 'ACCEPTED', 1, ?)",
                (round_id, DIVISION_ID, profile, total),
            )
        await db.commit()


def _sheet_channel() -> Any:
    channel = MagicMock()
    channel.id = ATTENDANCE_CHANNEL
    channel.send = AsyncMock(return_value=MagicMock(id=7402))
    channel.fetch_message = AsyncMock(return_value=MagicMock(delete=AsyncMock()))
    return channel


def _bot(db_path: str, *, attendance: bool = True, channel: Any = None,
         guild: Any = None) -> Any:
    """A bot double whose own placement service refuses every call: the hook is to use the one
    it is handed. Every channel it reaches is *channel*; the league's server is *guild*."""
    channel = channel or _sheet_channel()
    guild = guild or MagicMock()
    guild.get_channel = MagicMock(return_value=channel)
    bot = MagicMock()
    bot.db_path = db_path
    bot.user = MagicMock(id=BOT_USER_ID)
    bot.get_guild = MagicMock(return_value=guild)
    bot.config_service.get_league_server_id = AsyncMock(return_value=1)
    bot.guilds = [guild]
    bot.get_channel = MagicMock(return_value=channel)
    bot.fetch_channel = AsyncMock(return_value=channel)
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.module_service.is_attendance_enabled = AsyncMock(return_value=attendance)
    bot.module_service.is_images_enabled = AsyncMock(return_value=False)
    bot.placement_service = MagicMock(side_effect=AssertionError("the bot's placement service"))
    for name in ("sack_driver", "move_driver", "refresh_lineup", "_refresh_lineup_post",
                 "sack_on", "move_on", "guard_move", "grant_roles", "revoke_roles"):
        setattr(bot.placement_service, name,
                AsyncMock(side_effect=AssertionError("the bot's placement service")))
    return bot


def _placement() -> Any:
    placement = MagicMock()
    for name in ("sack_driver", "move_driver", "refresh_lineup"):
        setattr(placement, name, AsyncMock(return_value=None))
    return placement


def _real_placement(db_path: str) -> Any:
    """The placement service the entry point builds, on the test's database."""
    from leaguebot.core.services.placement_service import PlacementService

    return PlacementService(db_path)


def _role_guild(held: tuple[int, ...] = (), *, gone: tuple[int, ...] = (),
                fetch_fails: Exception | None = None, refuses: Exception | None = None) -> Any:
    """The league's server, holding the roles of Pro, Alpha and the Reserve team save those
    *gone*. Its one member holds the roles *held*; fetching them raises *fetch_fails*, and a
    role change raises *refuses*, where given."""
    roles = {}
    for role_id in (PRO_ROLE, ALPHA_ROLE, RESERVE_ROLE):
        if role_id not in gone:
            role = MagicMock()
            role.id = role_id
            roles[role_id] = role
    member = MagicMock()
    member.roles = [roles[r] for r in held if r in roles]
    member.add_roles = AsyncMock(side_effect=refuses)
    member.remove_roles = AsyncMock(side_effect=refuses)
    guild = MagicMock()
    guild.id = 1
    guild.get_role = MagicMock(side_effect=roles.get)
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=member, side_effect=fetch_fails)
    guild.member = member
    guild.roles_by_id = roles
    return guild


def _role_ids(call: Any) -> set[int]:
    """The ids of the roles a role change was asked for."""
    return {role.id for role in call.args}


async def _sacked(db_path: str, profile: int) -> None:
    """*profile*'s autosack, as saved: back to Not Signed Up, seat and placement gone."""
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = ?", (profile,)
        )
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = ?",
                         (profile,))
        await db.execute("DELETE FROM driver_season_assignments WHERE driver_profile_id = ?",
                         (profile,))
        await db.commit()


async def _moved_to_reserve(db_path: str, profile: int) -> None:
    """*profile*'s autoreserve, as saved: their Alpha seat freed, a Reserve seat taken."""
    async with get_connection(db_path) as db:
        await db.execute("UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = ?",
                         (profile,))
        await db.execute(
            "INSERT INTO team_seats (id, team_instance_id, seat_number, driver_profile_id) "
            "VALUES (23, 11, 2, ?)",
            (profile,),
        )
        await db.execute(
            "UPDATE driver_season_assignments SET team_seat_id = 23 WHERE driver_profile_id = ?",
            (profile,),
        )
        await db.commit()


async def _state(db_path: str, profile: int) -> tuple[str, int]:
    """*profile*'s state and the number of their placements, as committed."""
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id = ?", (profile,)
        )).fetchone()
        placed = await (await db.execute(
            "SELECT COUNT(*) FROM driver_season_assignments WHERE driver_profile_id = ?",
            (profile,),
        )).fetchone()
    return row["current_state"], placed[0]


def _hook(bot: Any, placement: Any) -> Any:
    from leaguebot.attendance.services.attendance_after_review import AttendanceAfterReview

    return AttendanceAfterReview(bot, placement)


def _by_profile(candidates: list[dict[str, Any]]) -> dict[int, str]:
    return {c["driver_profile_id"]: c["sanction"] for c in candidates}


async def test_a_driver_over_both_thresholds_is_owed_an_autosack_and_not_an_autoreserve(
    tmp_path,
):
    """FR-025: autosack supersedes autoreserve; a driver over the autoreserve threshold alone is
    owed an autoreserve."""
    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, MAX: 12})

    candidates = await _hook(_bot(db_path), _placement()).sanction_candidates(
        LATEST_ROUND, DIVISION_ID
    )

    assert _by_profile(candidates) == {LEWIS: "AUTOSACK", MAX: "AUTORESERVE"}
    assert {c["driver_user_id"] for c in candidates} == {LEWIS, MAX}


async def test_a_driver_signed_off_or_in_the_reserve_team_is_not_owed_a_sanction(tmp_path):
    """Attendance spec: a driver already sacked or already in the reserve team is not
    sanctioned a second time, so a retried sanction job applies only what is owed."""
    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, STAND_IN: 25})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = ?", (LEWIS,)
        )
        await db.commit()

    candidates = await _hook(_bot(db_path), _placement()).sanction_candidates(
        LATEST_ROUND, DIVISION_ID
    )

    assert candidates == []


async def test_an_autoreserve_in_a_division_with_no_reserve_team_raises(tmp_path):
    """Attendance spec: "a failure of the autoreserve of each driver owed one, not a sanction
    silently passed over". Pro has no Reserve team and Max is owed an autoreserve: the check
    made before the sanction is saved refuses it, for the queue to stop on, and the save
    refuses it too should it be reached. The driver is still owed it, and nothing is moved."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path, autosack=None, reserve_team=False)
    await _totals(db_path, LATEST_ROUND, {MAX: 12})
    hook = _hook(_bot(db_path), _real_placement(db_path))
    [owed] = await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID)

    with pytest.raises(StepFailedOnDiscord, match="Reserve"):
        await hook.check_sanction(LATEST_ROUND, DIVISION_ID, owed)
    async with get_connection(db_path) as db:
        with pytest.raises(Exception):
            await hook.apply_sanction_on(db, LATEST_ROUND, DIVISION_ID, owed)
        await db.rollback()

    assert owed["sanction"] == "AUTORESERVE"
    assert await _state(db_path, MAX) == ("ASSIGNED", 1)


async def test_a_sanction_is_applied_through_the_placement_service_handed_in(tmp_path):
    """Plan 2.6: the hook uses the placement service it is handed, never `bot.placement_service`,
    and saves each sanction on the save it is handed. Lewis (over the autosack threshold) is
    sacked through its `sack_on`; Max (over the autoreserve threshold) is moved to the Reserve
    team through its `move_on`, each on that save."""
    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, MAX: 12})
    placement = _real_placement(db_path)
    placement.sack_on = AsyncMock(wraps=placement.sack_on)
    placement.move_on = AsyncMock(wraps=placement.move_on)
    hook = _hook(_bot(db_path), placement)

    async with get_connection(db_path) as db:
        for owed in sorted(await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID),
                           key=lambda c: c["driver_profile_id"]):
            await hook.apply_sanction_on(db, LATEST_ROUND, DIVISION_ID, owed)
        await db.commit()

    placement.sack_on.assert_awaited_once()
    assert placement.sack_on.await_args.args[0] is db
    assert placement.sack_on.await_args.kwargs["driver_profile_id"] == LEWIS
    placement.move_on.assert_awaited_once()
    assert placement.move_on.await_args.args[0] is db
    assert placement.move_on.await_args.kwargs["driver_profile_id"] == MAX
    assert placement.move_on.await_args.kwargs["team_name"] == "Reserve"


async def test_the_hook_s_apply_sanction_returns_its_line_and_posts_nothing(tmp_path):
    """The queue writes a job's lines in the save that marks it done, so a stop between a line
    posted from the job and that mark would leave a line for a job tried again. Saving Lewis's
    autosack and Max's autoreserve on the save it is handed, the hook returns each line and the
    roles to change, posting nothing and changing no role: Lewis's roles of Pro and Alpha to be
    taken back, Max's Alpha role swapped for the Reserve team's. `/attendance sync` calls
    attendance's own `apply_sanction` with its default, which still posts the line."""
    from leaguebot.attendance.services import attendance_service

    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25, MAX: 12})
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE))
    bot = _bot(db_path, guild=guild)
    hook = _hook(bot, _real_placement(db_path))
    assert await hook.enabled() is True
    saved = {}
    async with get_connection(db_path) as db:
        for owed in sorted(await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID),
                           key=lambda c: c["driver_profile_id"]):
            saved[owed["driver_profile_id"]] = await hook.apply_sanction_on(
                db, LATEST_ROUND, DIVISION_ID, owed
            )
        await db.commit()

    assert saved[LEWIS]["line"].startswith("ATTENDANCE_AUTOSACK | ")
    assert f"driver_profile_id={LEWIS}" in saved[LEWIS]["line"]
    assert saved[MAX]["line"].startswith("ATTENDANCE_AUTORESERVE | ")
    assert saved[MAX]["line"].endswith("→ moved to Reserve")
    assert saved[LEWIS]["roles"]["user_id"] == LEWIS
    assert {PRO_ROLE, ALPHA_ROLE} <= set(saved[LEWIS]["roles"]["revoke"])
    assert saved[LEWIS]["roles"]["grant"] == []
    assert saved[MAX]["roles"]["user_id"] == MAX
    assert ALPHA_ROLE in saved[MAX]["roles"]["revoke"]
    assert RESERVE_ROLE in saved[MAX]["roles"]["grant"]
    assert ALPHA_ROLE not in saved[MAX]["roles"]["grant"]
    assert RESERVE_ROLE not in saved[MAX]["roles"]["revoke"]
    bot.output_router.post_log.assert_not_awaited()
    guild.member.add_roles.assert_not_awaited()
    guild.member.remove_roles.assert_not_awaited()

    synced = tmp_path / "synced"
    synced.mkdir()
    db_path = await _db(synced)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25})
    bot = _bot(db_path)
    [owed] = await _hook(bot, _placement()).sanction_candidates(LATEST_ROUND, DIVISION_ID)

    line = await attendance_service.apply_sanction(
        bot, bot.get_guild(1), db_path, _placement(), LATEST_ROUND, DIVISION_ID, owed,
    )

    assert line.startswith("ATTENDANCE_AUTOSACK | ")
    bot.output_router.post_log.assert_awaited_once_with(line)


async def test_apply_sanction_on_sacks_on_the_save_it_is_handed_and_commits_nothing(tmp_path):
    """Lewis is over the autosack threshold. Saving his autosack on the save handed takes him
    back to Not Signed Up with no placement on that save, and commits nothing: rolled back, he
    is still seated in Alpha. No role is changed and nothing is posted."""
    db_path = await _db(tmp_path, autoreserve=None)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25})
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE))
    bot = _bot(db_path, guild=guild)
    hook = _hook(bot, _real_placement(db_path))
    [owed] = await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID)

    async with get_connection(db_path) as db:
        saved = await hook.apply_sanction_on(db, LATEST_ROUND, DIVISION_ID, owed)
        row = await (await db.execute(
            "SELECT current_state FROM driver_profiles WHERE id = ?", (LEWIS,)
        )).fetchone()
        assert row["current_state"] == "NOT_SIGNED_UP"
        await db.rollback()

    assert saved["line"].startswith("ATTENDANCE_AUTOSACK | ")
    assert await _state(db_path, LEWIS) == ("ASSIGNED", 1)
    guild.member.remove_roles.assert_not_awaited()
    bot.output_router.post_log.assert_not_awaited()


async def test_apply_sanction_on_moves_to_reserve_and_names_the_roles_to_swap(tmp_path):
    """Max is over the autoreserve threshold alone. Saving it on the save handed, then
    committing, moves him from Alpha to the Reserve team of Pro, still placed; the result names
    Alpha's role to take back and the Reserve team's to give, and no role is changed yet."""
    db_path = await _db(tmp_path, autosack=None)
    await _totals(db_path, LATEST_ROUND, {MAX: 12})
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE))
    hook = _hook(_bot(db_path, guild=guild), _real_placement(db_path))
    [owed] = await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID)

    async with get_connection(db_path) as db:
        saved = await hook.apply_sanction_on(db, LATEST_ROUND, DIVISION_ID, owed)
        await db.commit()

    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT ti.name FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE dsa.driver_profile_id = ?",
            (MAX,),
        )).fetchone()
    assert row["name"] == "Reserve"
    assert saved["roles"]["user_id"] == MAX
    assert ALPHA_ROLE in saved["roles"]["revoke"]
    assert RESERVE_ROLE in saved["roles"]["grant"]
    guild.member.add_roles.assert_not_awaited()
    guild.member.remove_roles.assert_not_awaited()


async def test_change_sanction_roles_raises_where_discord_refuses(tmp_path):
    """Lewis's autosack is saved and he still holds the roles of Pro and Alpha. Discord refuses
    to take them (Missing Permissions): the role change raises `StepFailedOnDiscord`, for the
    queue to stop on."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path)
    await _sacked(db_path, LEWIS)
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE),
                        refuses=http_error(status=403, text="Missing Permissions"))
    hook = _hook(_bot(db_path, guild=guild), _real_placement(db_path))

    with pytest.raises(StepFailedOnDiscord):
        await hook.change_sanction_roles(LEWIS, LEWIS, revoke=[PRO_ROLE, ALPHA_ROLE], grant=[])


async def test_change_sanction_roles_passes_over_a_member_who_left(tmp_path):
    """Lewis's autosack is saved and he has since left the server (Discord answers Unknown
    Member): his roles went with him, so the change raises nothing, asks Discord for nothing,
    and says he was absent."""
    import discord

    db_path = await _db(tmp_path)
    await _sacked(db_path, LEWIS)
    guild = _role_guild(fetch_fails=http_error(discord.NotFound, status=404,
                                               text="Unknown Member"))
    hook = _hook(_bot(db_path, guild=guild), _real_placement(db_path))

    changed = await hook.change_sanction_roles(LEWIS, LEWIS, revoke=[PRO_ROLE, ALPHA_ROLE],
                                               grant=[])

    assert changed == {"absent": True}
    guild.member.remove_roles.assert_not_awaited()


async def test_change_sanction_roles_stops_where_the_member_cannot_be_fetched(tmp_path):
    """Lewis's autosack is saved, and Discord fails to fetch him for a reason other than his
    absence (a server error): the change raises `StepFailedOnDiscord`, rather than passing his
    roles over."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path)
    await _sacked(db_path, LEWIS)
    guild = _role_guild(fetch_fails=http_error(status=500, text="Internal Server Error"))
    hook = _hook(_bot(db_path, guild=guild), _real_placement(db_path))

    with pytest.raises(StepFailedOnDiscord):
        await hook.change_sanction_roles(LEWIS, LEWIS, revoke=[PRO_ROLE, ALPHA_ROLE], grant=[])


async def test_change_sanction_roles_stops_where_a_role_to_grant_is_gone(tmp_path):
    """Max's autoreserve is saved, his seat now the Reserve team's, but the Reserve team's role
    has been deleted from the server: the change raises `StepFailedOnDiscord`, and Max is given
    no role."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path)
    await _moved_to_reserve(db_path, MAX)
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE), gone=(RESERVE_ROLE,))
    hook = _hook(_bot(db_path, guild=guild), _real_placement(db_path))

    with pytest.raises(StepFailedOnDiscord):
        await hook.change_sanction_roles(MAX, MAX, revoke=[ALPHA_ROLE], grant=[RESERVE_ROLE])

    guild.member.add_roles.assert_not_awaited()


async def test_change_sanction_roles_leaves_a_role_the_driver_is_entitled_to_again(tmp_path):
    """Lewis's autosack named the roles of Pro and Alpha to take back, but before its role
    change ran a manager placed him in Alpha again. A retried role change takes neither: he is
    entitled to both. And the Reserve team's role, named to give Max for an autoreserve a
    manager has since undone, is not given, Max being back in Alpha."""
    db_path = await _db(tmp_path)
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE))
    hook = _hook(_bot(db_path, guild=guild), _real_placement(db_path))

    await hook.change_sanction_roles(LEWIS, LEWIS, revoke=[PRO_ROLE, ALPHA_ROLE], grant=[])
    await hook.change_sanction_roles(MAX, MAX, revoke=[], grant=[RESERVE_ROLE])

    for call in guild.member.remove_roles.await_args_list:
        assert not _role_ids(call) & {PRO_ROLE, ALPHA_ROLE}
    for call in guild.member.add_roles.await_args_list:
        assert RESERVE_ROLE not in _role_ids(call)


async def test_a_sanction_s_roles_are_changed_even_once_attendance_is_off(tmp_path):
    """Owner, check 1: "Change the roles anyway". Lewis's autosack was saved and attendance was
    then turned off before its role change ran. The hook still takes back his roles of Pro and
    Alpha, the one method that acts whatever the switch."""
    db_path = await _db(tmp_path, attendance=False)
    await _sacked(db_path, LEWIS)
    guild = _role_guild((PRO_ROLE, ALPHA_ROLE))
    hook = _hook(_bot(db_path, attendance=False, guild=guild), _real_placement(db_path))

    await hook.change_sanction_roles(LEWIS, LEWIS, revoke=[PRO_ROLE, ALPHA_ROLE], grant=[])

    taken = set().union(*(_role_ids(c) for c in guild.member.remove_roles.await_args_list))
    assert taken == {PRO_ROLE, ALPHA_ROLE}


async def test_check_sanction_refuses_a_full_reserve_block(tmp_path):
    """Max is owed an autoreserve, and the placement service's guard refuses the move to the
    Reserve team of Pro (its block in the lineup picture is full). The check made before the
    sanction is saved raises `StepFailedOnDiscord` carrying the guard's refusal, for the queue
    to stop on, and nothing is moved."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path, autosack=None)
    await _totals(db_path, LATEST_ROUND, {MAX: 12})
    placement = _real_placement(db_path)
    placement.guard_move = AsyncMock(side_effect=ValueError("The Reserve block of Pro is full."))
    hook = _hook(_bot(db_path), placement)
    [owed] = await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID)

    with pytest.raises(StepFailedOnDiscord, match="Reserve block of Pro is full"):
        await hook.check_sanction(LATEST_ROUND, DIVISION_ID, owed)

    placement.guard_move.assert_awaited_once()
    assert await _state(db_path, MAX) == ("ASSIGNED", 1)


async def test_a_sheet_discord_refuses_raises_and_is_not_put_on_the_retry_queue(tmp_path):
    """Defect 4: the sheet's failure stops the queue and is retried there, so it raises
    `StepFailedOnDiscord`; nothing is put on the old retry queue, and the sheet posted earlier is
    still the one recorded."""
    from leaguebot.core.models.change import StepFailedOnDiscord

    db_path = await _db(tmp_path)
    await _totals(db_path, LATEST_ROUND, {LEWIS: 3, MAX: 1})
    channel = _sheet_channel()
    channel.send = AsyncMock(side_effect=http_error(status=403, text="Missing Access"))
    hook = _hook(_bot(db_path, channel=channel), _placement())

    with patch("leaguebot.core.services.retry_service.enqueue", new=AsyncMock()) as enqueue:
        with pytest.raises(StepFailedOnDiscord):
            with in_a_job():
                await hook.post_sheet(LATEST_ROUND, DIVISION_ID, sanctioned=set(), as_text=False)

    channel.send.assert_awaited_once()
    enqueue.assert_not_awaited()
    async with get_connection(db_path) as db:
        row = await (await db.execute(
            "SELECT attendance_message_id FROM attendance_division_config WHERE division_id = ?",
            (DIVISION_ID,),
        )).fetchone()
    assert row["attendance_message_id"] == str(OLD_SHEET)


PARDONS_HOOK_UNBUILT = "#439: the attendance hook cannot say which pardons an amendment's review may offer"


@pytest.mark.xfail(strict=True, reason=PARDONS_HOOK_UNBUILT + "; " + SANCTION_JOBS_UNBUILT)
async def test_every_method_does_nothing_while_attendance_is_off(tmp_path):
    """Each method checks attendance's switch itself; the writers read it on the connection they
    are handed. With attendance off and Lewis over the autosack threshold: no driver is owed a
    sanction, the check of his sanction refuses nothing, saving it writes nothing and gives
    None, nothing is posted or written, and an amendment's review is offered no pardon. The one
    method that acts whatever the switch, the change of a saved sanction's roles, is pinned by
    its own test."""
    db_path = await _db(tmp_path, attendance=False)
    await _totals(db_path, AMENDED_ROUND, {LEWIS: 25, MAX: 0})
    await _totals(db_path, LATEST_ROUND, {LEWIS: 25})
    channel = _sheet_channel()
    placement = _placement()
    hook = _hook(_bot(db_path, attendance=False, channel=channel), placement)
    owed = {"driver_profile_id": LEWIS, "driver_user_id": LEWIS, "sanction": "AUTOSACK",
            "other_divisions": []}

    assert await hook.enabled() is False
    assert await hook.sanction_candidates(LATEST_ROUND, DIVISION_ID) == []
    await hook.check_sanction(LATEST_ROUND, DIVISION_ID, owed)
    await hook.announce_sanction(LATEST_ROUND, DIVISION_ID, owed, as_text=False)
    await hook.post_sheet(LATEST_ROUND, DIVISION_ID, sanctioned={LEWIS}, as_text=False)
    await hook.refresh_lineup(DIVISION_ID, as_text=False)
    async with get_connection(db_path) as db:
        before = db.total_changes
        await hook.record_on(db, AMENDED_ROUND, DIVISION_ID, [], NOW)
        await hook.rewrite_pardons_on(db, AMENDED_ROUND, [], NOW)
        await hook.recalculate_on(db, AMENDED_ROUND, DIVISION_ID)
        assert await hook.apply_sanction_on(db, LATEST_ROUND, DIVISION_ID, owed) is None, (
            "None is how the job knows nothing was applied"
        )
        assert db.total_changes == before
    assert await hook.pardons_to_review([object()]) == []

    assert placement.method_calls == []
    channel.send.assert_not_awaited()


@pytest.mark.xfail(strict=True, reason=PARDONS_HOOK_UNBUILT)
async def test_an_amendment_s_review_is_offered_every_pardon_while_attendance_is_on(tmp_path):
    """Attendance is on. Handed the two pardons an amended round carries, the hook gives both
    back, in their order, for the amendment's review to offer."""
    db_path = await _db(tmp_path, attendance=True)
    hook = _hook(_bot(db_path, attendance=True), _placement())
    pardons = [object(), object()]

    assert await hook.pardons_to_review(pardons) == pardons


@pytest.mark.xfail(strict=True, reason=PARDONS_HOOK_UNBUILT)
async def test_an_amendment_s_review_is_offered_no_pardon_while_attendance_is_off(tmp_path):
    """Attendance is off. Handed the two pardons an amended round carries, the hook gives none
    back, since the appeals stage would write no pardon back while the module is off."""
    db_path = await _db(tmp_path, attendance=False)
    hook = _hook(_bot(db_path, attendance=False), _placement())

    assert await hook.pardons_to_review([object(), object()]) == []


async def test_the_amended_round_s_attendance_is_rebuilt_both_ways_on_the_save_it_is_handed(
    tmp_path,
):
    """FR-028 (was test_the_recompute_rebuilds_the_amended_rounds_attendance): round 3's
    corrected classification has Max and not Lewis, so Lewis is marked absent and Max present,
    and round 7's running total is carried from round 3's. Rolled back, nothing stays: the hook
    commits nothing of its own."""
    db_path = await _db(tmp_path)
    await _totals(db_path, AMENDED_ROUND, {LEWIS: 0, MAX: 0})
    await _totals(db_path, LATEST_ROUND, {LEWIS: 0, MAX: 0})
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE driver_round_attendance SET attended = 0 WHERE driver_profile_id = ? "
            "AND round_id = ?",
            (MAX, AMENDED_ROUND),
        )
        await db.commit()

    async def _rows(db: Any) -> dict[tuple[int, int], Any]:
        cursor = await db.execute(
            "SELECT round_id, driver_profile_id, attended, points_awarded, total_points_after "
            "FROM driver_round_attendance"
        )
        return {(r["round_id"], r["driver_profile_id"]): r for r in await cursor.fetchall()}

    async with get_connection(db_path) as db:
        await _hook(_bot(db_path), _placement()).recalculate_on(db, AMENDED_ROUND, DIVISION_ID)
        rows = await _rows(db)
        await db.rollback()

    assert rows[(AMENDED_ROUND, LEWIS)]["attended"] == 0
    assert rows[(AMENDED_ROUND, MAX)]["attended"] == 1
    lewis_3, lewis_7 = rows[(AMENDED_ROUND, LEWIS)], rows[(LATEST_ROUND, LEWIS)]
    assert lewis_7["total_points_after"] == (
        (lewis_3["total_points_after"] or 0) + (lewis_7["points_awarded"] or 0)
    )
    async with get_connection(db_path) as db:
        assert (await _rows(db))[(AMENDED_ROUND, LEWIS)]["attended"] == 1


async def test_recording_a_round_carries_every_later_round_s_total_on_the_save_it_is_handed(
    tmp_path,
):
    """#238, on the hook (was test_finalize_reviews.py's
    test_an_amended_round_redistributes_every_later_round): round 7, already final, holds a
    running total for Max worked out before round 3 was recorded. Recording round 3 carries
    round 7's total on from round 3's, on the connection handed in; rolled back, round 7 keeps
    the total it held."""
    db_path = await _db(tmp_path)
    await _totals(db_path, AMENDED_ROUND, {MAX: 0})
    await _totals(db_path, LATEST_ROUND, {MAX: 99})

    async def _total(db: Any, round_id: int) -> Any:
        cursor = await db.execute(
            "SELECT points_awarded, total_points_after FROM driver_round_attendance "
            "WHERE round_id = ? AND driver_profile_id = ?",
            (round_id, MAX),
        )
        return await cursor.fetchone()

    async with get_connection(db_path) as db:
        await _hook(_bot(db_path), _placement()).record_on(
            db, AMENDED_ROUND, DIVISION_ID, [], NOW,
        )
        recorded, later = await _total(db, AMENDED_ROUND), await _total(db, LATEST_ROUND)
        await db.rollback()

    assert later["total_points_after"] != 99
    assert later["total_points_after"] == (
        (recorded["total_points_after"] or 0) + (later["points_awarded"] or 0)
    )
    async with get_connection(db_path) as db:
        assert (await _total(db, LATEST_ROUND))["total_points_after"] == 99


async def test_the_round_carries_exactly_the_pardons_handed_in_on_the_save_it_is_handed(
    tmp_path,
):
    """An amendment's pardons: the round's pardons go and the approved set is written whole, a
    pardon staged now stamped with the time handed in. Rolled back, the earlier pardon stands."""
    db_path = await _db(tmp_path)
    await _totals(db_path, AMENDED_ROUND, {LEWIS: 1})
    async with get_connection(db_path) as db:
        attendance_id = (await (await db.execute(
            "SELECT id FROM driver_round_attendance WHERE round_id = ? AND driver_profile_id = ?",
            (AMENDED_ROUND, LEWIS),
        )).fetchone())["id"]
        await db.execute(
            "INSERT INTO attendance_pardons (attendance_id, pardon_type, justification, "
            "granted_by, granted_at) VALUES (?, 'ABSENT', 'earlier', 1, '2026-02-02T00:00:00')",
            (attendance_id,),
        )
        await db.commit()
    pardon = StagedPardon(driver_user_id=LEWIS, driver_profile_id=LEWIS,
                          attendance_id=attendance_id, pardon_type="NO_SHOW",
                          justification="late start", grantor_id=555)

    async def _pardons(db: Any) -> list[tuple[str, int, str]]:
        cursor = await db.execute(
            "SELECT pardon_type, granted_by, granted_at FROM attendance_pardons ORDER BY id"
        )
        return [tuple(r) for r in await cursor.fetchall()]

    async with get_connection(db_path) as db:
        await _hook(_bot(db_path), _placement()).rewrite_pardons_on(
            db, AMENDED_ROUND, [pardon], NOW
        )
        written = await _pardons(db)
        await db.rollback()

    assert written == [("NO_SHOW", 555, NOW.isoformat())]
    async with get_connection(db_path) as db:
        assert [p[0] for p in await _pardons(db)] == ["ABSENT"]


async def test_a_sanction_announcement_shares_the_round_s_verdict_heading(tmp_path):
    """Image spec, verdict banner: one heading per approval over its verdicts and its sanctions.
    Round 3's verdicts went out under the written heading, recorded against the round; Max's
    autoreserve is announced beneath it, with no second heading, even from a fresh hook (as after
    a restart)."""
    from tests.support.review_league import (
        DIVISION_ID as PRO,
        MAX as MAX_ID,
        MAX_PROFILE,
        ROUND_ID,
        VERDICTS_CHANNEL,
        candidate,
        review_league,
        verdict_headings,
    )

    league = await review_league(tmp_path, attendance=True)
    league.channel(VERDICTS_CHANNEL).seed(8950, "**Season 1 Pro Round 3**")
    async with get_connection(league.db_path) as db:
        await db.execute("UPDATE attendance_config SET autoreserve_threshold = 10")
        await db.execute(
            "INSERT INTO verdict_banner_messages (round_id, channel_id, message_id, posted_at) "
            "VALUES (?, ?, '8950', '2026-10-05T11:00:00+00:00')",
            (ROUND_ID, str(VERDICTS_CHANNEL)),
        )
        await db.commit()

    with in_a_job():
        await _hook(league.bot, _placement()).announce_sanction(
            ROUND_ID, PRO, candidate(MAX_PROFILE, MAX_ID), as_text=False
        )

    assert len(league.sent_to(VERDICTS_CHANNEL)) == 1
    assert verdict_headings(league) == []


async def test_the_sheet_s_record_writes_nothing_while_attendance_is_off(tmp_path):
    """The review's sheet job saves the new sheet's id through the hook, on the job's own save.
    Pro's sheet 7401 stands and a new one, 7402, has been posted in its place. With attendance
    off, the record writes nothing and 7401 is still Pro's sheet; switched on, the record makes
    7402 Pro's sheet. Neither commits: the save handed does."""
    db_path = await _db(tmp_path, attendance=False)
    hook = _hook(_bot(db_path, attendance=False), _placement())
    posted = {"new": 7402, "old": str(OLD_SHEET), "channel_id": ATTENDANCE_CHANNEL,
              "takes_slot": True}

    async def _recorded() -> tuple[int, Any]:
        async with get_connection(db_path) as db:
            before = db.total_changes
            await hook.record_sheet_on(db, DIVISION_ID, posted)
            written = db.total_changes - before
            await db.commit()
            row = await (await db.execute(
                "SELECT attendance_message_id FROM attendance_division_config "
                "WHERE division_id = ?", (DIVISION_ID,),
            )).fetchone()
        return written, row["attendance_message_id"]

    assert await _recorded() == (0, str(OLD_SHEET))

    async with get_connection(db_path) as db:
        await db.execute("UPDATE attendance_config SET module_enabled = 1")
        await db.commit()

    written, saved = await _recorded()
    assert written == 1
    assert str(saved) == "7402"
