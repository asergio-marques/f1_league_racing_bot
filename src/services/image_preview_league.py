"""A whole league, invented, for a server that holds no season (046).

Feature 045 made every `/images test` preview draw the league's own data, which is right,
and refused where there was no season to draw — which locked the previews out of the two
moments a manager most wants them: while configuring templates and artwork before a season
exists at all, and on a bare server.

This module invents the league instead. One part of it is not invented: the **teams** come
from the server's own team list. A lineup template no longer names its fields after a
league's teams — that rationale went with the keyed template at v6.0.0 — but the names and
the badges drawn on the graphic are still the league's own, and a preview drawn over made-up
teams would show a manager nothing about the artwork they configured. Every other part — the
division, the calendar, the formats, the round, the drivers — is randomised per invocation.

The output is an ordinary :class:`PreviewContext`. That is the whole trick: every one of the
eleven builders draws from the context, so a context invented here flows through all of them
without a single builder knowing the difference.

**Nothing here writes.** No season, division, round, team, seat or driver survives the call.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from db.database import get_connection
from models.image_constants import PREVIEW_KINDS
from models.round import RoundFormat
from services.image_preview_service import (
    REASON_NO_SERVER_TEAMS,
    PreviewContext,
    PreviewDriver,
    PreviewRefused,
    _nationality_collected,
    resolve_asset_directories,
)

# ── The invented vocabulary ───────────────────────────────────────────────

#: Division names a league might plausibly use. Plausible rather than absurd, because the
#: field is drawn at a real template's real width and a manager is judging whether it fits.
_DIVISION_NAMES = (
    "Premier", "Championship", "Challenger", "Academy", "Apex",
    "Meridian", "Vanguard", "Summit", "Frontier", "Horizon",
)

#: Surnames for invented drivers.
_DRIVER_NAMES = (
    "Alvarez", "Bergström", "Castellano", "Dubois", "Eriksson",
    "Fontaine", "Gallagher", "Hoffmann", "Ivanov", "Jankowski",
    "Kowalski", "Lindqvist", "Moretti", "Nakamura", "O'Sullivan",
    "Petrov", "Quintana", "Rasmussen", "Silva", "Tanaka",
    "Ueda", "Vasquez", "Whitfield", "Ximenes", "Yamamoto", "Zieliński",
)

#: Nationalities among those the signup wizard accepts. "Other" is included deliberately:
#: it is what a driver stating none records, and it resolves an asset like any other.
_NATIONALITIES = (
    "British", "Dutch", "French", "Italian", "German",
    "Spanish", "Brazilian", "Japanese", "Other",
)

#: The formats a fabricated calendar draws from. All four, so that a manager judging a
#: calendar template sees every format marker it can be asked to draw.
_FORMATS = ("NORMAL", "SPRINT", "ENDURANCE", "MYSTERY")

#: Calendar length. A real season's, near enough that a crop falls where a crop would.
_MIN_ROUNDS = 8
_MAX_ROUNDS = 14

#: Synthetic keys, well clear of any real profile id.
_KEY_BASE = 8_100_000_000_000_000_000

#: A division id matching no row. The one builder that still reads the database by division
#: id — the check-in deadline — answers None for it and takes its documented default.
FABRICATED_DIVISION_ID = -1


async def build_fabricated_context(
    bot,
    server_id: int,
    *,
    kind: str,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> PreviewContext:
    """A complete league for *kind*, invented, where the server holds no season.

    *rng* and *now* default to live values, so production randomises afresh at every
    invocation (FR-014). A test passes a seeded ``random.Random`` and a pinned ``datetime``
    and asserts on exact output; pinning the clock alongside the seed is required, because
    the calendar is dated relative to it.

    Raises :class:`PreviewRefused` where the kind draws a roster and the server has
    configured no team beyond the reserve (FR-012).
    """
    rng = rng if rng is not None else random.Random()
    now = now if now is not None else datetime.now(timezone.utc)

    spec = PREVIEW_KINDS[kind]

    teams_configured = await _server_team_list(bot, server_id)
    if not teams_configured and spec["draws_roster"]:
        raise PreviewRefused(
            REASON_NO_SERVER_TEAMS,
            "⛔ This server has no season and has configured no team, so there is no "
            "roster to draw. Add your teams with `/team add` first.\n"
            "The calendar, the check-in call and the four weather previews need no team "
            "and can be drawn as they stand.",
        )

    season_number = await bot.season_service.get_previous_season_number(server_id) + 1
    nationality_collected = await _nationality_collected(bot, server_id)

    rounds = await _fabricate_calendar(bot, rng, now)
    chosen = _round_for_kind(rounds, spec, rng)

    teams = _fabricate_teams(teams_configured, rng, collected=nationality_collected)
    drivers = _drivers_of(teams)

    context = PreviewContext(
        server_id=server_id,
        season_number=season_number,
        division_id=FABRICATED_DIVISION_ID,
        division_name=rng.choice(_DIVISION_NAMES),
        division_tier=rng.randint(1, 5),
        round=chosen,
        rounds=rounds,
        teams=teams,
        drivers=drivers,
        nationality_collected=nationality_collected,
        fabricated_drivers=bool(drivers),
        fabricated_league=True,
    )
    context.asset_directories, context.directory_faults = await resolve_asset_directories(
        bot, server_id
    )
    return context


# ── The server's own teams ────────────────────────────────────────────────


async def _server_team_list(bot, server_id: int) -> list[tuple[str, int]]:
    """(name, seat count) per configured team, the reserve excluded.

    The reserve is excluded here for the same reason it is excluded everywhere else a
    division's teams are counted: it fields no car of its own.
    """
    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                "SELECT name, max_seats FROM default_teams "
                "WHERE server_id = ? AND is_reserve = 0 ORDER BY name",
                (server_id,),
            )
        ).fetchall()
    return [(row["name"], max(1, int(row["max_seats"] or 2))) for row in rows]


# ── The calendar ──────────────────────────────────────────────────────────


async def _fabricate_calendar(bot, rng: random.Random, now: datetime) -> list:
    """A randomised calendar over tracks the bot's own track data carries.

    Real track names matter: track imagery resolves by the normalised name and the flag by
    the track's country, so an invented name would report two fallbacks that say nothing
    about how the league has configured its artwork.
    """
    from services.track_service import get_all_tracks

    async with get_connection(bot.db_path) as db:
        tracks = await get_all_tracks(db)

    names = [row["name"] for row in tracks]
    rng.shuffle(names)

    count = rng.randint(_MIN_ROUNDS, _MAX_ROUNDS)
    if names:
        count = min(count, len(names))
    else:
        # A tracks table seeded by migration cannot be empty in practice; a corrupted
        # install draws unnamed rounds rather than raising.
        count = min(count, _MIN_ROUNDS)

    formats = _fabricate_formats(count, rng)

    rounds = []
    for index in range(count):
        fmt = formats[index]
        track = names[index] if index < len(names) else None
        rounds.append(
            SimpleNamespace(
                id=-(index + 1),
                division_id=FABRICATED_DIVISION_ID,
                round_number=index + 1,
                # The attribute surface matches `season_service._row_to_round` exactly: a
                # `RoundFormat` and a real `datetime`, not the strings the row carries.
                # Builders and the attendance service alike read `scheduled_at.tzinfo`.
                format=RoundFormat(fmt),
                # A mystery round conceals its track, as a real one does.
                track_name=None if fmt == "MYSTERY" else track,
                scheduled_at=now + timedelta(days=14 * (index + 1)),
                status="ACTIVE",
                finalized=False,
                phase1_done=False,
                phase2_done=False,
                phase3_done=False,
            )
        )
    return rounds


def _fabricate_formats(count: int, rng: random.Random) -> list[str]:
    """One format per round, with more than one kind among them (FR-015).

    A calendar of a single format would leave a manager unable to judge the format markers
    their template draws, which is half of what a calendar preview is for. At least one
    mystery and one non-mystery round are guaranteed so that the weather previews always
    find the round their format demands (FR-017).
    """
    if count == 1:
        return [rng.choice(_FORMATS)]

    formats = [rng.choice(_FORMATS) for _ in range(count)]

    # Guarantee both sides of the mystery divide, which also guarantees FR-015's
    # "more than one format" for any calendar of two rounds or more.
    positions = list(range(count))
    rng.shuffle(positions)
    formats[positions[0]] = "MYSTERY"
    formats[positions[1]] = rng.choice([f for f in _FORMATS if f != "MYSTERY"])
    return formats


def _round_for_kind(rounds: list, spec: dict, rng: random.Random):
    """The round the kind draws, chosen so its format demand is always met (FR-017).

    A preview of a fabricated league must never be refused for the format of a round the
    feature itself invented, so the choice is made here rather than left to chance.
    """
    if not spec["needs_round"] or not rounds:
        return None

    demanded = spec["format_demanded"]
    if demanded is True:
        candidates = [r for r in rounds if r.format is RoundFormat.MYSTERY]
    elif demanded is False:
        candidates = [r for r in rounds if r.format is not RoundFormat.MYSTERY]
    else:
        candidates = list(rounds)

    return rng.choice(candidates or rounds)


# ── Teams, seats and drivers ──────────────────────────────────────────────


def _fabricate_teams(
    configured: list[tuple[str, int]], rng: random.Random, *, collected: bool
) -> list:
    """The server's teams, each seat filled with an invented driver (FR-019).

    The team shape matches what `_load_teams_and_drivers` builds for a real division, so
    the lineup — which draws the seats themselves rather than the flat driver list — draws
    these exactly as it draws a real division's.
    """
    from services.image_preview_data import LONG_DRIVER_NAME

    pool = list(_DRIVER_NAMES)
    rng.shuffle(pool)

    teams = []
    index = 0
    for team_name, seat_count in configured:
        seats = []
        for seat_number in range(1, seat_count + 1):
            # One seat carries a name no league controls the length of, so a template
            # field's bound is exercised without waiting for an unlucky signup.
            if index == 2:
                name = LONG_DRIVER_NAME
            else:
                name = pool[index % len(pool)]
            nationality = rng.choice(_NATIONALITIES) if collected else None
            seats.append(
                SimpleNamespace(
                    seat_number=seat_number,
                    discord_user_id=_KEY_BASE + index,
                    profile_id=_KEY_BASE + index,
                    server_display_name=name,
                    discord_username=name,
                    test_display_name=None,
                    nationality=nationality,
                )
            )
            index += 1
        teams.append(
            SimpleNamespace(name=team_name, is_reserve=False, seats=seats)
        )
    return teams


def _drivers_of(teams: list) -> list[PreviewDriver]:
    """The flat driver list every kind but the lineup draws from."""
    drivers: list[PreviewDriver] = []
    for team in teams:
        for seat in team.seats:
            drivers.append(
                PreviewDriver(
                    key=int(seat.discord_user_id),
                    display_name=seat.server_display_name,
                    team_name=team.name,
                    seat_number=int(seat.seat_number),
                    nationality=seat.nationality,
                    fabricated=True,
                )
            )
    return drivers
