"""Mystery Notice service — posts a fixed informational notice at the phase 1 horizon.

It stands in the place of the phase 1 message and shares its horizon, which the league
configures; see ``phase1_service`` for why no horizon is named as fixed.

Called when a Mystery round's ``weather_p1_s{S}_d{D}_r{N}_id{round_id}`` job fires —
``_weather_phase_job`` reads the round's format as it does — and by `/test-mode advance`.
No random draws are performed; no phase_results row is written; no log-channel
message is produced (FR-008).  The notice is posted to the division's forecast
channel only, with no role tag (FR-003).

What the notice records, the round's Phase 1 flag, is written on the connection a save hands
it (`record_mystery_notice_on`); the notice itself is posted after it (`forecast_changes`).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.weather.models.forecast import Drawn
from leaguebot.weather.services.draw_guard import round_if_due
from leaguebot.weather.utils.message_builder import mystery_notice_message

if TYPE_CHECKING:
    from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)


async def record_mystery_notice_on(db: aiosqlite.Connection, round_id: int) -> Drawn:
    """Mark the notice of *round_id* given, its Phase 1 done, on *db*, committing nothing.

    Nothing is drawn and no line is written (weather specification, FR-008); the flag is the
    whole record, written by the save that goes before the notice's post, so a notice Discord
    refuses no longer leaves the flag set with nothing posted behind it.

    Writes nothing, and says so by a `Drawn` with ``written`` false, while the weather module
    is off (the round's Phase 1 is left undone), for a round that is cancelled, or whose
    division is, where it reads the round after the cancellation is recorded, for the reason
    ``draw_guard`` gives, and for a round amended away from MYSTERY before the notice's turn
    came: the standard amendment-invalidation path already informed drivers.
    """
    row = await round_if_due(db, round_id, label="Mystery notice", done_column=None)
    if row is None:
        return Drawn(0)
    if row["format"] != "MYSTERY":
        log.info(
            "Mystery notice: round %s format is now %s (not MYSTERY) — skipping.",
            round_id, row["format"],
        )
        return Drawn(0)
    await db.execute("UPDATE rounds SET phase1_done = 1 WHERE id = ?", (round_id,))
    return Drawn(0, written=True)


async def run_mystery_notice(round_id: int, bot: "LeagueBot") -> None:
    """Record the mystery round notice for *round_id*, then post it to its forecast channel.

    Until the timers, the recovery and test mode ask the change queue for a forecast's jobs
    (`forecast_changes`), the one caller of the record that still posts as it did.
    """
    async with get_connection(bot.db_path) as db:
        drawn = await record_mystery_notice_on(db, round_id)
        await db.commit()
    if not drawn.written:
        return

    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            "SELECT r.division_id, d.forecast_channel_id "
            "FROM rounds r JOIN divisions d ON d.id = r.division_id WHERE r.id = ?",
            (round_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return

    # The graphic, where the league draws one. The notice is the posting made at the phase 1
    # horizon for such a round (Principle IV, as corrected at constitution v4.7.0), and it is
    # a posting the module makes rather than one it withholds — which is the whole of what
    # lets an image type draw it at all (XIV.8, "no posting, no graphic").
    #
    # It carries **no division role mention**, as its textual counterpart carries none: the
    # conditions are unknown to every participant alike (FR-052).
    from leaguebot.weather.services.forecast_cleanup_service import post_phase_message
    from leaguebot.image.services.image_weather_post import attach_forecast

    attachment = await attach_forecast(bot, round_id, 1)

    await post_phase_message(
        bot,
        round_id=round_id,
        division_id=row["division_id"],
        channel_id=row["forecast_channel_id"],
        phase_number=0,
        text=mystery_notice_message(),
        attachment=attachment,
        attachment_text="",
    )

    log.info("Mystery notice posted for round %s.", round_id)
