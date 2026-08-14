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
from services.forecast_cleanup_service import store_forecast_message

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


async def run_mystery_notice(round_id: int, bot: "Bot") -> None:
    """Post the mystery round notice for *round_id* to its forecast channel."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.id, r.format, d.id AS division_id, d.forecast_channel_id, "
            "       s.server_id "
            "FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id "
            "JOIN seasons s ON s.id = d.season_id "
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

    # The graphic, where the league draws one. The notice is the posting made at the phase 1
    # horizon for such a round (Principle IV, as corrected at constitution v4.7.0), and it is
    # a posting the module makes rather than one it withholds — which is the whole of what
    # lets an image type draw it at all (XIV.8, "no posting, no graphic").
    #
    # It carries **no division role mention**, as its textual counterpart carries none: the
    # conditions are unknown to every participant alike (FR-052).
    from services.forecast_cleanup_service import post_phase_message
    from services.image_weather_post import attach_forecast

    attachment = await attach_forecast(bot, round_id, 1, row["server_id"])

    await post_phase_message(
        bot,
        round_id=round_id,
        division_id=row["division_id"],
        server_id=row["server_id"],
        channel_id=row["forecast_channel_id"],
        phase_number=0,
        text=mystery_notice_message(),
        attachment=attachment,
        attachment_text="",
    )

    async with get_connection(bot.db_path) as db:
        await db.execute("UPDATE rounds SET phase1_done = 1 WHERE id = ?", (round_id,))
        await db.commit()

    log.info("Mystery notice posted for round %s.", round_id)
