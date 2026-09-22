"""AttendanceService — read/write attendance module configuration."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import discord

from db.database import get_connection
from models.attendance import (
    AttendanceConfig,
    AttendanceDivisionConfig,
    AttendancePardon,
    DriverRoundAttendance,
    RsvpEmbedMessage,
)
from models.classification_occasion import ClassificationOccasion


@asynccontextmanager
async def _shared_or_own(db_path: str, db):
    """Yield ``(connection, owned)`` — *db* where a caller supplied one, else a new one.

    ``owned`` says whether the callee should commit: a function handed a connection is one
    step of somebody else's transaction and must not commit it, or the atomicity the caller
    opened it for is lost a step at a time.

    **Why the recalculation needs this** (#187). ``recalculate_attendance_for_round``
    recomputes a round's attendance and then propagates the running total through every
    finalised round after it, one call apiece. Each call used to open and commit its own
    connection, so a failure in the middle of that loop left the league's attendance points
    correct up to round four and stale from round five on — and no command re-runs the
    recalculation, so there was no way back. Sharing one transaction makes the whole
    propagation succeed or none of it, which is what the approval it belongs to promises.

    The DB half is pure SQLite with no Discord round-trip in it, so the transaction is
    short. The posting deliberately stays outside it.
    """
    if db is not None:
        yield db, False
        return
    async with get_connection(db_path) as owned:
        yield owned, True


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
    from datetime import timedelta

    if scheduled_at.tzinfo is None:
        scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
    return scheduled_at - timedelta(hours=deadline_hours)


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

    async def get_or_create_config(self) -> AttendanceConfig:
        existing = await self.get_config()
        if existing is not None:
            return existing
        async with get_connection(self._db_path) as db:
            await db.execute("INSERT OR IGNORE INTO attendance_config (id) VALUES (1)")
            await db.commit()
        result = await self.get_config()
        assert result is not None
        return result

    async def delete_division_configs(self) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute("DELETE FROM attendance_division_config")
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
            rsvp_channel_id=row["rsvp_channel_id"],
            attendance_channel_id=row["attendance_channel_id"],
            attendance_message_id=row["attendance_message_id"],
        )

    async def set_rsvp_channel(self, division_id: int, channel_id: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO attendance_division_config (division_id, rsvp_channel_id)
                VALUES (?, ?)
                ON CONFLICT(division_id)
                DO UPDATE SET rsvp_channel_id = excluded.rsvp_channel_id
                """,
                (division_id, str(channel_id)),
            )
            await db.commit()

    async def set_attendance_channel(self, division_id: int, channel_id: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO attendance_division_config (division_id, attendance_channel_id)
                VALUES (?, ?)
                ON CONFLICT(division_id)
                DO UPDATE SET attendance_channel_id = excluded.attendance_channel_id
                """,
                (division_id, str(channel_id)),
            )
            await db.commit()

    # ── Field updates ──────────────────────────────────────────────────────

    async def update_rsvp_notice_days(self, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_notice_days = ?", (value,)
            )
            await db.commit()

    async def update_rsvp_last_notice_hours(self, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_last_notice_hours = ?", (value,)
            )
            await db.commit()

    async def update_rsvp_deadline_hours(self, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_deadline_hours = ?", (value,)
            )
            await db.commit()

    async def update_no_rsvp_penalty(self, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET no_rsvp_penalty = ?", (value,)
            )
            await db.commit()

    async def update_absent_penalty(self, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET absent_penalty = ?", (value,)
            )
            await db.commit()

    async def update_no_show_penalty(self, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET no_show_penalty = ?", (value,)
            )
            await db.commit()

    async def update_autosack_threshold(self, value: int | None) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET autosack_threshold = ?", (value,)
            )
            await db.commit()

    async def update_autoreserve_threshold(self, value: int | None) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET autoreserve_threshold = ?", (value,)
            )
            await db.commit()

    # ── driver_round_attendance CRUD ───────────────────────────────────────

    async def bulk_insert_attendance_rows(
        self,
        round_id: int,
        division_id: int,
        driver_profile_ids: list[int],
    ) -> None:
        """Insert NO_RSVP rows for every driver in the list (ignore if already exists)."""
        async with get_connection(self._db_path) as db:
            await db.executemany(
                """
                INSERT OR IGNORE INTO driver_round_attendance
                    (round_id, division_id, driver_profile_id)
                VALUES (?, ?, ?)
                """,
                [(round_id, division_id, dp_id) for dp_id in driver_profile_ids],
            )
            await db.commit()

    async def upsert_rsvp_status(
        self,
        round_id: int,
        division_id: int,
        driver_profile_id: int,
        status: str,
    ) -> bool:
        """Record *status* for one driver of one round, and manage accepted_at with it.

        - Transitioning TO 'ACCEPTED': set accepted_at to current UTC time.
        - Re-accepting after a non-ACCEPTED status: reset accepted_at to current UTC time.
        - Transitioning AWAY from 'ACCEPTED': set accepted_at to NULL.

        Returns whether a row now carries the answer, so a caller can tell a driver the truth
        rather than assume the write landed.

        **It inserts, and that is the point** (issue #209). This was two bare ``UPDATE``
        statements despite its name, so a driver holding no ``driver_round_attendance`` row
        had their answer discarded in silence while the button handler thanked them for it.
        Only ``bulk_insert_attendance_rows`` creates those rows and only ``run_rsvp_notice``
        calls it, against the roster as it stood when the call was posted — so a driver
        assigned, moved or confirmed into the division afterwards has none, and pressing a
        button was the one way they could ever get one.

        The insert belongs here rather than on the paths that place a driver. By the time
        ``handle_rsvp_button`` reaches this method it has established everything the row
        needs — the driver holds a confirmed placement in this division, the round exists,
        and the answer is inside the lock — so the fact arrives at the press and nowhere
        earlier. Seeding rows from ``assign_driver``, ``move_driver`` and
        ``commit_mid_season_placements`` instead would put the same work in three places,
        each having to decide for itself which of the division's rounds have a call standing,
        and any placement route added later would reopen the hole.

        A row created here is written carrying the answered status in one statement, never
        created as NO_RSVP and then updated: the intermediate state has no reader, and
        ``accepted_at`` follows the ordinary rule above rather than a special case of it.
        Every other column takes the same default ``bulk_insert_attendance_rows`` gives it.
        """
        accepted_at = datetime.now(timezone.utc).isoformat() if status == "ACCEPTED" else None
        async with get_connection(self._db_path) as db:
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
            await db.commit()
        return cursor.rowcount > 0

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

    async def insert_embed_message(
        self,
        round_id: int,
        division_id: int,
        message_id: str,
        channel_id: str,
    ) -> None:
        """Store (or replace) the RSVP embed message IDs for a (round, division) pair."""
        now_iso = datetime.now(timezone.utc).isoformat()
        async with get_connection(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO rsvp_embed_messages (round_id, division_id, message_id, channel_id, posted_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(round_id, division_id)
                DO UPDATE SET message_id = excluded.message_id,
                              channel_id = excluded.channel_id,
                              posted_at  = excluded.posted_at
                """,
                (round_id, division_id, message_id, channel_id, now_iso),
            )
            await db.commit()

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

    async def delete_stale_embed_messages(
        self,
        division_id: int,
        keep_round_id: int,
    ) -> None:
        """Delete all rsvp_embed_messages rows for *division_id* except the one
        for *keep_round_id*.  Called after a new RSVP notice is posted so that
        stale rows from previous rounds do not confuse embed look-ups.
        """
        async with get_connection(self._db_path) as db:
            await db.execute(
                "DELETE FROM rsvp_embed_messages"
                " WHERE division_id = ? AND round_id != ?",
                (division_id, keep_round_id),
            )
            await db.commit()

    async def update_embed_last_notice_msg(
        self,
        round_id: int,
        division_id: int,
        msg_id: str,
    ) -> None:
        """Store the Discord message ID of the last-notice ping for this round."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET last_notice_msg_id = ?"
                " WHERE round_id = ? AND division_id = ?",
                (msg_id, round_id, division_id),
            )
            await db.commit()

    async def update_embed_distribution_msg(
        self,
        round_id: int,
        division_id: int,
        msg_id: str,
    ) -> None:
        """Store the Discord message ID of the reserve distribution announcement."""
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE rsvp_embed_messages SET distribution_msg_id = ?"
                " WHERE round_id = ? AND division_id = ?",
                (msg_id, round_id, division_id),
            )
            await db.commit()


# ── Row-to-dataclass helpers ───────────────────────────────────────────────


def _dra_from_row(row: object) -> DriverRoundAttendance:
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


def _rem_from_row(row: object) -> RsvpEmbedMessage:
    return RsvpEmbedMessage(
        id=row["id"],
        round_id=row["round_id"],
        division_id=row["division_id"],
        message_id=row["message_id"],
        channel_id=row["channel_id"],
        posted_at=row["posted_at"],
        last_notice_msg_id=row["last_notice_msg_id"],
        distribution_msg_id=row["distribution_msg_id"],
    )


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Attendance pipeline — new functions added by 033-attendance-tracking
# ---------------------------------------------------------------------------

async def record_attendance_from_results(
    db_path: str,
    round_id: int,
    division_id: int,
) -> None:
    """Populate attended flag for every full-time driver in the division (FR-001–FR-004).

    - drivers seated in the Reserve team for this round are skipped (FR-002).
    - upgrade-only when called during progressive session submission (FR-003): a driver
      already marked attended=1 is never reverted.
    - during amendment recalculation this function is still called the same way but the
      caller is responsible for passing updated DriverSessionResult rows (FR-028).
    - cancelled rounds: skipped (no attendance recording for cancelled rounds).
    """
    async with get_connection(db_path) as db:
        # Guard: skip if round is cancelled.
        cursor = await db.execute(
            "SELECT status FROM rounds WHERE id = ?",
            (round_id,),
        )
        round_row = await cursor.fetchone()
        if round_row is None or round_row["status"] == "CANCELLED":
            log.info("record_attendance_from_results: skipping cancelled round %s", round_id)
            return

        # Set of driver_profile_ids who have any result row for this round.
        # Outcome modifier is irrelevant — any row counts as attended (DSQ/DNS included).
        cursor = await db.execute(
            """
            SELECT DISTINCT driver_profile_id FROM (
                SELECT rsr.driver_profile_id
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
                UNION ALL
                SELECT qsr.driver_profile_id
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
            ) WHERE driver_profile_id IS NOT NULL
            """,
            (round_id, round_id),
        )
        attended_rows = await cursor.fetchall()
        attended_ids: set[int] = {r["driver_profile_id"] for r in attended_rows}

        # Full-time DRA rows for this round, plus allocated reserve DRA rows
        # (assigned_team_id IS NOT NULL); unallocated reserves are excluded (FR-002).
        cursor = await db.execute(
            """
            SELECT dra.id, dra.driver_profile_id, dra.attended
            FROM driver_round_attendance dra
            JOIN driver_season_assignments dsa
                ON dsa.driver_profile_id = dra.driver_profile_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dra.round_id = ?
              AND dra.division_id = ?
              AND ti.division_id = ?
              AND (
                  ti.is_reserve = 0
                  OR (ti.is_reserve = 1 AND dra.assigned_team_id IS NOT NULL)
              )
            """,
            (round_id, division_id, division_id),
        )
        dra_rows = await cursor.fetchall()

        for row in dra_rows:
            dra_id = row["id"]
            profile_id = row["driver_profile_id"]
            current_attended = row["attended"]

            if profile_id in attended_ids:
                # Only write if upgrading NULL → 1 or 0 → 1 (FR-003).
                if current_attended != 1:
                    await db.execute(
                        "UPDATE driver_round_attendance SET attended = 1 WHERE id = ?",
                        (dra_id,),
                    )
            else:
                # Only write if currently NULL — never revert 1 → 0 (FR-003).
                if current_attended is None:
                    await db.execute(
                        "UPDATE driver_round_attendance SET attended = 0 WHERE id = ?",
                        (dra_id,),
                    )
        await db.commit()


async def record_attendance_from_results_full_recompute(
    db_path: str,
    round_id: int,
    division_id: int,
    *,
    db=None,
) -> None:
    """Recompute attended flags without the upgrade-only constraint (FR-028/amendment).

    Used exclusively by recalculate_attendance_for_round so that a deliberate result
    correction can flip attended in either direction. Skipped for cancelled rounds.

    *db* joins a transaction the caller already opened, and is then the caller's to commit;
    see :func:`_shared_or_own`.
    """
    async with _shared_or_own(db_path, db) as (db, _owned):
        # Guard: skip if round is cancelled.
        cursor = await db.execute(
            "SELECT status FROM rounds WHERE id = ?",
            (round_id,),
        )
        round_row = await cursor.fetchone()
        if round_row is None or round_row["status"] == "CANCELLED":
            log.info("record_attendance_from_results_full_recompute: skipping cancelled round %s", round_id)
            return

        cursor = await db.execute(
            """
            SELECT DISTINCT driver_profile_id FROM (
                SELECT rsr.driver_profile_id
                FROM race_session_results rsr
                JOIN session_results sr ON sr.id = rsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
                UNION ALL
                SELECT qsr.driver_profile_id
                FROM qualifying_session_results qsr
                JOIN session_results sr ON sr.id = qsr.session_result_id
                WHERE sr.round_id = ? AND sr.status = 'ACTIVE'
            ) WHERE driver_profile_id IS NOT NULL
            """,
            (round_id, round_id),
        )
        attended_rows = await cursor.fetchall()
        attended_ids: set[int] = {r["driver_profile_id"] for r in attended_rows}

        cursor = await db.execute(
            """
            SELECT dra.id, dra.driver_profile_id
            FROM driver_round_attendance dra
            JOIN driver_season_assignments dsa
                ON dsa.driver_profile_id = dra.driver_profile_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dra.round_id = ?
              AND dra.division_id = ?
              AND ti.division_id = ?
              AND (
                  ti.is_reserve = 0
                  OR (ti.is_reserve = 1 AND dra.assigned_team_id IS NOT NULL)
              )
            """,
            (round_id, division_id, division_id),
        )
        dra_rows = await cursor.fetchall()

        for row in dra_rows:
            new_val = 1 if row["driver_profile_id"] in attended_ids else 0
            await db.execute(
                "UPDATE driver_round_attendance SET attended = ? WHERE id = ?",
                (new_val, row["id"]),
            )
        if _owned:
            await db.commit()


async def distribute_attendance_points(
    db_path: str,
    round_id: int,
    division_id: int,
    *,
    db=None,
) -> None:
    """Compute and persist points_awarded and total_points_after for every full-time
    driver in the division for this round (FR-012–FR-015).

    ``total_points_after`` is the driver's total **as at this round**: the points of every
    earlier finalised round of the division, plus this round's. It is not the season's total
    (#238). The distinction only shows once a round is scored a second time — the sum used to
    be taken over every *other* finalised round, with nothing to hold it to the ones before
    this, so recomputing round 3 of ten wrote the whole season's figure onto round 3 and every
    round the cascade then touched read the same. Each round's copy is kept precisely so a
    figure that looks wrong can be traced round by round, which a flattened one cannot be.

    It follows that the latest round is where a division's current total stands, and that is
    the round a sheet is drawn against and the sanctions enforced upon — see
    :func:`cascade_attendance_from_round` and :func:`sync_attendance`.

    *db* joins a transaction the caller already opened, and is then the caller's to commit;
    see :func:`_shared_or_own`.
    """
    async with _shared_or_own(db_path, db) as (db, _owned):
        # Guard: skip if round is cancelled (no penalties for cancelled rounds).
        cursor = await db.execute(
            "SELECT status, round_number FROM rounds WHERE id = ?",
            (round_id,),
        )
        round_row = await cursor.fetchone()
        if round_row is None or round_row["status"] == "CANCELLED":
            log.info("distribute_attendance_points: skipping cancelled round %s", round_id)
            return
        this_round_number: int = round_row["round_number"]

        # Load penalty config for this division's server.
        cursor = await db.execute(
            """
            SELECT no_rsvp_penalty, absent_penalty, no_show_penalty
            FROM attendance_config
            """,
        )
        cfg_row = await cursor.fetchone()
        if cfg_row is None:
            log.warning("distribute_attendance_points: no attendance_config for division %s", division_id)
            return

        no_rsvp_pen: int = cfg_row["no_rsvp_penalty"] or 0
        absent_pen: int = cfg_row["absent_penalty"] or 0
        no_show_pen: int = cfg_row["no_show_penalty"] or 0

        # Load full-time DRA rows for this round.
        cursor = await db.execute(
            """
            SELECT dra.id, dra.driver_profile_id, dra.rsvp_status, dra.attended
            FROM driver_round_attendance dra
            JOIN driver_season_assignments dsa
                ON dsa.driver_profile_id = dra.driver_profile_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dra.round_id = ?
              AND dra.division_id = ?
              AND ti.division_id = ?
              AND ti.is_reserve = 0
              AND dra.attended IS NOT NULL
            """,
            (round_id, division_id, division_id),
        )
        dra_rows = await cursor.fetchall()

        for row in dra_rows:
            dra_id = row["id"]
            rsvp = row["rsvp_status"]
            attended = bool(row["attended"])

            # Compute base points before pardons (US3 rules table).
            # "Checked-in" = any RSVP response (ACCEPTED/TENTATIVE/DECLINED).
            # "Failure to check-in" = NO_RSVP.
            base = 0
            if rsvp == "NO_RSVP":
                base = no_rsvp_pen + (absent_pen if not attended else 0)
            elif rsvp == "ACCEPTED" and not attended:
                base = no_show_pen
            elif rsvp in {"TENTATIVE", "DECLINED"} and not attended:
                base = absent_pen

            # Load pardons for this DRA row.
            c2 = await db.execute(
                "SELECT pardon_type FROM attendance_pardons WHERE attendance_id = ?",
                (dra_id,),
            )
            pardons = {r["pardon_type"] for r in await c2.fetchall()}

            # Apply pardons — each waives its matching component.
            net = base
            if "NO_RSVP" in pardons:
                net -= no_rsvp_pen
            if "ABSENT" in pardons:
                net -= absent_pen
            if "NO_SHOW" in pardons:
                net -= no_show_pen
            net = max(0, net)  # never negative

            # Compute cumulative total across all finalized rounds in division.
            c3 = await db.execute(
                """
                SELECT COALESCE(SUM(dra2.points_awarded), 0) AS prior_total
                FROM driver_round_attendance dra2
                JOIN rounds r ON r.id = dra2.round_id
                WHERE dra2.driver_profile_id = ?
                  AND dra2.division_id = ?
                  AND r.status IN ('AWAITING_APPEAL_VERDICTS', 'FINAL')
                  AND r.round_number < ?
                  AND dra2.round_id != ?
                  AND dra2.points_awarded IS NOT NULL
                """,
                (row["driver_profile_id"], division_id, this_round_number, round_id),
            )
            prior_row = await c3.fetchone()
            prior_total: int = prior_row["prior_total"] if prior_row else 0
            total_after = prior_total + net

            await db.execute(
                """
                UPDATE driver_round_attendance
                SET points_awarded = ?, total_points_after = ?
                WHERE id = ?
                """,
                (net, total_after, dra_id),
            )

        # Load allocated-reserve DRA rows: reserve-seated drivers that were
        # distributed into a full-time seat for this round
        # (assigned_team_id IS NOT NULL) and RSVP'd ACCEPTED.
        # Only no_show_penalty applies; non-allocated or non-ACCEPTED reserves
        # remain excluded from attendance scoring.
        cursor = await db.execute(
            """
            SELECT dra.id, dra.driver_profile_id, dra.attended
            FROM driver_round_attendance dra
            JOIN driver_season_assignments dsa
                ON dsa.driver_profile_id = dra.driver_profile_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE dra.round_id = ?
              AND dra.division_id = ?
              AND ti.division_id = ?
              AND ti.is_reserve = 1
              AND dra.assigned_team_id IS NOT NULL
              AND dra.rsvp_status = 'ACCEPTED'
              AND dra.attended IS NOT NULL
            """,
            (round_id, division_id, division_id),
        )
        reserve_dra_rows = await cursor.fetchall()

        for row in reserve_dra_rows:
            dra_id = row["id"]
            attended = bool(row["attended"])
            base = no_show_pen if not attended else 0

            c2 = await db.execute(
                "SELECT pardon_type FROM attendance_pardons WHERE attendance_id = ?",
                (dra_id,),
            )
            pardons = {r["pardon_type"] for r in await c2.fetchall()}

            net = base
            if "NO_SHOW" in pardons:
                net -= no_show_pen
            net = max(0, net)

            c3 = await db.execute(
                """
                SELECT COALESCE(SUM(dra2.points_awarded), 0) AS prior_total
                FROM driver_round_attendance dra2
                JOIN rounds r ON r.id = dra2.round_id
                WHERE dra2.driver_profile_id = ?
                  AND dra2.division_id = ?
                  AND r.status IN ('AWAITING_APPEAL_VERDICTS', 'FINAL')
                  AND r.round_number < ?
                  AND dra2.round_id != ?
                  AND dra2.points_awarded IS NOT NULL
                """,
                (row["driver_profile_id"], division_id, this_round_number, round_id),
            )
            prior_row = await c3.fetchone()
            prior_total: int = prior_row["prior_total"] if prior_row else 0
            total_after = prior_total + net

            await db.execute(
                """
                UPDATE driver_round_attendance
                SET points_awarded = ?, total_points_after = ?
                WHERE id = ?
                """,
                (net, total_after, dra_id),
            )

        if _owned:
            await db.commit()


async def post_attendance_sheet(
    bot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    sanctioned_profile_ids: set[int] | None = None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
) -> None:
    """Post a new sheet to the division's attendance channel, replacing the prior one.

    Pass ``sanctioned_profile_ids`` to annotate those drivers with "(reached point limit)"
    on this posting.

    **The replacement ordering (Constitution XIV.8).** The replacement is produced and posted
    **before** the message it replaces is deleted, so that at no instant is the channel
    without a sheet. A failed post therefore leaves the division holding the sheet it had,
    rather than none at all.

    This ordering belongs to *this* function and the image path inherits it, rather than
    carrying a rule of its own: two orderings in one flow would drift, and the half left
    deleting first would be the fallback — the path reached precisely because something has
    already gone wrong. There is exactly one send site and exactly one delete site below.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT * FROM attendance_division_config WHERE division_id = ?",
            (division_id,),
        )
        row = await cursor.fetchone()

    if row is None or not row["attendance_channel_id"]:
        log.warning("post_attendance_sheet: no attendance_channel_id configured for division %s", division_id)
        return

    # A round recorded as cancelled distributes no attendance points and produces no sheet,
    # whatever the `attendance` toggle says (FR-047). The recording flow already skips a
    # cancelled round upstream; the guard stands here too so the rule holds wherever this is
    # called from, and so that no graphic is ever generated for a posting that will not happen
    # (XIV.8 — no posting, no graphic).
    async with get_connection(db_path) as db:
        round_row = await (
            await db.execute("SELECT status FROM rounds WHERE id = ?", (round_id,))
        ).fetchone()
    if (
        occasion.names_a_round
        and round_row is not None
        and round_row["status"] == "CANCELLED"
    ):
        # A season-boundary sheet is about the season and not about a round, so no round's
        # cancellation bears on it (see `models.classification_occasion`).
        log.info("post_attendance_sheet: skipping cancelled round %s", round_id)
        return

    channel_id = int(row["attendance_channel_id"])
    prior_msg_id = row["attendance_message_id"]

    channel = guild.get_channel(channel_id)
    if channel is None:
        log.warning("post_attendance_sheet: channel %s not found for division %s", channel_id, division_id)
        return

    # Build sheet content.
    async with get_connection(db_path) as db:
        driver_rows = await _sheet_rows(db, round_id, division_id)

        # The opening sheet stands before any round has been attended, so
        # `driver_round_attendance` holds nothing to read and the query above returns
        # nothing. The division's seated drivers, all on zero, are the sheet.
        if occasion is ClassificationOccasion.SEASON_OPENING:
            driver_rows = await _opening_attendance_rows(db, division_id)

        cursor2 = await db.execute(
            """
            SELECT autoreserve_threshold, autosack_threshold
            FROM attendance_config
            """,
        )
        cfg_row = await cursor2.fetchone()

    # Sort: descending total_points_after, then alphabetical by display name.
    def _sort_key(r):
        member = guild.get_member(int(r["discord_user_id"]))
        display = member.display_name if member else str(r["discord_user_id"])
        return (-(r["total_points_after"] or 0), display.lower())

    # Nobody has attended anything yet, so the points above separate nobody. The opening
    # sheet is ordered as the opening standings grid is — team, then driver — so that a
    # league's two opening sheets agree about the order of the same drivers.
    def _opening_sort_key(r):
        member = guild.get_member(int(r["discord_user_id"]))
        display = member.display_name if member else str(r["discord_user_id"])
        return ((r["team_name"] or "").casefold(), display.casefold())

    sorted_drivers = sorted(
        driver_rows,
        key=(
            _opening_sort_key
            if occasion is ClassificationOccasion.SEASON_OPENING
            else _sort_key
        ),
    )

    # The opening and final sheets say which they are; an ordinary one names no round here,
    # the sheet itself carrying that. The phrase heads the *textual* body either way — a bare
    # table of names and numbers says nothing about what it is a table of.
    heading = (
        "**Attendance Standings**"
        if occasion.names_a_round
        else f"**Attendance — {occasion.label()}**"
    )
    lines: list[str] = [heading, ""]
    for r in sorted_drivers:
        pts = r["total_points_after"] or 0
        mention = f"<@{r['discord_user_id']}>"
        if r["test_display_name"]:
            mention += f" ({r['test_display_name']})"
        suffix = " *(reached point limit)*" if sanctioned_profile_ids and r["driver_profile_id"] in sanctioned_profile_ids else ""
        lines.append(f"{mention} — {pts} attendance point{'s' if pts != 1 else ''}{suffix}")

    # Footer (FR-019).
    footer_lines: list[str] = []
    if cfg_row:
        ar = cfg_row["autoreserve_threshold"]
        as_ = cfg_row["autosack_threshold"]
        if ar:
            footer_lines.append(f"Drivers who reach {ar} points will be moved to reserve.")
        if as_:
            footer_lines.append(f"Drivers who reach {as_} points will be removed from all driving roles in all divisions.")

    if footer_lines:
        lines.append("")
        lines.extend(footer_lines)

    content = "\n".join(lines)

    # ── The graphic, where the league draws one ───────────────────────────
    # Rendered *before* anything is sent or destroyed, and entirely optional: a failure here
    # leaves ``attachment`` None and the textual sheet below is posted exactly as it always
    # was. The graphic never gates this posting, and this posting never gates a sanction
    # (XIV.7 — image output adds no precondition).
    attachment = await _sheet_attachment(
        bot,
        guild,
        db_path,
        round_id=round_id,
        division_id=division_id,
        sorted_drivers=sorted_drivers,
        cfg_row=cfg_row,
        sanctioned_profile_ids=sanctioned_profile_ids,
        occasion=occasion,
    )

    # ── Produce ───────────────────────────────────────────────────────────
    # The one send site, for the graphic and the text alike. Nothing has been destroyed at this
    # point, so a failure here leaves the previously posted sheet standing (XIV.8).
    try:
        if attachment is not None:
            # **The graphic replaces the table, not merely decorates it** (FR-043, XIV.16).
            # The message keeps the heading and gives the sheet itself to the picture. Posting
            # the full textual body beside the attachment would tell a league everything twice
            # and ping every driver from a message whose point is the image — and the graphic
            # carries names precisely so that it carries no mention.
            # A season-boundary graphic goes out bare: the phrase naming the occasion is
            # drawn on the sheet, so a heading above it would only say it twice.
            new_msg = await channel.send(
                heading if occasion.names_a_round else None, file=attachment
            )
        else:
            new_msg = await channel.send(content)
    except discord.HTTPException as exc:
        log.warning("post_attendance_sheet: failed to post sheet for division %s: %s", division_id, exc)
        # A failure of the **service** rather than of the generation: it is the textual sheet
        # that is enqueued, never the rendered image (XIV.8, FR-060). The queue is durable and
        # outlives the state that filled it, so a picture retried an hour from now would be a
        # picture of a division that has moved on; the text is composed when it is finally
        # sent. Nothing has been deleted at this point, so the previous sheet still stands.
        try:
            from services import retry_service

            await retry_service.enqueue(
                db_path,
                channel_id=channel_id,
                content=content,
                failure_reason=f"attendance sheet for division {division_id}: {exc}",
            )
        except Exception:  # noqa: BLE001 — the queue must never mask the original failure
            log.exception("post_attendance_sheet: could not enqueue the textual sheet")
        return
    finally:
        # Posted or not, the picture has done all it will ever do: the retry queue carries
        # the textual sheet and never the image, so nothing reads this file again.
        from services.image_render_service import discard_attachment

        discard_attachment(attachment)

    # The final sheet is terminal: it stands *beside* the last round's rather than replacing
    # it, so it neither claims the slot nor deletes what is in it. Claiming the slot would
    # only hand some later posting the means to delete the season's last word.
    if not occasion.takes_the_live_slot:
        return

    # Persist the replacement's id before removing what it replaces, so that a failed
    # deletion leaves the config pointing at the message that actually exists.
    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE attendance_division_config SET attendance_message_id = ? WHERE division_id = ?",
            (str(new_msg.id), division_id),
        )
        await db.commit()

    # ── Then destroy ──────────────────────────────────────────────────────
    # The one delete site, so that at most one sheet stands in the channel at any moment.
    if prior_msg_id and str(prior_msg_id) != str(new_msg.id):
        try:
            prior_msg = await channel.fetch_message(int(prior_msg_id))
            await prior_msg.delete()
        except discord.NotFound:
            pass  # already gone — skip silently
        except discord.HTTPException as exc:
            log.warning("post_attendance_sheet: failed to delete prior message: %s", exc)


