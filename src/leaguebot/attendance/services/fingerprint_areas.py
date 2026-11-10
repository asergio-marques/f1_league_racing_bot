"""Attendance's own configuration, handed to the season's fingerprint.

Core's `fingerprint_areas_read` hook asks each module for the rows of its own configuration, on
the connection the fingerprint holds open, whatever the module's switch: a change to what the
module is set to is a change to what the review reported, off or on. Attendance hands two areas:
its RSVP and attendance channels for each division of the season, and its configuration, the
module's switch among it. Reads only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite

from leaguebot.core.db.database import tuple_rows, tuple_rows_in

if TYPE_CHECKING:
    from leaguebot.core.services.season_service import SeasonService


async def fingerprint_areas(
    seasons: "SeasonService", db: aiosqlite.Connection, season_id: int
) -> dict[str, Any]:
    """Attendance's areas of the fingerprint of *season_id*: its two channels by division, and its
    configuration."""
    return {
        "channels": await tuple_rows_in(
            db,
            "SELECT division_id, rsvp_channel_id, attendance_channel_id "
            "FROM attendance_division_config WHERE division_id IN ({marks}) ORDER BY division_id",
            await seasons.division_ids_on(db, season_id),
        ),
        "attendance": await tuple_rows(
            db,
            "SELECT module_enabled, rsvp_notice_days, rsvp_last_notice_hours, "
            "       rsvp_deadline_hours, no_rsvp_penalty, absent_penalty, "
            "       no_show_penalty, autoreserve_threshold, autosack_threshold "
            "FROM attendance_config",
        ),
    }
