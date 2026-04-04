"""Track service — queries against the ``tracks`` database table.

Provides application-layer access to the 28 F1 circuits seeded by migration 029.
The retired ``track_rpc_params`` CRUD functions have been removed along with that table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite


async def get_all_tracks(db: "aiosqlite.Connection") -> list:
    """Return all track rows ordered by numeric id.

    Each row is an :class:`aiosqlite.Row` with keys:
    ``id``, ``name``, ``gp_name``, ``location``, ``country``, ``mu``, ``sigma``.
    """
    cursor = await db.execute(
        "SELECT id, name, gp_name, location, country, mu, sigma FROM tracks ORDER BY id"
    )
    return await cursor.fetchall()


async def get_track_by_name(db: "aiosqlite.Connection", name: str):
    """Return the track row whose ``name`` matches exactly, or ``None`` if absent.

    Args:
        db: Open aiosqlite connection.
        name: Canonical circuit name (e.g. ``"Silverstone Circuit"``).
    """
    cursor = await db.execute(
        "SELECT id, name, gp_name, location, country, mu, sigma FROM tracks WHERE name = ?",
        (name,),
    )
    return await cursor.fetchone()


async def get_track_name_map(db: "aiosqlite.Connection") -> dict[str, str]:
    """Return a ``{str(id): name}`` mapping for all track rows.

    Keys are plain integer strings (e.g. ``"1"``, ``"12"``).
    Used by cogs and services that store track IDs as strings.
    """
    cursor = await db.execute("SELECT id, name FROM tracks ORDER BY id")
    rows = await cursor.fetchall()
    return {str(r["id"]): r["name"] for r in rows}

