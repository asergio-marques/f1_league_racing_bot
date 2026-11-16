"""A round's and a division's context: where it sits and what it is called.

Core owns the seasons, divisions, rounds and tracks, so a module that heads a picture with a round's
season number, its division's tier or the grand prix it runs asks these readers and never joins
those tables itself: image's weather, verdict, lineup, standings and preview pictures.

Each reader answers None where what it was asked for is not there, and the caller decides what a
missing round or division means to it.
"""
from __future__ import annotations

from dataclasses import dataclass

from leaguebot.framework.db.database import get_connection


@dataclass(frozen=True)
class DivisionContext:
    """A division and the season it belongs to."""

    division_id: int
    name: str
    tier: int | None
    season_id: int
    season_number: int | None


@dataclass(frozen=True)
class RoundContext:
    """A round with its division, its season and the circuit it runs on.

    *gp_name* and *country* are None where the round's track is not in the track registry (a round
    records the track's name, not an id), or where it names none.
    """

    round_id: int
    round_number: int
    format: str
    track_name: str | None
    division: DivisionContext
    gp_name: str | None
    country: str | None


@dataclass(frozen=True)
class RoundOfDivision:
    """One round of a division's calendar, as the calendar orders it."""

    round_id: int
    round_number: int
    format: str
    track_name: str | None


async def division_context(db_path: str, division_id: int) -> DivisionContext | None:
    """The division *division_id* with its season, or None where it is gone."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT d.id, d.name, d.tier, s.id AS season_id, s.season_number "
                "FROM divisions d JOIN seasons s ON s.id = d.season_id WHERE d.id = ?",
                (division_id,),
            )
        ).fetchone()
    return _division_of(row) if row is not None else None


async def round_context(db_path: str, round_id: int) -> RoundContext | None:
    """The round *round_id* with its division, its season and its circuit, or None where it is
    gone."""
    async with get_connection(db_path) as db:
        row = await (
            await db.execute(
                "SELECT r.id AS round_id, r.round_number, r.format, r.track_name, "
                "       d.id, d.name, d.tier, s.id AS season_id, s.season_number, "
                "       t.gp_name, t.country "
                "FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id "
                "LEFT JOIN tracks t ON t.name = r.track_name "
                "WHERE r.id = ?",
                (round_id,),
            )
        ).fetchone()
    if row is None:
        return None
    return RoundContext(
        round_id=int(row["round_id"]),
        round_number=int(row["round_number"]),
        format=row["format"],
        track_name=row["track_name"],
        division=_division_of(row),
        gp_name=row["gp_name"],
        country=row["country"],
    )


async def division_rounds(db_path: str, division_id: int) -> list[RoundOfDivision]:
    """Every round the division holds, run or not, in round order."""
    async with get_connection(db_path) as db:
        rows = await (
            await db.execute(
                "SELECT id, round_number, format, track_name FROM rounds "
                "WHERE division_id = ? ORDER BY round_number",
                (division_id,),
            )
        ).fetchall()
    return [
        RoundOfDivision(
            int(row["id"]), int(row["round_number"]), row["format"], row["track_name"]
        )
        for row in rows
    ]


def _division_of(row) -> DivisionContext:
    return DivisionContext(
        division_id=int(row["id"]),
        name=row["name"],
        tier=row["tier"],
        season_id=int(row["season_id"]),
        season_number=row["season_number"],
    )
