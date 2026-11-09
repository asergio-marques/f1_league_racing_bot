"""`/driver reject` — turning down an approved driver who has not been placed (issue #220).

Available where a window's drivers are settled: in Placements and in Ongoing, placements. The
driver returns to Not Signed Up and loses the driver role; their signup stays with the season, and
ceases to be approved (the signup specification).

The command is a change on the league's lane (#283): the driver named at the ask by their profile,
the queue's acknowledgement its reply, one save writing the turn-down, the approval's withdrawal
through core's driver-turned-down hook, its `DRIVER_REJECT` audit entry and its line with the
queue's clock, then a step taking the driver role that ends on a fault rather than stopping the
lane. These run on `season_league`'s season, in Placements, or `placing_league`'s, mid-season
placements, with the signup module on, the league's driver role set, and Valtteri (106) approved
for the season and never placed: Unassigned, his signup approved, holding the driver role.
"""
from __future__ import annotations

import pytest

from tests.core.test_driver_move import _ADMIN, _as_logged

_NOT_PLACING = (
    "⛔ `/driver reject` is available only while the season is in placements, or mid-season "
    "while the drivers of a closed signup window are being placed."
)
_GONE_ACCOUNT = 9999
_PAST_ACCOUNT = (
    f"⛔ <@106> is a past account of a driver whose current account, <@{_GONE_ACCOUNT}>, is not "
    "in the server."
)


def _not_unassigned(name: str = "Valtteri") -> str:
    return (
        f"⛔ **{name}** is not an Unassigned driver. A placed driver is unassigned first; a signup "
        "still in review is rejected from its panel."
    )


async def _in_placements(tmp_path):
    from tests.support.season_league import season_league

    return await season_league(tmp_path)


async def _placing_mid_season(tmp_path):
    from tests.support.season_league import placing_league

    return await placing_league(tmp_path)


async def _with_valtteri(league, *others):
    """The signup module on, the league's driver role set, and Valtteri (and each of *others*)
    approved and never placed, his signup for the season approved."""
    from tests.support.season_league import DRIVER_ROLE, UNASSIGNED, approved_unplaced

    await league.switch("signup", True)
    await league.write("UPDATE server_configs SET driver_role_id = ?", DRIVER_ROLE)
    for user_id in (UNASSIGNED, *others):
        await approved_unplaced(league, user_id)
        await league.write(
            "UPDATE signup_records SET approved = 1 WHERE discord_user_id = ?", str(user_id)
        )
    return league


async def _ask_reject(league, user_id):
    """`/driver reject` of the driver *user_id*, as the league admin Admin (77) runs it: the
    interaction. The queue is not run."""
    from leaguebot.core.cogs.driver_cog import DriverCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "driver reject",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(DriverCog.reject)(DriverCog(league.bot), asked, league._member(user_id))
    return asked


def _rejecting(name: str = "Valtteri") -> str:
    """How the queue words `/driver reject` of *name*."""
    return f"Turning down **{name}**"


def _turned_down(name: str = "Valtteri") -> str:
    """`/driver reject`'s reply once done: today's."""
    return f"✅ Turned down **{name}**. They are no longer signed up."


def _reject_line(name: str = "Valtteri", user_id: int = 106) -> str:
    """`/driver reject`'s line in the log channel, word for word: today's, the member asking
    named as every change on the queue names them."""
    return f"{_ADMIN} | /driver reject | Success\n  user: {name} (`<@{user_id}>`)"


def _reject_refusal(reply: str) -> str:
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ `/driver reject` refused for {_ADMIN} — {_as_logged(reply_reason(reply))}"


def _reject_lines(lines):
    return [line for line in lines if "/driver reject" in line]


async def _drivers(league):
    """Every driver's state and how many of their signups are approved, by profile id."""
    rows = await league.rows(
        "SELECT dp.id, dp.current_state, "
        "  (SELECT COUNT(*) FROM signup_records sr JOIN driver_accounts da "
        "     ON da.discord_user_id = sr.discord_user_id "
        "   WHERE da.driver_profile_id = dp.id AND sr.approved = 1) AS approved "
        "FROM driver_profiles dp ORDER BY dp.id"
    )
    return {row["id"]: (row["current_state"], row["approved"]) for row in rows}