async def _sheet_rows(db, round_id: int, division_id: int) -> list:
    """The drivers a division's attendance sheet after *round_id* lists, with their totals.

    Issue #220: every driver currently seated full-time in the division, and every driver who
    has held a seat in it during the season — full-time, or as a Reserve placed into a seat for
    a round — whether or not they still hold it. A driver sanctioned upon this posting, sacked
    at an earlier round, or moved to Reserve therefore stays on the sheet, which is what lets
    the sheet say they reached the limit.

    Having held a seat for a round is read from the attendance record itself: a round scores
    the full-time drivers and the Reserves placed into a seat, and nobody else, so a total
    recorded for the driver in this division is the evidence. Each driver's total is the one
    recorded at their latest scored round up to this one, and nought where none is.
    """
    from services.season_lifecycle_service import uncommitted_seat_excluded

    cursor = await db.execute(
        f"""
        WITH current_round AS (
            SELECT round_number FROM rounds WHERE id = ?
        ),
        listed AS (
            SELECT dra.driver_profile_id
            FROM driver_round_attendance dra
            JOIN rounds r ON r.id = dra.round_id
            WHERE dra.division_id = ?
              AND dra.total_points_after IS NOT NULL
              AND r.round_number <= (SELECT round_number FROM current_round)
            UNION
            SELECT ts.driver_profile_id
            FROM team_seats ts
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            WHERE ti.division_id = ?
              AND ti.is_reserve = 0
              AND ts.driver_profile_id IS NOT NULL
              AND {uncommitted_seat_excluded("ts")}
        )
        SELECT dp.id AS driver_profile_id, dp.discord_user_id, dp.test_display_name,
               COALESCE((
                   SELECT dra2.total_points_after
                   FROM driver_round_attendance dra2
                   JOIN rounds r2 ON r2.id = dra2.round_id
                   WHERE dra2.driver_profile_id = dp.id
                     AND dra2.division_id = ?
                     AND dra2.total_points_after IS NOT NULL
                     AND r2.round_number <= (SELECT round_number FROM current_round)
                   ORDER BY r2.round_number DESC
                   LIMIT 1
               ), 0) AS total_points_after
        FROM driver_profiles dp
        WHERE dp.id IN (SELECT driver_profile_id FROM listed)
        """,
        (round_id, division_id, division_id, division_id),
    )
    return await cursor.fetchall()


