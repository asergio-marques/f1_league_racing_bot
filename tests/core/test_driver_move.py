"""`/driver move` — a committed driver moved in one step (issue #220).

Full-time to Reserve, team to team, or between divisions: the old seat freed and the new one
taken together, the roles of the seat left revoked and those of the seat taken granted, and the
lineup of each division touched posted once. It replaces unassigning and assigning again, which
left the driver roleless in between, posted the lineup twice and let the seat be taken.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.services.placement_service import PlacementService

SERVER_ID = 22090
SEASON_ID = 1
PRO, AM = 11, 12
PROFILE_ID = 5
ROLES = {"Alpha": 501, "Bravo": 502, "Reserve": 509}


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "move.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', 'ACTIVE', 1, 'ONGOING')",
            (SEASON_ID,),
        )
        instance_id = 100
        for division_id, name, role in ((PRO, "Pro", 1001), (AM, "Am", 1002)):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
                "VALUES (?, ?, ?, ?, ?, 'ACTIVE')",
                (division_id, SEASON_ID, name, role, division_id - 10),
            )
            for team, reserve in (("Alpha", 0), ("Bravo", 0), ("Reserve", 1)):
                instance_id += 1
                await db.execute(
                    "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
                    "VALUES (?, ?, ?, ?, 2, ?)",
                    (instance_id, division_id, team, team, reserve),
                )
                for seat in (1, 2):
                    await db.execute(
                        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)",
                        (instance_id, seat),
                    )
        for team, role in ROLES.items():
            await db.execute(
                "INSERT INTO team_role_configs (team_name, role_id) VALUES (?, ?)",
                (team, role),
            )
        await db.execute(
            "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
            "VALUES (?, '4242', 'ASSIGNED')",
            (PROFILE_ID,),
        )
        await db.commit()
    return path


async def _seat(db_path, division_id, team, profile_id=PROFILE_ID, *, committed=1):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT ts.id FROM team_seats ts JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE ti.division_id = ? AND ti.name = ? AND ts.driver_profile_id IS NULL "
            "ORDER BY ts.seat_number LIMIT 1",
            (division_id, team),
        )
        seat_id = (await cursor.fetchone())["id"]
        await db.execute(
            "UPDATE team_seats SET driver_profile_id = ? WHERE id = ?", (profile_id, seat_id)
        )
        await db.execute(
            "INSERT INTO driver_season_assignments "
            "(driver_profile_id, season_id, division_id, team_seat_id, committed) "
            "VALUES (?, ?, ?, ?, ?)",
            (profile_id, SEASON_ID, division_id, seat_id, committed),
        )
        await db.commit()


async def _where(db_path, profile_id=PROFILE_ID):
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT dsa.division_id, ti.name FROM driver_season_assignments dsa "
            "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
            "JOIN team_instances ti ON ti.id = ts.team_instance_id "
            "WHERE dsa.driver_profile_id = ? ORDER BY dsa.division_id",
            (profile_id,),
        )
        return [(r["division_id"], r["name"]) for r in await cursor.fetchall()]


def _service(db_path) -> PlacementService:
    service = PlacementService(db_path)
    service._refresh_lineup_post = AsyncMock()
    service._grant_roles = AsyncMock()
    service._revoke_roles = AsyncMock()
    return service


def _guild():
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=MagicMock())
    return guild


async def _move(service, to_division, team, from_division=PRO):
    return await service.move_driver(
        driver_profile_id=PROFILE_ID, season_id=SEASON_ID,
        from_division_id=from_division, to_division_id=to_division, team_name=team,
        acting_user_id=1, acting_user_name="Manager", guild=_guild(), discord_user_id="4242",
    )


async def test_a_full_time_driver_is_moved_to_reserve_in_one_step(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)

    result = await _move(service, PRO, "Reserve")

    assert await _where(db_path) == [(PRO, "Reserve")]
    assert result["from_team"] == "Alpha" and result["to_team"] == "Reserve"
    service._revoke_roles.assert_awaited_once()
    assert service._revoke_roles.await_args.args[1:] == (ROLES["Alpha"],)
    assert service._grant_roles.await_args.args[1:] == (1001, ROLES["Reserve"])
    service._refresh_lineup_post.assert_not_awaited()
    assert result["lineups"] == [PRO]


async def test_a_driver_is_promoted_to_another_division(db_path):
    await _seat(db_path, AM, "Bravo")
    service = _service(db_path)

    result = await _move(service, PRO, "Bravo", from_division=AM)

    assert await _where(db_path) == [(PRO, "Bravo")]
    # The division role swaps; the team role is kept, the driver sitting in Bravo still.
    assert service._revoke_roles.await_args.args[1:] == (1002,)
    assert service._grant_roles.await_args.args[1:] == (1001, ROLES["Bravo"])
    service._refresh_lineup_post.assert_not_awaited()
    assert result["lineups"] == [AM, PRO]


async def test_the_seat_left_is_freed(db_path):
    await _seat(db_path, PRO, "Alpha")

    await _move(_service(db_path), PRO, "Bravo")

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) AS n FROM team_seats ts JOIN team_instances ti "
            "ON ti.id = ts.team_instance_id WHERE ti.name = 'Alpha' AND ts.driver_profile_id IS NOT NULL"
        )
        assert (await cursor.fetchone())["n"] == 0


async def test_an_uncommitted_placement_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha", committed=0)

    with pytest.raises(ValueError, match="not yet confirmed"):
        await _move(_service(db_path), PRO, "Reserve")

    assert await _where(db_path) == [(PRO, "Alpha")]


async def test_a_move_into_a_division_already_held_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha")
    await _seat(db_path, AM, "Alpha")

    with pytest.raises(ValueError, match="already holds a seat"):
        await _move(_service(db_path), AM, "Bravo")


async def test_a_move_into_the_team_already_sat_in_is_refused(db_path):
    await _seat(db_path, PRO, "Alpha")

    with pytest.raises(ValueError, match="already sits in"):
        await _move(_service(db_path), PRO, "Alpha")


async def test_a_move_into_a_full_team_is_refused_and_changes_nothing(db_path):
    await _seat(db_path, PRO, "Alpha")
    async with get_connection(db_path) as db:
        for profile_id in (6, 7):
            await db.execute(
                "INSERT INTO driver_profiles (id, discord_user_id, current_state) "
                "VALUES (?, ?, 'ASSIGNED')",
                (profile_id, str(profile_id)),
            )
        await db.commit()
    await _seat(db_path, PRO, "Bravo", 6)
    await _seat(db_path, PRO, "Bravo", 7)

    with pytest.raises(ValueError, match="no available seats"):
        await _move(_service(db_path), PRO, "Bravo")

    assert await _where(db_path) == [(PRO, "Alpha")]


@pytest.mark.parametrize("stage_name", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_the_command_is_refused_outside_the_ongoing_stages(stage_name):
    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    # Any account names the driver (issue #243); these tests name the current one.
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=SeasonStage(stage_name))
    )
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await undecorate(DriverCog.move)(cog, interaction, MagicMock(), "Pro", "Reserve")

    assert "only while the season is ongoing" in interaction.followup.send.await_args.args[0]
    assert cog.bot.change_queue.method_calls == []


async def test_a_move_for_a_profile_that_does_not_exist_is_refused(db_path):
    service = _service(db_path)

    with pytest.raises(ValueError, match="Driver profile not found"):
        await service.move_driver(
            driver_profile_id=999, season_id=SEASON_ID,
            from_division_id=PRO, to_division_id=PRO, team_name="Reserve",
            acting_user_id=1, acting_user_name="Manager", guild=_guild(),
            discord_user_id="4242",
        )


async def test_a_move_from_a_division_the_driver_does_not_sit_in_is_refused(db_path):
    await _seat(db_path, AM, "Alpha")

    with pytest.raises(ValueError, match="holds no seat in the division they are moved from"):
        await _move(_service(db_path), PRO, "Reserve", from_division=PRO)


async def test_a_member_not_cached_is_fetched_to_swap_their_roles(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    member = MagicMock()
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(return_value=member)

    await service.move_driver(
        driver_profile_id=PROFILE_ID, season_id=SEASON_ID,
        from_division_id=PRO, to_division_id=PRO, team_name="Reserve",
        acting_user_id=1, acting_user_name="Manager", guild=guild, discord_user_id="4242",
    )

    guild.fetch_member.assert_awaited_once_with(4242)
    assert service._grant_roles.await_args.args[0] is member


async def test_a_member_who_left_the_server_is_moved_without_roles(db_path):
    """The move is the league's record; a member Discord cannot find has no roles to swap."""
    import discord

    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=None)
    guild.fetch_member = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Member")
    )

    result = await service.move_driver(
        driver_profile_id=PROFILE_ID, season_id=SEASON_ID,
        from_division_id=PRO, to_division_id=PRO, team_name="Reserve",
        acting_user_id=1, acting_user_name="Manager", guild=guild, discord_user_id="4242",
    )

    assert await _where(db_path) == [(PRO, "Reserve")]
    service._grant_roles.assert_not_awaited()
    service._revoke_roles.assert_not_awaited()
    service._refresh_lineup_post.assert_not_awaited()
    assert result["lineups"] == [PRO]


