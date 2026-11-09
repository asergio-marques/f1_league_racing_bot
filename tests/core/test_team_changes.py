"""`/team add`, `modify`, `remove` and `reserve-role` on the change queue (#283).

Each is a change on the league's lane: the team named at the ask, the queue's acknowledgement its
reply, and one save, stamped with the queue's clock, writing the team's row of the server's list,
its role mapping, an audit entry for each and the line. Every refusal is the command's at the ask
and is judged again as the change starts; one found inside the save ends the change in its words,
writing nothing, and the lane goes on.

These run on `configuring_league`'s season 3, in Configuration, where the team list is open. The
server's list holds Ferrari ("Scuderia Ferrari", role 811), McLaren ("McLaren F1 Team", role 812)
and the Reserve team. Admin (77) asks every change, as a league manager for `/team add`,
`/team modify` and `/team reserve-role` and as a league admin for `/team remove`. Alpine's role (813) and Haas's (814) are
on the server, held by no team, and the bot can grant all four roles. Where a team's role moves
the drivers seated in it, they run on `ongoing_league`'s season instead, with the same list.
"""
from __future__ import annotations

import dataclasses

import pytest

from tests.core.test_driver_move import _ADMIN, _as_logged

#: Why each test below fails until the build.
_ON_THE_QUEUE = "#283: /team add and remove are not yet changes on the queue"

ALPINE_ROLE, HAAS_ROLE = 813, 814
_ADD_FORM = "the “Add a team” form"
_FIXED = (
    "⛔ The team list is fixed for Season 3 now that its configuration has been confirmed. "
    "`/team {command}` is available again once the season has ended, or while a new season is in "
    "configuration."
)
_PROTECTED = '⛔ The team name "Reserve" is protected and cannot be managed.'


def _held_by(role_id: int, team: str) -> str:
    return f'⛔ <@&{role_id}> is already the role of "{team}". A role belongs to one team only.'


async def _team_league(tmp_path):
    """The league of the module docstring."""
    from tests.support.season_league import configuring_league

    return await _the_server_list(await configuring_league(tmp_path))


async def _the_server_list(league):
    """*league* given the module docstring's server list and its four roles, grantable."""
    for name, full_name in (("Ferrari", "Scuderia Ferrari"), ("McLaren", "McLaren F1 Team")):
        await league.write(
            "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, 2, 0)",
            name, full_name,
        )
    await league.write(
        "INSERT OR IGNORE INTO default_teams (name, full_name, max_seats, is_reserve) "
        "VALUES ('Reserve', 'Reserve', -1, 1)"
    )
    for role_id, name in ((811, "Ferrari"), (812, "McLaren"), (ALPINE_ROLE, "Alpine"),
                          (HAAS_ROLE, "Haas")):
        _grantable(league, role_id, name)
    return league


def _grantable(league, role_id: int, name: str):
    """Role *role_id*, named *name*, on the server and one the bot can grant."""
    role = league._role(role_id)
    role.name = name
    role.mention = f"<@&{role_id}>"
    role.managed = False
    role.is_default.return_value = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True
    return role


async def _server_list(league):
    """The server's team list: each team's shorthand, full name and mapped role, by shorthand."""
    rows = await league.rows(
        "SELECT dt.name, dt.full_name, trc.role_id FROM default_teams dt "
        "LEFT JOIN team_role_configs trc ON trc.team_name = dt.name ORDER BY dt.name"
    )
    return {row["name"]: (row["full_name"], row["role_id"]) for row in rows}


async def _mappings(league):
    """Every role mapping, team by team."""
    rows = await league.rows("SELECT team_name, role_id FROM team_role_configs ORDER BY team_name")
    return {row["team_name"]: row["role_id"] for row in rows}


async def _division_teams(league):
    """Every division's teams: a `/team` command changes the server's list alone."""
    return await league.rows("SELECT * FROM team_instances ORDER BY id")


async def _team_audits(league):
    from tests.support.settings_league import audit_rows

    return [
        row for row in await audit_rows(league.db_path) if row["change_type"].startswith("TEAM_")
    ]


def _refusal(what: str, reply: str) -> str:
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ {what} refused for {_ADMIN} — {_as_logged(reply_reason(reply))}"


def _team_lines(lines, command: str):
    return [line for line in lines if command in line]


def _check_then(league, kind: str, then):
    """Register *kind* again with a check that, once it has passed for the first change asked,
    runs *then* on the league: between the check and the save."""
    queue = league.bot.change_queue
    [change] = [each for each in queue.types if each.kind == kind]
    done: list[bool] = []

    async def _check(ctx):
        verdict = await change.check(ctx)
        if not done:
            done.append(True)
            await then(league)
        return verdict

    queue.register(dataclasses.replace(change, check=_check))
    return done


# ── /team add ─────────────────────────────────────────────────────────────────────────


