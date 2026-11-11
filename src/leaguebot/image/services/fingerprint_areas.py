"""Image's own configuration, handed to the season's fingerprint.

Core's `fingerprint_areas_read` hook asks each module for the rows of its own configuration, on
the connection the fingerprint holds open, whatever the module's switch: a change to what the
module is set to is a change to what the review reported, off or on. Image hands one area: its
configuration, the module's switch among it, and its aspect toggles. The artwork on disk is
asked of the image drawing, not read here. Reads only.
"""

from __future__ import annotations

from typing import Any

import aiosqlite

from leaguebot.core.db.database import tuple_rows


async def fingerprint_areas(db: aiosqlite.Connection, season_id: int) -> dict[str, Any]:
    """Image's area of the fingerprint of *season_id*. The season does not narrow it: the
    configuration is the server's."""
    return {
        "images": [
            await tuple_rows(db, "SELECT * FROM image_config"),
            await tuple_rows(
                db, "SELECT aspect, enabled FROM image_aspect_toggles ORDER BY aspect"
            ),
        ],
    }