async def _opening_attendance_rows(db, division_id: int) -> list[dict]:
    """The division's seated drivers, all on zero, shaped like the ordinary sheet's rows.

    Posted once, when the season is approved. Selects the same drivers the sheet after a
    round does — seated, non-reserve — and carries the team name besides, which is what the
    opening order is taken on.
    """
    from services.season_lifecycle_service import uncommitted_seat_excluded

    cursor = await db.execute(
        f"""
        SELECT dp.id AS driver_profile_id, dp.discord_user_id, dp.test_display_name,
               ti.name AS team_name
        FROM team_seats ts
        JOIN team_instances ti ON ti.id = ts.team_instance_id
        JOIN driver_profiles dp ON dp.id = ts.driver_profile_id
        WHERE ti.division_id = ?
          AND ti.is_reserve = 0
          AND ts.driver_profile_id IS NOT NULL
          AND {uncommitted_seat_excluded("ts")}
        """,
        (division_id,),
    )
    return [
        {
            "driver_profile_id": row["driver_profile_id"],
            "total_points_after": 0,
            "discord_user_id": row["discord_user_id"],
            "test_display_name": row["test_display_name"],
            "team_name": row["team_name"],
        }
        for row in await cursor.fetchall()
    ]


async def _sheet_attachment(
    bot,
    guild: discord.Guild,
    db_path: str,
    *,
    round_id: int,
    division_id: int,
    sorted_drivers,
    cfg_row,
    sanctioned_profile_ids: set[int] | None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
):
    """The sheet graphic to attach, or None to post the textual sheet alone.

    Every path out of here that is not a rendered PNG returns None, and the caller posts the
    text it would have posted anyway. That is the whole of the fallback: the module being off,
    the aspect being off, the template being invalid and the render having failed are not
    distinguished, because the answer to all four is the same.

    Nothing raised here escapes. A graphic must never prevent, delay or condition the posting
    it rides on, still less the sanctions enforced beside it (XIV.7).
    """
    if bot is None or guild is None:
        return None

    try:
        from services.image_attendance_post import (
            attendance_enabled,
            render_sheet,
            report,
            report_notices,
        )
        from services.image_attendance_service import DriverRecord, resolve_drawing

        if not await attendance_enabled(bot):
            return None

        from services.image_results_post import (
            _driver_names,
            _nationalities,
            _nationality_collected,
        )

        records: list[DriverRecord] = []
        user_ids = [int(row["discord_user_id"]) for row in sorted_drivers]
        profile_ids = [int(row["driver_profile_id"]) for row in sorted_drivers]

        # The grid: every round the division holds, run or not (FR-016), and what each
        # conferred on each driver. The points are **read** from the record the module
        # persisted — already net of pardons — and never recomputed here (XIV.7).
        headings, cells = await _round_grid(db_path, division_id, profile_ids)
        profile_of = {
            int(row["discord_user_id"]): int(row["driver_profile_id"])
            for row in sorted_drivers
        }

        # The name each driver is drawn under, and their flag — both through the conventions
        # every graphic shares, called rather than restated (wip-spec § "The name of a person").
        display_names = await _driver_names(bot, guild, user_ids, division_id=division_id)
        nationalities = await _nationalities(bot, user_ids, division_id=division_id)
        collected = await _nationality_collected(db_path)

        # The team of a row is the team of the division seating the driver **at the moment of
        # generation** — the reserve team for a reserve — and never the team whose car they
        # drove in any one round (FR-020).
        team_names = await _seat_team_names(db_path, division_id, user_ids)
        team_keys = await _seat_team_keys(db_path, division_id, user_ids)

        for row in sorted_drivers:
            key = int(row["discord_user_id"])
            display_names.setdefault(key, str(key))
            records.append(
                DriverRecord(
                    key=key,
                    total=row["total_points_after"] or 0,
                    round_points=cells.get(profile_of.get(key, -1), {}),
                    sanctioned=bool(
                        sanctioned_profile_ids
                        and row["driver_profile_id"] in sanctioned_profile_ids
                    ),
                )
            )

        division_name = await _division_name(db_path, division_id)
        round_number = await _round_number(db_path, round_id)

        drawing = resolve_drawing(
            division_name=division_name,
            occasion=occasion,
            round_number=round_number,
            records=records,
            display_names=display_names,
            team_names=team_names,
            team_keys=team_keys,
            nationalities=nationalities,
            rounds=headings,
            autoreserve_threshold=(cfg_row["autoreserve_threshold"] if cfg_row else None),
            autosack_threshold=(cfg_row["autosack_threshold"] if cfg_row else None),
            # Where the league switched nationality collection off at its source, a sheet with
            # no flags at all is exactly what was configured and raises nothing — rather than
            # one notice per driver, on every render, reporting a setting back to whoever
            # chose it (XIV.4's configured absence).
            nationality_collected=collected,
        )

        render = await render_sheet(bot, drawing)
        label = (
            f"{division_name} — attendance after round {round_number}"
            if occasion.names_a_round
            else f"{division_name} — attendance, {occasion.label()}"
        )
        if render.notices:
            await report_notices(bot, label, render.notices)
        if render.problem:
            await report(bot, label, render.problem)
        if not render.draws:
            return None

        return discord.File(str(render.png), filename=Path(render.png).name)
    except Exception as exc:  # noqa: BLE001 — the sheet must post whatever happens here
        log.error(
            "post_attendance_sheet: the graphic could not be drawn for division %s: %s",
            division_id, exc,
        )
        return None


