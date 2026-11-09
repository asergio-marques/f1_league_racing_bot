"""`/driver release` — a committed driver released from one division (issue #220).

The driver keeps every other seat they hold. The division's role is revoked, the team's only
where no other seat maps to it, and the division's lineup is posted again. A driver's only seat
is not released — they are sacked or moved instead — and an uncommitted placement is unassigned.

The command is a change on the league's lane (#283): the driver and the division named at the ask
by their ids, the queue's acknowledgement its reply, one save (`unassign_on`) writing the release,
its `DRIVER_UNASSIGN` audit entry and its line with the queue's clock, then a step taking the
member's roles that ends on a fault rather than stopping the lane, then the division's lineup.
These run on `ongoing_league`'s season (see `test_driver_move`'s queue section), Lewis seated
twice, confirmed: at Ferrari in Pro and at McLaren in Am, holding all four roles.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.core.test_driver_move import (
    SERVER_ID,
    _ADMIN,
    _end_the_races,
    _lewis_seats,
    _ongoing,
    _seat_lewis_in_am,
)

#: Why each test below fails until `/driver release` is a change on the queue.
_RELEASE_NOT_QUEUED = "#283: /driver release is not yet a change on the queue"


async def _twice_seated(tmp_path):
    """`ongoing_league`'s season, Lewis seated at McLaren in Am as well as at Ferrari in Pro,
    both confirmed, and holding Am's role and McLaren's beside Pro's and Ferrari's."""
    from tests.support.season_league import AM_ROLE, LEWIS, MCLAREN_ROLE

    league = await _ongoing(tmp_path)
    await _seat_lewis_in_am(league)
    league.roles_held[LEWIS] |= {AM_ROLE, MCLAREN_ROLE}
    return league


async def _ask_release(league, user_id, division="Am"):
    """`/driver release` of the driver *user_id* from *division*, as the league admin Admin (77)
    runs it: the interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver release",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.release)(
        DriverCog(league.bot), asked, league._member(user_id), division
    )
    return asked


#: Where Lewis sits before anything is released.
_BOTH = [("Pro", "Ferrari", True), ("Am", "McLaren", True)]


def _releasing(name: str, division: str = "Am") -> str:
    """How the queue words `/driver release` of *name* from *division*."""
    return f"Releasing **{name}** from **{division}**"


def _release_line(name: str, user_id: int, division: str = "Am") -> str:
    """`/driver release`'s line in the log channel, word for word: today's, the member asking
    named as every change on the queue names them."""
    return (
        f"{_ADMIN} | /driver release | Success\n"
        f"  user: {name} (`<@{user_id}>`)\n"
        f"  division: {division}"
    )


