"""PhaseResult model."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

class PhaseStatus(str, Enum):
    ACTIVE = "ACTIVE"
    INVALIDATED = "INVALIDATED"

@dataclass
class PhaseResult:
    id: int
    round_id: int
    phase_number: int
    payload: dict
    status: PhaseStatus
    created_at: datetime
