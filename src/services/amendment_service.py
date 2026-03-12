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
                "       d.forecast_channel_id, d.mention_role_id "
                "FROM rounds r "
                "JOIN divisions d ON d.id = r.division_id "
                "JOIN seasons s ON s.id = d.season_id "
                "WHERE r.id = ?",
                (round_id,),
            )
            row = await cursor.fetchone()

        if row is None:
            raise ValueError(f"Round {round_id} not found")

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
                f"🔧 **Amendment** by {actor} — Round #{round_id}: `{field}` changed "
                f"from `{old_value}` to `{db_value}`",
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
