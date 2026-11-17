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


def forecast_leads(phase_1_days: int = 5, phase_2_days: int = 2, phase_3_hours: int = 2) -> tuple[Any, ...]:
    """Weather's leads for a round at the given horizons, as it hands core to arm them: its three
    phases before the round and its clean-up a day after, each armed whatever the hour. Without
    the date check's wording, which arming does not read."""
    from datetime import timedelta

    from leaguebot.core.models.round import RoundLead
    from leaguebot.core.services.rounds.round_moments import POST_RACE_CLEANUP_DELAY

    return (
        RoundLead(kind="weather_p1", offset=-timedelta(days=phase_1_days), label="Weather P1"),
        RoundLead(kind="weather_p2", offset=-timedelta(days=phase_2_days), label="Weather P2"),
        RoundLead(kind="weather_p3", offset=-timedelta(hours=phase_3_hours), label="Weather P3"),
        RoundLead(kind="cleanup", offset=POST_RACE_CLEANUP_DELAY, label="Forecast cleanup",
                  clean_up=True),
    )


def submission_lead() -> Any:
    """Results' lead for a round's submission, at the round's moment, run at once where that has
    passed and left to `/test-mode advance` where core says so."""
    from datetime import timedelta

    from leaguebot.core.models.round import RoundLead

    return RoundLead(kind="results", offset=timedelta(0), label="Result submission",
                     run_at_once=True, left_to_advance=True)
