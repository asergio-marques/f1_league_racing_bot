"""Results' step for `/test-mode advance`: whether a round's result submission is still to open,
read from results' own state alone.

Core's `pending_steps_read` hook asks each module which of the rounds it hands over have steps
still pending (architecture.md, "How modules and core fit together"). Results answers from its own
switch, the session results it keeps and the submission changes still in hand on the change queue;
core hands it the rounds as it reads them, their status among it, and orders what comes back.

A round's result submission (4) is pending while the round is not yet run or is awaiting its
results, no session's results stand and no submission is already asked of the change queue: it
never comes from the job store, so database state alone says it is due, whatever the round's
format. Results passes nothing over, and while it is off nothing of it is armed and nothing is
handed back.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from leaguebot.core.db.database import get_connection, tuple_rows_in
from leaguebot.core.services.change_queue import unfinished
from leaguebot.core.services.test_mode_service import (
    PHASE_KINDS,
    ActiveRound,
    ModuleSteps,
    PendingStep,
)
from leaguebot.results.services.module_switch_settings import results_enabled_on
from leaguebot.results.services.submission_changes import KIND as SUBMISSION_OPEN

#: The phase of the result submission, and the statuses a round stands at while it is still due.
SUBMISSION_PHASE = 4
_DUE_STATUSES = frozenset({"NOT_RUN", "AWAITING_RESULTS"})


async def pending_steps(
    db_path: str, rounds: Sequence[ActiveRound], *, now: datetime
) -> ModuleSteps:
    """Results' pending steps of *rounds*. It passes nothing over, so *now* judges nothing;
    nothing is handed back while results is off."""
    async with get_connection(db_path) as db:
        if not await results_enabled_on(db):
            return ModuleSteps(pending=[])
        with_results = {
            int(r[0])
            for r in await tuple_rows_in(
                db,
                "SELECT DISTINCT round_id FROM session_results "
                "WHERE status = 'ACTIVE' AND round_id IN ({marks})",
                [r.round_id for r in rounds],
            )
        }
    # Rounds whose submission an earlier advance asked of the queue and which it has not opened
    # yet: the database alone would offer the round start again.
    in_hand = {int(p["round_id"]) for p in await unfinished(db_path, (SUBMISSION_OPEN,))}
    return ModuleSteps(
        pending=[
            PendingStep(r.round_id, SUBMISSION_PHASE, PHASE_KINDS[SUBMISSION_PHASE])
            for r in rounds
            if r.status in _DUE_STATUSES
            and r.round_id not in with_results
            and r.round_id not in in_hand
        ]
    )
