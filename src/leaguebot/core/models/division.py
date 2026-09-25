"""Division model."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Division:
    id: int
    season_id: int
    name: str
    mention_role_id: int
    forecast_channel_id: int | None
    status: str = "ACTIVE"
    tier: int = 0
    results_channel_id: int | None = None
    standings_channel_id: int | None = None
    penalty_channel_id: int | None = None
    lineup_channel_id: int | None = None
    calendar_channel_id: int | None = None
    lineup_message_id: int | None = None
    #: The message carrying this division's calendar. Written by every calendar posting,
    #: graphic or textual, and deleted by the image flow when it replaces it — an
    #: attachment cannot be added to a message already posted (Constitution XIV.8).
    calendar_message_id: int | None = None
