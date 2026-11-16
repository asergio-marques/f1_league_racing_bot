"""Draw a round's two championships as graphics, or stand aside (040).

**One decision in one place**, as 039's results posting is. Every occasion that reposts the
textual standings reaches ``results_post_service.produce_standings``, the posting jobs of
`review_posting` among them, which asks the image drawing (`ImageDrawing.draw_standings`) for the
pictures and posts them itself: this module draws and posts nothing.

Two things make this module unlike the five before it.

**It draws two pictures from one call.** The textual standings are a single message carrying both
championships; the graphics are two, the driver standings first and the constructors second, each
with its own id column on the round's row in `round_standings_posts`. Every other aspect draws
one attachment per call and lets its caller loop.

**Failure is per championship** (Constitution XIV.4). A drivers graphic that will not draw
falls back to the drivers section **alone** — never to the whole textual message, which
would print the constructors table a second time beside the graphic that just drew it. That
is why ``results_post_service`` keeps section formatting and message composition apart, and
why :class:`StandingsDrawn` says which championships drew rather than simply whether it ran.

**The textual path is not reformed by this module.** Where the image flow does not run — no
drawing in scope, the module disabled, the `standings` toggle off, neither template valid —
`StandingsDrawn.applicable` is False and the caller's body runs exactly as it did before 040
(Constitution XIV.7).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import discord

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.image.models.image_module import PostingOrigin
from leaguebot.image.services.aspects.image_standings_service import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
)
from leaguebot.core.utils.league_bot import LeagueBot

log = logging.getLogger(__name__)

#: The standings' own aspect and the name its toggle goes by, which heads each line it hands back.
STANDINGS_ASPECT = "standings"
STANDINGS_TOGGLE_NAME = "Standings image"

#: Championship -> the template that draws it, in the order the two are posted.
TEMPLATE_KEYS: dict[str, str] = {
    "drivers": DRIVERS_TEMPLATE_KEY,
    "constructors": CONSTRUCTORS_TEMPLATE_KEY,
}


@dataclass(frozen=True)
class StandingsDrawn:
    """What the image module drew of a round's two championships, for the standings to post.

    *applicable* is False where the image flow stood aside wholesale (no switch on and no
    template valid), and the caller's untouched textual body is what should run, message for
    message as before 040. Where it is True, a championship whose file is None is one the caller
    posts as its text section, and that section alone. *lines* are what the drawing had to report
    (its render notices, and the faults of pictures that were due and not drawn), headed by the
    toggle's name, for the job that posts the standings to write.
    """

    applicable: bool = False
    drivers: discord.File | None = None
    constructors: discord.File | None = None
    lines: tuple[str, ...] = ()

    def file_of(self, championship: str) -> discord.File | None:
        """The picture of *championship* ("drivers" or "constructors"), or None."""
        return self.drivers if championship == "drivers" else self.constructors


def fault(what: str, detail: str) -> str:
    """The line saying why the picture of *what* was not drawn, headed by the toggle's name."""
    return f"⚠️ {STANDINGS_TOGGLE_NAME} — {what}: {detail}"


def lines_of(what: str, decision) -> tuple[str, ...]:
    """The lines a render's notices and problem make, naming *what* they pertain to."""
    from leaguebot.image.services.render.image_render_service import ImageRenderService

    lines: list[str] = []
    if decision.notices:
        lines.append(ImageRenderService.format_notices(decision.notices, subject=what))
    if decision.problem is not None:
        lines.append(fault(what, decision.problem.detail))
    return tuple(lines)


# ── The data behind the two drawings ──────────────────────────────────────


