"""Give a test's results a division team to stand under.

A result records the division's team it was driven for (issue #375), and the schema holds it
to a real `team_instances` row. Most results fixtures here predate that: they record a bare
number — 3001, 501, 999 — that stood for a Discord role and named no team at all. Rather than
renumber every fixture, this seeds a team **under that very number**, so each test keeps the
figure it was written with and gains the row the schema asks for.
"""
from __future__ import annotations

from unittest.mock import AsyncMock


async def seed_team_instances(db, division_id: int, *team_ids: int) -> None:
    """Create a non-reserve team of *division_id* for each of *team_ids*, keeping its id.

    Each is named after its number, which keeps two of them from colliding on the division's
    unique name. A team already present under that id is left alone, so a fixture may call
    this once per round without tracking what an earlier call seeded.
    """
    for team_id in team_ids:
        await db.execute(
            "INSERT OR IGNORE INTO team_instances (id, division_id, name, full_name, max_seats, is_reserve) "
            "VALUES (?, ?, ?, ?, 2, 0)",
            (team_id, division_id, f"Team {team_id}", f"Team {team_id}"),
        )


def resolves_as_typed() -> AsyncMock:
    """A stand-in for ``TeamService.resolve_division_team`` that finds whatever was typed.

    For a command test about something other than naming a team (#381): the team comes back
    under the typed text as its shorthand and its full name alike, so the command carries on
    exactly as it did when it took the name as typed. Naming a team is tested against the real
    resolver in ``test_team_reference.py``.
    """
    from services.team_service import TeamReference

    async def _resolve(division_id, text):
        return TeamReference(team={"id": 0, "name": text, "full_name": text, "role_id": None})

    return AsyncMock(side_effect=_resolve)