async def _valtteri(league):
    """Valtteri's state, how many of his signups are approved, and how many he has."""
    from tests.support.season_league import UNASSIGNED, profile_id

    [row] = await league.rows(
        "SELECT current_state, "
        "  (SELECT COUNT(*) FROM signup_records WHERE discord_user_id = ? AND approved = 1) "
        "    AS approved, "
        "  (SELECT COUNT(*) FROM signup_records WHERE discord_user_id = ?) AS signups "
        "FROM driver_profiles WHERE id = ?",
        str(UNASSIGNED), str(UNASSIGNED), profile_id(UNASSIGNED),
    )
    return row["current_state"], row["approved"], row["signups"]


_STANDING = ("UNASSIGNED", 1, 1)


async def _reject_audits(league):
    from tests.support.settings_league import audit_rows

    return [
        row for row in await audit_rows(league.db_path) if row["change_type"] == "DRIVER_REJECT"
    ]


async def _approve_placements(league):
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'ONGOING'")


def _season_back_at(stage: str):
    """The season, still in setup, back at *stage*: before its placements."""

    async def back_at(league):
        await league.write("UPDATE seasons SET stage = ?", stage)

    return back_at


async def _valtteri_in_review(league):
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "UPDATE driver_profiles SET current_state = 'PENDING_ADMIN_APPROVAL' WHERE id = ?",
        profile_id(UNASSIGNED),
    )


async def _valtteri_turned_down(league):
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "UPDATE driver_profiles SET current_state = 'NOT_SIGNED_UP' WHERE id = ?",
        profile_id(UNASSIGNED),
    )


async def _valtteri_on_an_account_gone_from_the_server(league):
    """Valtteri's current account becomes 9999, which is not in the server; 106 is then a past
    account of his."""
    from tests.support.season_league import UNASSIGNED, profile_id

    await league.write(
        "UPDATE driver_profiles SET discord_user_id = ? WHERE id = ?",
        str(_GONE_ACCOUNT), profile_id(UNASSIGNED),
    )
    league.absent.add(_GONE_ACCOUNT)


@pytest.mark.parametrize(
    "build",
    [
        pytest.param(_in_placements, id="in-placements"),
        pytest.param(_placing_mid_season, id="mid-season-placements"),
    ],
)
async def test_a_driver_rejection_its_withdrawal_audit_entry_and_line_are_one_save_then_the_role(
    tmp_path, build,
):
    """The core specification's turn-down of an approved driver and its record of what changed.
    The season is in Placements, or placing drivers mid-season; Valtteri is Unassigned, his signup
    approved, holding the driver role. The league admin Admin (77) runs `/driver reject` of
    Valtteri. They are answered at once with the queue's acknowledgement, "Turning down
    **Valtteri**", naming its first job, and nothing is changed and no role taken until the queue
    runs the change, of kind `core.driver_reject`. Once it has, Valtteri is Not Signed Up; his
    signup stays with the season but is no longer approved, withdrawn through the
    driver-turned-down hook in the same save; one audit entry, `DRIVER_REJECT`, by Admin, on no
    division, stamped with the queue's clock, records him Unassigned before and Not Signed Up after;
    the log channel holds today's success line once; the driver role is taken from him; and the
    acknowledgement is updated to today's reply, "✅ Turned down **Valtteri**. They are no longer
    signed up."."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.season_league import DRIVER_ROLE, UNASSIGNED
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _with_valtteri(await build(tmp_path))
    asked = await _ask_reject(league, UNASSIGNED)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_rejecting())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.driver_reject"]
    assert await _valtteri(league) == _STANDING
    assert league.revoked == {} and league.granted == {}
    await run_queue(league.bot)

    assert await _valtteri(league) == ("NOT_SIGNED_UP", 0, 1)
    [entry] = await _reject_audits(league)
    assert (entry["actor_id"], entry["division_id"]) == (77, None)
    assert entry["old_value"] == {"state": "UNASSIGNED"}
    assert entry["new_value"] == {"new_state": "NOT_SIGNED_UP"}
    assert entry["timestamp"] == league.clock.now.isoformat()
    assert _reject_lines(await log_lines(league)) == [_reject_line()]
    assert league.revoked == {UNASSIGNED: [DRIVER_ROLE]}
    assert league.granted == {}
    assert last_said(asked) == _turned_down()


@pytest.mark.parametrize(
    "setup, user_id, reply",
    [
        pytest.param(_season_back_at("CONFIGURATION"), 106, _NOT_PLACING, id="in-configuration"),
        pytest.param(_season_back_at("SIGNUPS"), 106, _NOT_PLACING, id="in-signups"),
        pytest.param(_approve_placements, 106, _NOT_PLACING, id="placements-approved"),
        pytest.param(None, 103, _not_unassigned("Charles"), id="a-placed-driver"),
        pytest.param(_valtteri_in_review, 106, _not_unassigned(), id="a-signup-in-review"),
        pytest.param(_valtteri_turned_down, 106, _not_unassigned(), id="not-signed-up"),
        pytest.param(
            _valtteri_on_an_account_gone_from_the_server, 106, _PAST_ACCOUNT,
            id="a-past-account-whose-current-one-left",
        ),
    ],
)
async def test_a_driver_rejection_is_refused_at_the_ask(tmp_path, setup, user_id, reply):
    """The season is in Placements; Valtteri is Unassigned and approved, Charles (103) placed at
    McLaren in Am. The league admin runs `/driver reject` where it cannot be done: the season
    still in Configuration or in Signups; the placements approved and the season Ongoing; of
    Charles, a placed driver; of Valtteri with his signup back in review, or already Not Signed Up;
    or of Valtteri's account 106 once his current account is another that has left the server.
    They are refused at once, in today's words, and the log channel takes the refusal as its one
    line. Nothing is asked of the queue, every driver's state and approvals stand, no audit entry
    is written and no role is touched. Takes the place of the mocked "refused outside the placing
    stages" and "only an Unassigned driver" tests."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _with_valtteri(await _in_placements(tmp_path))
    if setup is not None:
        await setup(league)
    standing = await _drivers(league)
    asked = await _ask_reject(league, user_id)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_reject_refusal(reply)]
    assert await change_rows(league.db_path) == []
    assert await _drivers(league) == standing
    assert await audit_rows(league.db_path) == []
    assert league.revoked == {} and league.granted == {}


