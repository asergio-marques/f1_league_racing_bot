"""AmendmentService — atomic round amendment with phase invalidation.

All changes are made inside a single DB transaction.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import discord

from db.database import get_connection
from models.round import RoundFormat
from services.season_service import SeasonImmutableError

if TYPE_CHECKING:
    from discord.ext.commands import Bot

log = logging.getLogger(__name__)


class AmendmentService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def amend_round(
        self,
        round_id: int,
        actor: discord.Member,
        field: str,
        new_value: Any,
        bot: "Bot",
    ) -> None:
        """Atomically amend *field* on *round_id*.

        Steps (inside one transaction):
        1. Load current Round.
        2. Record AuditEntry with old/new values.
        3. Update round field.
        4. Invalidate all PhaseResults and clear session phase data.
        5. Reset phase done flags.
        6. Cancel and re-schedule scheduler jobs.
        7. Post invalidation message if any prior phase was done.
        8. Immediately re-run any phase whose horizon has already passed.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                "SELECT r.*, d.division_id, s.server_id, "
                "       d.forecast_channel_id, d.mention_role_id, s.status AS season_status "
                "FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id "
                "WHERE r.id = ?",
                (round_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            raise ValueError(f"Round {round_id} not found")

        if row["season_status"] == "COMPLETED":
            raise SeasonImmutableError(
                f"Round {round_id} belongs to an archived season and cannot be amended."
            )

        old_value = row[field] if field in row.keys() else None
        server_id: int = row["server_id"]
        track_name: str = row["track_name"] or "Unknown"
        any_phase_done = bool(row["phase1_done"] or row["phase2_done"] or row["phase3_done"])

        now = datetime.now(timezone.utc)

        db_value = new_value
        if isinstance(new_value, datetime):
            db_value = new_value.isoformat()
        elif isinstance(new_value, RoundFormat):
            db_value = new_value.value

        async with get_connection(self._db_path) as db:
            # 1. Audit entry
            await db.execute(
                """
                INSERT INTO audit_entries
                    (server_id, actor_id, actor_name, division_id, change_type,
                     old_value, new_value, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    server_id,
                    actor.id,
                    str(actor),
                    row["division_id"],
                    f"round.{field}",
                    str(old_value) if old_value is not None else "",
                    str(db_value),
                    now.isoformat(),
                ),
            )

            # 2. Update round field
            allowed = {"track_name", "format", "scheduled_at"}
            if field not in allowed:
                raise ValueError(f"Field {field!r} is not amendable")
            await db.execute(
                f"UPDATE rounds SET {field} = ?, phase1_done = 0, phase2_done = 0, phase3_done = 0 WHERE id = ?",  # noqa: S608
                (db_value, round_id),
            )

            # 3. Invalidate phase results
            await db.execute(
                "UPDATE phase_results SET status = 'INVALIDATED' WHERE round_id = ?",
                (round_id,),
            )

            # 4. Clear session phase data
            await db.execute(
                "UPDATE sessions SET phase2_slot_type = NULL, phase3_slots = NULL WHERE round_id = ?",
                (round_id,),
            )

            await db.commit()

        # 5. Cancel + re-schedule
        from services.season_service import SeasonService
        season_svc = SeasonService(self._db_path)
        updated_round = await season_svc.get_round(round_id)
        if updated_round is None:
            log.error("amend_round: round not found after update")
            return

        bot.scheduler_service.cancel_round(round_id)

        scheduled_at = updated_round.scheduled_at
        if scheduled_at.tzinfo is None:
            from datetime import timezone as _tz
            scheduled_at = scheduled_at.replace(tzinfo=_tz.utc)

        from datetime import timedelta
        p1_horizon = scheduled_at - timedelta(days=5)
        p2_horizon = scheduled_at - timedelta(days=2)
        p3_horizon = scheduled_at - timedelta(hours=2)

        # For MYSTERY rounds, only register the notice job when T-5 is still in
        # the future.  If T-5 has already passed the invalidation notice already
        # informs drivers; no notice job should fire retroactively (FR-009).
        if updated_round.format != RoundFormat.MYSTERY or now < p1_horizon:
            if await bot.module_service.is_weather_enabled(server_id):
                bot.scheduler_service.schedule_round(updated_round)

        # Erase stored forecast messages for all phases (FR-011).
        # delete_forecast_message respects the test-mode guard; any skipped
        # deletions will be handled by flush_pending_deletions on toggle-off.
        if any_phase_done:
            from services.forecast_cleanup_service import delete_forecast_message
            division_id: int = row["division_id"]
            for phase_num in (1, 2, 3):
                await delete_forecast_message(round_id, division_id, phase_num, bot)

        # 6. Invalidation broadcast
        if any_phase_done:
            from utils.message_builder import invalidation_message

            class _Div:
                forecast_channel_id = row["forecast_channel_id"]

            amended_track = str(db_value) if field == "track_name" else track_name
            await bot.output_router.post_forecast(
                _Div(), invalidation_message(amended_track), server_id=server_id
            )
            await bot.output_router.post_log(
                server_id,
                f"{actor.display_name} (<@{actor.id}>) | /round amend (field) | Success\n"
                f"  round_id: {round_id}\n"
                f"  field: {field}\n"
                f"  old: {old_value}\n"
                f"  new: {db_value}",
            )

        # 7. Re-run missed phases (non-MYSTERY only)
        from services.phase1_service import run_phase1
        from services.phase2_service import run_phase2
        from services.phase3_service import run_phase3

        if updated_round.format != RoundFormat.MYSTERY:
            if now >= p1_horizon:
                await run_phase1(round_id, bot)
            if now >= p2_horizon:
                await run_phase2(round_id, bot)
            if now >= p3_horizon:
                await run_phase3(round_id, bot)


# ===========================================================================
# Mid-season points amendment workflow (T024)
# ===========================================================================

class AmendmentNotActiveError(Exception):
    """Raised when a modification store operation is attempted with amendment_active=0."""


class AmendmentModifiedError(Exception):
    """Raised when disabling amendment mode while modified_flag=1."""


async def get_amendment_state(db_path: str, season_id: int):
    """Return SeasonAmendmentState or None if no record exists."""
    from models.amendment_state import SeasonAmendmentState
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT season_id, amendment_active, modified_flag FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return SeasonAmendmentState(
        season_id=row["season_id"],
        amendment_active=bool(row["amendment_active"]),
        modified_flag=bool(row["modified_flag"]),
    )


async def enable_amendment_mode(db_path: str, season_id: int) -> None:
    """Copy season_points_entries/fl to modification store; set amendment_active=1."""
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO season_amendment_state (season_id, amendment_active, modified_flag)
            VALUES (?, 1, 0)
            """,
            (season_id,),
        )
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_modification_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_points_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            SELECT season_id, config_name, session_type, fl_points, fl_position_limit
            FROM season_points_fl WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.commit()


async def disable_amendment_mode(db_path: str, season_id: int) -> None:
    """Disable amendment mode. Raises AmendmentModifiedError if modified_flag=1."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT modified_flag FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if row and row["modified_flag"]:
        raise AmendmentModifiedError("Cannot disable — uncommitted changes exist.")

    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def revert_modification_store(db_path: str, season_id: int) -> None:
    """Reset the modification store to the current season points, clear modified_flag."""
    async with get_connection(db_path) as db:
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_modification_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_points_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            SELECT season_id, config_name, session_type, fl_points, fl_position_limit
            FROM season_points_fl WHERE season_id = ?
            """,
            (season_id,),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def _require_amendment_active(db_path: str, season_id: int) -> None:
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT amendment_active FROM season_amendment_state WHERE season_id = ?",
            (season_id,),
        )
        row = await cursor.fetchone()
    if not row or not row["amendment_active"]:
        raise AmendmentNotActiveError("Amendment mode is not active for this season.")


