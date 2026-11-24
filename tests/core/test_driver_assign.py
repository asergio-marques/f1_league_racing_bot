"""`/driver assign` on the change queue (#283).

The command is a change on the league's lane: the driver, the division and the team named at the
ask by their ids, the queue's acknowledgement its reply, and one save (`assign_on`) writing the
placement, its `DRIVER_ASSIGN` audit entry and its line with the queue's clock. The placement is
uncommitted, so no role is given and no lineup posted. The guards that read templates and the
server's test-mode flag run in the change's check, at the ask and again as it starts.

These run on `season_league`'s season, in Placements: Charles alone at McLaren in Am, its second
seat free, and Valtteri (106) approved and never placed (`approved_unplaced`). The signup module is
off unless a test turns it on; Oscar (108, `NEWCOMER`) is a member of the server holding no profile.
"""
from __future__ import annotations

import pytest

from tests.core.test_driver_move import _ADMIN


_NOT_PLACING = (
    "⛔ `/driver assign` is available only while the season is in placements, or mid-season "
    "while the drivers of a closed signup window are being placed."
)
_NO_SEAT = "⛔ **McLaren** in this division has no available seats."
_TEST_MODE = (
    "⛔ Test mode is active — a real driver cannot be assigned to a team. Turn it off with "
    "`/test-mode toggle` first."
)


async def _placing(tmp_path):
    """`season_league`'s season in Placements, McLaren's second seat in Am free and Valtteri
    approved and never placed."""
    from tests.support.season_league import MCLAREN, approved_unplaced, season_league

    league = await season_league(tmp_path)
    await league.write(
        "INSERT INTO team_seats (team_instance_id, seat_number) VALUES (?, 2)", MCLAREN
    )
    await approved_unplaced(league)
    return league


async def _ask_assign(league, user_id, division="Am", team="McLaren"):
    """`/driver assign` of the driver *user_id* to *team* of *division*, as the league admin
    Admin (77) runs it: the interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver assign",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.assign)(
        DriverCog(league.bot), asked, league._member(user_id), division, team
    )
    return asked


async def _valtteri(league):
    """Where Valtteri sits, (division, team, whether confirmed), and his state."""
    rows = await league.rows(
        "SELECT d.name AS division, ti.name AS team, dsa.committed "
        "FROM driver_season_assignments dsa "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = 46"
    )
    [state] = await league.rows("SELECT current_state FROM driver_profiles WHERE id = 46")
    return (
        [(r["division"], r["team"], bool(r["committed"])) for r in rows], state["current_state"]
    )


_UNPLACED = ([], "UNASSIGNED")


def _assigning(name: str = "Valtteri") -> str:
    """How the queue words `/driver assign` of *name* to McLaren in Am."""
    return f"Assigning **{name}** to **McLaren F1 Team** in **Am**"


def _assign_line(name: str = "Valtteri", user_id: int = 106) -> str:
    """`/driver assign`'s line in the log channel, word for word: today's, the member asking named
    as every change on the queue names them."""
    return (
        f"{_ADMIN} | /driver assign | Success\n"
        f"  user: {name} (`<@{user_id}>`)\n"
        f"  team: McLaren F1 Team\n"
        f"  division: Am"
    )


def _assign_refusal(reply: str) -> str:
    from leaguebot.framework.utils.log_lines import reply_reason

    return f"⛔ `/driver assign` refused for {_ADMIN} — {reply_reason(reply)}"


def _logged_refusal(reply: str) -> str:
    """`_assign_refusal`, each mention in the reason set in code, as the log channel sets it so
    that nobody is pinged."""
    import re

    from leaguebot.framework.utils.log_lines import reply_reason

    reason = re.sub(r"(<@\d+>)", r"`\1`", reply_reason(reply))
    return f"⛔ `/driver assign` refused for {_ADMIN} — {reason}"


def _assign_lines(lines):
    return [line for line in lines if "/driver assign" in line]


async def _approve_placements(league):
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


async def _fill_mclaren(league):
    """Max takes McLaren's free second seat in Am, as another assignment would leave it."""
    await league.write(
        "UPDATE team_seats SET driver_profile_id = 42 WHERE team_instance_id = 32 "
        "AND seat_number = 2"
    )
    await league.write(
        "INSERT INTO driver_season_assignments (driver_profile_id, season_id, division_id, "
        "team_seat_id, committed) SELECT 42, 7, 22, id, 0 FROM team_seats "
        "WHERE team_instance_id = 32 AND seat_number = 2"
    )


async def _turn_test_mode_on(league):
    await league.set_test_mode(True)


