"""The forms `/team add` and `/team modify` take their input on (#381).

A team carries three things — the shorthand a league types, the full name every post shows, and
the role its drivers are granted — and a form takes them together, bounding the two names as they
are typed. What a form must never do is decide: every rule is `TeamService`'s and every check runs
again on submit, because a season can move on while the form is open.

Each test is `async def`, as every test building a modal must be.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from leaguebot.core.services.team_service import FULL_NAME_MAX, SHORTHAND_MAX


def _role(role_id: int = 111) -> MagicMock:
    role = MagicMock()
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    role.name = "Red Bull"
    role.is_default.return_value = False
    role.managed = False
    role.guild.me.guild_permissions.manage_roles = True
    role.guild.me.top_role.__gt__ = lambda _self, _other: True
    return role


def _cog() -> MagicMock:
    from leaguebot.core.cogs.team_cog import TeamCog

    bot = MagicMock()
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=None)
    bot.team_role_service.team_holding_role = AsyncMock(return_value=None)
    bot.output_router.post_log = AsyncMock()
    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot
    return cog


def _interaction() -> MagicMock:
    """An interaction whose response knows whether it has been used, as Discord's does, so that
    a refusal answers by `response` until the interaction is answered or deferred (#482)."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.send_modal = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


# ── /team add ─────────────────────────────────────────────────────────────


async def test_the_command_opens_the_form_and_writes_nothing_itself():
    from leaguebot.core.cogs.team_cog import TeamCog, _TeamAddModal
    from tests.support.undecorate import undecorate

    cog = _cog()
    interaction = _interaction()

    await undecorate(TeamCog.team_add)(cog, interaction)

    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, _TeamAddModal)
    assert cog.bot.team_service.mock_calls == []
    cog.bot.change_queue.ask.assert_not_called()


async def test_the_form_bounds_both_names_as_they_are_typed():
    """Sixteen characters and sixty-four, which Discord itself enforces in the field."""
    from leaguebot.core.cogs.team_cog import _TeamAddModal

    modal = _TeamAddModal(_cog())

    assert (modal.shorthand.min_length, modal.shorthand.max_length) == (1, SHORTHAND_MAX)
    assert (modal.full_name.min_length, modal.full_name.max_length) == (1, FULL_NAME_MAX)
    assert modal.role.required is True


# ── /team modify ──────────────────────────────────────────────────────────


TEAM = {"name": "RBR", "full_name": "Oracle Red Bull Racing", "is_reserve": False, "role_id": 111}


def _modify_cog(*, team=None, list_open=True, season=None):
    from leaguebot.core.cogs.team_cog import TeamCog
    from leaguebot.core.services.team_service import TeamReference

    cog = _cog()
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(return_value=season)
    cog.bot.team_service.resolve_server_team = AsyncMock(
        return_value=TeamReference(team=dict(team or TEAM))
    )
    assert isinstance(cog, TeamCog)
    return cog


async def test_the_form_opens_pre_filled_with_every_field_that_may_change():
    from leaguebot.core.cogs.team_cog import TeamCog, _TeamModifyModal
    from tests.support.undecorate import undecorate

    cog = _modify_cog()
    interaction = _interaction()

    await undecorate(TeamCog.team_modify)(cog, interaction, "rbr")

    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, _TeamModifyModal)
    assert modal.shorthand.default == "RBR"
    assert modal.full_name.default == "Oracle Red Bull Racing"
    assert [o.id for o in modal.role.default_values] == [111]


async def test_the_form_offers_only_the_role_once_the_team_list_is_fixed():
    from leaguebot.core.cogs.team_cog import TeamCog
    from leaguebot.core.models.season import SeasonStage
    from tests.support.undecorate import undecorate

    cog = _modify_cog(season=MagicMock(season_number=3, stage=SeasonStage.ONGOING))
    interaction = _interaction()

    await undecorate(TeamCog.team_modify)(cog, interaction, "rbr")

    modal = interaction.response.send_modal.await_args.args[0]
    assert modal.shorthand is None and modal.full_name is None
    assert modal.role is not None


async def test_the_reserve_team_is_sent_to_its_own_command():
    from leaguebot.core.cogs.team_cog import TeamCog
    from tests.support.undecorate import undecorate

    cog = _modify_cog(team={**TEAM, "name": "Reserve", "full_name": "Reserve", "is_reserve": True})
    interaction = _interaction()

    await undecorate(TeamCog.team_modify)(cog, interaction, "Reserve")

    assert "/team reserve-role" in interaction.response.send_message.await_args.args[0]
    interaction.response.send_modal.assert_not_awaited()
