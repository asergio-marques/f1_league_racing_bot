"""A season moves to Pending completion once every division is done (issue #220).

Only a season in plain Ongoing moves. One with a signup window open, or placements still to
confirm, waits — and moves as soon as it returns to Ongoing. A division is done when it is
finished or cancelled. From Pending completion the season is completed, and from nowhere else.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage, status_of_stage  # noqa: E402
from services import season_lifecycle_service as lifecycle  # noqa: E402
from services.season_service import SeasonService  # noqa: E402

SERVER_ID = 22110
SEASON_ID = 1


async def _db(tmp_path, stage=SeasonStage.ONGOING, divisions=(("ACTIVE", "FINAL"), ("FINISHED", None))):
    """A season in *stage*; each division given as (status, status of its one round or None)."""
    path = str(tmp_path / "pending.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, server_id, start_date, status, season_number, stage) "
            "VALUES (?, ?, '2026-09-17', ?, 1, ?)",
            (SEASON_ID, SERVER_ID, status_of_stage(stage).value, stage.value),
        )
        for index, (division_status, round_status) in enumerate(divisions, start=1):
            await db.execute(
                "INSERT INTO divisions (id, season_id, name, mention_role_id, tier, status) "
                "VALUES (?, ?, ?, 1, ?, ?)",
                (index, SEASON_ID, f"D{index}", index, division_status),
            )
            if round_status is not None:
                await db.execute(
                    "INSERT INTO rounds (division_id, round_number, format, track_name, "
                    "scheduled_at, status) VALUES (?, 1, 'NORMAL', 'Silverstone Circuit', "
                    "'2026-06-01T14:00:00', ?)",
                    (index, round_status),
                )
        await db.commit()
    return path


async def _stage(path):
    return await SeasonService(path).get_stage(SEASON_ID)


async def test_a_season_whose_last_division_finishes_is_pending_completion(tmp_path):
    path = await _db(tmp_path)

    assert await SeasonService(path).refresh_division_status(1) is True

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


async def test_a_division_still_running_holds_the_season_ongoing(tmp_path):
    path = await _db(tmp_path, divisions=(("ACTIVE", "FINAL"), ("ACTIVE", "NOT_RUN")))

    await SeasonService(path).refresh_division_status(1)

    assert await _stage(path) is SeasonStage.ONGOING


async def test_cancelling_the_last_running_division_leaves_the_season_pending_completion(tmp_path):
    path = await _db(tmp_path, divisions=(("ACTIVE", "NOT_RUN"), ("FINISHED", None)))

    await SeasonService(path).cancel_division(1, SERVER_ID, 1, "Admin")

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


@pytest.mark.parametrize(
    "stage", [SeasonStage.ONGOING_SIGNUPS, SeasonStage.ONGOING_PLACEMENTS]
)
async def test_a_season_with_a_window_or_placements_waits(tmp_path, stage):
    path = await _db(tmp_path, stage=stage, divisions=(("FINISHED", None), ("CANCELLED", None)))

    assert await lifecycle.advance_to_pending_completion(path, SEASON_ID) is False

    assert await _stage(path) is stage


async def test_a_season_returning_to_ongoing_moves_on_at_once(tmp_path):
    path = await _db(
        tmp_path, stage=SeasonStage.ONGOING_SIGNUPS, divisions=(("FINISHED", None),)
    )

    assert await lifecycle.advance_on_window_close(path, SERVER_ID) is SeasonStage.ONGOING

    assert await _stage(path) is SeasonStage.PENDING_COMPLETION


async def test_a_season_with_no_division_is_not_pending_completion(tmp_path):
    path = await _db(tmp_path, divisions=())

    assert await lifecycle.advance_to_pending_completion(path, SEASON_ID) is False


# ── /season complete ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "stage, says",
    [
        (SeasonStage.ONGOING_SIGNUPS, "/signup close"),
        (SeasonStage.ONGOING_PLACEMENTS, "placements-review"),
    ],
)
async def test_completing_is_refused_while_a_window_or_placements_stand(tmp_path, stage, says):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from cogs.season_cog import SeasonCog
    from tests.support.undecorate import undecorate

    path = await _db(tmp_path, stage=stage, divisions=(("FINISHED", None),))
    cog = SeasonCog.__new__(SeasonCog)
    cog.bot = MagicMock()
    cog.bot.db_path = path
    service = SeasonService(path)
    cog.bot.season_service = service
    service.get_confirmed_season = AsyncMock(
        return_value=SimpleNamespace(id=SEASON_ID, stage=stage)
    )
    interaction = MagicMock()
    interaction.guild_id = SERVER_ID
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()

    await undecorate(SeasonCog.season_complete)(cog, interaction)

    assert says in interaction.response.send_message.await_args.args[0]
    interaction.response.defer.assert_not_awaited()
