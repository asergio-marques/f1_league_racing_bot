"""`/module enable` and `/module disable` answer to the season's stage (issue #220).

- The signup module is enabled and disabled only with no active season, or while its season
  is in Configuration.
- No other module is enabled once the season's placements have been confirmed.
- No module is disabled while the season is in Pending completion.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from cogs.module_cog import ModuleCog  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage, status_of_stage  # noqa: E402
from tests.support.undecorate import undecorate  # noqa: E402

SERVER_ID = 22020
OTHERS = ["weather", "results", "attendance", "images"]


async def _db(tmp_path, stage: SeasonStage | None) -> str:
    path = str(tmp_path / "module_gates.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        if stage is not None:
            await db.execute(
                "INSERT INTO seasons (server_id, start_date, status, season_number, stage) "
                "VALUES (?, '2026-09-17', ?, 5, ?)",
                (SERVER_ID, status_of_stage(stage).value, stage.value),
            )
        await db.commit()
    return path


def _cog(db_path: str, stage: SeasonStage | None) -> ModuleCog:
    cog = ModuleCog.__new__(ModuleCog)
    cog.bot = MagicMock()
    cog.bot.db_path = db_path
    confirmed = stage is not None and status_of_stage(stage).value == "ACTIVE"
    cog.bot.season_service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=1) if confirmed else None
    )
    for verb in ("enable", "disable"):
        for module in ["signup", *OTHERS]:
            setattr(cog, f"_{verb}_{module}", AsyncMock())
    return cog


def _interaction():
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock()
    return interaction


def _choice(value):
    return SimpleNamespace(name=value.title(), value=value)


async def _run(cog, verb, module):
    interaction = _interaction()
    await undecorate(getattr(ModuleCog, verb))(cog, interaction, _choice(module))
    handler = getattr(cog, f"_{verb}_{module}")
    return interaction, handler


# ── Signup ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("verb", ["enable", "disable"])
@pytest.mark.parametrize("stage", [None, SeasonStage.CONFIGURATION, SeasonStage.COMPLETED])
async def test_signup_is_free_with_no_season_or_in_configuration(tmp_path, verb, stage):
    cog = _cog(await _db(tmp_path, stage), stage)

    _, handler = await _run(cog, verb, "signup")

    handler.assert_awaited_once()


@pytest.mark.parametrize("verb", ["enable", "disable"])
@pytest.mark.parametrize(
    "stage", [SeasonStage.WAITING, SeasonStage.PLACEMENTS, SeasonStage.ONGOING]
)
async def test_signup_is_fixed_once_the_configuration_is_confirmed(tmp_path, verb, stage):
    cog = _cog(await _db(tmp_path, stage), stage)

    interaction, handler = await _run(cog, verb, "signup")

    handler.assert_not_awaited()
    assert "fixed for Season 5" in interaction.response.send_message.await_args.args[0]


# ── Every other module ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("module", OTHERS)
@pytest.mark.parametrize("stage", [SeasonStage.WAITING, SeasonStage.PLACEMENTS])
async def test_another_module_may_be_enabled_before_placements_are_confirmed(
    tmp_path, module, stage
):
    cog = _cog(await _db(tmp_path, stage), stage)

    _, handler = await _run(cog, "enable", module)

    handler.assert_awaited_once()


@pytest.mark.parametrize("module", OTHERS)
@pytest.mark.parametrize(
    "stage", [SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS, SeasonStage.PENDING_COMPLETION]
)
async def test_no_module_is_enabled_once_placements_are_confirmed(tmp_path, module, stage):
    cog = _cog(await _db(tmp_path, stage), stage)

    interaction, handler = await _run(cog, "enable", module)

    handler.assert_not_awaited()
    assert "cannot be enabled" in interaction.response.send_message.await_args.args[0]


@pytest.mark.parametrize("module", OTHERS)
async def test_a_module_may_be_disabled_while_ongoing(tmp_path, module):
    cog = _cog(await _db(tmp_path, SeasonStage.ONGOING), SeasonStage.ONGOING)

    _, handler = await _run(cog, "disable", module)

    handler.assert_awaited_once()


@pytest.mark.parametrize("module", OTHERS)
async def test_no_module_is_disabled_in_pending_completion(tmp_path, module):
    stage = SeasonStage.PENDING_COMPLETION
    cog = _cog(await _db(tmp_path, stage), stage)

    interaction, handler = await _run(cog, "disable", module)

    handler.assert_not_awaited()
    assert "pending completion" in interaction.response.send_message.await_args.args[0]