# ── A team's role repointed: its seated drivers follow ────────────────────────────


def _role_guild(members: dict):
    guild = MagicMock()
    guild.get_member = MagicMock(side_effect=lambda uid: members.get(uid))
    guild.fetch_member = AsyncMock(side_effect=lambda uid: members.get(uid))
    return guild


async def test_every_confirmed_driver_of_the_team_has_the_old_role_swapped_for_the_new(db_path):
    """Issue #220: a team's role is repointed in any stage, and its drivers follow."""
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)
    member = MagicMock()

    reached = await service.swap_team_role(
        "Alpha", ROLES["Alpha"], 777, _role_guild({4242: member})
    )

    assert reached == 1
    service._revoke_roles.assert_awaited_once_with(member, ROLES["Alpha"])
    service._grant_roles.assert_awaited_once_with(member, 777)


async def test_an_unconfirmed_driver_of_the_team_is_left_alone(db_path):
    """They hold no role until placements are confirmed."""
    await _seat(db_path, PRO, "Alpha", committed=0)
    service = _service(db_path)

    assert await service.swap_team_role(
        "Alpha", ROLES["Alpha"], 777, _role_guild({4242: MagicMock()})
    ) == 0
    service._grant_roles.assert_not_awaited()


async def test_a_role_cleared_is_taken_and_nothing_granted(db_path):
    await _seat(db_path, PRO, "Reserve")
    service = _service(db_path)

    await service.swap_team_role(
        "Reserve", ROLES["Reserve"], None, _role_guild({4242: MagicMock()})
    )

    service._revoke_roles.assert_awaited_once()
    service._grant_roles.assert_not_awaited()