async def test_a_failed_driver_rejection_changes_nothing(tmp_path):
    """The season is in Placements; Valtteri is Unassigned and approved. The league admin runs
    `/driver reject` of Valtteri, and as the queue runs it the database fails writing the audit
    entry. Nothing of the rejection is saved, the turn-down, the approval's withdrawal, its audit
    entry and its line being one save: Valtteri is Unassigned with his signup approved, no audit
    entry is written, the log channel holds no success line and no role is touched on Discord.
    The league's lane stops at the job. Once a league admin discards the stopped job, the reply
    says "Nothing was turned down: **Valtteri** is still an Unassigned driver. Run `/driver
    reject` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.season_league import UNASSIGNED
    from tests.support.settings_league import audit_rows, fault_in, last_said, log_lines

    league = await _with_valtteri(await _in_placements(tmp_path))
    asked = await _ask_reject(league, UNASSIGNED)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _valtteri(league) == _STANDING
    assert await audit_rows(league.db_path) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert league.revoked == {} and league.granted == {}
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was turned down: **Valtteri** is still an Unassigned driver. Run `/driver reject` "
        "again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.parametrize(
    "meanwhile, reply, standing",
    [
        pytest.param(
            _approve_placements, _NOT_PLACING, _STANDING, id="the-placements-approved",
        ),
        pytest.param(
            _valtteri_in_review, _not_unassigned(), ("PENDING_ADMIN_APPROVAL", 1, 1),
            id="back-in-review",
        ),
        pytest.param(
            _valtteri_on_an_account_gone_from_the_server, _PAST_ACCOUNT, _STANDING,
            id="now-a-past-account-whose-current-one-left",
        ),
    ],
)
async def test_a_driver_rejection_is_judged_again_as_it_starts(
    tmp_path, meanwhile, reply, standing,
):
    """The season is in Placements; Valtteri is Unassigned and approved. The league admin runs
    `/driver reject` of Valtteri and is acknowledged; before the queue runs it, the placements are
    approved and the season is Ongoing, Valtteri's signup is put back in review, or Valtteri's
    current account becomes another that has left the server. The queue runs. The rejection is
    refused as it starts, in the words it would meet at the ask, its acknowledgement updated to
    them. Valtteri stands as the meanwhile left him, his signup still approved, no audit entry is
    written, the log channel holds the refusal alone, no role is touched, and the queue is not
    stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.season_league import UNASSIGNED
    from tests.support.settings_league import audit_rows, last_said, log_lines

    league = await _with_valtteri(await _in_placements(tmp_path))
    refused = await _ask_reject(league, UNASSIGNED)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(refused) == reply
    assert await _valtteri(league) == standing
    assert await audit_rows(league.db_path) == []
    assert await log_lines(league) == [_reject_refusal(reply)]
    assert league.revoked == {} and league.granted == {}
    assert await stopped_job(league.db_path) is None


