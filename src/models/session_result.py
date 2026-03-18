"""Session result models."""
from __future__ import annotations

import enum
from dataclasses import dataclass

from src.models.points_config import SessionType


class OutcomeModifier(str, enum.Enum):
    CLASSIFIED = "CLASSIFIED"
    DNF = "DNF"
    DNS = "DNS"
    DSQ = "DSQ"

    @property
    def is_points_eligible(self) -> bool:
        """CLASSIFIED drivers earn finishing-position points."""
        return self == OutcomeModifier.CLASSIFIED

    @property
    def is_fl_eligible(self) -> bool:
        """CLASSIFIED and DNF drivers may earn the fastest-lap bonus."""
        return self in (OutcomeModifier.CLASSIFIED, OutcomeModifier.DNF)


@dataclass
class SessionResult:
    id: int
    round_id: int
    division_id: int
    session_type: SessionType
    status: str
    config_name: str | None
    submitted_by: int | None
    submitted_at: str | None
    results_message_id: int | None = None


@dataclass
class DriverSessionResult:
    id: int
    session_result_id: int
    driver_user_id: int
    team_role_id: int
    finishing_position: int
    outcome: OutcomeModifier
    tyre: str | None
    best_lap: str | None
    gap: str | None
    total_time: str | None
    fastest_lap: str | None
    time_penalties: str | None
    post_steward_total_time: str | None
    post_race_time_penalties: str | None
    points_awarded: int
    fastest_lap_bonus: int
    is_superseded: bool
