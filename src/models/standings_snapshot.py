"""Standings snapshot models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


def _optional_column(row: object, name: str, index: int):
    """Read *name* from *row*, by key where it can be and by *index* otherwise.

    A ``sqlite3.Row`` addresses its columns both ways; a plain tuple only by position. Rows
    selected by a query that names its columns, or written before the column existed, carry
    it not at all — and None is the right answer for each.
    """
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        pass
    try:
        return row[index]
    except (IndexError, KeyError, TypeError):
        return None


@dataclass
class DriverStandingsSnapshot:
    id: int
    round_id: int
    division_id: int
    driver_user_id: int
    standing_position: int
    total_points: int
    finish_counts: dict[str, int]
    first_finish_rounds: dict[str, int]
    standings_message_id: int | None = None
    #: The message carrying the **constructor** standings, where the image flow posted two.
    #: The textual flow posts one message for both championships and leaves this null; the
    #: image flow needs the two nameable apart so either may be replaced, or fall back to
    #: text, without disturbing the other (Constitution XIV.4, XIV.7 as amended at v4.5.0).
    constructor_standings_message_id: int | None = None
    driver_profile_id: int | None = None
    # True when the driver has at least one session result in the division (even 0-point DNF).
    # Not persisted to DB; set during compute_driver_standings.
    race_participant: bool = False

    @classmethod
    def from_row(cls, row: object) -> DriverStandingsSnapshot:
        return cls(
            id=row[0],
            round_id=row[1],
            division_id=row[2],
            driver_user_id=row[3],
            standing_position=row[4],
            total_points=row[5],
            finish_counts=json.loads(row[6]),
            first_finish_rounds=json.loads(row[7]),
            standings_message_id=row[8] if len(row) > 8 else None,
            # By name where the row supports it, because this column was appended by
            # migration 041 and sits at index 10 — *after* driver_profile_id, which
            # migration 020 added and which this constructor does not read. Guessing the
            # ordinal is how the two would silently swap.
            constructor_standings_message_id=_optional_column(
                row, "constructor_standings_message_id", 10
            ),
        )


@dataclass
class TeamStandingsSnapshot:
    id: int
    round_id: int
    division_id: int
    team_role_id: int
    standing_position: int
    total_points: int
    finish_counts: dict[str, int]
    first_finish_rounds: dict[str, int]

    @classmethod
    def from_row(cls, row: object) -> TeamStandingsSnapshot:
        return cls(
            id=row[0],
            round_id=row[1],
            division_id=row[2],
            team_role_id=row[3],
            standing_position=row[4],
            total_points=row[5],
            finish_counts=json.loads(row[6]),
            first_finish_rounds=json.loads(row[7]),
        )
