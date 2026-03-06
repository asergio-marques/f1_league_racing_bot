"""season_end_service — automatic season completion and data cleanup.

Two entry points:

check_and_schedule_season_end(server_id, bot)
    Called after every Phase 3 completion.  Checks whether all non-Mystery
    rounds in the active season are done; if so, schedules execute_season_end
    to fire 7 days after the latest round's scheduled_at.

execute_season_end(server_id, season_id, bot)
    Posts a season-completion message to the log channel, then deletes all
    season data (preserving server_configs so the bot stays configured).
    Idempotent: a no-op if no active season is found (handles duplicate calls).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


async def check_and_schedule_season_end(
    server_id: int,
    bot: "Bot",
    *,
    now: datetime | None = None,
) -> None:
    """Schedule season end if all non-Mystery rounds are fully phased.

    If *now* is provided it is used instead of the real wall-clock time (useful
    for testing and for the startup recovery path).  When the computed fire time
    is already in the past (``now >= fire_at``), ``execute_season_end`` is
    called directly instead of scheduling a future job.

    Safe to call multiple times — ``replace_existing=True`` in the scheduler
    means a duplicate call simply refreshes the job's fire time.
    """
    season_svc = bot.season_service  # type: ignore[attr-defined]

    if not await season_svc.all_phases_complete(server_id):
        return  # Some phases are still pending

    season = await season_svc.get_active_season(server_id)
    if season is None:
        return  # Already cleaned up or never activated

    last_at = await season_svc.get_last_scheduled_at(server_id)
    if last_at is None:
        log.warning(
            "check_and_schedule_season_end: no rounds found for server %s", server_id
        )
        return

    if last_at.tzinfo is None:
        last_at = last_at.replace(tzinfo=timezone.utc)

    fire_at = last_at + timedelta(days=7)

    season_id_captured = season.id
    effective_now = now if now is not None else datetime.now(tz=timezone.utc)
    if effective_now.tzinfo is None:
        effective_now = effective_now.replace(tzinfo=timezone.utc)

    if effective_now >= fire_at:
        # Due date already passed (e.g. bot was down for >7 days); fire now.
        log.warning(
            "Season end for server %s (season %s) is overdue (fire_at=%s); "
            "executing immediately.",
            server_id,
            season_id_captured,
            fire_at.isoformat(),
        )
        await execute_season_end(server_id, season_id_captured, bot)
        return

    bot.scheduler_service.schedule_season_end(  # type: ignore[attr-defined]
        server_id, fire_at, season_id_captured
    )
    log.info(
        "Season end for server %s (season %s) scheduled at %s",
        server_id,
        season_id_captured,
        fire_at.isoformat(),
    )


async def execute_season_end(server_id: int, season_id: int, bot: "Bot") -> None:
    """Delete the season's data and announce completion in the log channel.

    Idempotent: returns immediately if no active season is found for the server.
    """
    season_svc = bot.season_service  # type: ignore[attr-defined]

    # Idempotency guard: verify the season still exists and is active
    season = await season_svc.get_active_season(server_id)
    if season is None:
        log.info(
            "execute_season_end: no active season for server %s — already cleaned up.",
            server_id,
        )
        return

    # Cancel any pending season-end scheduler job (no-op if already fired)
    bot.scheduler_service.cancel_season_end(server_id)  # type: ignore[attr-defined]

    # Announce completion *before* deleting so the log message makes sense
    completion_msg = (
        "\U0001f3c1 **Season Complete!**\n"
        f"The season (ID: {season_id}) has concluded and all round data has been "
        "cleared from the database.\n"
        "Run `/season-setup` to begin a new season."
    )
    await bot.output_router.post_log(server_id, completion_msg)  # type: ignore[attr-defined]

    # Delete season data (keep server_configs — bot stays configured)
    from services.reset_service import reset_server_data

    result = await reset_server_data(
        server_id=server_id,
        db_path=bot.db_path,  # type: ignore[attr-defined]
        scheduler_service=bot.scheduler_service,  # type: ignore[attr-defined]
        full=False,
    )

    # Increment the season counter so the next season gets a higher display number
    await season_svc.increment_previous_season_number(server_id)

    log.info(
        "Season %s for server %s ended: %d season(s), %d division(s), %d round(s) deleted.",
        season_id,
        server_id,
        result["seasons_deleted"],
        result["divisions_deleted"],
        result["rounds_deleted"],
    )