async def _round_grid(db_path: str, division_id: int, profile_ids: list[int]):
    """The sheet's round columns, and the points each round conferred on each driver.

    Returns ``(headings, cells)`` where *headings* is one :class:`RoundHeading` per round the
    division holds — **every** round, run or not (FR-016), unlike the standings grid which
    draws only those already run — and *cells* maps a driver profile id to
    ``{round ordinal: points}``.

    The ordinal is the round's place in the calendar, counted from 1, because that is what the
    template's ``round_<z>`` addresses. The *number* drawn on the heading is the round's own
    human-readable number, which need not agree with its position if rounds were removed.

    A round of the mystery format records no track and is drawn from the datum "Mystery", as
    every graphic draws one (wip-spec § "A round of the mystery format").

    A missing cell and a stored zero are the same picture and the same meaning: the round
    counted nothing against that driver. Nothing here distinguishes the six ways that happens.
    """
    from services.calendar_post_service import tracks_by_name
    from services.image_attendance_service import RoundHeading
    from services.image_calendar_service import MYSTERY_DATUM

    headings: list = []
    cells: dict[int, dict[int, int | None]] = {}
    try:
        async with get_connection(db_path) as db:
            rows = await (
                await db.execute(
                    "SELECT id, round_number, track_name, format FROM rounds "
                    "WHERE division_id = ? ORDER BY round_number",
                    (division_id,),
                )
            ).fetchall()

            # The heading's flag resolves by **country**, not by circuit (044 moved the
            # datum when the column heading became a flag rather than a track map), so the
            # registry is joined here. Without it every heading on the posted sheet drew no
            # flag at all, while `/images test attendance` drew them from the same rounds.
            #
            # Read on its own terms: a registry that cannot be read costs the sheet its
            # heading flags, and must not cost it the grid. The rounds, the drivers and
            # the points they scored are what the sheet is for.
            try:
                tracks = await tracks_by_name(db_path)
            except Exception as exc:  # noqa: BLE001
                log.error(
                    "post_attendance_sheet: the track registry could not be read, so the "
                    "round headings are drawn without their flags: %s",
                    exc,
                )
                tracks = {}

            ordinal_of: dict[int, int] = {}
            for ordinal, row in enumerate(rows, start=1):
                ordinal_of[int(row["id"])] = ordinal
                mystery = str(row["format"] or "").upper().endswith("MYSTERY")
                track_name = None if mystery else (row["track_name"] or None)
                record = tracks.get(track_name) if track_name else None
                headings.append(
                    RoundHeading(
                        ordinal=ordinal,
                        number=str(row["round_number"]),
                        track=MYSTERY_DATUM if mystery else track_name,
                        country=(
                            MYSTERY_DATUM
                            if mystery
                            else (getattr(record, "country", None) if record else None)
                        ),
                    )
                )

            if ordinal_of and profile_ids:
                round_marks = ",".join("?" * len(ordinal_of))
                driver_marks = ",".join("?" * len(profile_ids))
                points_rows = await (
                    await db.execute(
                        f"SELECT round_id, driver_profile_id, points_awarded "
                        f"FROM driver_round_attendance "
                        f"WHERE division_id = ? "
                        f"  AND round_id IN ({round_marks}) "
                        f"  AND driver_profile_id IN ({driver_marks})",
                        [division_id, *ordinal_of.keys(), *profile_ids],
                    )
                ).fetchall()
                for row in points_rows:
                    ordinal = ordinal_of.get(int(row["round_id"]))
                    if ordinal is None:
                        continue
                    cells.setdefault(int(row["driver_profile_id"]), {})[ordinal] = (
                        row["points_awarded"]
                    )
    except Exception as exc:  # noqa: BLE001 — a grid that cannot be read is drawn empty
        log.error("post_attendance_sheet: could not read the round grid: %s", exc)
        return [], {}

    return headings, cells


