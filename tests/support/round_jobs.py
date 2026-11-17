"""Core's round jobs (`RoundJobs`) as the builder leaves them: every module's round kinds signed up.

Each module's wiring signs its round kinds up with `RoundJobs` at build, each with the phase
`/test-mode advance` numbers it by, results' with none (#544). `RoundJobs` reads that register to
know a job is a round's and which phase it is, so a test reading a round's jobs back (a cancel, the
pending read, the queued read) builds it here rather than bare.
"""
from __future__ import annotations

from typing import Any

#: Every round kind and its advance phase, as the three modules sign them up: weather's three
#: forecast phases and clean-up, attendance's call, last notice, deadline and clean-up, and results'
#: submission with no phase, which test mode reads from the round's state instead.
ROUND_KINDS: dict[str, int | None] = {
    "weather_p1": 1, "weather_p2": 2, "weather_p3": 3, "cleanup": 8,
    "rsvp_notice": 5, "rsvp_last_notice": 6, "rsvp_deadline": 7, "rsvp_cleanup": 9,
    "results": None,
}


def round_jobs_over(scheduler: Any, db_path: str) -> Any:
    """Core's round jobs over *scheduler* and the league database *db_path*, every round kind
    signed up (`ROUND_KINDS`)."""
    from leaguebot.core.services.rounds.round_jobs import RoundJobs

    round_jobs = RoundJobs(scheduler, db_path)
    round_jobs.sign_up_kinds(ROUND_KINDS)
    return round_jobs
