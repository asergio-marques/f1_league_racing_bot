"""Session result models."""
from __future__ import annotations

import enum
from dataclasses import dataclass

from models.points_config import SessionType


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
    fl_driver_override: int | None = None


@dataclass
class DriverSessionResult:
    """Legacy model — used by old driver_session_results rows and by
    compute_points_for_session (which is session-type-agnostic).
    New code should prefer QualifyingSessionResult / RaceSessionResult."""
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
    driver_profile_id: int | None = None


# ---------------------------------------------------------------------------
# New result models
# ---------------------------------------------------------------------------

@dataclass
class QualifyingSessionResult:
    """One driver's qualifying result from qualifying_session_results."""
    id: int
    session_result_id: int
    driver_user_id: int
    team_role_id: int
    finishing_position: int
    outcome: OutcomeModifier
    tyre: str | None
    # Absolute best-lap time string for all classified drivers, e.g. "1:23.456".
    # DNS/DNF/DSQ drivers carry their outcome literal here.
    best_lap: str | None
    points_awarded: int
    driver_profile_id: int | None = None


@dataclass
class RaceSessionResult:
    """One driver's race result from race_session_results."""
    id: int
    session_result_id: int
    driver_user_id: int
    team_role_id: int
    finishing_position: int
    outcome: OutcomeModifier
    # base_time_ms: race time in ms with ingame penalties already subtracted.
    # NULL for lapped drivers, DNF, DNS, DSQ.
    base_time_ms: int | None
    # laps_behind: N for drivers classified "+N Laps" behind leader; None otherwise.
    laps_behind: int | None
    ingame_time_penalties_ms: int   # game-applied penalty; 0 when N/A
    postrace_time_penalties_ms: int  # steward wizard; 0 by default
    appeal_time_penalties_ms: int    # appeals phase; 0 by default
    fastest_lap: str | None
    fastest_lap_bonus: int
    points_awarded: int
    driver_profile_id: int | None = None

    @property
    def total_time_ms(self) -> int | None:
        """Computed total race time in ms, or None when not applicable."""
        if self.base_time_ms is None:
            return None
        return (
            self.base_time_ms
            + self.ingame_time_penalties_ms
            + self.postrace_time_penalties_ms
            + self.appeal_time_penalties_ms
        )

