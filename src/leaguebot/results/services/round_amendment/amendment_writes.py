"""amendment_writes.py — Amending a round's results: the snapshot taken before, the
verdicts re-pointed, the claim and release, the division's and season's holds, the revert of an
abandoned amendment, the review stages and the sweep of expired amendments."""
from __future__ import annotations

import json as _json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Mapping

import aiosqlite

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.season_end import COMPLETION
from leaguebot.core.utils.in_a_job import posts_in_a_job
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.models.session_result import OutcomeModifier
from leaguebot.core.utils.league_bot import LeagueBot
from leaguebot.results.services.points.scoring import _apply_points_from_config_on
from leaguebot.results.services.submission.result_submission_service import (
    CollectedSession,
    _PROFILES_IN_ROUND_SQL,
    _insert_new_tables_in_tx,
    recompute_former_drivers_for_round,
)

if TYPE_CHECKING:
    from leaguebot.core.services.posting.output_router import OutputRouter
    from leaguebot.core.services.queue.change_queue import ChangeQueue
    from leaguebot.results.services.module.attendance_hook import AttendanceAfterReview
    from leaguebot.results.services.review.review_state import PenaltyReviewState, ReadServerConfig

log = logging.getLogger(__name__)


class AmendmentWouldOrphanVerdictError(Exception):
    """An amendment would leave a penalty or appeal verdict with no result row to point at.

    Raised where a driver carrying a verdict is absent from the corrected classification. The
    amendment is refused rather than applied, because every disposal open to the bot is worse
    than not proceeding: deleting the verdict discards the audit of why the driver lost places,
    and orphaning it keeps a row nothing can ever find — every read of these tables is by the
    foreign key, so a detached verdict is invisible to the republish and to the DSQ map alike.

    Whether an amendment may drop a driver who carries a verdict, and what becomes of that
    verdict, is a rule the results specification does not yet state. Refusing is the conservative
    reading until it does (issue #345), and it is what the three-stage replay supersedes: the
    wizard shows the manager the verdict and lets them decide, which is the answer this exception
    stands in for.
    """


#: The two tables whose rows point at a driver's result row, with the column each uses.
#: ``results_purge_service._delete_rows`` names the same pair for the same reason.
_VERDICT_TABLES: tuple[str, ...] = ("penalty_records", "appeal_records")


def _verdict_fk_column(session_type: SessionType) -> str:
    """The column of a verdict record that points at *session_type*'s result rows."""
    return "qual_result_id" if session_type.is_qualifying else "race_result_id"


async def _verdicts_by_driver(
    db, session_result_id: int, session_type: SessionType
) -> dict[str, dict[int, list[int]]]:
    """Map every verdict of this session to the driver it was applied to, before the rows go.

    Returned as ``{table: {driver_user_id: [verdict row id, ...]}}``. A driver may carry several
    verdicts in one session — the penalty wizard stages one record per incident, not per driver —
    so the value is a list rather than a single id.

    Read **before** the driver rows are deleted, because the driver a verdict belongs to is only
    recoverable through the row it points at: ``penalty_records`` and ``appeal_records`` store
    neither ``driver_user_id`` nor ``session_type``.
    """
    from leaguebot.results.services.review.verdict_records import select_verdicts

    by_table: dict[str, dict[int, list[int]]] = {}
    for verdict_table in _VERDICT_TABLES:
        per_driver: dict[int, list[int]] = {}
        for row in await select_verdicts(
            db, verdict_table, "v.id AS verdict_id, r.driver_user_id AS driver_user_id",
            session_result_id=session_result_id,
        ):
            per_driver.setdefault(row["driver_user_id"], []).append(row["verdict_id"])
        if per_driver:
            by_table[verdict_table] = per_driver
    return by_table


async def _detach_verdicts(
    db, session_type: SessionType, verdicts: dict[str, dict[int, list[int]]]
) -> None:
    """Null every verdict's reference so the driver rows can be deleted.

    The foreign key is checked on the DELETE itself, so re-pointing afterwards is too late — the
    reference has to be released first. The rows are detached only within the transaction that
    re-points them a few statements later, and :func:`_repoint_verdicts` refuses before writing
    anything where a driver is missing, so no verdict is ever left detached once the transaction
    settles: either all of them are re-pointed and committed, or the whole of it is abandoned.
    """
    if not verdicts:
        return
    fk_col = _verdict_fk_column(session_type)
    for verdict_table, per_driver in verdicts.items():
        ids = [vid for vids in per_driver.values() for vid in vids]
        placeholders = ", ".join("?" for _ in ids)
        await db.execute(
            f"UPDATE {verdict_table} SET {fk_col} = NULL WHERE id IN ({placeholders})",  # noqa: S608
            ids,
        )


