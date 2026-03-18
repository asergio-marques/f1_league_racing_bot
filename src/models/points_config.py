"""Points configuration models."""
from __future__ import annotations

import enum
from dataclasses import dataclass


class SessionType(str, enum.Enum):
    SPRINT_QUALIFYING = "SPRINT_QUALIFYING"
    SPRINT_RACE = "SPRINT_RACE"
    FEATURE_QUALIFYING = "FEATURE_QUALIFYING"
    FEATURE_RACE = "FEATURE_RACE"

    @property
    def is_race(self) -> bool:
        return self in (SessionType.SPRINT_RACE, SessionType.FEATURE_RACE)

    @property
    def is_qualifying(self) -> bool:
        return self in (SessionType.SPRINT_QUALIFYING, SessionType.FEATURE_QUALIFYING)

    def label(self) -> str:
        return self.value.replace("_", " ").title()


@dataclass
class PointsConfigStore:
    id: int
    server_id: int
    config_name: str


@dataclass
class PointsConfigEntry:
    id: int
    config_id: int
    session_type: SessionType
    position: int
    points: int


@dataclass
class PointsConfigFastestLap:
    id: int
    config_id: int
    session_type: SessionType
    fl_points: int
    fl_position_limit: int | None
