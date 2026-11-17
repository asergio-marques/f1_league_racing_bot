"""The delay after the race that places a round's clean-ups, and the words and reads the modules' timers share.

`clean_up_moment` gives when a round's clean-ups fall, the one next-day figure the weather and
attendance modules both place theirs by. Each module places the rest of its own events from its
own lead builder (`forecast_moments`, `check_in_moments`), which core's arming reads the same
leads from, so for them the rules hold in one place: a last notice of 0 hours is no last notice,
a deadline of 0 hours is the round's own moment, and a moment stored without a zone is UTC.

Pure by design: no database, no clock, no scheduler; `read_round_leads` only gathers what the
modules' subscribers to the leads hook hand back.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone

from leaguebot.core.models.round import RoundLead
from leaguebot.framework.utils.hooks import AwaitedHook

#: How long after a round's scheduled start its posted messages come down: the weather module's
#: forecast, whichever of Phases 1 to 3 still stands, and the attendance module's check-in call
#: with its last notice and its distribution message. One figure for both, so a round's
#: channels clear together.
POST_RACE_CLEANUP_DELAY = timedelta(hours=24)


def days_before(count: int) -> str:
    """*count* days ahead of a round as the approval's date check words it: "1 day before",
    "5 days before"."""
    return f"{count} day{'' if count == 1 else 's'} before"


def hours_before(count: int) -> str:
    """*count* hours ahead of a round as the approval's date check words it: "1 hour before",
    "2 hours before"."""
    return f"{count} hour{'' if count == 1 else 's'} before"


async def read_round_leads(leads_read: AwaitedHook) -> tuple[RoundLead, ...]:
    """Every timer the modules want set for a round, in the order the modules signed up to
    *leads_read*: each hands back its own leads, plain records core arms under its own naming
    (`RoundJobs.arm_leads`), and a module that is off hands back none, results alone answering
    whatever its switch."""
    return tuple(lead for part in await leads_read.call() for lead in part or ())


async def read_phases_drawn(
    phases_drawn_read: AwaitedHook, round_ids: Sequence[int]
) -> dict[int, dict[int, bool]]:
    """Whether each of Phases 1 to 3 has been drawn, by phase number, for each round in
    *round_ids*, by round id, as the module that draws them says: they are its own record, not a
    column of core's round. A round nobody answers for is absent, and its phases count as not
    drawn.
    """
    drawn: dict[int, dict[int, bool]] = {}
    for part in await phases_drawn_read.call(round_ids):
        drawn.update(part or {})
    return drawn


def results_left_to_advance(*, test_mode: bool, results_on: bool, weather_on: bool) -> bool:
    """Whether a round's results submission is left to `/test-mode advance`, armed by no job.

    So where results is on and weather off under test mode (core specification, "What confirming
    placements does"). The season's approval and a round's amendment both read it here, so that an
    amended round is left to the advance as its neighbours are, whatever its moment."""
    return test_mode and results_on and not weather_on


def as_utc(moment: datetime) -> datetime:
    """A moment with a zone is itself; a naive one is UTC. The queue's clock is read through it: a
    round's moment is aware already, and goes through no such conversion."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment


def clean_up_moment(scheduled_at: datetime) -> datetime:
    """When a round's clean-ups fall, `POST_RACE_CLEANUP_DELAY` after its moment, in UTC.

    The forecast's and the check-in's fall together and depend on no lead time, so the callers
    that need only this, the amendment's test of a clean-up due and the restart's catch-up, ask
    for it here; the modules' lead builders place both clean-ups by `POST_RACE_CLEANUP_DELAY`.
    """
    return scheduled_at + POST_RACE_CLEANUP_DELAY
