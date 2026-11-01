"""Core's text helpers: the Discord timestamp and the listings core's messages share.

A division list, a round list, a roster block and a fenced listing split across Discord's
message limit are core's own wording, used by core's commands, by signup and by the
weather module's and the entry point's callers alike.  They sit here, in core, so that no
module reaches into another for them.
"""

from __future__ import annotations

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
