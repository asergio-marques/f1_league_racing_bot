"""Driver profile models: DriverState enum and associated dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DriverState(str, Enum):
    """The seven states a driver stands in, every one of which some command can reach.

    There are no ban states. ``SEASON_BANNED`` and ``LEAGUE_BANNED`` were defined and wired
    into the transition table, but no command was ever built to impose or lift a ban, so a
    league could never see either. Sanctions belong to the stewarding module, which will bring
    whatever shape of ban it needs rather than inherit one designed against commands that were never
    written — so do not re-add them here ahead of it. ``tests/core/test_driver_states.py`` pins this
    set.
    """

    NOT_SIGNED_UP                  = "NOT_SIGNED_UP"
    PENDING_SIGNUP_COMPLETION      = "PENDING_SIGNUP_COMPLETION"
    PENDING_ADMIN_APPROVAL         = "PENDING_ADMIN_APPROVAL"
    AWAITING_CORRECTION_PARAMETER  = "AWAITING_CORRECTION_PARAMETER"
    PENDING_DRIVER_CORRECTION      = "PENDING_DRIVER_CORRECTION"
    UNASSIGNED                     = "UNASSIGNED"
    ASSIGNED                       = "ASSIGNED"


class InvalidDriverTransition(ValueError):
    """The state machine's refusal of a transition its table does not allow.

    A fault, not a `Refused`: a caller judges a move before it writes one, so a transition that
    reaches the table and is refused there is one that raced past the judgement. A caller that
    expects the race catches this by name.
    """


@dataclass
class DriverProfile:
    """A driver, as the league holds them.

    Carries no ban counters, as there are no ban states to count towards. The bot issues the
    four sanctions in ``leaguebot.image.services.image_preview_data.VERDICT_SANCTIONS`` and no
    others; the fifth entry there, no further action, is no sanction at all.
    """

    id: int
    discord_user_id: str
    current_state: DriverState
    former_driver: bool


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
