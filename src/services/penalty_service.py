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

#: What in a steward's text would notify a group rather than name a person (#204): a role
#: mention, and the two Discord reads as the whole server and as everyone online in it. A user
#: mention is not among them — naming the other car is the ordinary thing to write.
_GROUP_MENTION_RE = re.compile(r"<@&\d+>|@everyone|@here", re.IGNORECASE)


@dataclass
class StagedPenalty:
    driver_user_id: int
    session_type: SessionType
    penalty_type: Literal["TIME", "DSQ"]
    penalty_seconds: int | None
    description: str = ""
    justification: str = ""
    #: Who decided this and when, where it was read back from a decided round rather than typed
    #: now (#345). An amendment writes a round's decisions out again, and a verdict it keeps
    #: must keep its author and its time — otherwise every kept verdict would name the admin who
    #: amended the round, at the moment they did. None for a decision staged now, which takes
    #: the approving manager and the time of approval as it always has.
    decided_by: str | None = None
    decided_at: str | None = None


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


def group_mention_refusal(field_label: str, text: str) -> str | None:
    """Why *text* cannot stand as a steward's *field_label*, or None where it can (#204).

    A penalty's description and justification are published in its verdict, where a role
    mention, ``@everyone`` or ``@here`` would notify everybody it covers — a whole division told
    of one driver's penalty. Refused at the form, where the steward can still rewrite it, rather
    than stripped on the way out, which would publish a text they did not write.

    Matched in any case: nothing a verdict legitimately says reads "@Everyone", and refusing it
    keeps the rule from resting on exactly how Discord matches the two.
    """
    match = _GROUP_MENTION_RE.search(text or "")
    if match is None:
        return None
    found = match.group(0)
    named = "a role" if found.startswith("<@&") else f"`{found.lower()}`"
    return (
        f"The {field_label} mentions {named}, which would notify everybody it covers. "
        "Mention drivers only, then submit the form again."
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

    from services import results_post_service

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
            new_result_id = driver_to_new_result_id.get((sp.session_type.value, sp.driver_user_id))
            if new_result_id is None:
                log.warning(
                    "apply_penalties: no result row for user %s session %s — skipping record",
                    sp.driver_user_id,
                    sp.session_type.value,
                )
                continue
            race_result_id = new_result_id if not sp.session_type.is_qualifying else None
            qual_result_id = new_result_id if sp.session_type.is_qualifying else None
            # A decision read back from the round keeps its own author and time (#345).
            record_by = sp.decided_by or str(applied_by)
            cursor = await db.execute(
                """
                INSERT INTO penalty_records (
                    race_result_id, qual_result_id,
                    penalty_type, time_seconds,
                    description, justification, applied_by, applied_at,
                    announcement_channel_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
                """,
                (
                    race_result_id,
                    qual_result_id,
                    sp.penalty_type,
                    sp.penalty_seconds,
                    sp.description,
                    sp.justification,
                    record_by,
                    sp.decided_at or now_str,
                ),
            )
            inserted_records.append(
                {
                    "id": cursor.lastrowid,
                    "race_result_id": race_result_id,
                    "qual_result_id": qual_result_id,
                    "driver_user_id": sp.driver_user_id,
                    "penalty_type": sp.penalty_type,
                    "time_seconds": sp.penalty_seconds,
                    "description": sp.description,
                    "justification": sp.justification,
                    "applied_by": record_by,
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
            "SELECT 1 FROM divisions WHERE id = ?", (division_id,)
        )
        srv_row = await cursor2.fetchone()

    # Cascade recompute standings, then repost. The guild is resolved first so the
    # recomputation orders a full tie on the names the repost below will draw.
    repost_faults: list[str] = []
    if not _skip_post:
        guild = None
        async with get_connection(db_path) as db3:
            cursor3 = await db3.execute(
                "SELECT 1 FROM divisions WHERE id = ?", (division_id,)
            )
            row3 = await cursor3.fetchone()
        if row3:
            from utils.league_server import league_guild

            guild = await league_guild(bot)

        await results_post_service.recompute_standings_from_round(
            db_path, division_id, round_id, guild, bot
        )

        # An unreachable guild is a fault, not a reason to skip in silence (#237, #244).
        # ``league_guild`` returns None both where the guild is out of the gateway cache
        # and where no league is set up, and neither raises — so a penalty could rescore
        # the championship and leave every posted standing stale with nobody told.
        if guild is None:
            repost_faults.append(
                "The league's server could not be reached, so the round's results and "
                "standings were not reposted."
            )
        else:
            repost_faults += await results_post_service.repost_round_results(
                db_path, round_id, division_id, guild, bot=bot
            )

    # The audit line is written *after* the posting, and says what the posting achieved
    # (#237). Posting it first reported a success the cascade below had not yet earned and
    # might never earn — the ordering ``season_end_service`` already keeps.
    if srv_row:
        outcome = "Incomplete" if repost_faults else "Success"
        details_msg = (
            f"<@{applied_by}> | PENALTIES_APPLIED | {outcome}\n"
            f"  round_id: {round_id}\n"
            f"  penalties: {details}"
        )
        if repost_faults:
            hint = await results_post_service.results_sync_hint(db_path, division_id)
            details_msg += "\n" + "\n".join(f"  {line}" for line in repost_faults)
            details_msg += f"\n  {hint}"
        await bot.output_router.post_log(details_msg)

    return inserted_records


# ---------------------------------------------------------------------------
# Reading a decided round back into staged form (#345)
# ---------------------------------------------------------------------------


async def load_staged_from_records(
    db_path: str, round_id: int, *, session_types: list[SessionType] | None = None
) -> tuple[list[StagedPenalty], list[StagedPenalty], list["StagedPardon"]]:
    """Rebuild a round's decided reports, appeals and pardons as staged entries.

    The amendment replay shows a manager what was decided and lets them change it, which means
    reading the decisions back out of the tables they were written to. Nothing did that before:
    :class:`StagedPenalty` was only ever built from a steward's typed input, and the review
    wizard's own restart recovery deliberately reopens with an **empty** staged list rather than
    re-hydrating one.

    **The driver and the session have to be recovered by join.** ``penalty_records`` and
    ``appeal_records`` store neither ``driver_user_id`` nor ``session_type`` — only a reference
    to one driver's row in ``race_session_results`` or ``qualifying_session_results``, from which
    the driver reads directly and the session through ``session_results``.

    **Which list a penalty record belongs to is not recorded either.** ``apply_penalties``
    inserts into ``penalty_records`` on both phases, so the appeal phase writes a row there *and*
    a row in ``appeal_records``. The appeals are therefore taken from ``appeal_records`` alone,
    and a ``penalty_records`` row is a report unless an appeal record of the same driver, session
    and sanction accounts for it — matched once each, so two identical penalties are not both
    swallowed by one appeal.

    *session_types* narrows the read to those sessions, which is what an amendment wants: it
    replays the sessions it re-entered, and re-applying a report belonging to another would add
    to penalty columns that already hold it — the rows of an unamended session were never
    re-inserted at zero.

    Returned in the order the records were written, so a manager reads them as they were decided.
    """
    from services.penalty_wizard import StagedPardon

    reports: list[StagedPenalty] = []
    appeals: list[StagedPenalty] = []
    pardons: list[StagedPardon] = []

    from services.verdict_records import VERDICT_TABLES, select_verdicts

    async with get_connection(db_path) as db:
        rows_of: dict[str, list[dict]] = {}
        # The two tables name their author and time differently.
        provenance = {
            "penalty_records": ("applied_by", "applied_at"),
            "appeal_records": ("submitted_by", "submitted_at"),
        }
        for table in VERDICT_TABLES:
            by_col, at_col = provenance[table]
            rows_of[table] = await select_verdicts(
                db, table,
                "v.id AS record_id, v.penalty_type, v.time_seconds, v.description, "
                f"v.justification, v.{by_col} AS decided_by, v.{at_col} AS decided_at, "
                "r.driver_user_id AS driver_user_id, sr.session_type AS session_type",
                round_id=round_id, session_types=session_types,
            )

        for row in sorted(rows_of["appeal_records"], key=lambda r: r["record_id"]):
            appeals.append(_staged_from_record(row))

        for row in reports_only(
            sorted(rows_of["penalty_records"], key=lambda r: r["record_id"]),
            rows_of["appeal_records"],
        ):
            reports.append(_staged_from_record(row))

        cursor = await db.execute(
            """
            SELECT p.attendance_id, p.pardon_type, p.justification, p.granted_by,
                   p.granted_at,
                   a.driver_profile_id AS driver_profile_id,
                   d.discord_user_id AS discord_user_id
            FROM attendance_pardons p
            JOIN driver_round_attendance a ON a.id = p.attendance_id
            JOIN driver_profiles d ON d.id = a.driver_profile_id
            WHERE a.round_id = ?
            ORDER BY p.id
            """,
            (round_id,),
        )
        for row in await cursor.fetchall():
            try:
                driver_user_id = int(row["discord_user_id"])
            except (TypeError, ValueError):
                driver_user_id = 0
            pardons.append(
                StagedPardon(
                    driver_user_id=driver_user_id,
                    driver_profile_id=row["driver_profile_id"],
                    attendance_id=row["attendance_id"],
                    pardon_type=row["pardon_type"],
                    justification=row["justification"] or "",
                    grantor_id=int(row["granted_by"]) if row["granted_by"] else 0,
                    granted_at=row["granted_at"],
                )
            )

    return reports, appeals, pardons


def reports_only(penalty_rows: list, appeal_rows: list) -> list:
    """The penalty records that are reports, leaving out the ones an appeal wrote.

    ``apply_penalties`` inserts into ``penalty_records`` on both phases, so upholding an appeal
    writes a row there *and* a row in ``appeal_records``, and nothing records which phase a
    penalty row came from. A penalty row is therefore a report unless an appeal record of the
    same shape accounts for it — matched once each, so two identical penalties are not both
    swallowed by one appeal.

    Both the amendment's hydration and its republish need the split (#345): the one to show a
    report once rather than as a report *and* an appeal, the other to announce an upheld appeal
    once rather than as a penalty verdict as well. *penalty_rows* keep their order.
    """
    unclaimed = [_record_shape(row) for row in appeal_rows]
    kept = []
    for row in penalty_rows:
        shape = _record_shape(row)
        if shape in unclaimed:
            unclaimed.remove(shape)
            continue
        kept.append(row)
    return kept


def _record_shape(row) -> tuple:
    """What makes two verdict records the same sanction, for pairing an appeal to its penalty.

    **The text is part of it.** ``finalize_appeals_review`` writes the ``appeal_records`` row and
    the ``penalty_records`` row from the *same* ``StagedPenalty``, copying the description and
    the justification into both, so a genuine pair always agrees on all six fields.

    Matching on the driver, session, type and seconds alone was enough to stop an appeal being
    counted twice, but it also claimed a *different* report of the same shape: a driver given a
    5 s report for one incident and a separate 5 s appeal for another, in one session, lost the
    report from the review stage entirely — the manager could neither see nor edit it. Including
    the text distinguishes the two, and costs nothing, because a real pair shares it.
    """
    return (
        row["driver_user_id"],
        row["session_type"],
        row["penalty_type"],
        row["time_seconds"],
        row["description"] or "",
        row["justification"] or "",
    )


def _staged_from_record(row) -> StagedPenalty:
    """One verdict record as the staged entry a steward would have typed to produce it."""
    return StagedPenalty(
        driver_user_id=row["driver_user_id"],
        session_type=SessionType(row["session_type"]),
        penalty_type=row["penalty_type"],
        penalty_seconds=row["time_seconds"],
        description=row["description"] or "",
        justification=row["justification"] or "",
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
    )
