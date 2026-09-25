"""Message builders for forecast and log channel outputs.

All output is plain text (no embeds).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from leaguebot.core.models.division import Division
    from leaguebot.core.models.round import Round


def discord_ts(dt: datetime, fmt: str = "F") -> str:
    """Return a Discord dynamic timestamp string ``<t:UNIX:fmt>``.

    ``dt`` is assumed UTC if naïve.  ``fmt`` defaults to ``"F"``
    (long date + time, e.g. "Wednesday, 4 April 2026 20:00").
    Common format codes: ``F`` full, ``f`` short, ``R`` relative, ``D`` date.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"<t:{int(dt.timestamp())}:{fmt}>"


def format_rain_probability(rpc: float) -> str:
    """The likelihood of rain as a percentage — ``0.3047`` → ``"30%"``.

    Rounded to the **nearest whole number**, which is the rule the weather module has always
    carried and which this rendering did not honour: it produced one decimal place until
    2026-08-14, when the author ruled the textual form should round to the nearest integer as
    the graphic does.

    Half-up rather than Python's banker's rounding, so that the answer does not depend on
    which side of an even number a value happens to fall.

    This is the one rendering of the value. The forecast message and the phase 1, 2 and 3
    graphics all call it, so the picture and the message cannot disagree (Constitution
    XIV.7).
    """
    return f"{math.floor(rpc * 100 + 0.5)}%"


def format_session_weather_type(slot_type: str) -> str:
    """The type of weather drawn for a session — ``"mixed"`` → ``"Mixed"``.

    One of "Sunny", "Mixed" or "Rain". Shared by the phase 2 message and by the phase 2 and
    phase 3 graphics (Constitution XIV.7).
    """
    return str(slot_type).capitalize()


#: The description of the phase a forecast stands for — the one naming of a phase, shared by
#: the message and by the graphic drawn in its place (Constitution XIV.7).
#:
#: **No forecast names its own horizon** (issue #112, decided 2026-09-20). The wording once
#: read "(5 days out)", "(2 days out)" and "(2 hours out)", which was right only for a league
#: that had left all three deadlines at their defaults: the posts were built from string
#: literals and no caller passed the configured horizons in, so a league on seven days was
#: told five. Rather than substitute the configured numbers — a second rendering of the
#: deadlines beside the graphic's qualitative one, and every builder obliged to read the
#: configuration — a phase is named by what it is worth: an early figure, a first look at the
#: sessions, the final word. That holds at any deadline, which is why the graphics have always
#: described themselves this way and why the text now borrows their wording rather than
#: keeping a parallel set of its own.
#:
#: It lives here, beside the other shared renderings, because the image module imports from
#: this module and never the reverse; ``image_weather_service`` re-exports it.
PHASE_DESCRIPTIONS = {
    1: "Initial chance of rain",
    2: "Initial session forecast",
    3: "Final session forecast",
}


def phase1_message(division_role_id: int, track: str, rpc_pct: float) -> str:
    """Phase 1 forecast: the round's chance of rain, drawn at the phase 1 horizon.

    Titled by ``PHASE_DESCRIPTIONS[1]`` and naming no horizon of its own — see that constant
    for why. The promise of a further forecast stays, the weather specification requiring this
    message to indicate that a more detailed one follows; only the "at T−2 days" left it.
    """
    role_mention = f"<@&{division_role_id}>"
    return (
        f"{role_mention} 🏁 **Weather Forecast — {PHASE_DESCRIPTIONS[1]}**\n"
        f"**Track**: {track}\n"
        f"**Rain Probability**: {format_rain_probability(rpc_pct)}\n"
        f"A more detailed forecast will follow later."
    )


def phase2_message(
    division_role_id: int,
    track: str,
    session_slots: list[tuple[str, str]],
) -> str:
    """Phase 2 forecast: session-level rain/mixed/sunny slot assignment.

    Titled by ``PHASE_DESCRIPTIONS[2]`` and naming no horizon of its own — see that constant
    for why. Its closing line still points at the final forecast, calling it accurate and
    later rather than naming the hour it arrives.

    Args:
        session_slots: list of (session_type_label, slot_type) e.g. ('Qualifying', 'rain')
    """
    role_mention = f"<@&{division_role_id}>"
    lines = [
        f"{role_mention} 🏁 **Weather Forecast — {PHASE_DESCRIPTIONS[2]}**",
        f"**Track**: {track}",
        "",
        "**Session Overview**:",
    ]
    for session_label, slot in session_slots:
        icon = _slot_icon(slot)
        lines.append(
            f"  {icon} **{session_label}**: "
            f"{format_session_weather_type(slot)} conditions expected"
        )
    lines.append("\nAn accurate forecast will follow later.")
    return "\n".join(lines)


