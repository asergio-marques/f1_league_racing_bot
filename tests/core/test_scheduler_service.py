"""A job the scheduler runs answers no member's interaction, whoever scheduled it (#439).

A log line the log channel cannot take is told to the member whose command, button or form it
records, seen by them alone (`leaguebot.core.utils.answering`). A timed job records no member's
request, even where a command scheduled it: by the time it runs, the command is long over. These
tests use the real `SchedulerService`, on job-store files the test tears down, Windows included.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import discord
import pytest

from leaguebot.core.db.database import run_migrations
from tests.support.change_queue import (
    SERVER_ID,
    http_error,
    league_double,
    member_interaction,
    seed_server,
)


def _dispose(service) -> None:
    """Stop the scheduler and close the job store's connections, so its file can be deleted."""
    try:
        service._scheduler.shutdown(wait=False)
    except Exception:
        pass
    try:
        service._scheduler._jobstores["default"].engine.dispose()
    except Exception:
        pass


async def test_a_job_scheduled_from_an_admitted_interaction_finds_no_member_to_warn(
    tmp_path, monkeypatch, caplog
):
    """An admin's command schedules the signup close and is answered. When the job runs, the log
    channel refuses its line: the job finds no interaction it answers, the admin is sent nothing,
    and the host's log alone records the failure."""
    from leaguebot.core.services import scheduler_service
    from leaguebot.core.utils.answering import answering_now
    from leaguebot.core.utils.league_server import admits

    caplog.set_level(logging.INFO)
    db_path = str(tmp_path / "bot.db")
    await run_migrations(db_path)
    await seed_server(db_path)
    bot = league_double(db_path)
    bot.config_service.get_league_server_id = AsyncMock(return_value=SERVER_ID)
    bot.log_channel.fails = http_error(discord.Forbidden, status=403, text="Missing Access")
    interaction = member_interaction(bot, created_at=datetime.now(timezone.utc))

    monkeypatch.setattr(scheduler_service, "_GLOBAL_SERVICE", None)
    service = scheduler_service.SchedulerService(db_path, str(tmp_path / "jobs.db"))
    found: list[object] = []
    ran = asyncio.Event()

    async def _close_signups() -> None:
        found.append(answering_now())
        await bot.output_router.post_log("Admin (<@4242>) | signups closed | Success")
        ran.set()

    service.register_signup_close_callback(_close_signups)
    service.start()
    try:
        async def _command() -> None:
            assert await admits(bot, interaction)
            fire_at = datetime.now(timezone.utc) + timedelta(milliseconds=300)
            service.schedule_signup_close_timer(fire_at.replace(tzinfo=None).isoformat())
            await interaction.response.send_message("✅ Signups close shortly.", ephemeral=True)

        await asyncio.create_task(_command())
        await asyncio.wait_for(ran.wait(), 10)
        for _ in range(10):
            await asyncio.sleep(0.01)
    finally:
        _dispose(service)

    assert found == [None]
    assert interaction.followup.send.await_count == 0
    assert interaction.response.send_message.await_count == 1
    assert bot.interaction_channel.sent == []
    assert any(
        r.levelno >= logging.WARNING and r.name.endswith("output_router") for r in caplog.records
    )
