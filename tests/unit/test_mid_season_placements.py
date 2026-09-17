"""Confirming placements mid-season, in Ongoing, placements (issue #220).

The drivers of a window closed mid-season are placed uncommitted. Confirming commits them,
grants each their division's and team's roles, posts each affected lineup once, and returns the
season to Ongoing. The review is refused in the other ongoing stages.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.season_cog import SeasonCog  # noqa: E402
from db.database import get_connection  # noqa: E402
from models.season import SeasonStage  # noqa: E402
from services.placement_service import PlacementService  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402
from tests.unit.test_uncommitted_drivers_outside_attendance import (  # noqa: E402
    DIVISION_ID,
    SERVER_ID,
    db_path,  # noqa: F401 — the fixture
)


def _service(db_path) -> PlacementService:
    service = PlacementService.__new__(PlacementService)
    service._db_path = db_path
    service._bot = None
    service._refresh_lineup_post = AsyncMock()
    service._grant_roles = AsyncMock()
    service.get_team_role_config = AsyncMock(return_value=SimpleNamespace(role_id=777))
    return service


def _guild():
    guild = MagicMock()
    guild.get_member = MagicMock(return_value=MagicMock())
    return guild


# ── The service ────────────────────────────────────────────────────────────────────


async def test_the_uncommitted_placements_are_listed(db_path):
    placements = await _service(db_path).uncommitted_placements(1)

    assert [p["discord_user_id"] for p in placements] == ["1002"]
    assert placements[0]["team_name"] == "Alpha"


async def test_confirming_commits_grants_roles_and_posts_each_lineup_once(db_path):
    service = _service(db_path)

    committed = await service.commit_mid_season_placements(SERVER_ID, 1, _guild())

    assert [p["discord_user_id"] for p in committed] == ["1002"]
    assert await service.uncommitted_placements(1) == []
    role_ids = service._grant_roles.await_args.args[1:]
    assert role_ids == (1, 777)
    service._refresh_lineup_post.assert_awaited_once()
    assert service._refresh_lineup_post.await_args.args[1] == DIVISION_ID


async def test_confirming_with_nothing_uncommitted_does_nothing(db_path):
    service = _service(db_path)
    await service.commit_mid_season_placements(SERVER_ID, 1, _guild())
    service._refresh_lineup_post.reset_mock()

    assert await service.commit_mid_season_placements(SERVER_ID, 1, _guild()) == []
    service._refresh_lineup_post.assert_not_awaited()


# ── The command ────────────────────────────────────────────────────────────────────


def _cog(db_path, stage: SeasonStage) -> SeasonCog:
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    cog._pending = {}
    season = SimpleNamespace(id=1, season_number=1, stage=stage)
    cog.bot.season_service.get_confirmed_season = AsyncMock(return_value=season)
    cog.bot.season_service.set_stage = AsyncMock()
    cog.bot.placement_service.commit_mid_season_placements = AsyncMock(return_value=[{}])
    cog.bot.output_router.post_log = AsyncMock()
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.parametrize("stage", [SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS])
async def test_the_review_is_refused_in_the_other_ongoing_stages(db_path, stage):
    cog = _cog(db_path, stage)
    interaction = _interaction()

    await undecorate(SeasonCog.season_review)(cog, interaction)

    assert "only be reviewed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )


async def test_confirming_returns_the_season_to_ongoing(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'ASSIGNED'")
        await db.commit()
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_awaited_once()
    cog.bot.season_service.set_stage.assert_awaited_once_with(1, SeasonStage.ONGOING)


async def test_confirming_refuses_while_a_signup_is_unsettled(db_path):
    async with get_connection(db_path) as db:
        await db.execute("UPDATE driver_profiles SET current_state = 'UNASSIGNED' WHERE id = 3")
        await db.commit()
    cog = _cog(db_path, SeasonStage.ONGOING_PLACEMENTS)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_not_awaited()
    cog.bot.season_service.set_stage.assert_not_awaited()
    assert "Nothing has been confirmed" in interaction.followup.send.await_args.args[0]


async def test_confirming_a_season_no_longer_placing_confirms_nothing(db_path):
    cog = _cog(db_path, SeasonStage.ONGOING)
    interaction = _interaction()

    await cog._do_confirm_mid_season_placements(interaction)

    cog.bot.placement_service.commit_mid_season_placements.assert_not_awaited()


async def test_cancelling_discards_the_uncommitted_placements_and_frees_their_seats(db_path):
    from services.season_service import SeasonService

    assert await SeasonService(db_path).discard_uncommitted_placements(1) == 1

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM team_seats WHERE driver_profile_id = 2"
        )
        assert (await cursor.fetchone())[0] == 0
    assert await _service(db_path).uncommitted_placements(1) == []
