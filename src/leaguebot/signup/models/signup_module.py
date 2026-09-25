"""Signup module dataclasses."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

TimeType = Literal["TIME_TRIAL", "SHORT_QUALIFICATION"]

_DAY_NAMES = {
    1: "Monday",
    2: "Tuesday",
    3: "Wednesday",
    4: "Thursday",
    5: "Friday",
    6: "Saturday",
    7: "Sunday",
}


class WizardState(str, Enum):
    """State of a driver's signup wizard session."""
    UNENGAGED                    = "UNENGAGED"
    COLLECTING_NATIONALITY       = "COLLECTING_NATIONALITY"
    COLLECTING_PLATFORM          = "COLLECTING_PLATFORM"
    COLLECTING_PLATFORM_ID       = "COLLECTING_PLATFORM_ID"
    COLLECTING_AVAILABILITY      = "COLLECTING_AVAILABILITY"
    COLLECTING_DRIVER_TYPE       = "COLLECTING_DRIVER_TYPE"
    COLLECTING_PREFERRED_TEAMS   = "COLLECTING_PREFERRED_TEAMS"
    COLLECTING_PREFERRED_TEAMMATE = "COLLECTING_PREFERRED_TEAMMATE"
    COLLECTING_LAP_TIME          = "COLLECTING_LAP_TIME"
    COLLECTING_NOTES             = "COLLECTING_NOTES"


@dataclass
class SignupModuleConfig:
    """The module's own configuration. The base role and the driver role it reads are the
    league's, on `ServerConfig` (issue #276), and survive the module being disabled."""

    signup_channel_id: int | None
    signups_open: bool
    signup_button_message_id: int | None
    selected_tracks: list[str]
    signup_closed_message_id: int | None = None
    close_at: str | None = None


@dataclass
class SignupModuleSettings:
    nationality_required: bool
    time_type: TimeType
    time_image_required: bool


@dataclass
class AvailabilitySlot:
    """
    id               — internal surrogate PK (used for DB deletion).
    slot_id          — the slot's durable identity, "Mon_19_00" (computed on read).
    slot_sequence_id — temporary display ordinal, 1..N in chronological order.
    day_of_week      — 1=Mon … 7=Sun.
    time_hhmm        — "HH:MM" 24-hour.
    display_label    — e.g. "Monday 14:30 UTC" (computed on read).

    **A driver's recorded availability is stored as ``slot_id``, never as
    ``slot_sequence_id``.** The ordinal is recomputed from chronological order on
    every read, so adding or removing a slot renumbers every slot after it; anything
    stored against an ordinal silently comes to mean a different time (issue #126).
    ``slot_id`` is derived from the day and time, which are unique, so it
    cannot drift from the slot it names and a removed-then-re-added slot recovers its
    own answers. The ordinal exists only so a league types ``3`` rather than
    ``Fri_21_00``; it is converted to ``slot_id`` at the input boundary and never
    persisted. ``test_slot_id_format`` pins the format.
    """

    id: int
    slot_id: str
    slot_sequence_id: int
    day_of_week: int
    time_hhmm: str
    display_label: str

    @staticmethod
    def make_label(day_of_week: int, time_hhmm: str) -> str:
        day_name = _DAY_NAMES.get(day_of_week, f"Day{day_of_week}")
        return f"{day_name} {time_hhmm} UTC"

    @staticmethod
    def make_slot_id(day_of_week: int, time_hhmm: str) -> str:
        """Return the slot's durable identity, e.g. "Mon_19_00" for Monday 19:00."""
        day_abbr = _DAY_NAMES.get(day_of_week, f"Day{day_of_week}")[:3]
        return f"{day_abbr}_{time_hhmm.replace(':', '_')}"


@dataclass
class ConfigSnapshot:
    """Immutable snapshot of signup configuration captured at wizard start."""
    nationality_required: bool
    time_type: TimeType
    time_image_required: bool
    selected_track_ids: list[str]
    slots: list[AvailabilitySlot]
    team_names: list[str] = field(default_factory=list)


@dataclass
class SignupRecord:
    """One completed signup: kept permanently under its season and window (issue #220).

    A driver who signs up more than once holds a record for each signup. ``id`` is -1 for a
    record not yet stored; saving one inserts it, and saving a stored one updates it in place,
    which is how a correction amends the signup it was asked of.
    """
    id: int
    discord_user_id: str
    discord_username: str | None
    server_display_name: str | None
    nationality: str | None
    platform: str | None
    platform_id: str | None
    # Durable slot IDs ("Mon_19_00"), never display ordinals — see AvailabilitySlot.
    availability_slot_ids: list[str]
    driver_type: str | None
    preferred_teams: list[str]
    preferred_teammate: str | None
    lap_times: dict[str, str]     # track_id → normalised "M:ss.mss"
    notes: str | None
    signup_channel_id: int | None
    total_lap_ms: int | None = None  # computed once at approval; NULL = no times
    season_id: int | None = None
    window_id: int | None = None


@dataclass
class SignupWizardRecord:
    """In-progress wizard state for a driver on a server."""
    id: int
    discord_user_id: str
    wizard_state: WizardState
    signup_channel_id: int | None
    config_snapshot: ConfigSnapshot | None
    draft_answers: dict[str, Any]
    current_lap_track_index: int
    last_activity_at: str   # ISO-8601 UTC
