"""The signup window moves the season through its lifecycle (issue #220).

Opening the window moves Waiting to Signups, and Ongoing to Ongoing, signups open. Closing it
moves Signups to Placements, and Ongoing, signups open to Ongoing, placements where signups
remain unsettled or to Ongoing where none do.
"""
from __future__ import annotations

import pytest

from leaguebot.core.db.database import get_connection, run_migrations
from leaguebot.core.models.season import SeasonStage, status_of_stage
from leaguebot.core.services import season_lifecycle_service as lifecycle

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
            "INSERT INTO seasons (start_date, status, season_number, stage) "
            "VALUES ('2026-09-17', ?, 1, ?)",
            (status_of_stage(stage).value, stage.value),
        )
        await db.commit()
        return cursor.lastrowid


async def _driver(db_path, uid: str, state: str) -> None:
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO driver_profiles (discord_user_id, current_state) "
            "VALUES (?, ?)",
            (uid, state),
        )
        await db.commit()


async def _stage(db_path) -> SeasonStage | None:
    found = await lifecycle.live_season_stage(db_path)
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

    assert await lifecycle.advance_on_window_open(db_path) is expected
    assert await _stage(db_path) is expected


@pytest.mark.parametrize(
    "stage",
    [SeasonStage.CONFIGURATION, SeasonStage.PLACEMENTS, SeasonStage.ONGOING_PLACEMENTS,
     SeasonStage.PENDING_COMPLETION],
)
async def test_the_window_opens_from_no_other_stage(db_path, stage):
    await _season(db_path, stage)

    assert stage not in lifecycle.WINDOW_OPENS_FROM
    assert await lifecycle.advance_on_window_open(db_path) is None
    assert await _stage(db_path) is stage


async def test_opening_with_no_season_moves_nothing(db_path):
    assert await lifecycle.advance_on_window_open(db_path) is None


# ── Closing ────────────────────────────────────────────────────────────────────────


async def test_closing_signups_moves_the_season_to_placements(db_path):
    await _season(db_path, SeasonStage.SIGNUPS)
    await _driver(db_path, "1", "UNASSIGNED")

    assert await lifecycle.advance_on_window_close(db_path) is SeasonStage.PLACEMENTS


@pytest.mark.parametrize("state", lifecycle.UNSETTLED_STATES)
async def test_a_mid_season_close_with_unsettled_signups_leaves_placements(db_path, state):
    await _season(db_path, SeasonStage.ONGOING_SIGNUPS)
    await _driver(db_path, "1", state)

    target = await lifecycle.advance_on_window_close(db_path)

    assert target is SeasonStage.ONGOING_PLACEMENTS
    assert await _stage(db_path) is SeasonStage.ONGOING_PLACEMENTS


async def test_a_mid_season_close_with_nothing_unsettled_returns_to_ongoing(db_path):
    await _season(db_path, SeasonStage.ONGOING_SIGNUPS)
    await _driver(db_path, "1", "ASSIGNED")
    await _driver(db_path, "2", "NOT_SIGNED_UP")

    assert await lifecycle.advance_on_window_close(db_path) is SeasonStage.ONGOING


@pytest.mark.parametrize(
    "stage", [SeasonStage.CONFIGURATION, SeasonStage.WAITING, SeasonStage.ONGOING]
)
async def test_a_close_outside_a_window_stage_moves_nothing(db_path, stage):
    await _season(db_path, stage)

    assert await lifecycle.advance_on_window_close(db_path) is None
    assert await _stage(db_path) is stage


async def test_an_archived_season_is_not_the_live_one(db_path):
    await _season(db_path, SeasonStage.COMPLETED)
    assert await lifecycle.live_season_stage(db_path) is None


async def test_the_forced_close_moves_the_season_on(db_path):
    """Every close path runs through `execute_forced_close`, so it is where the move lives."""
    from unittest.mock import AsyncMock, MagicMock

    from leaguebot.core.cogs.module_cog import execute_forced_close

    await _season(db_path, SeasonStage.SIGNUPS)
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    cfg = MagicMock(signup_button_message_id=None, signup_channel_id=None)
    bot.signup_module_service.get_config = AsyncMock(return_value=cfg)
    bot.signup_module_service.set_window_closed = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)

    await execute_forced_close(bot, audit_action="SIGNUP_CLOSE")

    assert await _stage(db_path) is SeasonStage.PLACEMENTS


