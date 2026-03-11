"""Team models: DefaultTeam, TeamInstance, TeamSeat, TeamRoleConfig dataclasses."""
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


@dataclass
class TeamRoleConfig:
    """Server-scoped mapping of team name → Discord role ID."""
    id: int
    server_id: int
    team_name: str
    role_id: int
    updated_at: str
