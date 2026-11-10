"""Division model."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class DivisionStatus(str, Enum):
    """Where a division stands in its season: set up, running, run out or called off.

    The values are the ``divisions.status`` CHECK list in the baseline schema, which
    ``test_schema_rules`` holds to this enum so the two cannot drift.
    """

    SETUP = "SETUP"
    ACTIVE = "ACTIVE"
    FINISHED = "FINISHED"
    CANCELLED = "CANCELLED"


@dataclass
class Division:
    id: int
    season_id: int
    name: str
    mention_role_id: int
    status: str = "ACTIVE"
    tier: int = 0
    lineup_channel_id: int | None = None
    calendar_channel_id: int | None = None
    lineup_message_id: int | None = None
    #: The message carrying this division's calendar. Written by every calendar posting,
    #: graphic or textual, and deleted by the image flow when it replaces it — an
    #: attachment cannot be added to a message already posted (Constitution XIV.8).
    calendar_message_id: int | None = None
