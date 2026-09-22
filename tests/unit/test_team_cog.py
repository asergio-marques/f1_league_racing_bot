"""Unit tests for TeamCog — /team add, /team remove, /team rename, /team list."""
from __future__ import annotations

import sys
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from tests.support.undecorate import undecorate  # noqa: E402

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


def _make_season(season_number: int = 3, season_id: int = 1, stage=None) -> MagicMock:
    from models.season import SeasonStage
    season = MagicMock()
    season.id = season_id
    season.season_number = season_number
    season.stage = stage if stage is not None else SeasonStage.PLACEMENTS
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
    live_season=None,
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
    bot.placement_service.swap_team_role = AsyncMock(return_value=0)
    bot.placement_service.team_holding_role = AsyncMock(return_value=None)
    bot.season_service.get_setup_season = AsyncMock(return_value=setup_season)
    bot.season_service.get_setup_or_active_season = AsyncMock(return_value=live_season)
    bot.output_router.post_log = AsyncMock()
    return bot


def _unwrap(cmd):
    """Return the innermost callback, past whatever tier guard the command wears."""
    return undecorate(cmd)


# ---------------------------------------------------------------------------
# /team add
# ---------------------------------------------------------------------------

class TestTeamAdd:
    async def test_with_role_no_season_success(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 555
        role.mention = "<@&555>"

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=role)

        bot.team_service.add_default_team.assert_awaited_once_with("Alpine")
        bot.placement_service.set_team_role_config.assert_awaited_once()
        bot.team_service.season_team_add.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "Alpine" in content
        assert "<@&555>" in content
        assert kwargs.get("ephemeral") is True

    async def test_in_configuration_changes_the_server_list(self):
        from cogs.team_cog import TeamCog
        from models.season import SeasonStage
        season = _make_season(stage=SeasonStage.CONFIGURATION)
        bot = _make_bot(live_season=season)
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 555
        role.mention = "<@&555>"

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=role)

        bot.team_service.add_default_team.assert_awaited_once_with("Alpine")
        bot.team_service.season_team_add.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "✅" in content

    @pytest.mark.parametrize("stage_name", ["WAITING", "SIGNUPS", "PLACEMENTS", "ONGOING"])
    async def test_refused_once_the_configuration_is_confirmed(self, stage_name):
        from cogs.team_cog import TeamCog
        from models.season import SeasonStage
        season = _make_season(season_number=3, stage=SeasonStage(stage_name))
        bot = _make_bot(live_season=season)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=MagicMock())

        bot.team_service.add_default_team.assert_not_awaited()
        bot.placement_service.set_team_role_config.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content
        assert "Season 3" in content

    async def test_duplicate_name_returns_error(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(add_default_team_side_effect=ValueError('A default team named "Alpine" already exists.'))
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 555

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=role)

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

    async def test_refused_once_the_configuration_is_confirmed(self):
        from cogs.team_cog import TeamCog
        season = _make_season(season_number=3)
        bot = _make_bot(live_season=season)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_remove)(cog, interaction, name="Alpine")

        bot.team_service.remove_default_team.assert_not_awaited()
        bot.placement_service.delete_team_role_config.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content

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

        bot.team_service.rename_default_team.assert_awaited_once_with("Alpine", "BWT Alpine")
        bot.placement_service.rename_team_role_config.assert_awaited_once()
        bot.team_service.season_team_rename.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "✅" in content

    async def test_refused_once_the_configuration_is_confirmed(self):
        from cogs.team_cog import TeamCog
        season = _make_season(season_number=3)
        bot = _make_bot(live_season=season)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_rename)(cog, interaction, current_name="Alpine", new_name="BWT Alpine")

        bot.team_service.rename_default_team.assert_not_awaited()
        bot.placement_service.rename_team_role_config.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        content = args[0] if args else kwargs["content"]
        assert "⛔" in content

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
# /team role — available in every state, so a deleted role can be repaired
# ---------------------------------------------------------------------------

