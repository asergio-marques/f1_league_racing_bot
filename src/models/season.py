"""Season model.

A season carries two states. ``status`` is the coarse grain every older reader was written
against: SETUP until its placements are first confirmed, ACTIVE from then until it ends,
and COMPLETED or CANCELLED after. ``stage`` is the lifecycle state issue #220 specifies,
refining SETUP and ACTIVE into the stages a league actually passes through.

The two are kept in step by the database (the `seasons_stage_*` triggers) and never disagree:
``STAGES_OF_STATUS`` is the mapping, and ``test_season_stage_matches_status`` pins it. A
reader that means "placements confirmed and being raced" may keep reading ``status``; one
that needs to know whether signups are open, or whether placements are being made
mid-season, reads ``stage``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum


class SeasonStatus(str, Enum):
    SETUP = "SETUP"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class SeasonStage(str, Enum):
    CONFIGURATION = "CONFIGURATION"
    WAITING = "WAITING"
    SIGNUPS = "SIGNUPS"
    PLACEMENTS = "PLACEMENTS"
    ONGOING = "ONGOING"
    ONGOING_SIGNUPS = "ONGOING_SIGNUPS"
    ONGOING_PLACEMENTS = "ONGOING_PLACEMENTS"
    PENDING_COMPLETION = "PENDING_COMPLETION"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


#: The stages each status admits. The `seasons_stage_matches_status` triggers enforce the same
#: mapping.
STAGES_OF_STATUS: dict[SeasonStatus, frozenset[SeasonStage]] = {
    SeasonStatus.SETUP: frozenset({
        SeasonStage.CONFIGURATION,
        SeasonStage.WAITING,
        SeasonStage.SIGNUPS,
        SeasonStage.PLACEMENTS,
    }),
    SeasonStatus.ACTIVE: frozenset({
        SeasonStage.ONGOING,
        SeasonStage.ONGOING_SIGNUPS,
        SeasonStage.ONGOING_PLACEMENTS,
        SeasonStage.PENDING_COMPLETION,
    }),
    SeasonStatus.COMPLETED: frozenset({SeasonStage.COMPLETED}),
    SeasonStatus.CANCELLED: frozenset({SeasonStage.CANCELLED}),
}

#: The three ongoing states: the season is raced, and sacking and cancelling are open.
ONGOING_STAGES: frozenset[SeasonStage] = frozenset({
    SeasonStage.ONGOING,
    SeasonStage.ONGOING_SIGNUPS,
    SeasonStage.ONGOING_PLACEMENTS,
})

#: The transitions the lifecycle permits. Aborting a season is not among them: an aborted
#: season is deleted, not moved to a state.
ALLOWED_STAGE_TRANSITIONS: dict[SeasonStage, frozenset[SeasonStage]] = {
    SeasonStage.CONFIGURATION: frozenset({SeasonStage.WAITING, SeasonStage.PLACEMENTS}),
    SeasonStage.WAITING: frozenset({SeasonStage.SIGNUPS}),
    SeasonStage.SIGNUPS: frozenset({SeasonStage.PLACEMENTS}),
    SeasonStage.PLACEMENTS: frozenset({SeasonStage.ONGOING}),
    SeasonStage.ONGOING: frozenset({
        SeasonStage.ONGOING_SIGNUPS,
        SeasonStage.PENDING_COMPLETION,
        SeasonStage.CANCELLED,
    }),
    SeasonStage.ONGOING_SIGNUPS: frozenset({
        SeasonStage.ONGOING_PLACEMENTS,
        SeasonStage.ONGOING,
        SeasonStage.CANCELLED,
    }),
    SeasonStage.ONGOING_PLACEMENTS: frozenset({SeasonStage.ONGOING, SeasonStage.CANCELLED}),
    SeasonStage.PENDING_COMPLETION: frozenset({SeasonStage.COMPLETED}),
    SeasonStage.COMPLETED: frozenset(),
    SeasonStage.CANCELLED: frozenset(),
}


def status_of_stage(stage: SeasonStage) -> SeasonStatus:
    """The coarse status a stage belongs to."""
    for status, stages in STAGES_OF_STATUS.items():
        if stage in stages:
            return status
    raise ValueError(f"stage {stage!r} belongs to no status")


class InvalidStageTransition(Exception):
    """A season was asked to move to a stage its lifecycle does not allow from where it is."""


@dataclass
class Season:
    id: int
    start_date: date
    status: SeasonStatus
    season_number: int = 0
    game_edition: int = 0
    stage: SeasonStage | None = None