_STANDINGS_FULL = (
    "⛔ The driver standings template draws too few rows. The driver was **not** assigned. "
    "Enlarge the driver standings template."
)


async def _fill_the_standings(league):
    """The image module's drawing answers that the driver standings template cannot draw
    another driver, as a league's template one row short of the division would."""
    from unittest.mock import AsyncMock

    from leaguebot.core.services.modules.image_drawing import CapacityProblem

    league.bot.image_drawing.standings_capacity_problem = AsyncMock(return_value=CapacityProblem(
        problem="The driver standings template draws too few rows",
        remedy="Enlarge the driver standings template.",
    ))


async def test_a_driver_assignment_and_its_audit_entry_and_line_are_one_save(tmp_path):
    """The core specification's placement and its record of what changed. The season is in
    Placements; Valtteri was approved and never placed, and McLaren's second seat in Am is free.
    The league admin Admin (77) runs `/driver assign` of Valtteri to McLaren in Am. They are
    answered at once with the queue's acknowledgement, "Assigning **Valtteri** to **McLaren F1
    Team** in **Am**", naming its first job, and nothing is placed until the queue runs the
    change, of kind `core.driver_assign`. Once it has, Valtteri sits at McLaren in Am, not
    confirmed, and is Assigned; one audit entry, `DRIVER_ASSIGN`, by Admin, on Am, stamped with
    the queue's clock, records him Unassigned before and at McLaren in Am, Assigned, after; the
    log channel holds today's success line once; no role is given on Discord and no lineup is
    posted, the placement being unconfirmed; and the acknowledgement is updated to today's reply,
    "✅ Assigned **Valtteri** to **McLaren F1 Team** in **Am**."."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import AM, DIVISIONS, PRO
    from tests.support.settings_league import audit_rows, last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _placing(tmp_path)
    asked = await _ask_assign(league, 106)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_assigning())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.driver_assign"]
    assert await _valtteri(league) == _UNPLACED
    await run_queue(league.bot)

    assert await _valtteri(league) == ([("Am", "McLaren", False)], "ASSIGNED")
    [entry] = [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_ASSIGN"
    ]
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DRIVER_ASSIGN", 77, AM
    )
    assert entry["old_value"] == {"state": "UNASSIGNED"}
    assert {key: entry["new_value"][key] for key in ("team", "division", "new_state")} == {
        "team": "McLaren", "division": "Am", "new_state": "ASSIGNED",
    }
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _assign_lines(await log_lines(league)) == [_assign_line()]
    assert league.granted == {} and league.revoked == {}
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert last_said(asked) == "✅ Assigned **Valtteri** to **McLaren F1 Team** in **Am**."


async def test_a_failed_driver_assignment_places_nothing(tmp_path):
    """The season is in Placements; Valtteri was approved and never placed. The league admin runs
    `/driver assign` of Valtteri to McLaren in Am, and as the queue runs it the database fails
    writing the audit entry. Nothing of the placement is saved, the placement, its audit entry and
    its line being one save: Valtteri sits nowhere and is still Unassigned, McLaren's second seat
    is free, no audit entry is written and the log channel holds no success line. The league's
    lane stops at the job. Once a league admin discards the stopped job, the reply says "Nothing
    was assigned: **Valtteri** is not placed in **Am**. Run `/driver assign` again."; and the
    lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _placing(tmp_path)
    asked = await _ask_assign(league, 106)
    with fault_in("leaguebot.framework.services.queue.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _valtteri(league) == _UNPLACED
    assert await league.rows(
        "SELECT 1 FROM team_seats WHERE team_instance_id = 32 AND seat_number = 2 "
        "AND driver_profile_id IS NULL"
    )
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was assigned: **Valtteri** is not placed in **Am**. Run `/driver assign` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply",
    [
        pytest.param(_approve_placements, _NOT_PLACING, id="the-placements-approved"),
        pytest.param(_fill_mclaren, _NO_SEAT, id="the-last-seat-taken"),
        pytest.param(_turn_test_mode_on, _TEST_MODE, id="test-mode-turned-on"),
        pytest.param(_fill_the_standings, _STANDINGS_FULL, id="the-standings-template-full"),
    ],
)
async def test_a_driver_assignment_is_judged_again_as_it_starts(tmp_path, meanwhile, reply):
    """The season is in Placements; Valtteri was approved and never placed, and McLaren's second
    seat in Am is free. The league admin runs `/driver assign` of Valtteri to McLaren in Am and is
    acknowledged; before the queue runs it, the season's placements are approved (Ongoing), Max is
    placed in McLaren's last free seat, test mode is turned on, or the image module comes to
    answer that the driver standings template cannot draw another driver. The queue runs. The
    assignment is refused as it starts, in the words it would meet at the ask, its acknowledgement
    updated to them: "`/driver assign` is available only while the season is in placements, or
    mid-season while the drivers of a closed signup window are being placed.", "**McLaren** in
    this division has no available seats.", "Test mode is active — a real driver cannot be
    assigned to a team. Turn it off with `/test-mode toggle` first." or the image module's own
    words for the template's problem and its remedy, between them "The driver was **not**
    assigned.". Valtteri sits nowhere and is still Unassigned,
    no audit entry is written, the log channel holds the refusal alone, and the queue is not
    stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _placing(tmp_path)
    refused = await _ask_assign(league, 106)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _valtteri(league) == _UNPLACED
    assert [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_ASSIGN"
    ] == []
    assert _assign_lines(await log_lines(league)) == [_assign_refusal(reply)]
    assert await stopped_job(league.db_path) is None


_CONFIRMED_ELSEWHERE = (
    "⛔ Driver already holds a confirmed placement this season. Move them with `/driver move`, or "
    "release them from a division with `/driver release`."
)


async def _placing_mclaren_full(tmp_path):
    """`_placing`'s season, Max then placed in McLaren's free second seat in Am."""
    league = await _placing(tmp_path)
    await _fill_mclaren(league)
    return league


async def _placing_standings_full(tmp_path):
    """`_placing`'s season, the image module answering that the driver standings template cannot
    draw another driver."""
    league = await _placing(tmp_path)
    await _fill_the_standings(league)
    return league


async def _placing_mid_season(tmp_path):
    """`placing_league`'s season, placing mid-season: Lewis confirmed at Ferrari in Pro."""
    from tests.support.season_league import placing_league

    return await placing_league(tmp_path)


