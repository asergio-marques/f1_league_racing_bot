"""What the bot puts right when it starts — the orphaned amendment channel sweep.

Issue #208. `bot.py` was the worst-covered file in core at 26.7%. Most of it is `main()`, which
needs a real gateway connection and is out of scope for this suite (`CLAUDE.md`: no test may
require a live bot). The recovery helpers beneath it are not — they take a bot and a database
and put the world back the way it should be, and none was executed.

This file takes `_recover_orphaned_amend_channels`, the one whose failure mode is a channel
left behind on every restart, and `_recover_portrait_refresh_job`, which re-arms the daily
portrait refresh.

**The database row is deleted before the Discord channel is.** That ordering is the point of
the function: a crash between the two leaves a channel nobody will clean up, which is
untidy — but the other order leaves a *row* whose channel is already gone, and the next restart
would try to process it again, and the one after that, indefinitely.
`test_the_row_is_removed_before_the_channel` is what holds the ordering, and it is invisible
from reading the code unless somebody already knows why.

**Every Discord failure is swallowed.** The channel may have been deleted by hand, the guild
may no longer be one the bot is in, and the log channel may be unreachable. None of them may
stop the sweep, because the sweep runs during startup — an exception here would take the whole
bot down over a leftover channel.

**The league is told.** An amendment channel vanishing means the command that made it was never
finished, and the manager has to run it again; without the log line the work would simply be
missing and nobody would know to redo it.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from bot import _recover_orphaned_amend_channels, _recover_portrait_refresh_job  # noqa: E402
from db.database import get_connection, run_migrations  # noqa: E402

SERVER_ID = 10108
SEASON_ID = 1
DIVISION_ID = 11
ROUND_ID = 5
CHANNEL_ID = 770601


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


async def _make_db(tmp_path, *, channels: int = 1) -> str:
    db_path = os.path.join(str(tmp_path), "recovery.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO server_configs (server_id, interaction_role_id, "
            "interaction_channel_id, log_channel_id) VALUES (?, 900, 100, 101)",
            (SERVER_ID,),
        )
        await db.execute(
            "INSERT INTO seasons (id, season_number, start_date, status) "
            "VALUES (?, 1, '2026-01-01', 'ACTIVE')",
            (SEASON_ID,),
        )
        await db.execute(
            "INSERT INTO divisions (id, season_id, name, tier, mention_role_id) "
            "VALUES (?, ?, 'Division 1', 1, 555)",
            (DIVISION_ID, SEASON_ID),
        )
        await db.execute(
            "INSERT INTO rounds (id, division_id, round_number, format, track_name, "
            "scheduled_at) VALUES (?, ?, 3, 'NORMAL', 'Silverstone Circuit', '2026-06-01')",
            (ROUND_ID, DIVISION_ID),
        )
        # One row per session type: the table is unique on (round, session type), which is
        # the real shape — a round can have an amendment open per session, not two for one.
        session_types = ("FULL_RACE", "FULL_QUALIFYING", "LONG_SPRINT_RACE")
        for index in range(channels):
            await db.execute(
                "INSERT INTO round_amend_channels "
                "(id, round_id, channel_id, session_type, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    index + 1,
                    ROUND_ID,
                    CHANNEL_ID + index,
                    session_types[index],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        await db.commit()
    return db_path


def _make_bot(db_path: str, *, channel=..., guild_found: bool = True):
    bot = MagicMock()
    bot.db_path = db_path
    bot.output_router = MagicMock()
    bot.output_router.post_log = AsyncMock(return_value=None)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)

    resolved = MagicMock() if channel is ... else channel
    if resolved is not None:
        resolved.delete = AsyncMock(return_value=None)

    guild = MagicMock()
    guild.get_channel = MagicMock(return_value=resolved)
    bot.get_guild = MagicMock(return_value=guild if guild_found else None)
    bot._channel = resolved
    return bot


async def _rows(db_path: str) -> int:
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT COUNT(*) AS n FROM round_amend_channels")
        return (await cursor.fetchone())["n"]


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


async def test_an_orphaned_channel_is_deleted(tmp_path):
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)

    await _recover_orphaned_amend_channels(bot)

    bot._channel.delete.assert_awaited_once()


async def test_the_row_is_removed(tmp_path):
    db_path = await _make_db(tmp_path)

    await _recover_orphaned_amend_channels(_make_bot(db_path))

    assert await _rows(db_path) == 0


async def test_the_row_is_removed_before_the_channel(tmp_path):
    """The ordering is the point of the function. A crash between the two leaves a channel
    nobody cleans up, which is untidy — the other order leaves a *row* whose channel is
    already gone, and every restart from then on would try to process it again."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)
    rows_when_deleted: list[int] = []

    async def _delete(**kwargs):
        rows_when_deleted.append(await _rows(db_path))

    bot._channel.delete = AsyncMock(side_effect=_delete)

    await _recover_orphaned_amend_channels(bot)

    assert rows_when_deleted == [0]


async def test_every_orphan_is_swept_not_only_the_first(tmp_path):
    """A crash during a busy round can leave several behind at once."""
    db_path = await _make_db(tmp_path, channels=3)

    await _recover_orphaned_amend_channels(_make_bot(db_path))

    assert await _rows(db_path) == 0


