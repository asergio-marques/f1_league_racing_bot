"""Compose a round's two standings pictures from its classification (040).

A standings picture is drawn by the image module and composed here, by the module that owns the
classification (docs/design/architecture.md, "How modules and core fit together"):
:func:`resolve_drawing` turns a championship's persisted standings into the plain
:class:`StandingsDrawing` image declares, every cell of the season grid decided and nothing
drawn, and :func:`build_drawings` reads what both championships need (the calendar, the session
results behind the grid, the seating, the names through core's readers) once and hands the two
back for ``ImageDrawing.draw_standings`` to place on templates.

**This module renders nothing, and neither does image.** Who is in the driver championship is
``results_formatter.driver_is_drawn``, the predicate the textual standings compose by, and a grid
cell's text is ``results_formatter.format_grid_cell``'s, which is Constitution XIV.7: nothing
here restates a rule the textual path holds. The gap to the leader, the previous position and the
position change are derived in ``standings_service`` and formatted here as text by
``results_formatter`` (``_gap_text``, ``_movement_text``): nothing here subtracts points or
compares positions.

See specs/040-standings-image-generation/contracts/ — standings-catalogue.md for the fields and
derived-columns.md for what may be derived and where.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Mapping, Protocol, Sequence

from leaguebot.core.db.database import get_connection
from leaguebot.core.models.classification_occasion import ClassificationOccasion
from leaguebot.core.services.drivers.driver_names import (
    read_driver_names,
    read_driver_nationalities,
    read_nationality_collected,
)
from leaguebot.core.services.drivers.team_service import (
    team_artwork_keys_for_instances,
    team_names_for_instances,
)
from leaguebot.image.models.standings_drawing import (
    CELL_SUFFIXES,
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
    HIGHLIGHT_P1,
    HIGHLIGHT_P2,
    HIGHLIGHT_P3,
    HIGHLIGHT_POINTS,
    CellValue,
    MovementText,
    RoundCells,
    RoundHeading,
    StandingsDrawing,
    StandingsEntry,
)
from leaguebot.results.models.points_config import SessionType
from leaguebot.results.models.session_result import OutcomeModifier
from leaguebot.results.services.standings import standings_service
from leaguebot.results.utils import results_formatter

if TYPE_CHECKING:
    from leaguebot.core.utils.hooks import AwaitedHook

log = logging.getLogger(__name__)


class PositionMovement(Protocol):
    """One entry's change of position against the reference round, as ``derive_movement`` works it
    out: ``change`` is unsigned and ``direction`` carries the sense."""

    @property
    def previous_position(self) -> int: ...

    @property
    def change(self) -> int: ...

    @property
    def direction(self) -> str: ...


def _movement_text(movement: PositionMovement | None) -> MovementText | None:
    """The movement as the text a picture draws it as, or None where there is none."""
    if movement is None:
        return None
    return MovementText(
        previous_position=results_formatter.format_previous_position(movement.previous_position),
        change=results_formatter.format_position_change(movement.change),
        direction=movement.direction,
    )


def _gap_text(gap: int | None) -> str | None:
    """The gap to the leader as the text a picture draws it as, or None where there is none."""
    if gap is None:
        return None
    return results_formatter.format_gap_to_leader(gap, is_leader=False)

#: The lifecycle labels, shared with the textual standings path (XIV.7).
_STATUS_LABELS = {
    "AWAITING_REPORT_VERDICTS": "Provisional Results",
    "AWAITING_APPEAL_VERDICTS": "Post-Race Penalty Results",
    "FINAL": "Final Results",
}

#: The podium places, by finishing position.
_PODIUM = {1: HIGHLIGHT_P1, 2: HIGHLIGHT_P2, 3: HIGHLIGHT_P3}


def highlight_for(row) -> tuple[str | None, bool]:
    """The background kind and the fastest-lap layer one session result confers.

    Three rules, and each is a fact about the data rather than about the template — which
    paint answers a kind is settled later, against the template's own stylesheet.

    **A podium place is a podium place only where the driver was classified.** A driver
    disqualified from first place is drawn by ``format_grid_cell`` as the outcome literal
    ``DSQ``, and painting that gold would state something the results module does not.

    ``points_awarded`` and ``fastest_lap_bonus`` are read rather than recomputed, and they
    answer the question exactly. Per ``standings_service.compute_points_for_session``,
    ``points_awarded > 0`` means *classified, and in a points-paying position under the
    points configuration that session actually used*; ``fastest_lap_bonus > 0`` means *held
    the fastest lap, fastest-lap points were available for that race, and this driver was
    eligible under the configured position limit*. Both conditions the specification asks
    for are therefore already settled upstream, and asking the configuration a second time
    here could only introduce a way for the graphic and the points to disagree (XIV.7).

    The ``getattr`` on the bonus is required rather than defensive: a
    ``QualifyingSessionResult`` carries no such field, which is also why a qualifying cell
    can never hold the overlay.
    """
    classified = getattr(row, "outcome", None) is OutcomeModifier.CLASSIFIED

    highlight: str | None = None
    if classified:
        highlight = _PODIUM.get(getattr(row, "finishing_position", 0))
        if highlight is None and (getattr(row, "points_awarded", 0) or 0) > 0:
            highlight = HIGHLIGHT_POINTS

    fastest_lap = (getattr(row, "fastest_lap_bonus", 0) or 0) > 0
    return highlight, fastest_lap





# ── 1. Resolution ─────────────────────────────────────────────────────────


def status_label(result_status: str | None) -> str:
    """The lifecycle label for a round's ``result_status``."""
    return _STATUS_LABELS.get(result_status or "", "Results")


