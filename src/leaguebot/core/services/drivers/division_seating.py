"""A division's seating, as the lineup of record shows it.

Core owns the teams, the seats and the drivers who sit in them, so every module that draws or
lists who races in a division asks this reader and never joins those tables itself: core's text
lineup, image's lineup picture, the standings' seats and the preview. The portrait roster asks
`active_season_driver_ids`, below, which takes no filter.

What the lineup of record shows is the one filter every caller shares. A placement made mid-season
and not yet confirmed is left out (`include_uncommitted` draws it in, for the one caller that
must see the lineup as it will stand: the mid-season placements review); a seat whose placement is
left out is returned empty, never dropped, so a caller that draws empty seats still sees them.

The state of the driver who sits is the one filter on which callers differ. The text lineup lists
only a driver whose state is `ASSIGNED`, and the picture and the standings draw whoever holds the
seat: both keep their own filter, handed in as *states*, `None` meaning every state.
"""
from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from leaguebot.framework.db.database import get_connection


@dataclass(frozen=True)
class SeatOccupant:
    """The driver who holds a seat, with the fields of the profile that name and place them."""

    profile_id: int
    discord_user_id: str
    is_test_driver: bool
    test_display_name: str | None
    test_nationality: str | None
    current_state: str


@dataclass(frozen=True)
class SeatingSeat:
    """One seat of one team: its number and who holds it, or None where it is empty."""

    seat_number: int
    occupant: SeatOccupant | None


@dataclass(frozen=True)
class SeatingTeam:
    """One team of the division with its seats in ascending seat number."""

    team_instance_id: int
    name: str
    full_name: str
    max_seats: int
    is_reserve: bool
    seats: tuple[SeatingSeat, ...]


async def division_seating(
    db_path: str,
    division_id: int,
    *,
    include_uncommitted: bool = False,
    states: Collection[str] | None = None,
) -> list[SeatingTeam]:
    """Every team of *division_id*, regular teams first and reserves after, each with every one
    of its seats and who holds it.

    A seat is returned empty where its placement is not yet confirmed mid-season, unless
    *include_uncommitted*, and where its driver's state is not among *states* (every state where
    *states* is None). The teams come in the order they were made.
    """
    async with get_connection(db_path) as db:
        instances = await (
            await db.execute(
                "SELECT id, name, full_name, max_seats, is_reserve FROM team_instances "
                "WHERE division_id = ? ORDER BY is_reserve ASC, id ASC",
                (division_id,),
            )
        ).fetchall()
        teams = []
        for instance in instances:
            rows = await (
                await db.execute(
                    "SELECT ts.seat_number, dp.id AS profile_id, dp.discord_user_id, "
                    "       dp.is_test_driver, dp.test_display_name, dp.test_nationality, "
                    "       dp.current_state "
                    "FROM team_seats ts "
                    # A placement not yet confirmed mid-season is not shown, unless asked for.
                    "LEFT JOIN driver_season_assignments dsa "
                    "       ON dsa.team_seat_id = ts.id AND dsa.division_id = ? "
                    "      AND (dsa.committed = 1 OR ? OR NOT EXISTS ("
                    "          SELECT 1 FROM seasons s WHERE s.id = dsa.season_id "
                    "          AND s.status = 'ACTIVE')) "
                    "LEFT JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id "
                    "WHERE ts.team_instance_id = ? ORDER BY ts.seat_number",
                    (division_id, int(include_uncommitted), instance["id"]),
                )
            ).fetchall()
            teams.append(
                SeatingTeam(
                    team_instance_id=int(instance["id"]),
                    name=instance["name"],
                    full_name=instance["full_name"],
                    max_seats=int(instance["max_seats"] or 0),
                    is_reserve=bool(instance["is_reserve"]),
                    seats=tuple(
                        SeatingSeat(int(row["seat_number"]), _occupant(row, states))
                        for row in rows
                    ),
                )
            )
    return teams


def _occupant(row, states: Collection[str] | None) -> SeatOccupant | None:
    """The occupant a seat's row carries, or None where it carries none or its state is not
    among *states*."""
    if row["profile_id"] is None:
        return None
    if states is not None and row["current_state"] not in states:
        return None
    return SeatOccupant(
        profile_id=int(row["profile_id"]),
        discord_user_id=str(row["discord_user_id"]),
        is_test_driver=bool(row["is_test_driver"]),
        test_display_name=row["test_display_name"],
        test_nationality=row["test_nationality"],
        current_state=row["current_state"],
    )


async def active_season_driver_ids(db_path: str) -> list[str]:
    """The Discord user ids of every driver assigned in the active season, sorted, test drivers
    left out: their ids are synthetic and resolve to no Discord account.

    A placement not yet confirmed counts: the roster is who may race, and a portrait fetched for
    them is ready the day they are confirmed.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT dp.discord_user_id AS uid
            FROM driver_season_assignments dsa
            JOIN driver_profiles dp ON dp.id = dsa.driver_profile_id
            JOIN seasons s ON s.id = dsa.season_id
            WHERE s.status = 'ACTIVE'
              AND dp.is_test_driver = 0
            """,
        )
        rows = await cursor.fetchall()
    return sorted(str(row["uid"]) for row in rows)
