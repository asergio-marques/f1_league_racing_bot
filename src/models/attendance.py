"""Dataclasses for the Attendance Module."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AttendanceConfig:
    server_id: int
    module_enabled: bool
    rsvp_notice_days: int
    rsvp_last_notice_hours: int
    rsvp_deadline_hours: int
    no_rsvp_penalty: int
    no_attend_penalty: int
    no_show_penalty: int
    autoreserve_threshold: int | None
    autosack_threshold: int | None


@dataclass
class AttendanceDivisionConfig:
    division_id: int
    server_id: int
    rsvp_channel_id: str | None
    attendance_channel_id: str | None


@dataclass
class DriverRoundAttendance:
    id: int
    round_id: int
    division_id: int
    driver_profile_id: int
    rsvp_status: str        # NO_RSVP | ACCEPTED | TENTATIVE | DECLINED
    accepted_at: str | None
    assigned_team_id: int | None
    is_standby: bool
    attended: bool | None   # None until results submitted (future increment)


@dataclass
class RsvpEmbedMessage:
    id: int
    round_id: int
    division_id: int
    message_id: str
    channel_id: str
    posted_at: str