async def _repoint_verdicts(
    db,
    session_result_id: int,
    session_type: SessionType,
    verdicts: dict[str, dict[int, list[int]]],
    division_id: int,
) -> None:
    """Point every verdict of this session at its driver's **new** result row.

    The amendment deletes a session's driver rows and re-inserts them, so a verdict recorded
    against the old row would fail the foreign key on the delete and, were the constraint absent,
    point at nothing afterwards. The verdict follows its driver: the audit it carries — the
    justification, who applied it and when — is the only record of why that driver lost places,
    and an amendment correcting a lap time has no business discarding it (issue #345).

    **Both sides are read under the driver's current account** (#243). A pasted classification is
    normalised onto it by `validate_submission_block` before anything is stored, while the row a
    verdict points at holds whichever account the driver raced under — so comparing them raw made
    a session un-amendable the moment somebody changed account, and told the manager to include a
    driver the classification already named.

    Raises :class:`AmendmentWouldOrphanVerdictError` where a driver carrying a verdict is not in
    the corrected classification. Nothing is written in that case; the caller's transaction is
    abandoned whole.
    """
    if not verdicts:
        return
    from leaguebot.core.services.drivers.driver_service import current_account_map_for_division

    current_of = await current_account_map_for_division(db, division_id)

    def _now(driver_user_id: int) -> int:
        return current_of.get(driver_user_id, driver_user_id)

    fk_col = _verdict_fk_column(session_type)
    table = "qualifying_session_results" if session_type.is_qualifying else "race_session_results"
    cursor = await db.execute(
        f"SELECT id, driver_user_id FROM {table} WHERE session_result_id = ?",  # noqa: S608
        (session_result_id,),
    )
    new_row_of: dict[int, int] = {
        _now(r["driver_user_id"]): r["id"] for r in await cursor.fetchall()
    }

    orphaned: set[int] = set()
    for per_driver in verdicts.values():
        orphaned.update({_now(driver) for driver in per_driver} - set(new_row_of))
    if orphaned:
        named = ", ".join(f"<@{driver}>" for driver in sorted(orphaned))
        raise AmendmentWouldOrphanVerdictError(
            f"The corrected classification leaves out {named}, who carries a penalty or appeal "
            f"verdict for this session. Amending would discard the record of why that driver's "
            f"result changed, so the amendment was refused and nothing was altered. Include the "
            f"driver in the classification, or ask for the verdict to be withdrawn first."
        )

    for verdict_table, per_driver in verdicts.items():
        for driver_user_id, verdict_ids in per_driver.items():
            for verdict_id in verdict_ids:
                await db.execute(
                    f"UPDATE {verdict_table} SET {fk_col} = ? WHERE id = ?",  # noqa: S608
                    (new_row_of[_now(driver_user_id)], verdict_id),
                )


async def _remember_superseded_announcements_on(
    db: aiosqlite.Connection, round_id: int, session_types: list[SessionType]
) -> None:
    """Note the amended sessions' verdict announcements, before their records are cleared.

    The report stage deletes the session's verdict records and writes the approved set back, so
    the ids of the announcements to be taken down do not survive it. The final stage re-announces
    and needs them; without this it found every id already NULL and left both the old
    announcement and its replacement standing (#345).

    **The amended sessions only.** The round's other sessions keep their records, ids included,
    and the final stage's republish takes their announcements down itself — noting them here as
    well sent a second delete after each.

    **The first capture stands.** It is taken before any record is cleared, so a later one —
    a report stage retried after a failure — would read what the first had already cleared and
    overwrite the list with less.

    Commits nothing: the amendment's report stage saves it with the clearing of the records
    and the writing of the approved set, so a stop between them cannot lose the list.
    """
    from leaguebot.results.services.review.verdict_records import VERDICT_TABLES, select_verdicts

    rows: list[dict] = []
    for table in VERDICT_TABLES:
        rows.extend(
            await select_verdicts(
                db, table,
                "v.announcement_message_id AS anchor, v.announcement_message_ids AS chunks, "
                "v.announcement_channel_id AS channel_id, r.driver_user_id AS driver_user_id",
                round_id=round_id, session_types=session_types,
                where=" AND v.announcement_message_id IS NOT NULL",
            )
        )
    await db.execute(
        "UPDATE round_amend_channels SET superseded_announcements = ? "
        "WHERE round_id = ? AND superseded_announcements IS NULL",
        (_json.dumps(rows) if rows else None, round_id),
    )


async def _clear_round_verdict_records_on(
    db: aiosqlite.Connection, round_id: int, session_types: "list[SessionType] | None" = None
) -> None:
    """Remove a round's penalty and appeal records so the approved set can be written whole.

    **The amendment rewrites rather than adds** (#345). ``apply_penalties_on`` only ever inserts,
    and adds to the stored penalty columns; replaying a round's reports over records that are
    still there duplicated every one of them, and doubled the sanction again on a second
    amendment. The times were right — stage one re-inserts the driver rows with those columns
    at zero — but the audit was not.

    **Nothing a league can see is lost.** The reports and appeals were read back into the
    review stages before this runs, carrying their justification, their author and the time
    they were given; approving writes them out again with those intact. What changes is the
    row id, which no league reads. That is the price of a stage where removing a report
    actually removes it.

    Deleting them is also what releases the foreign key, so the rows may be rewritten against
    whichever driver rows the corrected classification produced.

    Commits nothing, so the amendment's report stage clears and rewrites in one save.
    """
    from leaguebot.results.services.review.verdict_records import delete_verdicts

    # Scoped to the sessions given, because those are all an amendment replays: clearing the
    # whole round would drop the decisions of sessions whose reports are never re-approved,
    # losing them outright.
    await delete_verdicts(db, round_id=round_id, session_types=session_types)


#: How long an amendment may sit unapproved before it is reverted (#345).
#:
#: The amendment's first stage commits: the corrected classification is written and the round
#: scored from it, before its reports and appeals have been reviewed. A manager who pastes and
#: then walks away therefore leaves the round scored one way and posted another, with the views
#: carrying no timeout of their own — so nothing resolves it.
#:
#: Half an hour is long enough to work through two review stages without being hurried, and
#: short enough that an abandoned amendment does not outlive the evening it was started in.
#:
#: **Counted from stage one, across both review stages, and never extended** (decided
#: 2026-09-21). Approving the reports hands back the deadline it was given rather than a fresh
#: one: a league amending a round is expected to have its decisions prepared before it starts,
#: however many sessions it chose. Do not turn this into an inactivity timer.
AMENDMENT_STAGE_TIMEOUT_SECONDS: int = 1800


async def sync_held_by_amendment(db_path: str, division_id: int) -> str | None:
    """The refusal a sync of *division_id* meets while an amendment of one of its rounds is open
    (#345, decided 2026-09-21), or None where none is.

    An amendment's first stage writes its corrections and recalculates the division, publishing
    nothing until its last stage is approved; a sync reposts the division from the same database,
    so it would publish them unapproved. It waits, as a submission of another of the division's
    rounds does. Read by the command at the press and by the sync's change when it runs.
    """
    row = await open_amendment_in_division(db_path, division_id)
    if row is None:
        return None
    async with get_connection(db_path) as db:
        division = await (await db.execute(
            "SELECT name FROM divisions WHERE id = ?", (division_id,)
        )).fetchone()
    name = division["name"] if division is not None else "the division"
    return (
        f"\u23f8\ufe0f Round {row['round_number']} of **{name}** is being amended in "
        f"<#{row['channel_id']}>, and its corrections are not approved yet, so the "
        f"division cannot be synced until that ends — {amendment_wait_text()}. "
        "Run this again then."
    )