async def modify_session_points(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    position: int,
    points: int,
) -> None:
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO season_modification_entries
                (season_id, config_name, session_type, position, points)
            VALUES (?, ?, ?, ?, ?)
            """,
            (season_id, config_name, session_type, position, points),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def modify_fl_bonus(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    fl_points: int,
) -> None:
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT (season_id, config_name, session_type)
            DO UPDATE SET fl_points = excluded.fl_points
            """,
            (season_id, config_name, session_type, fl_points),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def modify_fl_position_limit(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    limit: int,
) -> None:
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        await db.execute(
            """
            INSERT INTO season_modification_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            VALUES (?, ?, ?, 0, ?)
            ON CONFLICT (season_id, config_name, session_type)
            DO UPDATE SET fl_position_limit = excluded.fl_position_limit
            """,
            (season_id, config_name, session_type, limit),
        )
        await db.execute(
            "UPDATE season_amendment_state SET modified_flag = 1 WHERE season_id = ?",
            (season_id,),
        )
        await db.commit()


async def get_modification_store_diff(db_path: str, season_id: int) -> str:
    """Return a human-readable diff of season_points_entries vs modification store."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name, session_type, position, points FROM season_points_entries WHERE season_id = ?",
            (season_id,),
        )
        current_rows = {
            (r["config_name"], r["session_type"], r["position"]): r["points"]
            for r in await cursor.fetchall()
        }
        cursor = await db.execute(
            "SELECT config_name, session_type, position, points FROM season_modification_entries WHERE season_id = ?",
            (season_id,),
        )
        mod_rows = {
            (r["config_name"], r["session_type"], r["position"]): r["points"]
            for r in await cursor.fetchall()
        }

    all_keys = sorted(set(current_rows) | set(mod_rows))
    lines = []
    changed = 0
    for key in all_keys:
        old_pts = current_rows.get(key)
        new_pts = mod_rows.get(key)
        if old_pts != new_pts:
            config, session, pos = key
            lines.append(
                f"{config}/{session}/P{pos}: {old_pts if old_pts is not None else '?'} → {new_pts if new_pts is not None else '(removed)'}"
            )
            changed += 1

    if not lines:
        return "No changes in modification store."
    header = f"**{changed} change{'s' if changed != 1 else ''} staged:**"
    return header + "\n" + "\n".join(lines)


async def approve_amendment(
    db_path: str,
    season_id: int,
    approved_by: int,
    bot,
) -> None:
    """Atomically overwrite season points from the modification store, then recompute all standings."""
    async with get_connection(db_path) as db:
        # Overwrite season_points_entries
        await db.execute(
            "DELETE FROM season_points_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_points_entries (season_id, config_name, session_type, position, points)
            SELECT season_id, config_name, session_type, position, points
            FROM season_modification_entries WHERE season_id = ?
            """,
            (season_id,),
        )
        # Overwrite season_points_fl
        await db.execute(
            "DELETE FROM season_points_fl WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            """
            INSERT INTO season_points_fl (season_id, config_name, session_type, fl_points, fl_position_limit)
            SELECT season_id, config_name, session_type, fl_points, fl_position_limit
            FROM season_modification_fl WHERE season_id = ?
            """,
            (season_id,),
        )
        # Clear modification store
        await db.execute(
            "DELETE FROM season_modification_entries WHERE season_id = ?", (season_id,)
        )
        await db.execute(
            "DELETE FROM season_modification_fl WHERE season_id = ?", (season_id,)
        )
        # Disable amendment mode
        await db.execute(
            "UPDATE season_amendment_state SET amendment_active = 0, modified_flag = 0 WHERE season_id = ?",
            (season_id,),
        )
        # Fetch server_id for audit log
        cursor = await db.execute(
            "SELECT server_id FROM seasons WHERE id = ?", (season_id,)
        )
        srv_row = await cursor.fetchone()
        await db.commit()

    server_id = int(srv_row["server_id"]) if srv_row else None

    if server_id:
        await bot.output_router.post_log(
            server_id,
            f"<@{approved_by}> | AMENDMENT_APPROVED | Success\n"
            f"  season_id: {season_id}"
        )

    # Cascade-recompute all divisions
    from services import standings_service, results_post_service
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id FROM divisions WHERE season_id = ?", (season_id,)
        )
        div_rows = await cursor.fetchall()

    guild = bot.get_guild(server_id) if server_id else None

    for div_row in div_rows:
        division_id = div_row["id"]
        # Get first round number for division
        async with get_connection(db_path) as db:
            cursor = await db.execute(
                """
                SELECT id FROM rounds
                WHERE division_id = ? AND status != 'CANCELLED'
                ORDER BY round_number ASC LIMIT 1
                """,
                (division_id,),
            )
            first_round_row = await cursor.fetchone()
        if first_round_row is None:
            continue
        first_round_id = first_round_row["id"]
        await standings_service.cascade_recompute_from_round(db_path, division_id, first_round_id)
        if guild:
            # Repost for each round
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    "SELECT id FROM rounds WHERE division_id = ? AND status != 'CANCELLED' ORDER BY round_number",
                    (division_id,),
                )
                round_rows = await cursor.fetchall()
            for r_row in round_rows:
                try:
                    await results_post_service.repost_round_results(db_path, r_row["id"], division_id, guild)
                except Exception:
                    log.exception(
                        "approve_amendment: failed repost for round %s / division %s",
                        r_row["id"],
                        division_id,
                    )

