"""`/driver unassign` on the change queue (#283).

The command is a change on the league's lane: the driver and the division named at the ask by
their ids, the queue's acknowledgement its reply, and one save (`unassign_on`) writing the
removal, its `DRIVER_UNASSIGN` audit entry and its line with the queue's clock. The placement it
removes is never confirmed, so no role is taken back and no lineup posted.

These run on `season_league`'s season, in Placements, Charles alone at McLaren in Am, his
placement not confirmed; or on `placing_league`'s, mid-season placements, where it is the same.
"""
from __future__ import annotations

import pytest

from tests.core.test_driver_move import _ADMIN

_REASON = "#283: /driver unassign is not yet a change on the queue"

_NOT_PLACING = (
    "⛔ `/driver unassign` is available only while the season is in placements, or mid-season "
    "while the drivers of a closed signup window are being placed."
)
_NOT_THERE = "⛔ Driver is not assigned to any team in **Am**."
_CONFIRMED = (
    "⛔ That placement has been confirmed. Move the driver with `/driver move`, or release them "
    "from the division with `/driver release`."
)


async def _ask_unassign(league, user_id, division="Am"):
    """`/driver unassign` of the driver *user_id* from *division*, as the league admin Admin (77)
    runs it: the interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver unassign",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.unassign)(
        DriverCog(league.bot), asked, league._member(user_id), division
    )
    return asked


async def _charles(league):
    """Where Charles sits, (division, team, whether confirmed), and his state."""
    rows = await league.rows(
        "SELECT d.name AS division, ti.name AS team, dsa.committed "
        "FROM driver_season_assignments dsa "
        "JOIN divisions d ON d.id = dsa.division_id "
        "JOIN team_seats ts ON ts.id = dsa.team_seat_id "
        "JOIN team_instances ti ON ti.id = ts.team_instance_id "
        "WHERE dsa.driver_profile_id = 43"
    )
    [state] = await league.rows("SELECT current_state FROM driver_profiles WHERE id = 43")
    return (
        [(r["division"], r["team"], bool(r["committed"])) for r in rows], state["current_state"]
    )


_SEATED = ([("Am", "McLaren", False)], "ASSIGNED")


def _unassigning(name: str = "Charles") -> str:
    """How the queue words `/driver unassign` of *name* from Am."""
    return f"Unassigning **{name}** from **Am**"


def _unassign_line(name: str = "Charles", user_id: int = 103) -> str:
    """`/driver unassign`'s line in the log channel, word for word: today's, the member asking
    named as every change on the queue names them."""
    return (
        f"{_ADMIN} | /driver unassign | Success\n"
        f"  user: {name} (`<@{user_id}>`)\n"
        f"  division: Am"
    )


def _unassign_refusal(reply: str) -> str:
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/driver unassign` refused for {_ADMIN} — {reply_reason(reply)}"


def _unassign_lines(lines):
    return [line for line in lines if "/driver unassign" in line]


async def _unassign_audits(league):
    from tests.support.settings_league import audit_rows

    return [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_UNASSIGN"
    ]


async def _in_placements(tmp_path):
    from tests.support.season_league import season_league

    return await season_league(tmp_path)


async def _placing_mid_season(tmp_path):
    from tests.support.season_league import placing_league

    return await placing_league(tmp_path)


async def _approve_placements(league):
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


async def _take_charles_out_of_am(league):
    await league.write("UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = 43")
    await league.write("DELETE FROM driver_season_assignments WHERE driver_profile_id = 43")


async def _confirm_charles(league):
    await league.write(
        "UPDATE driver_season_assignments SET committed = 1 WHERE driver_profile_id = 43"
    )


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(
            _in_placements, id="in-placements",
            marks=pytest.mark.xfail(strict=True, reason=_REASON),
        ),
        pytest.param(
            _placing_mid_season, id="mid-season-placements",
            marks=pytest.mark.xfail(strict=True, reason=_REASON),
        ),
    ],
)
async def test_a_driver_unassignment_and_its_audit_entry_and_line_are_one_save(tmp_path, build):
    """The core specification's removal of a placement and its record of what changed. The season
    is in Placements, or placing drivers mid-season; Charles sits alone at McLaren in Am, his
    placement not confirmed. The league admin Admin (77) runs `/driver unassign` of Charles from
    Am. They are answered at once with the queue's acknowledgement, "Unassigning **Charles** from
    **Am**", naming its first job, and nothing is removed until the queue runs the change, of kind
    `core.driver_unassign`. Once it has, Charles sits nowhere and is Unassigned, his seat free;
    one audit entry, `DRIVER_UNASSIGN`, by Admin, on Am, stamped with the queue's clock, records
    McLaren before and Charles Unassigned with no seat left after; the log channel holds today's
    success line once; no role is taken on Discord and no lineup is posted, the placement having
    been unconfirmed; and the acknowledgement is updated to today's reply, "✅ Removed
    **Charles** from **McLaren F1 Team** in **Am**."."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import AM, DIVISIONS, PRO
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await build(tmp_path)
    asked = await _ask_unassign(league, 103)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_unassigning())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.driver_unassign"
    ]
    assert await _charles(league) == _SEATED
    await run_queue(league.bot)

    assert await _charles(league) == ([], "UNASSIGNED")
    assert await league.rows("SELECT 1 FROM team_seats WHERE driver_profile_id = 43") == []
    [entry] = await _unassign_audits(league)
    assert (entry["actor_id"], entry["division_id"]) == (77, AM)
    assert entry["old_value"]["team"] == "McLaren"
    assert entry["new_value"] == {"new_state": "UNASSIGNED", "has_remaining": False}
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _unassign_lines(await log_lines(league)) == [_unassign_line()]
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert last_said(asked) == "✅ Removed **Charles** from **McLaren F1 Team** in **Am**."


