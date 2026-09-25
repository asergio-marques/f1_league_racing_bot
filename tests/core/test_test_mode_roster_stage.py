"""The test roster changes only while the season is in Placements (issue #220).

Fake drivers are seated, removed and cleared where real drivers are placed: in Placements.
Test mode replicates the live flow rather than inventing one of its own. Listing the roster
stays free.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from leaguebot.core.cogs.test_mode_cog import TestModeCog as _Cog
from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage, status_of_stage
from tests.support.undecorate import undecorate

SERVER_ID = 22030


async def _cog(tmp_path, stage: SeasonStage | None) -> _Cog:
    path = str(tmp_path / "roster_stage.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id, test_mode_active) VALUES (?, 1, 2, 3, 1)",
            (SERVER_ID,),
        )
        if stage is not None:
            await db.execute(
                "INSERT INTO seasons (start_date, status, season_number, stage) "
                "VALUES ('2026-09-17', ?, 1, ?)",
                (status_of_stage(stage).value, stage.value),
            )
        await db.commit()
    cog = _Cog.__new__(_Cog)
    cog.bot = MagicMock()
    cog.bot.db_path = path
    cog.bot.config_service.get_server_config = AsyncMock(
        return_value=SimpleNamespace(test_mode_active=True)
    )
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_the_roster_may_change_in_placements(tmp_path):
    cog = await _cog(tmp_path, SeasonStage.PLACEMENTS)
    interaction = _interaction()

    assert await cog._refuse_roster_change_outside_placements(interaction) is False
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.parametrize(
    "stage", [None, SeasonStage.CONFIGURATION, SeasonStage.ONGOING, SeasonStage.COMPLETED]
)
async def test_the_roster_may_not_change_outside_placements(tmp_path, stage):
    cog = await _cog(tmp_path, stage)
    interaction = _interaction()

    assert await cog._refuse_roster_change_outside_placements(interaction) is True
    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )


@pytest.mark.parametrize(
    "command, args",
    [
        ("roster_add", ("Mock", "Redline", "Pro")),
        ("roster_add_bulk", ()),
        ("roster_remove", ("9000000000000000001",)),
        ("roster_clear", ("Pro",)),
    ],
)
async def test_every_roster_change_is_guarded(tmp_path, command, args):
    cog = await _cog(tmp_path, SeasonStage.ONGOING)
    interaction = _interaction()
    interaction.response.send_modal = AsyncMock()

    await undecorate(getattr(_Cog, command))(cog, interaction, *args)

    assert "only be changed while the season is in placements" in (
        interaction.response.send_message.await_args.args[0]
    )
    interaction.response.send_modal.assert_not_awaited()
