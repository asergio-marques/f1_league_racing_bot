"""The timers weather wants set for a round: its three forecast phases and the clean-up that takes
what they posted down, as the plain records core arms (`RoundLead`).

One function builds them from the league's horizons, so that core's arming, the approval's date
check and the module's own reads of the same moments take them from one place and cannot drift
apart (#544).

Every lead is armed whatever the hour, with no lateness limit: a timer whose moment passed while
the bot was down fires when it is back, and the handler of the phase judges what late means. The
horizons are the league's own, never the packaged 5 days, 2 days and 2 hours: a round armed again
by an amendment is armed at the horizons the league has (weather specification, "Changes to a
round after generation").

`forecast_moments` places the same leads at a round's moment, for every read weather makes of when
its events fall (recovery, the amendment's rules, its phases due), so those reads and core's
arming cannot drift apart.
"""
from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime, timedelta, timezone

from leaguebot.core.models.round import RoundLead
from leaguebot.weather.models.weather_config import WeatherWindows
from leaguebot.core.services.rounds.round_moments import (
    POST_RACE_CLEANUP_DELAY,
    days_before,
    hours_before,
)

#: The kinds of the jobs the weather module owns: its three forecast phases and the post-race
#: clean-up that deletes what they posted. Weather's wiring signs them up as a round's kinds, and
#: its switch-off cancels them and nothing else, so that what is registered and what is switched
#: off cannot drift.
WEATHER_JOB_KINDS = frozenset({"weather_p1", "weather_p2", "weather_p3", "cleanup"})


def forecast_leads(
    windows: WeatherWindows, skip_phases: Collection[int] = frozenset()
) -> tuple[RoundLead, ...]:
    """The three phases and the clean-up a round's forecasts want, for the horizons *windows*, in
    that order. The phases are quoted by the approval's date check; the clean-up is never judged.

    *skip_phases* leaves out the phases an amendment draws at once itself: a timer armed for a
    horizon already passed has no lateness limit and would fire at once, asking for the phase a
    second time. The clean-up is kept.
    """
    phases = (
        (1, timedelta(days=windows.phase_1_days), days_before(windows.phase_1_days)),
        (2, timedelta(days=windows.phase_2_days), days_before(windows.phase_2_days)),
        (3, timedelta(hours=windows.phase_3_hours), hours_before(windows.phase_3_hours)),
    )
    leads = [
        RoundLead(
            kind=f"weather_p{number}", offset=-lead, label=f"Weather P{number}",
            check_label=f"Weather Phase {number}", check_wording=wording,
        )
        for number, lead, wording in phases
        if number not in skip_phases
    ]
    leads.append(RoundLead(
        kind="cleanup", offset=POST_RACE_CLEANUP_DELAY, label="Forecast cleanup", clean_up=True
    ))
    return tuple(leads)


def forecast_moments(scheduled_at: datetime, windows: WeatherWindows) -> Mapping[str, datetime]:
    """When each of a round's forecast events falls, by the kind its timer is stored under, in UTC:
    the leads `forecast_leads` gives for *windows*, placed at *scheduled_at*. No moment is judged,
    neither past nor ahead: whether one still falls ahead is the caller's to ask of the `now` it
    holds."""
    at = scheduled_at.astimezone(timezone.utc)
    return {lead.kind: at + lead.offset for lead in forecast_leads(windows)}
