"""The reads a season's approval makes of the league, outside the cog (#439, slice 4a).

The approval is judged twice: at the press, by the season cog, and again when it comes up to run
on the change queue, by the change type, which may import no cog. So the two reads both make, the
channels a division posts to and the signups not yet settled, live here, and the cog's methods
call them; the same question is then answered the same way at both. `not_done_section` is the
reply's list of what a confirmation could not do.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.services.approval_window_service import (
    AttendanceWindows,
    WeatherWindows,
    calendar_faults,
)

log = logging.getLogger(__name__)


def not_done_section(not_done: list[str]) -> str:
    """What a placements confirmation could not do, as a section of its reply (#387)."""
    if not not_done:
        return ""
    return "\n\n⚠️ **Not everything could be done**\n" + "\n".join(
        f"• {line}" for line in not_done
    )


@dataclass(frozen=True)
class DatesRefusal:
    """A calendar holding dates gone by, as a member is told it (*reply*) and as the log's
    refusal line states it (*reason*)."""

    reply: str
    reason: str


def _stamp(moment: datetime) -> str:
    """A Discord dynamic timestamp, full form, a naive moment taken as UTC. Written here and not
    imported from the weather module's message builder, which core may not reach from a service."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return f"<t:{int(moment.timestamp())}:F>"


def date_refusal(
    divisions: list[Any],
    rounds_by_division: dict[int, list[Any]],
    *,
    now: datetime,
    attendance: AttendanceWindows | None,
    weather: WeatherWindows | None,
) -> DatesRefusal | None:
    """The refusal for a season whose calendar holds a round already run or inside a window, or
    None where every division's calendar is clean (Gate 2d, #121, #122, #181).

    **Both confirmations of a season's placements ask this**: the press, and the change queue's
    check as the approval comes up to run, where the clock has moved on. One bullet per division,
    and at most two findings within it. Each division's rounds are judged by `calendar_faults`.
    """
    problems: list[str] = []
    for division in divisions:
        fault = calendar_faults(
            rounds_by_division[division.id], now=now, attendance=attendance, weather=weather
        )
        if fault is None:
            continue
        bits = []
        if fault.latest_past is not None:
            bits.append(
                f"Round {fault.latest_past.round_number} has already run "
                f"({_stamp(fault.latest_past.scheduled_at)}), and so has every "
                f"round before it"
            )
        if fault.latest_window is not None:
            bits.append(
                f"Round {fault.latest_window.round_number} is inside its "
                f"{fault.latest_window.label.lower()}, due "
                f"{_stamp(fault.latest_window.fire_at)} "
                f"({fault.latest_window.lead})"
            )
        problems.append(f"• **{division.name}** — " + "; ".join(bits) + ".")
    if not problems:
        return None
    body = "\n".join(problems)
    return DatesRefusal(
        reply=(
            f"❌ Season cannot be approved — its calendar holds dates that have "
            f"already gone by:\n{body}\n"
            f"Move those rounds with `/round amend`, or shorten the windows, then run "
            f"`/season placements-review` again. **Nothing has been approved.**"
        ),
        reason=f"its calendar holds dates that have already gone by:\n{body}",
    )


async def channel_on_server(guild, channel_id: int) -> bool:
    """Whether *channel_id* is still a channel of *guild*, only NotFound answering no (#374).

    The cache is asked first and the API second, the cache holding no channel it has not
    seen. Any failure but NotFound answers yes: a check that could not tell must not be the
    thing that refuses a season.
    """
    if guild.get_channel(channel_id) is not None:
        return True
    try:
        await guild.fetch_channel(channel_id)
    except discord.NotFound:
        return False
    except Exception as exc:  # noqa: BLE001 — cannot tell, so not a fault
        log.warning("channel check: could not fetch channel %s: %s", channel_id, exc, exc_info=True)
    return True


#: Every channel a division posts to, in the order a manager reads them: the column that
#: holds it, what a league calls it, the command that sets it, and the module that needs
#: it — None where every season does.
DIVISION_CHANNELS = (
    ("lineup_channel_id", "lineup channel", "/division lineup-channel", None),
    ("calendar_channel_id", "calendar channel", "/division calendar-channel", None),
    ("forecast_channel_id", "weather channel", "/weather channel", "weather"),
    ("results_channel_id", "results channel", "/results channel results", "results"),
    ("standings_channel_id", "standings channel", "/results channel standings", "results"),
    ("penalty_channel_id", "verdicts channel", "/results channel verdicts", "results"),
    ("rsvp_channel_id", "RSVP channel", "/attendance channel rsvp", "attendance"),
    (
        "attendance_channel_id",
        "attendance channel",
        "/attendance channel attendance",
        "attendance",
    ),
)


async def division_channel_faults(modules, db_path: str, season_id: int, guild=None) -> list[str]:
    """Every channel a division of the season posts to that is not set, or not on the server.

    **Both confirmations ask this** (#374): the first, and the one mid-season. Nothing
    clears a channel's id when Discord deletes the channel, and no module can be enabled
    once a season is under way, so mid-season every channel is still *set*; the question
    worth asking is whether it is still *there*. Asked at both, so the two cannot come to
    disagree about what a division needs.

    A channel is looked for in the cache and then fetched, a fetch answering NotFound
    being the one proof it is gone. Any other failure is **not** a fault: a check that
    could not tell must not be what refuses a season. With no *guild*, only whether each
    channel is set is judged. A cancelled division posts nothing and is passed over.
    """
    enabled = {
        None: True,
        "weather": await modules.is_weather_enabled(),
        "results": await modules.is_results_enabled(),
        "attendance": await modules.is_attendance_enabled(),
    }
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT d.name, d.lineup_channel_id, d.calendar_channel_id, "
            "       d.forecast_channel_id, rc.results_channel_id, "
            "       rc.standings_channel_id, rc.penalty_channel_id, "
            "       ac.rsvp_channel_id, ac.attendance_channel_id "
            "FROM divisions d "
            "LEFT JOIN division_results_config rc ON rc.division_id = d.id "
            "LEFT JOIN attendance_division_config ac ON ac.division_id = d.id "
            "WHERE d.season_id = ? AND d.status != 'CANCELLED' ORDER BY d.tier",
            (season_id,),
        )
        rows = await cursor.fetchall()

    faults: list[str] = []
    for row in rows:
        for column, label, command, module in DIVISION_CHANNELS:
            if not enabled[module]:
                continue
            value = row[column]
            if not value:
                faults.append(f"**{row['name']}** has no {label} — `{command}`.")
            elif guild is not None and not await channel_on_server(guild, int(value)):
                faults.append(
                    f"**{row['name']}**'s {label} is no longer on the server — `{command}`."
                )
    return faults


async def unsettled_signups(db_path: str) -> list[str]:
    """The signups not yet settled, named: what the review and the confirmation both list (#220)."""
    from leaguebot.core.services.driver_service import DRIVERS_SIGNUP_OF_DP_SQL
    from leaguebot.core.services.season_lifecycle_service import UNSETTLED_STATES

    placeholders = ",".join("?" for _ in UNSETTLED_STATES)
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            f"SELECT dp.discord_user_id, dp.current_state, sr.server_display_name "
            f"FROM driver_profiles dp "
            f"LEFT JOIN signup_records sr ON sr.id = {DRIVERS_SIGNUP_OF_DP_SQL} "
            f"WHERE dp.current_state IN ({placeholders}) "
            f"ORDER BY dp.current_state, dp.discord_user_id",
            (*UNSETTLED_STATES,),
        )
        unsettled_rows = await cursor.fetchall()

    state_labels = {
        "UNASSIGNED": "not yet placed",
        "PENDING_ADMIN_APPROVAL": "awaiting approval",
        "AWAITING_CORRECTION_PARAMETER": "awaiting approval",
        "PENDING_DRIVER_CORRECTION": "correcting their signup",
    }
    return [
        f"**{row['server_display_name'] or row['discord_user_id']}** — "
        f"{state_labels.get(row['current_state'], row['current_state'])}"
        for row in unsettled_rows
    ]