async def _calendar(bot: LeagueBot, division_id: int):
    """The division's whole calendar as ``RoundHeading``s, and the ordinal of each round id.

    Every round the division holds is headed, run or not: the grid is the season's shape,
    and a column that disappears when a round is cancelled would redraw the graphic's width
    from one posting to the next. A round's *cells* are what emptying handles.
    """
    from leaguebot.core.services.rounds.calendar_post_service import tracks_by_name
    from leaguebot.image.services.aspects.image_calendar_service import MYSTERY_DATUM
    from leaguebot.image.services.aspects.image_standings_service import RoundHeading

    tracks = await tracks_by_name(bot.db_path)

    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                "SELECT id, round_number, format, track_name FROM rounds "
                "WHERE division_id = ? ORDER BY round_number",
                (division_id,),
            )
        ).fetchall()

    headings = []
    ordinal_of_round: dict[int, int] = {}
    for ordinal, row in enumerate(rows, start=1):
        # A mystery round names no circuit until its format is changed, and its flag is the
        # mystery asset rather than a country's (044, FR-012). The datum is the literal
        # `Mystery`, which the closed-set rule resolves to the module's own `mystery.svg`
        # even where a league's flag directory has none — so this is a *substitution*, not
        # an absence, and `country=None` would delete the heading's flag instead.
        mystery = row["format"] == "MYSTERY"
        track_name = None if mystery else row["track_name"]
        record = tracks.get(track_name) if track_name else None
        headings.append(
            RoundHeading(
                ordinal=ordinal,
                number=str(row["round_number"]),
                track=track_name,
                country=(
                    MYSTERY_DATUM
                    if mystery
                    else (getattr(record, "country", None) if record else None)
                ),
            )
        )
        ordinal_of_round[row["id"]] = ordinal

    return headings, ordinal_of_round


async def _round_session_results(bot: LeagueBot, ordinal_of_round: dict[int, int]):
    """Round ordinal -> session type value -> the rows of that session.

    A round with no active session result is **absent** from the mapping, which is how the
    grid tells "not yet run, or cancelled" from "run, and this driver took no part".
    """
    from leaguebot.results.models.session_result import SessionType
    from leaguebot.results.services.publishing.results_post_service import load_driver_rows

    if not ordinal_of_round:
        return {}

    placeholders = ",".join("?" * len(ordinal_of_round))
    async with get_connection(bot.db_path) as db:
        rows = await (
            await db.execute(
                f"SELECT id, round_id, session_type FROM session_results "
                f"WHERE round_id IN ({placeholders}) AND status = 'ACTIVE'",
                list(ordinal_of_round),
            )
        ).fetchall()

    results: dict[int, dict[str, list]] = {}
    for row in rows:
        ordinal = ordinal_of_round[row["round_id"]]
        try:
            session_type = SessionType(row["session_type"])
        except ValueError:
            log.warning(
                "standings: session result %s has an unknown session type %r",
                row["id"],
                row["session_type"],
            )
            continue
        driver_rows = await load_driver_rows(bot.db_path, row["id"], session_type)
        results.setdefault(ordinal, {})[session_type.value] = driver_rows

    return results


async def _seats(bot: LeagueBot, division_id: int):
    """The division's seating, in the three shapes the two drawings need.

    Returns ``(assignments, counts, driver_team_names, driver_team_keys)`` — the first two
    keyed by the team's id, as the constructors classification is, for that graphic's car
    allocation and seat trim; the last two by driver user id, because a drivers row names the
    team its own driver sits in rather than the row's own subject: the name drawn, and the
    shorthand its artwork is found by (#381).

    The classification and the seats name a team the same way — by the division's team, never
    its Discord role (#375) — so the two are joined directly.
    """
    assignments: dict[int, dict[int, int]] = {}
    counts: dict[int, int] = {}
    driver_team_names: dict[int, str] = {}
    driver_team_keys: dict[int, str] = {}

    # A driver whose placement is not yet confirmed is not in the standings (issue #220): core's
    # seating leaves it out, whatever state its driver is in.
    from leaguebot.core.services.drivers.division_seating import division_seating

    for team in await division_seating(bot.db_path, division_id):
        counts[team.team_instance_id] = team.max_seats

        for seat in team.seats:
            if seat.occupant is None:
                continue
            try:
                driver_key = int(seat.occupant.discord_user_id)
            except (TypeError, ValueError):
                continue

            driver_team_names[driver_key] = team.full_name
            driver_team_keys[driver_key] = team.name
            assignments.setdefault(team.team_instance_id, {})[driver_key] = seat.seat_number

    return assignments, counts, driver_team_names, driver_team_keys


