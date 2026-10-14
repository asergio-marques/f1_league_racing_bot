"""What the bot puts right when it starts — the orphaned amendment channel sweep.

Issue #208. `__main__.py` was the worst-covered file in core at 26.7%. Most of it is `main()`, which
needs a real gateway connection and is out of scope for this suite (`CLAUDE.md`: no test may
require a live bot). The recovery helpers beneath it are not — they take a bot and a database
and put the world back the way it should be, and none was executed.

This file takes `_recover_orphaned_amend_channels` and `_recover_portrait_refresh_job`, which
re-arms the daily portrait refresh.

**An abandoned amendment's lapse is asked of the change queue** (#439, slice 6b, C64). The sweep
asks `results.amend.lapse` for each amendment left open, as the bot, before the queue starts, and
deletes, reverts and writes nothing itself; the lapse's own jobs do, each amendment's line its own,
so two abandoned amendments are two commands to re-run and the log says so twice. What the lapse
does at a restart, and the wording of its line, are pinned in `test_restart_recovery_sweeps.py`;
a refused deletion on a lapse stopping the queue in `tests/results/test_amend_changes.py`.

An amendment whose stage approval is in hand on the queue is left alone by the sweep, so the
queue, brought back up, finishes the approval.
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import pytest

from leaguebot.__main__ import _recover_orphaned_amend_channels, _recover_portrait_refresh_job
from leaguebot.core.db.database import get_connection, run_migrations

NO_AMEND_LAPSE_AT_RESTART = (
    "#439: the restart deletes each abandoned amendment's channel and writes its line itself, off "
    "the change queue, rather than asking the queue for `results.amend.lapse` as the bot"
)


# ---------------------------------------------------------------------------
# The sweep
# ---------------------------------------------------------------------------


@pytest.mark.xfail(strict=True, reason=NO_AMEND_LAPSE_AT_RESTART)
async def test_each_orphan_is_reported_separately(tmp_path):
    """Two abandoned amendments are two commands to re-run, and a single summary would leave a
    manager guessing which. Rounds 3 and 4 of Pro were each being amended when the bot stopped:
    the restart asks one lapse for each, as the bot, and once the queue has run them each writes
    its own line in the log channel."""
    from tests.support import review_league as league_of
    from tests.support.change_queue import change_rows, run_queue

    league = await league_of.review_league(tmp_path, round_status="FINAL")
    async with get_connection(league.db_path) as db:
        for round_id, channel_id in ((league_of.ROUND_ID, league_of.AMEND_CHANNEL),
                                     (league_of.LATER_ROUND_ID, league_of.AMEND_CHANNEL + 1)):
            await db.execute(
                "INSERT INTO round_amend_channels (round_id, channel_id, session_types, "
                "created_at) VALUES (?, ?, '[\"FEATURE_RACE\"]', ?)",
                (round_id, channel_id, league_of.NOW.isoformat()),
            )
        await db.commit()

    await _recover_orphaned_amend_channels(league.bot)
    lapses = [row for row in await change_rows(league.db_path)
              if row["kind"] == "results.amend.lapse"]
    await run_queue(league.bot)

    assert [row["origin"] for row in lapses] == ["BOT", "BOT"]
    lines = [line for line in league.bot.log_channel.sent if "/results rounds amend` of" in line]
    assert len(lines) == 2


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


async def test_an_amendment_whose_stage_is_queued_is_not_reverted_on_restart(tmp_path):
    """**A restart with an approval in hand finishes it** (#439). An amendment whose report stage
    was asked for and not yet carried out when the bot stopped is left by recovery: its row and
    channel stand, and the queue, brought back up, approves the stage."""
    from tests.support import review_league as league_of
    from tests.support.change_queue import member_interaction, restart_queue, run_queue

    league = await league_of.review_league(tmp_path, round_status="FINAL")
    async with get_connection(league.db_path) as db:
        await db.execute(
            "INSERT INTO round_amend_channels (round_id, channel_id, session_types, created_at, "
            "pre_amendment_state, expires_at, started_by) "
            "VALUES (?, ?, '[\"FEATURE_RACE\"]', ?, ?, ?, 77)",
            (league_of.ROUND_ID, league_of.AMEND_CHANNEL, league_of.NOW.isoformat(),
             json.dumps({"sessions": [], "profiles_before": []}),
             "2099-01-01T00:00:00+00:00"),
        )
        await db.commit()
    await league.bot.change_queue.ask(
        "results.amendment.reports.approve",
        {"round_id": league_of.ROUND_ID, "division_id": league_of.DIVISION_ID,
         "session_types": ["FEATURE_RACE"], "staged": [], "pardons": [],
         "prompt_message_id": None, "approval_message_id": None},
        interaction=member_interaction(league.bot), what="✅ Approve on the amendment's reports",
    )

    await restart_queue(league.bot)
    await _recover_orphaned_amend_channels(league.bot)

    assert ("delete_channel", league_of.AMEND_CHANNEL, league_of.AMEND_CHANNEL) not in league.events
    assert await league_of.one(
        league.db_path, "SELECT pre_amendment_state FROM round_amend_channels"
    ) is not None

    await run_queue(league.bot)

    assert await league_of.stopped_at(league) is None
    assert await league_of.one(
        league.db_path, "SELECT reports_approved_at FROM round_amend_channels"
    ) is not None
