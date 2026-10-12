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

import aiosqlite

from leaguebot.weather.models.forecast import Drawn
from leaguebot.weather.services.draw_guard import round_if_due

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
