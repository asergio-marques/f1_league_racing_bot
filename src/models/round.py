"""Round model."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class RoundFormat(str, Enum):
    NORMAL = "NORMAL"
    SPRINT = "SPRINT"
    MYSTERY = "MYSTERY"
    ENDURANCE = "ENDURANCE"


class RoundStatus(str, Enum):
    """Where a round stands in its life, as one chain.

    Each intermediate state is named for what the round is waiting on rather than for what has
    already happened to it, because what comes next is what a league manager needs to know.

    A round carried two columns until migration 053 — one saying whether it was on, another how
    settled its results were. Only six of their combinations were ever reachable, and the old
    `PROVISIONAL` covered three of them at once: not yet due, due but unentered, and entered but
    unjudged. That last distinction is the one that matters, and is why cancelling misbehaved.
    """

    NOT_RUN = "NOT_RUN"
    AWAITING_RESULTS = "AWAITING_RESULTS"
    AWAITING_REPORT_VERDICTS = "AWAITING_REPORT_VERDICTS"
    AWAITING_APPEAL_VERDICTS = "AWAITING_APPEAL_VERDICTS"
    FINAL = "FINAL"
    CANCELLED = "CANCELLED"


#: A round is done — it holds nothing open — when it is either of these.
ROUND_TERMINAL = frozenset({RoundStatus.FINAL.value, RoundStatus.CANCELLED.value})

#: A round may only be cancelled before its results are entered. Once they are, the drivers have
#: reports and appeals to lodge, and calling the round off would take that from them. This is the
#: whole cancellation rule, and both the single-round command and the cascade read it from here.
ROUND_CANCELLABLE = frozenset({RoundStatus.NOT_RUN.value, RoundStatus.AWAITING_RESULTS.value})

#: The states only the results module can move a round out of. Every one of them waits on a
#: results command — the submission wizard, the report verdicts, the appeal verdicts — so with
#: the module switched off nothing will ever move them and the round waits for ever, its
#: division never finishes and its season can never be completed (issue #167). Disabling the
#: module therefore has to close these rounds itself.
#:
#: NOT_RUN is deliberately not among them: that round is waiting on the clock, not on results,
#: and ``run_result_submission_job`` already closes it as FINAL when its moment arrives with the
#: module off.
ROUND_AWAITING_RESULTS_MODULE = frozenset({
    RoundStatus.AWAITING_RESULTS.value,
    RoundStatus.AWAITING_REPORT_VERDICTS.value,
    RoundStatus.AWAITING_APPEAL_VERDICTS.value,
})


@dataclass
class Round:
    id: int
    division_id: int
    round_number: int
    format: RoundFormat
    track_name: str | None
    scheduled_at: datetime
    phase1_done: bool = False
    phase2_done: bool = False
    phase3_done: bool = False
    status: str = RoundStatus.NOT_RUN.value
