"""`/driver assign` on the change queue (#283).

The command is a change on the league's lane: the driver, the division and the team named at the
ask by their ids, the queue's acknowledgement its reply, and one save (`assign_on`) writing the
placement, its `DRIVER_ASSIGN` audit entry and its line with the queue's clock. The placement is
uncommitted, so no role is given and no lineup posted. The guards that read templates and the
server's test-mode flag run in the change's check, at the ask and again as it starts.

These run on `season_league`'s season, in Placements: Charles alone at McLaren in Am, its second
seat free, and Valtteri (106) approved and never placed (`approved_unplaced`).
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
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/driver assign` refused for {_ADMIN} — {reply_reason(reply)}"


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

    from leaguebot.core.services.image_drawing import CapacityProblem

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
    with fault_in("leaguebot.core.services.queue.change_queue.record_change_on"):
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


@pytest.mark.parametrize(
    "build, user_id, reply",
    [
        pytest.param(_placing_mclaren_full, 106, _NO_SEAT, id="no-free-seat"),
        pytest.param(_placing_mid_season, 101, _CONFIRMED_ELSEWHERE, id="a-confirmed-driver"),
        pytest.param(
            _placing_standings_full, 106, _STANDINGS_FULL, id="the-standings-template-full"
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
    one line. Nothing is asked of the queue, nobody is placed, no audit entry is written and no
    role touched. Takes the place of `test_driver_cog_refusals.py`'s "assign refused by the
    service" case and `test_driver_cog.py`'s `test_a_refused_assignment_is_reported_not_raised`,
    which mocked the service, and of the mid-season case of its
    `test_an_assignment_is_made_uncommitted`."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await build(tmp_path)
    placements = await league.rows("SELECT * FROM driver_season_assignments ORDER BY id")
    asked = await _ask_assign(league, user_id)

    assert last_said(asked) == reply
    assert _assign_lines(await log_lines(league)) == [_assign_refusal(reply)]
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
