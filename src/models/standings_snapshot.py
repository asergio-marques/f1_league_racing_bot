"""Standings snapshot models."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DriverStandingsSnapshot:
    id: int
    round_id: int
    division_id: int
    driver_user_id: int
    standing_position: int
    total_points: int
    finish_counts: dict[str, int]
    first_finish_rounds: dict[str, int]
    standings_message_id: int | None = None
    #: The message carrying the **constructor** standings, where the image flow posted two.
    #: The textual flow posts one message for both championships and leaves this null; the
    #: image flow needs the two nameable apart so either may be replaced, or fall back to
    #: text, without disturbing the other (Constitution XIV.4, XIV.7 as amended at v4.5.0).
    constructor_standings_message_id: int | None = None
    driver_profile_id: int | None = None
    # True when the driver has at least one session result in the division (even 0-point DNF).
    # Not persisted to DB; set during compute_driver_standings.
    race_participant: bool = False


@dataclass
class TeamStandingsSnapshot:
    id: int
    round_id: int
    division_id: int
    #: The division's team the row ranks, never its Discord role (#375).
    team_instance_id: int
    standing_position: int
    total_points: int
    finish_counts: dict[str, int]
    first_finish_rounds: dict[str, int]
