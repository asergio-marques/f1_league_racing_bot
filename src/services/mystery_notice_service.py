"""Mystery Notice service — posts a fixed informational notice at T−5 days.

Called by the APScheduler ``mystery_r{round_id}`` job for Mystery rounds.
No random draws are performed; no phase_results row is written; no log-channel
message is produced (FR-008).  The notice is posted to the division's forecast
channel only, with no role tag (FR-003).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from db.database import get_connection
from utils.message_builder import mystery_notice_message

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


async def run_mystery_notice(round_id: int, bot: "Bot") -> None:
    """Post the mystery round notice for *round_id* to its forecast channel."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.id, r.format, d.forecast_channel_id "
            "FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id "
            "WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()

    if row is None:
        log.warning("Mystery notice: round_id=%s not found — skipping.", round_id)
        return

    # Guard: if the round was amended away from MYSTERY before the job fired,
    # do nothing — the standard amendment-invalidation path already informed drivers.
    if row["format"] != "MYSTERY":
        log.info(
            "Mystery notice: round %s format is now %s (not MYSTERY) — skipping.",
            round_id, row["format"],
        )
        return

    class _Div:
        forecast_channel_id = row["forecast_channel_id"]

    await bot.output_router.post_forecast(_Div(), mystery_notice_message())
    log.info("Mystery notice posted for round %s.", round_id)