async def test_the_same_role_or_no_guild_changes_nothing(db_path):
    await _seat(db_path, PRO, "Alpha")
    service = _service(db_path)

    assert await service.swap_team_role("Alpha", 5, 5, _role_guild({})) == 0
    assert await service.swap_team_role("Alpha", 5, 6, None) == 0


# ── A refused role change: the committing form attendance's sync moves through ──────────────


async def test_a_role_discord_refuses_is_logged_and_the_move_still_saved(db_path, caplog):
    """`move_driver`, the committing form `/attendance sync` moves a driver to Reserve through
    until attendance's own pass (#285), `/driver move` being a change on the queue (#283): the
    driver of Pro's Alpha is moved to its Reserve team, Discord refuses every role change
    (Missing Permissions), each refusal is logged on the host, and the move is saved all the
    same."""
    import discord

    await _seat(db_path, PRO, "Alpha")
    service = PlacementService(db_path)
    service._refresh_lineup_post = AsyncMock(return_value=None)
    roles = {}
    for role_id in (*ROLES.values(), 1001):
        role = MagicMock()
        role.id = role_id
        roles[role_id] = role
    guild = MagicMock()
    guild.get_role = MagicMock(side_effect=roles.get)
    member = MagicMock()
    member.guild = guild
    member.roles = [roles[ROLES["Alpha"]], roles[1001]]
    refused = discord.Forbidden(MagicMock(status=403), "Missing Permissions")
    member.add_roles = AsyncMock(side_effect=refused)
    member.remove_roles = AsyncMock(side_effect=refused)
    guild.get_member = MagicMock(return_value=member)
    guild.fetch_member = AsyncMock(return_value=member)

    with caplog.at_level("WARNING", logger="leaguebot.core.services.placement_service"):
        await service.move_driver(
            driver_profile_id=PROFILE_ID, season_id=SEASON_ID, from_division_id=PRO,
            to_division_id=PRO, team_name="Reserve", acting_user_id=1,
            acting_user_name="Manager", guild=guild, discord_user_id="4242",
        )

    assert member.add_roles.await_count + member.remove_roles.await_count > 0
    assert any("failed to" in r.getMessage() for r in caplog.records)
    assert await _where(db_path) == [(PRO, "Reserve")]


