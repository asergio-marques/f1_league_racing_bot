"""Message builders for forecast and log channel outputs.

All output is plain text (no embeds) per Constitution Principle VII.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.division import Division
    from models.round import Round


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


def phase1_message(division_role_id: int, track: str, rpc_pct: float) -> str:
    """Phase 1 forecast: rain probability preview (T−5 days)."""
    role_mention = f"<@&{division_role_id}>"
    return (
        f"{role_mention} 🏁 **Weather Forecast — Phase 1** (5 days out)\n"
        f"**Track**: {track}\n"
        f"**Rain Probability**: {format_rain_probability(rpc_pct)}\n"
        f"A more detailed forecast will follow at T−2 days."
    )


def phase2_message(
    division_role_id: int,
    track: str,
    session_slots: list[tuple[str, str]],
) -> str:
    """Phase 2 forecast: session-level rain/mixed/sunny slot assignment (T−2 days).

    Args:
        session_slots: list of (session_type_label, slot_type) e.g. ('Qualifying', 'rain')
    """
    role_mention = f"<@&{division_role_id}>"
    lines = [
        f"{role_mention} 🏁 **Weather Forecast — Phase 2** (2 days out)",
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
    lines.append("\nFull slot-by-slot forecast will follow at T−2 hours.")
    return "\n".join(lines)


def phase3_message(
    division_role_id: int,
    track: str,
    session_weather: list[tuple[str, list[str]]],
) -> str:
    """Phase 3 forecast: slot-by-slot weather for all sessions (T−2 hours).

    Args:
        session_weather: list of (session_label, [weather_slot, ...])
    """
    role_mention = f"<@&{division_role_id}>"
    lines = [
        f"{role_mention} 🏁 **Final Weather Forecast — Phase 3** (2 hours out)",
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
    """Mystery round notice posted to the forecast channel at T−5 days.

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
