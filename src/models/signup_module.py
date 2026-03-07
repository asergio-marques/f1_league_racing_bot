"""Signup module dataclasses."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

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


@dataclass
class SignupModuleConfig:
    server_id: int
    signup_channel_id: int
    base_role_id: int
    signed_up_role_id: int
    signups_open: bool
    signup_button_message_id: int | None
    selected_tracks: list[str]


@dataclass
class SignupModuleSettings:
    server_id: int
    nationality_required: bool
    time_type: TimeType
    time_image_required: bool


@dataclass
class AvailabilitySlot:
    """
    id            — internal surrogate PK (used for DB deletion).
    server_id     — owning server.
    slot_id       — 1-based user-visible rank (computed on read, not stored).
    day_of_week   — 1=Mon … 7=Sun.
    time_hhmm     — "HH:MM" 24-hour.
    display_label — e.g. "Monday 14:30 UTC" (computed on read).
    """

    id: int
    server_id: int
    slot_id: int
    day_of_week: int
    time_hhmm: str
    display_label: str

    @staticmethod
    def make_label(day_of_week: int, time_hhmm: str) -> str:
        day_name = _DAY_NAMES.get(day_of_week, f"Day{day_of_week}")
        return f"{day_name} {time_hhmm} UTC"
