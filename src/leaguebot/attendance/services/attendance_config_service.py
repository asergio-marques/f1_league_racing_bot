"""Attendance's settings, and the answers and messages of a round's check-in.

`AttendanceService` reads and writes `attendance_config` and `attendance_division_config`, a
round's rows in `driver_round_attendance` and its check-in messages in `rsvp_embed_messages`.
The rules of timing are here too: `validate_timing_invariant`, and `derive_checkin_deadline`,
the moment a round's check-in closes, which the image module also reads. What the module hands
core's hooks is in `attendance_hooks`, the points a round confers in `attendance_scoring`,
the sheet in `attendance_sheet` and the sanctions in `attendance_sanctions`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta

import aiosqlite

from leaguebot.attendance.models.attendance import (
    AttendanceConfig,
    AttendanceDivisionConfig,
    DriverRoundAttendance,
    RsvpEmbedMessage,
)
from leaguebot.attendance.services.module_switch_settings import attendance_enabled_on
from leaguebot.core.db.database import get_connection

log = logging.getLogger(__name__)


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


def derive_checkin_deadline(
    scheduled_at: datetime,
    deadline_hours: int,
) -> datetime:
    """The moment beyond which a round's check-in can no longer be altered.

    The round's scheduled time less the hours configured via ``attendance config
    rsvp-deadline``; a configuration of ``0`` places it at the round's own start.

    **Why this lives here and not in the image utility.** The check-in graphic draws this
    value and the embed does not, which makes it a *derived presentation* under Constitution
    XIV.7 — arithmetic over figures the bot already holds, deciding nothing. The clause admits
    it on the condition that the derivation is written in the service owning the figures, so
    the textual path can adopt the column later without a second implementation. A graphic
    that subtracted its own hours would be that second implementation.

    It is a measurement and not a decision: the module already *enforces* this deadline when
    it schedules the deadline job, and the graphic reads the result of that rule rather than
    applying one.

    This is the deadline held against **full-time** drivers. The later deadline a reserve is
    held to is carried by neither the graphic nor the embed, and is not this function's.
    """
    return scheduled_at - timedelta(hours=deadline_hours)


class RsvpOutcome(Enum):
    """What `AttendanceService.answer_rsvp_on` did with one press of a check-in button."""

    # a full-time driver, at the deadline (the round's start where it is 0)
    LOCKED_AT_DEADLINE = "locked at the deadline"
    LOCKED_ACCEPTED = "locked, accepted"  # a reserve who has accepted, at the deadline
    LOCKED_AT_START = "locked at the start"  # any other reserve, at the round's start
    UNCHANGED = "unchanged"  # the answer was already held
    RECORDED = "recorded"
    NOT_RECORDED = "not recorded"  # the write changed no row


@dataclass(frozen=True)
class RsvpAnswer:
    """The result of `AttendanceService.answer_rsvp_on`: the *outcome*, and *found*, the answer
    the driver held when it was decided (``NO_RSVP`` for a driver with no row)."""

    outcome: RsvpOutcome
    found: str


def rsvp_lock(
    found: str,
    *,
    is_reserve: bool,
    now: datetime,
    scheduled_at: datetime,
    deadline_hours: int,
) -> RsvpOutcome | None:
    """The lock that bars an answer given at *now* to a call whose driver holds *found*, else None.

    The lock rules (attendance specification, RSVPing), in the order they are applied, each before
    the answer held is compared:

    - a full-time driver is locked at the deadline (``LOCKED_AT_DEADLINE``);
    - a reserve who has accepted is locked at the deadline (``LOCKED_ACCEPTED``);
    - any other reserve is locked at the round's start (``LOCKED_AT_START``);
    - a deadline of 0 puts the deadline at the round's start.
    """
    deadline_at = derive_checkin_deadline(scheduled_at, deadline_hours)
    start = deadline_at + timedelta(hours=deadline_hours)
    if not is_reserve:
        return RsvpOutcome.LOCKED_AT_DEADLINE if now >= deadline_at else None
    if found == "ACCEPTED":
        return RsvpOutcome.LOCKED_ACCEPTED if now >= deadline_at else None
    return RsvpOutcome.LOCKED_AT_START if now >= start else None


class AttendanceService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── League-level config ────────────────────────────────────────────────

    async def get_config(self) -> AttendanceConfig | None:
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT * FROM attendance_config")
            row = await cursor.fetchone()
        if row is None:
            return None
        return AttendanceConfig(
            module_enabled=bool(row["module_enabled"]),
            rsvp_notice_days=row["rsvp_notice_days"],
            rsvp_last_notice_hours=row["rsvp_last_notice_hours"],
            rsvp_deadline_hours=row["rsvp_deadline_hours"],
            no_rsvp_penalty=row["no_rsvp_penalty"],
            absent_penalty=row["absent_penalty"],
            no_show_penalty=row["no_show_penalty"],
            autoreserve_threshold=row["autoreserve_threshold"],
            autosack_threshold=row["autosack_threshold"],
        )

    async def get_config_or_defaults(self) -> AttendanceConfig:
        """The stored configuration, or the packaged defaults where none is stored, writing nothing.

        The defaults are the schema's own column defaults, read from it, so that the module
        keeps one copy of them: the row the module's switch-on writes leaves them to the schema.
        A read hook uses this, so that a read never creates the row.
        """
        existing = await self.get_config()
        if existing is not None:
            return existing
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT name, dflt_value FROM pragma_table_info('attendance_config')"
            )
            defaults = {row["name"]: row["dflt_value"] for row in await cursor.fetchall()}

        def _int(column: str) -> int:
            return int(defaults[column])

        def _nullable(column: str) -> int | None:
            value = defaults[column]
            return None if value is None else int(value)

        return AttendanceConfig(
            module_enabled=bool(_int("module_enabled")),
            rsvp_notice_days=_int("rsvp_notice_days"),
            rsvp_last_notice_hours=_int("rsvp_last_notice_hours"),
            rsvp_deadline_hours=_int("rsvp_deadline_hours"),
            no_rsvp_penalty=_int("no_rsvp_penalty"),
            absent_penalty=_int("absent_penalty"),
            no_show_penalty=_int("no_show_penalty"),
            autoreserve_threshold=_nullable("autoreserve_threshold"),
            autosack_threshold=_nullable("autosack_threshold"),
        )

    async def switch_off_on(self, db: aiosqlite.Connection) -> bool:
        """Turn the module off on *db* without committing, and say whether it was on.

        The flag goes down and every division's check-in and attendance channels are cleared;
        the settings row, with its timings, penalties and thresholds, is kept. It writes on the
        caller's connection so that a change which turns attendance off along with something
        else saves both in one go: the plain `/module disable attendance` and the cascade from
        turning Results & Standings off both use it.
        """
        was_on = await attendance_enabled_on(db)
        await db.execute("UPDATE attendance_config SET module_enabled = 0")
        await db.execute("DELETE FROM attendance_division_config")
        return was_on

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
            rsvp_channel_id=row["rsvp_channel_id"],
            attendance_channel_id=row["attendance_channel_id"],
            attendance_message_id=row["attendance_message_id"],
        )

    # ── driver_round_attendance CRUD ───────────────────────────────────────

    @staticmethod
    async def bulk_insert_attendance_rows_on(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        driver_profile_ids: list[int],
    ) -> None:
        """Insert NO_RSVP rows for every driver in the list (ignore if already exists) on *db*,
        committing nothing: the call's record writes its rows in the save that marks the posting
        job done."""
        await db.executemany(
            """
            INSERT OR IGNORE INTO driver_round_attendance
                (round_id, division_id, driver_profile_id)
            VALUES (?, ?, ?)
            """,
            [(round_id, division_id, dp_id) for dp_id in driver_profile_ids],
        )

    @staticmethod
    async def upsert_rsvp_status_on(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        driver_profile_id: int,
        status: str,
        *,
        now: datetime,
    ) -> bool:
        """Record *status* for one driver of one round on *db*, and manage accepted_at with it.

        Nothing is committed: the bulk-set change's save writes every entry of a paste and commits
        them once.

        - Transitioning TO 'ACCEPTED': set accepted_at to *now*.
        - Re-accepting after a non-ACCEPTED status: reset accepted_at to *now*.
        - Transitioning AWAY from 'ACCEPTED': set accepted_at to NULL.

        Returns whether a row now carries the answer, so that the bulk-set form lists as applied
        only what was saved, rather than assume the write landed.

        **It inserts, and that is the point** (issue #209). This was two bare ``UPDATE``
        statements despite its name, so a driver holding no ``driver_round_attendance`` row
        had their answer discarded in silence while the button handler thanked them for it.
        Only ``bulk_insert_attendance_rows_on`` creates those rows and only the check-in call's
        record calls it, against the roster as it stood when the call was posted — so a driver
        assigned, moved or confirmed into the division afterwards has none, and pressing a
        button was the one way they could ever get one.

        The insert belongs here rather than on the paths that place a driver. A press reaches the
        same write through ``answer_rsvp_on`` (by way of ``_write_answer``), which has established
        everything the row needs — the driver holds a confirmed placement in this division, the
        round exists, and the answer is inside the lock — so the fact arrives at the press and
        nowhere earlier; the test-mode bulk-set form is this method's caller.
        Seeding rows from ``assign_on``, ``move_driver`` and the mid-season confirmation
        instead would put the same work in three places, each having to decide for itself which
        of the division's rounds have a call standing, and any placement route added later would
        reopen the hole.

        A row created here is written carrying the answered status in one statement, never
        created as NO_RSVP and then updated: the intermediate state has no reader, and
        ``accepted_at`` follows the ordinary rule above rather than a special case of it.
        Every other column takes the same default ``bulk_insert_attendance_rows_on`` gives it.
        """
        accepted_at = now.isoformat() if status == "ACCEPTED" else None
        return await AttendanceService._write_answer(
            db, round_id, division_id, driver_profile_id, status, accepted_at
        )

    @staticmethod
    async def _write_answer(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        driver_profile_id: int,
        status: str,
        accepted_at: str | None,
    ) -> bool:
        """Write one driver's answer on *db* without committing, and say whether a row holds it.

        The one statement `upsert_rsvp_status_on` and `answer_rsvp_on` share, so the row an answer
        opens and the `accepted_at` it carries are decided in one place. The caller commits.
        """
        cursor = await db.execute(
            """
            INSERT INTO driver_round_attendance
                (round_id, division_id, driver_profile_id, rsvp_status, accepted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (round_id, division_id, driver_profile_id) DO UPDATE
               SET rsvp_status = excluded.rsvp_status,
                   accepted_at = excluded.accepted_at
            """,
            (round_id, division_id, driver_profile_id, status, accepted_at),
        )
        return cursor.rowcount > 0

    @staticmethod
    async def answer_rsvp_on(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        driver_profile_id: int,
        status: str,
        *,
        now: datetime,
        scheduled_at: datetime,
        deadline_hours: int,
        is_reserve: bool,
    ) -> RsvpAnswer:
        """Decide one press of a check-in button and, where it stands, write it, on *db*.

        The answer the driver holds is read, the lock rules (`rsvp_lock`) applied against *now*,
        the moment the button was pressed and not the moment the answer's turn comes (Constitution
        XIII), and the new answer written, all on the caller's connection and commit: the answer's
        job runs this in its save, one change at a time, so a second press waits for the first and
        decides on the answer the first left (#482, F1). That is what keeps an accepted reserve
        locked at the deadline whatever presses follow (F2).

        Past the locks, an answer equal to *status* is ``UNCHANGED`` and nothing is written, so a
        reserve's ``accepted_at`` stays as it was. Otherwise it is written, with ``accepted_at``
        set from *now* by `upsert_rsvp_status_on`'s rule, and ``RECORDED`` or, where the write
        changed no row, ``NOT_RECORDED``. Nothing is committed; every Discord call is the job's
        after, so the lock is held for the database alone.
        """
        cursor = await db.execute(
            """
            SELECT rsvp_status FROM driver_round_attendance
             WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?
            """,
            (round_id, division_id, driver_profile_id),
        )
        row = await cursor.fetchone()
        found: str = row["rsvp_status"] if row is not None else "NO_RSVP"
        locked = rsvp_lock(
            found, is_reserve=is_reserve, now=now, scheduled_at=scheduled_at,
            deadline_hours=deadline_hours,
        )
        if locked is not None:
            return RsvpAnswer(locked, found)
        if found == status:
            return RsvpAnswer(RsvpOutcome.UNCHANGED, found)
        accepted_at = now.isoformat() if status == "ACCEPTED" else None
        recorded = await AttendanceService._write_answer(
            db, round_id, division_id, driver_profile_id, status, accepted_at
        )
        return RsvpAnswer(
            RsvpOutcome.RECORDED if recorded else RsvpOutcome.NOT_RECORDED, found
        )

    async def get_attendance_rows(
        self,
        round_id: int,
        division_id: int,
    ) -> list[DriverRoundAttendance]:
        """Return all DRA rows for a (round, division) pair."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM driver_round_attendance WHERE round_id = ? AND division_id = ?",
                (round_id, division_id),
            )
            rows = await cursor.fetchall()
        return [_dra_from_row(r) for r in rows]

    async def driver_profile_id_for_account(self, discord_user_id: int) -> int | None:
        """The id of the driver profile whose current account is *discord_user_id*, or None.

        What the test-mode bulk-set form reads an entry's id by; a profile is matched on its
        number so that an id stored as text still finds it.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT id FROM driver_profiles WHERE CAST(discord_user_id AS INTEGER) = ?",
                (discord_user_id,),
            )
            row = await cursor.fetchone()
        return int(row["id"]) if row is not None else None

    async def get_attendance_row_for_driver(
        self,
        round_id: int,
        division_id: int,
        driver_profile_id: int,
    ) -> DriverRoundAttendance | None:
        """Return the DRA row for a specific driver, or None if not found."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT * FROM driver_round_attendance
                 WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?
                """,
                (round_id, division_id, driver_profile_id),
            )
            row = await cursor.fetchone()
        return _dra_from_row(row) if row is not None else None

    # ── rsvp_embed_messages CRUD ───────────────────────────────────────────

    @staticmethod
    async def insert_embed_message_on(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        message_id: str,
        channel_id: str,
        *,
        posted_at: datetime,
    ) -> None:
        """Store (or replace) the RSVP embed message IDs for a (round, division) pair on *db*,
        committing nothing, the call posted at *posted_at*: the call's record is saved with the
        mark of the job that posted it. A call replacing one recorded for the pair takes its record
        in this one statement, its last notice and distribution announcement cleared: they were
        the earlier call's, and the new one has none yet."""
        await db.execute(
            """
            INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, posted_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(round_id, division_id)
            DO UPDATE SET message_id = excluded.message_id,
                          channel_id = excluded.channel_id,
                          posted_at  = excluded.posted_at,
                          last_notice_msg_id = NULL,
                          last_notice_channel_id = NULL,
                          distribution_msg_id = NULL,
                          distribution_channel_id = NULL,
                          deadline_run_at = NULL
            """,
            (round_id, division_id, message_id, channel_id,
             posted_at.isoformat()),
        )

    async def get_embed_message(
        self,
        round_id: int,
        division_id: int,
    ) -> RsvpEmbedMessage | None:
        """Return the embed message row for a (round, division) pair, or None."""
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT * FROM rsvp_embed_messages WHERE round_id = ? AND division_id = ?",
                (round_id, division_id),
            )
            row = await cursor.fetchone()
        return _rem_from_row(row) if row is not None else None

    async def get_all_embed_messages(self) -> list[RsvpEmbedMessage]:
        """Return all rsvp_embed_messages rows unconditionally.

        Locking is enforced at interaction time, not at view re-arm time.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute("SELECT * FROM rsvp_embed_messages")
            rows = await cursor.fetchall()
        return [_rem_from_row(r) for r in rows]

    async def get_current_embed_message(self, division_id: int) -> RsvpEmbedMessage | None:
        """The call *division_id*'s check-in is at: the earliest whose deadline has not run.

        Where every standing call's deadline has run, the latest of them; where none stands,
        None. A deadline marks its call (`deadline_run_at`) when it writes its distribution, whatever
        becomes of its announcement; the restart's catch-up of a missed deadline reads the same
        mark (#429), and the two must agree.

        A division can hold more than one call. Each stands until 24 hours after its own round
        (#425), so two rounds close together — a double-header — stand side by side, and "the
        division's call" is no longer the only row it holds.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                """
                SELECT rem.*
                  FROM rsvp_embed_messages rem
                  JOIN rounds r ON r.id = rem.round_id
                 WHERE rem.division_id = ?
                 ORDER BY r.scheduled_at, rem.round_id
                """,
                (division_id,),
            )
            calls = [_rem_from_row(r) for r in await cursor.fetchall()]
        still_open = [call for call in calls if call.deadline_run_at is None]
        if still_open:
            return still_open[0]
        return calls[-1] if calls else None

    @staticmethod
    async def update_embed_last_notice_msg_on(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        msg_id: str,
        channel_id: str | None = None,
    ) -> None:
        """Store the last-notice ping's message ID for this round on *db*, committing nothing:
        the last notice's record is saved with the mark of the job that posted it."""
        await db.execute(
            "UPDATE rsvp_embed_messages SET last_notice_msg_id = ?, last_notice_channel_id = ?"
            " WHERE round_id = ? AND division_id = ?",
            (msg_id, channel_id, round_id, division_id),
        )

    @staticmethod
    async def update_embed_distribution_msg_on(
        db: aiosqlite.Connection,
        round_id: int,
        division_id: int,
        msg_id: str,
        channel_id: str | None = None,
    ) -> None:
        """Store the reserve distribution announcement's message ID on *db*, committing nothing:
        the announcement's record is saved with the mark of the job that posted it."""
        await db.execute(
            "UPDATE rsvp_embed_messages SET distribution_msg_id = ?, distribution_channel_id = ?"
            " WHERE round_id = ? AND division_id = ?",
            (msg_id, channel_id, round_id, division_id),
        )


def _dra_from_row(row: aiosqlite.Row) -> DriverRoundAttendance:
    return DriverRoundAttendance(
        id=row["id"],
        round_id=row["round_id"],
        division_id=row["division_id"],
        driver_profile_id=row["driver_profile_id"],
        rsvp_status=row["rsvp_status"],
        accepted_at=row["accepted_at"],
        assigned_team_id=row["assigned_team_id"],
        is_standby=bool(row["is_standby"]),
        attended=bool(row["attended"]) if row["attended"] is not None else None,
        points_awarded=row["points_awarded"],
        total_points_after=row["total_points_after"],
    )


def _rem_from_row(row: aiosqlite.Row) -> RsvpEmbedMessage:
    return RsvpEmbedMessage(
        id=row["id"],
        round_id=row["round_id"],
        division_id=row["division_id"],
        message_id=row["message_id"],
        channel_id=row["channel_id"],
        posted_at=row["posted_at"],
        last_notice_msg_id=row["last_notice_msg_id"],
        distribution_msg_id=row["distribution_msg_id"],
        last_notice_channel_id=row["last_notice_channel_id"],
        distribution_channel_id=row["distribution_channel_id"],
        deadline_run_at=row["deadline_run_at"],
    )
