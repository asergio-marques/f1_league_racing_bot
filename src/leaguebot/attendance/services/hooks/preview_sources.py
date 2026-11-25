"""Attendance's share of what the image module's previews draw
(the protocol is `image/services/preview/preview_sources.py`).

Built once by the entry point, which joins it with results' share and attaches the whole as
`bot.preview_sources`. It hands over two things. The check-in's deadline: it reads the lead
times as `windows_on` has them (the stored row's, or the schema's own column defaults where none
is stored, writing nothing) and works the deadline out with `derive_checkin_deadline`, so the
preview draws the deadline a real call draws and holds no arithmetic of its own. It answers
whether the module is on or off: a manager previewing the call before switching the module on
sees the deadline the league configured. And the order of the attendance sheet's rows, by
`after_round_order_key`, the key the textual sheet after a round sorts by, so that the preview
draws rows in an order attendance owns and the image module works out none.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from leaguebot.attendance.services.check_in.attendance_config_service import (
    derive_checkin_deadline,
    windows_on,
)
from leaguebot.attendance.services.sheet.attendance_sheet import after_round_order_key
from leaguebot.framework.db.database import get_connection


class AttendancePreviewSources:
    """The check-in's deadline and the attendance sheet's row order, for the image module's previews."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def check_in_deadline(self, scheduled_at: datetime) -> datetime:
        async with get_connection(self._db_path) as db:
            windows = await windows_on(db)
        return derive_checkin_deadline(scheduled_at, windows.deadline_hours)

    def attendance_sheet_order(
        self, totals: Mapping[int, int], names: Mapping[int, str]
    ) -> list[int]:
        """The keys of *totals* in the order the textual sheet after a round lists its drivers:
        most points first, drivers level on points by name (*names*, by the same keys)."""
        return sorted(totals, key=lambda key: after_round_order_key(totals[key], names[key]))