@pytest.mark.xfail(strict=True, reason=_REASON)
async def test_a_failed_driver_unassignment_removes_nothing(tmp_path):
    """The season is in Placements; Charles sits at McLaren in Am. The league admin runs `/driver
    unassign` of Charles from Am, and as the queue runs it the database fails writing the audit
    entry. Nothing of the removal is saved, the removal, its audit entry and its line being one
    save: Charles still sits at McLaren in Am, Assigned, no audit entry is written and the log
    channel holds no success line. The league's lane stops at the job. Once a league admin
    discards the stopped job, the reply says "Nothing was unassigned: **Charles** keeps their seat
    in **Am**. Run `/driver unassign` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import fault_in, last_said, log_lines

    league = await _in_placements(tmp_path)
    asked = await _ask_unassign(league, 103)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _charles(league) == _SEATED
    assert await _unassign_audits(league) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was unassigned: **Charles** keeps their seat in **Am**. "
        "Run `/driver unassign` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "build, meanwhile, reply, charles",
    [
        pytest.param(
            _in_placements, _approve_placements, _NOT_PLACING, _SEATED,
            id="the-placements-approved", marks=pytest.mark.xfail(strict=True, reason=_REASON),
        ),
        pytest.param(
            _in_placements, _take_charles_out_of_am, _NOT_THERE, ([], "ASSIGNED"),
            id="no-longer-seated-there", marks=pytest.mark.xfail(strict=True, reason=_REASON),
        ),
        pytest.param(
            _placing_mid_season, _confirm_charles, _CONFIRMED, ([("Am", "McLaren", True)],
                                                                "ASSIGNED"),
            id="the-placement-confirmed", marks=pytest.mark.xfail(strict=True, reason=_REASON),
        ),
    ],
)
async def test_a_driver_unassignment_is_judged_again_as_it_starts(
    tmp_path, build, meanwhile, reply, charles
):
    """Charles sits at McLaren in Am, unconfirmed. The league admin runs `/driver unassign` of
    Charles from Am and is acknowledged; before the queue runs it, the season's placements are
    approved (Ongoing), Charles is taken out of Am, or, the season placing drivers mid-season,
    his placement is confirmed. The queue runs. The removal is refused as it starts, in the words
    it would meet at the ask, its acknowledgement updated to them: "`/driver unassign` is
    available only while the season is in placements, or mid-season while the drivers of a closed
    signup window are being placed.", "Driver is not assigned to any team in **Am**." or "That
    placement has been confirmed. Move the driver with `/driver move`, or release them from the
    division with `/driver release`.". Charles stands as the meanwhile left him, no audit entry is
    written, the log channel holds the refusal alone, no role is touched, and the queue is not
    stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await build(tmp_path)
    refused = await _ask_unassign(league, 103)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _charles(league) == charles
    assert await _unassign_audits(league) == []
    assert _unassign_lines(await log_lines(league)) == [_unassign_refusal(reply)]
    assert league.revoked == {} and league.granted == {}
    assert await stopped_job(league.db_path) is None