async def test_a_driver_rejection_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Placements; Valtteri is Unassigned and approved. The league admin runs `/driver
    reject` of Valtteri and is acknowledged; before the queue runs it, they run it again. The
    second is refused at once, in the queue's words, and the log channel takes that refusal as a
    line; only the one change is queued. The queue runs it: Valtteri is Not Signed Up, with one
    audit entry, and the log channel holds the refusal and then the success line."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.season_league import UNASSIGNED
    from tests.support.settings_league import log_lines, said
    from tests.support.setup_rounds import acknowledged, repeated

    league = await _with_valtteri(await _in_placements(tmp_path))
    first, repeat = (
        await _ask_reject(league, UNASSIGNED), await _ask_reject(league, UNASSIGNED)
    )

    assert said(first)[0].startswith(acknowledged(_rejecting()))
    assert said(repeat) == [repeated(_rejecting())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert await _valtteri(league) == ("NOT_SIGNED_UP", 0, 1)
    assert len(await _reject_audits(league)) == 1
    assert _reject_lines(await log_lines(league)) == [
        _reject_refusal(repeated(_rejecting())), _reject_line(),
    ]


async def test_the_driver_role_goes_after_the_rejection_is_saved_and_a_refusal_is_reported(
    tmp_path,
):
    """The rejection is saved before the driver role is taken, and a role Discord refuses ends its
    own step rather than stopping the lane (architecture.md, "A job that fails stops its lane").
    The season is in Placements; Valtteri and Kimi (105) are both Unassigned and approved, holding
    the driver role. The league admin runs `/driver reject` of Valtteri, then of Kimi, before the
    queue runs; Discord refuses to take any role from Valtteri (403 Missing Permissions). The queue
    runs both. Valtteri's rejection stands, saved before his role was asked of Discord: he is Not
    Signed Up, his signup no longer approved, and he still holds the driver role. His reply is
    today's, then "⚠️ Done, but on Discord:" and a bullet, "the role <@&820> could not be taken
    from <@106>: Discord refused or failed a request from the bot"; his success line carries a
    "not done:" line in the same words, its mentions written in code as the log channel writes
    every mention. The lane is not stopped, and Kimi's rejection, asked after, goes through: his
    success line written, the driver role taken, his reply today's alone. Takes the place of
    `test_driver_ongoing_commands.py`'s mocked "rejection whose role cannot be removed"."""
    import discord

    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.season_league import DRIVER_ROLE, SIGNING_UP, UNASSIGNED
    from tests.support.settings_league import last_said, log_lines

    league = await _with_valtteri(await _in_placements(tmp_path), SIGNING_UP)
    league.revoke_fails[UNASSIGNED] = http_error(
        discord.Forbidden, status=403, text="Missing Permissions"
    )
    valtteri = await _ask_reject(league, UNASSIGNED)
    kimi = await _ask_reject(league, SIGNING_UP)
    await run_queue(league.bot)

    problem = (
        f"the role <@&{DRIVER_ROLE}> could not be taken from <@{UNASSIGNED}>: Discord refused or "
        "failed a request from the bot"
    )
    assert await _valtteri(league) == ("NOT_SIGNED_UP", 0, 1)
    assert league.roles_held[UNASSIGNED] == {DRIVER_ROLE}
    assert last_said(valtteri) == f"{_turned_down()}\n⚠️ Done, but on Discord:\n• {problem}"
    assert _reject_lines(await log_lines(league)) == [
        f"{_reject_line()}\n  not done: {_as_logged(problem)}",
        _reject_line("Kimi", SIGNING_UP),
    ]
    assert await stopped_job(league.db_path) is None
    assert league.revoked[SIGNING_UP] == [DRIVER_ROLE]
    assert last_said(kimi) == _turned_down("Kimi")
