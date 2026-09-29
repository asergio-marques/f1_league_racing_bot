"""Every refusal of the `/team` commands and their forms is recorded in the log channel (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case turns a league manager away at one of
the team cog's own checks, and expects today's reply, seen by them alone, and exactly one standard
refusal line. A refusal on a form names the form ("the “Add a team” form"), not the command that
opened it.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.team_cog import TeamCog, _TeamAddModal, _TeamModifyModal
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.team_service import TeamReference
from tests.support.undecorate import undecorate

MANAGER_ID = 4242
TEAM = {"id": 5, "name": "rbr", "full_name": "Oracle Red Bull Racing", "role_id": 111,
        "is_reserve": False}

#: The marks a reply may open with, which the refusal line leaves out of its reason.
_REPLY_MARKS = ("❌", "⛔", "⚠️", "⚠", "ℹ️", "ℹ", "⏳", "⛓", "⏸️", "⏸")


def _reason(reply: str) -> str:
    """The reason a refusal line gives: the reply's first line, without its opening mark."""
    first = reply.strip().splitlines()[0].strip()
    for mark in _REPLY_MARKS:
        if first.startswith(mark):
            return first[len(mark):].lstrip("️").strip()
    return first


def _role(role_id: int = 222, *, managed: bool = False) -> MagicMock:
    """A role the bot can grant, or, *managed*, one an integration holds, which it cannot."""
    role = MagicMock()
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    role.name = "Red Bull"
    role.is_default.return_value = False
    role.managed = managed
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True
    return role


def _cog() -> TeamCog:
    """A team cog whose every check passes, with the team list open, until a case refuses."""
    bot = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.team_service.resolve_server_team = AsyncMock(return_value=TeamReference(team=dict(TEAM)))
    bot.team_service.add_default_team = AsyncMock()
    bot.team_service.remove_default_team = AsyncMock()
    bot.team_service.modify_default_team = AsyncMock(return_value=dict(TEAM))
    bot.team_service.get_teams_with_roles = AsyncMock(return_value=[])
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    bot.placement_service.set_team_role_config = AsyncMock()
    bot.placement_service.delete_team_role_config = AsyncMock()
    bot.placement_service.swap_team_role = AsyncMock(return_value=0)
    bot.placement_service.rename_team_role_config = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot
    return cog


