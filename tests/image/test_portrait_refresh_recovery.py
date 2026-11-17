"""Image's daily portrait refresh, re-armed as the bot starts again.

The refresh is recurring and its job store survives a restart, so image's start-up step
(`recover_portrait_refresh_job`, in image's wiring) re-adds it at the time the league has stored,
and arms nothing the league has not asked for. A refusal is reported by the start-up sweep. Moved
from `tests/core/test_bot_recovery.py` (#544), as the step is image's.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from leaguebot.framework.db.database import get_connection, run_migrations
from tests.support.settings_league import portrait_refresh_times



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
    """The job is recurring and its store survives a restart; this re-adds it at start-up at the
    time the league has stored, replacing whatever the store kept."""
    from leaguebot.image.wiring import recover_portrait_refresh_job

    bot = await _portrait_bot(tmp_path, use_pfp=1, pfp_daily=1)

    await recover_portrait_refresh_job(bot)

    assert portrait_refresh_times(bot.scheduler_service) == ["04:30"]


@pytest.mark.parametrize("use_pfp,pfp_daily", [(0, 1), (1, 0)])
async def test_a_refresh_the_league_has_not_asked_for_is_not_armed(tmp_path, use_pfp, pfp_daily):
    from leaguebot.image.wiring import recover_portrait_refresh_job

    bot = await _portrait_bot(tmp_path, use_pfp=use_pfp, pfp_daily=pfp_daily)

    await recover_portrait_refresh_job(bot)

    assert portrait_refresh_times(bot.scheduler_service) == []


async def test_a_refresh_that_cannot_be_armed_is_reported_by_the_start_up_sweep(tmp_path):
    """The league asks for portraits refreshed daily at 04:30, and the scheduler refuses the job
    as the bot starts again. Image's start-up steps run in its turn of the real start-up sweep,
    images on: the start goes on, and the sweep reports the step that failed in the log channel,
    in one line (core specification, "When the bot stops": a step of it that fails shall be
    reported in the log channel), rather than the step swallowing the refusal."""
    from tests.support.start_up_sweep import run_image_start_up

    bot = await _portrait_bot(tmp_path, use_pfp=1, pfp_daily=1)
    bot.scheduler_service.arm_daily.side_effect = ValueError("bad time")

    lines = await run_image_start_up(bot, on=True)

    assert portrait_refresh_times(bot.scheduler_service) == ["04:30"]
    assert len(lines) == 1, lines
