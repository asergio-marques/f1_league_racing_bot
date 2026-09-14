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
        changes: list[tuple[str, Any]],
        bot: "Bot",
        now: datetime | None = None,
    ) -> None:
        """Atomically apply every amendment in *changes* to *round_id*, as one change.

        *changes* is ``[(field, new_value), ...]`` over ``track_name``, ``format`` and
        ``scheduled_at``. Every field is validated before anything is written, all of them are
        written in one statement, and the work that follows — the cancel, the re-arm, the
        invalidation notice, the re-run of overdue phases — happens exactly once however many
        fields were given.

        **Why a change set rather than a field** (issue #115). This took one field and the
        command called it once per field the manager gave, so amending a round's track *and* its
        date ran the whole amendment twice: two audit entries were right, but two invalidation
        notices, two cancels, two re-arms and two re-runs of every overdue phase were not, and a
        league amending two things at once was told twice that its forecasts had been thrown
        away. It also made the amendment rules impossible to apply, those being rules about the
        round as it will stand once *all* the changes are in — a track change is judged against
        the new date when one is given in the same breath.

        **The rules are judged by the caller, not here** — as `approval_window_service` is judged
        by `_do_approve` rather than by the season service. `amendment_rules_service` is pure and
        decides; the command refuses on its answer and calls this only for an amendment that may
        proceed. Keeping the judgement out of here is what lets a test drive an amendment the
        rules would now refuse, which is how the issue #113 gate below is still covered: its
        round is deliberately past-dated so the overdue phases re-run, and that is an amendment
        `/round amend` itself would decline.

        Steps (inside one transaction):
        1. Load current Round.
        2. Record an AuditEntry per field, with old/new values.
        3. Update every amended field at once.
        4. Invalidate all PhaseResults and clear session phase data.
        5. Reset phase done flags.
        6. Cancel and re-schedule scheduler jobs — weather only.
        7. Post invalidation message if any prior phase was done — weather only.
        8. Immediately re-run any phase whose horizon has already passed — weather only.

        **What the weather gate covers, and what it deliberately does not** (issue #113). The
        re-scheduling, the invalidation notice and the re-run of overdue phases all produce the
        weather module's output — a scheduled job, a post to the forecast channel, and a forecast
        computed, recorded and posted — so all three ask ``is_weather_enabled`` first. Only the
        re-scheduling did, which is how an amendment with weather switched off came to draw a
        forecast, post it, and mark the phase done, leaving a later ``/module enable weather`` to
        skip it for good. The audit line beside the notice is not weather output and is not gated.

        Resetting the done flags, invalidating the phase results and deleting the stored forecast
        messages all stay unconditional, and that is not an oversight. They *clear* weather work
        rather than produce any: the round's circuit or date has changed, so the forecasts behind
        it are wrong whatever the module's state, and clearing them is exactly what makes a later
        enable find the work not already done — the second half of the rule this restores. The
        message deletion likewise removes stale output; leaving wrong-circuit forecasts standing
        in the channel while the records behind them are invalidated is the worse of the two
        outcomes.
        """
        async with get_connection(self._db_path) as db:
            cursor = await db.execute(
                # ``r.*`` already carries rounds.division_id, which is the one every reader
                # below wants. Asking ``divisions`` for a division_id of its own — that table
                # has only ``id`` — made this statement raise on every amendment.
                "SELECT r.*, s.server_id, "
                "       d.forecast_channel_id, d.mention_role_id, d.tier AS division_tier, "
                "       s.status AS season_status, s.season_number "
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

        server_id: int = row["server_id"]
        track_name: str = row["track_name"] or "Unknown"
        any_phase_done = bool(row["phase1_done"] or row["phase2_done"] or row["phase3_done"])

        if now is None:
            now = datetime.now(timezone.utc)

        # Validate every field before writing any of them: a change set carrying one bad field
        # must leave the round exactly as it stood, not half-amended.
        allowed = {"track_name", "format", "scheduled_at"}
        if not changes:
            raise ValueError("An amendment must carry at least one field")
        for field, _ in changes:
            if field not in allowed:
                raise ValueError(f"Field {field!r} is not amendable")

        # (field, old value, value as the database will hold it), in the order given.
        applied: list[tuple[str, Any, Any]] = []
        for field, new_value in changes:
            db_value = new_value
            if isinstance(new_value, datetime):
                db_value = new_value.isoformat()
            elif isinstance(new_value, RoundFormat):
                db_value = new_value.value
            applied.append((field, row[field] if field in row.keys() else None, db_value))

        # Which of this round's forecasts survive the amendment, judged by the one question the
        # rules turn on: would this phase have been performed already, were the round always to
        # have stood at its new moment? Where it would, the forecast drawn for it stands and is
        # left alone. Where it would not, it is withdrawn and drawn again.
        #
        # Read the league's own horizons rather than the packaged 5/2/2, so that the answer here
        # and the windows the phases are actually judged at cannot disagree about a round.
        from models.round import Round as _Round
        from services.amendment_rules_service import judge_amendment
        from services.approval_window_service import AttendanceWindows, WeatherWindows
        from services.weather_config_service import get_weather_pipeline_config

        # Read before the verdict, because the verdict is what decides the fate of the round's
        # check-in as well as its forecasts. The config is only fetched where the module is on:
        # ``get_or_create_config`` writes a row, and a disabled module should leave no trace.
        _attendance_on = await bot.module_service.is_attendance_enabled(server_id)
        _acfg = (
            await bot.attendance_service.get_or_create_config(server_id)
            if _attendance_on
            else None
        )
        _attendance_windows = (
            AttendanceWindows(
                notice_days=_acfg.rsvp_notice_days,
                last_notice_hours=_acfg.rsvp_last_notice_hours,
                deadline_hours=_acfg.rsvp_deadline_hours,
            )
            if _acfg is not None
            else None
        )

        _wcfg = await get_weather_pipeline_config(self._db_path, server_id)
        _verdict = judge_amendment(
            _Round(
                id=round_id,
                division_id=row["division_id"],
                round_number=row["round_number"],
                format=RoundFormat(row["format"]),
                track_name=row["track_name"],
                scheduled_at=datetime.fromisoformat(row["scheduled_at"]),
                phase1_done=bool(row["phase1_done"]),
                phase2_done=bool(row["phase2_done"]),
                phase3_done=bool(row["phase3_done"]),
                status=row["status"],
            ),
            dict(changes),
            now=now,
            attendance=_attendance_windows,
            weather=WeatherWindows(
                phase_1_days=_wcfg.phase_1_days,
                phase_2_days=_wcfg.phase_2_days,
                phase_3_hours=_wcfg.phase_3_hours,
            ),
        )
        # Phase number → does the forecast drawn for it survive this amendment?
        _stands = {n: o.stands for n, o in _verdict.phases.items()}
        _withdrawn = sorted(n for n, stands in _stands.items() if not stands)

        async with get_connection(self._db_path) as db:
            # 1. One audit entry per field
            for field, old_value, db_value in applied:
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

            # 2. Update every amended field at once. The column names are the validated
            # members of ``allowed`` above and never reach here from user input.
            _assignments = ", ".join(f"{field} = ?" for field, _, _ in applied)
            # Only the withdrawn phases are marked not performed. A phase that still stands keeps
            # its flag, which is what lets the rules refuse a later track change on the strength
            # of a forecast that is genuinely still posted — before this, every amendment wiped
            # all three flags and the next one had nothing left to read.
            _flag_resets = "".join(f", phase{n}_done = 0" for n in _withdrawn)
            await db.execute(
                f"UPDATE rounds SET {_assignments}{_flag_resets} WHERE id = ?",  # noqa: S608
                (*[db_value for _, _, db_value in applied], round_id),
            )

            # 3. Invalidate the results of the withdrawn phases alone
            if _withdrawn:
                _marks = ", ".join("?" for _ in _withdrawn)
                await db.execute(
                    "UPDATE phase_results SET status = 'INVALIDATED' "  # noqa: S608
                    f"WHERE round_id = ? AND phase_number IN ({_marks})",
                    (round_id, *_withdrawn),
                )

            # 4. Clear the session data each withdrawn phase recorded, and no more: Phase 2
            # chose the slot type and Phase 3 the slots, so a Phase 2 that stands keeps its
            # choice even where Phase 3 is drawn again.
            if 2 in _withdrawn:
                await db.execute(
                    "UPDATE sessions SET phase2_slot_type = NULL WHERE round_id = ?",
                    (round_id,),
                )
            if 3 in _withdrawn:
                await db.execute(
                    "UPDATE sessions SET phase3_slots = NULL WHERE round_id = ?",
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

        # The horizons the verdict measured, which are the league's own rather than the packaged
        # 5 / 2 / 2, and are computed from the round's new moment — the same moment
        # ``updated_round`` now carries.
        p1_horizon = _verdict.phases[1].fire_at

        # For MYSTERY rounds, only re-schedule when T-5 is still in the future.
        # If T-5 has already passed, the invalidation notice already informed
        # drivers; we must not fire the mystery notice retroactively (FR-009).
        # For non-mystery rounds we always re-schedule; overdue phases are
        # immediately re-run below.
        _weather_on = await bot.module_service.is_weather_enabled(server_id)
        if _weather_on:
            from models.round import RoundFormat as _RoundFormat
            if updated_round.format != _RoundFormat.MYSTERY or now < p1_horizon:
                # The league's own horizons, the same ones the verdict above was measured
                # against. Left to its defaults `schedule_round` arms at the packaged 5 / 2 / 2,
                # so a league that had configured its own quietly got them back every time a
                # round was amended (issue #110) — and now that the rules read the configured
                # horizons, arming at the packaged ones would have the amendment judge a phase
                # at one moment and schedule it at another.
                bot.scheduler_service.schedule_round(
                    updated_round,
                    season_number=row["season_number"],
                    division_tier=row["division_tier"],
                    phase_1_days=_wcfg.phase_1_days,
                    phase_2_days=_wcfg.phase_2_days,
                    phase_3_hours=_wcfg.phase_3_hours,
                )

        # Arm the round's result submission again where the weather module did not (issue #133).
        #
        # ``schedule_round`` creates the weather jobs and the results job together, so with
        # weather switched off it is never called and the round lost the results job that
        # ``cancel_round`` had just taken. That job is not only the results module's:
        # ``run_result_submission_job`` is the round's one clock-driven status transition and
        # runs for every round whatever the modules, closing it as FINAL where results are off.
        # Without it the round never leaves NOT_RUN, its division never finishes and its season
        # can never be completed — which is why it is armed here whatever the results module
        # says, exactly as ``cancel_all_weather_for_server`` refuses to cancel it.
        if not _weather_on:
            bot.scheduler_service.schedule_result_submission_jobs(
                [updated_round],
                division_meta={
                    updated_round.division_id: (row["season_number"], row["division_tier"])
                },
            )

        # Arm the round's check-in again (issue #120).
        #
        # ``cancel_round`` above takes all eight of the round's jobs, the three the check-in runs
        # on included, and until now only the weather ones were put back. Nothing else arms them:
        # ``schedule_attendance_round`` is called from ``/season approve`` and nowhere else, and
        # that cannot be run again on an active season. So an amended round asked nobody whether
        # they were racing, opened no attendance records, distributed no reserves and charged
        # nobody — and was recorded afterwards as perfect attendance for the whole division.
        #
        # ``schedule_attendance_round`` arms only the windows still ahead, which is the same rule
        # the verdict above holds to: a window that would have run under the round's new moment
        # stands, and is not honoured retroactively.
        if _attendance_on and _acfg is not None:
            bot.scheduler_service.schedule_attendance_round(
                updated_round,
                season_number=row["season_number"],
                division_tier=row["division_tier"],
                notice_days=_acfg.rsvp_notice_days,
                last_notice_hours=_acfg.rsvp_last_notice_hours,
                deadline_hours=_acfg.rsvp_deadline_hours,
            )

            # What becomes of a call that has already gone out. The call names the circuit, the
            # sessions and the moment, and the amendment can have changed all three under it.
            #
            #   * Its window would have passed under the round's new moment, and the check-in is
            #     still open — the call is posted again, carrying every answer already given, so
            #     the division sees what actually changed and may still answer.
            #   * Its window is ahead again, the round having moved far enough — the standing
            #     call is taken down and the job armed above posts a fresh one at the new time.
            #   * The deadline has passed under the new moment too — nothing is posted and
            #     nothing is taken down. The check-in is closed, the reserves are distributed
            #     against it, and reopening it would unsettle a grid already told who is racing.
            from services.rsvp_service import repost_rsvp_call, withdraw_rsvp_call

            _division_id = row["division_id"]
            if not _verdict.check_in_stays_closed:
                if _verdict.check_in["call"].stands:
                    await repost_rsvp_call(round_id, _division_id, bot)
                else:
                    await withdraw_rsvp_call(round_id, _division_id, bot)

        # Erase the stored forecast message of each withdrawn phase, and only those (FR-011).
        # A phase that still stands keeps its message, which is what leaves the division holding
        # the latest forecast that survives the amendment rather than an empty channel.
        if any_phase_done and _withdrawn:
            from services.forecast_cleanup_service import delete_forecast_message
            division_id: int = row["division_id"]
            for phase_num in _withdrawn:
                await delete_forecast_message(round_id, division_id, phase_num, bot)

        # 6. Invalidation broadcast — only where a forecast was actually withdrawn.
        #
        # Gated on what the amendment took away rather than on any phase having been performed
        # at all. A phase that still stands has not been withdrawn and there is nothing to
        # announce: telling a division its forecasts no longer stand while the one in its
        # channel does is worse than saying nothing, and it is the message deletion above that
        # this has to agree with.
        _forecast_withdrawn = any_phase_done and bool(
            [n for n in _withdrawn if row[f"phase{n}_done"]]
        )
        if _forecast_withdrawn:
            from utils.message_builder import invalidation_message

            class _Div:
                forecast_channel_id = row["forecast_channel_id"]

            amended_track = next(
                (str(db_value) for f, _, db_value in applied if f == "track_name"),
                track_name,
            )
            # The notice goes to the forecast channel and is about forecasts, so it is the
            # weather module's output and waits on the module. Reachable with weather off —
            # phases run, module switched off, round amended — which is issue #113 again by a
            # second route.
            if _weather_on:
                await bot.output_router.post_forecast(
                    _Div(), invalidation_message(amended_track), server_id=server_id
                )

        # The audit line is not weather output and does not wait on a forecast having been
        # withdrawn: an amendment is worth recording whether or not it cost the round anything.
        _changed = "\n".join(
            f"  {f}: {old_value} → {db_value}" for f, old_value, db_value in applied
        )
        await bot.output_router.post_log(
            server_id,
            f"{actor.display_name} (<@{actor.id}>) | /round amend (field) | Success\n"
            f"  round: {updated_round.round_number}\n"
            f"{_changed}",
        )

        # 7. Re-run missed phases (non-MYSTERY only, and only with weather on)
        from services.phase1_service import run_phase1
        from services.phase2_service import run_phase2
        from services.phase3_service import run_phase3

        # A phase is run here only where its horizon has passed under the round's new moment
        # *and* it was never performed — the round having been brought forward past a horizon it
        # had not yet reached. A phase that stands was already performed and must not be drawn
        # again; a phase that was withdrawn is now ahead of us and is armed, not run.
        if _weather_on and updated_round.format != RoundFormat.MYSTERY:
            _runners = {1: run_phase1, 2: run_phase2, 3: run_phase3}
            for _number in (1, 2, 3):
                if _stands.get(_number) and not row[f"phase{_number}_done"]:
                    await _runners[_number](round_id, bot)


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
                    await results_post_service.repost_round_results(
                        db_path, r_row["id"], division_id, guild, bot=bot
                    )
                except Exception:
                    log.exception(
                        "approve_amendment: failed repost for round %s / division %s",
                        r_row["id"],
                        division_id,
                    )

        # T018: Attendance recalculation (033-attendance-tracking).
        if guild and server_id and await bot.module_service.is_attendance_enabled(server_id):  # type: ignore[attr-defined]
            from services.attendance_service import recalculate_attendance_for_round

            # Find the most recently finalized round per division to recalculate.
            async with get_connection(db_path) as db:
                cursor = await db.execute(
                    """
                    SELECT id FROM rounds
                    WHERE division_id = ?
                      AND status IN ('AWAITING_APPEAL_VERDICTS', 'FINAL')
                    ORDER BY round_number DESC LIMIT 1
                    """,
                    (division_id,),
                )
                latest_row = await cursor.fetchone()

            if latest_row is not None:
                try:
                    await recalculate_attendance_for_round(
                        bot, guild, db_path,
                        latest_row["id"], division_id,
                        server_id, season_id,
                    )
                except Exception:
                    log.exception(
                        "approve_amendment: recalculate_attendance_for_round failed for division %s",
                        division_id,
                    )