async def _seat_team_names(
    db_path: str, division_id: int, user_ids: list[int]
) -> dict[int, str]:
    """The team of the division seating each driver **now**, keyed by Discord user id.

    A reserve driver's team is the reserve team, which is what the sheet draws for them —
    not the team whose car they drove in some round (FR-020). This is the name drawn; the
    badge is looked up by the team's shorthand, from ``_seat_team_keys`` (#381).
    """
    return await _seat_team_field(db_path, division_id, user_ids, "name")


async def _seat_team_keys(
    db_path: str, division_id: int, user_ids: list[int]
) -> dict[int, str]:
    """The shorthand of the team seating each driver now, which its badge is found by (#381)."""
    return await _seat_team_field(db_path, division_id, user_ids, "name")


async def _seat_team_field(
    db_path: str, division_id: int, user_ids: list[int], field: str
) -> dict[int, str]:
    """One field of the team seating each driver now — ``name`` or ``full_name``."""
    if field not in ("name", "full_name"):
        raise ValueError(f"not a team name field: {field}")
    if not user_ids:
        return {}
    placeholders = ",".join("?" * len(user_ids))
    try:
        async with get_connection(db_path) as db:
            rows = await (
                await db.execute(
                    f"SELECT dp.discord_user_id AS uid, ti.{field} AS name "
                    f"FROM driver_profiles dp "
                    f"JOIN driver_season_assignments dsa "
                    f"  ON dsa.driver_profile_id = dp.id AND dsa.division_id = ? "
                    f"JOIN team_seats ts ON ts.id = dsa.team_seat_id "
                    f"JOIN team_instances ti ON ti.id = ts.team_instance_id "
                    f"WHERE dp.discord_user_id IN ({placeholders})",
                    [division_id, *[str(uid) for uid in user_ids]],
                )
            ).fetchall()
    except Exception:  # noqa: BLE001 — a nameless team is drawn empty, never fatal
        return {}
    return {int(row["uid"]): row["name"] for row in rows if row["name"]}


