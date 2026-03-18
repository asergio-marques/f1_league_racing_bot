"""Amendment state models — mid-season points modification workflow."""
from __future__ import annotations

from dataclasses import dataclass

from src.models.points_config import SessionType


@dataclass
class SeasonAmendmentState:
    season_id: int
    amendment_active: bool
    modified_flag: bool


@dataclass
class SeasonModificationEntry:
    id: int
    season_id: int
    config_name: str
    session_type: SessionType
    position: int
    points: int


@dataclass
class SeasonModificationFl:
    id: int
    season_id: int
    config_name: str
    session_type: SessionType
    fl_points: int
    fl_position_limit: int | None
