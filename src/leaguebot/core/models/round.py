"""Round model, and the plain data that places a round's timed events: its job kinds and windows."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

class RoundFormat(str, Enum):
    NORMAL = "NORMAL"
    SPRINT = "SPRINT"
    MYSTERY = "MYSTERY"
    ENDURANCE = "ENDURANCE"

    @property
    def label(self) -> str:
        """The format as a league reads it: "Sprint", never ``SPRINT``.

        Text a league reads names a format through this, never by interpolating the member.
        A ``str`` enum in an f-string formats as ``RoundFormat.SPRINT`` from Python 3.12 on,
        which is no way to name a format to a league.
        """
        return _ROUND_FORMAT_LABELS[self]


#: Written out rather than derived from the value, so a format whose name has two words cannot
#: reach a league as ``Reverse_grid``.
_ROUND_FORMAT_LABELS: dict[RoundFormat, str] = {
    RoundFormat.NORMAL: "Normal",
    RoundFormat.SPRINT: "Sprint",
    RoundFormat.MYSTERY: "Mystery",
    RoundFormat.ENDURANCE: "Endurance",
}


class RoundStatus(str, Enum):
    """Where a round stands in its life, as one chain.

    Each intermediate state is named for what the round is waiting on rather than for what has
    already happened to it, because what comes next is what a league manager needs to know.

    Rejected: two columns, one saying whether the round is on and another how settled its
    results are. Only six of their combinations are reachable, and a single `PROVISIONAL`
    covers three of them at once: not yet due, due but unentered, and entered but unjudged.
    That last distinction is the one that matters, and blurring it makes cancelling misbehave.
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
#: division never finishes and its season can never be completed. Disabling the
#: module therefore has to close these rounds itself.
#:
#: NOT_RUN is deliberately not among them: that round is waiting on the clock, not on results,
#: and the round's start already closes it as FINAL when its moment arrives with the
#: module off.
ROUND_AWAITING_RESULTS_MODULE = frozenset({
    RoundStatus.AWAITING_RESULTS.value,
    RoundStatus.AWAITING_REPORT_VERDICTS.value,
    RoundStatus.AWAITING_APPEAL_VERDICTS.value,
})

#: The states a round was *raced* in but whose verdicts are still open. These are exactly the
#: states ``ROUND_CANCELLABLE`` excludes and ``ROUND_TERMINAL`` has not yet reached: a round here
#: has results entered, so cancelling a season may not call it off — "a cancellation shall never
#: discard a result", and "a round further along shall keep its place and its results"
#: (``core_specification.md``, Cancelling a season). Cancellation closes them as FINAL instead,
#: which is what makes their results final and their drivers former drivers.
ROUND_RACED_AWAITING_VERDICTS = frozenset({
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
    status: str = RoundStatus.NOT_RUN.value


def stored_scheduled_at(moment: datetime) -> str:
    """*moment* as ``rounds.scheduled_at`` holds it: UTC, ISO 8601, written with its offset.

    The one writer's form, so that text order is time order wherever the column is sorted or
    compared in SQL. A moment with no offset is refused with a ``ValueError``, since converting
    it would read it in the host's zone.
    """
    if moment.tzinfo is None:
        raise ValueError(f"a round's moment carries no offset: {moment.isoformat()!r}")
    return moment.astimezone(timezone.utc).isoformat()


def utc_text(moment: datetime) -> str:
    """*moment* in UTC as ISO text without its offset: how a reply and a log line show a round's
    moment, the zone being said where it is not obvious."""
    return moment.astimezone(timezone.utc).replace(tzinfo=None).isoformat()


def read_scheduled_at(stored: str) -> datetime:
    """A round's stored moment as an aware UTC datetime: the one reader of ``rounds.scheduled_at``.

    Every writer stores ``moment.astimezone(timezone.utc).isoformat()``, the offset included, so
    no reader makes a moment aware itself. A value without an offset is no form a writer
    produces, and it is refused with a ``ValueError`` rather than read as UTC by guesswork.
    """
    moment = datetime.fromisoformat(stored)
    if moment.tzinfo is None:
        raise ValueError(f"a round's stored moment carries no offset: {stored!r}")
    return moment.astimezone(timezone.utc)


#: The kinds of the jobs the weather module owns: its three forecast phases and the post-race
#: clean-up that deletes what they posted.
WEATHER_JOB_KINDS = frozenset({"weather_p1", "weather_p2", "weather_p3", "cleanup"})

#: The kinds of a round's nine jobs: each is its job id's prefix, and its key the round id. A
#: job is a round's where its kind is one of these; another job's key, an amendment's lapse
#: timer's for one, is no round's, however it is numbered.
ROUND_JOB_KINDS = WEATHER_JOB_KINDS | {
    "results", "rsvp_notice", "rsvp_last_notice", "rsvp_deadline", "rsvp_cleanup",
}

#: The phase number (`get_next_pending_phase`) of each kind of round job the job store holds
#: for the steps `/test-mode advance` runs: the one map both ways read, the job store from kind to
#: phase and test mode from phase to kind. Result submission (4) is read from database state and
#: so is not here, and a mystery round's notice (0) is armed as `weather_p1`, which is 1 here.
ROUND_JOB_PHASES = {
    "weather_p1": 1, "weather_p2": 2, "weather_p3": 3, "rsvp_notice": 5,
    "rsvp_last_notice": 6, "rsvp_deadline": 7, "cleanup": 8, "rsvp_cleanup": 9,
}


@dataclass(frozen=True)
class AttendanceWindows:
    """The three check-in lead times, as `attendance_config` stores them."""

    notice_days: int
    last_notice_hours: int
    deadline_hours: int


@dataclass(frozen=True)
class WeatherWindows:
    """The three forecast phase horizons, as `weather_pipeline_config` stores them."""

    phase_1_days: int
    phase_2_days: int
    phase_3_hours: int