async def test_a_driver_unassignment_is_refused_at_the_ask(tmp_path):
    """The season is in Placements, and Charles holds no seat in Am. The league admin runs
    `/driver unassign` of Charles from Am. They are refused at once, in today's words: "Driver is
    not assigned to any team in **Am**.", and the log channel takes the refusal as its one line.
    Nothing is asked of the queue, no audit entry is written and no role touched. Takes the place
    of `test_driver_cog_refusals.py`'s "unassign refused by the service" case and
    `test_driver_cog.py`'s `test_a_refused_unassignment_is_reported_not_raised`, which mocked the
    service."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _in_placements(tmp_path)
    await _take_charles_out_of_am(league)
    asked = await _ask_unassign(league, 103)

    assert last_said(asked) == _NOT_THERE
    assert _unassign_lines(await log_lines(league)) == [_unassign_refusal(_NOT_THERE)]
    assert await change_rows(league.db_path) == []
    assert await audit_rows(league.db_path) == []
    assert league.granted == {} and league.revoked == {}


async def test_unassigning_a_driver_who_holds_no_seat_names_no_team(tmp_path):
    """The season is in Placements; Charles is of Am but holds no seat in it, his seat at McLaren
    freed. The league admin runs `/driver unassign` of Charles from Am, and the queue runs it. The
    reply names the division alone, "✅ Removed **Charles** in **Am**.", rather than an empty
    team. Takes the place of `test_driver_cog.py`'s
    `test_unassigning_a_driver_who_held_no_seat_omits_the_team`, which mocked the service."""
    from tests.support.change_queue import run_queue
    from tests.support.settings_league import last_said

    league = await _in_placements(tmp_path)
    await league.write("UPDATE team_seats SET driver_profile_id = NULL WHERE driver_profile_id = 43")
    await league.write(
        "UPDATE driver_season_assignments SET team_seat_id = NULL WHERE driver_profile_id = 43"
    )
    asked = await _ask_unassign(league, 103)
    await run_queue(league.bot)

    assert last_said(asked) == "✅ Removed **Charles** in **Am**."
    assert await league.rows(
        "SELECT 1 FROM driver_season_assignments WHERE driver_profile_id = 43"
    ) == []


@pytest.mark.xfail(strict=True, reason=_REASON)
async def test_a_driver_unassignment_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements; Charles sits at McLaren in Am. The league admin runs `/driver
    unassign` of Charles from Am and is acknowledged; before the queue runs it, they run it
    again. The second is refused at once, in the queue's words, and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: Charles sits nowhere,
    with one audit entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _in_placements(tmp_path)
    first, repeat = await _ask_unassign(league, 103), await _ask_unassign(league, 103)

    assert said(first)[0].startswith(acknowledged(_unassigning()))
    assert said(repeat) == [repeated(_unassigning())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _charles(league) == ([], "UNASSIGNED")
    assert len(await _unassign_audits(league)) == 1
    assert _unassign_lines(await log_lines(league)) == [
        _unassign_refusal(repeated(_unassigning())), _unassign_line(),
    ]
