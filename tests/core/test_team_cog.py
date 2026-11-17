"""Unit tests for TeamCog — /team list and /team lineup; the commands that change the team list
are `test_team_changes.py`'s."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from tests.support.undecorate import undecorate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interaction(guild_id: int = 1) -> MagicMock:
    """An interaction whose response knows whether it has been used, as Discord's does, so that
    a refusal answers by `response` until the interaction is answered or deferred (#482)."""
    state = {"done": False}

    async def _answer(*_args, **_kwargs):
        state["done"] = True

    interaction = MagicMock()
    interaction.guild_id = guild_id
    interaction.user.id = 42
    interaction.user.__str__ = lambda self: "admin#0001"
    interaction.response.is_done = MagicMock(side_effect=lambda: state["done"])
    interaction.response.send_message = AsyncMock(side_effect=_answer)
    interaction.response.defer = AsyncMock(side_effect=_answer)
    interaction.followup.send = AsyncMock()
    return interaction


def _make_season(season_number: int = 3, season_id: int = 1, stage=None) -> MagicMock:
    from leaguebot.core.models.season import SeasonStage
    season = MagicMock()
    season.id = season_id
    season.season_number = season_number
    season.stage = stage if stage is not None else SeasonStage.PLACEMENTS
    return season


def _make_bot(
    *,
    setup_season=None,
    season_team_names: set | None = None,
    teams_with_roles: list | None = None,
) -> MagicMock:
    from leaguebot.core.services.drivers.team_service import TeamService

    bot = MagicMock()
    # Held to the service's real methods, so a stub cannot outlive the method it stands for.
    bot.team_service = MagicMock(spec_set=TeamService)
    bot.team_service.get_setup_season_team_names = AsyncMock(return_value=season_team_names or set())
    bot.team_service.get_teams_with_roles = AsyncMock(return_value=teams_with_roles or [])
    bot.season_service.get_setup_season = AsyncMock(return_value=setup_season)
    bot.output_router.post_log = AsyncMock()
    return bot


def _unwrap(cmd):
    """Return the innermost callback, past whatever tier guard the command wears."""
    return undecorate(cmd)


# ---------------------------------------------------------------------------
# /team list
# ---------------------------------------------------------------------------

class TestTeamList:
    async def test_no_teams_returns_empty_state(self):
        from leaguebot.core.cogs.team_cog import TeamCog
        bot = _make_bot(teams_with_roles=[], setup_season=None)
        cog = TeamCog(bot)
        interaction = _make_interaction()

        await _unwrap(cog.team_list)(cog, interaction)

        interaction.followup.send.assert_awaited_once()
        args, kwargs = interaction.followup.send.call_args
        content = args[0] if args else kwargs["content"]
        assert "No teams" in content

    async def test_teams_no_season_shows_server_list(self):
        from leaguebot.core.cogs.team_cog import TeamCog
        teams = [
            {"name": "Ferrari", "full_name": "Ferrari Racing", "max_seats": 2, "is_reserve": False, "role_id": 111},
            {"name": "Alpine", "full_name": "Alpine Racing", "max_seats": 2, "is_reserve": False, "role_id": None},
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
        from leaguebot.core.cogs.team_cog import TeamCog
        teams = [
            {"name": "Ferrari", "full_name": "Ferrari Racing", "max_seats": 2, "is_reserve": False, "role_id": None},
            {"name": "Alpine", "full_name": "Alpine Racing", "max_seats": 2, "is_reserve": False, "role_id": None},
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
        from leaguebot.core.cogs.team_cog import TeamCog
        teams = [
            {"name": "Ferrari", "full_name": "Ferrari Racing", "max_seats": 2, "is_reserve": False, "role_id": None},
            {"name": "Alpine", "full_name": "Alpine Racing", "max_seats": 2, "is_reserve": False, "role_id": None},
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
# /team lineup — the pictures of several divisions go out together
#
# Which divisions are drawn, the refusal and the release of every picture drawn are
# `lineups_for_command`'s, tested in `test_lineup_command.py`; the reply is the command's.
# ---------------------------------------------------------------------------

class TestTeamLineupSendsItsPictures:
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

    def _cog_and_bot(self, divisions, drawing):
        from leaguebot.core.cogs.team_cog import TeamCog

        bot = _make_bot()
        bot.season_service.get_confirmed_season = AsyncMock(return_value=_make_season())
        bot.season_service.get_divisions = AsyncMock(return_value=divisions)
        bot.image_drawing = drawing
        return TeamCog(bot), bot

    async def test_the_pictures_of_every_division_go_out_in_one_message(self, tmp_path):
        """Two divisions, both drawn: the two pictures go out in one message, which names each
        division in bold on a line of its own."""
        from tests.support.lineup_drawing import LineupDrawingStub

        divisions = [self._division(1, 1), self._division(2, 2)]
        pngs = [self._artifact(tmp_path, 1), self._artifact(tmp_path, 2)]
        drawing = LineupDrawingStub(
            by_division={1: {"picture": pngs[0]}, 2: {"picture": pngs[1]}}
        )
        cog, _bot = self._cog_and_bot(divisions, drawing)

        interaction = _make_interaction()
        await _unwrap(cog.team_lineup)(cog, interaction, division=None, public=False)

        interaction.followup.send.assert_awaited_once()
        assert interaction.followup.send.await_args.args[0] == "**Division 1**\n**Division 2**"
        assert [each.filename for each in interaction.followup.send.await_args.kwargs["files"]] == [
            "lineup_template.png", "lineup_template.png"
        ]
