"""Team models: DefaultTeam, TeamInstance, TeamRoleConfig dataclasses."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DefaultTeam:
    """A team of the server's list.

    A team has three names, each with one job: its **role**, which identifies it; its
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


@dataclass(frozen=True)
class SeatedDriver:
    """A driver holding a seat of a division, with the team's full name and whether it is the
    reserve team."""

    discord_user_id: int
    team_name: str
    is_reserve: bool


@dataclass
class TeamRoleConfig:
    """Server-scoped mapping of team name → Discord role ID."""
    id: int
    team_name: str
    role_id: int
    updated_at: str