def amendment_wait_text() -> str:
    """How an open amendment ends, as the refusals it causes tell a league.

    Worked out from the deadline rather than written out, so that tuning it cannot leave a
    refusal quoting the old figure. "Once", never "at most": the sweep that undoes a lapsed
    amendment runs every few minutes, and nothing bounds the pastes before the deadline starts.
    """
    minutes = AMENDMENT_STAGE_TIMEOUT_SECONDS // 60
    return (
        f"it ends when approved, or is undone once {minutes} minutes have passed since its "
        "corrections were entered"
    )


async def snapshot_before_amendment_on(
    db: aiosqlite.Connection, round_id: int, session_types: list[SessionType],
    *, now: datetime, attendance: AttendanceAfterReview,
) -> None:
    """Record the round as it stands, so an abandoned amendment can be undone, on the connection
    handed and committing nothing: stage one writes it in its own save (#439).

    Taken before stage one writes. Captures, for every session being amended, its header, its
    driver rows, and its verdict records, and the round's attendance pardons — which is the
    whole of what the amendment changes that a revert cannot recompute. The standings and the
    points follow from the driver rows, so restoring those and cascading again reproduces them.
    The amendment's half-hour runs from *now*, the queue's clock, handed in by the caller. The
    pardons are asked of *attendance*, which keeps them as its own value that the snapshot holds
    as it is.
    """
    from leaguebot.results.services.review.verdict_records import VERDICT_TABLES, select_verdicts

    sessions: list[dict] = []
    for session_type in session_types:
        is_qualifying = session_type.is_qualifying
        rows_table = (
            "qualifying_session_results" if is_qualifying else "race_session_results"
        )
        cursor = await db.execute(
            "SELECT * FROM session_results WHERE round_id = ? AND session_type = ?",
            (round_id, session_type.value),
        )
        header = await cursor.fetchone()
        if header is None:
            continue
        cursor = await db.execute(
            f"SELECT * FROM {rows_table} WHERE session_result_id = ? ORDER BY id",  # noqa: S608
            (header["id"],),
        )
        driver_rows = [dict(row) for row in await cursor.fetchall()]

        # **Whole rows, not references** (#345). Approving the report stage *deletes* the
        # session's verdict records and writes the approved set back, so an amendment
        # reverted after that point has nothing left to re-point: a snapshot holding only
        # ``(id, points_at)`` would silently no-op and lose every appeal record it carried.
        verdicts: list[dict] = []
        for table in VERDICT_TABLES:
            for row in await select_verdicts(
                db, table, "v.*", session_result_id=header["id"]
            ):
                verdicts.append({"table": table, "row": row})

        sessions.append(
            {
                "session_result_id": header["id"],
                "session_type": session_type.value,
                "rows_table": rows_table,
                "header": dict(header),
                "driver_rows": driver_rows,
                "verdicts": verdicts,
            }
        )
    if not sessions:
        return

    # **The pardons, whole** (#345). The appeal stage rewrites them before it releases the
    # snapshot, so a failure between the two — or a crash — would otherwise revert the
    # classification and the verdicts and leave the amendment's pardons standing on them.
    pardons = await attendance.pardons_of_on(db, round_id)

    expires = (now + timedelta(seconds=AMENDMENT_STAGE_TIMEOUT_SECONDS)).isoformat()
    await db.execute(
        "UPDATE round_amend_channels SET pre_amendment_state = ?, expires_at = ? "
        "WHERE round_id = ?",
        (_json.dumps({"sessions": sessions, "pardons": pardons}), expires, round_id),
    )


async def _restore_from_snapshot_on(
    db: aiosqlite.Connection, round_id: int, *, attendance: AttendanceAfterReview
) -> bool:
    """Put a round's driver rows, verdicts, headers, former-driver flags and pardons back from its
    amendment's snapshot and clear the snapshot, on the connection handed and committing nothing.

    The restore itself, which `revert_abandoned_amendment_on` runs for an amendment's lapse and
    cancel in their saves. Returns False, writing nothing, where there is no snapshot to restore
    from. The standings are not touched here: the caller cascades them. The pardons are put back
    by *attendance*, which does so only while attendance is on.
    """
    cursor = await db.execute(
        "SELECT pre_amendment_state FROM round_amend_channels WHERE round_id = ?",
        (round_id,),
    )
    row = await cursor.fetchone()
    if row is None or not row["pre_amendment_state"]:
        return False

    snapshot = _json.loads(row["pre_amendment_state"])

    from leaguebot.results.services.review.verdict_records import delete_verdicts

    for state in snapshot["sessions"]:
        rows_table: str = state["rows_table"]
        session_result_id: int = state["session_result_id"]
        # Whatever the amendment left behind goes, in both tables, so the snapshot is
        # written over a clean slate rather than merged into one.
        await delete_verdicts(db, session_result_id=session_result_id)
        await db.execute(
            f"DELETE FROM {rows_table} WHERE session_result_id = ?",  # noqa: S608
            (session_result_id,),
        )
        for driver_row in state["driver_rows"]:
            columns = ", ".join(driver_row)
            placeholders = ", ".join("?" for _ in driver_row)
            await db.execute(
                f"INSERT INTO {rows_table} ({columns}) VALUES ({placeholders})",  # noqa: S608
                list(driver_row.values()),
            )
        # The verdicts come back whole, ids included, so anything that referenced one
        # still finds it — and the DSQ marks they alone produce reappear with them.
        for verdict in state["verdicts"]:
            verdict_row = verdict["row"]
            columns = ", ".join(verdict_row)
            placeholders = ", ".join("?" for _ in verdict_row)
            await db.execute(
                f"INSERT INTO {verdict['table']} ({columns}) VALUES ({placeholders})",  # noqa: S608
                list(verdict_row.values()),
            )
        header = state["header"]
        await db.execute(
            "UPDATE session_results SET submitted_by = ?, submitted_at = ?, "
            "config_name = ?, fl_driver_override = ? WHERE id = ?",
            (
                header.get("submitted_by"),
                header.get("submitted_at"),
                header.get("config_name"),
                header.get("fl_driver_override"),
                session_result_id,
            ),
        )
    # The flag follows the results back (#216). Stage one marks nobody now, so in the
    # ordinary case this finds nothing to do; it runs all the same, because the round's
    # rows have just been restored and the flag is defined by them. `profiles_before`
    # names anyone the abandoned amendment had struck out, who is in no result to be
    # found by until the restore above puts them back.
    await recompute_former_drivers_for_round(
        db, round_id, also_consider=snapshot.get("profiles_before", [])
    )
    if "pardons" in snapshot:
        await attendance.put_back_pardons_on(db, round_id, snapshot["pardons"])
    await db.execute(
        "UPDATE round_amend_channels SET pre_amendment_state = NULL, expires_at = NULL "
        "WHERE round_id = ?",
        (round_id,),
    )
    return True