_NOT_SIGNED_UP_REFUSED = (
    "⛔ Driver must be Unassigned or Assigned to be placed (current state: Not Signed Up)."
)
_NO_PROFILE = "⛓ No driver profile found for **Oscar**."
#: Nico's current account, gone from the server, and a past account of his still in it.
_NICO, _NICO_PAST = 109, 110
_PAST_ACCOUNT_GONE = (
    f"⛔ <@{_NICO_PAST}> is a past account of a driver whose current account, <@{_NICO}>, is not "
    "in the server."
)


async def _placing_signup_on(tmp_path):
    """`_placing`'s season with the signup module on, Oscar holding no profile."""
    league = await _placing(tmp_path)
    await league.switch("signup", True)
    return league


async def _placing_signup_on_not_signed_up(tmp_path):
    """`_placing_signup_on`'s season, Oscar holding a profile and Not Signed Up."""
    from tests.support.season_league import NEWCOMER

    league = await _placing_signup_on(tmp_path)
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
        "VALUES (48, ?, 'NOT_SIGNED_UP', 0)", str(NEWCOMER),
    )
    return league


async def _placing_test_mode(tmp_path):
    """`_placing`'s season, the signup module off and test mode on, Oscar holding no profile."""
    league = await _placing(tmp_path)
    await league.set_test_mode(True)
    return league


async def _placing_past_account_gone(tmp_path):
    """`_placing`'s season, the signup module off: Nico (109), Not Signed Up, has left the server,
    and his past account (110) is still in it."""
    league = await _placing(tmp_path)
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
        "VALUES (49, ?, 'NOT_SIGNED_UP', 0)", str(_NICO),
    )
    await league.write(
        "INSERT INTO driver_accounts (driver_profile_id, discord_user_id) VALUES (49, ?)",
        str(_NICO_PAST),
    )
    league.absent.add(_NICO)
    return league