async def _division_name(db_path: str, division_id: int) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT name FROM divisions WHERE id = ?", (division_id,)
        )
        row = await cursor.fetchone()
    return (row["name"] if row and row["name"] else f"Division {division_id}")


async def _round_number(db_path: str, round_id: int) -> str:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT round_number FROM rounds WHERE id = ?", (round_id,)
        )
        row = await cursor.fetchone()
    return str(row["round_number"]) if row and row["round_number"] else str(round_id)


@dataclass
class SanctionOutcome:
    """What one run of the attendance sanctions did, driver by driver (#239).

    *applied* holds ``(driver, sanction)`` and *failed* holds ``(driver, sanction, reason)``,
    the driver as a mention a league can read. A run is never rolled back: every candidate
    is attempted whatever befell the one before, and what failed is recovered by running the
    sanctions again, which `/attendance sync` does. That re-run is safe because a driver
    already sacked is no longer a candidate, and one already in Reserve is passed over.
    """

    applied: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str, str]] = field(default_factory=list)
    #: Postings that failed around sanctions that did apply — the lineup, a sheet.
    posting_faults: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failed and not self.posting_faults

    def failure_lines(self) -> list[str]:
        return [
            f"{driver} — {sanction}: {reason}" for driver, sanction, reason in self.failed
        ] + list(self.posting_faults)