def phase3_message(
    division_role_id: int,
    track: str,
    session_weather: list[tuple[str, list[str]]],
) -> str:
    """Phase 3 forecast: slot-by-slot weather for all sessions.

    Titled by ``PHASE_DESCRIPTIONS[3]`` and naming no horizon of its own — see that constant
    for why. It carries no forward reference at all, being the last forecast a round receives,
    and the "Final" it once spelled out in its own heading now comes from the description.

    Args:
        session_weather: list of (session_label, [weather_slot, ...])
    """
    role_mention = f"<@&{division_role_id}>"
    lines = [
        f"{role_mention} 🏁 **Weather Forecast — {PHASE_DESCRIPTIONS[3]}**",
        f"**Track**: {track}",
        "",
        "**Slot-by-Slot Forecast**:",
    ]
    for session_label, slots in session_weather:
        slot_str = format_slots_for_forecast(slots)
        lines.append(f"  🏎️ **{session_label}**: {slot_str}")
    return "\n".join(lines)


def invalidation_message(track: str) -> str:
    """Broadcast message when prior weather results are invalidated by an amendment."""
    return (
        f"⚠️ **Weather Forecast Invalidated**\n"
        f"The configuration for **{track}** has been amended by an admin. "
        f"All previously published forecasts for this round have been invalidated. "
        f"An updated forecast will be posted automatically."
    )


def phase_log_message(
    phase_number: int,
    round_id: int,
    track: str,
    payload: dict,
) -> str:
    """Produce a structured log entry for the calculation log channel."""
    import json

    header = (
        f"📋 **Phase {phase_number} Calculation Log** | "
        f"Round #{round_id} | {track}"
    )
    body = json.dumps(payload, indent=2, default=str)
    return f"{header}\n```json\n{body}\n```"


def format_slot_sequence(slots: list[str]) -> str:
    """A session's Phase 3 slot sequence as a **value**, carrying no channel markup.

    Rules (FR-024, amended 2026-03-04):
    - Single slot (len == 1): the bare label; no arrow, no simplification marker.
    - All slots identical (len > 1, exact match): the single type label.
    - Otherwise: the slots joined by " → ".

    This is what a weather graphic draws. The italics the forecast message applies are an
    instruction to Discord rather than part of the value, so they are added by the message
    and never baked in here — Constitution XIV.16 (v4.7.0) puts the separation in the code
    that hands the value over, precisely so that no image type has to strip markup back out
    of a string it was given.
    """
    if len(slots) == 1:
        return slots[0]
    if len(set(slots)) == 1:
        return slots[0]
    return " → ".join(slots)


def format_slots_for_forecast(slots: list[str]) -> str:
    """The same sequence as the forecast **message** presents it, emphasis included.

    Identical to :func:`format_slot_sequence` but for italicising each entry of a sequence
    that varies. A session of one weather, or of one slot, carries no emphasis in either.
    """
    if len(slots) == 1:
        return slots[0]
    if len(set(slots)) == 1:
        return slots[0]
    return " → ".join(f"*{s}*" for s in slots)


def format_slots_for_log(slots: list[str]) -> str:
    """Format a session's Phase 3 slot sequence for the calculation log channel.

    Rules (FR-024, amended 2026-03-04):
    - Single slot (len == 1): return the bare label verbatim.
    - All slots identical (len > 1, exact match): return
      "<type> (draws: <slot>, <slot>, ...)".
    - Otherwise: return slots joined by " → " (no italics needed for log).
    """
    if len(slots) == 1:
        return slots[0]
    if len(set(slots)) == 1:
        raw = ", ".join(slots)
        return f"{slots[0]} (draws: {raw})"
    return " → ".join(slots)


def mystery_notice_message() -> str:
    """Mystery round notice posted to the forecast channel at the phase 1 horizon.

    No division role is tagged — conditions are unknown to all participants;
    weather will be set by the game at race time, not pre-determined by the bot.
    """
    return (
        "\U0001f3c1 **Weather Forecast**\n"
        "**Track**: Mystery\n"
        "Conditions are unknown to all \u2014 weather will be determined by the game at race time."
    )


def _slot_icon(slot: str) -> str:
    return {"rain": "🌧️", "mixed": "🌦️", "sunny": "☀️"}.get(slot, "❓")


def session_type_label(session_type_value: str) -> str:
    """Convert a SessionType enum value to a human-readable label.

    Strips the leading length qualifier (Short / Long / Full) so outputs read
    e.g. 'Sprint Qualifying' rather than 'Short Sprint Qualifying'.
    """
    label = session_type_value.replace("_", " ").title()
    for prefix in ("Short ", "Long ", "Full "):
        if label.startswith(prefix):
            return label[len(prefix):]
    return label


