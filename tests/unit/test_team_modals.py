"""The forms `/team add` and `/team modify` take their input on (#381).

A team carries three things — the shorthand a league types, the full name every post shows, and
the role its drivers are granted — and a form takes them together, bounding the two names as they
are typed. What a form must never do is decide: every rule is `TeamService`'s and every check runs
again on submit, because a season can move on while the form is open.

Each test is `async def`, as every test building a modal must be.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.team_service import FULL_NAME_MAX, SHORTHAND_MAX  # noqa: E402


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
    from cogs.team_cog import TeamCog

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
    from cogs.team_cog import TeamCog, _TeamAddModal
    from tests.support.undecorate import undecorate

    cog = _cog()
    interaction = _interaction()

    await undecorate(TeamCog.team_add)(cog, interaction)

    modal = interaction.response.send_modal.await_args.args[0]
    assert isinstance(modal, _TeamAddModal)
    cog.bot.team_service.add_default_team.assert_not_awaited()


async def test_the_form_bounds_both_names_as_they_are_typed():
    """Sixteen characters and sixty-four, which Discord itself enforces in the field."""
    from cogs.team_cog import _TeamAddModal

    modal = _TeamAddModal(_cog())

    assert (modal.shorthand.min_length, modal.shorthand.max_length) == (1, SHORTHAND_MAX)
    assert (modal.full_name.min_length, modal.full_name.max_length) == (1, FULL_NAME_MAX)
    assert modal.role.required is True


async def test_submitting_the_form_records_all_three_and_says_so():
    from cogs.team_cog import _TeamAddModal

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
    from cogs.team_cog import _TeamAddModal
    from models.season import SeasonStage

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
