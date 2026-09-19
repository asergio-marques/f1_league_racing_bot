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

**Every send is on its own.** One failure never stops another, nor the cancellation that
called it: the cancellation has already been decided and recorded, or is about to be, and
losing a notice is not a reason to undo it. What failed is returned so the command can name
it to the admin who ran it, as well as being logged; a missed notice that only reached the
bot's own log was the second half of #175.
"""
from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass

from db.database import get_connection
from models.round import RoundStatus

log = logging.getLogger(__name__)

SCOPE_ROUND = "round"
SCOPE_DIVISION = "division"
SCOPE_SEASON = "season"


@dataclass
class CancellationReport:
    """What announcing a cancellation left for the command to report."""

    #: Every place the cancellation could not reach, for the admin's reply and the log.
    failures: list["NoticeFailure"] = dataclasses.field(default_factory=list)
    #: The log-channel audit of each cancelled round's check-in; empty where there is none.
    audit: str = ""


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


# ── The calendar ──────────────────────────────────────────────────────────


async def refresh_division_calendar(
    bot,
    guild,
    division,
    *,
    season_number=None,
    round_ids: frozenset[int] = frozenset(),
) -> str | None:
    """Post *division*'s calendar again, as it now stands. Returns what went wrong, or None.

    *round_ids* names the rounds the cancellation calls off, drawn as cancelled whether or not
    they are recorded so yet. A season
    is cancelled by a cascade, and its calendars must be refreshed **before** it — once the
    season is recorded cancelled its channels are no longer read — so the rounds the cascade
    is about to call off are named here instead.

    A division whose calendar was never posted is left alone: there is nothing to bring up
    to date, and posting a first calendar is the approval's to do, not a cancellation's.

    Not a commanded posting. The command asked for the cancellation, not the calendar, so a
    graphic that cannot be drawn falls back to text as it does at approval (XIV.7).
    """
    if not getattr(division, "calendar_message_id", None):
        return None

    from services import calendar_post_service as calendar

    rounds = await bot.season_service.get_division_rounds(division.id)
    if round_ids:
        rounds = [
            dataclasses.replace(r, status=RoundStatus.CANCELLED.value)
            if r.id in round_ids
            else r
            for r in rounds
        ]
    tracks = await calendar.tracks_by_name(bot.db_path)
    posting = await calendar.post_division_calendar(
        bot, guild, division, rounds, tracks, season_number=season_number
    )
    if posting.message_id is None:
        return posting.problem or "the calendar could not be posted"
    if posting.fell_back:
        # Posted, but not as the league asked: the picture could not be drawn and the text
        # stands in. Worth the admin's knowing, since the calendar now looks different.
        return f"posted as text, as the picture could not be drawn ({posting.problem})"
    return None


# ── The one entry point ───────────────────────────────────────────────────


async def _module_channels(bot, division_id: int) -> dict[str, int | None]:
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
    """Send *content* to *channel_id*. Returns what went wrong, or None."""
    if not channel_id:
        return "no channel is set"
    channel = guild.get_channel(int(channel_id)) if guild is not None else None
    if channel is None:
        return "the channel could not be found"
    try:
        await channel.send(content, **kwargs)
    except Exception as exc:  # noqa: BLE001 — one notice never stops another
        return f"the message could not be posted ({exc})"
    return None


#: The order the audit lists the answers in, and the words it lists them under.
_ANSWERS = (
    ("ACCEPTED", "accepted"),
    ("TENTATIVE", "tentative"),
    ("DECLINED", "declined"),
    ("NO_RSVP", "no answer"),
)


async def _checkin_audit(bot, division, round_id: int) -> str:
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
                   dra.rsvp_status, dra.is_standby, ti.name AS team_name
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


async def _withdraw_call(bot, round_id: int, division_id: int) -> str | None:
    """Take down *round_id*'s check-in call in *division_id*, if one stands. Returns what went
    wrong, or None.

    `withdraw_rsvp_call` is what an amendment uses to take a call down. It deletes the call,
    its last notice and its distribution announcement, and the row recording which messages
    those were — the row being read elsewhere as "the call now standing", by the restart that
    re-arms its buttons among others, so it must not outlive the call. The answers in
    `driver_round_attendance` are not touched.
    """
    from services.rsvp_service import withdraw_rsvp_call

    undeleted: list[str] = []
    try:
        await withdraw_rsvp_call(round_id, division_id, bot, undeleted=undeleted)
    except Exception as exc:  # noqa: BLE001 — one call never stops the rest
        log.exception("cancellation notice: could not take down the call of round %s", round_id)
        return f"could not be taken down ({exc})"
    if undeleted:
        # Left standing, so a league manager must delete it by hand; its buttons refuse a
        # cancelled round's answers in the meantime.
        return (
            f"{len(undeleted)} message(s) could not be deleted and must be removed by hand "
            f"(ids {', '.join(undeleted)})"
        )
    return None


async def announce_cancellation(
    bot,
    guild,
    divisions,
    *,
    scope: str,
    round_number: int | None = None,
    track_name: str | None = None,
    season_number=None,
    round_ids: frozenset[int] = frozenset(),
) -> CancellationReport:
    """Have each enabled module say what the cancellation means for it, in each division.

    *divisions* are those told: the one a round or division belongs to, or every division of
    a season still running. *round_ids* are the rounds this cancellation calls off — the one
    round, or every round of the division or season whose results are not yet in. Returns
    every place that could not be reached, and the audit of those rounds' check-ins.

    **Never raises.** It is called in the middle of a cancellation — before the cascade, for a
    season — and an exception escaping it would stop the cancellation part-done, its jobs gone
    and its records untouched. Whatever goes wrong is returned as a failure instead.
    """
    report = CancellationReport()
    try:
        await _announce(
            bot, guild, divisions, report,
            scope=scope, round_number=round_number, track_name=track_name,
            season_number=season_number, round_ids=frozenset(round_ids),
        )
    except Exception as exc:  # noqa: BLE001 — see the docstring
        log.exception("cancellation notice: the announcement raised")
        report.failures.append(NoticeFailure("Every division", "the announcement", str(exc)))
    return report


async def _announce(
    bot, guild, divisions, report: CancellationReport, *,
    scope, round_number, track_name, season_number, round_ids,
) -> None:
    import discord

    enabled = {
        "weather": await bot.module_service.is_weather_enabled(),
        "results": await bot.module_service.is_results_enabled(),
        "attendance": await bot.module_service.is_attendance_enabled(),
    }

    def _fail(division, target: str, reason: str | None) -> None:
        if reason is None:
            return
        log.warning("cancellation notice: %s — %s: %s", division.name, target, reason)
        report.failures.append(NoticeFailure(division.name, target, reason))

    words = dict(round_number=round_number, track_name=track_name)
    for division in divisions:
        try:
            channels = await _module_channels(bot, division.id)
        except Exception as exc:  # noqa: BLE001 — one division never stops the next
            log.exception("cancellation notice: could not read %s's channels", division.name)
            _fail(division, "its module channels", f"could not be read ({exc})")
            channels = None

        if channels is not None and enabled["attendance"]:
            role_id = getattr(division, "mention_role_id", None)
            ping = f"<@&{role_id}>\n" if role_id else ""
            _fail(division, "check-in channel", await _send(
                guild,
                channels["attendance"],
                ping + attendance_notice(scope, division.name, **words),
                allowed_mentions=discord.AllowedMentions(roles=bool(role_id)),
            ))
        if enabled["attendance"]:
            # Each round called off has its check-in written into the log first, so the answers
            # its call gathered stand on record beside the cancellation (decided 2026-09-19).
            # Then the call comes down with its last notice and its distribution announcement,
            # whether or not the notice above could be posted: it has nothing left to ask, and
            # its buttons would go on recording answers to it. The answers are kept.
            for round_id in sorted(round_ids):
                try:
                    block = await _checkin_audit(bot, division, round_id)
                except Exception as exc:  # noqa: BLE001 — the audit never stops the rest
                    log.exception("cancellation notice: could not audit round %s", round_id)
                    _fail(division, "check-in audit", f"could not be read ({exc})")
                else:
                    report.audit += block
                _fail(division, "check-in call", await _withdraw_call(bot, round_id, division.id))
        if channels is not None and enabled["weather"]:
            _fail(division, "forecast channel", await _send(
                guild,
                channels["weather"],
                weather_note(scope, division.name, **words),
                silent=True,
            ))
        if channels is not None and enabled["results"]:
            _fail(division, "results channel", await _send(
                guild,
                channels["results"],
                results_note(scope, division.name, **words),
                silent=True,
            ))

        # The calendar is core's and needs none of the module channels above.
        try:
            reason = await refresh_division_calendar(
                bot, guild, division,
                season_number=season_number,
                round_ids=round_ids,
            )
        except Exception as exc:  # noqa: BLE001 — the calendar never stops the cancellation
            log.exception("cancellation notice: calendar refresh raised for %s", division.name)
            reason = str(exc)
        _fail(division, "calendar", reason)


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
