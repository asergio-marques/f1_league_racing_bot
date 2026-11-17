"""The timers attendance wants set for a round: its check-in's call, last notice, deadline and
clean-up, as the plain records core arms (`RoundLead`).

One function builds them from the league's lead times, so that core's arming, the approval's date
check and the modules' own reads of the same moments take them from one place and cannot drift
apart (#544). A last notice of 0 hours is no last notice and is left out, and a deadline of 0
hours is the round's own moment, which the date check words "at the round".

Every lead is armed only while its moment is still ahead of the queue's clock: no timer has a
lateness limit, so one whose moment passed while the bot was down fires when it is back and its
handler, the check-in's change, judges what late means (a last notice is sent late while the
deadline is ahead, and a call or a deadline is asked as it would be on time), while a moment
already past when a round is armed is not armed at all, the restart's catch-up taking it
(`check_in_recovery`).
"""
from __future__ import annotations

from datetime import timedelta

from leaguebot.core.models.round import AttendanceWindows, RoundLead
from leaguebot.core.services.rounds.round_moments import (
    POST_RACE_CLEANUP_DELAY,
    days_before,
    hours_before,
)


def check_in_leads(windows: AttendanceWindows) -> tuple[RoundLead, ...]:
    """The call, the last notice (none where its hours are 0), the deadline and the clean-up a
    round's check-in wants, for the lead times *windows*, in that order. The first three are
    quoted by the approval's date check; the clean-up is never judged."""
    leads = [
        RoundLead(
            kind="rsvp_notice", offset=-timedelta(days=windows.notice_days),
            label="RSVP notice", ahead_only=True, check_label="Check-in call",
            check_wording=days_before(windows.notice_days),
        )
    ]
    if windows.last_notice_hours > 0:
        leads.append(RoundLead(
            kind="rsvp_last_notice", offset=-timedelta(hours=windows.last_notice_hours),
            label="RSVP last-notice", ahead_only=True, check_label="Check-in last notice",
            check_wording=hours_before(windows.last_notice_hours),
        ))
    leads.append(RoundLead(
        kind="rsvp_deadline", offset=-timedelta(hours=max(windows.deadline_hours, 0)),
        label="RSVP deadline", ahead_only=True, check_label="Check-in deadline",
        check_wording=(
            hours_before(windows.deadline_hours) if windows.deadline_hours > 0 else "at the round"
        ),
    ))
    leads.append(RoundLead(
        kind="rsvp_cleanup", offset=POST_RACE_CLEANUP_DELAY, label="RSVP cleanup",
        ahead_only=True, clean_up=True,
    ))
    return tuple(leads)
