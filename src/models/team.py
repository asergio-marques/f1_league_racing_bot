"""Team models: DefaultTeam, TeamInstance, TeamSeat dataclasses."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DefaultTeam:
    id: int
    server_id: int
    name: str
    max_seats: int  # -1 = unlimited
    is_reserve: bool


@dataclass
class TeamInstance:
    id: int
    division_id: int
    name: str
    max_seats: int  # -1 = unlimited
    is_reserve: bool


@dataclass
class TeamSeat:
    id: int
    team_instance_id: int
    seat_number: int
    driver_profile_id: int | None  # None = unassigned