def _interaction(cog: TeamCog, command: str | None) -> MagicMock:
    """`/<command>` run by the manager Alex, or a form's submission where *command* is None,
    answering as Discord's does."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
    if command is None:
        interaction.command = None
    else:
        interaction.command.qualified_name = command
    interaction.user.id = MANAGER_ID
    interaction.user.display_name = "Alex"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _season(stage: SeasonStage):
    def setup(bot):
        bot.season_service.get_setup_or_active_season = AsyncMock(
            return_value=SimpleNamespace(id=1, season_number=3, stage=stage)
        )
    return setup


def _no_team(bot):
    bot.team_service.resolve_server_team = AsyncMock(
        return_value=TeamReference(refusal="No team in the server's list has the shorthand `x`.")
    )


def _reserve(bot):
    bot.team_service.resolve_server_team = AsyncMock(
        return_value=TeamReference(
            team={**TEAM, "name": "Reserve", "full_name": "Reserve", "is_reserve": True}
        )
    )


def _raises(service: str, method: str, reason: str):
    def setup(bot):
        setattr(getattr(bot, service), method, AsyncMock(side_effect=ValueError(reason)))
    return setup


def _role_held(bot):
    bot.placement_service.team_holding_role = AsyncMock(return_value="Ferrari")


async def _add_form(cog, interaction, *, role=None):
    modal = _TeamAddModal(cog)
    modal.shorthand._value = "RB"
    modal.full_name._value = "Red Bull Racing"
    modal.role._values = [role or _role()]
    await modal.on_submit(interaction)


async def _modify_form(cog, interaction, *, shorthand="rbr", full_name=TEAM["full_name"], role=None):
    modal = _TeamModifyModal(cog, team=dict(TEAM), names_offered=True)
    modal.shorthand._value = shorthand
    modal.full_name._value = full_name
    modal.role._values = [role or _role(TEAM["role_id"])]
    await modal.on_submit(interaction)


_LOCKED = _season(SeasonStage.PLACEMENTS)
_DONE = _season(SeasonStage.PENDING_COMPLETION)
_ADD_FORM = "the “Add a team” form"
_MODIFY_FORM = "the “Modify a team” form"

# case id → (what the line names, the command or None for a form, how it runs, how the cog is
# made to refuse, a phrase of today's reply)
_CASES = {
    "add team list fixed": (
        "`/team add`", "team add",
        lambda cog, i: undecorate(TeamCog.team_add)(cog, i),
        _LOCKED, "The team list is fixed for Season 3",
    ),
    "modify season done": (
        "`/team modify`", "team modify",
        lambda cog, i: undecorate(TeamCog.team_modify)(cog, i, "rbr"),
        _DONE, "Every division of Season 3 is done",
    ),
    "modify unknown team": (
        "`/team modify`", "team modify",
        lambda cog, i: undecorate(TeamCog.team_modify)(cog, i, "x"),
        _no_team, "No team in the server's list has the shorthand `x`.",
    ),
    "modify the reserve": (
        "`/team modify`", "team modify",
        lambda cog, i: undecorate(TeamCog.team_modify)(cog, i, "Reserve"),
        _reserve, "The Reserve team's role is set with `/team reserve-role`",
    ),
    "remove team list fixed": (
        "`/team remove`", "team remove",
        lambda cog, i: undecorate(TeamCog.team_remove)(cog, i, "rbr"),
        _LOCKED, "The team list is fixed for Season 3",
    ),
    "remove unknown team": (
        "`/team remove`", "team remove",
        lambda cog, i: undecorate(TeamCog.team_remove)(cog, i, "x"),
        _no_team, "No team in the server's list has the shorthand `x`.",
    ),
    "remove refused by the service": (
        "`/team remove`", "team remove",
        lambda cog, i: undecorate(TeamCog.team_remove)(cog, i, "rbr"),
        _raises("team_service", "remove_default_team", "The last team cannot be removed."),
        "The last team cannot be removed.",
    ),
    "reserve-role season done": (
        "`/team reserve-role`", "team reserve-role",
        lambda cog, i: undecorate(TeamCog.team_reserve_role)(cog, i, _role()),
        _DONE, "Every division of Season 3 is done",
    ),
    "reserve-role cannot be granted": (
        "`/team reserve-role`", "team reserve-role",
        lambda cog, i: undecorate(TeamCog.team_reserve_role)(cog, i, _role(managed=True)),
        None, "",
    ),
    "add form team list fixed": (
        _ADD_FORM, None,
        lambda cog, i: _add_form(cog, i),
        _LOCKED, "The team list is fixed for Season 3",
    ),
    "add form role cannot be granted": (
        _ADD_FORM, None,
        lambda cog, i: _add_form(cog, i, role=_role(managed=True)),
        None, "",
    ),
    "add form role held": (
        _ADD_FORM, None,
        lambda cog, i: _add_form(cog, i),
        _role_held, 'is already the role of "Ferrari"',
    ),
    "add form refused by the service": (
        _ADD_FORM, None,
        lambda cog, i: _add_form(cog, i),
        _raises("team_service", "add_default_team", "A team with the shorthand `rb` exists."),
        "A team with the shorthand `rb` exists.",
    ),
    "add form role taken at the last moment": (
        _ADD_FORM, None,
        lambda cog, i: _add_form(cog, i),
        _raises("placement_service", "set_team_role_config",
                '<@&222> is already the role of "Ferrari".'),
        '<@&222> is already the role of "Ferrari".',
    ),
    "modify form team gone": (
        _MODIFY_FORM, None,
        lambda cog, i: _modify_form(cog, i, full_name="Red Bull Racing"),
        _no_team, "is no longer in the server's team list",
    ),
    "modify form names fixed": (
        _MODIFY_FORM, None,
        lambda cog, i: _modify_form(cog, i, full_name="Red Bull Racing"),
        _LOCKED, "The team list is fixed now that the season's configuration is confirmed",
    ),
    "modify form role cannot be granted": (
        _MODIFY_FORM, None,
        lambda cog, i: _modify_form(cog, i, role=_role(managed=True)),
        None, "Nothing was written.",
    ),
    "modify form role held": (
        _MODIFY_FORM, None,
        lambda cog, i: _modify_form(cog, i, role=_role()),
        _raises("placement_service", "set_team_role_config",
                '<@&222> is already the role of "Ferrari".'),
        '<@&222> is already the role of "Ferrari". Nothing was written.',
    ),
    "modify form refused by the service": (
        _MODIFY_FORM, None,
        lambda cog, i: _modify_form(cog, i, full_name="Red Bull Racing"),
        _raises("team_service", "modify_default_team", "A team is already named that."),
        "A team is already named that.",
    ),
}


@pytest.mark.xfail(strict=True, reason="#482: the team cog's own refusals write no log line")
@pytest.mark.parametrize("case", sorted(_CASES))
async def test_a_refusal_of_the_team_cog_is_recorded_in_the_log_channel(case):
    """A manager the team cog's own checks turn away, at a command or on its form, is answered
    as today, seen by them alone, and one standard line records the refusal (#482)."""
    what, command, run, setup, phrase = _CASES[case]
    cog = _cog()
    if setup is not None:
        setup(cog.bot)
    interaction = _interaction(cog, command)

    await run(cog, interaction)

    interaction.response.send_modal.assert_not_awaited()
    sent = (
        interaction.response.send_message.await_args_list
        + interaction.followup.send.await_args_list
    )
    assert len(sent) == 1 and sent[0].kwargs.get("ephemeral") is True
    reply = sent[0].args[0]
    assert reply.startswith("⛔") and phrase in reply
    lines = [call.args[0] for call in cog.bot.output_router.post_log.await_args_list]
    assert lines == [f"⛔ {what} refused for Alex (<@{MANAGER_ID}>) — {_reason(reply)}"]
