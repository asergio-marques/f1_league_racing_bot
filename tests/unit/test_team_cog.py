"""Unit tests for TeamCog — /team add, /team remove, /team rename, /team list."""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interaction(guild_id: int = 1) -> MagicMock:
    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user.id = 42
    interaction.user.__str__ = lambda self: "admin#0001"
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _make_season(season_number: int = 3, season_id: int = 1) -> MagicMock:
    season = MagicMock()
    season.id = season_id
    season.season_number = season_number
    return season


def _make_bot(
    *,
    setup_season=None,
    add_default_team_side_effect=None,
    remove_default_team_side_effect=None,
    rename_default_team_side_effect=None,
    season_team_add_return=2,
    season_team_remove_return=2,
    season_team_rename_return=2,
    season_team_names: set | None = None,
    teams_with_roles: list | None = None,
) -> MagicMock:
    bot = MagicMock()
    bot.team_service.add_default_team = AsyncMock(side_effect=add_default_team_side_effect)
    bot.team_service.remove_default_team = AsyncMock(side_effect=remove_default_team_side_effect)
    bot.team_service.rename_default_team = AsyncMock(side_effect=rename_default_team_side_effect)
    bot.team_service.season_team_add = AsyncMock(return_value=season_team_add_return)
    bot.team_service.season_team_remove = AsyncMock(return_value=season_team_remove_return)
    bot.team_service.season_team_rename = AsyncMock(return_value=season_team_rename_return)
    bot.team_service.get_setup_season_team_names = AsyncMock(return_value=season_team_names or set())
    bot.team_service.get_teams_with_roles = AsyncMock(return_value=teams_with_roles or [])
    bot.placement_service.set_team_role_config = AsyncMock()
    bot.placement_service.delete_team_role_config = AsyncMock()
    bot.placement_service.rename_team_role_config = AsyncMock()
    bot.season_service.get_setup_season = AsyncMock(return_value=setup_season)
    return bot


def _unwrap(cmd):
    """Return the innermost callback (bypasses channel_guard and admin_only)."""
    return cmd.callback.__wrapped__.__wrapped__


# ---------------------------------------------------------------------------
# /team add
# ---------------------------------------------------------------------------

class TestTeamAdd:
    async def test_no_role_no_season_success(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=None)

        bot.team_service.add_default_team.assert_awaited_once_with(1, "Alpine")
        bot.placement_service.set_team_role_config.assert_not_awaited()
        bot.team_service.season_team_add.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        assert "Alpine" in (args[0] if args else kwargs["content"])
        assert kwargs.get("ephemeral") is True

    async def test_with_role_no_season(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 555
        role.mention = "<@&555>"

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=role)

        bot.placement_service.set_team_role_config.assert_awaited_once()
        bot.team_service.season_team_add.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "<@&555>" in content

    async def test_with_role_and_setup_season(self):
        from cogs.team_cog import TeamCog
        season = _make_season(season_number=3)
        bot = _make_bot(setup_season=season, season_team_add_return=2)
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 555
        role.mention = "<@&555>"

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=role)

        bot.team_service.add_default_team.assert_awaited_once()
        bot.placement_service.set_team_role_config.assert_awaited_once()
        bot.team_service.season_team_add.assert_awaited_once_with(1, season.id, "Alpine", 2)
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "2 division" in content
        assert "Season 3" in content

    async def test_duplicate_name_returns_error(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(add_default_team_side_effect=ValueError('A default team named "Alpine" already exists.'))
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=None)

        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content
        bot.placement_service.set_team_role_config.assert_not_awaited()
        bot.team_service.season_team_add.assert_not_awaited()


# ---------------------------------------------------------------------------
# /team remove
# ---------------------------------------------------------------------------

class TestTeamRemove:
    async def test_no_season_removes_from_server_only(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_remove)(cog, interaction, name="Alpine")

        bot.team_service.remove_default_team.assert_awaited_once()
        bot.placement_service.delete_team_role_config.assert_awaited_once()
        bot.team_service.season_team_remove.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "✅" in content

    async def test_with_setup_season_team_present(self):
        from cogs.team_cog import TeamCog
        season = _make_season(season_number=3)
        bot = _make_bot(
            setup_season=season,
            season_team_names={"Alpine", "Ferrari"},
            season_team_remove_return=2,
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_remove)(cog, interaction, name="Alpine")

        bot.team_service.season_team_remove.assert_awaited_once()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "2 division" in content
        assert "Season 3" in content

    async def test_with_setup_season_team_absent(self):
        from cogs.team_cog import TeamCog
        season = _make_season(season_number=3)
        bot = _make_bot(
            setup_season=season,
            season_team_names={"Ferrari"},  # "Alpine" not present
            season_team_remove_return=2,
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_remove)(cog, interaction, name="Alpine")

        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "Not present" in content

    async def test_not_found_returns_error(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(remove_default_team_side_effect=ValueError('No default team named "Ghost" found.'))
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_remove)(cog, interaction, name="Ghost")

        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content
        bot.placement_service.delete_team_role_config.assert_not_awaited()