async def revert_abandoned_amendment_on(
    db: aiosqlite.Connection, round_id: int, display_names: Mapping[int, str] | None = None,
    *, attendance: AttendanceAfterReview,
) -> bool:
    """Put a round back as it was before an amendment nobody approved, on the connection handed
    and committing nothing: an amendment's lapse and its cancel run it in their `revert` save.

    The restore is `_restore_from_snapshot_on`'s, and the standings are cascaded again from the
    round in the same save (`standings_service.cascade_recompute_from_round_on`), so the round and
    its championship are put back together or not at all. A save waits on nothing but its own
    connection (architecture.md, "The database"), so the names a full tie is settled by are not
    looked up here: the change resolves them before the save, in a job of its own, and hands them
    in as *display_names*; none handed, the tie is settled by user id.

    **Nothing is reposted, because nothing was posted** (#345), and the round's pardons come back
    as they were, put back by *attendance* **only while attendance is on**: a league that turned
    attendance off meanwhile keeps the pardons it has. An amendment publishes nothing until its last stage is approved, and approving
    that stage releases the snapshot before the rebuild begins, so wherever a snapshot is still
    here to revert from, the channels are still showing the round as it was. Reposting would only
    move the round to the bottom of each channel, out of order.

    Returns True where the round was put back, and False where there was no snapshot to put it
    back from: an amendment abandoned before stage one wrote has nothing to undo.
    """
    if not await _restore_from_snapshot_on(db, round_id, attendance=attendance):
        return False
    from leaguebot.results.services.standings import standings_service

    cursor = await db.execute("SELECT division_id FROM rounds WHERE id = ?", (round_id,))
    division = await cursor.fetchone()
    if division is not None:
        await standings_service.cascade_recompute_from_round_on(
            db, int(division["division_id"]), round_id, display_names
        )
    return True


# ---------------------------------------------------------------------------
# Acting on an open amendment (#345)
# ---------------------------------------------------------------------------


#: What a manager is told on pressing a stage of an amendment that is no longer open.
_AMENDMENT_NOT_OPEN = (
    "❌ This amendment is no longer open — it lapsed, was cancelled, or this step is "
    "already being approved. Nothing was changed. Run `/results rounds amend` again if the "
    "round still needs correcting."
)


#: The next step an amendment that stopped tells its manager.
AMENDMENT_RE_RUN = "Re-run `/results rounds amend` to try again."


#: What an amendment put back as it was says became of the round.
ROUND_PUT_BACK = "The round was put back as it was. " + AMENDMENT_RE_RUN


def _sessions_text(session_types) -> str:
    """The sessions of an amendment as its log lines name them."""
    return ", ".join(st.value if isinstance(st, SessionType) else str(st) for st in session_types)


async def _claim_amendment_on(db: aiosqlite.Connection, round_id: int) -> str | None:
    """Take an open amendment's deadline, so that the sweep no longer finds it.

    The sweep's lapse is the only claimant (`amend_end_changes`): it takes the deadline in its
    own save before it reverts the round, by a compare-and-set of ``expires_at`` to NULL that
    only one save can win, so that a second lapse of the same amendment reverts nothing. The
    approvals of the amendment's stages and its **Cancel** are changes on the queue and claim
    nothing. The sweep reads only rows with a deadline, so a claimed amendment is invisible to it.

    Returns the deadline taken, or None where the
    amendment is not open: never begun, already committed or reverted, or claimed by another.
    Commits nothing: the claim is taken in the save of whatever acts on the amendment, and given up
    with it if that save is undone.
    """
    row = await (
        await db.execute(
            "SELECT expires_at FROM round_amend_channels "
            "WHERE round_id = ? AND pre_amendment_state IS NOT NULL",
            (round_id,),
        )
    ).fetchone()
    if row is None or row["expires_at"] is None:
        return None
    cursor = await db.execute(
        "UPDATE round_amend_channels SET expires_at = NULL "
        "WHERE round_id = ? AND expires_at = ?",
        (round_id, row["expires_at"]),
    )
    return row["expires_at"] if cursor.rowcount == 1 else None


