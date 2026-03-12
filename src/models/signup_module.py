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
    server_id: int
    signup_channel_id: int
    base_role_id: int
    signed_up_role_id: int
    signups_open: bool
    signup_button_message_id: int | None
    selected_tracks: list[str]
    signup_closed_message_id: int | None = None


@dataclass
class SignupModuleSettings:
    server_id: int
    nationality_required: bool
    time_type: TimeType
    time_image_required: bool


@dataclass
class AvailabilitySlot:
    """
    id               — internal surrogate PK (used for DB deletion).
    server_id        — owning server.
    slot_sequence_id — stable per-server integer ID (never reused after removal).
    day_of_week      — 1=Mon … 7=Sun.
    time_hhmm        — "HH:MM" 24-hour.
    display_label    — e.g. "Monday 14:30 UTC" (computed on read).
    """

    id: int
    server_id: int
    slot_sequence_id: int
    day_of_week: int
    time_hhmm: str
    display_label: str

    @staticmethod
    def make_label(day_of_week: int, time_hhmm: str) -> str:
        day_name = _DAY_NAMES.get(day_of_week, f"Day{day_of_week}")
        return f"{day_name} {time_hhmm} UTC"


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
    """Committed signup submission for a driver on a server."""
    id: int
    server_id: int
    discord_user_id: str
    discord_username: str | None
    server_display_name: str | None
    nationality: str | None
    platform: str | None
    platform_id: str | None
    availability_slot_ids: list[int]
    driver_type: str | None
    preferred_teams: list[str]
    preferred_teammate: str | None
    lap_times: dict[str, str]     # track_id → normalised "M:ss.mss"
    notes: str | None
    signup_channel_id: int | None
    total_lap_ms: int | None = None  # computed once at approval; NULL = no times


@dataclass
class SignupWizardRecord:
    """In-progress wizard state for a driver on a server."""
    id: int
    server_id: int
    discord_user_id: str
    wizard_state: WizardState
    signup_channel_id: int | None
    config_snapshot: ConfigSnapshot | None
    draft_answers: dict[str, Any]
    current_lap_track_index: int
    last_activity_at: str   # ISO-8601 UTC