async def _ask_add(league, shorthand: str = "Alpine", full_name: str = "Alpine Racing",
                   role_id: int = ALPINE_ROLE, *, role=None):
    """The "Add a team" form submitted by Admin with *shorthand*, *full_name* and the role
    *role_id* (or the double *role*): the interaction. The queue is not run."""
    from leaguebot.core.cogs.team_cog import TeamCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction

    asked = interaction(
        league.bot, None, user=tier_member("manager", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await TeamCog(league.bot).add_team(
        asked, shorthand=shorthand, full_name=full_name,
        role=role if role is not None else league._role(role_id), what=_ADD_FORM,
    )
    return asked


def _adding(full_name: str = "Alpine Racing") -> str:
    """How the queue words `/team add` of *full_name*."""
    return f"Adding **{full_name}**"


def _added(shorthand: str = "Alpine", full_name: str = "Alpine Racing",
           role_id: int = ALPINE_ROLE) -> str:
    """`/team add`'s reply once done: today's."""
    return f'✅ Team "{full_name}" added as `{shorthand}`, with role <@&{role_id}>.'


def _add_line(shorthand: str = "Alpine", full_name: str = "Alpine Racing",
              role_id: int = ALPINE_ROLE, role_name: str = "Alpine") -> str:
    """`/team add`'s line, word for word: today's, the member named as the queue names them."""
    return (
        f"{_ADMIN} | /team add | Success\n  team: {full_name}\n  shorthand: {shorthand}\n"
        f"  role: {role_name} (`<@&{role_id}>`)"
    )


_STANDING = {
    "Ferrari": ("Scuderia Ferrari", 811), "McLaren": ("McLaren F1 Team", 812),
    "Reserve": ("Reserve", None),
}


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_team_add_its_row_mapping_audit_entries_and_line_are_one_save(tmp_path):
    """The core specification's team list, and its record of what changed. The season is in
    Configuration. Admin submits the "Add a team" form with the shorthand Alpine, the full name
    "Alpine Racing" and the role 813. They are answered at once with the queue's acknowledgement,
    "Adding **Alpine Racing**", naming its first job, and nothing is written until the queue runs
    the change, of kind `core.team_add`. Once it has, the server's list holds Alpine, "Alpine
    Racing", mapped to 813; two audit entries, by Admin, on no division, stamped with the queue's
    clock, record the team added (`TEAM_ADD`: the shorthand and full name) and then its role
    (`TEAM_ROLE_CONFIG`: Alpine, 813, where it had none); the log channel holds today's success
    line once; no role is given or taken on Discord; no division's teams change, the server's
    list alone being the command's; and the acknowledgement is updated to today's reply. Takes
    the place of `test_team_cog.py`'s mocked "with role, no season" and "in
    configuration" tests and `test_team_modals.py`'s "submitting the form records all three"."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _team_league(tmp_path)
    divisions = await _division_teams(league)
    asked = await _ask_add(league)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_adding())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.team_add"]
    assert await _server_list(league) == _STANDING
    await run_queue(league.bot)

    assert await _server_list(league) == {**_STANDING, "Alpine": ("Alpine Racing", ALPINE_ROLE)}
    added, mapped = await _team_audits(league)
    assert (added["change_type"], added["actor_id"], added["division_id"]) == ("TEAM_ADD", 77, None)
    assert added["new_value"] == {"shorthand": "Alpine", "full_name": "Alpine Racing"}
    assert mapped["change_type"] == "TEAM_ROLE_CONFIG"
    assert mapped["old_value"] == {"team": "Alpine", "role_id": None}
    assert mapped["new_value"] == {"team": "Alpine", "role_id": ALPINE_ROLE}
    assert added["timestamp"] == mapped["timestamp"] == league.clock.now.isoformat()
    assert _team_lines(await log_lines(league), "/team add") == [_add_line()]
    assert league.granted == {} and league.revoked == {}
    assert await _division_teams(league) == divisions
    assert last_said(asked) == _added()


async def _configuration_confirmed(league):
    await league.write("UPDATE seasons SET stage = 'SIGNUPS'")


def _unmanaged(league):
    """Alpine's role, managed by an integration."""
    role = _grantable(league, ALPINE_ROLE, "Alpine")
    role.managed = True
    return role


@pytest.mark.parametrize(
    "setup, shorthand, full_name, role_id, reply",
    [
        pytest.param(
            _configuration_confirmed, "Alpine", "Alpine Racing", ALPINE_ROLE,
            _FIXED.format(command="add"), id="the-team-list-fixed",
        ),
        pytest.param(
            "managed", "Alpine", "Alpine Racing", ALPINE_ROLE,
            "⛔ <@&813> is managed by an integration — a bot, a subscription or a linked role — "
            "so Discord will not let me grant it. Choose a role of the team's own.",
            id="a-role-the-bot-cannot-grant",
        ),
        pytest.param(
            None, "Alpine", "Alpine Racing", 811, _held_by(811, "Ferrari"),
            id="a-role-another-team-holds",
        ),
        pytest.param(None, "Reserve", "Reserve Racing", ALPINE_ROLE, _PROTECTED,
                     id="the-reserve-name"),
        pytest.param(
            None, "Ferrari", "Ferrari Racing", ALPINE_ROLE,
            '⛔ A default team named "Ferrari" already exists.', id="a-shorthand-taken",
        ),
        pytest.param(
            None, "Alpine", "Scuderia Ferrari", ALPINE_ROLE,
            '⛔ "Scuderia Ferrari" is already the full name of a team. A full name names one team '
            "only.",
            id="a-full-name-taken",
        ),
    ],
)
async def test_a_team_add_is_refused_at_the_ask(tmp_path, setup, shorthand, full_name, role_id,
                                               reply):
    """The season is in Configuration. Admin submits the "Add a team" form where it cannot be
    done: once the season's configuration is confirmed (the season in Signups); with a role managed
    by an integration; with Ferrari's role, 811; under the Reserve team's name; under Ferrari's
    shorthand; or under Ferrari's full name. They are refused at once, in today's words, and the
    log channel takes the refusal, naming the form, as its one line. Nothing is asked of the queue,
    the server's list and its role mappings stand, and no audit entry is written. Takes the place
    of `test_team_cog.py`'s mocked "refused once the configuration is confirmed", "duplicate name"
    and "not added under a role another team holds" tests, `test_team_modals.py`'s "submitted after
    the list was fixed", `test_team_role_grantable.py`'s "adding a team refuses a role the bot
    cannot grant", and `test_team_cog_refusals.py`'s mocked "add form" cases: "team list fixed",
    "role cannot be granted", "role held" and "refused by the service"."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    role = _unmanaged(league) if setup == "managed" else None
    if callable(setup):
        await setup(league)
    asked = await _ask_add(league, shorthand, full_name, role_id, role=role)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_refusal(_ADD_FORM, reply)]
    assert await change_rows(league.db_path) == []
    assert await _server_list(league) == _STANDING
    assert await _team_audits(league) == []


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_failed_team_add_changes_nothing(tmp_path):
    """The season is in Configuration. Admin submits the "Add a team" form for Alpine, and as the
    queue runs it the database fails writing the audit entries. Nothing of it is saved, the row,
    its mapping, the entries and the line being one save: the server's list stands without Alpine,
    no mapping names 813, no audit entry is written and the log channel holds no success line. The
    league's lane stops at the job. Once a league admin discards it, the reply says "Nothing was
    added: "Alpine Racing" is not in the server's team list. Run `/team add` again."; and the lane
    is clear. Takes the place of the compensating delete `test_team_cog.py`'s "role taken at the
    last moment" pinned: the save writes both or neither."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import fault_in, last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_add(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _server_list(league) == _STANDING
    assert await _team_audits(league) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        'Nothing was added: "Alpine Racing" is not in the server\'s team list. Run `/team add` '
        "again."
    )
    assert await stopped_job(league.db_path) is None


async def _mclaren_takes_alpine_s_role(league):
    await league.write("UPDATE team_role_configs SET role_id = ? WHERE team_name = 'McLaren'",
                       ALPINE_ROLE)


async def _alpine_added_meanwhile(league):
    await league.write(
        "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
        "VALUES ('Alpine', 'Alpine F1', 2, 0)"
    )


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "meanwhile, reply, standing",
    [
        pytest.param(_configuration_confirmed, _FIXED.format(command="add"), _STANDING,
                     id="the-team-list-fixed"),
        pytest.param(
            _mclaren_takes_alpine_s_role, _held_by(ALPINE_ROLE, "McLaren"),
            {**_STANDING, "McLaren": ("McLaren F1 Team", ALPINE_ROLE)},
            id="the-role-taken-by-another-team",
        ),
        pytest.param(
            _alpine_added_meanwhile, '⛔ A default team named "Alpine" already exists.',
            {**_STANDING, "Alpine": ("Alpine F1", None)}, id="the-shorthand-taken",
        ),
    ],
)
async def test_a_team_add_is_judged_again_as_it_starts(tmp_path, meanwhile, reply, standing):
    """The season is in Configuration. Admin submits the "Add a team" form for Alpine, role 813,
    and is acknowledged; before the queue runs it, the season's configuration is confirmed,
    McLaren is given 813, or another team takes the shorthand Alpine. The queue runs. The add is
    refused as it starts, in the words it would meet at the ask, its acknowledgement updated to
    them. The server's list stands as the meanwhile left it, no audit entry is written, the log
    channel holds the refusal, naming the form, alone, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_add(league)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(asked) == reply
    assert await _server_list(league) == standing
    assert await _team_audits(league) == []
    assert await log_lines(league) == [_refusal(_ADD_FORM, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_refusal_found_inside_the_save_ends_the_team_add_and_the_lane_goes_on(tmp_path):
    """The one-save decision: the refusals are read again inside the save, so a role taken between
    the check and the write cannot leave a team behind without one. The season is in
    Configuration. Admin submits the "Add a team" form for Alpine (813), then for Haas ("Haas F1
    Team", 814), before the queue runs. As Alpine's change starts its check passes, and McLaren is
    then given 813 before its save. Alpine's add ends in today's words, '⛔ <@&813> is already the
    role of "McLaren". A role belongs to one team only.', its acknowledgement updated to them, and
    the log channel takes that refusal as a line: no Alpine row, no mapping of its own, no audit
    entry. The lane is not stopped, and Haas's add, asked after, goes through: Haas is on the list
    with 814, and its success line follows Alpine's refusal. Takes the place of `test_team_cog.py`'s
    "a role taken at the last moment takes the new team away again" and `test_team_cog_refusals.py`'s
    mocked "add form role taken at the last moment"."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    alpine = await _ask_add(league)
    haas = await _ask_add(league, "Haas", "Haas F1 Team", HAAS_ROLE)
    taken = _check_then(league, "core.team_add", _mclaren_takes_alpine_s_role)
    await run_queue(league.bot)

    assert taken == [True]
    reply = _held_by(ALPINE_ROLE, "McLaren")
    assert last_said(alpine) == reply
    assert await _server_list(league) == {
        **_STANDING, "McLaren": ("McLaren F1 Team", ALPINE_ROLE),
        "Haas": ("Haas F1 Team", HAAS_ROLE),
    }
    assert [row["new_value"] for row in await _team_audits(league)] == [
        {"shorthand": "Haas", "full_name": "Haas F1 Team"}, {"team": "Haas", "role_id": HAAS_ROLE},
    ]
    assert [
        line for line in await log_lines(league) if "/team add" in line or _ADD_FORM in line
    ] == [_refusal(_ADD_FORM, reply), _add_line("Haas", "Haas F1 Team", HAAS_ROLE, "Haas")]
    assert await stopped_job(league.db_path) is None
    assert last_said(haas) == _added("Haas", "Haas F1 Team", HAAS_ROLE)


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_team_add_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The core specification's "How a change is carried out": a request repeating a change that
    has not started, with nothing else asked for since on its lane, is refused, saying so. The
    season is in Configuration. Admin submits the "Add a team" form for Alpine and is acknowledged;
    before the queue runs it, they submit it again, the same. The second is refused at once, in the
    queue's words, and the log channel takes that refusal as a line; only the one change is queued.
    The queue runs it: Alpine is on the list once, with one `TEAM_ADD` entry."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import said
    from tests.support.setup_rounds import repeated

    league = await _team_league(tmp_path)
    first, repeat = await _ask_add(league), await _ask_add(league)

    assert said(repeat) == [repeated(_adding())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert (await _server_list(league))["Alpine"] == ("Alpine Racing", ALPINE_ROLE)
    assert [row["change_type"] for row in await _team_audits(league)] == [
        "TEAM_ADD", "TEAM_ROLE_CONFIG",
    ]
    assert said(first)[-1] == _added()


# ── /team remove ──────────────────────────────────────────────────────────────────────


async def _ask_remove(league, name: str = "McLaren"):
    """`/team remove` of *name*, as the league admin Admin runs it: the interaction. The queue is
    not run."""
    from leaguebot.core.cogs.team_cog import TeamCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "team remove",
        user=tier_member("admin", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    await undecorate(TeamCog.team_remove)(TeamCog(league.bot), asked, name=name)
    return asked


def _removing(full_name: str = "McLaren F1 Team") -> str:
    return f"Removing **{full_name}**"


def _removed(full_name: str = "McLaren F1 Team") -> str:
    return f'✅ Team "{full_name}" removed from the server list.'


def _remove_line(shorthand: str = "McLaren", full_name: str = "McLaren F1 Team") -> str:
    return f"{_ADMIN} | /team remove | Success\n  team: {full_name}\n  shorthand: {shorthand}"


_REMOVE = "`/team remove`"
_NO_MCLAREN = "⛔ No team in the server's list has the shorthand `McLaren`."


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_team_remove_its_row_mapping_audit_entries_and_line_are_one_save(tmp_path):
    """The season is in Configuration. The league admin Admin runs `/team remove` of McLaren. They
    are answered at once with the queue's acknowledgement, "Removing **McLaren F1 Team**", naming
    its first job, and nothing is written until the queue runs the change, of kind
    `core.team_remove`. Once it has, McLaren is gone from the server's list and its mapping to 812
    with it; two audit entries, by Admin, on no division, stamped with the queue's clock, record
    the team removed (`TEAM_REMOVE`: the shorthand and full name it had) and then its role
    (`TEAM_ROLE_CONFIG`: McLaren, 812, now none); the log channel holds today's success line once;
    no role is given or taken on Discord; no division's teams change; and the acknowledgement is
    updated to today's reply. Takes the place of `test_team_cog.py`'s mocked "no season removes
    from server only"."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _team_league(tmp_path)
    divisions = await _division_teams(league)
    asked = await _ask_remove(league)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_removing())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.team_remove"]
    assert await _server_list(league) == _STANDING
    await run_queue(league.bot)

    standing = dict(_STANDING)
    del standing["McLaren"]
    assert await _server_list(league) == standing
    assert "McLaren" not in await _mappings(league)
    removed, unmapped = await _team_audits(league)
    assert (removed["change_type"], removed["actor_id"], removed["division_id"]) == (
        "TEAM_REMOVE", 77, None,
    )
    assert removed["old_value"] == {"shorthand": "McLaren", "full_name": "McLaren F1 Team"}
    assert unmapped["change_type"] == "TEAM_ROLE_CONFIG"
    assert unmapped["old_value"] == {"team": "McLaren", "role_id": 812}
    assert unmapped["new_value"] == {"team": "McLaren", "role_id": None}
    assert removed["timestamp"] == unmapped["timestamp"] == league.clock.now.isoformat()
    assert _team_lines(await log_lines(league), "/team remove") == [_remove_line()]
    assert league.granted == {} and league.revoked == {}
    assert await _division_teams(league) == divisions
    assert last_said(asked) == _removed()


@pytest.mark.parametrize(
    "setup, name, reply",
    [
        pytest.param(_configuration_confirmed, "McLaren", _FIXED.format(command="remove"),
                     id="the-team-list-fixed"),
        pytest.param(None, "Ghost", "⛔ No team in the server's list has the shorthand `Ghost`.",
                     id="no-such-team"),
        pytest.param(None, "Reserve", _PROTECTED, id="the-reserve-team"),
    ],
)
async def test_a_team_remove_is_refused_at_the_ask(tmp_path, setup, name, reply):
    """The season is in Configuration. The league admin runs `/team remove` where it cannot be
    done: once the season's configuration is confirmed (the season in Signups); of Ghost, which no
    team is; or of the Reserve team. They are refused at once, in today's words, and the log
    channel takes the refusal as its one line. Nothing is asked of the queue, the server's list and
    its role mappings stand, and no audit entry is written. Takes the place of `test_team_cog.py`'s
    mocked "refused once the configuration is confirmed" and "not found" remove tests, and
    `test_team_cog_refusals.py`'s mocked "remove team list fixed", "remove unknown team" and
    "remove refused by the service"."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    if setup is not None:
        await setup(league)
    asked = await _ask_remove(league, name)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_refusal(_REMOVE, reply)]
    assert await change_rows(league.db_path) == []
    assert await _server_list(league) == _STANDING
    assert await _team_audits(league) == []


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_failed_team_remove_changes_nothing(tmp_path):
    """The season is in Configuration. The league admin runs `/team remove` of McLaren, and as the
    queue runs it the database fails writing the audit entries. Nothing is saved: McLaren is on the
    list with 812, no audit entry is written and the log channel holds no success line. The lane
    stops at the job. Once a league admin discards it, the reply says "Nothing was removed:
    "McLaren F1 Team" stays in the server's team list. Run `/team remove` again."; and the lane is
    clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import fault_in, last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_remove(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _server_list(league) == _STANDING
    assert await _team_audits(league) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        'Nothing was removed: "McLaren F1 Team" stays in the server\'s team list. Run '
        "`/team remove` again."
    )
    assert await stopped_job(league.db_path) is None


async def _mclaren_removed_meanwhile(league):
    await league.write("DELETE FROM team_role_configs WHERE team_name = 'McLaren'")
    await league.write("DELETE FROM default_teams WHERE name = 'McLaren'")


_WITHOUT_MCLAREN = {key: value for key, value in _STANDING.items() if key != "McLaren"}


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "meanwhile, reply, standing",
    [
        pytest.param(_configuration_confirmed, _FIXED.format(command="remove"), _STANDING,
                     id="the-team-list-fixed"),
        pytest.param(_mclaren_removed_meanwhile, _NO_MCLAREN, _WITHOUT_MCLAREN,
                     id="the-team-gone"),
    ],
)
async def test_a_team_remove_is_judged_again_as_it_starts(tmp_path, meanwhile, reply, standing):
    """The season is in Configuration. The league admin runs `/team remove` of McLaren and is
    acknowledged; before the queue runs it, the season's configuration is confirmed, or McLaren is
    taken off the list by other means. The queue runs. The remove is refused as it starts, in the
    words it would meet at the ask, its acknowledgement updated to them. The list stands as the
    meanwhile left it, no audit entry is written, the log channel holds the refusal alone, and the
    queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_remove(league)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(asked) == reply
    assert await _server_list(league) == standing
    assert await _team_audits(league) == []
    assert await log_lines(league) == [_refusal(_REMOVE, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_refusal_found_inside_the_save_ends_the_team_remove_and_the_lane_goes_on(
    tmp_path,
):
    """The season is in Configuration. The league admin runs `/team remove` of McLaren, then of
    Ferrari, before the queue runs. As McLaren's change starts its check passes, and McLaren is
    then taken off the list by other means before its save. McLaren's remove ends in the words the
    ask would give, "⛔ No team in the server's list has the shorthand `McLaren`.", its
    acknowledgement updated to them, and the log channel takes that refusal as a line: no audit
    entry of it. The lane is not
    stopped, and Ferrari's remove, asked after, goes through: Ferrari is off the list, its mapping
    with it, and its success line follows McLaren's refusal."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    mclaren = await _ask_remove(league)
    ferrari = await _ask_remove(league, "Ferrari")
    gone = _check_then(league, "core.team_remove", _mclaren_removed_meanwhile)
    await run_queue(league.bot)

    assert gone == [True]
    assert last_said(mclaren) == _NO_MCLAREN
    assert await _server_list(league) == {"Reserve": ("Reserve", None)}
    assert [row["old_value"] for row in await _team_audits(league)] == [
        {"shorthand": "Ferrari", "full_name": "Scuderia Ferrari"},
        {"team": "Ferrari", "role_id": 811},
    ]
    assert _team_lines(await log_lines(league), "/team remove") == [
        _refusal(_REMOVE, _NO_MCLAREN), _remove_line("Ferrari", "Scuderia Ferrari"),
    ]
    assert await stopped_job(league.db_path) is None
    assert last_said(ferrari) == _removed("Scuderia Ferrari")


@pytest.mark.xfail(strict=True, reason=_ON_THE_QUEUE)
async def test_a_team_remove_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The season is in Configuration. The league admin runs `/team remove` of McLaren and is
    acknowledged; before the queue runs it, they run it again. The second is refused at once, in
    the queue's words; only the one change is queued. The queue runs it: McLaren is off the list,
    with one `TEAM_REMOVE` entry."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import said
    from tests.support.setup_rounds import repeated

    league = await _team_league(tmp_path)
    first, repeat = await _ask_remove(league), await _ask_remove(league)

    assert said(repeat) == [repeated(_removing())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert "McLaren" not in await _server_list(league)
    assert [row["change_type"] for row in await _team_audits(league)] == [
        "TEAM_REMOVE", "TEAM_ROLE_CONFIG",
    ]
    assert said(first)[-1] == _removed()


# ── /team modify ──────────────────────────────────────────────────────────────────────

#: Why each `/team modify` test below fails until the build.
_MODIFY_ON_THE_QUEUE = "#283: /team modify is not yet a change on the queue"

_MODIFY_FORM = "the “Modify a team” form"
#: The reason Discord's audit log is given for a seated driver's roles moved by `/team modify`.
_MODIFY_REASON = "Team's role changed"
_NAMES_FIXED = (
    "⛔ The team list is fixed now that the season's configuration is confirmed, so a team's "
    "shorthand and full name cannot change. Only its role can, and nothing was written."
)
_GONE = '⛔ "McLaren F1 Team" is no longer in the server\'s team list.'
_SEASON_DONE = (
    "⛔ Every division of Season 3 is done, so `/team {command}` no longer has anything to act on "
    "— completing the season revokes the team roles. Repair the mapping once the season has ended."
)
_UNGRANTABLE = (
    "⛔ <@&813> is managed by an integration — a bot, a subscription or a linked role — so Discord "
    "will not let me grant it. Choose a role of the team's own. Nothing was written."
)


async def _ask_modify(league, team: str = "McLaren", *, meanwhile=None, shorthand=None,
                      full_name=None, role_id=None):
    """`/team modify` of *team* run by Admin, and the "Modify a team" form it opens submitted
    with *shorthand*, *full_name* and the role *role_id*, each left as the form offered it where
    not given: the form's interaction. *meanwhile* runs on the league while the form stands
    open. The queue is not run."""
    from unittest.mock import AsyncMock

    from leaguebot.core.cogs.team_cog import TeamCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    def _admin(command):
        asked = interaction(
            league.bot, command,
            user=tier_member("manager", member_id=ADMIN_ID, display_name="Admin"),
        )
        asked.guild = league.guild
        return asked

    cog = TeamCog(league.bot)
    opened = _admin("team modify")
    opened.response.send_modal = AsyncMock()
    await undecorate(TeamCog.team_modify)(cog, opened, team)
    form = opened.response.send_modal.await_args.args[0]
    if meanwhile is not None:
        await meanwhile(league)
    if form.shorthand is not None:
        form.shorthand._value = shorthand if shorthand is not None else form.shorthand.default
        form.full_name._value = full_name if full_name is not None else form.full_name.default
    [standing] = form.role.default_values
    form.role._values = [league._role(role_id if role_id is not None else standing.id)]
    submitted = _admin(None)
    await form.on_submit(submitted)
    return submitted


def _changing(full_name: str = "McLaren F1 Team") -> str:
    """How the queue words `/team modify` of the team *full_name* was as the form was submitted."""
    return f"Changing **{full_name}**"


def _modified(full_name: str, *changes: str) -> str:
    """`/team modify`'s reply once done, today's: the team's name as it now stands, then each
    change on a line of its own."""
    return "\n".join([f'✅ Team "{full_name}" updated.', *(f"  {each}" for each in changes)])


def _modify_line(full_name: str, shorthand: str, role_name: str, role_id: int,
                 moved: int = 0) -> str:
    """`/team modify`'s line, word for word: today's, the member named as the queue names them."""
    return (
        f"{_ADMIN} | /team modify | Success\n  team: {full_name}\n  shorthand: {shorthand}\n"
        f"  role: {role_name} (`<@&{role_id}>`)\n  seated drivers moved: {moved}"
    )


async def _pending_completion(league):
    """Every division of the season done: the season live, Pending completion."""
    await league.write("UPDATE seasons SET status = 'ACTIVE', stage = 'PENDING_COMPLETION'")


async def _ferrari_takes_alpine_s_role(league):
    await league.write("UPDATE team_role_configs SET role_id = ? WHERE team_name = 'Ferrari'",
                       ALPINE_ROLE)


async def _alpine_s_role_now_managed(league):
    _unmanaged(league)


async def _mcl_added_meanwhile(league):
    await league.write(
        "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
        "VALUES ('MCL', 'MCL Racing', 2, 0)"
    )


async def _mac_added_meanwhile(league):
    await league.write(
        "INSERT INTO default_teams (name, full_name, max_seats, is_reserve) "
        "VALUES ('Mac', 'McLaren Racing', 2, 0)"
    )


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
async def test_a_team_modify_its_row_mapping_audit_entries_and_line_are_one_save(tmp_path):
    """The core specification's team list, and its record of what changed. The season is in
    Configuration. Admin runs `/team modify` of McLaren and submits the "Modify a team" form with
    the shorthand MCL, the full name "McLaren Racing" and Alpine's role, 813. They are answered at
    once with the queue's acknowledgement, "Changing **McLaren F1 Team**", naming its first job,
    and nothing is written until the queue runs the change, of kind `core.team_modify`. Once it
    has, the server's list holds MCL, "McLaren Racing", mapped to 813, and no McLaren; two audit
    entries, by Admin, on no division, stamped with the queue's clock, record the names
    (`TEAM_NAMES`: McLaren, "McLaren F1 Team" to MCL, "McLaren Racing") and then the role
    (`TEAM_ROLE_CONFIG`: McLaren, 812 to MCL, 813), one entry for the mapping; the log channel
    holds today's success line once, no seated driver moved; no role is given or taken on
    Discord, the season not being live; no division's teams change; and the acknowledgement is
    updated to today's reply, naming both names' changes, the artwork's new filename and the role.
    Takes the place of `test_team_modals.py`'s mocked "changing both names records them and names
    the artwork file"."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _team_league(tmp_path)
    divisions = await _division_teams(league)
    asked = await _ask_modify(league, shorthand="MCL", full_name="McLaren Racing",
                              role_id=ALPINE_ROLE)

    first_job = (await step_rows(league.db_path))[0]["id"]
    assert said(asked)[0] == f"{acknowledged(_changing())} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == ["core.team_modify"]
    assert await _server_list(league) == _STANDING
    await run_queue(league.bot)

    assert await _server_list(league) == {
        **_WITHOUT_MCLAREN, "MCL": ("McLaren Racing", ALPINE_ROLE),
    }
    named, mapped = await _team_audits(league)
    assert (named["change_type"], named["actor_id"], named["division_id"]) == (
        "TEAM_NAMES", 77, None,
    )
    assert named["old_value"] == {"shorthand": "McLaren", "full_name": "McLaren F1 Team"}
    assert named["new_value"] == {"shorthand": "MCL", "full_name": "McLaren Racing"}
    assert mapped["change_type"] == "TEAM_ROLE_CONFIG"
    assert mapped["old_value"] == {"team": "McLaren", "role_id": 812}
    assert mapped["new_value"] == {"team": "MCL", "role_id": ALPINE_ROLE}
    assert named["timestamp"] == mapped["timestamp"] == league.clock.now.isoformat()
    assert _team_lines(await log_lines(league), "/team modify") == [
        _modify_line("McLaren Racing", "MCL", "Alpine", ALPINE_ROLE),
    ]
    assert league.granted == {} and league.revoked == {}
    assert await _division_teams(league) == divisions
    assert last_said(asked) == _modified(
        "McLaren Racing",
        'Full name: "McLaren F1 Team" → "McLaren Racing"',
        "Shorthand: `McLaren` → `MCL`",
        "Its artwork is now looked for as `mcl`, not `mclaren` — rename the file in the team "
        "image directory.",
        f"Role: <@&{ALPINE_ROLE}>",
    )


@pytest.mark.parametrize(
    "meanwhile, fields, reply, standing",
    [
        pytest.param(
            _mclaren_removed_meanwhile, {"full_name": "McLaren Racing"}, _GONE, _WITHOUT_MCLAREN,
            id="the-team-gone-while-the-form-stood-open",
        ),
        pytest.param(
            _configuration_confirmed, {"full_name": "McLaren Racing", "role_id": ALPINE_ROLE},
            _NAMES_FIXED, _STANDING, id="names-changed-after-the-list-was-fixed",
        ),
        pytest.param(
            _alpine_s_role_now_managed, {"role_id": ALPINE_ROLE}, _UNGRANTABLE, _STANDING,
            id="a-role-the-bot-cannot-grant",
        ),
        pytest.param(
            None, {"role_id": 811}, f"{_held_by(811, 'Ferrari')} Nothing was written.",
            _STANDING, id="a-role-another-team-holds",
        ),
        pytest.param(
            None, {"shorthand": "Ferrari"}, '⛔ A default team named "Ferrari" already exists.',
            _STANDING, id="a-shorthand-taken",
        ),
        pytest.param(
            None, {"full_name": "Scuderia Ferrari"},
            '⛔ "Scuderia Ferrari" is already the full name of a team. A full name names one team '
            "only.",
            _STANDING, id="a-full-name-taken",
        ),
        pytest.param(
            None, {"shorthand": "Ferrari", "role_id": ALPINE_ROLE},
            '⛔ A default team named "Ferrari" already exists.', _STANDING,
            id="a-shorthand-taken-with-a-new-role",
            marks=pytest.mark.xfail(
                strict=True, reason="#283: a submission refused for its names saves its role"
            ),
        ),
        pytest.param(
            _pending_completion, {"role_id": ALPINE_ROLE}, _SEASON_DONE.format(command="modify"),
            _STANDING, id="the-season-pending-completion",
            marks=pytest.mark.xfail(
                strict=True,
                reason="#283: /team modify's form is not refused once the season is pending "
                "completion",
            ),
        ),
    ],
)
async def test_a_team_modify_is_refused_at_the_ask(tmp_path, meanwhile, fields, reply, standing):
    """The season is in Configuration. Admin runs `/team modify` of McLaren, and the "Modify a
    team" form is submitted where it cannot be done: McLaren taken off the list while the form
    stood open; a full name changed, with Alpine's role, once the season's configuration was
    confirmed while it stood open, which writes not even the role; Alpine's role, which an integration manages; Ferrari's role, 811; Ferrari's shorthand;
    Ferrari's full name; Ferrari's shorthand together with Alpine's role, which #490 saw save the
    role though the names were refused; or Alpine's role once every division is done (the season
    Pending completion while the form stood open). They are refused at once, in today's words, and
    the log channel takes the refusal, naming the form, as its one line. Nothing is asked of the
    queue: the server's list and its mappings stand as they were before the form was submitted,
    no audit entry is written, and no role is given or taken on Discord. Takes the place of
    `test_team_modals.py`'s mocked "name changed after the list was fixed", "role another team
    holds" and "team removed while the form stood open", and `test_team_cog_refusals.py`'s five
    mocked "modify form" cases."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_modify(league, meanwhile=meanwhile, **fields)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_refusal(_MODIFY_FORM, reply)]
    assert await change_rows(league.db_path) == []
    assert await _server_list(league) == standing
    assert await _team_audits(league) == []
    assert league.granted == {} and league.revoked == {}


async def test_a_team_modify_submitted_untouched_is_answered_with_its_line_alone(tmp_path):
    """The season is in Configuration. Admin runs `/team modify` of McLaren and submits the form
    as it opened, every field as it was. They are told 'Nothing changed: "McLaren F1 Team" stands
    as it was.', and the log channel takes one line saying nothing changed: "/team modify |
    Nothing changed", the team, and "the form was submitted as it stood". Nothing is asked of the
    queue, the list stands and no audit entry is written. Takes the place of
    `test_team_modals.py`'s mocked "form submitted untouched changes nothing", and of
    `test_driver_move.py`'s "the same role or no guild changes nothing"."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_modify(league)

    assert last_said(asked) == 'Nothing changed: "McLaren F1 Team" stands as it was.'
    assert await log_lines(league) == [
        f"{_ADMIN} | /team modify | Nothing changed\n  team: McLaren F1 Team\n"
        "  the form was submitted as it stood"
    ]
    assert await change_rows(league.db_path) == []
    assert await _server_list(league) == _STANDING
    assert await _team_audits(league) == []


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
async def test_a_failed_team_modify_changes_nothing(tmp_path):
    """The season is in Configuration. Admin submits the "Modify a team" form of McLaren with the
    shorthand MCL, the full name "McLaren Racing" and the role 813, and as the queue runs it the
    database fails writing the audit entries. Nothing of it is saved, the names, the mapping, the
    entries and the line being one save: McLaren stands as it was, with 812, no audit entry is
    written and the log channel holds no success line. The lane stops at the job. Once a league
    admin discards it, the reply says "Nothing was changed: "McLaren F1 Team" stands as it was.
    Run `/team modify` again."; and the lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import fault_in, last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_modify(league, shorthand="MCL", full_name="McLaren Racing",
                              role_id=ALPINE_ROLE)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _server_list(league) == _STANDING
    assert await _team_audits(league) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        'Nothing was changed: "McLaren F1 Team" stands as it was. Run `/team modify` again.'
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "fields, meanwhile, reply, standing",
    [
        pytest.param(
            {"full_name": "McLaren Racing"}, _configuration_confirmed, _NAMES_FIXED, _STANDING,
            id="the-team-list-fixed",
        ),
        pytest.param(
            {"full_name": "McLaren Racing"}, _mclaren_removed_meanwhile, _GONE, _WITHOUT_MCLAREN,
            id="the-team-gone",
        ),
        pytest.param(
            {"role_id": ALPINE_ROLE}, _ferrari_takes_alpine_s_role,
            f"{_held_by(ALPINE_ROLE, 'Ferrari')} Nothing was written.",
            {**_STANDING, "Ferrari": ("Scuderia Ferrari", ALPINE_ROLE)},
            id="the-role-taken-by-another-team",
        ),
        pytest.param(
            {"role_id": ALPINE_ROLE}, _alpine_s_role_now_managed, _UNGRANTABLE, _STANDING,
            id="the-role-no-longer-grantable",
        ),
        pytest.param(
            {"shorthand": "MCL"}, _mcl_added_meanwhile,
            '⛔ A default team named "MCL" already exists.',
            {**_STANDING, "MCL": ("MCL Racing", None)}, id="the-shorthand-taken",
        ),
        pytest.param(
            {"full_name": "McLaren Racing"}, _mac_added_meanwhile,
            '⛔ "McLaren Racing" is already the full name of a team. A full name names one team '
            "only.",
            {**_STANDING, "Mac": ("McLaren Racing", None)}, id="the-full-name-taken",
        ),
        pytest.param(
            {"role_id": ALPINE_ROLE}, _pending_completion, _SEASON_DONE.format(command="modify"),
            _STANDING, id="the-season-pending-completion",
        ),
    ],
)
async def test_a_team_modify_is_judged_again_as_it_starts(tmp_path, fields, meanwhile, reply,
                                                         standing):
    """The season is in Configuration. Admin submits the "Modify a team" form of McLaren, changing
    its full name to "McLaren Racing", its shorthand to MCL, or its role to Alpine's, 813, and is
    acknowledged; before the queue runs it, the season's configuration is confirmed, McLaren is
    taken off the list by other means, Ferrari is given 813, an integration takes over 813,
    another team takes the shorthand MCL or the full name "McLaren Racing", or every division is
    done (the season Pending completion). The queue runs. The change is refused as it starts, in
    the words it would meet at the ask, its acknowledgement updated to them. The server's list
    stands as the meanwhile left it, no audit entry is written, no role is given or taken, the log
    channel holds the refusal, naming the form, alone, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    asked = await _ask_modify(league, **fields)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(asked) == reply
    assert await _server_list(league) == standing
    assert await _team_audits(league) == []
    assert league.granted == {} and league.revoked == {}
    assert await log_lines(league) == [_refusal(_MODIFY_FORM, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
async def test_a_refusal_found_inside_the_save_ends_the_team_modify_and_the_lane_goes_on(
    tmp_path,
):
    """The season is in Configuration. Admin submits the "Modify a team" form of McLaren, changing
    its full name to "McLaren Racing", then that of Ferrari, changing its role to Haas's, 814,
    before the queue runs. As McLaren's change starts its check passes, and McLaren is then taken
    off the list by other means before its save. McLaren's change ends in the words the ask would
    give, '⛔ "McLaren F1 Team" is no longer in the server's team list.', its acknowledgement
    updated to them, and the log channel takes that refusal as a line: no audit entry of it. The
    lane is not stopped, and Ferrari's change, asked after, goes through: Ferrari is mapped to 814,
    one `TEAM_ROLE_CONFIG` entry records it (Ferrari, 811 to 814) and no `TEAM_NAMES` entry, its
    names being unchanged, and its success line follows McLaren's refusal."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _team_league(tmp_path)
    mclaren = await _ask_modify(league, full_name="McLaren Racing")
    ferrari = await _ask_modify(league, "Ferrari", role_id=HAAS_ROLE)
    gone = _check_then(league, "core.team_modify", _mclaren_removed_meanwhile)
    await run_queue(league.bot)

    assert gone == [True]
    assert last_said(mclaren) == _GONE
    assert await _server_list(league) == {
        **_WITHOUT_MCLAREN, "Ferrari": ("Scuderia Ferrari", HAAS_ROLE),
    }
    assert [
        (row["change_type"], row["old_value"], row["new_value"])
        for row in await _team_audits(league)
    ] == [
        ("TEAM_ROLE_CONFIG", {"team": "Ferrari", "role_id": 811},
         {"team": "Ferrari", "role_id": HAAS_ROLE}),
    ]
    assert [
        line for line in await log_lines(league) if "/team modify" in line or _MODIFY_FORM in line
    ] == [
        _refusal(_MODIFY_FORM, _GONE),
        _modify_line("Scuderia Ferrari", "Ferrari", "Haas", HAAS_ROLE),
    ]
    assert await stopped_job(league.db_path) is None
    assert last_said(ferrari) == _modified("Scuderia Ferrari", f"Role: <@&{HAAS_ROLE}>")


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
async def test_a_team_modify_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The season is in Configuration. Admin submits the "Modify a team" form of McLaren with the
    shorthand MCL and the full name "McLaren Racing", and is acknowledged; before the queue runs
    it, they open the form again and submit it, the same. The second is refused at once, in the
    queue's words; only the one change is queued. The queue runs it: MCL is on the list, with one
    `TEAM_NAMES` entry."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import said
    from tests.support.setup_rounds import repeated

    league = await _team_league(tmp_path)
    fields = {"shorthand": "MCL", "full_name": "McLaren Racing"}
    first = await _ask_modify(league, **fields)
    repeat = await _ask_modify(league, **fields)

    assert said(repeat) == [repeated(_changing())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert (await _server_list(league))["MCL"] == ("McLaren Racing", 812)
    assert [row["change_type"] for row in await _team_audits(league)] == ["TEAM_NAMES"]
    assert said(first)[-1] == _modified(
        "McLaren Racing",
        'Full name: "McLaren F1 Team" → "McLaren Racing"',
        "Shorthand: `McLaren` → `MCL`",
        "Its artwork is now looked for as `mcl`, not `mclaren` — rename the file in the team "
        "image directory.",
    )


async def _seated_league(tmp_path):
    """`ongoing_league`'s live season with the module docstring's server list: Lewis (101) and
    Max (102) seated at Ferrari in Pro, each holding Pro's role (801) and Ferrari's (811), and
    Charles (103) at McLaren in Am, holding Am's (802) and McLaren's (812); every placement
    confirmed. The test driver Tester (104), who holds no role, is seated at McLaren too."""
    from tests.support.season_league import (
        AM_ROLE, CHARLES, LEWIS, MAX, PRO_ROLE, ongoing_league,
    )

    league = await _the_server_list(await ongoing_league(tmp_path, test_mode=True))
    for user_id in (LEWIS, MAX):
        league.roles_held[user_id] = {PRO_ROLE, 811}
    league.roles_held[CHARLES] = {AM_ROLE, 812}
    return league


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
async def test_a_team_modify_moves_its_seated_drivers_roles_after_the_save(tmp_path):
    """architecture.md, "A job that fails stops its lane": the roles of a team's members follow
    its change, one job for each member. The season is ongoing, so the form offers the role
    alone. Ferrari holds Lewis, his placement confirmed, and Max, his new placement not yet
    confirmed; the test driver Tester is moved into Ferrari too. Admin submits the "Modify a team"
    form of Ferrari with Alpine's role, 813. The queue runs it. Ferrari is mapped to 813, one
    `TEAM_ROLE_CONFIG` entry records it (Ferrari, 811 to 813), and then the queue moves Lewis's
    roles in a job of its own: 811 taken and 813 given, Discord's audit log told "Team's role
    changed" for each. Max, whose placement is unconfirmed, and Tester, a test driver holding no
    role, are left alone and get no job; McLaren's Charles is untouched. The reply is today's,
    naming the role and "1 seated driver(s) moved to the new role.", and the success line counts
    one seated driver moved. Takes the place of `test_team_modals.py`'s mocked "changing the role
    moves the drivers seated in the team" and `test_driver_move.py`'s "every confirmed driver of
    the team has the old role swapped for the new" and "an unconfirmed driver of the team is left
    alone"."""
    import json

    from tests.support.change_queue import run_queue, step_rows
    from tests.support.season_league import (
        FERRARI, LEWIS, MAX, PRO, TEST_DRIVER, profile_id,
    )
    from tests.support.settings_league import last_said, log_lines

    league = await _seated_league(tmp_path)
    await league.write(
        "UPDATE driver_season_assignments SET committed = 0 WHERE driver_profile_id = ?",
        profile_id(MAX),
    )
    await league.write(
        "UPDATE team_seats SET team_instance_id = ?, seat_number = 3 WHERE driver_profile_id = ?",
        FERRARI, profile_id(TEST_DRIVER),
    )
    await league.write(
        "UPDATE driver_season_assignments SET division_id = ? WHERE driver_profile_id = ?",
        PRO, profile_id(TEST_DRIVER),
    )
    asked = await _ask_modify(league, "Ferrari", role_id=ALPINE_ROLE)
    await run_queue(league.bot)

    assert (await _server_list(league))["Ferrari"] == ("Scuderia Ferrari", ALPINE_ROLE)
    assert [
        (row["change_type"], row["old_value"], row["new_value"])
        for row in await _team_audits(league)
    ] == [
        ("TEAM_ROLE_CONFIG", {"team": "Ferrari", "role_id": 811},
         {"team": "Ferrari", "role_id": ALPINE_ROLE}),
    ]
    assert [
        json.loads(row["payload"])["user_id"]
        for row in await step_rows(league.db_path) if row["name"] == "move_roles"
    ] == [LEWIS]
    assert league.revoked == {LEWIS: [811]}
    assert league.granted == {LEWIS: [ALPINE_ROLE]}
    assert league.revoke_reasons[LEWIS] == league.grant_reasons[LEWIS] == [_MODIFY_REASON]
    assert last_said(asked) == _modified(
        "Scuderia Ferrari", f"Role: <@&{ALPINE_ROLE}>", "1 seated driver(s) moved to the new role.",
    )
    assert _team_lines(await log_lines(league), "/team modify") == [
        _modify_line("Scuderia Ferrari", "Ferrari", "Alpine", ALPINE_ROLE, moved=1),
    ]


@pytest.mark.xfail(strict=True, reason=_MODIFY_ON_THE_QUEUE)
async def test_a_role_refused_after_a_team_modify_is_reported_and_the_queue_goes_on(tmp_path):
    """Decision 5: a role Discord refuses after a team's change is reported, and the queue goes
    on. The season is ongoing. Admin submits the "Modify a team" form of Ferrari with Alpine's
    role, 813, then that of McLaren with Haas's, 814, before the queue runs; Discord refuses to
    give Lewis any role (403 Missing Permissions). The queue runs both. Ferrari's change stands,
    saved before the roles were asked of Discord: Ferrari is mapped to 813; Lewis has 811 taken
    and nothing given; Max has 811 taken and 813 given. Ferrari's reply is today's, counting the
    one driver moved in full, then "⚠️ Done, but on Discord:" and a bullet, "the role <@&813>
    could not be given to <@101>: Discord refused or failed a request from the bot"; its success
    line counts one driver moved and carries a "not done:" line in the same words, its mentions in
    code. The lane is not stopped, and McLaren's change, asked after, is made in full: McLaren is
    mapped to 814, Charles has 812 taken and 814 given, Tester is left alone, and its reply is
    today's alone."""
    import discord

    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.season_league import CHARLES, LEWIS, MAX
    from tests.support.settings_league import last_said, log_lines

    league = await _seated_league(tmp_path)
    league.grant_fails[LEWIS] = http_error(discord.Forbidden, status=403,
                                           text="Missing Permissions")
    ferrari = await _ask_modify(league, "Ferrari", role_id=ALPINE_ROLE)
    mclaren = await _ask_modify(league, "McLaren", role_id=HAAS_ROLE)
    await run_queue(league.bot)

    problem = (
        f"the role <@&{ALPINE_ROLE}> could not be given to <@{LEWIS}>: Discord refused or failed "
        "a request from the bot"
    )
    assert await _mappings(league) == {"Ferrari": ALPINE_ROLE, "McLaren": HAAS_ROLE}
    assert league.revoked == {LEWIS: [811], MAX: [811], CHARLES: [812]}
    assert league.granted == {MAX: [ALPINE_ROLE], CHARLES: [HAAS_ROLE]}
    assert last_said(ferrari) == (
        _modified("Scuderia Ferrari", f"Role: <@&{ALPINE_ROLE}>",
                  "1 seated driver(s) moved to the new role.")
        + f"\n⚠️ Done, but on Discord:\n• {problem}"
    )
    assert _team_lines(await log_lines(league), "/team modify") == [
        _modify_line("Scuderia Ferrari", "Ferrari", "Alpine", ALPINE_ROLE, moved=1)
        + f"\n  not done: {_as_logged(problem)}",
        _modify_line("McLaren F1 Team", "McLaren", "Haas", HAAS_ROLE, moved=1),
    ]
    assert await stopped_job(league.db_path) is None
    assert last_said(mclaren) == _modified(
        "McLaren F1 Team", f"Role: <@&{HAAS_ROLE}>", "1 seated driver(s) moved to the new role.",
    )


# ── /team reserve-role ────────────────────────────────────────────────────────────────

#: Why each `/team reserve-role` test below fails until the build.
_RESERVE_ON_THE_QUEUE = "#283: /team reserve-role is not yet a change on the queue"

#: The Reserve team's role where a test gives it one, and the role a test moves it to.
RESERVE_ROLE, RESERVES_ROLE = 815, 816
_RESERVE_ROLE_COMMAND = "`/team reserve-role`"
#: The reason Discord's audit log is given for a seated driver's roles moved by
#: `/team reserve-role`.
_RESERVE_REASON = "Reserve team's role changed"


async def _ask_reserve_role(league, role_id: int | None = RESERVES_ROLE, *, role=None):
    """`/team reserve-role` run by Admin, as a league manager, with the role *role_id* (None:
    cleared) or the double *role*: the interaction. The queue is not run."""
    from leaguebot.core.cogs.team_cog import TeamCog
    from tests.support.change_queue import tier_member
    from tests.support.season_league import ADMIN_ID
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    asked = interaction(
        league.bot, "team reserve-role",
        user=tier_member("manager", member_id=ADMIN_ID, display_name="Admin"),
    )
    asked.guild = league.guild
    if role is None and role_id is not None:
        role = league._role(role_id)
    await undecorate(TeamCog.team_reserve_role)(TeamCog(league.bot), asked, role=role)
    return asked


def _setting(cleared: bool = False) -> str:
    """How the queue words `/team reserve-role`, setting the role or clearing it."""
    return f"{'Clearing' if cleared else 'Setting'} the Reserve team's role"


def _reserve_set(role_id: int | None = RESERVES_ROLE, moved: int = 0) -> str:
    """`/team reserve-role`'s reply once done, today's: the role set or cleared, and the count of
    seated drivers moved where any were."""
    said = (
        "✅ Reserve team role cleared." if role_id is None
        else f"✅ Reserve team role set to <@&{role_id}>."
    )
    return f"{said} {moved} seated driver(s) moved to the new role." if moved else said


def _reserve_line(role_name: str | None = "Reserves", role_id: int = RESERVES_ROLE) -> str:
    """`/team reserve-role`'s line, word for word: today's, the member named as the queue names
    them."""
    role = "cleared" if role_name is None else f"{role_name} (`<@&{role_id}>`)"
    return f"{_ADMIN} | /team reserve-role | Success\n  role: {role}"


async def _reserve_holds(league, role_id: int = RESERVE_ROLE):
    """The Reserve team mapped to *role_id*, a role on the server named "Reserve" that the bot
    can grant; the role `/team reserve-role` moves it to, 816, named "Reserves", grantable too."""
    await league.write(
        "INSERT INTO team_role_configs (team_name, role_id) VALUES ('Reserve', ?)", role_id
    )
    _grantable(league, role_id, "Reserve")
    _grantable(league, RESERVES_ROLE, "Reserves")
    return league


async def _reserve_team_league(tmp_path):
    """The module docstring's league, the Reserve team mapped to 815 (`_reserve_holds`)."""
    return await _reserve_holds(await _team_league(tmp_path))


_RESERVE_STANDING = {**_STANDING, "Reserve": ("Reserve", RESERVE_ROLE)}


async def _role_held_meanwhile(league):
    """Ferrari mapped to 816, the role the Reserve team is being given."""
    await league.write("UPDATE team_role_configs SET role_id = ? WHERE team_name = 'Ferrari'",
                       RESERVES_ROLE)


async def _reserves_role_now_managed(league):
    _grantable(league, RESERVES_ROLE, "Reserves").managed = True


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "role_id, role_name",
    [pytest.param(RESERVES_ROLE, "Reserves", id="set"), pytest.param(None, None, id="cleared")],
)
async def test_a_team_reserve_role_its_mapping_audit_entry_and_line_are_one_save(
    tmp_path, role_id, role_name,
):
    """The core specification's team list, and its record of what changed. The season is in
    Configuration, and the Reserve team is mapped to the role 815. Admin runs
    `/team reserve-role` with the role 816, "Reserves", or with no role, clearing it. They are
    answered at once with the queue's acknowledgement, "Setting the Reserve team's role" or
    "Clearing the Reserve team's role", naming its first job, and nothing is written until the
    queue runs the change, of kind `core.team_reserve_role`. Once it has, the Reserve team is
    mapped to 816, or to no role; one audit entry, by Admin, on no division, stamped with the
    queue's clock, records it (`TEAM_ROLE_CONFIG`: Reserve, 815 to 816 or to none), the team's
    row being unchanged; the log channel holds today's success line once, naming the role or
    "cleared"; no role is given or taken on Discord, no driver being seated in the Reserve team;
    no division's teams change; and the acknowledgement is updated to today's reply, "✅ Reserve
    team role set to <@&816>." or "✅ Reserve team role cleared.". Takes the place of
    `test_team_cog.py`'s mocked "set role calls set config" and "clear role calls delete config",
    and `test_team_lineup_command.py`'s "setting the reserve role is logged with the role" and
    "clearing the reserve role is logged as cleared"."""
    from tests.support.change_queue import change_rows, run_queue, step_rows
    from tests.support.settings_league import last_said, log_lines, said
    from tests.support.setup_rounds import acknowledged

    league = await _reserve_team_league(tmp_path)
    divisions = await _division_teams(league)
    asked = await _ask_reserve_role(league, role_id)

    first_job = (await step_rows(league.db_path))[0]["id"]
    cleared = role_id is None
    assert said(asked)[0] == f"{acknowledged(_setting(cleared))} It begins with job #{first_job}."
    assert [row["kind"] for row in await change_rows(league.db_path)] == [
        "core.team_reserve_role",
    ]
    assert await _server_list(league) == _RESERVE_STANDING
    await run_queue(league.bot)

    assert await _server_list(league) == {**_STANDING, "Reserve": ("Reserve", role_id)}
    [mapped] = await _team_audits(league)
    assert (mapped["change_type"], mapped["actor_id"], mapped["division_id"]) == (
        "TEAM_ROLE_CONFIG", 77, None,
    )
    assert mapped["old_value"] == {"team": "Reserve", "role_id": RESERVE_ROLE}
    assert mapped["new_value"] == {"team": "Reserve", "role_id": role_id}
    assert mapped["timestamp"] == league.clock.now.isoformat()
    assert _team_lines(await log_lines(league), "/team reserve-role") == [
        _reserve_line(role_name),
    ]
    assert league.granted == {} and league.revoked == {}
    assert await _division_teams(league) == divisions
    assert last_said(asked) == _reserve_set(role_id)


@pytest.mark.parametrize(
    "setup, role_id, reply",
    [
        pytest.param(
            _pending_completion, RESERVES_ROLE, _SEASON_DONE.format(command="reserve-role"),
            id="the-season-pending-completion",
        ),
        pytest.param(
            _reserves_role_now_managed, RESERVES_ROLE,
            "⛔ <@&816> is managed by an integration — a bot, a subscription or a linked role — "
            "so Discord will not let me grant it. Choose a role of the team's own.",
            id="a-role-the-bot-cannot-grant",
        ),
        pytest.param(None, 811, _held_by(811, "Ferrari"), id="a-role-another-team-holds"),
    ],
)
async def test_a_team_reserve_role_is_refused_at_the_ask(tmp_path, setup, role_id, reply):
    """The season is in Configuration, and the Reserve team is mapped to 815. Admin runs
    `/team reserve-role` where it cannot be done: once every division is done (the season Pending
    completion), with the role 816; with 816 once an integration manages it; or with Ferrari's
    role, 811. They are refused at once, in today's words, and the log channel takes the refusal,
    naming the command, as its one line. Nothing is asked of the queue, the Reserve team keeps 815,
    no audit entry is written, and no role is given or taken on Discord. Takes the place of
    `test_team_cog.py`'s mocked "the reserve is not given a role another team holds",
    `test_team_role_grantable.py`'s `/team reserve-role` case of "a team command refuses a role
    the bot cannot grant", and `test_team_cog_refusals.py`'s mocked "reserve-role season done"
    and "reserve-role cannot be granted"."""
    from tests.support.change_queue import change_rows
    from tests.support.settings_league import last_said, log_lines

    league = await _reserve_team_league(tmp_path)
    if setup is not None:
        await setup(league)
    asked = await _ask_reserve_role(league, role_id)

    assert last_said(asked) == reply
    assert await log_lines(league) == [_refusal(_RESERVE_ROLE_COMMAND, reply)]
    assert await change_rows(league.db_path) == []
    assert await _server_list(league) == _RESERVE_STANDING
    assert await _team_audits(league) == []
    assert league.granted == {} and league.revoked == {}


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
async def test_a_failed_team_reserve_role_changes_nothing(tmp_path):
    """The season is in Configuration, and the Reserve team is mapped to 815. Admin runs
    `/team reserve-role` with 816, and as the queue runs it the database fails writing the audit
    entry. Nothing of it is saved, the mapping, the entry and the line being one save: the Reserve
    team keeps 815, no audit entry is written and the log channel holds no success line. The
    league's lane stops at the job. Once a league admin discards it, the reply says "Nothing was
    changed: the Reserve team's role stands as it was. Run `/team reserve-role` again."; and the
    lane is clear."""
    from tests.support.change_queue import discard_job, run_queue, stopped_job
    from tests.support.settings_league import fault_in, last_said, log_lines

    league = await _reserve_team_league(tmp_path)
    asked = await _ask_reserve_role(league)
    with fault_in("leaguebot.core.services.change_queue.record_change_on"):
        await run_queue(league.bot)

    assert await _server_list(league) == _RESERVE_STANDING
    assert await _team_audits(league) == []
    assert not any("| Success" in line for line in await log_lines(league))
    assert await stopped_job(league.db_path) is not None

    await discard_job(league.bot)

    assert last_said(asked) == (
        "Nothing was changed: the Reserve team's role stands as it was. Run "
        "`/team reserve-role` again."
    )
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "meanwhile, reply, standing",
    [
        pytest.param(
            _pending_completion, _SEASON_DONE.format(command="reserve-role"), _RESERVE_STANDING,
            id="the-season-pending-completion",
        ),
        pytest.param(
            _role_held_meanwhile, _held_by(RESERVES_ROLE, "Ferrari"),
            {**_RESERVE_STANDING, "Ferrari": ("Scuderia Ferrari", RESERVES_ROLE)},
            id="the-role-taken-by-another-team",
        ),
        pytest.param(
            _reserves_role_now_managed,
            "⛔ <@&816> is managed by an integration — a bot, a subscription or a linked role — "
            "so Discord will not let me grant it. Choose a role of the team's own.",
            _RESERVE_STANDING, id="the-role-no-longer-grantable",
        ),
    ],
)
async def test_a_team_reserve_role_is_judged_again_as_it_starts(tmp_path, meanwhile, reply,
                                                               standing):
    """The season is in Configuration, and the Reserve team is mapped to 815. Admin runs
    `/team reserve-role` with 816 and is acknowledged; before the queue runs it, every division is
    done (the season Pending completion), Ferrari is given 816, or an integration takes over 816.
    The queue runs. The change is refused as it starts, in the words it would meet at the ask,
    its acknowledgement updated to them. The server's list stands as the meanwhile left it, the
    Reserve team keeping 815, no audit entry is written, no role is given or taken, the log
    channel holds the refusal, naming the command, alone, and the queue is not stopped."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _reserve_team_league(tmp_path)
    asked = await _ask_reserve_role(league)
    await meanwhile(league)
    await run_queue(league.bot)

    assert last_said(asked) == reply
    assert await _server_list(league) == standing
    assert await _team_audits(league) == []
    assert league.granted == {} and league.revoked == {}
    assert await log_lines(league) == [_refusal(_RESERVE_ROLE_COMMAND, reply)]
    assert await stopped_job(league.db_path) is None


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
async def test_a_refusal_found_inside_the_save_ends_the_team_reserve_role_and_the_lane_goes_on(
    tmp_path,
):
    """The one-save decision: the refusals are read again inside the save, so a role taken
    between the check and the write is not given to two teams. The season is in Configuration,
    and the Reserve team is mapped to 815. Admin runs `/team reserve-role` with 816, then submits
    the "Add a team" form for Haas ("Haas F1 Team", 814), before the queue runs. As the Reserve
    team's change starts its check passes, and Ferrari is then given 816 before its save. The
    change ends in today's words, '⛔ <@&816> is already the role of "Ferrari". A role belongs to
    one team only.', its acknowledgement updated to them, and the log channel takes that refusal
    as a line: the Reserve team keeps 815, and no audit entry of it is written. The lane is not
    stopped, and Haas's add, asked after, goes through: Haas is on the list with 814, and its
    success line follows the refusal."""
    from tests.support.change_queue import run_queue, stopped_job
    from tests.support.settings_league import last_said, log_lines

    league = await _reserve_team_league(tmp_path)
    reserve = await _ask_reserve_role(league)
    haas = await _ask_add(league, "Haas", "Haas F1 Team", HAAS_ROLE)
    taken = _check_then(league, "core.team_reserve_role", _role_held_meanwhile)
    await run_queue(league.bot)

    assert taken == [True]
    reply = _held_by(RESERVES_ROLE, "Ferrari")
    assert last_said(reserve) == reply
    assert await _server_list(league) == {
        **_RESERVE_STANDING, "Ferrari": ("Scuderia Ferrari", RESERVES_ROLE),
        "Haas": ("Haas F1 Team", HAAS_ROLE),
    }
    assert [row["new_value"] for row in await _team_audits(league)] == [
        {"shorthand": "Haas", "full_name": "Haas F1 Team"}, {"team": "Haas", "role_id": HAAS_ROLE},
    ]
    assert [
        line for line in await log_lines(league)
        if "/team reserve-role" in line or "/team add" in line or _ADD_FORM in line
    ] == [
        _refusal(_RESERVE_ROLE_COMMAND, reply),
        _add_line("Haas", "Haas F1 Team", HAAS_ROLE, "Haas"),
    ]
    assert await stopped_job(league.db_path) is None
    assert last_said(haas) == _added("Haas", "Haas F1 Team", HAAS_ROLE)


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
async def test_a_team_reserve_role_asked_again_while_it_waits_is_refused_as_a_repeat(tmp_path):
    """The season is in Configuration, and the Reserve team is mapped to 815. Admin runs
    `/team reserve-role` with 816 and is acknowledged; before the queue runs it, they run it again,
    the same. The second is refused at once, in the queue's words; only the one change is queued.
    The queue runs it: the Reserve team is mapped to 816, with one `TEAM_ROLE_CONFIG` entry."""
    from tests.support.change_queue import change_rows, run_queue
    from tests.support.settings_league import said
    from tests.support.setup_rounds import repeated

    league = await _reserve_team_league(tmp_path)
    first = await _ask_reserve_role(league)
    repeat = await _ask_reserve_role(league)

    assert said(repeat) == [repeated(_setting())]
    assert len(await change_rows(league.db_path)) == 1
    await run_queue(league.bot)

    assert (await _server_list(league))["Reserve"] == ("Reserve", RESERVES_ROLE)
    assert [row["change_type"] for row in await _team_audits(league)] == ["TEAM_ROLE_CONFIG"]
    assert said(first)[-1] == _reserve_set()


async def _reserve_seated_league(tmp_path, *seated: int):
    """`_seated_league`'s live season with a Reserve team in Pro and in Am, the Reserve team
    mapped to 815 (`_reserve_holds`), and each driver of *seated* moved from their team into
    their division's Reserve team, in the same seat, confirmed, holding their division's role and
    815 in place of their team's."""
    from tests.support.season_league import AM, AM_ROLE, PRO, PRO_ROLE, SEATS, TEAMS, profile_id

    league = await _reserve_holds(await _seated_league(tmp_path))
    reserves = {PRO: 33, AM: 34}
    for division_id, instance_id in reserves.items():
        await league.write(
            "INSERT INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, 'Reserve', 'Reserve', -1, 1)",
            instance_id, division_id,
        )
    for user_id in seated:
        division_id = TEAMS[SEATS[user_id][0]][0]
        await league.write(
            "UPDATE team_seats SET team_instance_id = ? WHERE driver_profile_id = ?",
            reserves[division_id], profile_id(user_id),
        )
        league.roles_held[user_id] = {PRO_ROLE if division_id == PRO else AM_ROLE, RESERVE_ROLE}
    return league


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
@pytest.mark.parametrize(
    "role_id, role_name",
    [pytest.param(RESERVES_ROLE, "Reserves", id="set"), pytest.param(None, None, id="cleared")],
)
async def test_a_team_reserve_role_moves_its_seated_drivers_roles_after_the_save(
    tmp_path, role_id, role_name,
):
    """architecture.md, "A job that fails stops its lane": the roles of a team's members follow
    its change, one job for each member. The season is ongoing, and the Reserve team is mapped to
    815. Lewis sits in Pro's Reserve team and Charles in Am's, each placement confirmed and each
    holding 815; Max stays at Ferrari. Admin runs `/team reserve-role` with 816, or with no role,
    clearing it. The queue runs it. The Reserve team is mapped to 816, or to none, one
    `TEAM_ROLE_CONFIG` entry recording it, and then the queue moves Lewis's roles and Charles's,
    each in a job of its own: 815 taken from each and 816 given, or, cleared, nothing given,
    Discord's audit log told "Reserve team's role changed" for each. Max is left alone and gets no
    job. The reply is today's, naming the role or saying it is cleared, then "2 seated driver(s)
    moved to the new role.", and the success line is today's. Takes the place of
    `test_team_cog.py`'s mocked "the drivers seated in reserve follow its new role" and
    `test_driver_move.py`'s "a role cleared is taken and nothing granted"."""
    import json

    from tests.support.change_queue import run_queue, step_rows
    from tests.support.season_league import CHARLES, LEWIS
    from tests.support.settings_league import last_said, log_lines

    league = await _reserve_seated_league(tmp_path, LEWIS, CHARLES)
    asked = await _ask_reserve_role(league, role_id)
    await run_queue(league.bot)

    assert (await _server_list(league))["Reserve"] == ("Reserve", role_id)
    assert [
        (row["change_type"], row["old_value"], row["new_value"])
        for row in await _team_audits(league)
    ] == [
        ("TEAM_ROLE_CONFIG", {"team": "Reserve", "role_id": RESERVE_ROLE},
         {"team": "Reserve", "role_id": role_id}),
    ]
    assert sorted(
        json.loads(row["payload"])["user_id"]
        for row in await step_rows(league.db_path) if row["name"] == "move_roles"
    ) == [LEWIS, CHARLES]
    assert league.revoked == {LEWIS: [RESERVE_ROLE], CHARLES: [RESERVE_ROLE]}
    if role_id is None:
        assert league.granted == {}
    else:
        assert league.granted == {LEWIS: [RESERVES_ROLE], CHARLES: [RESERVES_ROLE]}
        assert league.grant_reasons[LEWIS] == league.grant_reasons[CHARLES] == [_RESERVE_REASON]
    assert league.revoke_reasons[LEWIS] == league.revoke_reasons[CHARLES] == [_RESERVE_REASON]
    assert last_said(asked) == _reserve_set(role_id, moved=2)
    assert _team_lines(await log_lines(league), "/team reserve-role") == [
        _reserve_line(role_name),
    ]


@pytest.mark.xfail(strict=True, reason=_RESERVE_ON_THE_QUEUE)
async def test_a_role_refused_after_a_team_reserve_role_is_reported_and_the_queue_goes_on(
    tmp_path,
):
    """Decision 5: a role Discord refuses after a team's change is reported, and the queue goes
    on. The season is ongoing, and the Reserve team is mapped to 815. Lewis and Max sit in Pro's
    Reserve team, each holding 815; Charles stays at McLaren. Admin runs `/team reserve-role` with
    816, then submits the "Modify a team" form of McLaren with Haas's role, 814, before the queue
    runs; Discord refuses to give Lewis any role (403 Missing Permissions). The queue runs both.
    The Reserve team's change stands, saved before the roles were asked of Discord: it is mapped
    to 816; Lewis has 815 taken and nothing given; Max has 815 taken and 816 given. Its reply is
    today's, counting the one driver moved in full, then "⚠️ Done, but on Discord:" and a bullet,
    "the role <@&816> could not be given to <@101>: Discord refused or failed a request from the
    bot"; its success line carries a "not done:" line in the same words, its mentions in code.
    The lane is not stopped, and McLaren's change, asked after, is made in full: McLaren is
    mapped to 814, Charles has 812 taken and 814 given, and its reply is today's alone."""
    import discord

    from tests.support.change_queue import http_error, run_queue, stopped_job
    from tests.support.season_league import CHARLES, LEWIS, MAX
    from tests.support.settings_league import last_said, log_lines

    league = await _reserve_seated_league(tmp_path, LEWIS, MAX)
    league.grant_fails[LEWIS] = http_error(discord.Forbidden, status=403,
                                           text="Missing Permissions")
    reserve = await _ask_reserve_role(league)
    mclaren = await _ask_modify(league, "McLaren", role_id=HAAS_ROLE)
    await run_queue(league.bot)

    problem = (
        f"the role <@&{RESERVES_ROLE}> could not be given to <@{LEWIS}>: Discord refused or "
        "failed a request from the bot"
    )
    assert await _mappings(league) == {
        "Ferrari": 811, "McLaren": HAAS_ROLE, "Reserve": RESERVES_ROLE,
    }
    assert league.revoked == {LEWIS: [RESERVE_ROLE], MAX: [RESERVE_ROLE], CHARLES: [812]}
    assert league.granted == {MAX: [RESERVES_ROLE], CHARLES: [HAAS_ROLE]}
    assert last_said(reserve) == f"{_reserve_set(moved=1)}\n⚠️ Done, but on Discord:\n• {problem}"
    assert _team_lines(await log_lines(league), "/team reserve-role") == [
        f"{_reserve_line()}\n  not done: {_as_logged(problem)}",
    ]
    assert await stopped_job(league.db_path) is None
    assert last_said(mclaren) == _modified(
        "McLaren F1 Team", f"Role: <@&{HAAS_ROLE}>", "1 seated driver(s) moved to the new role.",
    )
