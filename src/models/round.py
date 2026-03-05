"""Round model."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class RoundFormat(str, Enum):
    NORMAL = "NORMAL"
    SPRINT = "SPRINT"
    MYSTERY = "MYSTERY"
    ENDURANCE = "ENDURANCE"

@dataclass
class Round:
    id: int
    division_id: int
    round_number: int
    format: RoundFormat
    track_name: str | None
    scheduled_at: datetime
    phase1_done: bool = False
    phase2_done: bool = False
    phase3_done: bool = False
    status: str = "ACTIVE"