async def _recompute_former_drivers_after_amendment_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Settle the former-driver flag once an amendment commits (#216), on the save *db* belongs to.

    Reads ``profiles_before`` from the snapshot — the profiles the round held before stage one
    rewrote it — and hands them to :func:`recompute_former_drivers_for_round` as candidates to
    reconsider alongside the drivers the round now holds. Without them a driver the amendment
    struck out is in no result to be found by, and their flag would stand for ever on a round
    they are no longer in. Commits nothing.

    Must run before :func:`_release_amendment_on`, which drops the snapshot this reads.
    """
    cursor = await db.execute(
        "SELECT pre_amendment_state FROM round_amend_channels WHERE round_id = ?", (round_id,)
    )
    row = await cursor.fetchone()
    profiles_before: list[int] = []
    if row is not None and row["pre_amendment_state"]:
        profiles_before = _json.loads(row["pre_amendment_state"]).get("profiles_before", [])
    await recompute_former_drivers_for_round(db, round_id, also_consider=profiles_before)


async def _release_amendment_on(db: aiosqlite.Connection, round_id: int) -> None:
    """Drop the snapshot and the deadline: the amendment is committed and can no longer revert.

    Done by the appeal stage's save, together with everything the rebuild shows, so that a stop
    after it leaves no standings or attendance owed. With the snapshot gone, a restart part-way
    through the rebuild would otherwise have reverted the round from under channels already
    showing the amendment. Commits nothing.
    """
    await db.execute(
        "UPDATE round_amend_channels SET pre_amendment_state = NULL, expires_at = NULL "
        "WHERE round_id = ?",
        (round_id,),
    )


async def open_amendment_in_division(db_path: str, division_id: int):
    """The amendment open in a division — its round, round number, channel and sessions — or None.

    One is open from the moment its channel is recorded until it is approved, cancelled or
    reverted. A row carrying ``closed_at`` is one that has ended and could not delete its
    channel; it is kept for restart recovery to find, and holds nothing open.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT rac.round_id, r.round_number, rac.channel_id, rac.session_types "
            "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
            "WHERE r.division_id = ? AND rac.closed_at IS NULL",
            (division_id,),
        )
        return await cursor.fetchone()


async def open_amendment_in_season(db_path: str, season_id: int):
    """An amendment open in any division of a season — its division, round and channel — or None.

    For what reposts or closes every division at once: completing the season, and approving a
    change to its points (#345, decided 2026-09-21). Where several are open, the one in the
    highest division is named; each is refused until none is left.
    """
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT d.name AS division_name, r.round_number, rac.channel_id "
            "FROM round_amend_channels rac JOIN rounds r ON r.id = rac.round_id "
            "JOIN divisions d ON d.id = r.division_id "
            "WHERE d.season_id = ? AND rac.closed_at IS NULL "
            "ORDER BY d.tier, r.round_number LIMIT 1",
            (season_id,),
        )
        return await cursor.fetchone()


async def season_end_refusal(db_path: str, season_id: int, ending: str) -> str | None:
    """What stands in the way of the season's *ending* (`COMPLETION` or `CANCELLATION`), as results
    has it: the refusal where a round is being amended, else None (#345, decided 2026-09-21).

    The corrections of an amendment open reach the final classification a completion posts and the
    history a cancellation writes, before they are approved. Subscribed to core's
    `season_end_refused`, which asks it with the database's path as the end is asked and as it
    runs. Reads only.
    """
    held = await open_amendment_in_season(db_path, season_id)
    if held is None:
        return None
    where = (
        f"round {held['round_number']} of **{held['division_name']}** is being amended in "
        f"<#{held['channel_id']}>. Finish or cancel it first: "
    )
    if ending == COMPLETION:
        return (
            f"❌ Cannot complete season — {where}completing posts every division's final "
            "classification, which would carry its corrections before they are approved."
        )
    return (
        f"❌ Cannot cancel the season — {where}cancelling writes every driver's history from the "
        "standings, which would carry its corrections before they are approved."
    )


async def held_by_amendment(
    db_path: str, round_id: int, division_id: int, *, then: str
) -> str | None:
    """Why nothing of *round_id* may be committed now, or None when it may.

    **A division with an amendment open commits nothing else** (#345, decided 2026-09-21).
    Stage one writes the amended round's corrected classification and recalculates the
    championship from it, publishing nothing. A first pass of another round in the division
    posts standings as its results go in and at each approval, and would publish those
    unapproved corrections — and leave them published if the amendment were then cancelled or
    lapsed, its revert posting nothing. So while one is open, a first pass's results paste and
    its report and appeal approvals are refused, and the channel stays open for the manager to
    try again. The wait is short: an amendment lapses a fixed time after its first stage.

    Called at the point of the commit itself — after the points configuration is chosen, not
    when the paste arrives — so an amendment opened while the manager is choosing still holds.
    *then* ends the refusal, saying what to do once the amendment has ended.
    """
    row = await open_amendment_in_division(db_path, division_id)
    if row is None or row["round_id"] == round_id:
        return None
    return (
        f"⏸️ Round {row['round_number']} of this division is being amended in "
        f"<#{row['channel_id']}>, so nothing can be committed for another of its rounds until "
        f"that ends — {amendment_wait_text()}. " + then
    )


async def closed_by_cancellation(db_path: str, round_id: int) -> str | None:
    """Why the wizard may take nothing more for *round_id*, or None when it may (#439).

    **A submission a cancellation has closed takes nothing** (decided 2026-10-08). The wizard
    saves each session as it is accepted, so a paste landing between a cancellation's save and
    the deletion of its channel (which a stopped `delete_channel` can stretch to the hour) would
    be saved against a cancelled round. Two causes are told apart: the round cancelled, its own
    status or its division's or its season's, and the submission closed with the round not
    cancelled, which `/season cancel` does between its closing and its cascade, and turning
    results off does by its purge. The words say which. A round with no submission row at all
    is not closed by this: a resubmission's round, which has its results entered, cannot be
    cancelled and is not asked.
    """
    async with get_connection(db_path) as db:
        return await closed_by_cancellation_on(db, round_id)