def _release_refusal(reply: str) -> str:
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/driver release` refused for {_ADMIN} — {reply_reason(reply)}"


def _release_lines(lines):
    return [line for line in lines if "/driver release" in line]


async def _unconfirm_lewis_in_am(league):
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 "
        "WHERE driver_profile_id = 41 AND division_id = 22"
    )


async def _unconfirm_lewis_in_pro(league):
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 "
        "WHERE driver_profile_id = 41 AND division_id <> 22"
    )


async def _take_lewis_out_of_am(league):
    await league.write("UPDATE team_seats SET driver_profile_id = NULL WHERE team_instance_id = 32 "
                       "AND driver_profile_id = 41")
    await league.write(
        "DELETE FROM driver_season_assignments WHERE driver_profile_id = 41 AND division_id = 22"
    )


_ONLY_SEAT = (
    "⛔ That is the driver's only seat. Sack them with `/driver sack`, or move them with "
    "`/driver move`."
)
_NOT_CONFIRMED = "⛔ That placement is not yet confirmed. Remove it with `/driver unassign`."
_NO_SEAT = "⛔ Driver holds no seat in that division."


async def test_a_driver_release_and_its_audit_entry_and_line_are_one_save_then_roles_and_lineup(
    tmp_path,
):
    """The core specification's release and its record of what changed. The season is ongoing;
    Lewis sits at Ferrari in Pro and at McLaren in Am, both confirmed, holding the four roles.
    The league admin Admin (77) runs `/driver release` of Lewis from Am. They are answered at
    once with the queue's acknowledgement, "Releasing **Lewis** from **Am**", naming its first
    job, and nothing is released, no role touched and no lineup posted until the queue runs the
    change, of kind `core.driver_release`. Once it has, Lewis sits at Ferrari in Pro alone, still
    Assigned; one audit entry, `DRIVER_UNASSIGN`, by Admin, on Am, stamped with the queue's clock,
    records McLaren before and Lewis still Assigned with a seat left after; the log channel holds
    today's success line once; Lewis has Am's role and McLaren's taken and keeps Pro's and
    Ferrari's; Am's lineup is posted once in its lineup channel and Pro's not at all; and the
    acknowledgement is updated to today's reply, "✅ Released **Lewis** from **Am**."."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import AM, AM_ROLE, DIVISIONS, LEWIS, MCLAREN_ROLE, PRO
    from tests.support.settings_league import audit_rows, last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _twice_seated(tmp_path)
    asked = await _ask_release(league, LEWIS)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == (
        f"{acknowledged(_releasing('Lewis'))} It begins with job #{first_job}."
    )
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.driver_release"]
    assert await _lewis_seats(league) == _BOTH
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    await run_queue(league.bot)

    assert await _lewis_seats(league) == [("Pro", "Ferrari", True)]
    [entry] = [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_UNASSIGN"
    ]
    assert (entry["change_type"], entry["actor_id"], entry["division_id"]) == (
        "DRIVER_UNASSIGN", 77, AM
    )
    assert entry["old_value"]["team"] == "McLaren"
    assert entry["new_value"] == {"new_state": "ASSIGNED", "has_remaining": True}
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _release_lines(await log_lines(league)) == [_release_line("Lewis", LEWIS)]
    assert sorted(league.revoked[LEWIS]) == [AM_ROLE, MCLAREN_ROLE]
    assert LEWIS not in league.granted
    assert len(league.texts(DIVISIONS[AM][3].lineup)) == 1
    assert league.texts(DIVISIONS[PRO][3].lineup) == []
    assert last_said(asked) == "✅ Released **Lewis** from **Am**."


@pytest.mark.parametrize(
    "setup, division, reply, seats",
    [
        pytest.param(None, "Pro", _ONLY_SEAT, [("Pro", "Ferrari", True)], id="the-only-seat"),
        pytest.param(
            _unconfirm_lewis_in_am, "Pro", _ONLY_SEAT,
            [("Pro", "Ferrari", True), ("Am", "McLaren", False)],
            id="an-unconfirmed-seat-elsewhere-does-not-count",
        ),
        pytest.param(
            _unconfirm_lewis_in_am, "Am", _NOT_CONFIRMED,
            [("Pro", "Ferrari", True), ("Am", "McLaren", False)], id="an-unconfirmed-placement",
        ),
        pytest.param(None, "Am", _NO_SEAT, [("Pro", "Ferrari", True)], id="no-seat-there"),
    ],
)
async def test_a_driver_release_is_refused_at_the_ask(tmp_path, setup, division, reply, seats):
    """The season is ongoing and Lewis sits at Ferrari in Pro, confirmed. The league admin runs
    `/driver release` of Lewis from a division where it cannot be done: from Pro, his only seat;
    from Pro, while his seat at McLaren in Am is unconfirmed, which does not count; from Am,
    where that seat is unconfirmed; or from Am, where he holds no seat. They are refused at once,
    in today's words: "That is the driver's only seat. Sack them with `/driver sack`, or move them
    with `/driver move`.", "That placement is not yet confirmed. Remove it with `/driver
    unassign`." or "Driver holds no seat in that division.", and the log channel takes the
    refusal as its one line. Nothing is asked of the queue, Lewis sits where he sat, no audit
    entry is written and no role touched. Takes the place too of `test_driver_cog_refusals.py`'s
    "release refused by the service" case, which mocked the service."""
    from tests.support.change_queue import change_rows
    from tests.support.season_league import LEWIS
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _ongoing(tmp_path)
    if setup is not None:
        await _seat_lewis_in_am(league)
        await setup(league)
    asked = await _ask_release(league, LEWIS, division)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_release_refusal(reply)]
    assert await change_rows(league.db_path) == []
    assert await _lewis_seats(league) == seats
    assert await audit_rows(league.db_path) == []
    assert league.revoked == {} and league.granted == {}


async def test_a_failed_driver_release_releases_nothing(tmp_path):
    """The season is ongoing; Lewis sits at Ferrari in Pro and at McLaren in Am. The league admin
    runs `/driver release` of Lewis from Am, and as the queue runs it the database fails writing
    the audit entry. Nothing of the release is saved, the release, its audit entry and its line
    being one save: Lewis sits in both, no audit entry is written, the log channel holds no
    success line, no role is touched on Discord and no lineup posted. The league's lane stops at
    the job. Once a league admin discards the stopped job, the reply says "Nothing was released:
    **Lewis** keeps their seat in **Am**. Run `/driver release` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.season_league import AM, DIVISIONS, LEWIS
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _twice_seated(tmp_path)
    asked = await _ask_release(league, LEWIS)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _lewis_seats(league) == _BOTH
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was released: **Lewis** keeps their seat in **Am**. Run `/driver release` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply, seats",
    [
        pytest.param(
            _end_the_races, "⛔ `/driver release` is available only while the season is ongoing.",
            _BOTH, id="the-races-ended",
        ),
        pytest.param(
            _unconfirm_lewis_in_pro, _ONLY_SEAT,
            [("Pro", "Ferrari", False), ("Am", "McLaren", True)], id="now-the-only-confirmed-seat",
        ),
        pytest.param(
            _unconfirm_lewis_in_am, _NOT_CONFIRMED,
            [("Pro", "Ferrari", True), ("Am", "McLaren", False)], id="the-placement-unconfirmed",
        ),
        pytest.param(
            _take_lewis_out_of_am, _NO_SEAT, [("Pro", "Ferrari", True)], id="no-longer-seated-there",
        ),
    ],
)
async def test_a_driver_release_is_judged_again_as_it_starts(tmp_path, meanwhile, reply, seats):
    """The season is ongoing; Lewis sits at Ferrari in Pro and at McLaren in Am, both confirmed.
    The league admin runs `/driver release` of Lewis from Am and is acknowledged; before the queue
    runs it, the season's races end (Pending completion), his Pro placement is made unconfirmed
    (leaving Am his only confirmed seat), his Am placement is made unconfirmed, or he is taken out
    of Am. The queue runs. The release is refused as it starts, in the words it would meet at the
    ask, its acknowledgement updated to them. Lewis's seats stand as the meanwhile left them, no
    audit entry is written, the log channel holds the refusal alone, no role is touched, no lineup
    posted, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import AM, DIVISIONS, LEWIS
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _twice_seated(tmp_path)
    refused = await _ask_release(league, LEWIS)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _lewis_seats(league) == seats
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [_release_refusal(reply)]
    assert league.revoked == {} and league.granted == {}
    assert league.texts(DIVISIONS[AM][3].lineup) == []
    assert await stopped_job(league.db_path) is None


async def test_a_driver_release_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is ongoing; Lewis sits at Ferrari in Pro and at McLaren in Am. The league admin runs
    `/driver release` of Lewis from Am and is acknowledged; before the queue runs it, they run it
    again. The second is refused at once, in the queue's words, and the log channel takes that
    refusal as a line; only the one change is queued. The queue runs it: Lewis sits in Pro alone,
    with one audit entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import LEWIS
    from tests.support.settings_league import audit_rows, log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _twice_seated(tmp_path)
    first, repeat = await _ask_release(league, LEWIS), await _ask_release(league, LEWIS)

    assert said(first)[0].startswith(acknowledged(_releasing("Lewis")))
    assert said(repeat) == [repeated(_releasing("Lewis"))]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _lewis_seats(league) == [("Pro", "Ferrari", True)]
    assert len([
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_UNASSIGN"
    ]) == 1
    assert _release_lines(await log_lines(league)) == [
        _release_refusal(repeated(_releasing("Lewis"))), _release_line("Lewis", LEWIS),
    ]


@pytest.mark.parametrize("stage_name", ["PLACEMENTS", "PENDING_COMPLETION"])
async def test_the_command_is_refused_outside_the_ongoing_stages(stage_name):
    from leaguebot.core.cogs.driver_cog import DriverCog
    from leaguebot.core.models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = DriverCog.__new__(DriverCog)
    cog.bot = MagicMock()
    cog.bot.driver_service.current_account = AsyncMock(side_effect=lambda a: str(a))
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1, stage=SeasonStage(stage_name))
    )
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()

    await undecorate(DriverCog.release)(cog, interaction, MagicMock(), "Pro")

    assert "only while the season is ongoing" in interaction.followup.send.await_args.args[0]
    assert cog.bot.change_queue.method_calls == []
