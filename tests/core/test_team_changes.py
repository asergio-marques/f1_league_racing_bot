"""`/team add`, `modify`, `remove` and `reserve-role` on the change queue (#283).

Each is a change on the league's lane: the team named at the ask, the queue's acknowledgement its
reply, and one save, stamped with the queue's clock, writing the team's row of the server's list,
its role mapping, an audit entry for each and the line. Every refusal is the command's at the ask
and is judged again as the change starts; one found inside the save ends the change in its words,
writing nothing, and the lane goes on.

These run on `configuring_league`'s season 3, in Configuration, where the team list is open. The
server's list holds Ferrari ("Scuderia Ferrari", role 811), McLaren ("McLaren F1 Team", role 812)
and the Reserve team. Admin (77) asks every change, as a league manager for `/team add` and as a
league admin for `/team remove`. Alpine's role (813) and Haas's (814) are on the server, held by
no team, and the bot can grant all four roles.
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

    league = await configuring_league(tmp_path)
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
    the list was fixed" and `test_team_role_grantable.py`'s "adding a team refuses a role the bot
    cannot grant"."""
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
    "a role taken at the last moment takes the new team away again"."""
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
    mocked "refused once the configuration is confirmed" and "not found" remove tests."""
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
