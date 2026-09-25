"""Session model with format->session-type mappings."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from models.round import RoundFormat

class SessionType(str, Enum):
    SHORT_QUALIFYING = "SHORT_QUALIFYING"
    SHORT_SPRINT_QUALIFYING = "SHORT_SPRINT_QUALIFYING"
    SHORT_FEATURE_QUALIFYING = "SHORT_FEATURE_QUALIFYING"
    LONG_RACE = "LONG_RACE"
    LONG_FEATURE_RACE = "LONG_FEATURE_RACE"
    LONG_SPRINT_RACE = "LONG_SPRINT_RACE"
    FULL_QUALIFYING = "FULL_QUALIFYING"
    FULL_RACE = "FULL_RACE"

# Maximum number of in-game weather slots per session type (from spec)
MAX_SLOTS: dict[SessionType, int] = {
    SessionType.SHORT_QUALIFYING: 2,
    SessionType.SHORT_SPRINT_QUALIFYING: 2,
    SessionType.SHORT_FEATURE_QUALIFYING: 2,
    SessionType.LONG_RACE: 3,
    SessionType.LONG_FEATURE_RACE: 3,
    SessionType.LONG_SPRINT_RACE: 1,
    SessionType.FULL_QUALIFYING: 3,
    SessionType.FULL_RACE: 4,
}

# Sessions per round format (in order)
SESSIONS_BY_FORMAT: dict[RoundFormat, list[SessionType]] = {
    RoundFormat.NORMAL: [
        SessionType.SHORT_QUALIFYING,
        SessionType.LONG_RACE,
    ],
    RoundFormat.SPRINT: [
        SessionType.SHORT_SPRINT_QUALIFYING,
        SessionType.LONG_SPRINT_RACE,
        SessionType.SHORT_FEATURE_QUALIFYING,
        SessionType.LONG_FEATURE_RACE,
    ],
    RoundFormat.MYSTERY: [],  # No phases for Mystery
    RoundFormat.ENDURANCE: [
        SessionType.FULL_QUALIFYING,
        SessionType.FULL_RACE,
    ],
}

@dataclass
class Session:
    id: int
    round_id: int
    session_type: SessionType
    phase2_slot_type: str | None = None   # "rain" | "mixed" | "sunny"
    phase3_slots: list[str] | None = None  # list of weather labels