class TestTeamRole:
    _TEAMS = [
        {"name": "Ferrari", "max_seats": 2, "is_reserve": False, "role_id": 111},
        {"name": "Reserve", "max_seats": 0, "is_reserve": True, "role_id": None},
    ]

    async def test_sets_the_role_while_the_season_is_ongoing(self):
        from cogs.team_cog import TeamCog
        from models.season import SeasonStage
        bot = _make_bot(
            teams_with_roles=self._TEAMS,
            live_season=_make_season(stage=SeasonStage.ONGOING),
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 222
        role.mention = "<@&222>"

        await _unwrap(cog.team_role)(cog, interaction, name="ferrari", role=role)

        bot.placement_service.set_team_role_config.assert_awaited_once()
        call_args = bot.placement_service.set_team_role_config.call_args
        assert call_args.args[0:2] == ("Ferrari", 222)
        args, kwargs = interaction.followup.send.call_args
        content = args[0] if args else kwargs["content"]
        assert "✅" in content
        assert "<@&222>" in content

    async def test_the_drivers_seated_in_the_team_follow_its_new_role(self):
        """Issue #220: every driver of the team has the old role taken and the new granted."""
        from cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=self._TEAMS)
        bot.placement_service.swap_team_role = AsyncMock(return_value=3)
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 222
        role.mention = "<@&222>"

        await _unwrap(cog.team_role)(cog, interaction, name="Ferrari", role=role)

        bot.placement_service.swap_team_role.assert_awaited_once_with(
            "Ferrari", 111, 222, interaction.guild
        )
        assert "3 seated driver(s) moved to the new role" in interaction.followup.send.call_args.args[0]
        assert "seated drivers moved: 3" in bot.output_router.post_log.call_args.args[0]

    async def test_refuses_a_team_not_in_the_list(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=self._TEAMS)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_role)(cog, interaction, name="Ghost", role=MagicMock())

        bot.placement_service.set_team_role_config.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        assert "⛔" in (args[0] if args else kwargs["content"])

    async def test_points_the_reserve_team_at_its_own_command(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=self._TEAMS)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_role)(cog, interaction, name="Reserve", role=MagicMock())

        bot.placement_service.set_team_role_config.assert_not_awaited()
        args, kwargs = interaction.response.send_message.call_args
        assert "reserve-role" in (args[0] if args else kwargs["content"])


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
        assert call_args.args[0] == "Reserve"
        assert call_args.args[1] == 999
        args, kwargs = interaction.followup.send.call_args
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
        assert call_args.args[0] == "Reserve"
        bot.placement_service.set_team_role_config.assert_not_awaited()
        args, kwargs = interaction.followup.send.call_args
        content = args[0] if args else kwargs["content"]
        assert "cleared" in content

    async def test_the_drivers_seated_in_reserve_follow_its_new_role(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=[
            {"name": "Reserve", "max_seats": 0, "is_reserve": True, "role_id": 555},
        ])
        cog = TeamCog(bot)
        interaction = _make_interaction()
        role = MagicMock()
        role.id = 999
        role.mention = "<@&999>"

        await _unwrap(cog.team_reserve_role)(cog, interaction, role=role)

        bot.placement_service.swap_team_role.assert_awaited_once_with(
            "Reserve", 555, 999, interaction.guild
        )


# ---------------------------------------------------------------------------
# A role belongs to one team only (decided 2026-09-22, with #375)
#
# A submission names a team by its role, and a result records the team the role resolves to,
# so a role two teams held would name either. Every command that sets a team's role refuses
# one another team holds, names that team, and changes nothing.
# ---------------------------------------------------------------------------

class TestOneRolePerTeam:
    @staticmethod
    def _role(role_id: int = 111):
        role = MagicMock()
        role.id = role_id
        role.mention = f"<@&{role_id}>"
        return role

    async def test_a_team_is_not_added_under_a_role_another_team_holds(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot()
        bot.placement_service.team_holding_role = AsyncMock(return_value="Ferrari")
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=self._role())

        bot.team_service.add_default_team.assert_not_awaited()
        bot.placement_service.set_team_role_config.assert_not_awaited()
        content = interaction.response.send_message.call_args.args[0]
        assert content.startswith("⛔") and '"Ferrari"' in content

    async def test_a_role_taken_at_the_last_moment_takes_the_new_team_away_again(self):
        """The check and the write are separate, so the write refuses too; the team it
        would have left behind without a role is removed."""
        from cogs.team_cog import TeamCog
        bot = _make_bot()
        bot.placement_service.set_team_role_config = AsyncMock(
            side_effect=ValueError('<@&111> is already the role of "Ferrari".')
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_add)(cog, interaction, name="Alpine", role=self._role())

        bot.team_service.remove_default_team.assert_awaited_once_with("Alpine")
        assert interaction.response.send_message.call_args.args[0].startswith("⛔")

    async def test_a_team_is_not_given_a_role_another_team_holds(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=[
            {"name": "Alpine", "max_seats": 2, "is_reserve": False, "role_id": 222},
        ])
        bot.placement_service.set_team_role_config = AsyncMock(
            side_effect=ValueError('<@&111> is already the role of "Ferrari".')
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_role)(cog, interaction, name="Alpine", role=self._role())

        bot.placement_service.swap_team_role.assert_not_awaited()
        bot.output_router.post_log.assert_not_awaited()
        assert '"Ferrari"' in interaction.followup.send.call_args.args[0]

    async def test_the_reserve_is_not_given_a_role_another_team_holds(self):
        from cogs.team_cog import TeamCog
        bot = _make_bot()
        bot.placement_service.set_team_role_config = AsyncMock(
            side_effect=ValueError('<@&111> is already the role of "Ferrari".')
        )
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_reserve_role)(cog, interaction, role=self._role())

        bot.placement_service.swap_team_role.assert_not_awaited()
        bot.output_router.post_log.assert_not_awaited()
        assert '"Ferrari"' in interaction.followup.send.call_args.args[0]