def _entry_key(snapshot, *, drivers: bool) -> int:
    return snapshot.driver_user_id if drivers else snapshot.team_instance_id


def resolve_drawing(
    *,
    template_key: str,
    division_name: str,
    result_status: str | None,
    occasion: ClassificationOccasion = ClassificationOccasion.AFTER_ROUND,
    round_number: str | int | None = None,
    snapshots: Sequence,
    display_names: Mapping[int, str],
    team_names: Mapping[int, str],
    movements: Mapping[int, PositionMovement | None],
    gaps: Mapping[int, int] | None = None,
    nationalities: Mapping[int, str | None] | None = None,
    reserve_user_ids: set[int] | None = None,
    show_reserves: bool = False,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    race_name: str | None = None,
    nationality_collected: bool = True,
    rounds: Sequence[RoundHeading] = (),
    round_session_results: Mapping[int, Mapping[str, Sequence]] | None = None,
    team_seat_assignments: Mapping[int, Mapping[int, int]] | None = None,
    team_seat_counts: Mapping[int, int] | None = None,
    driver_display_names: Mapping[int, str] | None = None,
    team_keys: Mapping[int, str] | None = None,
) -> StandingsDrawing:
    """Resolve every value a standings graphic draws.

    *snapshots* are the persisted ``DriverStandingsSnapshot`` or ``TeamStandingsSnapshot``
    rows of the round being drawn. Their ``standing_position`` is the order — already
    separated by the countback — and is read, never re-established (XIV.12).

    *movements* comes from ``standings_service.derive_movement`` and *gaps* from the standings'
    own points; both are formatted as the textual standings format them, and nothing here
    subtracts points or compares positions.

    *rounds* is the division's calendar, headed for the grid. *round_session_results*
    maps a round's ordinal to its session results, keyed by :class:`SessionType` value — a
    round **absent** from the mapping is not yet run or was cancelled, and every cell of it
    is emptied (FR-022); a session type present in the outer mapping's value but absent from
    an entry means that round holds no session of that type. *team_seat_assignments* and
    *team_seat_counts* (keyed by ``team_instance_id``, matching *team_names*) are
    constructors-only: the first feeds the car allocation of FR-026, the second the trim a
    team's own seat count applies over the template's declared room. *driver_display_names*
    names the drivers a constructors car draws — keyed by ``driver_user_id``, unlike
    *display_names* itself, which on the constructors graphic names its rows (the teams) and
    cannot also name the drivers inside their cars.

    *team_keys* holds each row's team shorthand, keyed as *team_names* is, which the team's
    artwork is found by apart from the full name drawn (#381). A row absent from it is found by
    the name drawn.
    """
    drivers = template_key == DRIVERS_TEMPLATE_KEY
    reserves = reserve_user_ids or set()
    nationality_map = nationalities or {}
    gap_map = gaps or {}
    seat_assignments = team_seat_assignments or {}

    ordered = sorted(snapshots, key=lambda s: s.standing_position)

    entries: list[StandingsEntry] = []
    for snapshot in ordered:
        # Composition is the textual standings' own rule, called and not restated (XIV.7).
        if drivers and not results_formatter.driver_is_drawn(
            snapshot, reserves, show_reserves
        ):
            continue

        key = _entry_key(snapshot, drivers=drivers)
        cells = (
            _driver_round_cells(key, rounds, round_session_results)
            if drivers
            else _constructor_round_cells(
                key,
                rounds,
                round_session_results,
                seat_assignments.get(key, {}),
                driver_display_names or {},
            )
        )
        entries.append(
            StandingsEntry(
                ordinal=len(entries) + 1,
                position=str(snapshot.standing_position),
                team_name=team_names.get(key, ""),
                points=str(snapshot.total_points),
                driver_name=display_names.get(key) if drivers else None,
                nationality=nationality_map.get(key) if drivers else None,
                gap_to_leader=_gap_text(gap_map.get(key)),
                movement=_movement_text(movements.get(key)),
                cells=cells,
                team_key=(team_keys or {}).get(key),
            )
        )

    # Named by team, since that is what a row carries — the drawing addresses a row by its
    # ordinal, never by the team id resolve_drawing itself took the classification's keys from.
    seat_counts_by_name = {
        team_names[team_id]: count
        for team_id, count in (team_seat_counts or {}).items()
        if team_id in team_names
    }

    return StandingsDrawing(
        template_key=template_key,
        division_name=division_name,
        classification_label=occasion.label(round_number),
        # The phase of the results this classification is computed from. The final sheet
        # has one — it *is* the last round's classification, and that round's results are
        # settled — so it names it, mildly redundant beside `Final Classification` but
        # true. The opening sheet rests on no results at all, so the field is emptied
        # rather than made to say something (XIV.3, a value the data determined to be
        # nothing). This is not `names_a_round`: the question is whether results exist,
        # not whether a round is the subject, and the two part company at the final sheet.
        result_status_label=(
            ""
            if occasion is ClassificationOccasion.SEASON_OPENING
            else status_label(result_status)
        ),
        names_a_race=occasion.names_a_round,
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        race_name=race_name,
        nationality_collected=nationality_collected,
        entries=entries,
        rounds=list(rounds),
        team_seat_counts=seat_counts_by_name,
    )



