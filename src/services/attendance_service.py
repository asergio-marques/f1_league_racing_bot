"""AttendanceService — read/write attendance module configuration."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
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
            no_rsvp_absent_penalty=row["no_rsvp_absent_penalty"],
            rsvp_absent_penalty=row["rsvp_absent_penalty"],
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
            attendance_message_id=row["attendance_message_id"],
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

    async def update_no_rsvp_absent_penalty(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET no_rsvp_absent_penalty = ? WHERE server_id = ?",
                (value, server_id),
            )
            await db.commit()

    async def update_rsvp_absent_penalty(self, server_id: int, value: int) -> None:
        async with get_connection(self._db_path) as db:
            await db.execute(
                "UPDATE attendance_config SET rsvp_absent_penalty = ? WHERE server_id = ?",
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
    ) -> None:
        """Update rsvp_status and manage accepted_at per FR-022.

        - Transitioning TO 'ACCEPTED': set accepted_at to current UTC time.
        - Re-accepting after a non-ACCEPTED status: reset accepted_at to current UTC time.
        - Transitioning AWAY from 'ACCEPTED': set accepted_at to NULL.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        if status == "ACCEPTED":
            async with get_connection(self._db_path) as db:
                await db.execute(
                    """
                    UPDATE driver_round_attendance
                       SET rsvp_status = ?,
                           accepted_at = ?
                     WHERE round_id = ?
                       AND division_id = ?
                       AND driver_profile_id = ?
                    """,
                    (status, now_iso, round_id, division_id, driver_profile_id),
                )
                await db.commit()
        else:
            async with get_connection(self._db_path) as db:
                await db.execute(
                    """
                    UPDATE driver_round_attendance
                       SET rsvp_status = ?,
                           accepted_at = NULL
                     WHERE round_id = ?
                       AND division_id = ?
                       AND driver_profile_id = ?
                    """,
                    (status, round_id, division_id, driver_profile_id),
                )
                await db.commit()

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
    """
    async with get_connection(db_path) as db:
        # Set of driver_profile_ids who have any result row for this round.
        # Outcome modifier is irrelevant — any row counts as attended (DSQ/DNS included).
        cursor = await db.execute(
            """
            SELECT DISTINCT dp.driver_profile_id
            FROM driver_session_results dp
            JOIN session_results sr ON sr.id = dp.session_result_id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND dp.is_superseded = 0
            """,
            (round_id,),
        )
        attended_rows = await cursor.fetchall()
        attended_ids: set[int] = {r["driver_profile_id"] for r in attended_rows}

        # Full-time DRA rows for this round: exclude drivers in the Reserve team (FR-002).
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
              AND ti.is_reserve = 0
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
) -> None:
    """Recompute attended flags without the upgrade-only constraint (FR-028/amendment).

    Used exclusively by recalculate_attendance_for_round so that a deliberate result
    correction can flip attended in either direction.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT DISTINCT dp.driver_profile_id
            FROM driver_session_results dp
            JOIN session_results sr ON sr.id = dp.session_result_id
            WHERE sr.round_id = ? AND sr.status = 'ACTIVE' AND dp.is_superseded = 0
            """,
            (round_id,),
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
              AND ti.is_reserve = 0
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
        await db.commit()


async def distribute_attendance_points(
    db_path: str,
    round_id: int,
    division_id: int,
) -> None:
    """Compute and persist points_awarded and total_points_after for every full-time
    driver in the division for this round (FR-012–FR-015).
    """
    async with get_connection(db_path) as db:
        # Load penalty config for this division's server.
        cursor = await db.execute(
            """
            SELECT ac.no_rsvp_penalty, ac.no_rsvp_absent_penalty, ac.rsvp_absent_penalty
            FROM attendance_config ac
            JOIN seasons s ON s.server_id = ac.server_id
            JOIN divisions d ON d.season_id = s.id
            WHERE d.id = ?
            """,
            (division_id,),
        )
        cfg_row = await cursor.fetchone()
        if cfg_row is None:
            log.warning("distribute_attendance_points: no attendance_config for division %s", division_id)
            return

        no_rsvp_pen: int = cfg_row["no_rsvp_penalty"] or 0
        no_rsvp_absent_pen: int = cfg_row["no_rsvp_absent_penalty"] or 0
        rsvp_absent_pen: int = cfg_row["rsvp_absent_penalty"] or 0

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
                base = no_rsvp_pen + (no_rsvp_absent_pen if not attended else 0)
            elif not attended:
                # ACCEPTED/TENTATIVE/DECLINED + no-show = rsvp_absent_penalty
                base = rsvp_absent_pen

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
            if "NO_RSVP_ABSENT" in pardons:
                net -= no_rsvp_absent_pen
            if "RSVP_ABSENT" in pardons:
                net -= rsvp_absent_pen
            net = max(0, net)  # never negative

            # Compute cumulative total across all finalized rounds in division.
            c3 = await db.execute(
                """
                SELECT COALESCE(SUM(dra2.points_awarded), 0) AS prior_total
                FROM driver_round_attendance dra2
                JOIN rounds r ON r.id = dra2.round_id
                WHERE dra2.driver_profile_id = ?
                  AND dra2.division_id = ?
                  AND r.result_status IN ('POST_RACE_PENALTY', 'FINAL')
                  AND dra2.round_id != ?
                  AND dra2.points_awarded IS NOT NULL
                """,
                (row["driver_profile_id"], division_id, round_id),
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
        await db.commit()


async def post_attendance_sheet(
    bot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    sanctioned_profile_ids: set[int] | None = None,
) -> None:
    """Delete prior sheet and post a new one to the division's attendance channel (FR-016–FR-021).

    Pass ``sanctioned_profile_ids`` to annotate those drivers with "(reached point limit)"
    on this posting.
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

    channel_id = int(row["attendance_channel_id"])
    prior_msg_id = row["attendance_message_id"]

    channel = guild.get_channel(channel_id)
    if channel is None:
        log.warning("post_attendance_sheet: channel %s not found for division %s", channel_id, division_id)
        return

    # Delete prior sheet message (FR-020).
    if prior_msg_id:
        try:
            prior_msg = await channel.fetch_message(int(prior_msg_id))
            await prior_msg.delete()
        except discord.NotFound:
            pass  # already gone — skip silently (FR-020)
        except discord.HTTPException as exc:
            log.warning("post_attendance_sheet: failed to delete prior message: %s", exc)

    # Build sheet content.
    async with get_connection(db_path) as db:
        # Full-time drivers sorted by total_points_after DESC, then display name.
        cursor = await db.execute(
            """
            SELECT dra.driver_profile_id, dra.total_points_after,
                   dp.discord_user_id, dp.test_display_name
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

        cursor2 = await db.execute(
            """
            SELECT ac.autoreserve_threshold, ac.autosack_threshold
            FROM attendance_config ac
            JOIN seasons s ON s.server_id = ac.server_id
            JOIN divisions d ON d.season_id = s.id
            WHERE d.id = ?
            """,
            (division_id,),
        )
        cfg_row = await cursor2.fetchone()

    # Sort: descending total_points_after, then alphabetical by display name.
    def _sort_key(r):
        member = guild.get_member(int(r["discord_user_id"]))
        display = member.display_name if member else str(r["discord_user_id"])
        return (-(r["total_points_after"] or 0), display.lower())

    sorted_drivers = sorted(driver_rows, key=_sort_key)

    lines: list[str] = ["**Attendance Standings**", ""]
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

    # Post new sheet and persist message ID (FR-021).
    try:
        new_msg = await channel.send(content)
    except discord.HTTPException as exc:
        log.warning("post_attendance_sheet: failed to post sheet for division %s: %s", division_id, exc)
        return

    async with get_connection(db_path) as db:
        await db.execute(
            "UPDATE attendance_division_config SET attendance_message_id = ? WHERE division_id = ?",
            (str(new_msg.id), division_id),
        )
        await db.commit()


async def enforce_attendance_sanctions(
    bot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    server_id: int,
    season_id: int,
) -> None:
    """Evaluate every full-time driver against autosack/autoreserve thresholds (FR-022–FR-027)."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT autoreserve_threshold, autosack_threshold FROM attendance_config WHERE server_id = ?",
            (server_id,),
        )
        cfg_row = await cursor.fetchone()

    if cfg_row is None:
        return
    autoreserve_threshold: int | None = cfg_row["autoreserve_threshold"] or None
    autosack_threshold: int | None = cfg_row["autosack_threshold"] or None

    if not autoreserve_threshold and not autosack_threshold:
        return  # both disabled — nothing to do (FR-027)

    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT dra.driver_profile_id, dra.total_points_after,
                   dp.discord_user_id, dp.test_display_name
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
    placement: PlacementService = bot.placement_service  # type: ignore[attr-defined]
    acting_id = bot.user.id
    acting_name = str(bot.user)

    # Track which profiles were actually sanctioned for the attendance sheet re-post.
    sanctioned_profile_ids: set[int] = set()

    for row in driver_rows:
        profile_id = row["driver_profile_id"]
        discord_user_id = str(row["discord_user_id"])
        discord_user_id_int = int(row["discord_user_id"])
        test_display_name: str | None = row["test_display_name"]
        total = row["total_points_after"] or 0

        def _driver_ref(uid: int, name: str | None) -> str:
            return f"<@{uid}>" + (f" ({name})" if name else "")

        # Autosack supersedes autoreserve (FR-025).
        if autosack_threshold and total >= autosack_threshold:
            try:
                await placement.sack_driver(
                    server_id=server_id,
                    driver_profile_id=profile_id,
                    season_id=season_id,
                    acting_user_id=acting_id,
                    acting_user_name=acting_name,
                    guild=guild,
                    discord_user_id=discord_user_id,
                )
                sanctioned_profile_ids.add(profile_id)
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    server_id,
                    f"ATTENDANCE_AUTOSACK | {_driver_ref(discord_user_id_int, test_display_name)}"
                    f" | driver_profile_id={profile_id} | total={total} >= threshold={autosack_threshold}",
                )
                await _vas.post_autosanction_announcement(
                    bot=bot,
                    db_path=db_path,
                    round_id=round_id,
                    driver_discord_id=discord_user_id_int,
                    driver_display_name=test_display_name,
                    sanction_type="AUTOSACK",
                    threshold=autosack_threshold,
                )
            except ValueError:
                # Driver already NOT_SIGNED_UP — emit no-op log and continue (I1 edge case).
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    server_id,
                    f"ATTENDANCE_AUTOSACK | No-op | driver_profile_id={profile_id} "
                    f"already NOT_SIGNED_UP (total={total})",
                )
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
                log.warning("enforce_attendance_sanctions: no Reserve team found for division %s", division_id)
                continue

            reserve_team_name: str = reserve_row["name"]

            try:
                await placement.unassign_driver(
                    server_id=server_id,
                    driver_profile_id=profile_id,
                    division_id=division_id,
                    season_id=season_id,
                    acting_user_id=acting_id,
                    acting_user_name=acting_name,
                    guild=guild,
                    discord_user_id=discord_user_id,
                )
                await placement.assign_driver(
                    server_id=server_id,
                    driver_profile_id=profile_id,
                    division_id=division_id,
                    team_name=reserve_team_name,
                    season_id=season_id,
                    acting_user_id=acting_id,
                    acting_user_name=acting_name,
                    guild=guild,
                    discord_user_id=discord_user_id,
                )
                sanctioned_profile_ids.add(profile_id)
                await bot.output_router.post_log(  # type: ignore[attr-defined]
                    server_id,
                    f"ATTENDANCE_AUTORESERVE | {_driver_ref(discord_user_id_int, test_display_name)}"
                    f" | driver_profile_id={profile_id} | total={total} >= threshold={autoreserve_threshold}"
                    f" → moved to {reserve_team_name}",
                )
                await _vas.post_autosanction_announcement(
                    bot=bot,
                    db_path=db_path,
                    round_id=round_id,
                    driver_discord_id=discord_user_id_int,
                    driver_display_name=test_display_name,
                    sanction_type="AUTORESERVE",
                    threshold=autoreserve_threshold,
                )
            except (ValueError, Exception) as exc:
                log.warning(
                    "enforce_attendance_sanctions: autoreserve failed for profile %s: %s",
                    profile_id, exc,
                )

    # Refresh lineup and re-post attendance sheet with sanctioned annotations.
    if sanctioned_profile_ids:
        await placement._refresh_lineup_post(guild, division_id)  # type: ignore[attr-defined]
        await post_attendance_sheet(
            bot, guild, db_path, round_id, division_id,
            sanctioned_profile_ids=sanctioned_profile_ids,
        )