def format_division_list(divisions: "list[Division]") -> str:
    """Format a list of Division objects as a readable summary.

    Returns one line per division showing name, tier (if set), role mention, and forecast channel.
    """
    if not divisions:
        return "*(no divisions)*"
    lines = ["**Divisions:**"]
    for div in divisions:
        tier_tag = f" (Tier {div.tier})" if div.tier > 0 else ""
        lines.append(
            f"  📂 **{div.name}**{tier_tag} | <@&{div.mention_role_id}>"
        )
    return "\n".join(lines)


def format_round_list(rounds: "list[Round]") -> str:
    """Format a list of Round objects as a readable summary.

    Returns one line per round showing number, format, track, and datetime.
    """
    if not rounds:
        return "*(no rounds)*"
    lines = ["**Rounds:**"]
    for r in rounds:
        track = r.track_name or "TBD"
        status_tag = " ~~[CANCELLED]~~" if r.status == "CANCELLED" else ""
        lines.append(
            f"  Round {r.round_number}: {r.format.value} @ {track}"
            f" — {discord_ts(r.scheduled_at)}{status_tag}"
        )
    return "\n".join(lines)


def format_roster_block(teams: "list[dict]") -> str:
    """Format a team-roster block for a single division.

    Args:
        teams: list of dicts with keys: name, max_seats, is_reserve, seats
               (seats is a list of dicts with keys: seat_number, driver_profile_id)

    Returns a multi-line string suitable for embedding in a review message.
    """
    if not teams:
        return "  *(no teams seeded)*"
    lines = ["  **Teams:**"]
    for team in teams:
        if team.get("is_reserve"):
            lines.append(f"    🏎️ **{team['name']}** — (no seats pre-assigned)")
        else:
            seats = team.get("seats", [])
            seat_parts = []
            for seat in sorted(seats, key=lambda s: s["seat_number"]):
                driver_id = seat.get("driver_profile_id")
                if driver_id:
                    seat_parts.append(f"Seat {seat['seat_number']}: <@{driver_id}>")
                else:
                    seat_parts.append(f"Seat {seat['seat_number']}: unassigned")
            seats_str = " | ".join(seat_parts) if seat_parts else "no seats"
            lines.append(f"    🏎️ **{team['name']}** — {seats_str}")
    return "\n".join(lines)


#: Discord refuses a message body over 2000 characters with a 400. Pages are built to a
#: lower ceiling so that the code fence, the continuation header and the trailing newline
#: a page carries all fit inside the real limit rather than pushing it over.
DISCORD_MESSAGE_LIMIT = 2000


def paginate_fenced(
    header: str,
    body_lines: "list[str]",
    footer: str = "",
    limit: int = DISCORD_MESSAGE_LIMIT,
) -> "list[str]":
    """Split a fenced listing into messages that each fit Discord's limit.

    A listing that grows with the league — a roster, a division's drivers — outgrows one
    message eventually, and Discord answers the whole command with a 400 rather than
    truncating, so the manager gets no listing at all. Splitting on whole lines keeps
    each page a valid table: a page break inside a row, or inside the ``` fence, would
    render as garbage.

    *header* is repeated on every page so a page read on its own still says what it is;
    *footer* is placed on the last page only, since it usually explains what to do with
    the whole listing. Both sit outside the fence. A single line too long to fit even
    alone is emitted on its own page rather than dropped — it will be truncated by
    Discord, but one mangled row beats losing the listing.

    Returns at least one page, even for an empty *body_lines*, so a caller can always
    send ``pages[0]``.
    """
    fence_cost = len("```\n\n```")
    pages: list[str] = []
    current: list[str] = []

    def _render(lines: list[str], with_footer: bool) -> str:
        page = f"{header}\n```\n" + "\n".join(lines) + "\n```"
        if with_footer and footer:
            page += f"\n{footer}"
        return page

    # The footer only ever lands on the final page, but its length has to be reserved
    # while filling, or the last page overflows exactly when the footer is added.
    budget = limit - len(header) - fence_cost - 1
    footer_budget = budget - (len(footer) + 1 if footer else 0)

    for line in body_lines:
        candidate = current + [line]
        used = sum(len(item) + 1 for item in candidate)
        if current and used > budget:
            pages.append(_render(current, with_footer=False))
            current = [line]
        else:
            current = candidate

    # Whatever is left is the last page, and it is the one carrying the footer — so it is
    # measured against the tighter budget and split once more if the footer will not fit.
    used = sum(len(item) + 1 for item in current)
    if current and used > footer_budget and len(current) > 1:
        pages.append(_render(current[:-1], with_footer=False))
        current = current[-1:]

    pages.append(_render(current, with_footer=True))
    return pages