async def enforce_attendance_sanctions(
    bot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    season_id: int,
    head=None,
) -> SanctionOutcome:
    """Evaluate every full-time driver against autosack/autoreserve thresholds (FR-022–FR-027).

    *head* is the banner poster for the run of verdicts this belongs to, an attendance
    sanction being a verdict and headed like one (decided 2026-09-09). A penalty approval
    reaches this further down the same call that posted its penalty verdicts and passes
    **its** poster, already spent, so the sanctions fall under that banner rather than
    raising a second. Reached any other way — a recalculation after a pardon or an amendment
    — this builds one of its own, which is the case that would otherwise post sanctions
    with nothing above them.

    **No driver's failure stops the run, and none is swallowed** (decided 2026-09-18, #239).
    A sanction that raises is recorded in the returned :class:`SanctionOutcome` and the next
    driver is taken; the caller tells the manager who triggered the run. Nothing already
    applied is undone — a sack revokes roles through Discord before it writes, so it cannot
    be reversed reliably — and recovery is a second run by `/attendance sync`.
    """
    outcome = SanctionOutcome()
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT autoreserve_threshold, autosack_threshold FROM attendance_config"
        )
        cfg_row = await cursor.fetchone()

    if cfg_row is None:
        return outcome
    autoreserve_threshold: int | None = cfg_row["autoreserve_threshold"] or None
    autosack_threshold: int | None = cfg_row["autosack_threshold"] or None

    if not autoreserve_threshold and not autosack_threshold:
        return outcome  # both disabled — nothing to do (FR-027)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dra.driver_profile_id, dra.total_points_after,
                   dp.discord_user_id, dp.test_display_name, dp.current_state
            FROM driver_round_attendance dra
            JOIN driver_season_assignments dsa
                ON dsa.driver_profile_id = dra.driver_profile_id
            JOIN team_seats ts ON ts.id = dsa.team_seat_id
            JOIN team_instances ti ON ti.id = ts.team_instance_id
            JOIN driver_profiles dp ON dp.id = dra.driver_profile_id
            WHERE dra.round_id = ?
              AND dra.division_id = ?
              AND ti.division_id = ?
              AND ti.is_reserve = 0
              AND dra.total_points_after IS NOT NULL
            """,
            (round_id, division_id, division_id),
        )
        driver_rows = await cursor.fetchall()

    from services.placement_service import PlacementService
    from services import verdict_announcement_service as _vas
    if head is None:
        head = _vas.banner_for_round(bot, db_path, round_id)
    placement: PlacementService = bot.placement_service  # type: ignore[attr-defined]
    acting_id = bot.user.id
    acting_name = str(bot.user)

    # Track which profiles were actually sanctioned for the attendance sheet re-post.
    sanctioned_profile_ids: set[int] = set()
    # Other divisions an autosacked driver sat in, and who of theirs was sacked from them.
    other_divisions: dict[int, set[int]] = {}

    for row in driver_rows:
        profile_id = row["driver_profile_id"]
        discord_user_id = str(row["discord_user_id"])
        discord_user_id_int = int(row["discord_user_id"])
        test_display_name: str | None = row["test_display_name"]
        total = row["total_points_after"] or 0

        def _driver_ref(uid: int, name: str | None) -> str:
            return f"<@{uid}>" + (f" ({name})" if name else "")

        driver = _driver_ref(discord_user_id_int, test_display_name)

        # Autosack supersedes autoreserve (FR-025).
        if autosack_threshold and total >= autosack_threshold:
            if row["current_state"] == "NOT_SIGNED_UP":
                # Already signed off — the one refusal that is expected, not a failure (I1).
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    f"ATTENDANCE_AUTOSACK | No-op | driver_profile_id={profile_id} "
                    f"already NOT_SIGNED_UP (total={total})",
                )
                continue
            # Autosack takes every seat in every division (issue #220), so every division
            # the driver sits in has its sheet posted again, not only this one.
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT division_id FROM driver_season_assignments "
                    "WHERE driver_profile_id = ? AND season_id = ? AND division_id != ?",
                    (profile_id, season_id, division_id),
                )
                for other in await cursor.fetchall():
                    other_divisions.setdefault(other["division_id"], set()).add(profile_id)
            try:
                await placement.sack_driver(
                    driver_profile_id=profile_id,
                    season_id=season_id,
                    acting_user_id=acting_id,
                    acting_user_name=acting_name,
                    guild=guild,
                    discord_user_id=discord_user_id,
                )
                sanctioned_profile_ids.add(profile_id)
                outcome.applied.append((driver, "autosack"))
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    f"ATTENDANCE_AUTOSACK | {driver}"
                    f" | driver_profile_id={profile_id} | total={total} >= threshold={autosack_threshold}",
                )
                # An announcement that never went out is recorded with the run (#237). The
                # `except` below only ever caught what *raised*, and this returns quietly
                # instead — so `ATTENDANCE_SANCTIONS | Incomplete` has been promising a
                # line about unannounced sanctions that it could not produce.
                outcome.posting_faults += await _vas.post_autosanction_announcement(
                    bot=bot,
                    db_path=db_path,
                    round_id=round_id,
                    driver_discord_id=discord_user_id_int,
                    driver_display_name=test_display_name,
                    sanction_type="AUTOSACK",
                    threshold=autosack_threshold,
                    head=head,
                )
            except Exception as exc:  # noqa: BLE001 — recorded, reported, and the next driver taken
                log.exception(
                    "enforce_attendance_sanctions: autosack failed for profile %s", profile_id
                )
                outcome.failed.append((driver, "autosack", _failure_reason(
                    exc, applied=profile_id in sanctioned_profile_ids
                )))
            continue  # skip autoreserve for this driver (FR-025)

        if autoreserve_threshold and total >= autoreserve_threshold:
            # Check if already in Reserve (FR-026).
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT ti.is_reserve
                    FROM driver_season_assignments dsa
                    JOIN team_seats ts ON ts.id = dsa.team_seat_id
                    JOIN team_instances ti ON ti.id = ts.team_instance_id
                    WHERE dsa.driver_profile_id = ?
                      AND dsa.season_id = ?
                      AND dsa.division_id = ?
                    """,
                    (profile_id, season_id, division_id),
                )
                seat_row = await cursor.fetchone()

            if seat_row and seat_row["is_reserve"]:
                continue  # already in Reserve — skip (FR-026)

            # Look up Reserve team name for this division.
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT name FROM team_instances WHERE division_id = ? AND is_reserve = 1 LIMIT 1",
                    (division_id,),
                )
                reserve_row = await cursor.fetchone()

            if reserve_row is None:
                outcome.failed.append(
                    (driver, "autoreserve", "the division has no Reserve team")
                )
                continue

            reserve_team_name: str = reserve_row["name"]

            try:
                # One move rather than an unassign and an assign (issue #220): the driver
                # keeps a seat throughout, their roles are swapped once, and the lineup is
                # posted once rather than twice.
                await placement.move_driver(
                    driver_profile_id=profile_id,
                    season_id=season_id,
                    from_division_id=division_id,
                    to_division_id=division_id,
                    team_name=reserve_team_name,
                    acting_user_id=acting_id,
                    acting_user_name=acting_name,
                    guild=guild,
                    discord_user_id=discord_user_id,
                )
                sanctioned_profile_ids.add(profile_id)
                outcome.applied.append((driver, "autoreserve"))
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    f"ATTENDANCE_AUTORESERVE | {driver}"
                    f" | driver_profile_id={profile_id} | total={total} >= threshold={autoreserve_threshold}"
                    f" → moved to {reserve_team_name}",
                )
                outcome.posting_faults += await _vas.post_autosanction_announcement(
                    bot=bot,
                    db_path=db_path,
                    round_id=round_id,
                    driver_discord_id=discord_user_id_int,
                    driver_display_name=test_display_name,
                    sanction_type="AUTORESERVE",
                    threshold=autoreserve_threshold,
                    head=head,
                )
            except Exception as exc:  # noqa: BLE001 — recorded, reported, and the next driver taken
                log.exception(
                    "enforce_attendance_sanctions: autoreserve failed for profile %s", profile_id
                )
                outcome.failed.append((driver, "autoreserve", _failure_reason(
                    exc, applied=profile_id in sanctioned_profile_ids
                )))

    # Refresh lineup and re-post attendance sheet with sanctioned annotations.
    if sanctioned_profile_ids:
        try:
            await placement._refresh_lineup_post(guild, division_id)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — reported with the outcome
            log.exception("enforce_attendance_sanctions: lineup refresh failed")
            outcome.posting_faults.append(f"the lineup could not be posted again: {exc}")
        try:
            await post_attendance_sheet(
                bot, guild, db_path, round_id, division_id,
                sanctioned_profile_ids=sanctioned_profile_ids,
            )
        except Exception as exc:  # noqa: BLE001 — reported with the outcome
            log.exception("enforce_attendance_sanctions: sheet repost failed")
            outcome.posting_faults.append(f"the attendance sheet could not be posted again: {exc}")
        for other_division_id, profile_ids in other_divisions.items():
            sacked_here = profile_ids & sanctioned_profile_ids
            if not sacked_here:
                continue
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT r.id FROM rounds r "
                    "JOIN driver_round_attendance dra ON dra.round_id = r.id "
                    "WHERE r.division_id = ? AND dra.total_points_after IS NOT NULL "
                    "ORDER BY r.round_number DESC LIMIT 1",
                    (other_division_id,),
                )
                latest = await cursor.fetchone()
            if latest is None:
                continue  # the division has posted no sheet yet, so there is none to correct
            try:
                await post_attendance_sheet(
                    bot, guild, db_path, latest["id"], other_division_id,
                    sanctioned_profile_ids=sacked_here,
                )
            except Exception as exc:  # noqa: BLE001 — one division's sheet is not worth the others
                log.exception(
                    "enforce_attendance_sanctions: could not repost the sheet of division %s",
                    other_division_id,
                )
                outcome.posting_faults.append(
                    f"the attendance sheet of "
                    f"{await _division_name(db_path, other_division_id)} "
                    f"could not be posted again: {exc}"
                )

    if not outcome.complete:
        lines = "\n".join(f"  {line}" for line in outcome.failure_lines())
        try:
            await bot.output_router.post_log(  # type: ignore[attr-defined]
                f"ATTENDANCE_SANCTIONS | Incomplete\n{lines}\n"
                f"  {await sync_hint(db_path, division_id, round_id)}",
            )
        except Exception:  # noqa: BLE001 — the caller still reports the outcome it is handed
            log.exception("enforce_attendance_sanctions: could not log the incomplete run")
    return outcome


async def sync_hint(db_path: str, division_id: int, round_id: int) -> str:
    """The line telling a manager how to finish a run of sanctions that did not all apply."""
    return (
        "Repair the cause, then run `/attendance sync "
        f"division:{await _division_name(db_path, division_id)} "
        f"round:{await _round_number(db_path, round_id)}`."
    )


def _failure_reason(exc: Exception, *, applied: bool) -> str:
    """Why a driver's sanction is in the failures, told apart by whether it took effect.

    A sanction whose placement change succeeded and whose log line or announcement then
    failed is **applied**: re-running the sanctions will not announce it, because the driver
    is no longer a candidate, so the manager must be told it happened unannounced.
    """
    reason = str(exc) or type(exc).__name__
    if applied:
        return f"applied, but not announced ({reason})"
    return reason


async def recalculate_attendance_for_round(
    bot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    season_id: int,
) -> SanctionOutcome:
    """Re-run the full attendance pipeline for an amended round (FR-028–FR-031).

    Upgrade-only rule does NOT apply here — this is a deliberate correction and may
    flip attended in either direction (FR-028). Existing AttendancePardon rows are
    preserved (FR-029). total_points_after is propagated forward through any
    subsequent finalized rounds (FR-030).

    **The recompute and the whole propagation are one transaction** (#187). Every step
    below used to open and commit a connection of its own, so a failure in the middle of
    the propagation loop left a division's attendance points correct up to one round and
    stale from the next on — and nothing a league manager can run re-runs this, so there
    was no route back. Committing once means the recalculation either lands whole or does
    not land at all.

    The **posting** stays outside that transaction deliberately. It is Discord I/O, and
    holding a write transaction open across it would block every other writer for as long
    as Discord took to answer.
    """
    touched = await _recalculate_forward(
        db_path, round_id, division_id, recompute="round"
    )
    latest = touched[-1]

    # FR-031: re-post sheet and re-evaluate sanctions, against the last round recalculated
    # rather than the one named (#238). Each round's stored total is the driver's total as at
    # that round, so the division's current standing is on the last of them. Its only caller
    # names the latest finalised round already, which makes the two the same round today —
    # the rule is written out so it stays right if that ever changes.
    await post_attendance_sheet(bot, guild, db_path, latest, division_id)
    return await enforce_attendance_sanctions(
        bot, guild, db_path, latest, division_id, season_id
    )


