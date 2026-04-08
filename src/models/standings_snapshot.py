"""Standings snapshot models."""
from __future__ import annotations

import json
from dataclasses import dataclass, field


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
