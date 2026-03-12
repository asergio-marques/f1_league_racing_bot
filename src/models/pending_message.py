"""PendingMessage model."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class PendingMessage:
    id: int
    server_id: int
    channel_id: int
    content: str
    failure_reason: str
    enqueued_at: datetime
    retry_count: int
    last_attempted_at: datetime | None
