"""Message builders for forecast and log channel outputs.

All output is plain text (no embeds) per Constitution Principle VII.
"""

from __future__ import annotations


def phase1_message(division_role_id: int, track: str, rpc_pct: float) -> str:
    """Phase 1 forecast: rain probability preview (T−5 days)."""
    role_mention = f"<@&{division_role_id}>"
    pct = round(rpc_pct * 100, 1)
    return (
        f"{role_mention} 🏁 **Weather Forecast — Phase 1** (5 days out)\n"
        f"**Track**: {track}\n"
        f"**Rain Probability (Rpc)**: {pct}%\n"
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
        slot_str = " → ".join(f"*{s}*" for s in slots)
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


def _slot_icon(slot: str) -> str:
    return {"rain": "🌧️", "mixed": "🌥️", "sunny": "☀️"}.get(slot, "❓")


def session_type_label(session_type_value: str) -> str:
    """Convert a SessionType enum value to a human-readable label."""
    return session_type_value.replace("_", " ").title()
