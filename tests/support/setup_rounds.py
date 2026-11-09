"""A league whose season is being set up, holding divisions and their rounds as the database alone
holds them, for the tests of the commands that add rounds to it (`/round add`, `/round add-bulk`
and `/round add-xml`, #283).

The league is `settings_league`'s, no module on, its change queue's clock `NOW`, and the member
asking is its league manager Admin (4242), whom the log channel names `NAMED`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from leaguebot.core.db.database import get_connection

#: "Now", as the change queue's clock gives it.
NOW = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)

#: The log channel's name for the member asking: `member()`'s Admin (4242).
NAMED = "Admin (`<@4242>`)"

#: A division as (name, tier, role id, its rounds), each round as (format, track, moment in UTC).
Division = tuple[str, int, int, tuple[tuple[str, str | None, datetime], ...]]


async def setup_league(tmp_path: Any, *, stage: str | None = "PLACEMENTS",
                       divisions: tuple[Division, ...] = ()) -> Any:
    """`settings_league`'s league, no module on, its queue's clock `NOW`. With *stage*, season #1
    is being set up, in that stage, holding *divisions*, each round numbered by its moment and
    stored as a round of a setup is, its moment naive UTC in ISO form; with None, no season is
    being set up."""
    from tests.support.settings_league import settings_league

    league = await settings_league(tmp_path, modules=(), now=NOW)
    if stage is None:
        return league
    async with get_connection(league.db_path) as db:
        cursor = await db.execute(
            "INSERT INTO seasons (start_date, status, season_number, game_edition, stage) "
            "VALUES ('2026-10-01', 'SETUP', 1, 25, ?)",
            (stage,),
        )
        season_id = cursor.lastrowid
        for name, tier, role_id, rounds in divisions:
            cursor = await db.execute(
                "INSERT INTO divisions (season_id, name, mention_role_id, tier) "
                "VALUES (?, ?, ?, ?)",
                (season_id, name, role_id, tier),
            )
            division_id = cursor.lastrowid
            for number, (fmt, track, at) in enumerate(sorted(rounds, key=lambda r: r[2]), 1):
                await db.execute(
                    "INSERT INTO rounds (division_id, round_number, format, track_name, "
                    "scheduled_at) VALUES (?, ?, ?, ?, ?)",
                    (division_id, number, fmt, track, at.replace(tzinfo=None).isoformat()),
                )
        await db.commit()
    return league


async def round_rows(db_path: str, division: str) -> list[tuple[int, str, str | None, str]]:
    """The rounds of the division named *division*, as (number, format, track, moment as
    stored), by number."""
    async with get_connection(db_path) as db:
        cursor = await db.execute(
            "SELECT r.round_number, r.format, r.track_name, r.scheduled_at FROM rounds r "
            "JOIN divisions d ON d.id = r.division_id WHERE d.name = ? ORDER BY r.round_number",
            (division,),
        )
        return [tuple(row) for row in await cursor.fetchall()]


async def round_ids(db_path: str, division: str) -> dict[str, int]:
    """The id of each round of the division named *division*, by its moment as stored, and the
    division's own id under "division"."""
    async with get_connection(db_path) as db:
        cursor = await db.execute("SELECT id FROM divisions WHERE name = ?", (division,))
        ids = {"division": (await cursor.fetchone())[0]}
        cursor = await db.execute(
            "SELECT scheduled_at, id FROM rounds WHERE division_id = ?", (ids["division"],)
        )
        ids.update({at: round_id for at, round_id in await cursor.fetchall()})
        return ids


def refusal_line(what: str, reply: str) -> str:
    """The log channel's line for *what* (a command or a form, as the log names it) refused for
    Admin with *reply*."""
    from leaguebot.core.utils.log_lines import reply_reason

    return f"⛔ {what} refused for {NAMED} — {reply_reason(reply)}"


def acknowledged(doing: str) -> str:
    """The change queue's acknowledgement of a change it words as *doing*, before its first job."""
    return (
        f"⏳ {doing}. This message will be updated when it is done; if it takes longer, the log "
        "channel will say so."
    )


def repeated(doing: str) -> str:
    """The change queue's refusal of a change worded *doing*, asked again while the first waits."""
    return (
        f"⚠️ {doing} has already been asked for and has not started yet, so it was not asked "
        "for again."
    )


def no_room(problem: str, remedy: str) -> Any:
    """The image module's drawing, saying a calendar has no room: *problem* and *remedy*."""
    from unittest.mock import AsyncMock

    from leaguebot.core.services.image_drawing import CapacityProblem

    return AsyncMock(return_value=CapacityProblem(problem=problem, remedy=remedy))
