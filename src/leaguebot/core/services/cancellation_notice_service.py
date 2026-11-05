"""Core's own part of a round, a division or a season called off (#175, #283).

Core announces nothing of its own: telling the drivers a race is off is each enabled module's
own notice, in its own words, found and posted through its own subscriber to the
`cancellation_notices` hook (`cancellation_changes`, "Cancellation notices";
`attendance.services.cancellation_notice`, `weather.services.cancellation_notice`,
`results.services.cancellation_notice`). A round's cancellation, a division's, a season's, and a
round ended because every session was entered as not held (results' `submission_changes`) all
call the hook, inside the save, for every division it tells; the calendar, owned by core, is
refreshed whatever the modules, through its own step.

What stays core's own:

- `NoticeFailure`, `failure_lines` and `failure_log_lines` — the one shape every failure to
  notify is reported in, whichever module or job it came from;
- `checkin_record` — a round's check-in, for the log channel, read on the connection core hands
  the job that reads it; see the module's own docstring.

A module's own notice is a job of its own on the change queue (`cancellation_changes`): one
Discord refuses stops the queue until it is retried or discarded, and is named only once
discarded, through `notice_target` on the job's own payload (`cancellation_changes.not_notified`).
"""
from __future__ import annotations

from dataclasses import dataclass

from leaguebot.core.db.database import get_connection


@dataclass(frozen=True)
class NoticeFailure:
    """One place a cancellation could not reach."""

    division_name: str
    #: What was not reached, in the words the command's reply uses.
    target: str
    reason: str

    def describe(self) -> str:
        return f"**{self.division_name}** — {self.target}: {self.reason}"


def _round_label(round_number: int, track_name: str | None) -> str:
    return f"Round {round_number} ({track_name or 'Mystery'})"


#: The order the audit lists the answers in, and the words it lists them under.
_ANSWERS = (
    ("ACCEPTED", "accepted"),
    ("TENTATIVE", "tentative"),
    ("DECLINED", "declined"),
    ("NO_RSVP", "no answer"),
)


async def checkin_record(db_path: str, division, round_id: int) -> str:
    """The log-channel record of *round_id*'s check-in in *division*, or "" where it had none.

    *division* is anything with an `id` and a `name`. Public because a round that ends cancelled
    (every session entered as not held) writes the same record beneath its line, through a job
    core builds and hands the module that ends it; it reads on its own connection and writes
    nothing.

    Every driver the check-in recorded, grouped by their answer, and — where the reserves had
    already been distributed — who was sent to which team and who stood by. A driver is named
    as the attendance module's own log lines name one: their mention, with the name a test
    driver goes by. A round whose call was never posted recorded nobody and is left out.
    """
    async with get_connection(db_path) as db:
        round_row = await (await db.execute(
            "SELECT round_number, track_name FROM rounds WHERE id = ? AND division_id = ?",
            (round_id, division.id),
        )).fetchone()
        if round_row is None:
            return ""
        rows = await (await db.execute(
            """
            SELECT dp.discord_user_id, dp.test_display_name,
                   dra.rsvp_status, dra.is_standby, ti.full_name AS team_name
              FROM driver_round_attendance dra
              JOIN driver_profiles dp ON dp.id = dra.driver_profile_id
              LEFT JOIN team_instances ti ON ti.id = dra.assigned_team_id
             WHERE dra.round_id = ? AND dra.division_id = ?
             ORDER BY CAST(dp.discord_user_id AS INTEGER)
            """,
            (round_id, division.id),
        )).fetchall()
    if not rows:
        return ""

    def _name(row) -> str:
        name = row["test_display_name"]
        return f"<@{row['discord_user_id']}>" + (f" ({name})" if name else "")

    lines = [
        f"\n  check-in, {division.name}, "
        f"{_round_label(round_row['round_number'], round_row['track_name'])}:"
    ]
    for status, label in _ANSWERS:
        named = [_name(r) for r in rows if r["rsvp_status"] == status]
        lines.append(f"\n    {label}: {', '.join(named) if named else 'none'}")
    reserves = [
        f"{_name(r)} to {r['team_name']}" if r["team_name"] else f"{_name(r)} on standby"
        for r in rows
        if r["team_name"] or r["is_standby"]
    ]
    if reserves:
        lines.append(f"\n    reserves: {', '.join(reserves)}")
    return "".join(lines)


#: How many failures a reply names before summing up the rest, and how long one line may run.
#: A reply is one Discord message, capped at 2,000 characters; a season of many divisions whose
#: every channel is gone, or one long error from Discord, would otherwise lose the whole reply.
#: The log channel carries every failure in full.
MAX_LINES = 8
MAX_LINE = 210


def failure_lines(failures: list[NoticeFailure]) -> str:
    """The part of a command's reply naming what could not be reached, or an empty string."""
    if not failures:
        return ""
    lines = []
    for failure in failures[:MAX_LINES]:
        text = failure.describe()
        if len(text) > MAX_LINE:
            text = text[: MAX_LINE - 1] + "…"
        lines.append(f"  • {text}")
    if len(failures) > MAX_LINES:
        lines.append(f"  • and {len(failures) - MAX_LINES} more; the log channel names them all")
    return "\n⚠️ **Not notified**\n" + "\n".join(lines)


def failure_log_lines(failures: list[NoticeFailure]) -> str:
    """Every failure, one line each, for the command's entry in the log channel."""
    return "".join(f"\n  not notified: {failure.describe()}" for failure in failures)