async def test_nothing_to_sweep_is_not_an_error(tmp_path):
    """The ordinary case on almost every start."""
    db_path = await _make_db(tmp_path, channels=0)
    bot = _make_bot(db_path)

    await _recover_orphaned_amend_channels(bot)

    bot.output_router.post_log.assert_not_awaited()


# ---------------------------------------------------------------------------
# Everything that can go wrong during startup
# ---------------------------------------------------------------------------


async def test_a_guild_the_bot_has_left_still_clears_the_row(tmp_path):
    """The row is the bot's own bookkeeping and must not outlive the server it names."""
    db_path = await _make_db(tmp_path)

    await _recover_orphaned_amend_channels(_make_bot(db_path, guild_found=False))

    assert await _rows(db_path) == 0


async def test_a_channel_already_deleted_still_clears_the_row(tmp_path):
    db_path = await _make_db(tmp_path)

    await _recover_orphaned_amend_channels(_make_bot(db_path, channel=None))

    assert await _rows(db_path) == 0


@pytest.mark.parametrize(
    "error",
    [
        discord.NotFound(MagicMock(), "gone"),
        discord.HTTPException(MagicMock(), "boom"),
    ],
    ids=["not-found", "http-error"],
)
async def test_a_channel_that_refuses_to_delete_does_not_stop_the_sweep(tmp_path, error):
    """The sweep runs during startup, so an exception here would take the whole bot down
    over a leftover channel."""
    db_path = await _make_db(tmp_path, channels=2)
    bot = _make_bot(db_path)
    bot._channel.delete = AsyncMock(side_effect=error)

    await _recover_orphaned_amend_channels(bot)

    assert await _rows(db_path) == 0


async def test_a_failing_log_does_not_stop_the_sweep(tmp_path):
    """The log channel may be unreachable on a cold start before the guild is cached."""
    db_path = await _make_db(tmp_path, channels=2)
    bot = _make_bot(db_path)
    bot.output_router.post_log = AsyncMock(side_effect=RuntimeError("no log channel"))

    await _recover_orphaned_amend_channels(bot)

    assert await _rows(db_path) == 0


# ---------------------------------------------------------------------------
# Telling the league
# ---------------------------------------------------------------------------


async def test_the_league_is_told_the_amendment_never_finished(tmp_path):
    """An amendment channel vanishing means the command that made it was never finished,
    and the manager has to run it again — without this the work would simply be missing
    and nobody would know to redo it."""
    db_path = await _make_db(tmp_path)
    bot = _make_bot(db_path)

    await _recover_orphaned_amend_channels(bot)

    bot.output_router.post_log.assert_awaited()
    logged = str(bot.output_router.post_log.await_args.args[1])
    assert str(ROUND_ID) in logged or "amend" in logged.lower()


async def test_each_orphan_is_reported_separately(tmp_path):
    """Two abandoned amendments are two commands to re-run, and a single summary would
    leave a manager guessing which."""
    db_path = await _make_db(tmp_path, channels=2)
    bot = _make_bot(db_path)

    await _recover_orphaned_amend_channels(bot)

    assert bot.output_router.post_log.await_count == 2


# ---------------------------------------------------------------------------
# The daily portrait refresh
# ---------------------------------------------------------------------------


async def _portrait_bot(tmp_path, *, use_pfp: int, pfp_daily: int):
    db_path = os.path.join(str(tmp_path), "portraits.db")
    await run_migrations(db_path)
    async with get_connection(db_path) as db:
        await db.execute(
            "INSERT INTO image_config (id, use_pfp, pfp_daily, pfp_daily_time) "
            "VALUES (1, ?, ?, '04:30')",
            (use_pfp, pfp_daily),
        )
        await db.commit()
    bot = MagicMock()
    bot.db_path = db_path
    return bot


async def test_the_portrait_refresh_is_re_armed_at_its_own_time(tmp_path):
    """The job is recurring and in memory only, so a restart loses it until this re-adds it."""
    bot = await _portrait_bot(tmp_path, use_pfp=1, pfp_daily=1)

    await _recover_portrait_refresh_job(bot)

    bot.scheduler_service.schedule_portrait_refresh.assert_called_once_with("04:30")


@pytest.mark.parametrize("use_pfp,pfp_daily", [(0, 1), (1, 0)])
async def test_a_refresh_the_league_has_not_asked_for_is_not_armed(tmp_path, use_pfp, pfp_daily):
    bot = await _portrait_bot(tmp_path, use_pfp=use_pfp, pfp_daily=pfp_daily)

    await _recover_portrait_refresh_job(bot)

    bot.scheduler_service.schedule_portrait_refresh.assert_not_called()


async def test_a_refresh_that_cannot_be_armed_does_not_stop_the_start(tmp_path):
    bot = await _portrait_bot(tmp_path, use_pfp=1, pfp_daily=1)
    bot.scheduler_service.schedule_portrait_refresh.side_effect = ValueError("bad time")

    await _recover_portrait_refresh_job(bot)