# ── 1a. The round grid ────────────────────────────────────────────────────

#: Session type -> the suffix of the four cell fields the grid may carry (FR-023).
_CELL_SUFFIX_BY_SESSION = dict(
    zip(
        (
            SessionType.SPRINT_QUALIFYING,
            SessionType.SPRINT_RACE,
            SessionType.FEATURE_QUALIFYING,
            SessionType.FEATURE_RACE,
        ),
        CELL_SUFFIXES,
    )
)


def _session_cells(
    driver_key: int, session_map: Mapping[str, Sequence] | None
) -> dict[str, CellValue]:
    """One driver's four session cells for one round (FR-023, FR-024).

    Emptied — never dashed — in every case the data determines to be nothing: the round
    holds no session of that type, or this driver holds no record in a session it does hold
    (took no part). A cell with nothing in it earns no highlight either.

    The **single funnel** for both championships: the drivers grid calls it once per row and
    the constructors grid once per car, so a highlight decided here cannot reach one graphic
    and miss the other. The preview reaches it by the same path and needs no code of its own.
    """
    cells: dict[str, CellValue] = {}
    for session_type, suffix in _CELL_SUFFIX_BY_SESSION.items():
        rows = None if session_map is None else session_map.get(session_type.value)
        if not rows:
            cells[suffix] = CellValue()
            continue
        row = next((r for r in rows if r.driver_user_id == driver_key), None)
        if row is None:
            cells[suffix] = CellValue()
            continue
        highlight, fastest_lap = highlight_for(row)
        cells[suffix] = CellValue(
            text=results_formatter.format_grid_cell(row),
            highlight=highlight,
            fastest_lap=fastest_lap,
        )
    return cells


def _driver_round_cells(
    driver_key: int,
    rounds: Sequence[RoundHeading],
    round_session_results: Mapping[int, Mapping[str, Sequence]] | None,
) -> dict[int, RoundCells]:
    """Every round's cells for one driver's row."""
    results = round_session_results or {}
    return {
        heading.ordinal: RoundCells(sessions=_session_cells(driver_key, results.get(heading.ordinal)))
        for heading in rounds
    }


def _drivers_for_team(team_instance_id: int, session_map: Mapping[str, Sequence] | None) -> list[int]:
    """The distinct drivers a team's session results record for one round, id-ascending."""
    if not session_map:
        return []
    found: set[int] = set()
    for rows in session_map.values():
        for row in rows:
            if row.team_instance_id == team_instance_id:
                found.add(row.driver_user_id)
    return sorted(found)


def _allocate_cars(drivers_who_drove: Sequence[int], seat_map: Mapping[int, int]) -> dict[int, int]:
    """Car ordinal -> driver, per FR-026.

    A seated driver takes their own seat's ordinal. A driver who drove but holds no seat on
    the team — a non-seated substitute — takes the lowest ordinal no seated driver occupies,
    in driver-id order where more than one substitute needs placing (the rule does not fix
    an order between them; ascending id keeps the result deterministic).
    """
    cars: dict[int, int] = {}
    unseated: list[int] = []
    for driver_key in drivers_who_drove:
        seat = seat_map.get(driver_key)
        if seat is not None:
            cars[seat] = driver_key
        else:
            unseated.append(driver_key)

    next_ordinal = 1
    for driver_key in unseated:
        while next_ordinal in cars:
            next_ordinal += 1
        cars[next_ordinal] = driver_key
        next_ordinal += 1

    return cars