@pytest.mark.parametrize(
    "build, user_id, reply",
    [
        pytest.param(_placing_mclaren_full, 106, _NO_SEAT, id="no-free-seat"),
        pytest.param(_placing_mid_season, 101, _CONFIRMED_ELSEWHERE, id="a-confirmed-driver"),
        pytest.param(
            _placing_standings_full, 106, _STANDINGS_FULL, id="the-standings-template-full"
        ),
        pytest.param(
            _placing_signup_on_not_signed_up, 108, _NOT_SIGNED_UP_REFUSED,
            id="signup-on-not-signed-up",
        ),
        pytest.param(_placing_signup_on, 108, _NO_PROFILE, id="signup-on-no-profile"),
        pytest.param(_placing_test_mode, 108, _TEST_MODE, id="signup-off-test-mode-no-profile"),
        pytest.param(
            _placing_past_account_gone, _NICO_PAST, _PAST_ACCOUNT_GONE,
            id="signup-off-past-account-gone",
        ),
    ],
)
async def test_a_driver_assignment_is_refused_at_the_ask(tmp_path, build, user_id, reply):
    """The core specification's placement. Either the season is in Placements and McLaren's two
    seats in Am are both taken, and the league admin runs `/driver assign` of Valtteri to McLaren
    in Am; or the season is placing drivers mid-season, Lewis holds a confirmed seat at Ferrari in
    Pro, and the league admin runs `/driver assign` of Lewis to McLaren in Am; or the season is in
    Placements with McLaren's second seat free, the image module answers that the driver standings
    template cannot draw another driver, and the league admin runs `/driver assign` of Valtteri to
    McLaren in Am (#140 as a league met it). They are refused at once, in today's words:
    "**McLaren** in this division has no available seats.", "Driver already holds a confirmed
    placement this season. Move them with `/driver move`, or release them from a division with
    `/driver release`." or the image module's own words for the template's problem and its
    remedy, between them "The driver was **not** assigned.", and the log channel takes the refusal as its
    one line. Nothing is asked of the queue, nobody is placed, no audit entry is written, no
    profile is created and no role touched.

    The rest are #624's, the season in Placements with McLaren's second seat free. With the signup
    module on, Oscar, Not Signed Up, is refused as he always was, his state named as the core
    specification names it: "Driver must be Unassigned or Assigned to be placed (current state:
    Not Signed Up)."; and Oscar holding no profile is refused, "No driver profile found for
    **Oscar**.". With the signup module off and test mode on, Oscar, a real member with no profile,
    is refused as any real driver is under test mode. With the signup module off, Nico's past
    account (110) names Nico, whose current account (109) has left the server, and the assignment
    is refused as today: "<@110> is a past account of a driver whose current account, <@109>, is
    not in the server.", no profile created for the past account. Takes the place of `test_driver_cog_refusals.py`'s "assign refused by the
    service" case and `test_driver_cog.py`'s `test_a_refused_assignment_is_reported_not_raised`,
    which mocked the service, and of the mid-season case of its
    `test_an_assignment_is_made_uncommitted`."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await build(tmp_path)
    placements = await league.rows("SELECT * FROM driver_season_assignments ORDER BY id")
    profiles = await league.rows("SELECT * FROM driver_profiles ORDER BY id")
    asked = await _ask_assign(league, user_id)

    assert last_said(asked) == reply
    assert await league.rows("SELECT * FROM driver_profiles ORDER BY id") == profiles
    assert _assign_lines(await log_lines(league)) == [_logged_refusal(reply)]
    assert await change_rows(league.db_path) == []
    assert await league.rows("SELECT * FROM driver_season_assignments ORDER BY id") == placements
    assert await audit_rows(league.db_path) == []
    assert league.granted == {} and league.revoked == {}


async def test_a_driver_assignment_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements; Valtteri was approved and never placed. The league admin runs
    `/driver assign` of Valtteri to McLaren in Am and is acknowledged; before the queue runs it,
    they run it again. The second is refused at once, in the queue's words, and the log channel
    takes that refusal as a line; only the one change is queued. The queue runs it: Valtteri sits
    at McLaren in Am, with one audit entry, and the log channel holds the refusal and then the
    success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import audit_rows, log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _placing(tmp_path)
    first, repeat = await _ask_assign(league, 106), await _ask_assign(league, 106)

    assert said(first)[0].startswith(acknowledged(_assigning()))
    assert said(repeat) == [repeated(_assigning())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _valtteri(league) == ([("Am", "McLaren", False)], "ASSIGNED")
    assert [row["change_type"] for row in await audit_rows(league.db_path)] == ["DRIVER_ASSIGN"]
    assert _assign_lines(await log_lines(league)) == [
        _assign_refusal(repeated(_assigning())), _assign_line(),
    ]


async def _signup_off_newcomer(tmp_path):
    """`_placing`'s season, the signup module off, Oscar a member holding no profile."""
    return await _placing(tmp_path)


