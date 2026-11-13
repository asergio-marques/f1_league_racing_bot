"""Attendance's steps for `/test-mode advance`: what of each round's check-in it still has to post
or take down, and how the review marks them, read from attendance's own state alone.

Core's `pending_steps_read` hook asks each module which of the rounds it hands over have steps
still pending (architecture.md, "How modules and core fit together"). Attendance answers from its
own switch and configuration, its own column on `rounds` (`checkin_cleared`), the check-in calls it
keeps and the check-in changes still in hand on the change queue; core hands it the rounds as it
reads them and orders what comes back.

Attendance's steps of a round are the check-in call (5) while none is posted and the check-in has
not been taken down, then, once it stands, its last notice (6) until it is sent (never where the
league set it to 0), the deadline (7) until it has run, and the clean-up (9) while the call
stands. A step already asked of the change queue and not yet run is not handed back: the advance
that asked for it cancelled the scheduler's job, so the database alone would offer it again.
Attendance passes nothing over, and while it is off nothing of it is armed and nothing is handed
back.

The review's marks of a round come through core's `review_marks_read` hook: its check-in call, last
notice (left out where the league set it to 0), deadline and clean-up, each done (✅), pending with
its job queued, already asked of the change queue or with no scheduler to ask (⏳), or pending with
no job (⚠️). A check-in taken down has every step done.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

import aiosqlite

from leaguebot.attendance.models.attendance import (
    CHECK_IN_CALL,
    CHECK_IN_CLEAN_UP,
    CHECK_IN_DEADLINE,
    CHECK_IN_LAST_NOTICE,
)
from leaguebot.attendance.services.settings.module_switch_settings import attendance_enabled_on
from leaguebot.core.db.database import get_connection, tuple_rows_in
from leaguebot.core.services.queue.queue_records import unfinished
from leaguebot.core.services.test_mode.test_mode_service import (
    PHASE_KINDS,
    ActiveRound,
    ModuleSteps,
    PendingStep,
    ReviewMarks,
    phase_mark,
)

#: The step of each check-in phase, by the change that asks for it.
_CHECK_IN_PHASES = {
    CHECK_IN_CALL: 5,
    CHECK_IN_LAST_NOTICE: 6,
    CHECK_IN_DEADLINE: 7,
    CHECK_IN_CLEAN_UP: 9,
}

#: The scheduler's event type of each check-in step, by its phase.
_CHECK_IN_EVENTS = {5: "rsvp_notice", 6: "rsvp_last_notice", 7: "rsvp_deadline", 9: "rsvp_cleanup"}


async def check_ins_cleared(
    db: aiosqlite.Connection, round_ids: Sequence[int]
) -> set[int]:
    """The rounds of *round_ids* whose check-in has been taken down."""
    rows = await tuple_rows_in(
        db, "SELECT id, checkin_cleared FROM rounds WHERE id IN ({marks})", round_ids
    )
    return {int(r[0]) for r in rows if r[1]}


async def _calls_standing(db: aiosqlite.Connection, round_ids: Sequence[int]) -> dict[int, Any]:
    """The check-in call recorded for each of *round_ids* that has one: its last notice message,
    its distribution message and the mark of its deadline having run."""
    rows = await tuple_rows_in(
        db,
        "SELECT round_id, last_notice_msg_id, distribution_msg_id, deadline_run_at "
        "FROM rsvp_embed_messages WHERE round_id IN ({marks})",
        round_ids,
    )
    return {int(r[0]): r for r in rows}


async def _check_ins_in_hand(db_path: str) -> set[tuple[int, int]]:
    """Each check-in step asked of the change queue and not yet run, as (round, phase)."""
    return {
        (int(payload["round_id"]), phase)
        for kind, phase in _CHECK_IN_PHASES.items()
        for payload in await unfinished(db_path, (kind,))
    }


def _deadline_has_run(call: Any) -> bool:
    """Whether a round's check-in deadline has run: it is marked (`deadline_run_at`, set in the
    save that records the distribution, before the announcement is posted) or its announcement
    stands. The mark is what the deadline's own job reads, so a deadline whose announcement was
    refused is not run again by advance; the announcement alone covers a row written before the
    mark."""
    return bool(call[3] or call[2])


async def _check_in_state(
    db_path: str, round_ids: list[int]
) -> tuple[bool, set[int], dict[int, Any]] | None:
    """What advance and the review both judge the check-in of *round_ids* by, read once: whether
    the last notice is sent at all, the rounds whose check-in has been taken down, and each
    standing call; None while attendance is off. A last notice set to 0 is never sent and no job is
    armed for it, and the setting cannot change while a season runs."""
    async with get_connection(db_path) as db:
        if not await attendance_enabled_on(db):
            return None
        cursor = await db.execute("SELECT rsvp_last_notice_hours FROM attendance_config")
        found = await cursor.fetchone()
        last_notice_on = found is not None and bool(found["rsvp_last_notice_hours"])
        cleared = await check_ins_cleared(db, round_ids)
        calls = await _calls_standing(db, round_ids)
    return last_notice_on, cleared, calls


async def pending_steps(
    db_path: str, rounds: Sequence[ActiveRound], *, now: datetime
) -> ModuleSteps:
    """Attendance's pending steps of *rounds*. It passes nothing over, so *now* judges nothing;
    nothing is handed back while attendance is off."""
    round_ids = [r.round_id for r in rounds]
    state = await _check_in_state(db_path, round_ids)
    if state is None:
        return ModuleSteps(pending=[])
    last_notice_on, cleared, calls = state
    in_hand = await _check_ins_in_hand(db_path)

    pending: list[PendingStep] = []

    def add(rid: int, phase: int) -> None:
        if (rid, phase) not in in_hand:
            pending.append(PendingStep(rid, phase, PHASE_KINDS[phase]))

    for rid in round_ids:
        call = calls.get(rid)
        if call is None:
            # A round whose check-in has been taken down has no call recorded either, and is not
            # owed one.
            if rid not in cleared:
                add(rid, 5)
            continue
        if last_notice_on and not call[1]:
            add(rid, 6)
        if not _deadline_has_run(call):
            add(rid, 7)
        add(rid, 9)
    return ModuleSteps(pending=pending)


async def review_marks(
    db_path: str,
    rounds: Sequence[ActiveRound],
    *,
    now: datetime,
    queued: set[tuple[int, str]] | None,
) -> ReviewMarks:
    """Attendance's marks of *rounds* for `/test-mode review`; *queued* is the jobs the scheduler
    holds as (round, event type), None where there is no scheduler. It marks nothing by *now*, and
    nothing is handed back while attendance is off.

    A check-in step in hand on the change queue is pending though its scheduler job was cancelled
    by the advance that asked for it, so it counts as queued."""
    round_ids = [r.round_id for r in rounds]
    state = await _check_in_state(db_path, round_ids)
    if state is None:
        return {}
    last_notice_on, cleared, calls = state
    if queued is not None:
        queued = queued | {
            (rid, _CHECK_IN_EVENTS[phase]) for rid, phase in await _check_ins_in_hand(db_path)
        }

    marks: ReviewMarks = {}
    for rid in round_ids:
        steps: list[tuple[str, str]]
        if rid in cleared:
            # Taken down a day after the round, and its record with it: every step before the
            # clean-up had run by then.
            steps = [("RSVP", "✅"), ("Last", "✅"), ("Deadline", "✅"), ("Cleared", "✅")]
        else:
            call = calls.get(rid)
            steps = [
                ("RSVP", "✅" if call is not None
                 else phase_mark(False, (rid, "rsvp_notice"), queued)),
                ("Last", "✅" if call is not None and call[1]
                 else phase_mark(False, (rid, "rsvp_last_notice"), queued)),
                ("Deadline", "✅" if call is not None and _deadline_has_run(call)
                 else phase_mark(False, (rid, "rsvp_deadline"), queued)),
                ("Cleared", phase_mark(False, (rid, "rsvp_cleanup"), queued)),
            ]
        marks[rid] = [(name, mark) for name, mark in steps if last_notice_on or name != "Last"]
    return marks