async def closed_by_cancellation_on(db: aiosqlite.Connection, round_id: int) -> str | None:
    """`closed_by_cancellation`, read on the connection handed (#439, slice 8, F2).

    A session's save reads it again inside its own save, after its turn was settled: a
    cancellation's save written on the same connection, and not yet committed, is seen, and the
    two saves hold the queue's write lock in turn, so one of them is read by the other.
    """
    cursor = await db.execute(
        "SELECT r.round_number, r.status AS round_status, d.status AS division_status, "
        "s.status AS season_status, rsc.closed "
        "FROM rounds r JOIN divisions d ON d.id = r.division_id "
        "JOIN seasons s ON s.id = d.season_id "
        "LEFT JOIN round_submission_channels rsc ON rsc.round_id = r.id "
        "WHERE r.id = ?",
        (round_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return None
    if "CANCELLED" in (row["round_status"], row["division_status"], row["season_status"]):
        return (
            f"⛔ Round {row['round_number']} has been cancelled, so its results can no longer be "
            "entered. Nothing was saved."
        )
    if row["closed"]:
        return (
            f"⛔ Round {row['round_number']}'s results submission has been closed, so its "
            "results can no longer be entered. Nothing was saved."
        )
    return None


async def stage_in_hand(db_path: str, round_id: int, *, before: int | None = None) -> bool:
    """Whether the recording of the amendment's stage one, an approval of one of its stages, or the
    lane's putting up of the stage prompt that follows either, is in hand on the change queue for
    *round_id*, queued, running or stopped on a failure. Nothing overtakes it, so the sweep,
    Cancel, the lapse and restart recovery leave the amendment alone while it is.

    With *before*, only what was asked ahead of the change of that id counts: a Cancel asked
    before the last paste handed stage one over comes up first and ends the amendment, so stage one
    queued behind it is no stage "in hand" ahead of it."""
    from leaguebot.core.services.queue.queue_records import unfinished
    from leaguebot.results.services.round_amendment.amend_turn_change import STAGE_ONE_KIND
    from leaguebot.results.services.round_amendment.amendment_stage_changes import (
        APPEALS_KIND,
        PROMPT_KIND,
        REPORTS_KIND,
    )

    return any(
        change.get("round_id") == round_id
        for change in await unfinished(
            db_path, [STAGE_ONE_KIND, REPORTS_KIND, APPEALS_KIND, PROMPT_KIND], before=before
        )
    )


@posts_in_a_job
async def run_amendment_review_stages(
    db_path: str,
    round_id: int,
    division_id: int,
    channel,
    bot: LeagueBot,
    queue: ChangeQueue,
    server_config: ReadServerConfig,
    router: OutputRouter,
    *,
    round_number: int,
    division_name: str,
    session_types: list[SessionType],
    attendance: AttendanceAfterReview,
) -> "PenaltyReviewState":
    """Replay a settled round's report and appeal stages in the amendment channel (#345).

    Opens stage two of three. The corrected classification of every amended session has been
    written, and nothing posted; this shows the league manager what was decided about those
    sessions, together, and lets them change it — keep a report, remove it, add one, and the
    same for the appeals and for the round's attendance pardons.

    **The round's decisions are read back out of the database first.** They were written when
    the round was originally reviewed and nothing has held them in memory since, so
    :func:`~leaguebot.results.services.review.penalty_service.load_staged_from_records` turns the stored rows back into
    the staged entries the review screens already know how to draw. A manager who changes
    nothing approves exactly what was there before.

    **The state is marked an amendment**, which routes approving either screen to
    :func:`_approve_amendment_reports` and :func:`_approve_amendment_appeals` rather than the
    first pass's finalisers. The round is already FINAL: a first pass through these screens sets
    ``AWAITING_APPEAL_VERDICTS`` then ``FINAL``, finishes the division, may wind the season down
    and publishes as it goes, none of which an amendment may do.

    *attendance* is the hook the change type was built with: it says which of the round's pardons
    the review may offer, so results reads nothing of attendance's switch itself.

    Returns the state the screens share. The approvals happen through the screens themselves.
    """
    from leaguebot.results.services.review.penalty_service import load_staged_from_records
    from leaguebot.results.services.review.penalty_review_views import PenaltyReviewView
    from leaguebot.results.services.review.review_state import PenaltyReviewState
    from leaguebot.results.services.review.penalty_review_views import _render_prompt_content

    # **Scoped to the sessions being amended** (#345). `apply_penalties_on` walks whatever
    # session types the staged set names, and stage one re-inserted only the amended sessions'
    # driver rows — with their penalty columns at zero. A report hydrated from an *unamended*
    # session would therefore be added on top of the milliseconds already standing there,
    # doubling a sanction in a session the manager never touched. To review a session's
    # decisions, the manager includes that session in the amendment.
    staged, staged_appeals, staged_pardons = await load_staged_from_records(
        db_path, round_id, session_types=session_types
    )
    # **The pardons only while attendance is on, as attendance's hook says.** The appeal stage
    # writes them back only then, so offering them with the module off let a manager remove one,
    # be told it was removed, and approve a round still carrying it. Results never reads
    # attendance's switch itself (architecture.md, "Results reaches attendance through one hook").
    staged_pardons = await attendance.pardons_to_review(staged_pardons)

    state = PenaltyReviewState(
        round_id=round_id,
        division_id=division_id,
        submission_channel_id=channel.id,
        session_types_present=list(session_types),
        db_path=db_path,
        bot=bot,
        queue=queue,
        server_config=server_config,
        router=router,
        staged=staged,
        staged_appeals=staged_appeals,
        staged_pardons=staged_pardons,
        round_number=round_number,
        division_name=division_name,
        is_amendment=True,
    )

    view = PenaltyReviewView(state=state)
    content = await _render_prompt_content(state)
    message = await channel.send(content, view=view)
    state.prompt_message_id = message.id
    bot.add_view(view, message_id=message.id)

    return state


@dataclass
class AmendedSession:
    """One session's corrected classification, as stage one of an amendment writes it (#345)."""

    session_type: SessionType
    #: ``ParsedQualifyingRow`` / ``ParsedRaceRow`` dataclasses, or plain dicts of the same keys.
    driver_rows: list
    config_name: str | None
    fl_driver_override: int | None = None


async def amend_round_results_on(
    db: aiosqlite.Connection,
    round_id: int,
    division_id: int,
    sessions: list[AmendedSession],
    amended_by: int,
    display_names: Mapping[int, str] | None = None,
    *,
    now: datetime,
    attendance: AttendanceAfterReview,
) -> None:
    """Supersede the chosen sessions' results with their corrected classifications, on the
    connection handed and committing nothing.

    The first of an amendment's three stages (#345). It snapshots every chosen session, writes
    each corrected classification and re-points its verdicts at it, applies each session's points
    and recomputes the standings once, all in the caller's save, so the round is never left
    holding some sessions corrected and others not. It posts nothing: the amendment's lane puts
    up the next stage. *display_names* are the standings names a full tie is settled by, resolved
    by the caller before its save; the save waits on nothing but its own connection (#439).
    *now* is the clock the amendment's half-hour runs from, and *attendance* keeps the round's
    pardons in the snapshot.

    Raises :class:`AmendmentWouldOrphanVerdictError` where a driver carrying a verdict is missing
    from their session's corrected classification, and ``SeasonImmutableError`` for an archived
    season, with nothing written in either case, the caller's save being rolled back.
    """
    from leaguebot.core.services.seasons.season_service import SeasonImmutableError

    submitted_at = now.isoformat()
    # The round as it stands, before any of it is overwritten (#345). Stage one commits, so an
    # amendment abandoned before its last stage is approved would otherwise leave the round
    # scored from the corrected classification and posted from the old one, for ever.
    await snapshot_before_amendment_on(
        db, round_id, [s.session_type for s in sessions], now=now, attendance=attendance
    )
    written: list[tuple[int, AmendedSession]] = []
    # Immutability guard: reject writes to archived seasons
    cursor = await db.execute(
        """
        SELECT s.status AS season_status, s.id AS season_id
        FROM rounds r
        JOIN divisions d ON d.id = r.division_id
        JOIN seasons s ON s.id = d.season_id
        WHERE r.id = ?
        """,
        (round_id,),
    )
    season_row = await cursor.fetchone()
    if season_row and season_row["season_status"] == "COMPLETED":
        raise SeasonImmutableError(
            f"Round {round_id} belongs to an archived season — results cannot be amended."
        )
    season_id: int | None = season_row["season_id"] if season_row else None

    # **Who the round held before the rewrite goes into the snapshot** (#216), read here
    # because the next lines delete those rows. A driver an amendment strikes out entirely
    # is afterwards in no result at all, so nothing would find them to reconsider their
    # former-driver flag — which was the "never cleared" half of #216. Stage three passes
    # this list to `recompute_former_drivers_for_round` as `also_consider`.
    cursor = await db.execute(_PROFILES_IN_ROUND_SQL, (round_id, round_id))
    profiles_before = sorted({r["profile_id"] for r in await cursor.fetchall()})

    for session in sessions:
        written.append(
            (
                await _write_amended_session_in_tx(
                    db, round_id, division_id, session, amended_by, submitted_at,
                    has_season=season_row is not None,
                ),
                session,
            )
        )
    if profiles_before:
        cursor = await db.execute(
            "SELECT pre_amendment_state FROM round_amend_channels WHERE round_id = ?",
            (round_id,),
        )
        snapshot_row = await cursor.fetchone()
        if snapshot_row is not None and snapshot_row["pre_amendment_state"]:
            snapshot = _json.loads(snapshot_row["pre_amendment_state"])
            snapshot["profiles_before"] = profiles_before
            await db.execute(
                "UPDATE round_amend_channels SET pre_amendment_state = ? WHERE round_id = ?",
                (_json.dumps(snapshot), round_id),
            )

    # Apply points from the config so points_awarded / fastest_lap_bonus are populated
    # before standings computation. (ParsedRaceRow / ParsedQualifyingRow carry no pts.)
    for session_result_id, session in written:
        if season_id is not None and session.config_name is not None:
            await _apply_points_from_config_on(
                db, session_result_id, season_id, session.config_name, session.session_type,
            )

    # **Stage one publishes nothing** (#345). It records the corrected classification and the
    # championship follows in the database; everything a league sees is rebuilt once, when the
    # appeal stage is approved, so that an amendment abandoned before then has nothing posted to
    # take back and the channels stay in round order.
    #
    # It used to post the round as "Provisional Results", which was wrong three ways. The
    # reports and appeals had not been reviewed, so the table carried the sanctions of the round
    # being replaced — and its post-race DSQ marks too, drawn from verdict rows stage one had
    # just re-pointed onto the new classification. The posting also *added* a message rather than
    # replacing one, so the original Final Results posting was orphaned above it. And an
    # amendment reverted before it completed left that provisional posting standing.
    from leaguebot.results.services.standings import standings_service  # lazy import

    await standings_service.cascade_recompute_from_round_on(
        db, division_id, round_id, display_names
    )


async def _write_amended_session_in_tx(
    db,
    round_id: int,
    division_id: int,
    session: AmendedSession,
    amended_by: int,
    submitted_at: str,
    *,
    has_season: bool,
) -> int:
    """Replace one session's driver rows with its corrected classification, inside the caller's
    transaction, and return the session's ``session_results`` id.

    **Nothing is superseded here, and nothing keeps the classification being replaced.** The
    header is updated in place and the driver rows deleted outright, then re-inserted from the
    amendment. That is why `/results rounds amend` is a league admin's command rather than a
    league manager's (issue #116): amending a FINAL round overwrites what the league raced, and
    only the amendment's own snapshot, held until it completes, puts it back.
    """
    from leaguebot.core.services.drivers.driver_service import resolve_driver_profile_id

    def _get(obj, key, default=None):
        if hasattr(obj, key):
            return getattr(obj, key)
        if hasattr(obj, "get"):
            return obj.get(key, default)
        return default

    session_type = session.session_type
    await db.execute(
        """
        UPDATE session_results
        SET submitted_by = ?, submitted_at = ?, config_name = ?, fl_driver_override = ?
        WHERE round_id = ? AND session_type = ?
        """,
        (amended_by, submitted_at, session.config_name, session.fl_driver_override,
         round_id, session_type.value),
    )
    cursor = await db.execute(
        "SELECT id FROM session_results WHERE round_id = ? AND session_type = ?",
        (round_id, session_type.value),
    )
    row = await cursor.fetchone()
    if row is None:
        raise ValueError(
            f"No session_results row found for round={round_id} session={session_type.value}"
        )
    session_result_id = row["id"]
    # **A verdict is re-pointed, not deleted** (#345). The driver rows below are deleted and
    # re-inserted, and `penalty_records` / `appeal_records` reference them without
    # `ON DELETE CASCADE` while `PRAGMA foreign_keys` is ON — so the delete was refused outright
    # for any round that had reached a penalty or appeal verdict, which is every round eligible
    # for amendment. Which driver a verdict belongs to is only knowable while the old rows
    # stand, so it is read first and applied after the re-insertion.
    verdicts = await _verdicts_by_driver(db, session_result_id, session_type)
    await _detach_verdicts(db, session_type, verdicts)
    if session_type.is_qualifying:
        await db.execute(
            "DELETE FROM qualifying_session_results WHERE session_result_id = ?",
            (session_result_id,),
        )
    else:
        await db.execute(
            "DELETE FROM race_session_results WHERE session_result_id = ?",
            (session_result_id,),
        )
    profile_map: dict[int, int | None] = {}
    rows_normalised: list[dict] = []
    for i, dr in enumerate(session.driver_rows, start=1):
        driver_user_id = _get(dr, "driver_user_id")
        drv_profile_id: int | None = None
        if has_season and driver_user_id is not None:
            drv_profile_id = await resolve_driver_profile_id(driver_user_id, db)
        if driver_user_id is not None:
            profile_map[driver_user_id] = drv_profile_id
        fp = _get(dr, "position") or _get(dr, "finishing_position") or i
        rows_normalised.append({
            "driver_user_id": driver_user_id,
            "team_instance_id": _get(dr, "team_instance_id"),
            "finishing_position": fp,
            "outcome": _get(dr, "outcome", OutcomeModifier.CLASSIFIED.value),
            "tyre": _get(dr, "tyre"),
            "best_lap": _get(dr, "best_lap"),
            "gap": _get(dr, "gap"),
            "total_time": _get(dr, "total_time"),
            "fastest_lap": _get(dr, "fastest_lap"),
            "ingame_penalties": _get(dr, "ingame_penalties"),
        })
    # **The former-driver flag is not touched here** (#216). Stage one writes the corrected
    # classification but publishes nothing and may yet be reverted, and an amendment can take a
    # driver *out* of a round as readily as put one in — so the flag is recomputed from the
    # round's results as a whole, by `recompute_former_drivers_for_round`, once stage three
    # commits the amendment. Marking row by row could only ever raise it.
    await _insert_new_tables_in_tx(
        db, session_result_id, session_type, rows_normalised, profile_map
    )
    # The verdicts follow their drivers onto the rows just inserted. Refuses, without writing,
    # where a driver carrying one is no longer in the classification.
    await _repoint_verdicts(db, session_result_id, session_type, verdicts, division_id)
    return session_result_id


async def amend_drafts_on(db: aiosqlite.Connection, amend_id: int) -> list[CollectedSession]:
    """The sessions an amendment holds as drafts, in running order, read on the connection handed.
    A draft is held as a resubmission's is (`resubmission_drafts_on`), keyed by the amendment; one
    whose points configuration is still to be chosen is not given."""
    held_rows = await (
        await db.execute(
            "SELECT session_type, rows_json, config_name FROM amend_drafts "
            "WHERE amend_id = ? AND config_name IS NOT NULL",
            (amend_id,),
        )
    ).fetchall()
    drafts = {found["session_type"]: found for found in held_rows}
    collected: list[CollectedSession] = []
    for session in SessionType:
        found = drafts.get(session.value)
        if found is None:
            continue
        held = _json.loads(found["rows_json"])
        collected.append(CollectedSession(
            session, "ACTIVE", found["config_name"] or None, None,
            list(held["rows"]), held["fl_override"],
        ))
    return collected


async def sweep_expired_amendments(
    db_path: str, *, queue: "ChangeQueue", now: datetime
) -> int:
    """Ask the change queue to end every amendment whose stages went unapproved past their
    deadline (`results.amend.lapse`, as the bot), and give how many it was asked of.

    The lapse puts the round back as it was, deletes the amendment's channel and tells the league
    in the log channel, as jobs of the queue: an amendment quietly undone would be worse than one
    left hanging. It is not due, and so not asked, while a stage of the amendment is in hand on the
    queue, stopped included, nor while the stage-two prompt is (results specification :491): the
    next sweep asks again, once the stage is done or an admin has discarded it. The queue carries
    one lapse of an amendment at a time, so a lapse already asked is not asked again.

    *db_path* is the database the amendments are read from and *queue* the change queue, both
    handed by whoever sweeps (a service does not look them up on the bot). *now* is the moment the
    deadlines are judged against, handed in by whoever sweeps (the scheduler's callback reads it off
    the queue), so that a test can pin it alongside the deadline it seeds.
    """
    from leaguebot.results.services.round_amendment.amend_end_changes import SWEEP, ask_lapse

    # Normalised to UTC-aware, because the stored deadline always is: comparing it against a
    # naive *now* raises `TypeError` outside the guard below and aborts the whole sweep, leaving
    # every lapsed amendment unreverted rather than one (#345).
    moment = now
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT id, expires_at FROM round_amend_channels WHERE expires_at IS NOT NULL"
        )
        candidates = [dict(row) for row in await cursor.fetchall()]

    asked = 0
    for row in candidates:
        try:
            deadline = datetime.fromisoformat(row["expires_at"])
        except (TypeError, ValueError):
            continue
        if deadline > moment:
            continue
        try:
            change_id = await ask_lapse(queue, db_path, int(row["id"]), cause=SWEEP)
        except Exception:  # noqa: BLE001 — one stuck amendment must not stop the rest
            log.exception("sweep_expired_amendments: could not ask the lapse of amendment %s", row["id"])
            continue
        if change_id is not None:
            asked += 1
    return asked
