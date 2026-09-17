"""The signup window moves the season through its lifecycle (issue #220).

Opening the window moves Waiting to Signups, and Ongoing to Ongoing, signups open. Closing it
moves Signups to Placements, and Ongoing, signups open to Ongoing, placements where signups
remain unsettled or to Ongoing where none do.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from db.database import get_connection, run_migrations  # noqa: E402
from models.season import SeasonStage, status_of_stage  # noqa: E402
from services import season_lifecycle_service as lifecycle  # noqa: E402

SERVER_ID = 22001


@pytest.fixture
async def db_path(tmp_path):
    path = str(tmp_path / "lifecycle_window.db")
    await run_migrations(path)
    async with get_connection(path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 1, 2, 3)",
            (SERVER_ID,),
        )
        await db.commit()
    return path


async def _season(db_path, stage: SeasonStage) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (server_id, start_date, status, season_number, stage) "
            "VALUES (?, '2026-09-17', ?, 1, ?)",
            (SERVER_ID, status_of_stage(stage).value, stage.value),
        )
        await db.commit()
        return cursor.lastrowid


async def _driver(db_path, uid: str, state: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (server_id, discord_user_id, current_state) "
            "VALUES (?, ?, ?)",
            (SERVER_ID, uid, state),
        )
        await db.commit()


async def _stage(db_path) -> SeasonStage | None:
    found = await lifecycle.live_season_stage(db_path, SERVER_ID)
    return found[1] if found else None


# ── Opening ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "start, expected",
    [
        (SeasonStage.WAITING, SeasonStage.SIGNUPS),
        (SeasonStage.ONGOING, SeasonStage.ONGOING_SIGNUPS),
    ],
)
async def test_opening_the_window_moves_the_season_on(db_path, start, expected):
    await _season(db_path, start)

    assert await lifecycle.advance_on_window_open(db_path, SERVER_ID) is expected
    assert await _stage(db_path) is expected


@pytest.mark.parametrize(
    "stage",
    [SeasonStage.CONFIGURATION, SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS,
     SeasonStage.PENDING_COMPLETION],
)
async def test_the_window_opens_from_no_other_stage(db_path, stage):
    await _season(db_path, stage)

    assert stage not in lifecycle.WINDOW_OPENS_FROM
    assert await lifecycle.advance_on_window_open(db_path, SERVER_ID) is None
    assert await _stage(db_path) is stage


async def test_opening_with_no_season_moves_nothing(db_path):
    assert await lifecycle.advance_on_window_open(db_path, SERVER_ID) is None


# ── Closing ────────────────────────────────────────────────────────────────────────


async def test_closing_signups_moves_the_season_to_placements(db_path):
    await _season(db_path, SeasonStage.SIGNUPS)
    await _driver(db_path, "1", "UNASSIGNED")

    assert await lifecycle.advance_on_window_close(db_path, SERVER_ID) is SeasonStage.PLACEMENTS


@pytest.mark.parametrize("state", lifecycle.UNSETTLED_STATES)
async def test_a_mid_season_close_with_unsettled_signups_leaves_placements(db_path, state):
    await _season(db_path, SeasonStage.ONGOING_SIGNUPS)
    await _driver(db_path, "1", state)

    target = await lifecycle.advance_on_window_close(db_path, SERVER_ID)

    assert target is SeasonStage.ONGOING_PLACEMENTS
    assert await _stage(db_path) is SeasonStage.ONGOING_PLACEMENTS


async def test_a_mid_season_close_with_nothing_unsettled_returns_to_ongoing(db_path):
    await _season(db_path, SeasonStage.ONGOING_SIGNUPS)
    await _driver(db_path, "1", "ASSIGNED")
    await _driver(db_path, "2", "NOT_SIGNED_UP")

    assert await lifecycle.advance_on_window_close(db_path, SERVER_ID) is SeasonStage.ONGOING


@pytest.mark.parametrize(
    "stage", [SeasonStage.CONFIGURATION, SeasonStage.WAITING, SeasonStage.ONGOING]
)
async def test_a_close_outside_a_window_stage_moves_nothing(db_path, stage):
    await _season(db_path, stage)

    assert await lifecycle.advance_on_window_close(db_path, SERVER_ID) is None
    assert await _stage(db_path) is stage


async def test_an_archived_season_is_not_the_live_one(db_path):
    await _season(db_path, SeasonStage.COMPLETED)
    assert await lifecycle.live_season_stage(db_path, SERVER_ID) is None


async def test_the_forced_close_moves_the_season_on(db_path):
    """Every close path runs through `execute_forced_close`, so it is where the move lives."""
    from unittest.mock import AsyncMock, MagicMock

    from cogs.module_cog import execute_forced_close

    await _season(db_path, SeasonStage.SIGNUPS)
    bot = MagicMock()
    bot.db_path = db_path
    cfg = MagicMock(signup_button_message_id=None, signup_channel_id=None)
    bot.signup_module_service.get_config = AsyncMock(return_value=cfg)
    bot.signup_module_service.set_window_closed = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)

    await execute_forced_close(SERVER_ID, bot, audit_action="SIGNUP_CLOSE")

    assert await _stage(db_path) is SeasonStage.PLACEMENTS


async def test_a_season_that_cannot_move_on_does_not_undo_the_close(db_path):
    """The window is shut either way; the season's move is logged and let go."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from cogs.module_cog import execute_forced_close

    await _season(db_path, SeasonStage.SIGNUPS)
    bot = MagicMock()
    bot.db_path = db_path
    cfg = MagicMock(signup_button_message_id=None, signup_channel_id=None)
    bot.signup_module_service.get_config = AsyncMock(return_value=cfg)
    bot.signup_module_service.set_window_closed = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)

    with patch(
        "services.season_lifecycle_service.advance_on_window_close",
        new=AsyncMock(side_effect=RuntimeError("database is locked")),
    ):
        await execute_forced_close(SERVER_ID, bot, audit_action="SIGNUP_CLOSE")

    bot.signup_module_service.set_window_closed.assert_awaited_once()