# ---------------------------------------------------------------------------
# /driver move, on the change queue (#283)
# ---------------------------------------------------------------------------
#
# The command asks the change queue for a change of its own on the league's lane, the driver and
# both divisions named at the ask by their ids. The queue's acknowledgement is its reply. One save,
# stamped with the queue's clock, moves the driver (`move_on`) and writes the `DRIVER_MOVE` audit
# entry and the log channel's line; then a step moves the member's roles, which ends on a fault
# rather than stopping the lane (architecture.md, "A job that fails stops its lane"), a role
# Discord refuses being reported in the reply and the line; then the lineup of each division
# touched is posted. These run on `ongoing_league`'s season: Pro (role 801, lineup channel 600)
# holds Lewis (101) and Max (102) at Ferrari (role 811), Am (role 802, lineup channel 700) holds
# Charles (103) at McLaren (role 812, one seat of three taken), every placement confirmed; Lewis
# and Max hold Pro's role and Ferrari's on the server.

#: Why each test below fails until `/driver move` is a change on the queue.
_MOVE_NOT_QUEUED = "#283: /driver move is not yet a change on the queue"

#: The command as the log channel names it in a refusal.
_MOVE = "`/driver move`"


async def _ongoing(tmp_path):
    """`ongoing_league`'s season, Lewis and Max holding Pro's role and Ferrari's on the server,
    and McLaren's second and third seats free."""
    from tests.support.season_league import (
        FERRARI_ROLE, LEWIS, MAX, MCLAREN, PRO_ROLE, ongoing_league,
    )

    league = await ongoing_league(tmp_path)
    for user_id in (LEWIS, MAX):
        league.roles_held[user_id] = {PRO_ROLE, FERRARI_ROLE}
    for seat in (2, 3):
        await league.write(
            "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, ?)", MCLAREN, seat
        )
    return league


async def _ask_move(league, user_id, from_division="Pro", team="McLaren", to_division="Am"):
    """`/driver move` of the driver *user_id* from *from_division* into *team* of *to_division*,
    as the league admin Admin (77) runs it: the interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver move",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.move)(
        DriverCog(league.bot), asked, league._member(user_id), from_division, team, to_division
    )
    return asked


async def _seated(league):
    """Where each driver sits: (division, team, whether confirmed) by Discord id."""
    rows = await league.rows(
        "SELECT dp.discord_user_id AS user_id, d.name AS division, ti.name AS team, "
        "dsa.committed FROM driver_season_assignments dsa "
        "JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id"
    )
    return {int(r["user_id"]): (r["division"], r["team"], bool(r["committed"])) for r in rows}


async def _lewis_seats(league):
    """Where Lewis sits: (division, team, whether confirmed), in division order."""
    rows = await league.rows(
        "SELECT d.name AS division, ti.name AS team, dsa.committed "
        "FROM driver_season_assignments dsa "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = 41 ORDER BY d.tier"
    )
    return [(r["division"], r["team"], bool(r["committed"])) for r in rows]


#: Where every driver sits before anything is moved.
_STANDING = {101: ("Pro", "Ferrari", True), 102: ("Pro", "Ferrari", True),
             103: ("Am", "McLaren", True)}

_ADMIN = "Admin (`<@77>`)"


def _moving(name: str, team: str = "McLaren F1 Team", division: str = "Am") -> str:
    """How the queue words `/driver move` of *name* into *team* of *division*."""
    return f"Moving **{name}** to **{team}** in **{division}**"


def _moved(name: str) -> str:
    """`/driver move`'s reply once saved, word for word, as today."""
    return (
        f"✅ Moved **{name}** from **Scuderia Ferrari** in **Pro** to **McLaren F1 Team** in "
        "**Am**."
    )


def _move_line(name: str, user_id: int) -> str:
    """`/driver move`'s line in the log channel, word for word: today's, the member asking named
    as every change on the queue names them."""
    return (
        f"{_ADMIN} | /driver move | Success\n"
        f"  user: {name} (`<@{user_id}>`)\n"
        "  from: Scuderia Ferrari, Pro\n"
        "  to: McLaren F1 Team, Am"
    )


def _as_logged(text: str) -> str:
    """*text* as the log channel writes it: every mention in code, so it names nobody."""
    import re

    return re.sub(r"(<@&?\d+>)", r"`\1`", text)


