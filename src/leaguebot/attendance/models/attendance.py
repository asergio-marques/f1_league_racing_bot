"""Dataclasses for the Attendance Module."""
from __future__ import annotations

from dataclasses import dataclass

#: The kinds of attendance's four check-in changes. Core names none of them: attendance asks for
#: each itself and reads what is still unfinished of each for its own steps of `/test-mode
#: advance`.
CHECK_IN_CALL = "attendance.check_in.call"
CHECK_IN_LAST_NOTICE = "attendance.check_in.last_notice"
CHECK_IN_DEADLINE = "attendance.check_in.deadline"
CHECK_IN_CLEAN_UP = "attendance.check_in.clean_up"


@dataclass
class AttendanceConfig:
    module_enabled: bool
    rsvp_notice_days: int
    rsvp_last_notice_hours: int
    rsvp_deadline_hours: int
    no_rsvp_penalty: int
    absent_penalty: int
    no_show_penalty: int
    autoreserve_threshold: int | None
    autosack_threshold: int | None


@dataclass
class AttendanceDivisionConfig:
    division_id: int
    rsvp_channel_id: str | None
    attendance_channel_id: str | None
    attendance_message_id: str | None


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
    attended: bool | None   # None until results submitted
    points_awarded: int | None      # Net points after pardons; set at finalization
    total_points_after: int | None  # The driver's division total as at this round (#238)


@dataclass
class RsvpEmbedMessage:
    id: int
    round_id: int
    division_id: int
    message_id: str
    channel_id: str
    posted_at: str
    last_notice_msg_id: str | None = None
    distribution_msg_id: str | None = None
    #: The channel each of the two was posted in, which may not be the call's: the division's
    #: check-in channel is read when they are posted, and may have moved since the call.
    last_notice_channel_id: str | None = None
    distribution_channel_id: str | None = None
    #: When the call's deadline ran (its distribution was written), apart from the announcement it
    #: posts: a deadline whose announcement was discarded has still run, and is not run again.
    deadline_run_at: str | None = None
