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


async def round_moments(db_path: str, division: str) -> list[tuple[int, str, str | None, datetime]]:
    """The rounds of the division named *division*, as `round_rows` gives them, each moment read
    as UTC, whether it is stored with its zone or without."""
    moments = []
    for number, fmt, track, at in await round_rows(db_path, division):
        moment = datetime.fromisoformat(at)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        moments.append((number, fmt, track, moment.astimezone(timezone.utc)))
    return moments


async def ask_duplicate(league: Any, source: str = "Pro", new_name: str = "Am", tier: int = 2, *,
                        role_id: int = 556, day_offset: int = 0,
                        hour_offset: float = 0.0) -> Any:
    """`/division duplicate` of *source* as *new_name* at *tier*, mentioning role *role_id*, its
    rounds shifted by *day_offset* days and *hour_offset* hours, as the league manager Admin
    (4242) runs it: the interaction."""
    from unittest.mock import MagicMock

    import discord

    from leaguebot.core.cogs.division_cog import DivisionCog
    from tests.support.settings_league import interaction
    from tests.support.undecorate import undecorate

    role = MagicMock(spec=discord.Role)
    role.id = role_id
    role.mention = f"<@&{role_id}>"
    asked = interaction(league.bot, "division duplicate")
    await undecorate(DivisionCog.division_duplicate)(
        DivisionCog(league.bot), asked, source, new_name, role, tier, day_offset, hour_offset
    )
    return asked


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


#: How the log channel names each import form, as `describe_form` names it.
FORMS = {"bulk": "the “Add rounds in bulk” form", "xml": "the “Add rounds from XML” form"}


async def import_rounds(league: Any, form: str, value: str, division: str = "Pro") -> Any:
    """Admin (4242) submitting an import form with *value*: `/round add-bulk`'s for *division*
    (*form* "bulk") or `/round add-xml`'s (*form* "xml"). Gives the form's interaction."""
    from unittest.mock import MagicMock

    from leaguebot.core.cogs.season_cog import BulkRoundModal, SeasonCog, XmlRoundModal
    from tests.support.settings_league import interaction

    cog = SeasonCog(league.bot)
    league.bot.get_cog = MagicMock(side_effect=lambda name: cog if name == "SeasonCog" else None)
    modal: Any
    if form == "bulk":
        modal = BulkRoundModal(division)
        modal.entries._value = value
    else:
        modal = XmlRoundModal()
        modal.payload._value = value
    asked = interaction(league.bot)
    await modal.on_submit(asked)
    return asked


def calendar_xml(*divisions: tuple[str, tuple[tuple[str, str, str, str | None], ...]]) -> str:
    """An XML calendar of *divisions*, each (name, its rounds), each round (local datetime,
    zone, format, track or None)."""
    def round_(at: str, zone: str, fmt: str, track: str | None) -> str:
        tracked = f"<track>{track}</track>" if track is not None else ""
        return (f"<round><datetime>{at}</datetime><timezone>{zone}</timezone>"
                f"<format>{fmt}</format>{tracked}</round>")

    return "<config>" + "".join(
        f'<division name="{name}">' + "".join(round_(*r) for r in rounds) + "</division>"
        for name, rounds in divisions
    ) + "</config>"