def _refusal(reply: str) -> str:
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ {_MOVE} refused for {_ADMIN} — {reply_reason(reply)}"


def _move_lines(lines):
    return [line for line in lines if "/driver move" in line]


@pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED)
async def test_a_driver_move_and_its_audit_entry_and_line_are_one_save_then_roles_and_lineups(
    tmp_path,
):
    """The core specification's placements and its record of what changed. The season is
    ongoing; Lewis sits at Ferrari in Pro, holding Pro's role and Ferrari's. The league admin
    Admin (77) runs `/driver move` of Lewis from Pro into McLaren in Am. They are answered at once
    with the queue's acknowledgement, "Moving **Lewis** to **McLaren F1 Team** in **Am**", naming
    its first job, and nothing is moved, no role touched and no lineup posted until the queue runs
    the change, of kind `core.driver_move`. Once it has, Lewis sits at McLaren in Am, still
    confirmed, and every other driver where they sat; one audit entry, `DRIVER_MOVE`, by Admin, on
    Am, stamped with the queue's clock, records Pro and Ferrari before and Am and McLaren after;
    the log channel holds today's success line once; Lewis has Pro's role and Ferrari's taken and
    Am's and McLaren's given; the lineups of Pro and of Am are each posted once in their lineup
    channel; and the acknowledgement is updated to today's reply, word for word."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import (
        AM, AM_ROLE, DIVISIONS, FERRARI_ROLE, LEWIS, MCLAREN_ROLE, PRO, PRO_ROLE,
    )
    from tests.support.settings_league import audit_rows, last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _ongoing(tmp_path)
    asked = await _ask_move(league, LEWIS)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_moving('Lewis'))} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.driver_move"]
    assert await _seated(league) == _STANDING
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    await run_queue(league.bot)

    assert await _seated(league) == {**_STANDING, LEWIS: ("Am", "McLaren", True)}
    [entry] = await audit_rows(league.db_path)
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DRIVER_MOVE", 77, AM
    )
    assert entry["old_value"] == {"division": "Pro", "team": "Ferrari"}
    assert entry["new_value"] == {"division": "Am", "team": "McLaren"}
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _move_lines(await log_lines(league)) == [_move_line("Lewis", LEWIS)]
    assert sorted(league.revoked[LEWIS]) == [PRO_ROLE, FERRARI_ROLE]
    assert sorted(league.granted[LEWIS]) == [AM_ROLE, MCLAREN_ROLE]
    assert len(league.texts(DIVISIONS[PRO][3].lineup)) == 1
    assert len(league.texts(DIVISIONS[AM][3].lineup)) == 1
    assert last_said(asked) == _moved("Lewis")


@pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED)
async def test_a_refused_role_move_is_reported_and_the_queue_goes_on(tmp_path):
    """architecture.md, "A job that fails stops its lane": a member's roles moved after their
    driver's change end on a fault rather than stopping the lane. The season is ongoing; Lewis and
    Max sit at Ferrari in Pro. The league admin runs `/driver move` of Lewis into McLaren in Am,
    then of Max likewise, before the queue runs; Discord refuses to give Lewis any role (403
    Missing Permissions). The queue runs both. Lewis's move stands, saved before his roles were
    asked of Discord: he sits at McLaren in Am, his Pro and Ferrari roles are taken, and nothing
    is given him. His reply is today's, then "⚠️ Done, but on Discord:" and a bullet naming the
    roles refused, Am's and McLaren's, then the kind of fault in plain words, never Discord's own:
    "the roles <@&802> and <@&812> could not be given to <@101>: Discord refused or failed a
    request from the bot"; his success line carries a "not done:" line in the same words, its
    mentions written in code as the log channel writes every mention. The lane is not stopped,
    and Max's move, asked after, is made in full: he sits at McLaren in Am, given Am's and
    McLaren's roles, his reply today's alone."""
    import discord

    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.season_league import (
        AM_ROLE, FERRARI_ROLE, LEWIS, MAX, MCLAREN_ROLE, PRO_ROLE,
    )
    from tests.support.settings_league import last_said, log_lines

    league = await _ongoing(tmp_path)
    refused = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    league.grant_fails[LEWIS] = refused
    lewis = await _ask_move(league, LEWIS)
    max_ = await _ask_move(league, MAX)
    await run_queue(league.bot)

    problem = (
        f"the roles <@&{AM_ROLE}> and <@&{MCLAREN_ROLE}> could not be given to <@{LEWIS}>: "
        "Discord refused or failed a request from the bot"
    )
    assert await _seated(league) == {
        **_STANDING, LEWIS: ("Am", "McLaren", True), MAX: ("Am", "McLaren", True),
    }
    assert sorted(league.revoked[LEWIS]) == [PRO_ROLE, FERRARI_ROLE]
    assert LEWIS not in league.granted
    assert last_said(lewis) == f"{_moved('Lewis')}\n⚠️ Done, but on Discord:\n• {problem}"
    assert _move_lines(await log_lines(league)) == [
        f"{_move_line('Lewis', LEWIS)}\n  not done: {_as_logged(problem)}", _move_line("Max", MAX),
    ]
    assert await stopped_job(league.db_path) is None
    assert sorted(league.granted[MAX]) == [AM_ROLE, MCLAREN_ROLE]
    assert last_said(max_) == _moved("Max")


@pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED)
async def test_a_failed_driver_move_moves_nothing(tmp_path):
    """The season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver move`
    of Lewis into McLaren in Am, and as the queue runs it the database fails writing the audit
    entry. Nothing of the move is saved, the move, its audit entry and its line being one save:
    every driver sits where they sat, no audit entry is written, the log channel holds no success
    line, no role is touched on Discord and no lineup posted. The league's lane stops at the job.
    Once a league admin discards the stopped job, the reply says "Nothing was moved: **Lewis**
    stays where they were. Run `/driver move` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, LEWIS, PRO
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _ongoing(tmp_path)
    asked = await _ask_move(league, LEWIS)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _seated(league) == _STANDING
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was moved: **Lewis** stays where they were. Run `/driver move` again."
    )
    assert await stopped_job(league.db_path) is None


async def _end_the_races(league):
    await league.write("UPDATE seasons SET stage = 'PENDING_COMPLETION'")


async def _unconfirm_lewis(league):
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id = 41"
    )


async def _seat_lewis_in_am(league):
    """Lewis takes McLaren's third seat in Am as well, as a release-and-move would leave him."""
    await league.write(
        "UPDATE team_seats SET driver_profile_id = 41 WHERE team_instance_id = 32 "
        "AND seat_number = 3"
    )
    await league.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id, committed) SELECT 41, 7, 22, id, 1 FROM team_seats "
        "WHERE team_instance_id = 32 AND seat_number = 3"
    )


@pytest.mark.parametrize(
    "meanwhile, reply, lewis_after",
    [
        pytest.param(
            _end_the_races, "⛔ `/driver move` is available only while the season is ongoing.",
            [("Pro", "Ferrari", True)], id="the-races-ended",
            marks=pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED),
        ),
        pytest.param(
            _unconfirm_lewis,
            "⛔ That placement is not yet confirmed. Change it with `/driver unassign` and "
            "`/driver assign`.",
            [("Pro", "Ferrari", False)], id="the-placement-unconfirmed",
            marks=pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED),
        ),
        pytest.param(
            _seat_lewis_in_am,
            "⛔ Driver already holds a seat in the division they would move into.",
            [("Pro", "Ferrari", True), ("Am", "McLaren", True)],
            id="a-seat-taken-in-the-division-moved-into",
            marks=pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED),
        ),
    ],
)
async def test_a_driver_move_is_judged_again_as_it_starts(tmp_path, meanwhile, reply, lewis_after):
    """The season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver move`
    of Lewis into McLaren in Am and is acknowledged; before the queue runs it, the season's races
    end (Pending completion), Lewis's placement in Pro is made unconfirmed, or Lewis takes a seat
    in Am as well. The queue runs. The move is refused as it starts, in the words it would meet
    at the ask, its acknowledgement updated to them. Lewis's seats, in division order, stand as
    the meanwhile left them, no audit entry is written, the log channel holds the refusal alone, no role is
    touched, no lineup posted, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import DIVISIONS, LEWIS, PRO
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _ongoing(tmp_path)
    refused = await _ask_move(league, LEWIS)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _lewis_seats(league) == lewis_after
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [_refusal(reply)]
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "setup, mover, team, to_division, reply",
    [
        pytest.param(
            _unconfirm_lewis, 101, "McLaren", "Am",
            "⛔ That placement is not yet confirmed. Change it with `/driver unassign` and "
            "`/driver assign`.",
            id="an-unconfirmed-placement",
        ),
        pytest.param(
            _seat_lewis_in_am, 101, "McLaren", "Am",
            "⛔ Driver already holds a seat in the division they would move into.",
            id="a-seat-held-in-the-division-moved-into",
        ),
        pytest.param(
            None, 101, "Ferrari", None, "⛔ Driver already sits in **Ferrari** in this division.",
            id="the-team-already-sat-in",
        ),
        pytest.param(
            None, 103, "Ferrari", "Pro", "⛔ **Ferrari** in this division has no available seats.",
            id="a-full-team",
        ),
    ],
)
async def test_a_driver_move_is_refused_at_the_ask(
    tmp_path, setup, mover, team, to_division, reply
):
    """The core specification's placements. The season is ongoing; Lewis and Max sit at Ferrari
    in Pro, its two seats, and Charles at McLaren in Am. The league admin runs `/driver move` that
    cannot be done: of Lewis from Pro into McLaren in Am, his Pro placement unconfirmed, or while
    he holds a seat in Am as well; of Lewis from Pro into Ferrari, where he sits; or of Charles
    from Am into Ferrari in Pro, which is full. They are refused at once, in today's words, and
    the log channel takes the refusal as its one line. Nothing is asked of the queue, every
    driver sits where they sat, no audit entry is written and no role touched. Takes the place of
    `test_driver_cog_refusals.py`'s "move refused by the service" case, which mocked the
    service."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _ongoing(tmp_path)
    if setup is not None:
        await setup(league)
    before = await _seated(league)
    from_division = "Am" if mover == 103 else "Pro"
    asked = await _ask_move(league, mover, from_division, team, to_division)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_refusal(reply)]
    assert await change_rows(league.db_path) == []
    assert await _seated(league) == before
    assert await audit_rows(league.db_path) == []
    assert league.revoked == {} and league.granted == {}


@pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED)
async def test_a_driver_move_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver move` of Lewis
    into McLaren in Am and is acknowledged; before the queue runs it, they run it again. The
    second is refused at once, in the queue's words, and the log channel takes that refusal as a
    line; only the one change is queued. The queue runs it: Lewis sits at McLaren in Am, with one
    audit entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import LEWIS
    from tests.support.settings_league import audit_rows, log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _ongoing(tmp_path)
    first, repeat = await _ask_move(league, LEWIS), await _ask_move(league, LEWIS)

    assert said(first)[0].startswith(acknowledged(_moving("Lewis")))
    assert said(repeat) == [repeated(_moving("Lewis"))]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert (await _seated(league))[LEWIS] == ("Am", "McLaren", True)
    assert len(await audit_rows(league.db_path)) == 1
    assert _move_lines(await log_lines(league)) == [
        _refusal(repeated(_moving("Lewis"))), _move_line("Lewis", LEWIS),
    ]


@pytest.mark.xfail(strict=True, reason=_MOVE_NOT_QUEUED)
async def test_a_driver_who_left_the_server_before_the_move_runs_is_moved_without_roles(
    tmp_path,
):
    """The move is the league's record; a member Discord cannot find has no roles to move. The
    season is ongoing; Lewis sits at Ferrari in Pro. The league admin runs `/driver move` of Lewis
    into McLaren in Am, and Lewis leaves the server before the queue runs it. The move is saved:
    Lewis sits at McLaren in Am, still confirmed, and the log channel holds today's success line
    alone. No role is taken from him or given him, and nothing is reported of his roles: his
    reply is today's, word for word. The lineups of Pro and of Am are each posted once, and the
    lane is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import AM, DIVISIONS, LEWIS, PRO
    from tests.support.settings_league import last_said, log_lines

    league = await _ongoing(tmp_path)
    asked = await _ask_move(league, LEWIS)
    league.absent.add(LEWIS)
    await run_queue(league.bot)

    assert await _seated(league) == {**_STANDING, LEWIS: ("Am", "McLaren", True)}
    assert _move_lines(await log_lines(league)) == [_move_line("Lewis", LEWIS)]
    assert LEWIS not in league.revoked and LEWIS not in league.granted
    assert last_said(asked) == _moved("Lewis")
    assert len(league.texts(DIVISIONS[PRO][3].lineup)) == 1
    assert len(league.texts(DIVISIONS[AM][3].lineup)) == 1
    assert await stopped_job(league.db_path) is None