# ---------------------------------------------------------------------------
# /team lineup — the batch abandons its pictures on a rejection
#
# The one leak no per-send cleanup would catch: a division rejected part way through
# returns immediately, and every picture drawn for the divisions before it is never sent.
# There is no sweeper behind this, so the `finally` around the batch is the only thing
# that removes them.
# ---------------------------------------------------------------------------

class TestTeamLineupDiscardsItsPictures:
    @staticmethod
    def _artifact(tmp_path, div_id):
        directory = tmp_path / f"f1bot_render_lineup{div_id}"
        directory.mkdir()
        png = directory / "lineup_template.png"
        png.write_bytes(b"\x89PNG")
        return png

    @staticmethod
    def _division(div_id, tier):
        division = MagicMock()
        division.id = div_id
        division.tier = tier
        division.name = f"Division {tier}"
        return division

    def _cog_and_bot(self, divisions):
        from cogs.team_cog import TeamCog

        bot = _make_bot()
        bot.season_service.get_confirmed_season = AsyncMock(return_value=_make_season())
        bot.season_service.get_divisions = AsyncMock(return_value=divisions)
        return TeamCog(bot), bot

    async def test_a_rejection_part_way_through_leaves_no_picture_behind(
        self, tmp_path, monkeypatch
    ):
        from services import image_lineup_post

        divisions = [self._division(1, 1), self._division(2, 2)]
        drawn = self._artifact(tmp_path, 1)
        cog, _bot = self._cog_and_bot(divisions)

        outcomes = iter(
            [
                MagicMock(action="POSTED", png_path=drawn, notices=[], message=None),
                MagicMock(
                    action="REJECTED", png_path=None, notices=[],
                    message="❌ the template is at fault",
                ),
            ]
        )

        monkeypatch.setattr(
            image_lineup_post, "lineup_enabled", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            image_lineup_post,
            "render_for_command",
            AsyncMock(side_effect=lambda *a, **k: next(outcomes)),
        )

        interaction = _make_interaction()
        await _unwrap(cog.team_lineup)(cog, interaction, division=None, public=False)

        assert not drawn.exists(), (
            "a picture drawn before the rejection was abandoned and never removed"
        )
        assert not drawn.parent.exists()

    async def test_the_pictures_are_gone_once_the_batch_has_posted(
        self, tmp_path, monkeypatch
    ):
        from services import image_lineup_post

        divisions = [self._division(1, 1), self._division(2, 2)]
        pngs = [self._artifact(tmp_path, 1), self._artifact(tmp_path, 2)]
        cog, _bot = self._cog_and_bot(divisions)
        handed = iter(pngs)

        monkeypatch.setattr(
            image_lineup_post, "lineup_enabled", AsyncMock(return_value=True)
        )
        monkeypatch.setattr(
            image_lineup_post,
            "render_for_command",
            AsyncMock(
                side_effect=lambda *a, **k: MagicMock(
                    action="POSTED", png_path=next(handed), notices=[], message=None
                )
            ),
        )

        interaction = _make_interaction()
        await _unwrap(cog.team_lineup)(cog, interaction, division=None, public=False)

        interaction.followup.send.assert_awaited()
        for png in pngs:
            assert not png.exists()
