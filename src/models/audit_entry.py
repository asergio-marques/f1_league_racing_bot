"""AuditEntry model."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime

@dataclass
class AuditEntry:
    id: int
    server_id: int
    actor_id: int
    actor_name: str
    division_id: int | None
    change_type: str
    old_value: str
    new_value: str
    timestamp: datetime
