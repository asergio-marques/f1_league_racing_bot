"""Message builders for forecast and log channel outputs.

All output is plain text (no embeds) per Constitution Principle VII.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from models.division import Division
    from models.round import Round


def phase1_message(division_role_id: int, track: str, rpc_pct: float) -> str:
    """Phase 1 forecast: rain probability preview (T−5 days)."""
    role_mention = f"<@&{division_role_id}>"
    pct = round(rpc_pct * 100, 1)
    return (
        f"{role_mention} 🏁 **Weather Forecast — Phase 1** (5 days out)\n"
        f"**Track**: {track}\n"
        f"**Rain Probability**: {pct}%\n"
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
        lines.append(f"  {icon} **{session_label}**: {slot.capitalize()} conditions expected")
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


def format_slots_for_forecast(slots: list[str]) -> str:
    """Format a session's Phase 3 slot sequence for the forecast channel.

    Rules (FR-024, amended 2026-03-04):
    - Single slot (len == 1): return the bare label; no arrow, no simplification marker.
    - All slots identical (len > 1, exact match): return the single type label.
    - Otherwise: return slots joined by " → " with each entry in italics.
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

    Returns one line per division showing name, role mention, and forecast channel.
    """
    if not divisions:
        return "*(no divisions)*"
    lines = ["**Divisions:**"]
    for div in divisions:
        lines.append(
            f"  📂 **{div.name}** | <@&{div.mention_role_id}> | <#{div.forecast_channel_id}>"
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
            f" — {r.scheduled_at.isoformat()} UTC{status_tag}"
        )
    return "\n".join(lines)
