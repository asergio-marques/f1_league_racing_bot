"""Every refusal of the `/team` commands and their forms is recorded in the log channel (#482).

The rule is the core specification's "The record of what changed": a refusal is recorded as one
line naming the member, what was refused and why. Each case turns a league manager away at one of
the team cog's own checks as a command opens its form, and expects today's reply, seen by them
alone, and exactly one standard refusal line. The refusals of the forms themselves, and of
`/team remove` and `/team reserve-role`, are `test_team_changes.py`'s, run on a league's database.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.team_cog import TeamCog
from leaguebot.core.models.season import SeasonStage
from leaguebot.core.services.drivers.team_service import TeamReference
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


def _cog() -> TeamCog:
    """A team cog whose every check passes, with the team list open, until a case refuses."""
    bot = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.team_service.resolve_server_team = AsyncMock(return_value=TeamReference(team=dict(TEAM)))
    bot.team_service.get_teams_with_roles = AsyncMock(return_value=[])
    bot.output_router.post_log = AsyncMock()
    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot
    return cog


def _interaction(cog: TeamCog, command: str) -> MagicMock:
    """`/<command>` run by the manager Alex, answering as Discord's does."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.client = cog.bot
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


_LOCKED = _season(SeasonStage.PLACEMENTS)
_DONE = _season(SeasonStage.PENDING_COMPLETION)

# case id → (what the line names, the command, how it runs, how the cog is
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
}


@pytest.mark.parametrize("case", sorted(_CASES))
async def test_a_refusal_of_the_team_cog_is_recorded_in_the_log_channel(case):
    """A manager the team cog's own checks turn away at a command is answered
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
