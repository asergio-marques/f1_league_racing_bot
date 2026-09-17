"""Driver profile models: DriverState enum and associated dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DriverState(str, Enum):
    """The seven states a driver stands in, every one of which some command can reach.

    There are no ban states. ``SEASON_BANNED`` and ``LEAGUE_BANNED`` were defined and wired
    into the transition table, but no command was ever built to impose or lift a ban, so a
    league could never see either (issue #221). Sanctions belong to the stewarding module,
    which will bring whatever shape of ban it needs rather than inherit one designed against
    commands that were never written — so do not re-add them here ahead of it.
    ``tests/unit/test_driver_states.py`` pins this set.
    """

    NOT_SIGNED_UP                  = "NOT_SIGNED_UP"
    PENDING_SIGNUP_COMPLETION      = "PENDING_SIGNUP_COMPLETION"
    PENDING_ADMIN_APPROVAL         = "PENDING_ADMIN_APPROVAL"
    AWAITING_CORRECTION_PARAMETER  = "AWAITING_CORRECTION_PARAMETER"
    PENDING_DRIVER_CORRECTION      = "PENDING_DRIVER_CORRECTION"
    UNASSIGNED                     = "UNASSIGNED"
    ASSIGNED                       = "ASSIGNED"


@dataclass
class DriverProfile:
    """A driver, as the league holds them.

    Carries no ban counters. ``race_ban_count``, ``season_ban_count``, ``league_ban_count``
    and ``ban_races_remaining`` were only ever written as zero and went with the ban states
    in issue #221; a qualifying ban never had a counter at all. The bot issues the four
    sanctions in ``services.image_preview_data.VERDICT_SANCTIONS`` and no others.
    """

    id: int
    server_id: int
    discord_user_id: str
    current_state: DriverState
    former_driver: bool


@dataclass
class DriverSeasonAssignment:
    id: int
    driver_profile_id: int
    season_id: int
    division_id: int
    team_seat_id: int | None  # FK → team_seats(id); nullable for legacy rows
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