async def build_drawings(
    bot: LeagueBot,
    guild,
    *,
    db_path: str,
    division_id: int,
    round_id: int,
    round_number,
    driver_snapshots,
    team_snapshots,
    reserve_user_ids: set[int],
    show_reserves: bool,
    result_status: str | None,
    division_name: str,
    division_tier=None,
    season_number=None,
    race_name: str | None = None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
):
    """Resolve both championships into drawings, or raise ``StandingsDataError``.

    Both are built from one pass over the shared data — the calendar, the session results
    behind the grid, the names — because the two graphics draw the same season and differ
    only in what a row stands for.
    """
    from leaguebot.results.services.standings import standings_service
    from leaguebot.image.services.aspects.image_results_post import (
        _driver_names,
        _nationality_collected,
        _nationalities,
        _team_keys,
        _team_names,
    )
    from leaguebot.image.services.aspects.image_standings_service import resolve_drawing

    driver_keys = [s.driver_user_id for s in driver_snapshots]
    team_keys = [s.team_instance_id for s in team_snapshots]

    names = await _driver_names(bot, guild, driver_keys, division_id=division_id)
    nationalities = await _nationalities(bot, driver_keys, division_id=division_id)
    collected = await _nationality_collected(db_path)

    # A constructors row *is* a team, so that graphic's names are keyed by the team. A
    # drivers row names the team its own driver sits in, so that graphic's are keyed by driver.
    team_names_by_team = await _team_names(bot, guild, division_id, team_keys)
    artwork_keys_by_team = await _team_keys(bot, team_keys)

    headings, ordinal_of_round = await _calendar(bot, division_id)
    session_results = await _round_session_results(bot, ordinal_of_round)
    seat_assignments, seat_counts, driver_team_names, driver_team_keys = await _seats(
        bot, division_id
    )

    driver_current = [
        (s.driver_user_id, s.standing_position, s.total_points) for s in driver_snapshots
    ]
    team_current = [
        (s.team_instance_id, s.standing_position, s.total_points) for s in team_snapshots
    ]

    # The opening sheet stands after nothing, so nobody has moved. The final sheet *is* the
    # last round's classification, and shows the movement that round produced.
    if occasion is ClassificationOccasion.SEASON_OPENING:
        driver_previous = None
        team_previous = None
    else:
        driver_previous = await standings_service.previous_standing_positions(
            db_path, division_id, round_id
        )
        team_previous = await standings_service.previous_standing_positions(
            db_path, division_id, round_id, teams=True
        )

    shared = dict(
        division_name=division_name,
        occasion=occasion,
        round_number=round_number,
        result_status=result_status,
        division_tier=division_tier,
        season_number=season_number,
        race_name=race_name,
        nationality_collected=collected,
        rounds=headings,
        round_session_results=session_results,
    )

    drivers_drawing = resolve_drawing(
        template_key=DRIVERS_TEMPLATE_KEY,
        snapshots=driver_snapshots,
        display_names=names,
        team_names=driver_team_names,
        team_keys=driver_team_keys,
        movements=standings_service.derive_movement(driver_current, driver_previous),
        gaps=standings_service.derive_gaps(driver_current),
        nationalities=nationalities,
        reserve_user_ids=reserve_user_ids,
        show_reserves=show_reserves,
        **shared,
    )
    constructors_drawing = resolve_drawing(
        template_key=CONSTRUCTORS_TEMPLATE_KEY,
        snapshots=team_snapshots,
        # Both maps name the same thing here, the row's own team; the drivers inside its
        # cars are named separately, keyed by user id.
        display_names=team_names_by_team,
        team_names=team_names_by_team,
        team_keys=artwork_keys_by_team,
        movements=standings_service.derive_movement(team_current, team_previous),
        gaps=standings_service.derive_gaps(team_current),
        team_seat_assignments=seat_assignments,
        team_seat_counts=seat_counts,
        driver_display_names=names,
        **shared,
    )

    return drivers_drawing, constructors_drawing


# ── Render ────────────────────────────────────────────────────────────────


async def render_png(config_service, renderer, drawing):
    """Render one championship. Returns the render service's PostingDecision.

    A fault in resolving the directories or building the fill is raised, for the drawing to
    name as the reason this championship's text stands.
    """
    from leaguebot.image.services.render.image_render_service import (
        resolve_configured_directories,
        spec_builder_with_faults,
    )
    from leaguebot.image.services.aspects.image_standings_service import build_fill_spec

    config = await config_service.get_config()
    directories, directory_faults = resolve_configured_directories(
        config,
        (
            ("team", "team_image_directory"),
            ("flag", "flag_directory"),
            ("track", "track_image_directory"),
            # The only aspect that draws position-change arrows, and the class the
            # result marks share with them.
            ("marker", "marker_directory"),
        ),
        image_type=drawing.template_key,
    )

    from leaguebot.image.utils.image_naming import stem_for_drawing

    return await renderer.render_for_posting(
        drawing.template_key,
        spec_builder_with_faults(
            build_fill_spec, drawing, directories, directory_faults
        ),
        posting_origin=PostingOrigin.SCHEDULED,
        division_name=drawing.division_name,
        filename_stem=stem_for_drawing(drawing),
    )