async def recalculate_attendance_for_round(
    bot,
    guild: discord.Guild,
    db_path: str,
    round_id: int,
    division_id: int,
    server_id: int,
    season_id: int,
) -> None:
    """Re-run the full attendance pipeline for an amended round (FR-028–FR-031).

    Upgrade-only rule does NOT apply here — this is a deliberate correction and may
    flip attended in either direction (FR-028). Existing AttendancePardon rows are
    preserved (FR-029). total_points_after is propagated forward through any
    subsequent finalized rounds (FR-030).
    """
    # FR-028: full recompute without upgrade-only constraint.
    await record_attendance_from_results_full_recompute(db_path, round_id, division_id)

    # FR-029: pardons are already persisted — just recompute points using them.
    await distribute_attendance_points(db_path, round_id, division_id)

    # FR-030: propagate total_points_after forward through subsequent rounds.
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            """
            SELECT id FROM rounds
            WHERE division_id = ?
              AND result_status IN ('POST_RACE_PENALTY', 'FINAL')
              AND round_number > (SELECT round_number FROM rounds WHERE id = ?)
            ORDER BY round_number ASC
            """,
            (division_id, round_id),
        )
        subsequent_rounds = await cursor.fetchall()

    for sub_row in subsequent_rounds:
        await distribute_attendance_points(db_path, sub_row["id"], division_id)

    # FR-031: re-post sheet and re-evaluate sanctions.
    await post_attendance_sheet(bot, guild, db_path, round_id, division_id)
    await enforce_attendance_sanctions(bot, guild, db_path, round_id, division_id, server_id, season_id)