# ---------------------------------------------------------------------------
# /team rename
# ---------------------------------------------------------------------------

class TestTeamRename:
    async def test_no_season_renames_server_list(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_rename)(cog, interaction, current_name="Alpine", new_name="BWT Alpine")

        bot.team_service.rename_default_team.assert_awaited_once_with(1, "Alpine", "BWT Alpine")
        bot.placement_service.rename_team_role_config.assert_awaited_once()
        bot.team_service.season_team_rename.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "✅" in content

    async def test_with_setup_season_propagates(self):
        from cogs.team_cog import TeamCog
        season = _make_season(season_number=3)
        bot = _make_bot(setup_season=season, season_team_rename_return=2)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_rename)(cog, interaction, current_name="Alpine", new_name="BWT Alpine")

        bot.team_service.rename_default_team.assert_awaited_once()
        bot.placement_service.rename_team_role_config.assert_awaited_once()
        bot.team_service.season_team_rename.assert_awaited_once_with(1, season.id, "Alpine", "BWT Alpine")
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "2 division" in content
        assert "Season 3" in content

    async def test_current_name_not_found_returns_error(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(rename_default_team_side_effect=ValueError('No default team named "Ghost" found.'))
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_rename)(cog, interaction, current_name="Ghost", new_name="NewName")

        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content
        bot.placement_service.rename_team_role_config.assert_not_awaited()

    async def test_new_name_conflict_returns_error(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(rename_default_team_side_effect=ValueError('A default team named "Ferrari" already exists.'))
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_rename)(cog, interaction, current_name="Alpine", new_name="Ferrari")

        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content


# ---------------------------------------------------------------------------
# /team list
# ---------------------------------------------------------------------------

class TestTeamList:
    async def test_no_teams_returns_empty_state(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=[], setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_list)(cog, interaction)

        interaction.followup.send.assert_awaited_once()
        args, kwargs = interaction.followup.send.call_args
        content = args[0] if args else kwargs["content"]
        assert "No teams" in content

    async def test_teams_no_season_shows_server_list(self):
        from cogs.team_cog import TeamCog
        teams = [
            {"name": "Ferrari", "max_seats": 2, "is_reserve": False, "role_id": 111},
            {"name": "Alpine", "max_seats": 2, "is_reserve": False, "role_id": None},
        ]
        bot = _make_bot(teams_with_roles=teams, setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_list)(cog, interaction)

        bot.team_service.get_setup_season_team_names.assert_not_awaited()
        args, kwargs = interaction.followup.send.call_args
        content = args[0] if args else kwargs["content"]
        assert "Ferrari" in content
        assert "Alpine" in content
        assert "⚠️" not in content

    async def test_setup_season_matching_shows_unified_header(self):
        from cogs.team_cog import TeamCog
        teams = [
            {"name": "Ferrari", "max_seats": 2, "is_reserve": False, "role_id": None},
            {"name": "Alpine", "max_seats": 2, "is_reserve": False, "role_id": None},
        ]
        season = _make_season(season_number=3)
        bot = _make_bot(
            teams_with_roles=teams,
            setup_season=season,
            season_team_names={"Ferrari", "Alpine"},
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_list)(cog, interaction)

        args, kwargs = interaction.followup.send.call_args
        content = args[0] if args else kwargs["content"]
        assert "Season 3 will use this list" in content
        assert "⚠️" not in content

    async def test_setup_season_divergent_shows_warning(self):
        from cogs.team_cog import TeamCog
        teams = [
            {"name": "Ferrari", "max_seats": 2, "is_reserve": False, "role_id": None},
            {"name": "Alpine", "max_seats": 2, "is_reserve": False, "role_id": None},
        ]
        season = _make_season(season_number=3)
        bot = _make_bot(
            teams_with_roles=teams,
            setup_season=season,
            season_team_names={"Ferrari"},  # Alpine missing from season
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_list)(cog, interaction)

        # May be one or more followup calls due to splitting
        all_content = " ".join(
            (call.args[0] if call.args else call.kwargs.get("content", ""))
            for call in interaction.followup.send.await_args_list
        )
        assert "⚠️" in all_content
        assert "Season 3" in all_content


# ---------------------------------------------------------------------------
# /team reserve-role
# ---------------------------------------------------------------------------

class TestTeamReserveRole:
    async def test_set_role_calls_set_config(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot()
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 999
        role.mention = "<@&999>"

        await _unwrap(cog.team_reserve_role)(cog, interaction, role=role)

        bot.placement_service.set_team_role_config.assert_awaited_once()
        call_args = bot.placement_service.set_team_role_config.call_args
        assert call_args.args[1] == "Reserve"
        assert call_args.args[2] == 999
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "✅" in content
        assert "<@&999>" in content

    async def test_clear_role_calls_delete_config(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot()
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_reserve_role)(cog, interaction, role=None)

        bot.placement_service.delete_team_role_config.assert_awaited_once()
        call_args = bot.placement_service.delete_team_role_config.call_args
        assert call_args.args[1] == "Reserve"
        bot.placement_service.set_team_role_config.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "cleared" in content
