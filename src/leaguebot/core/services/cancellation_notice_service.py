"""What each module says when a round, a division or a season is called off (#175).

Core announces nothing of its own. Telling the drivers a race is off is the league's to do;
what the bot owes them is that no module goes on talking about a round that will not be run,
and that the calendar stops showing it as though it would be. So a cancellation:

- **attendance** — posts the one real notification, to the division's check-in channel,
  mentioning the division role as the check-in call does. The check-in channel is where
  drivers answer whether they are racing, so it is where they learn they need not. The call
  standing for a round called off is then taken down, its answers kept;
- **weather** — posts a note to the forecast channel that no forecast is coming;
- **results** — posts a note to the results channel that no results are coming;
- **the calendar** — is posted again with the round struck through, or veiled in the
  graphic.

The weather and results notes are sent as Discord **silent** messages: they sit in the
channel for anyone reading it and ping nobody, the attendance notification being the one a
driver is told by. Each module speaks only while it is enabled — a disabled module produces
nothing, whatever the path arrives at it (core specification) — and the calendar, owned by
core, is refreshed whatever the modules.

**Every send is a job of its own.** A season's, a division's and a round's cancellation all run
on the change queue (`cancellation_changes`), where each notice (`post_module_notice`) and each
call taken down (`take_down_call`) is a job: one Discord refuses stops the queue until it is
retried or discarded, and is named only once discarded. So each raises, turning only a failure
Discord caused into `StepFailedOnDiscord` and letting a fault of the bot's own through unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.change import StepFailedOnDiscord
from leaguebot.core.utils.league_bot import LeagueBot

SCOPE_ROUND = "round"
SCOPE_DIVISION = "division"
SCOPE_SEASON = "season"


@dataclass(frozen=True)
class NoticeFailure:
    """One place a cancellation could not reach."""

    division_name: str
    #: What was not reached, in the words the command's reply uses.
    target: str
    reason: str

    def describe(self) -> str:
        return f"**{self.division_name}** — {self.target}: {self.reason}"


# ── The wording ───────────────────────────────────────────────────────────


def _round_label(round_number: int, track_name: str | None) -> str:
    return f"Round {round_number} ({track_name or 'Mystery'})"


def weather_note(scope: str, division_name: str, round_number=None, track_name=None) -> str:
    if scope == SCOPE_ROUND:
        return (
            f"\U0001f4e2 **Round {round_number} Cancelled: {division_name}**\n"
            f"{_round_label(round_number, track_name)} has been cancelled. "
            "No weather forecast will be posted for it."
        )
    if scope == SCOPE_DIVISION:
        return (
            f"\U0001f4e2 **Division Cancelled: {division_name}**\n"
            "No further weather forecasts will be posted for this division."
        )
    return (
        "\U0001f4e2 **Season Cancelled**\n"
        "No further weather forecasts will be posted this season."
    )


def results_note(scope: str, division_name: str, round_number=None, track_name=None) -> str:
    if scope == SCOPE_ROUND:
        return (
            f"\U0001f4e2 **Round {round_number} Cancelled: {division_name}**\n"
            f"{_round_label(round_number, track_name)} has been cancelled. "
            "No results will be posted for it."
        )
    if scope == SCOPE_DIVISION:
        return (
            f"\U0001f4e2 **Division Cancelled: {division_name}**\n"
            "No further results will be posted for this division."
        )
    return (
        "\U0001f4e2 **Season Cancelled**\n"
        "No further results will be posted this season."
    )


def attendance_notice(scope: str, division_name: str, round_number=None, track_name=None) -> str:
    if scope == SCOPE_ROUND:
        return (
            f"\U0001f4e2 **Round {round_number} Cancelled: {division_name}**\n"
            f"{_round_label(round_number, track_name)} has been cancelled. "
            "There is no check-in to answer for it, and nobody is charged for it."
        )
    if scope == SCOPE_DIVISION:
        return (
            f"\U0001f4e2 **Division Cancelled: {division_name}**\n"
            "This division has been cancelled. There are no further check-ins to answer."
        )
    return (
        "\U0001f4e2 **Season Cancelled**\n"
        "The season has been cancelled. There are no further check-ins to answer."
    )


# ── The posts and take-downs, each a job ───────────────────────────────────────────────────


async def _module_channels(bot: LeagueBot, division_id: int) -> dict[str, int | None]:
    """The division's forecast, results and check-in channels, whatever table holds each."""
    async with get_connection(bot.db_path) as db:
        cursor = await db.execute(
            """
            SELECT d.forecast_channel_id,
                   drc.results_channel_id,
                   adc.rsvp_channel_id
              FROM divisions d
              LEFT JOIN division_results_config drc ON drc.division_id = d.id
              LEFT JOIN attendance_division_config adc ON adc.division_id = d.id
             WHERE d.id = ?
            """,
            (division_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return {"weather": None, "results": None, "attendance": None}
    return {
        "weather": row["forecast_channel_id"],
        "results": row["results_channel_id"],
        "attendance": row["rsvp_channel_id"],
    }


async def _send(guild, channel_id, content: str, **kwargs) -> str | None:
    """Send *content* to *channel_id*, as a job on the change queue. Returns what went wrong, or None.

    A channel never set is returned as "no channel is set": there is nothing to retry. A channel
    that is set and no longer on the server raises `StepFailedOnDiscord` (no cause), and a send
    Discord refuses raises it `from` the `discord.HTTPException`; any other exception is a fault
    of the bot's own and propagates unchanged.
    """
    if not channel_id:
        return "no channel is set"
    channel = guild.get_channel(int(channel_id)) if guild is not None else None
    if channel is None:
        raise StepFailedOnDiscord(f"the channel <#{channel_id}> is no longer on the server")
    try:
        await channel.send(content, **kwargs)
    except discord.HTTPException as exc:
        raise StepFailedOnDiscord(
            f"the message could not be posted to <#{channel_id}>: {exc}"
        ) from exc
    return None


async def post_module_notice(
    bot: LeagueBot,
    guild,
    division,
    module: str,
    *,
    scope: str,
    round_number: int | None = None,
    track_name: str | None = None,
) -> str | None:
    """Post *module*'s ("attendance", "weather" or "results") notice of a cancellation to
    *division*'s channel for it, in the cancellation's words, and raise on a
    failure Discord caused (`_send`). Returns "no channel is set" where the division has none.

    The caller has decided that the module is on; this does not look.
    """
    channels = await _module_channels(bot, division.id)
    words = dict(round_number=round_number, track_name=track_name)
    if module == "attendance":
        role_id = getattr(division, "mention_role_id", None)
        ping = f"<@&{role_id}>\n" if role_id else ""
        return await _send(
            guild,
            channels["attendance"],
            ping + attendance_notice(scope, division.name, **words),
            allowed_mentions=discord.AllowedMentions(roles=bool(role_id)),
        )
    if module == "weather":
        note = weather_note(scope, division.name, **words)
    elif module == "results":
        note = results_note(scope, division.name, **words)
    else:
        raise ValueError(f"no cancellation notice for module {module!r}")
    return await _send(guild, channels[module], note, silent=True)


#: The order the audit lists the answers in, and the words it lists them under.
_ANSWERS = (
    ("ACCEPTED", "accepted"),
    ("TENTATIVE", "tentative"),
    ("DECLINED", "declined"),
    ("NO_RSVP", "no answer"),
)


async def _checkin_audit(bot: LeagueBot, division, round_id: int) -> str:
    """The log-channel record of *round_id*'s check-in in *division*, or "" where it had none.

    Every driver the check-in recorded, grouped by their answer, and — where the reserves had
    already been distributed — who was sent to which team and who stood by. A driver is named
    as the attendance module's own log lines name one: their mention, with the name a test
    driver goes by. A round whose call was never posted recorded nobody and is left out.
    """
    async with get_connection(bot.db_path) as db:
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


async def take_down_call(bot: LeagueBot, division, round_id: int) -> dict:
    """Read *round_id*'s check-in, then take its call down, as a job on the change queue.

    Returns `{"audit", "taken_down"}`: the log-channel record of the check-in (`_checkin_audit`),
    read **before** the call comes down, and whether a call stood. Where a message cannot be
    deleted it raises `StepFailedOnDiscord` whose `result` is `{"audit", "undeleted"}`, so the
    audit is kept on the job whether it goes through or is discarded, and the call's record
    stays for the next try, which reads the audit again. The raise is `from` the Discord failure
    that caused it, as `withdraw_rsvp_call` raises it.
    """
    from leaguebot.attendance.services.rsvp_service import withdraw_rsvp_call

    audit = await _checkin_audit(bot, division, round_id)
    try:
        taken_down = await withdraw_rsvp_call(round_id, division.id, bot, raise_on_failure=True)
    except StepFailedOnDiscord as exc:
        undeleted = (exc.result or {}).get("undeleted", [])
        raise StepFailedOnDiscord(
            exc.reason, result={"audit": audit, "undeleted": undeleted}
        ) from exc.__cause__
    return {"audit": audit, "taken_down": taken_down}


#: How many failures a reply names before summing up the rest, and how long one line may run.
#: A reply is one Discord message, capped at 2,000 characters; a season of many divisions whose
#: every channel is gone, or one long error from Discord, would otherwise lose the whole reply.
#: The log channel carries every failure in full.
MAX_LINES = 8
MAX_LINE = 180


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
