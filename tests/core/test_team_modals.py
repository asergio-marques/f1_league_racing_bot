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
    bot.team_service.add_default_team = AsyncMock()
    bot.team_service.remove_default_team = AsyncMock()
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    bot.placement_service.set_team_role_config = AsyncMock()
    bot.output_router.post_log = AsyncMock()
    cog = TeamCog.__new__(TeamCog)
    cog.bot = bot
    return cog


def _interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.user.display_name = "Manager"
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    interaction.response.defer = AsyncMock()
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
    cog.bot.team_service.add_default_team.assert_not_awaited()


async def test_the_form_bounds_both_names_as_they_are_typed():
    """Sixteen characters and sixty-four, which Discord itself enforces in the field."""
    from leaguebot.core.cogs.team_cog import _TeamAddModal

    modal = _TeamAddModal(_cog())

    assert (modal.shorthand.min_length, modal.shorthand.max_length) == (1, SHORTHAND_MAX)
    assert (modal.full_name.min_length, modal.full_name.max_length) == (1, FULL_NAME_MAX)
    assert modal.role.required is True


async def test_submitting_the_form_records_all_three_and_says_so():
    from leaguebot.core.cogs.team_cog import _TeamAddModal

    cog = _cog()
    modal = _TeamAddModal(cog)
    modal.shorthand._value = "  RBR  "
    modal.full_name._value = " Oracle Red Bull Racing "
    modal.role._values = [_role(555)]
    interaction = _interaction()

    await modal.on_submit(interaction)

    cog.bot.team_service.add_default_team.assert_awaited_once_with(
        "RBR", full_name="Oracle Red Bull Racing"
    )
    cog.bot.placement_service.set_team_role_config.assert_awaited_once()
    assert cog.bot.placement_service.set_team_role_config.await_args.args[0] == "RBR"
    said = interaction.response.send_message.await_args.args[0]
    assert "Oracle Red Bull Racing" in said and "RBR" in said


async def test_a_form_submitted_after_the_list_was_fixed_writes_nothing():
    """The season moved on while the form stood open, so the submission is refused whole."""
    from leaguebot.core.cogs.team_cog import _TeamAddModal
    from leaguebot.core.models.season import SeasonStage

    cog = _cog()
    cog.bot.season_service.get_setup_or_active_season = AsyncMock(
        return_value=MagicMock(season_number=3, stage=SeasonStage.ONGOING)
    )
    modal = _TeamAddModal(cog)
    modal.shorthand._value = "RBR"
    modal.full_name._value = "Oracle Red Bull Racing"
    modal.role._values = [_role(555)]
    interaction = _interaction()

    await modal.on_submit(interaction)

    assert "fixed for Season 3" in interaction.response.send_message.await_args.args[0]
    cog.bot.team_service.add_default_team.assert_not_awaited()


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
    cog.bot.team_service.modify_default_team = AsyncMock(
        side_effect=lambda current, *, shorthand=None, full_name=None, **kw: {
            "name": shorthand or current, "full_name": full_name or "Oracle Red Bull Racing"
        }
    )
    cog.bot.placement_service.rename_team_role_config = AsyncMock()
    cog.bot.placement_service.swap_team_role = AsyncMock(return_value=2)
    assert isinstance(cog, TeamCog)
    return cog


async def _submit(cog, *, shorthand=None, full_name=None, role=None, names_offered=True):
    from leaguebot.core.cogs.team_cog import _TeamModifyModal

    modal = _TeamModifyModal(cog, team=dict(TEAM), names_offered=names_offered)
    if modal.shorthand is not None:
        modal.shorthand._value = shorthand if shorthand is not None else TEAM["name"]
        modal.full_name._value = full_name if full_name is not None else TEAM["full_name"]
    modal.role._values = [role] if role is not None else [_role(TEAM["role_id"])]
    interaction = _interaction()
    await modal.on_submit(interaction)
    return interaction


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


async def test_a_form_submitted_untouched_changes_nothing():
    cog = _modify_cog()

    interaction = await _submit(cog)

    assert "Nothing changed" in interaction.followup.send.await_args.args[0]
    cog.bot.team_service.modify_default_team.assert_not_awaited()
    cog.bot.placement_service.set_team_role_config.assert_not_awaited()


async def test_changing_both_names_records_them_and_names_the_artwork_file():
    cog = _modify_cog()

    interaction = await _submit(cog, shorthand="RB", full_name="Red Bull Racing")

    cog.bot.team_service.modify_default_team.assert_awaited_once()
    assert cog.bot.team_service.modify_default_team.await_args.kwargs["shorthand"] == "RB"
    cog.bot.placement_service.rename_team_role_config.assert_awaited_once()
    said = interaction.followup.send.await_args.args[0]
    assert "`rb`" in said and "`rbr`" in said


async def test_changing_the_role_moves_the_drivers_seated_in_the_team():
    cog = _modify_cog()

    interaction = await _submit(cog, role=_role(222))

    cog.bot.placement_service.set_team_role_config.assert_awaited_once()
    cog.bot.placement_service.swap_team_role.assert_awaited_once()
    assert "2 seated driver(s) moved" in interaction.followup.send.await_args.args[0]


async def test_a_name_changed_after_the_list_was_fixed_refuses_the_whole_submission():
    """The window closed while the form stood open: nothing is written, not even the role."""
    from leaguebot.core.models.season import SeasonStage

    cog = _modify_cog(season=MagicMock(season_number=3, stage=SeasonStage.ONGOING))

    interaction = await _submit(cog, shorthand="RB", role=_role(222))

    said = interaction.followup.send.await_args.args[0]
    assert "team list is fixed" in said and "nothing was written" in said.lower()
    cog.bot.team_service.modify_default_team.assert_not_awaited()
    cog.bot.placement_service.set_team_role_config.assert_not_awaited()


async def test_a_role_another_team_holds_writes_nothing():
    cog = _modify_cog()
    cog.bot.placement_service.set_team_role_config = AsyncMock(
        side_effect=ValueError('<@&222> is already the role of "MCL".')
    )

    interaction = await _submit(cog, full_name="Red Bull Racing", role=_role(222))

    assert "already the role" in interaction.followup.send.await_args.args[0]
    cog.bot.team_service.modify_default_team.assert_not_awaited()


async def test_a_team_removed_while_the_form_stood_open_is_reported():
    from leaguebot.core.services.team_service import TeamReference

    cog = _modify_cog()
    cog.bot.team_service.resolve_server_team = AsyncMock(
        return_value=TeamReference(refusal="No team has the shorthand `RBR`.")
    )

    interaction = await _submit(cog, full_name="Red Bull Racing")

    assert "no longer in the server's team list" in interaction.followup.send.await_args.args[0]
