"""Signup's own configuration, handed to the season's fingerprint.

Core's `fingerprint_areas_read` hook asks each module for the rows of its own configuration, on
the connection the fingerprint holds open, whatever the module's switch: a change to what the
module is set to is a change to what the review reported, off or on. Signup hands one area: its
channel and window, its settings and its availability slots. The module's switch is asked through
the module service, not handed here. Reads only.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

from leaguebot.core.db.database import tuple_rows


async def fingerprint_areas(db: aiosqlite.Connection, season_id: int) -> dict[str, Any]:
    """Signup's area of the fingerprint of *season_id*. The season does not narrow it: the
    configuration is the server's."""
    return {
        "signup": [
            await tuple_rows(
                db,
                "SELECT signup_channel_id, signups_open, selected_tracks_json, close_at "
                "FROM signup_module_config",
            ),
            await tuple_rows(
                db,
                "SELECT nationality_required, time_type, time_image_required "
                "FROM signup_module_settings",
            ),
            await tuple_rows(
                db,
                "SELECT id, day_of_week, time_hhmm FROM signup_availability_slots ORDER BY id",
            ),
        ],
    }
