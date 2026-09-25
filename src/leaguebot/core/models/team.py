"""Team models: DefaultTeam, TeamInstance, TeamSeat, TeamRoleConfig dataclasses."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DefaultTeam:
    """A team of the server's list.

    A team has three names, each with one job (#381): its **role**, which identifies it; its
    **shorthand** (``name``), a short alias a league types wherever a team is entered, and the
    filename its artwork is found by; and its **full name**, which is what is shown.
    """

    id: int
    name: str
    full_name: str
    max_seats: int  # -1 = unlimited
    is_reserve: bool


@dataclass
class TeamInstance:
    """A division's copy of a team of the server's list, its names as ``DefaultTeam``'s."""

    id: int
    division_id: int
    name: str
    full_name: str
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
    team_name: str
    role_id: int
    updated_at: str
