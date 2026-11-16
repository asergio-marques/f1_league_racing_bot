"""Core's listings: the division list, the round list and the roster block, in core's own wording.

The Discord timestamp and the fenced listing split across Discord's message limit are not core's
wording but Discord's, and sit in `leaguebot.utils.messages`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from leaguebot.utils.messages import discord_ts

if TYPE_CHECKING:
    from leaguebot.core.models.division import Division
    from leaguebot.core.models.round import Round


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
