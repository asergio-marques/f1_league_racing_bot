"""Driver profile models: DriverState enum and associated dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DriverState(str, Enum):
    NOT_SIGNED_UP             = "NOT_SIGNED_UP"
    PENDING_SIGNUP_COMPLETION = "PENDING_SIGNUP_COMPLETION"
    PENDING_ADMIN_APPROVAL    = "PENDING_ADMIN_APPROVAL"
    PENDING_DRIVER_CORRECTION = "PENDING_DRIVER_CORRECTION"
    UNASSIGNED                = "UNASSIGNED"
    ASSIGNED                  = "ASSIGNED"
    SEASON_BANNED             = "SEASON_BANNED"
    LEAGUE_BANNED             = "LEAGUE_BANNED"


@dataclass
class DriverProfile:
    id: int
    server_id: int
    discord_user_id: str
    current_state: DriverState
    former_driver: bool
    race_ban_count: int
    season_ban_count: int
    league_ban_count: int


@dataclass
class DriverSeasonAssignment:
    id: int
    driver_profile_id: int
    season_id: int
    division_id: int
    current_position: int
    current_points: int
    points_gap_to_first: int


@dataclass
class DriverHistoryEntry:
    id: int
    driver_profile_id: int
    season_number: int
    division_name: str
    division_tier: int
    final_position: int
    final_points: int
    points_gap_to_winner: int