def _constructor_round_cells(
    team_instance_id: int,
    rounds: Sequence[RoundHeading],
    round_session_results: Mapping[int, Mapping[str, Sequence]] | None,
    seat_map: Mapping[int, int],
    driver_display_names: Mapping[int, str],
) -> dict[int, RoundCells]:
    """Every round's cells for one constructor's row — a car per driver who drove (FR-026)."""
    results = round_session_results or {}
    out: dict[int, RoundCells] = {}
    for heading in rounds:
        session_map = results.get(heading.ordinal)
        drivers_who_drove = _drivers_for_team(team_instance_id, session_map)
        allocation = _allocate_cars(drivers_who_drove, seat_map)
        out[heading.ordinal] = RoundCells(
            cars={
                ordinal: (
                    driver_display_names.get(driver_key),
                    _session_cells(driver_key, session_map),
                )
                for ordinal, driver_key in allocation.items()
            }
        )
    return out



# ── The data behind the two drawings ──────────────────────────────────────


async def _calendar(db_path: str, division_id: int):
    """The division's whole calendar as ``RoundHeading``s, and the ordinal of each round id.

    Every round the division holds is headed, run or not: the grid is the season's shape,
    and a column that disappears when a round is cancelled would redraw the graphic's width
    from one posting to the next. A round's *cells* are what emptying handles.
    """
    from leaguebot.core.services.rounds.calendar_post_service import tracks_by_name
    from leaguebot.core.services.rounds.round_context import division_rounds

    tracks = await tracks_by_name(db_path)

    rows = await division_rounds(db_path, division_id)

    headings = []
    ordinal_of_round: dict[int, int] = {}
    for ordinal, row in enumerate(rows, start=1):
        # A mystery round names no circuit until its format is changed, and its flag is the
        # mystery asset rather than a country's (044, FR-012). The heading carries the
        # flag, and names the datum `Mystery` itself, which the closed-set rule resolves to
        # the module's own `mystery.svg` even where a league's flag directory has none — so
        # this is a *substitution*, not an absence, and a heading with no country and no
        # mystery flag would delete the flag instead.
        mystery = row.format == "MYSTERY"
        track_name = None if mystery else row.track_name
        record = tracks.get(track_name) if track_name else None
        headings.append(
            RoundHeading(
                ordinal=ordinal,
                number=str(row.round_number),
                track=track_name,
                country=getattr(record, "country", None) if record else None,
                mystery=mystery,
            )
        )
        ordinal_of_round[row.round_id] = ordinal

    return headings, ordinal_of_round


async def _round_session_results(db_path: str, ordinal_of_round: dict[int, int]):
    """Round ordinal -> session type value -> the rows of that session.

    A round with no active session result is **absent** from the mapping, which is how the
    grid tells "not yet run, or cancelled" from "run, and this driver took no part".
    """
    from leaguebot.results.services.publishing.results_post_service import load_driver_rows

    if not ordinal_of_round:
        return {}

    placeholders = ",".join("?" * len(ordinal_of_round))
    async with get_connection(db_path) as db:
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
        driver_rows = await load_driver_rows(db_path, row["id"], session_type)
        results.setdefault(ordinal, {})[session_type.value] = driver_rows

    return results


async def _seats(db_path: str, division_id: int):
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

    for team in await division_seating(db_path, division_id):
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
    guild,
    *,
    driver_names_read: AwaitedHook,
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
) -> tuple[StandingsDrawing, StandingsDrawing]:
    """Resolve both championships into drawings, or raise ``StandingsDataError``.

    Both are built from one pass over the shared data — the calendar, the session results
    behind the grid, the names — because the two graphics draw the same season and differ
    only in what a row stands for.
    """
    driver_keys = [s.driver_user_id for s in driver_snapshots]
    team_keys = [s.team_instance_id for s in team_snapshots]

    names = await read_driver_names(
        db_path, driver_names_read, guild, driver_keys, division_id=division_id
    )
    nationalities = await read_driver_nationalities(
        db_path, driver_names_read, driver_keys, division_id=division_id
    )
    collected = await read_nationality_collected(db_path, driver_names_read)

    # A constructors row *is* a team, so that graphic's names are keyed by the team. A
    # drivers row names the team its own driver sits in, so that graphic's are keyed by driver.
    team_names_by_team = await team_names_for_instances(db_path, team_keys)
    artwork_keys_by_team = await team_artwork_keys_for_instances(db_path, team_keys)

    headings, ordinal_of_round = await _calendar(db_path, division_id)
    session_results = await _round_session_results(db_path, ordinal_of_round)
    seat_assignments, seat_counts, driver_team_names, driver_team_keys = await _seats(
        db_path, division_id
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

