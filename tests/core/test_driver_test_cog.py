"""The test-mode `/driver` group: every driver command acts on a fake driver as on a real one (#600).

Discord's member field cannot carry a fake driver, so while test mode is on `/driver` is a group of
its own, `DriverTestCog`, whose driver fields take text, a mention or an ID. The text is resolved
to the driver's profile through any account they have held, and the command asks the queue for the
very change the member group asks for, the fake driver named by its test name. Each command keeps
its tier, and the group is kept off the global tree as its cog loads, so the two never clash there.

These run on `season_league`'s season (Placements unless moved on), with test mode on unless said
otherwise: Charles (103) is seated at McLaren in Am; with `extra_drivers`, the fake driver Tester
(104) at McLaren's second seat in Am; Valtteri (106) approved and never placed
(`approved_unplaced`); and Ghost (`GHOST`), a fake driver, Unassigned with no seat.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

PENDING = "#600: no test-mode /driver group takes a driver named as text"

GHOST = 9000000000000000001
GHOST_PROFILE = 90
TESTER = 104


async def _league(tmp_path, *, test_mode: bool = True, ongoing: bool = False):
    """The league of the module docstring: Tester named, Ghost and Valtteri Unassigned."""
    from tests.support.season_league import approved_unplaced, season_league

    league = await season_league(tmp_path, extra_drivers=True, test_mode=test_mode)
    await league.write(
        "UPDATE driver_profiles SET test_display_name = 'Tester' WHERE discord_user_id = ?",
        str(TESTER),
    )
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver, "
        "test_display_name) VALUES (?, ?, 'UNASSIGNED', 1, 'Ghost')",
        GHOST_PROFILE, str(GHOST),
    )
    await approved_unplaced(league)
    if ongoing:
        await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")
        await league.write("UPDATE divisions SET status = 'ACTIVE'")
        await league.write("UPDATE driver_season_assignments SET committed = 1")
    return league


def _asked_by_admin(league, command: str):
    """The interaction of the league admin Admin (77) running `/<command>`."""
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction

    asked = interaction(
        league.bot, command,
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    return asked


async def _run_variant(league, name: str, **params):
    """`/driver <name>` of the test-mode group, run by Admin with *params*: the interaction."""
    from leaguebot.core.cogs.driver_test_cog import DriverTestCog
    from tests.support.undecorate import undecorate

    asked = _asked_by_admin(league, f"driver {name}")
    await undecorate(getattr(DriverTestCog, name))(DriverTestCog(league.bot), asked, **params)
    return asked


#: Each command: whether it runs mid-season, the real driver and fake driver it is given, and the
#: rest of its parameters.
_COMMANDS = {
    "assign": (False, 106, GHOST, {"division": "Am", "team": "McLaren"}),
    "unassign": (False, 103, TESTER, {"division": "Am"}),
    "move": (True, 103, TESTER, {"from_division": "Am", "team": "McLaren"}),
    "release": (True, 103, TESTER, {"division": "Am"}),
    "reject": (False, 106, GHOST, {}),
    "sack": (True, 103, TESTER, {}),
}
_FAKE = {GHOST: (GHOST_PROFILE, "Ghost"), TESTER: (TESTER - 60, "Tester")}


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("name", sorted(_COMMANDS))
async def test_a_fake_driver_named_by_id_is_asked_for_as_a_real_driver_is(
    tmp_path, monkeypatch, name
):
    """Test mode is on. The member group's command is run for a real driver, and the test-mode
    group's for a fake one named by its ID: each asks the queue for the same change with the same
    payload, but for the driver, the fake one named by its test name."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.undecorate import undecorate

    ongoing, real, fake, params = _COMMANDS[name]
    league = await _league(tmp_path, ongoing=ongoing)
    ask = AsyncMock()
    monkeypatch.setattr(league.bot.change_queue, "ask", ask)

    await undecorate(getattr(DriverCog, name))(
        DriverCog(league.bot), _asked_by_admin(league, f"driver {name}"),
        league._member(real), **params,
    )
    await _run_variant(league, name, user=str(fake), **params)

    [(real_kind, real_payload), (fake_kind, fake_payload)] = [
        call.args[:2] for call in ask.await_args_list
    ]
    profile, test_name = _FAKE[fake]
    assert fake_kind == real_kind
    assert fake_payload == {
        **real_payload,
        "driver_profile_id": profile,
        "discord_user_id": str(fake),
        "driver_name": test_name,
    }


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_seated_fake_driver_is_given_a_seat_in_a_second_division(tmp_path):
    """Test mode is on and the season in Placements. Ghost sits at McLaren in Am, and Ferrari in
    Pro has a third seat free. Admin runs the test-mode `/driver assign` of Ghost, by ID, to
    Ferrari in Pro, and the queue runs it: Ghost then holds two placements, McLaren in Am and
    Ferrari in Pro, sits in a seat of each team, and is given no role."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import FERRARI, MCLAREN

    league = await _league(tmp_path)
    await league.write(
        "INSERT INTO team_seats (team_instance_id, seat_number, driver_profile_id) "
        "VALUES (?, 3, ?)", MCLAREN, GHOST_PROFILE,
    )
    await league.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id, committed) SELECT ?, 7, 22, id, 0 FROM team_seats "
        "WHERE team_instance_id = ? AND seat_number = 3", GHOST_PROFILE, MCLAREN,
    )
    await league.write(
        "UPDATE driver_profiles SET current_state = 'ASSIGNED' WHERE id = ?", GHOST_PROFILE
    )
    await league.write("INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 3)", FERRARI)

    await _run_variant(league, "assign", user=str(GHOST), division="Pro", team="Ferrari")
    await run_queue(league.bot)

    placed = await league.rows(
        "SELECT d.name AS division, ti.name AS team FROM driver_season_assignments dsa "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = ? ORDER BY d.tier", GHOST_PROFILE,
    )
    seats = await league.rows(
        "SELECT team_instance_id FROM team_seats WHERE driver_profile_id = ? "
        "ORDER BY team_instance_id", GHOST_PROFILE,
    )
    assert [(r["division"], r["team"]) for r in placed] == [("Pro", "Ferrari"), ("Am", "McLaren")]
    assert [r["team_instance_id"] for r in seats] == [FERRARI, MCLAREN]
    assert league.granted == {}


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_real_driver_named_by_id_is_still_refused_an_assignment(tmp_path):
    """Test mode is on and the season in Placements. Admin runs the test-mode `/driver assign` of
    Valtteri (a real driver, approved and never placed) by his ID to McLaren in Am. He is refused,
    as the member group refuses him under test mode, the log channel takes the refusal as its one
    line, and nothing is asked of the queue."""
    from tests.core.test_driver_assign import _TEST_MODE, _assign_lines, _assign_refusal
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path)
    await league.write("INSERT INTO team_seats (team_instance_id, seat_number) VALUES (32, 3)")

    asked = await _run_variant(league, "assign", user="106", division="Am", team="McLaren")

    assert last_said(asked) == _TEST_MODE
    assert _assign_lines(await log_lines(league)) == [_assign_refusal(_TEST_MODE)]
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("text", ["nobody", "555"])
async def test_text_naming_no_driver_is_refused_naming_the_text(tmp_path, text):
    """Test mode is on. Admin runs the test-mode `/driver assign` naming *text*, which is neither a
    mention nor an ID of any driver, to McLaren in Am. The reply refuses it, naming what was typed;
    the log channel takes the refusal as its one line; nothing is asked of the queue."""
    from tests.core.test_driver_assign import _assign_lines, _assign_refusal
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path)

    asked = await _run_variant(league, "assign", user=text, division="Am", team="McLaren")

    reply = last_said(asked)
    assert reply.startswith("⛔") and text in reply
    assert _assign_lines(await log_lines(league)) == [_assign_refusal(reply)]
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_a_test_mode_command_given_while_test_mode_is_off_is_refused(tmp_path):
    """Test mode is off (a swap having failed, the test-mode group still shows). Admin runs its
    `/driver assign` of Ghost by ID to McLaren in Am. The reply refuses it, saying test mode is off
    and that a restart puts `/driver` right; the log channel takes the refusal as its one line;
    nothing is asked of the queue."""
    from tests.core.test_driver_assign import _assign_lines, _assign_refusal
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path, test_mode=False)

    asked = await _run_variant(league, "assign", user=str(GHOST), division="Am", team="McLaren")

    reply = last_said(asked)
    assert reply.startswith("⛔")
    assert "test mode" in reply.casefold() and "restart" in reply.casefold()
    assert _assign_lines(await log_lines(league)) == [_assign_refusal(reply)]
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize(
    "old_user, new_user", [(str(TESTER), "555"), ("103", str(TESTER))], ids=["old", "new"]
)
async def test_reassign_refuses_a_fake_driver_on_either_side(tmp_path, old_user, new_user):
    """Test mode is on and the season in Placements. Admin runs the test-mode `/driver reassign`
    with the fake driver Tester as the driver's account, or as the account to make current. It is
    refused as a test-mode driver, the log channel takes the refusal as its one line, and nothing
    is asked of the queue."""
    from tests.core.test_driver_reassign import _reassign_lines, _reassign_refusal
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path)

    asked = await _run_variant(league, "reassign", old_user=old_user, new_user=new_user)

    reply = last_said(asked)
    assert "test-mode driver" in reply
    assert _reassign_lines(await log_lines(league)) == [_reassign_refusal(reply)]
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_reassign_makes_a_server_member_with_no_profile_current(tmp_path):
    """Test mode is on and the season in Placements. Admin runs the test-mode `/driver reassign`
    of Charles (103), by mention, to the server member 555, who has no driver profile: the queue
    is asked for one reassignment, of Charles's account 103 onto 555."""
    import json

    from tests.support.change_queue import change_rows

    league = await _league(tmp_path)

    await _run_variant(league, "reassign", old_user="<@103>", new_user="555")

    [row] = await change_rows(league.db_path)
    payload = json.loads(row["payload"])
    assert row["kind"] == "core.driver_reassign"
    assert (payload["old_user_id"], payload["new_user_id"]) == ("103", "555")


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_reassign_refuses_an_id_that_is_not_a_member(tmp_path):
    """Test mode is on and the season in Placements. Admin runs the test-mode `/driver reassign`
    of Charles by ID to 555, who is not a member of the server. The reply refuses it, naming 555,
    the log channel takes the refusal as its one line, and nothing is asked of the queue."""
    from tests.core.test_driver_reassign import _reassign_lines, _reassign_refusal
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _league(tmp_path)
    league.absent.add(555)

    asked = await _run_variant(league, "reassign", old_user="103", new_user="555")

    reply = last_said(asked)
    assert reply.startswith("⛔") and "555" in reply
    assert _reassign_lines(await log_lines(league)) == [_reassign_refusal(reply)]
    assert await change_rows(league.db_path) == []