async def test_a_season_that_cannot_move_on_does_not_undo_the_close(db_path):
    """The window is shut either way; the season's move is logged and let go."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.cogs.module_cog import execute_forced_close

    await _season(db_path, SeasonStage.SIGNUPS)
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    cfg = MagicMock(signup_button_message_id=None, signup_channel_id=None)
    bot.signup_module_service.get_config = AsyncMock(return_value=cfg)
    bot.signup_module_service.set_window_closed = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)

    with patch(
        "leaguebot.core.services.season_lifecycle_service.advance_on_window_close",
        new=AsyncMock(side_effect=RuntimeError("database is locked")),
    ):
        await execute_forced_close(bot, audit_action="SIGNUP_CLOSE")

    bot.signup_module_service.set_window_closed.assert_awaited_once()


# ── A season the close's window left behind (#439, slice 5) ─────────────────────────


def _start_bot(db_path, *, window_open: bool):
    """A bot double as the start-up finds it: signup on, its window open or recorded closed."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    bot = MagicMock()
    bot.db_path = db_path
    bot.module_service.is_signup_enabled = AsyncMock(return_value=True)
    bot.signup_module_service.get_config = AsyncMock(
        return_value=SimpleNamespace(signups_open=window_open)
    )
    return bot


async def _start(bot) -> None:
    from leaguebot.__main__ import _recover_season_left_by_a_closed_window

    await _recover_season_left_by_a_closed_window(bot)


@pytest.mark.parametrize(
    "stage, expected",
    [
        (SeasonStage.SIGNUPS, SeasonStage.PLACEMENTS),
        (SeasonStage.ONGOING_SIGNUPS, SeasonStage.ONGOING),
    ],
)
async def test_a_season_left_in_its_window_stage_by_a_close_cut_off_is_moved_on_at_start(
    db_path, stage, expected,
):
    """A close was killed after recording the window closed and before moving the season on, so
    the season stands in Signups (or Ongoing, signups open, nothing left unsettled) with no
    window open. When the bot starts, the season is moved on as the close would have moved it."""
    await _season(db_path, stage)

    await _start(_start_bot(db_path, window_open=False))

    assert await _stage(db_path) is expected


async def test_a_season_a_close_failed_to_move_on_is_moved_on_at_the_next_start(db_path):
    """`/signup close` records the window closed, then fails to move the season on, which it
    names and lets go: the season stands in Signups. At the next start it is moved on to
    Placements."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from leaguebot.core.cogs.module_cog import execute_forced_close

    await _season(db_path, SeasonStage.SIGNUPS)
    bot = MagicMock()
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.db_path = db_path
    cfg = MagicMock(signup_button_message_id=None, signup_channel_id=None)
    bot.signup_module_service.get_config = AsyncMock(return_value=cfg)
    bot.signup_module_service.set_window_closed = AsyncMock()
    bot.get_guild = MagicMock(return_value=None)
    with patch(
        "leaguebot.core.services.season_lifecycle_service.advance_on_window_close",
        new=AsyncMock(side_effect=RuntimeError("database is locked")),
    ):
        outcome = await execute_forced_close(bot, audit_action="SIGNUP_CLOSE")
    assert "The season could not be moved on." in outcome.failed
    assert await _stage(db_path) is SeasonStage.SIGNUPS

    await _start(_start_bot(db_path, window_open=False))

    assert await _stage(db_path) is SeasonStage.PLACEMENTS


async def test_a_season_whose_window_stands_open_is_never_moved_on_at_start(db_path):
    """The season stands in Signups with its window open, a later window than any close cut
    off: the start leaves it in Signups."""
    await _season(db_path, SeasonStage.SIGNUPS)

    await _start(_start_bot(db_path, window_open=True))

    assert await _stage(db_path) is SeasonStage.SIGNUPS


async def test_a_season_whose_end_is_in_hand_is_left_to_it_at_start(db_path):
    """The season stands in Ongoing, signups open with no window open, its cancellation in hand
    on the queue, which closed the window and moves nothing until it records the season's end.
    The start leaves the stage to it."""
    from tests.support.season_league import SEASON_CANCEL_KIND, seed_season_end

    season_id = await _season(db_path, SeasonStage.ONGOING_SIGNUPS)
    await seed_season_end(db_path, SEASON_CANCEL_KIND, season_id=season_id, stopped=True)

    await _start(_start_bot(db_path, window_open=False))

    assert await _stage(db_path) is SeasonStage.ONGOING_SIGNUPS


def test_the_season_is_moved_on_after_the_close_timers_and_before_the_finish_is_asked():
    """After the close timers' recovery, whose restart close moves its season itself, and before
    the finish of a close a stop cut off is asked. Read from the source, because both run inside
    `on_ready` among a dozen start-up steps no test drives whole."""
    import inspect

    from leaguebot import __main__ as bot_module

    source = inspect.getsource(bot_module.main)
    timers = source.index("await _recover_signup_close_timers()")
    moved = source.index("await _recover_season_left_by_a_closed_window(bot)")
    finish = source.index("await _recover_off_queue_closing_notices(bot)")
    assert timers < moved < finish
