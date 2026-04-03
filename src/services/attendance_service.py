"""AttendanceService — read/write attendance module configuration."""
from __future__ import annotations

from db.database import get_connection
from models.attendance import AttendanceConfig, AttendanceDivisionConfig


def validate_timing_invariant(
    notice_days: int,
    last_notice_hours: int,
    deadline_hours: int,
) -> str | None:
    """Return an error string if the timing invariant is violated, else None.

    Invariants:
    - notice_days * 24 > last_notice_hours  (always)
    - last_notice_hours > deadline_hours    (only when last_notice_hours > 0;
                                             0 is the sentinel meaning "no last notice")
    """
    if notice_days * 24 <= last_notice_hours:
        return (
            f"`rsvp_notice_days` ({notice_days}) \u00d7 24 = {notice_days * 24}h "
            f"must be greater than `rsvp_last_notice_hours` ({last_notice_hours}h)."
        )
    if last_notice_hours > 0 and last_notice_hours <= deadline_hours:
        return (
            f"`rsvp_last_notice_hours` ({last_notice_hours}h) "
            f"must be greater than `rsvp_deadline_hours` ({deadline_hours}h)."
        )
    return None


class AttendanceService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Server-level config ────────────────────────────────────────────────

    async def get_config(self, server_id: int) -> AttendanceConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM attendance_config WHERE server_id = ?",
                (server_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return AttendanceConfig(
            server_id=row["server_id"],
            module_enabled=bool(row["module_enabled"]),
            rsvp_notice_days=row["rsvp_notice_days"],
            rsvp_last_notice_hours=row["rsvp_last_notice_hours"],
            rsvp_deadline_hours=row["rsvp_deadline_hours"],
            no_rsvp_penalty=row["no_rsvp_penalty"],
            no_attend_penalty=row["no_attend_penalty"],
            no_show_penalty=row["no_show_penalty"],
            autoreserve_threshold=row["autoreserve_threshold"],
            autosack_threshold=row["autosack_threshold"],
        )

    async def get_or_create_config(self, server_id: int) -> AttendanceConfig:
        existing = await self.get_config(server_id)
        if existing is not None:
            return existing
        async with get_connection(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO attendance_config (server_id) VALUES (?)",
                (server_id,),
            )
            await db.commit()
        result = await self.get_config(server_id)
        assert result is not None
        return result

    async def delete_division_configs(self, server_id: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM attendance_division_config WHERE server_id = ?",
                (server_id,),
            )
            await db.commit()

    # ── Division-level config ──────────────────────────────────────────────

    async def get_division_config(self, division_id: int) -> AttendanceDivisionConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM attendance_division_config WHERE division_id = ?",
                (division_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return AttendanceDivisionConfig(
            division_id=row["division_id"],
            server_id=row["server_id"],
            rsvp_channel_id=row["rsvp_channel_id"],
            attendance_channel_id=row["attendance_channel_id"],
        )

    async def set_rsvp_channel(
        self, division_id: int, server_id: int, channel_id: int
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO attendance_division_config (division_id, server_id, rsvp_channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(division_id)
                DO UPDATE SET rsvp_channel_id = excluded.rsvp_channel_id
                """,
                (division_id, server_id, str(channel_id)),
            )
            await db.commit()

    async def set_attendance_channel(
        self, division_id: int, server_id: int, channel_id: int
    ) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO attendance_division_config (division_id, server_id, attendance_channel_id)
                VALUES (?, ?, ?)
                ON CONFLICT(division_id)
                DO UPDATE SET attendance_channel_id = excluded.attendance_channel_id
                """,
                (division_id, server_id, str(channel_id)),
            )
            await db.commit()

    # ── Field updates ──────────────────────────────────────────────────────

    async def update_rsvp_notice_days(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_notice_days = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_rsvp_last_notice_hours(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_last_notice_hours = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_rsvp_deadline_hours(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_deadline_hours = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_no_rsvp_penalty(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET no_rsvp_penalty = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_no_attend_penalty(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET no_attend_penalty = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_no_show_penalty(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET no_show_penalty = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_autosack_threshold(self, server_id: int, value: int | None) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET autosack_threshold = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_autoreserve_threshold(self, server_id: int, value: int | None) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET autoreserve_threshold = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()