@pytest.mark.xfail(strict=True, reason=PENDING)
@pytest.mark.parametrize("name", ["assign", "move", "reassign", "reject", "release", "sack", "unassign"])
def test_each_test_mode_command_keeps_its_tier(name):
    """Each command of the test-mode group asks for the tier its member counterpart asks for:
    `/driver sack` a league admin's, the rest a league manager's."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.cogs.driver_test_cog import DriverTestCog
    from leaguebot.core.utils.channel_guard import LEAGUE_ADMIN, LEAGUE_MANAGER, TIER_ATTRIBUTE

    tier = getattr(getattr(DriverTestCog, name).callback, TIER_ATTRIBUTE)

    assert tier == getattr(getattr(DriverCog, name).callback, TIER_ATTRIBUTE)
    assert tier == (LEAGUE_ADMIN if name == "sack" else LEAGUE_MANAGER)


@pytest.mark.xfail(strict=True, reason=PENDING)
async def test_both_groups_load_and_the_global_tree_carries_the_member_group(tmp_path):
    """Both `/driver` cogs are added to one bot, the member group's first: neither clashes with the
    other, and the global command tree carries `/driver` once, the member group's."""
    import discord
    from discord.ext import commands

    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.cogs.driver_test_cog import DriverTestCog

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    member_cog, test_cog = DriverCog(bot), DriverTestCog(bot)

    await bot.add_cog(member_cog)
    await bot.add_cog(test_cog)

    assert [c.name for c in bot.tree.get_commands()].count("driver") == 1
    assert bot.tree.get_command("driver") is member_cog.driver