async def _signup_off_not_signed_up(tmp_path):
    """`_placing`'s season, the signup module off, Oscar a real driver Not Signed Up."""
    from tests.support.season_league import NEWCOMER

    league = await _placing(tmp_path)
    await league.write(
        "INSERT INTO driver_profiles (id, discord_user_id, current_state, is_test_driver) "
        "VALUES (48, ?, 'NOT_SIGNED_UP', 0)", str(NEWCOMER),
    )
    return league


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_signup_off_newcomer, id="no-profile"),
        pytest.param(_signup_off_not_signed_up, id="not-signed-up"),
    ],
)
async def test_with_signup_off_a_member_not_signed_up_is_placed(tmp_path, build):
    """#624. The season is in Placements and its configuration was confirmed with the signup
    module off; test mode is off and McLaren's second seat in Am is free. Oscar is a member of the
    server who holds no driver profile, or a real driver Not Signed Up. The league admin Admin
    (77) runs `/driver assign` of Oscar to McLaren in Am, and the queue runs it. Oscar holds
    exactly one profile, on his account, and is Assigned, sitting at McLaren in Am, not confirmed;
    one audit entry, `DRIVER_ASSIGN`, by Admin, on Am, records him Not Signed Up before and at
    McLaren in Am, Assigned, after; the log channel holds the usual success line; no role is given
    on Discord, the placement being unconfirmed; and the reply is the usual "✅ Assigned **Oscar**
    to **McLaren F1 Team** in **Am**."."""
    from tests.support.change_queue import run_queue
    from tests.support.season_league import AM, NEWCOMER
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await build(tmp_path)
    asked = await _ask_assign(league, NEWCOMER)
    await run_queue(league.bot)

    [profile] = await league.rows(
        "SELECT id, current_state, is_test_driver FROM driver_profiles WHERE discord_user_id = ?",
        str(NEWCOMER),
    )
    assert (profile["current_state"], profile["is_test_driver"]) == ("ASSIGNED", 0)
    placed = await league.rows(
        "SELECT d.name AS division, ti.name AS team, dsa.committed "
        "FROM driver_season_assignments dsa "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = ?", profile["id"],
    )
    assert [(r["division"], r["team"], bool(r["committed"])) for r in placed] == [
        ("Am", "McLaren", False)
    ]
    [entry] = [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_ASSIGN"
    ]
    assert (entry["actor_id"], entry["division_id"]) == (77, AM)
    assert entry["old_value"] == {"state": "NOT_SIGNED_UP"}
    assert {key: entry["new_value"][key] for key in ("team", "division", "new_state")} == {
        "team": "McLaren", "division": "Am", "new_state": "ASSIGNED",
    }
    assert _assign_lines(await log_lines(league)) == [_assign_line("Oscar", NEWCOMER)]
    assert league.granted == {} and league.revoked == {}
    assert last_said(asked) == "✅ Assigned **Oscar** to **McLaren F1 Team** in **Am**."


async def test_with_signup_off_a_second_assign_of_a_member_with_no_profile_is_refused_at_its_check(
    tmp_path,
):
    """#624. The season is in Placements and its configuration was confirmed with the signup
    module off; test mode is off. Am holds McLaren, its second seat free, and Williams, a seat
    free. Oscar is a member of the server who holds no driver profile. Before the queue runs, the
    league admin runs `/driver assign` of Oscar to McLaren in Am, then of Oscar to Williams in Am;
    both are acknowledged. The queue runs them in turn. The first places Oscar at McLaren in Am,
    not confirmed, and makes his profile; the second, judged as it starts against the profile the
    first made, is refused, its acknowledgement updated to "⛔ Driver is already assigned to a
    team in **Am**.". Oscar holds one profile and the one placement, at McLaren; one
    `DRIVER_ASSIGN` audit entry is written; the log channel holds the success line and then the
    refusal; and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import AM, NEWCOMER
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _placing(tmp_path)
    await league.write(
        "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
        "VALUES (33, ?, 'Williams', 'Williams Racing', 3, 0)", AM,
    )
    await league.write("INSERT INTO team_seats (team_instance_id, seat_number) VALUES (33, 1)")
    first = await _ask_assign(league, NEWCOMER)
    second = await _ask_assign(league, NEWCOMER, team="Williams")
    await run_queue(league.bot)

    refusal = "⛔ Driver is already assigned to a team in **Am**."
    assert last_said(first) == "✅ Assigned **Oscar** to **McLaren F1 Team** in **Am**."
    assert last_said(second) == refusal
    [profile] = await league.rows(
        "SELECT id, current_state FROM driver_profiles WHERE discord_user_id = ?", str(NEWCOMER)
    )
    assert profile["current_state"] == "ASSIGNED"
    placed = await league.rows(
        "SELECT ti.name AS team, dsa.committed FROM driver_season_assignments dsa "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = ?", profile["id"],
    )
    assert [(r["team"], bool(r["committed"])) for r in placed] == [("McLaren", False)]
    assert [
        row["change_type"] for row in await audit_rows(league.db_path)
    ] == ["DRIVER_ASSIGN"]
    assert _assign_lines(await log_lines(league)) == [
        _assign_line("Oscar", NEWCOMER), _assign_refusal(refusal),
    ]
    assert await stopped_job(league.db_path) is None