async def _recalculate_forward(
    db_path: str, round_id: int, division_id: int, *, recompute: str
) -> list[int]:
    """Recompute *round_id* and carry the running total through every finalised round after
    it, in **one transaction** (#187). Returns the ids of the rounds it touched, in order.

    *recompute* says how much of the attended record is rebuilt from the results, and is one
    of three values. Every one of them redistributes the points of every round it touches;
    they differ only in whose attended flags are rebuilt first.

    ``"all"``
        Every round from this one on is rebuilt from its results, which is what
        `/attendance sync` asks for: it repairs a round whose recording failed, not only a
        total carried from an amended one.
    ``"round"``
        Only the named round is rebuilt; the rounds after it are redistributed (FR-030), as
        an amendment approval needs.
    ``"none"``
        None is. The caller has already recorded the attendance under the upgrade-only rule
        and wants the totals carried forward and nothing else — see
        :func:`cascade_attendance_from_round`.

    The rebuild is a **full** one (FR-028), without the upgrade-only constraint, so it can
    flip a driver from present to absent. That is why it is not reached under ``"none"``.
    """
    if recompute not in {"all", "round", "none"}:
        raise ValueError(f"unknown recompute mode {recompute!r}")
    async with get_connection(db_path) as db:
        # FR-028: full recompute without upgrade-only constraint.
        if recompute in {"all", "round"}:
            await record_attendance_from_results_full_recompute(
                db_path, round_id, division_id, db=db
            )

        # FR-029: pardons are already persisted — just recompute points using them.
        await distribute_attendance_points(db_path, round_id, division_id, db=db)

        # FR-030: propagate total_points_after forward through subsequent rounds.
        cursor = await db.execute(
            """
            SELECT id FROM rounds
            WHERE division_id = ?
              AND status IN ('AWAITING_APPEAL_VERDICTS', 'FINAL')
              AND round_number > (SELECT round_number FROM rounds WHERE id = ?)
            ORDER BY round_number ASC
            """,
            (division_id, round_id),
        )
        subsequent_rounds = [row["id"] for row in await cursor.fetchall()]

        for sub_round_id in subsequent_rounds:
            if recompute == "all":
                await record_attendance_from_results_full_recompute(
                    db_path, sub_round_id, division_id, db=db
                )
            await distribute_attendance_points(db_path, sub_round_id, division_id, db=db)

        await db.commit()
    return [round_id, *subsequent_rounds]


async def cascade_attendance_from_round(
    db_path: str, round_id: int, division_id: int
) -> list[int]:
    """Award *round_id*'s attendance points and carry the new totals through every later
    finalised round, in one transaction. Returns the ids of the rounds it touched.

    What `finalize_penalty_review` calls where it once called `distribute_attendance_points`
    alone (#238). A driver's total is the sum of their rounds, but a copy of the answer is
    stored on every round's row as ``total_points_after``, and the sheet and the sanctions
    read a copy rather than the sum. Amending round 3 of ten and correcting only round 3's
    copy left rounds 4 to 10 holding a total worked out from the old figure — which the
    season's **final** sheet then published, that sheet being drawn against the last round
    with results. Standings already cascade from this same path through
    ``standings_service.cascade_recompute_from_round``; attendance did not.

    **It deliberately does not rebuild the attended flags.** On this path the recording was
    done by ``record_attendance_from_results``, which only ever upgrades a driver to present
    and never revokes it (FR-003): a driver dropped from the results was given no chance to
    justify themselves, and the record errs in their favour.
    ``record_attendance_from_results_full_recompute`` can flip present to absent, so it is
    not reached from here — it is unused on this path by design, not by oversight.

    **The last id returned is where the division's total now stands**, each round's copy being
    that driver's total as at that round and no further. The caller posts its sheet and
    enforces its sanctions against that round, not against the amended one, exactly as
    :func:`sync_attendance` does.
    """
    return await _recalculate_forward(
        db_path, round_id, division_id, recompute="none"
    )


async def sync_attendance(
    bot,
    guild: discord.Guild,
    db_path: str,
    division_id: int,
    from_round_id: int,
    season_id: int,
) -> SanctionOutcome:
    """What `/attendance sync` does: recalculate from a round forward, then finish the job.

    Decided 2026-09-18 (#239), as the recovery for sanctions that did not all apply. Every
    finalised round from *from_round_id* on is recomputed from its results and its points
    redistributed, in one transaction; the division's sheet is then posted for the latest of
    them, the channel holding one sheet; and the sanctions are enforced against that latest
    round, where the running totals now stand.

    **Safe to run again.** A driver already sacked is no longer a candidate and one already
    in Reserve is passed over, so a second run applies only what the first did not.
    """
    touched = await _recalculate_forward(
        db_path, from_round_id, division_id, recompute="all"
    )
    latest = touched[-1]
    await post_attendance_sheet(bot, guild, db_path, latest, division_id)
    return await enforce_attendance_sanctions(
        bot, guild, db_path, latest, division_id, season_id
    )



async def recalculation_faults(
    db_path: str, season_id: int, guild, bot=None
) -> list[str]:
    """What stands between this season and recalculating its attendance (#187).

    Returns the faults as lines a league can read, and an empty list where every division's
    attendance posting could be made.

    Called by `amendment_service.approval_faults` before an approved amendment writes
    anything, because the approval recalculates attendance as part of its cascade and a
    recalculation that cannot be posted used to be swallowed under the same false success
    as the reposting.

    **The verdicts channel is asked for only where sanctions could actually fall.**
    ``enforce_attendance_sanctions`` returns immediately when both the autosack and
    autoreserve thresholds are unset, so a league using neither must not be refused for a
    channel it will never post to.
    """
    from services.image_validity_service import aspect_attaches_files
    from services.results_post_service import _bot_member, _channel_fault

    if guild is None:
        # The results half already reports an absent guild; saying it twice would have a
        # manager repairing one thing from two lines.
        return []

    bot_member = _bot_member(guild, bot)
    if bot_member is None:
        # The results half already refuses on an unresolvable bot member; saying it twice
        # would have a manager repairing one thing from two lines.
        return []

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.id, d.name,
                   adc.attendance_channel_id,
                   drc.penalty_channel_id
            FROM divisions d
            LEFT JOIN attendance_division_config adc ON adc.division_id = d.id
            LEFT JOIN division_results_config drc ON drc.division_id = d.id
            WHERE d.season_id = ? AND d.status != 'CANCELLED'
            ORDER BY d.tier, d.id
            """,
            (season_id,),
        )
        division_rows = await cursor.fetchall()

        cursor = await db.execute(
            """
            SELECT autoreserve_threshold, autosack_threshold
            FROM attendance_config
            """,
        )
        thresholds = await cursor.fetchone()

    sanctions_possible = bool(
        thresholds
        and (thresholds["autoreserve_threshold"] or thresholds["autosack_threshold"])
    )
    attendance_graphics = await aspect_attaches_files(bot, "attendance")

    faults: list[str] = []
    for row in division_rows:
        division_name = row["name"] or f"division {row['id']}"
        if row["attendance_channel_id"]:
            fault = _channel_fault(
                guild, bot_member, division_name, "attendance",
                int(row["attendance_channel_id"]),
                needs_attachment=attendance_graphics,
            )
            if fault is not None:
                faults.append(fault)
        if sanctions_possible and row["penalty_channel_id"]:
            fault = _channel_fault(
                guild, bot_member, division_name, "verdicts",
                int(row["penalty_channel_id"]),
                needs_attachment=False,
            )
            if fault is not None:
                faults.append(fault)
    return faults
