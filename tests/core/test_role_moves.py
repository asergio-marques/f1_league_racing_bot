"""The role step a driver's change plans for each member (#283, slice S16a).

architecture.md, "A job that fails stops its lane": the roles moved after a driver's or a team's
change are steps carrying `Step.on_fault`, so a role Discord refuses ends the job, reported in the
member's reply and the change's line, and the lane goes on. `/driver move`, `sack` and `release`
plan it now, and the assign, reject, reassign and team commands after them, so it is tested here
on its own as well as through each command.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.support.season_league import (
    AM_ROLE, DRIVER_ROLE, FERRARI_ROLE, LEWIS, MCLAREN_ROLE, PRO_ROLE, ongoing_league,
)

_REFUSED = "Discord refused or failed a request from the bot"

#: Why the role step's tests fail until the bot declares a team-role service to hand it (#283).
_NOT_YET_A_ROLE_SERVICE = "#283: the bot declares no TeamRoleService to hand the role step"

#: (what Discord does, the roles taken, the roles given, the problem the ended job reports or
#: None where the step completes, what Lewis has taken from him, what he is given).
_CASES = [
    pytest.param(
        None, [PRO_ROLE, FERRARI_ROLE], [AM_ROLE, MCLAREN_ROLE], None,
        [PRO_ROLE, FERRARI_ROLE], [AM_ROLE, MCLAREN_ROLE], id="moved in full",
    ),
    pytest.param(
        "grant refused", [PRO_ROLE, FERRARI_ROLE], [AM_ROLE, MCLAREN_ROLE],
        f"the roles <@&{AM_ROLE}> and <@&{MCLAREN_ROLE}> could not be given to <@{LEWIS}>: "
        f"{_REFUSED}",
        [PRO_ROLE, FERRARI_ROLE], None, id="a grant refused, after the old roles are taken",
    ),
    pytest.param(
        "revoke refused", [PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE], [],
        f"the roles <@&{PRO_ROLE}>, <@&{FERRARI_ROLE}> and <@&{DRIVER_ROLE}> could not be taken "
        f"from <@{LEWIS}>: {_REFUSED}",
        None, None, id="a revoke refused",
    ),
    pytest.param(
        "revoke refused", [PRO_ROLE, FERRARI_ROLE], [AM_ROLE, MCLAREN_ROLE],
        f"the roles <@&{PRO_ROLE}> and <@&{FERRARI_ROLE}> could not be taken from <@{LEWIS}>, "
        f"nor <@&{AM_ROLE}> and <@&{MCLAREN_ROLE}> given: {_REFUSED}",
        None, None, id="a revoke refused with roles still to give, as a move's",
    ),
    pytest.param(
        "left the server", [PRO_ROLE, FERRARI_ROLE], [AM_ROLE, MCLAREN_ROLE], None, None, None,
        id="a member Discord cannot find, passed over",
    ),
]


@pytest.mark.xfail(strict=True, reason=_NOT_YET_A_ROLE_SERVICE)
@pytest.mark.parametrize("discord_does, take, give, problem, taken, given", _CASES)
async def test_the_role_step_takes_then_gives_and_a_refusal_ends_the_job(
    tmp_path, discord_does, take, give, problem, taken, given
):
    """The season is ongoing; Lewis holds Pro's role, Ferrari's and the driver role on the server.
    The role step one of his changes plans for him (`role_move_step`, through the real team-role
    service) is run, to take some of his roles and give him others, while Discord answers as each
    case says.

    Moved in full: Pro's and Ferrari's roles are taken, then Am's and McLaren's given, and the step
    completes. A grant Discord refuses (403 Missing Permissions): the old roles are already taken,
    since taking comes first, nothing is given, and the job ends on the fault, its result naming the
    roles not given and the kind of fault in plain words, never Discord's own. A revoke Discord
    refuses, as a sack's: nothing is taken and the job ends likewise, naming the roles not taken. A
    revoke Discord refuses with roles still to give, as a move's: nothing is taken or given, and the
    problem names both the roles not taken and those not given. A member Discord cannot find (he
    left the server): the step completes, nothing taken or given, as his roles went with him. The
    step is never undiscardable, the queue refusing a step that is both."""
    import discord

    from leaguebot.core.services.role_moves import role_move_step
    from tests.support.change_queue import http_error, in_a_job

    league = await ongoing_league(tmp_path)
    league.roles_held[LEWIS] = {PRO_ROLE, FERRARI_ROLE, DRIVER_ROLE}
    refused = http_error(discord.Forbidden, status=403, text="Missing Permissions")
    if discord_does == "grant refused":
        league.grant_fails[LEWIS] = refused
    elif discord_does == "revoke refused":
        league.revoke_fails[LEWIS] = refused
    elif discord_does == "left the server":
        league.absent.add(LEWIS)
    step = role_move_step(league.bot.team_role_service)
    ctx = SimpleNamespace(
        bot=league.bot, db_path=league.db_path, kept=None, payload={}, tries=0,
        step_payload={"user_id": LEWIS, "revoke": take, "grant": give},
    )

    assert step.on_fault is not None and step.undiscardable is None
    if problem is None:
        with in_a_job():
            await step.run(ctx)
    else:
        with in_a_job(), pytest.raises(Exception) as raised:
            await step.run(ctx)
        ended = step.on_fault(ctx, raised.value)
        assert ended.result["problem"] == problem
    assert sorted(league.revoked.get(LEWIS, [])) == sorted(taken or [])
    assert sorted(league.granted.get(LEWIS, [])) == sorted(given or [])


_GONE, _ALSO_GONE = 999_998, 999_999


@pytest.mark.parametrize("gone, problem", [
    pytest.param(
        {_GONE}, f"the role <@&{_GONE}> could not be given to <@{LEWIS}>: it is no longer on the "
        "server", id="one role gone",
    ),
    pytest.param(
        {_GONE, _ALSO_GONE},
        f"the roles <@&{_GONE}> and <@&{_ALSO_GONE}> could not be given to <@{LEWIS}>: they are "
        "no longer on the server", id="two roles gone",
    ),
])
@pytest.mark.xfail(strict=True, reason=_NOT_YET_A_ROLE_SERVICE)
async def test_the_role_step_gives_what_remains_and_names_the_roles_gone(tmp_path, gone, problem):
    """The core specification's change of a driver's account gives the new account the roles the
    driver holds. The season is ongoing; Lewis holds Ferrari's role. A role step built to give what
    remains, with a reason of its own, is run to take Ferrari's role and give Am's and roles the
    league's server has deleted. Ferrari's role is taken and Am's given, each with the step's
    reason in Discord's audit log, and the step completes with a problem naming the roles gone, in
    the singular or the plural."""
    from leaguebot.core.services.role_moves import role_move_step
    from tests.support.change_queue import in_a_job

    league = await ongoing_league(tmp_path)
    league.roles_held[LEWIS] = {FERRARI_ROLE}
    league.roles_gone.update(gone)
    step = role_move_step(league.bot.team_role_service, reason="Because", give_what_remains=True)
    ctx = SimpleNamespace(
        bot=league.bot, db_path=league.db_path, kept=None, payload={}, tries=0,
        step_payload={
            "user_id": LEWIS, "revoke": [FERRARI_ROLE], "grant": [AM_ROLE, *sorted(gone)],
        },
    )

    with in_a_job():
        done = await step.run(ctx)

    assert done.result == {"problem": problem}
    assert league.revoked[LEWIS] == [FERRARI_ROLE] and league.granted[LEWIS] == [AM_ROLE]
    assert league.revoke_reasons[LEWIS] == ["Because"]
    assert league.grant_reasons[LEWIS] == ["Because"]


@pytest.mark.xfail(strict=True, reason=_NOT_YET_A_ROLE_SERVICE)
async def test_the_role_step_gives_nothing_where_a_role_is_gone_unless_built_to_give_what_remains(
    tmp_path,
):
    """A role step built without giving what remains, as a placement's: one of the roles to give
    has been deleted from the league's server, and the step ends on a fault with nothing given."""
    from leaguebot.core.services.role_moves import role_move_step
    from tests.support.change_queue import in_a_job

    league = await ongoing_league(tmp_path)
    league.roles_gone.add(_GONE)
    step = role_move_step(league.bot.team_role_service)
    ctx = SimpleNamespace(
        bot=league.bot, db_path=league.db_path, kept=None, payload={}, tries=0,
        step_payload={"user_id": LEWIS, "revoke": [], "grant": [AM_ROLE, _GONE]},
    )

    with in_a_job(), pytest.raises(Exception):
        await step.run(ctx)
    assert LEWIS not in league.granted
