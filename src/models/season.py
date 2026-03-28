"""Season model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class SeasonStatus(str, Enum):
    SETUP = "SETUP"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


@dataclass
class Season:
    id: int
    server_id: int
    start_date: date
    status: SeasonStatus
    season_number: int = 0
    game_edition: int = 0
