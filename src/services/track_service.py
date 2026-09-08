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


async def resolve_track_name(db: "aiosqlite.Connection", raw: str) -> str | None:
    """Return the canonical track name *raw* refers to, or ``None`` if it names none.

    Accepts every form a league manager can actually arrive at:

    - a bare id, ``14`` or ``04``;
    - a canonical name, in any case — ``Hungaroring``, ``hungaroring``;
    - the label the autocomplete *displays*, ``14 – Hungaroring``.

    The last of those is the reason this exists. The autocomplete offers the label as
    its display name and the bare track name as its value, so **choosing** a suggestion
    sends a clean name. A manager who types or pastes what they see on screen instead —
    or edits a previous command in place — sends the label, which matched neither the id
    branch nor the exact-name branch and was rejected as an unknown track. Both the en
    dash the autocomplete uses and the hyphen a keyboard produces are accepted, since
    the two are indistinguishable to the person reading the list.
    """
    candidate = raw.strip()
    if not candidate:
        return None

    # "14 – Hungaroring" → the id, which is authoritative; the name after the dash is
    # only what was displayed alongside it.
    for dash in ("–", "—", "-"):
        head, sep, tail = candidate.partition(dash)
        if sep and head.strip().isdigit():
            candidate = head.strip()
            break
        # A hyphen inside a circuit name ("Spa-Francorchamps") is not a separator, so
        # only a numeric head counts as one and anything else falls through untouched.

    if candidate.isdigit():
        cursor = await db.execute(
            "SELECT name FROM tracks WHERE id = ?", (int(candidate),)
        )
    else:
        cursor = await db.execute(
            "SELECT name FROM tracks WHERE LOWER(name) = LOWER(?)", (candidate,)
        )
    row = await cursor.fetchone()
    return row["name"] if row else None


async def get_track_name_map(db: "aiosqlite.Connection") -> dict[str, str]:
    """Return a ``{str(id): name}`` mapping for all track rows.

    Keys are plain integer strings (e.g. ``"1"``, ``"12"``).
    Used by cogs and services that store track IDs as strings.
    """
    cursor = await db.execute("SELECT id, name FROM tracks ORDER BY id")
    rows = await cursor.fetchall()
    return {str(r["id"]): r["name"] for r in rows}

