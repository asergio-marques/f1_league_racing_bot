"""A round's attendance and the points it confers.

`record_attendance_from_results_on` and `record_attendance_from_results_full_recompute` mark who
attended from the round's results, and `distribute_attendance_points` computes each full-time
driver's `points_awarded` and `total_points_after` and saves them. `_shared_or_own` lends a
function the connection it was handed, or opens one where it was handed none. Carrying the running
total through the rounds after is `attendance_recompute`'s.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from leaguebot.core.db.database import get_connection


log = logging.getLogger(__name__)


@asynccontextmanager
async def _shared_or_own(db_path: str, db):
    """Yield ``(connection, owned)`` — *db* where a caller supplied one, else a new one.

    ``owned`` says whether the callee should commit: a function handed a connection is one
    step of somebody else's transaction and must not commit it, or the atomicity the caller
    opened it for is lost a step at a time.

    **Why the recalculation needs this** (#187). ``_recalculate_forward`` recomputes a round's
    attendance and then propagates the running total through every finalised round after it,
    one call apiece. Each call used to open and commit its own
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


async def record_attendance_from_results_on(
    db: aiosqlite.Connection,
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

    Written on *db*, the connection a change's save hands it, and commits nothing: the round's
    attendance lands with the penalties and points or not at all (#439).
    """
    # Guard: skip if round is cancelled.
    cursor = await db.execute(
        "SELECT status FROM rounds WHERE id = ?",
        (round_id,),
    )
    round_row = await cursor.fetchone()
    if round_row is None or round_row["status"] == "CANCELLED":
        log.info("record_attendance_from_results_on: skipping cancelled round %s", round_id)
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


async def record_attendance_from_results_full_recompute(
    db_path: str,
    round_id: int,
    division_id: int,
    *,
    db,
) -> None:
    """Recompute attended flags without the upgrade-only constraint (FR-028/amendment).

    Used by ``_recalculate_forward`` so that a deliberate result
    correction can flip attended in either direction. Skipped for cancelled rounds.

    *db* is the caller's transaction, which it commits: this opens and commits no connection of
    its own (#439). *db_path* names the league database *db* is a connection to.
    """
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
    :func:`_recalculate_forward` and :func:`sync_attendance`.

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
            prior_total = prior_row["prior_total"] if prior_row else 0
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


async def attendance_row_of(
    db, round_id: int, division_id: int, profile_id: int
) -> dict[str, Any] | None:
    """The driver's attendance row of the round: its ``id`` and ``rsvp_status``; None where the
    driver has none. What the review's pardon form stages a pardon against
    (`AttendanceAfterReview.attendance_row_of`). Reads only."""
    cursor = await db.execute(
        "SELECT id, rsvp_status FROM driver_round_attendance "
        "WHERE round_id = ? AND division_id = ? AND driver_profile_id = ?",
        (round_id, division_id, profile_id),
    )
    row = await cursor.fetchone()
    return None if row is None else {"id": row["id"], "rsvp_status": row["rsvp_status"]}


async def latest_scored_round(db, division_id: int) -> int | None:
    """The division's latest round with a recorded attendance total, which is where its current
    sheet stands; None where it has none (`AttendanceAfterReview.latest_scored_round`). Reads
    only. The division's rounds, newest first, are core's to give (`division_rounds_on`), and
    the first of them carrying a total is attendance's to pick."""
    from leaguebot.core.services.seasons.season_service import division_rounds_on

    for round_id in await division_rounds_on(db, division_id):
        cursor = await db.execute(
            "SELECT 1 FROM driver_round_attendance "
            "WHERE round_id = ? AND total_points_after IS NOT NULL LIMIT 1",
            (round_id,),
        )
        if await cursor.fetchone() is not None:
            return round_id
    return None
