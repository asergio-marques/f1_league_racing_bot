"""Division model."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Division:
    id: int
    season_id: int
    name: str
    mention_role_id: int
    forecast_channel_id: int
    race_day: int   # 0=Monday ... 6=Sunday
    race_time: str  # HH:MM UTC
