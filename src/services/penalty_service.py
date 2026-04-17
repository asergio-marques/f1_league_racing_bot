"""penalty_service.py — Post-race penalty staging and application."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Literal

import discord

from db.database import get_connection
from models.points_config import SessionType

log = logging.getLogger(__name__)

_TIME_PENALTY_RE = re.compile(r"^([+-]?\d+)s?$", re.IGNORECASE)

# HH:MM:SS.mmm or MM:SS.mmm or SS.mmm
_LAP_TIME_RE = re.compile(
    r"^(?:(?P<h>\d+):)?(?P<m>\d+):(?P<s>\d+)(?:\.(?P<ms>\d+))?$"
)

# Delta gap: +SS.mmm  |  +M:SS.mmm  |  +H:MM:SS.mmm  (leading + required)
_DELTA_GAP_RE = re.compile(
    r"^\+(?:(?:(?P<h>\d+):)?(?P<m>\d+):)?(?P<s>\d+)(?:\.(?P<ms>\d+))?$"
)

# Lap gap: "+N Lap(s)" or "N Lap(s)"
_LAP_GAP_RE = re.compile(r"^\+?(\d+) Laps?$", re.IGNORECASE)


@dataclass
class StagedPenalty:
    driver_user_id: int
    session_type: SessionType
    penalty_type: Literal["TIME", "DSQ"]
    penalty_seconds: int | None
    description: str = ""
    justification: str = ""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_penalty_input(
    driver_user_id: int,
    session_type: SessionType,
    penalty_value: str,
    current_time_ms: int | None = None,
    current_time_penalty_s: int | None = None,
) -> StagedPenalty | str:
    """Parse penalty_value for the given session.

    Returns a StagedPenalty on success or an error string on failure.

    Args:
        driver_user_id: The Discord user ID of the driver.
        session_type: The session the penalty applies to.
        penalty_value: Raw input string, e.g. ``+5s``, ``-3``, ``10``, ``DSQ``.
        current_time_ms: The driver's current total race time in milliseconds.
            When provided, negative penalties are rejected if they would make
            the resulting time negative.  Pass ``None`` to skip this check.
        current_time_penalty_s: The driver's currently applied time penalty in
            seconds (``post_race_time_penalties`` column).  When provided,
            negative penalties are rejected if their absolute value exceeds
            this figure — you cannot remove more penalty than was applied.
    """
    pv = penalty_value.strip().upper()

    if pv == "DSQ":
        return StagedPenalty(
            driver_user_id=driver_user_id,
            session_type=session_type,
            penalty_type="DSQ",
            penalty_seconds=None,
        )

    if session_type.is_qualifying:
        return "Only DSQ is accepted for qualifying sessions."

    m = _TIME_PENALTY_RE.match(penalty_value.strip())
    if not m:
        return "Invalid penalty. Use seconds (e.g. `5`, `+5s`, `-3s`) or `DSQ`."

    seconds = int(m.group(1))
    if seconds == 0:
        return "Penalty must be a non-zero number of seconds."

    if seconds < 0 and current_time_penalty_s is not None:
        if abs(seconds) > current_time_penalty_s:
            return (
                f"A {seconds}s penalty cannot be applied — the driver only has "
                f"{current_time_penalty_s}s of time penalties in this session."
            )

    if seconds < 0 and current_time_ms is not None:
        resulting_ms = current_time_ms + seconds * 1000
        if resulting_ms < 0:
            return (
                f"A {seconds}s penalty would result in a negative race time. "
                "Please review your input."
            )

    return StagedPenalty(
        driver_user_id=driver_user_id,
        session_type=session_type,
        penalty_type="TIME",
        penalty_seconds=seconds,
    )


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _time_to_ms(time_str: str) -> int | None:
    """Parse HH:MM:SS.mmm or MM:SS.mmm into total milliseconds."""
    if not time_str or time_str.strip() in ("-", "N/A", ""):
        return None
    m = _LAP_TIME_RE.match(time_str.strip())
    if not m:
        return None
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    secs = int(m.group("s") or 0)
    ms_raw = m.group("ms") or "0"
    ms = int(ms_raw.ljust(3, "0")[:3])
    return (h * 3600 + mins * 60 + secs) * 1000 + ms


def _ms_to_time(ms: int) -> str:
    """Format total milliseconds as HH:MM:SS.mmm."""
    total_s, ms_part = divmod(ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    return f"{mins}:{secs:02d}.{ms_part:03d}"


def _delta_to_ms(delta_str: str) -> int | None:
    """Parse a delta gap string (+SS.mmm, +M:SS.mmm, +H:MM:SS.mmm) into ms.

    Returns None if the string does not match the expected format.
    """
    m = _DELTA_GAP_RE.match((delta_str or "").strip())
    if not m:
        return None
    h = int(m.group("h") or 0)
    mins = int(m.group("m") or 0)
    secs = int(m.group("s") or 0)
    ms_raw = m.group("ms") or "0"
    ms = int(ms_raw.ljust(3, "0")[:3])
    return (h * 3600 + mins * 60 + secs) * 1000 + ms


def _ms_to_delta(gap_ms: int) -> str:
    """Format a gap in milliseconds as a delta string (+SS.mmm, +M:SS.mmm, etc.)."""
    total_s, ms_part = divmod(gap_ms, 1000)
    total_m, secs = divmod(total_s, 60)
    hours, mins = divmod(total_m, 60)
    if hours:
        return f"+{hours}:{mins:02d}:{secs:02d}.{ms_part:03d}"
    if mins:
        return f"+{mins}:{secs:02d}.{ms_part:03d}"
    return f"+{secs}.{ms_part:03d}"


def _apply_time_penalty(total_time_str: str, penalty_seconds: int) -> str:
    """Add *penalty_seconds* (positive or negative) to a total-time string.

    Returns the adjusted time string, or the original string unchanged when
    the time cannot be parsed.

    Note: callers (i.e. ``validate_penalty_input``) are responsible for
    guaranteeing that the resulting time is non-negative before calling this
    function.  No floor clamp is applied here.
    """
    ms = _time_to_ms(total_time_str)
    if ms is None:
        return total_time_str  # Cannot parse — leave unchanged
    ms += penalty_seconds * 1000
    return _ms_to_time(ms)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

async def apply_penalties(
    db_path: str,
    round_id: int,
    division_id: int,
    staged: list[StagedPenalty],
    applied_by: int,
    bot: discord.Client,
    *,
    _skip_post: bool = False,
    _phase: Literal["PENALTY", "APPEAL"] = "PENALTY",
) -> list[dict]:
    """Apply a list of staged penalties to the DB and cascade standings.

    Inserts one row into ``penalty_records`` per staged penalty and returns
    the list of inserted record dicts (including ``id`` from the DB).

    When *_skip_post* is ``True`` the internal cascade-recompute and
    ``repost_round_results`` calls are skipped.  Pass ``True`` when the
    caller (e.g. ``finalize_penalty_review``) will handle reposting itself.
    """
    import datetime

    from services import standings_service
    from services import results_post_service

    # Map (session_type_value, driver_user_id) -> driver_session_results.id
    # populated during the session loop so we can INSERT penalty_records after.
    driver_to_dsr_id: dict[tuple[str, int], int] = {}
    # Map (session_type_value, driver_user_id) -> new table row id (race or qual)
    driver_to_new_result_id: dict[tuple[str, int], int] = {}
    inserted_records: list[dict] = []

    async with get_connection(db_path) as db:
        # Load season_id for audit log
        cursor = await db.execute(
            """
            SELECT d.season_id
            FROM rounds r
            JOIN divisions d ON d.id = r.division_id
            WHERE r.id = ?
            """,
            (round_id,),
        )
        row = await cursor.fetchone()
        season_id: int | None = row["season_id"] if row else None

        # Group staged penalties by session_type
        session_types = {sp.session_type for sp in staged}

        for session_type in session_types:
            session_penalties = [sp for sp in staged if sp.session_type == session_type]

            # Fetch the session_result for this round + session
            cursor = await db.execute(
                "SELECT id FROM session_results WHERE round_id = ? AND session_type = ? AND status = 'ACTIVE'",
                (round_id, session_type.value),
            )
            sr_row = await cursor.fetchone()
            if sr_row is None:
                log.warning(
                    "apply_penalties: no ACTIVE session_result for round %s, session %s",
                    round_id,
                    session_type.value,
                )
                continue
            session_result_id: int = sr_row["id"]

            # Fetch all driver rows for this session (non-superseded)
            cursor = await db.execute(
                """
                SELECT id, driver_user_id, finishing_position, outcome, total_time,
                       fastest_lap, time_penalties, post_steward_total_time,
                       post_race_time_penalties, best_lap, tyre, gap
                FROM driver_session_results
                WHERE session_result_id = ? AND is_superseded = 0
                ORDER BY finishing_position
                """,
                (session_result_id,),
            )
            driver_rows = list(await cursor.fetchall())

            # Track driver_user_id -> driver_session_result_id for penalty_records INSERT.
            for dr in driver_rows:
                driver_to_dsr_id[(session_type.value, int(dr["driver_user_id"]))] = dr["id"]
            dsq_ids = {sp.driver_user_id for sp in session_penalties if sp.penalty_type == "DSQ"}
            time_boosts: dict[int, int] = {
                sp.driver_user_id: sp.penalty_seconds
                for sp in session_penalties
                if sp.penalty_type == "TIME" and sp.penalty_seconds is not None
            }

            updated_rows: list[dict] = []
            for dr in driver_rows:
                uid = int(dr["driver_user_id"])
                update: dict = {
                    "id": dr["id"],
                    "driver_user_id": int(dr["driver_user_id"]),
                    "outcome": dr["outcome"],
                    "total_time": dr["total_time"],
                    "post_steward_total_time": dr["post_steward_total_time"],
                    "post_race_time_penalties": dr["post_race_time_penalties"],
                    "fastest_lap": dr["fastest_lap"],
                    "time_penalties": dr["time_penalties"],
                    "best_lap": dr["best_lap"],
                    "tyre": dr["tyre"],
                    "gap": dr["gap"],
                    "points_awarded": None,  # will be recomputed
                    "fastest_lap_bonus": None,
                    "finishing_position": dr["finishing_position"],
                }
                if uid in dsq_ids:
                    update["outcome"] = "DSQ"
                    update["points_awarded"] = 0
                    update["fastest_lap_bonus"] = 0
                    if session_type.is_qualifying:
                        update["best_lap"] = "DSQ"
                        update["tyre"] = "N/A"
                        update["gap"] = "N/A"
                    else:
                        update["total_time"] = "DSQ"
                        update["fastest_lap"] = "N/A"
                        update["time_penalties"] = "N/A"
                elif uid in time_boosts:
                    # Race TIME penalties are applied in the re-sort block
                    # below so that gap strings (e.g. "+2.955") are handled
                    # alongside absolute times via absolute-ms conversion.
                    pass
                updated_rows.append(update)

            # Re-sort positions: DSQs go last; TIME-penalised rows move accordingly
            # Qualifying: by best_lap (no total_time); Race: by total_time
            classified = []
            dsq_list = []
            for dr_dict in updated_rows:
                if dr_dict["outcome"] == "DSQ":
                    dsq_list.append(dr_dict)
                else:
                    classified.append(dr_dict)

            if not session_type.is_qualifying:
                # -----------------------------------------------------------
                # Convert all classified drivers to absolute ms so that gap
                # strings ("+2.955", "+1:02.345") are handled alongside
                # absolute times when applying penalties and re-sorting.
                # -----------------------------------------------------------

                # P1's total_time is always an absolute time string; find it
                # from the first classified row that parses as absolute.
                p1_ms: int | None = None
                for u in classified:
                    ms_val = _time_to_ms(u["total_time"] or "")
                    if ms_val is not None:
                        p1_ms = ms_val
                        break  # rows are ordered by finishing_position

                timed: list[dict] = []           # computable absolute ms
                lapped: list[dict] = []          # "+N Lap(s)"
                other_classified: list[dict] = []  # DNF, DNS literals, etc.

                for u in classified:
                    tt = u["total_time"] or ""
                    abs_ms = _time_to_ms(tt)
                    if abs_ms is not None:
                        u["_abs_ms"] = abs_ms
                        timed.append(u)
                    elif p1_ms is not None and _delta_to_ms(tt) is not None:
                        u["_abs_ms"] = p1_ms + (_delta_to_ms(tt) or 0)
                        timed.append(u)
                    elif _LAP_GAP_RE.match(tt):
                        lapped.append(u)
                    else:
                        other_classified.append(u)

                # Apply time penalties to affected drivers' absolute ms.
                for u in timed:
                    uid2 = int(u["driver_user_id"])
                    if uid2 in time_boosts:
                        u["_abs_ms"] += time_boosts[uid2] * 1000

                # Re-sort timed drivers by absolute ms.
                timed.sort(key=lambda d: d["_abs_ms"])

                # Recalculate total_time: new P1 gets absolute string; rest
                # get a recalculated gap relative to the new P1.
                if timed:
                    new_p1_ms: int = timed[0]["_abs_ms"]
                    timed[0]["total_time"] = _ms_to_time(new_p1_ms)
                    for u in timed[1:]:
                        u["total_time"] = _ms_to_delta(u["_abs_ms"] - new_p1_ms)

                # Lapped and other-classified keep their original total_time;
                # preserve relative order by original finishing_position.
                lapped.sort(key=lambda d: d["finishing_position"])
                other_classified.sort(key=lambda d: d["finishing_position"])

                classified = timed + lapped + other_classified

            # DSQ rows keep relative order by original position
            dsq_list.sort(key=lambda d: d["finishing_position"])

            reordered = classified + dsq_list
            for new_pos, dr_dict in enumerate(reordered, start=1):
                dr_dict["finishing_position"] = new_pos

            # Persist mutations
            for dr_dict in reordered:
                await db.execute(
                    """
                    UPDATE driver_session_results
                    SET outcome = ?,
                        total_time = ?,
                        post_steward_total_time = ?,
                        post_race_time_penalties = ?,
                        fastest_lap = ?,
                        time_penalties = ?,
                        best_lap = ?,
                        tyre = ?,
                        gap = ?,
                        finishing_position = ?
                    WHERE id = ?
                    """,
                    (
                        dr_dict["outcome"],
                        dr_dict["total_time"],
                        dr_dict["post_steward_total_time"],
                        dr_dict["post_race_time_penalties"],
                        dr_dict["fastest_lap"],
                        dr_dict["time_penalties"],
                        dr_dict["best_lap"],
                        dr_dict["tyre"],
                        dr_dict["gap"],
                        dr_dict["finishing_position"],
                        dr_dict["id"],
                    ),
                )

            # --- Update new result tables ---
            if not session_type.is_qualifying:
                for sp in session_penalties:
                    if sp.penalty_type == "DSQ":
                        await db.execute(
                            "UPDATE race_session_results SET outcome = 'DSQ' "
                            "WHERE session_result_id = ? AND driver_user_id = ?",
                            (session_result_id, sp.driver_user_id),
                        )
                    elif sp.penalty_type == "TIME" and sp.penalty_seconds is not None:
                        penalty_ms = sp.penalty_seconds * 1000
                        if _phase == "APPEAL":
                            await db.execute(
                                "UPDATE race_session_results "
                                "SET appeal_time_penalties_ms = appeal_time_penalties_ms + ? "
                                "WHERE session_result_id = ? AND driver_user_id = ?",
                                (penalty_ms, session_result_id, sp.driver_user_id),
                            )
                        else:
                            await db.execute(
                                "UPDATE race_session_results "
                                "SET postrace_time_penalties_ms = postrace_time_penalties_ms + ? "
                                "WHERE session_result_id = ? AND driver_user_id = ?",
                                (penalty_ms, session_result_id, sp.driver_user_id),
                            )
                # Re-sort race_session_results by total_time_ms for CLASSIFIED non-lapped
                rr_cursor = await db.execute(
                    "SELECT id, driver_user_id, outcome, base_time_ms, laps_behind, "
                    "ingame_time_penalties_ms, postrace_time_penalties_ms, "
                    "appeal_time_penalties_ms, finishing_position "
                    "FROM race_session_results WHERE session_result_id = ? "
                    "ORDER BY finishing_position",
                    (session_result_id,),
                )
                rsr_rows = list(await rr_cursor.fetchall())
                for rr in rsr_rows:
                    driver_to_new_result_id[(session_type.value, int(rr["driver_user_id"]))] = rr["id"]
                sortable_rsr = []
                fixed_rsr = []
                for rr in rsr_rows:
                    if (
                        rr["outcome"] == "CLASSIFIED"
                        and rr["laps_behind"] is None
                        and rr["base_time_ms"] is not None
                    ):
                        total_ms = (
                            rr["base_time_ms"]
                            + rr["ingame_time_penalties_ms"]
                            + rr["postrace_time_penalties_ms"]
                            + rr["appeal_time_penalties_ms"]
                        )
                        sortable_rsr.append({"id": rr["id"], "total_ms": total_ms})
                    else:
                        fixed_rsr.append({"id": rr["id"], "fp": rr["finishing_position"]})
                sortable_rsr.sort(key=lambda r: r["total_ms"])
                next_pos = 1
                for item in sortable_rsr:
                    await db.execute(
                        "UPDATE race_session_results SET finishing_position = ? WHERE id = ?",
                        (next_pos, item["id"]),
                    )
                    next_pos += 1
                fixed_rsr.sort(key=lambda r: r["fp"])
                for item in fixed_rsr:
                    await db.execute(
                        "UPDATE race_session_results SET finishing_position = ? WHERE id = ?",
                        (next_pos, item["id"]),
                    )
                    next_pos += 1
            else:
                # Qualifying: apply DSQ, then re-sort positions
                for sp in session_penalties:
                    if sp.penalty_type == "DSQ":
                        await db.execute(
                            "UPDATE qualifying_session_results SET outcome = 'DSQ' "
                            "WHERE session_result_id = ? AND driver_user_id = ?",
                            (session_result_id, sp.driver_user_id),
                        )
                # Re-sort qualifying_session_results: CLASSIFIED by best_lap_ms, then fixed last
                qr_cursor = await db.execute(
                    "SELECT id, driver_user_id, outcome, best_lap, finishing_position "
                    "FROM qualifying_session_results WHERE session_result_id = ? "
                    "ORDER BY finishing_position",
                    (session_result_id,),
                )
                qsr_rows = list(await qr_cursor.fetchall())
                for qr in qsr_rows:
                    driver_to_new_result_id[(session_type.value, int(qr["driver_user_id"]))] = qr["id"]
                sortable_qsr = []
                fixed_qsr = []
                for qr in qsr_rows:
                    if qr["outcome"] == "CLASSIFIED":
                        lap_ms = _time_to_ms(qr["best_lap"] or "")
                        if lap_ms is not None:
                            sortable_qsr.append({"id": qr["id"], "lap_ms": lap_ms})
                            continue
                    fixed_qsr.append({"id": qr["id"], "fp": qr["finishing_position"]})
                sortable_qsr.sort(key=lambda r: r["lap_ms"])
                next_pos = 1
                for item in sortable_qsr:
                    await db.execute(
                        "UPDATE qualifying_session_results SET finishing_position = ? WHERE id = ?",
                        (next_pos, item["id"]),
                    )
                    next_pos += 1
                fixed_qsr.sort(key=lambda r: r["fp"])
                for item in fixed_qsr:
                    await db.execute(
                        "UPDATE qualifying_session_results SET finishing_position = ? WHERE id = ?",
                        (next_pos, item["id"]),
                    )
                    next_pos += 1

        # INSERT one penalty_records row per staged penalty.
        now_str = datetime.datetime.utcnow().isoformat()
        for sp in staged:
            dsr_id = driver_to_dsr_id.get((sp.session_type.value, sp.driver_user_id))
            if dsr_id is None:
                log.warning(
                    "apply_penalties: no dsr_id for user %s session %s — skipping record",
                    sp.driver_user_id,
                    sp.session_type.value,
                )
                continue
            new_result_id = driver_to_new_result_id.get((sp.session_type.value, sp.driver_user_id))
            race_result_id = new_result_id if not sp.session_type.is_qualifying else None
            qual_result_id = new_result_id if sp.session_type.is_qualifying else None
            cursor = await db.execute(
                """
                INSERT INTO penalty_records (
                    driver_session_result_id, race_result_id, qual_result_id,
                    penalty_type, time_seconds,
                    description, justification, applied_by, applied_at,
                    announcement_channel_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    dsr_id,
                    race_result_id,
                    qual_result_id,
                    sp.penalty_type,
                    sp.penalty_seconds,
                    sp.description,
                    sp.justification,
                    str(applied_by),
                    now_str,
                ),
            )
            inserted_records.append(
                {
                    "id": cursor.lastrowid,
                    "driver_session_result_id": dsr_id,
                    "race_result_id": race_result_id,
                    "qual_result_id": qual_result_id,
                    "driver_user_id": sp.driver_user_id,
                    "penalty_type": sp.penalty_type,
                    "time_seconds": sp.penalty_seconds,
                    "description": sp.description,
                    "justification": sp.justification,
                    "applied_by": str(applied_by),
                    "announcement_channel_id": None,
                }
            )

        await db.commit()

    # Audit log
    details = json.dumps(
        [
            {
                "driver_user_id": sp.driver_user_id,
                "session_type": sp.session_type.value,
                "penalty_type": sp.penalty_type,
                "penalty_seconds": sp.penalty_seconds,
            }
            for sp in staged
        ]
    )
    async with get_connection(db_path) as db2:
        cursor2 = await db2.execute(
            "SELECT s.server_id FROM seasons s JOIN divisions d ON d.season_id = s.id WHERE d.id = ?",
            (division_id,),
        )
        srv_row = await cursor2.fetchone()
    if srv_row:
        details_msg = (
            f"<@{applied_by}> | PENALTIES_APPLIED | Success\n"
            f"  round_id: {round_id}\n"
            f"  penalties: {details}"
        )
        await bot.output_router.post_log(int(srv_row["server_id"]), details_msg)

    # Cascade recompute standings
    if not _skip_post:
        await standings_service.cascade_recompute_from_round(db_path, division_id, round_id)

    # Repost results in Discord
    if not _skip_post:
        guild = None
        async with get_connection(db_path) as db3:
            cursor3 = await db3.execute(
                "SELECT s.server_id FROM seasons s JOIN divisions d ON d.season_id = s.id WHERE d.id = ?",
                (division_id,),
            )
            row3 = await cursor3.fetchone()
        if row3:
            guild = bot.get_guild(int(row3["server_id"]))
        if guild:
            await results_post_service.repost_round_results(db_path, round_id, division_id, guild)

    return inserted_records

