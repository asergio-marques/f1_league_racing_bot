"""AmendmentService — atomic round amendment with phase invalidation.

All changes are made inside a single DB transaction.
"""

from __future__ import annotations

import contextlib
import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from itertools import groupby
from typing import Any

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.round import RoundFormat
from leaguebot.core.services.audit_service import record_change_on
from leaguebot.core.services.season_service import SeasonImmutableError
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.utils.points_ordering import ordering_message, ordering_violations
from leaguebot.core.utils.league_server import league_guild

log = logging.getLogger(__name__)


class AmendmentService:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        #: The divisions with a `/round amend` being applied, each with how many: from the moment
        #: its confirmation's checks pass until its division's rounds are renumbered. Held in
        #: memory only, as the amendment is, so that a restart clears it.
        self._applying: dict[int, int] = {}

    @contextlib.contextmanager
    def applying(self, division_id: int) -> Iterator[None]:
        """Mark *division_id* as having a round being amended for as long as the block runs, a
        failure included.

        A round's or a division's cancellation is refused while the mark stands, when it is
        asked and again when it runs (`cancellation_changes`, owner 2026-10-08): an amended
        time renumbers the division's rounds, and a cancellation that read the rounds before
        the renumbering would announce a round by a number it no longer bears.
        """
        self._applying[division_id] = self._applying.get(division_id, 0) + 1
        try:
            yield
        finally:
            left = self._applying.get(division_id, 0) - 1
            if left > 0:
                self._applying[division_id] = left
            else:
                self._applying.pop(division_id, None)

    def is_applying(self, division_id: int) -> bool:
        """Whether a `/round amend` of a round of *division_id* is being applied."""
        return division_id in self._applying

    def forget_applying(self) -> None:
        """Drop every mark, as `/bot pack` and `/bot factory-reset` let go of the league."""
        self._applying.clear()

    async def amend_round(
        self,
        round_id: int,
        actor: discord.User | discord.Member,
        changes: list[tuple[str, Any]],
        bot: "LeagueBot",
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

        **The amendment's one success line is written here**, straight after the save and before
        the steps that can still fail, so the record holds what changed however the rest ends.
        The command does not write a second.

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
                "SELECT r.*, "
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
        from leaguebot.core.models.round import Round as _Round
        from leaguebot.core.services.amendment_rules_service import judge_amendment
        from leaguebot.core.services.approval_window_service import AttendanceWindows, WeatherWindows
        from leaguebot.weather.services.weather_config_service import get_weather_pipeline_config

        # Read before the verdict, because the verdict is what decides the fate of the round's
        # check-in as well as its forecasts. The config is only fetched where the module is on:
        # ``get_or_create_config`` writes a row, and a disabled module should leave no trace.
        _attendance_on = await bot.module_service.is_attendance_enabled()
        _acfg = (
            await bot.attendance_service.get_or_create_config()
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

        _wcfg = await get_weather_pipeline_config(self._db_path)
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
                        (actor_id, actor_name, division_id, change_type,
                         old_value, new_value, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
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

        # The one success line of the amendment, written as soon as it is saved so that it
        # stands whatever fails in the steps after (the core specification's "The record of
        # what changed"). Each field is named as `/round amend` names its parameter, and a
        # field given at the value it already held is no change and is not listed.
        _parameter_of = {"track_name": "track"}
        _changed = "".join(
            f"\n  {_parameter_of.get(f, f)}: {old_value if old_value is not None else 'none'}"
            f" \u2192 {db_value}"
            for f, old_value, db_value in applied
            if old_value != db_value
        )
        await bot.output_router.post_log(
            f"{actor.display_name} (<@{actor.id}>) | /round amend | Success\n"
            f"  round {row['round_number']} (round_id: {round_id})"
            f"{_changed}",
        )

        # 5. Cancel + re-schedule
        from leaguebot.core.services.season_service import SeasonService
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
        _weather_on = await bot.module_service.is_weather_enabled()
        if _weather_on:
            from leaguebot.core.models.round import RoundFormat as _RoundFormat
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
        # says, exactly as ``cancel_all_weather`` refuses to cancel it.
        if not _weather_on:
            bot.scheduler_service.schedule_result_submission_jobs(
                [updated_round],
                division_meta={
                    updated_round.division_id: (row["season_number"], row["division_tier"])
                },
            )

        # Arm the round's check-in again (issue #120).
        #
        # ``cancel_round`` above takes all nine of the round's jobs, the four the check-in runs
        # on included, and until now only the weather ones were put back. Nothing else arms them:
        # ``schedule_attendance_round`` is called from the confirmation of placements and nowhere else, and
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
            from leaguebot.attendance.services.rsvp_service import repost_rsvp_call, withdraw_rsvp_call

            _division_id = row["division_id"]
            if not _verdict.check_in_stays_closed:
                # The check-in is open again, so a round whose check-in had been taken down a
                # day after it is so no longer (#425); test mode reads the mark.
                #
                # And the reserves are placed no longer (#429). The answers carry over to the
                # call that replaces this one, but the distribution was made against this one,
                # and the new call's own deadline makes it afresh. Left standing, a reserve
                # that deadline puts on standby would keep the old team, and be charged as a
                # no-show for not racing in it.
                async with get_connection(self._db_path) as db:
                    await db.execute(
                        "UPDATE rounds SET checkin_cleared = 0 WHERE id = ?", (round_id,)
                    )
                    await db.execute(
                        "UPDATE driver_round_attendance "
                        "SET assigned_team_id = NULL, is_standby = 0 WHERE round_id = ?",
                        (round_id,),
                    )
                    await db.commit()
                if _verdict.check_in["call"].stands:
                    await repost_rsvp_call(round_id, _division_id, bot)
                else:
                    await withdraw_rsvp_call(round_id, _division_id, bot)

        # Erase the stored forecast message of each withdrawn phase, and only those (FR-011).
        # A phase that still stands keeps its message, which is what leaves the division holding
        # the latest forecast that survives the amendment rather than an empty channel.
        if any_phase_done and _withdrawn:
            from leaguebot.weather.services.forecast_cleanup_service import delete_forecast_message
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
            from leaguebot.weather.utils.message_builder import invalidation_message
            from leaguebot.core.services.output_router import ForecastChannel

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
                    ForecastChannel(row["forecast_channel_id"]),
                    invalidation_message(amended_track),
                    enqueue_on_failure=True,
                )

        # 7. Re-run missed phases (non-MYSTERY only, and only with weather on)
        from leaguebot.weather.services.phase1_service import run_phase1
        from leaguebot.weather.services.phase2_service import run_phase2
        from leaguebot.weather.services.phase3_service import run_phase3

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


async def validate_modification_ordering(db_path: str, season_id: int) -> list[str]:
    """Return ordering errors in the modification store, in approval's own words.

    The mid-season counterpart to the season approval's ordering gate. Both guard the
    same thing — the table a championship is scored on — and a rule that held only at
    approval would be a rule a league could step around by amending afterwards, with
    every round rescored against the bad table on the spot.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT config_name, session_type, position, points "
            "FROM season_modification_entries WHERE season_id = ? "
            "ORDER BY config_name, session_type, position",
            (season_id,),
        )
        rows = await cursor.fetchall()

    errors: list[str] = []
    for (config_name, session_type), group in groupby(
        rows, key=lambda r: (r["config_name"], r["session_type"])
    ):
        pairs = [(r["position"], r["points"]) for r in group]
        errors.extend(
            ordering_message(config_name, session_type, violation)
            for violation in ordering_violations(pairs)
        )
    return errors


async def modification_ordering_warnings(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
) -> list[str]:
    """Return how one staged session's table now reads out of order, if it does.

    The modification store's answer to
    :func:`leaguebot.results.services.points_config_service.ordering_warnings`, and given on the same
    terms: a staged edit that breaks the ordering **warns and still applies**. A manager
    restructuring a table mid-season moves through the same transient states as one
    building it in the first place, and the refusal waits for
    the approval, which is where the table would stop being staged and start scoring a
    championship.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT position, points FROM season_modification_entries "
            "WHERE season_id = ? AND config_name = ? AND session_type = ?",
            (season_id, config_name, session_type),
        )
        rows = await cursor.fetchall()

    return [
        f"position {position} ({points} pts) < position {next_position} ({next_points} pts)"
        for position, points, next_position, next_points in ordering_violations(
            [(r["position"], r["points"]) for r in rows]
        )
    ]


async def get_amendment_state(db_path: str, season_id: int):
    """Return SeasonAmendmentState or None if no record exists."""
    from leaguebot.results.models.amendment_state import SeasonAmendmentState
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


async def staged_position_points(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    position: int,
) -> dict[str, int | None]:
    """The points the modification store holds for one position, read so that the results cog
    can judge whether a request changes nothing (#482).

    ``{"points": None}`` where nothing is held, and always where the season is not in
    amendment mode: a request made outside the mode can never stand, so it reaches the
    store's own refusal, which comes before "nothing changed".
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT e.points FROM season_modification_entries e "
            "JOIN season_amendment_state s ON s.season_id = e.season_id AND s.amendment_active = 1 "
            "WHERE e.season_id = ? AND e.config_name = ? AND e.session_type = ? "
            "AND e.position = ?",
            (season_id, config_name, session_type, position),
        )
        row = await cursor.fetchone()
    return {"points": None if row is None else row["points"]}


async def staged_fastest_lap(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
) -> dict[str, int | None]:
    """The fastest-lap bonus and position limit the modification store holds for one session,
    both ``None`` where it holds no row or the season is not in amendment mode (#482). See
    :func:`staged_position_points`."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT f.fl_points, f.fl_position_limit FROM season_modification_fl f "
            "JOIN season_amendment_state s ON s.season_id = f.season_id AND s.amendment_active = 1 "
            "WHERE f.season_id = ? AND f.config_name = ? AND f.session_type = ?",
            (season_id, config_name, session_type),
        )
        row = await cursor.fetchone()
    if row is None:
        return {"fl_points": None, "fl_position_limit": None}
    return {"fl_points": row["fl_points"], "fl_position_limit": row["fl_position_limit"]}


async def modify_session_points(
    db_path: str,
    season_id: int,
    config_name: str,
    session_type: str,
    pairs: list[tuple[int, int]],
    *,
    actor_id: int,
    actor_name: str,
    now: datetime,
) -> None:
    """Stage every ``(position, points)`` of *pairs* in the modification store, in one
    transaction, recording each staged change.

    `/results amend session` stages one pair and `/results amend bulk-session` a whole paste,
    which is all or nothing: every pair is staged, with an audit entry by *actor_id* at *now*
    for each position whose staged points changed, from what to what, or — where anything
    fails before the commit — none is, and no entry either. Raises
    :class:`AmendmentNotActiveError` where the season is not in amendment mode.
    """
    await _require_amendment_active(db_path, season_id)
    async with get_connection(db_path) as db:
        for position, points in pairs:
            cursor = await db.execute(
                "SELECT points FROM season_modification_entries "
                "WHERE season_id = ? AND config_name = ? AND session_type = ? AND position = ?",
                (season_id, config_name, session_type, position),
            )
            row = await cursor.fetchone()
            old = None if row is None else row["points"]
            await db.execute(
                """
                INSERT OR REPLACE INTO season_modification_entries
                    (season_id, config_name, session_type, position, points)
                VALUES (?, ?, ?, ?, ?)
                """,
                (season_id, config_name, session_type, position, points),
            )
            if old != points:
                await record_change_on(
                    db,
                    actor_id=actor_id,
                    actor_name=actor_name,
                    change_type="POINTS_AMENDMENT_STAGED",
                    old_value={
                        "season_id": season_id, "config": config_name,
                        "session_type": session_type, "position": position, "points": old,
                    },
                    new_value={
                        "season_id": season_id, "config": config_name,
                        "session_type": session_type, "position": position, "points": points,
                    },
                    now=now,
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


async def _season_exists(db_path: str, season_id: int) -> bool:
    """Whether the season can be read, checked before the approval writes anything."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT 1 FROM seasons WHERE id = ?", (season_id,))
        return await cursor.fetchone() is not None


async def approval_faults(db_path: str, season_id: int, bot: LeagueBot) -> list[str]:
    """Everything that would stop an approved amendment being published (#187).

    Returns the faults as lines a league can read, and an empty list where the whole
    cascade could be carried out.

    **Called three times, from one reading.** `/results amend review` calls it to draw its
    panel, so a manager sees what is wrong while deciding rather than after pressing Approve;
    the points approval's check calls it again at the press and once more when the approval
    runs, before it writes anything, so the refusal cannot be stepped around by a panel drawn
    when the channels were still sound. This is how `validate_modification_ordering` is already
    used, and how `_placement_confirmation_faults` serves the season's own review and
    confirmation — the report and the refusal are the same reading, so they cannot drift.

    **A cancelled division is included** (owner, 2026-10-06, "Refuse at the press"): the
    approval rescores, reposts and recalculates its raced rounds as a live division's, so the
    results, standings, attendance and verdicts channels it configured are asked too, as the
    results specification's "every division's" requires.

    **Each module answers for its own channels.** The results module knows what its repost
    needs and the attendance module knows what its recalculation needs; this function only
    composes them, and reaches into neither's configuration itself.
    """
    from leaguebot.results.services import results_post_service

    if not await _season_exists(db_path, season_id):
        return ["The season could not be read, so nothing was changed."]

    guild = (await league_guild(bot)) if bot is not None else None
    faults = await results_post_service.repost_channel_faults(
        db_path, season_id, guild, bot
    )

    # The attendance module recalculates as part of the same cascade, so its channels are
    # part of the same question. Asked only where it is switched on: a league without it
    # must not be refused for an attendance channel it has never configured (#187).
    attendance_on = False
    try:
        attendance_on = await bot.module_service.is_attendance_enabled()
    except Exception:  # noqa: BLE001 — never refuse an amendment on this reader
        log.exception("approval_faults: could not read the attendance module's state")
    if attendance_on:
        from leaguebot.attendance.services import attendance_service

        faults += await attendance_service.recalculation_faults(
            db_path, season_id, guild, bot, cancelled_too=True
        )

    return faults
