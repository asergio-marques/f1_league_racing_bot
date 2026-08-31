"""Resolve and project a standings graphic — one championship of one division.

Two templates draw one aspect: the driver championship and the constructor championship. They
share every field but the columns of their rows, and each is its own catalogue entry
(Constitution XIV.10).

**What this module does not do.** It performs no arithmetic over points or positions. The gap
to the leader, the previous position and the position change are derived in
``services.standings_service.derive_movement`` and arrive here finished — XIV.7 as amended at
v4.5.0 admits them as a derived *presentation* on the condition that the derivation lives with
the data, so the textual path can adopt the columns by calling it rather than growing a second
implementation. A subtraction appearing in this file would break that contract silently.

Nor does it compose the classification by a rule of its own: who is in the driver championship
is ``results_formatter.driver_is_drawn``, the same predicate the textual standings compose by.

See specs/040-standings-image-generation/contracts/ — standings-catalogue.md for the fields and
derived-columns.md for what may be derived and where.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from models.image_catalogues import CapacityError, catalogue_for, row_crop_fields
from models.points_config import SessionType
from utils import results_formatter
from utils.svg_document import FieldIndex, stylesheet
from utils.svg_fill import FillSpec
from utils.country_data import country_for_nationality

DRIVERS_TEMPLATE_KEY = "standings_drivers_template"
CONSTRUCTORS_TEMPLATE_KEY = "standings_constructors_template"

_ROW_PREFIX = "row"
_ROUND_PREFIX = "round"

#: The lifecycle labels, shared with the textual standings path (XIV.7).
_STATUS_LABELS = {
    "PROVISIONAL": "Provisional Results",
    "POST_RACE_PENALTY": "Post-Race Penalty Results",
    "FINAL": "Final Results",
}


class StandingsDataError(Exception):
    """A fatal disagreement between a classification and what a standings graphic needs.

    Raised before anything is drawn. The caller turns it into a Problem, which abandons this
    graphic — and that graphic alone, the other championship answering for itself (XIV.4).
    """


#: The four backgrounds a cell may be given, in precedence order. A podium place takes
#: priority over a points finish — a winner is in the points too, and the gold says more.
#:
#: Each value doubles as the **stem of the selector** the template is asked for, so
#: ``f"highlight_{kind}"`` composes without a second table to keep in step.
HIGHLIGHT_P1 = "p1"
HIGHLIGHT_P2 = "p2"
HIGHLIGHT_P3 = "p3"
HIGHLIGHT_POINTS = "points"

#: The fastest-lap overlay. Not a member of the four above: it is an independent layer and
#: may stand at the same time as any of them.
HIGHLIGHT_FASTEST_LAP = "fastest_lap"

#: The podium places, by finishing position.
_PODIUM = {1: HIGHLIGHT_P1, 2: HIGHLIGHT_P2, 3: HIGHLIGHT_P3}


@dataclass(frozen=True)
class CellValue:
    """One cell of the season grid: what it says, and what it is worth calling out.

    Text and highlight travel together deliberately. They are two readings of one session
    result, and a projection that derived them apart would eventually draw a gold chip under
    an outcome literal — which is exactly the case ``highlight_for`` exists to refuse.
    """

    #: The finishing position or outcome literal drawn in the cell. An **empty string** means
    #: the data determined it to be nothing — no session of that type, a round unrun or
    #: cancelled, or a driver who took no part — and is emptied quietly rather than dashed
    #: (XIV.3).
    text: str = ""
    #: One of the four background kinds, or None where the cell earns no background.
    highlight: str | None = None
    #: Whether the fastest-lap overlay stands on this cell.
    fastest_lap: bool = False


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
    from models.session_result import OutcomeModifier

    classified = getattr(row, "outcome", None) is OutcomeModifier.CLASSIFIED

    highlight: str | None = None
    if classified:
        highlight = _PODIUM.get(getattr(row, "finishing_position", 0))
        if highlight is None and (getattr(row, "points_awarded", 0) or 0) > 0:
            highlight = HIGHLIGHT_POINTS

    fastest_lap = (getattr(row, "fastest_lap_bonus", 0) or 0) > 0
    return highlight, fastest_lap


@dataclass(frozen=True)
class RoundCells:
    """The cells of one round on one row, part of the season grid."""

    #: Session key → cell, for a drivers row.
    sessions: dict[str, CellValue] = field(default_factory=dict)
    #: Car ordinal → (driver name or None, session key → cell), for a constructors row.
    cars: dict[int, tuple[str | None, dict[str, CellValue]]] = field(default_factory=dict)


@dataclass(frozen=True)
class StandingsEntry:
    """One row of the classification, every cell already the string that will be drawn."""

    #: The row's discriminator — the drawing order, contiguous from 1.
    ordinal: int
    #: The value placed on ``row_<x>_position``: the position the standings **recorded**.
    #:
    #: Usually the ordinal, and deliberately not filled from it. A reserve driver who raced
    #: holds a standing position but is drawn only where the division's reserves toggle is
    #: on, so with the toggle off the recorded positions run 1, 2, 4 while the rows run
    #: 1, 2, 3. The textual standings print the recorded position, and XIV.7 forbids the
    #: graphic and the table disagreeing about a value both draw — so the recorded position
    #: is what is drawn, and the ordinal addresses the row and nothing else.
    #:
    #: XIV.11's "fill from the ordinal" governs a collection whose ordinal genuinely
    #: coincides with the datum, as a results row's does; here the two can part company.
    position: str
    #: The team drawn on this row. On the drivers graphic that is the team of the division
    #: **seating the driver at the moment of generation** — the reserve team for a reserve —
    #: and never the team whose car they drove in any one round (FR-020).
    team_name: str
    points: str
    #: Drivers graphic only; None on the constructors graphic, which names no driver here.
    driver_name: str | None = None
    #: The datum behind ``row_<x>_driver_flag``. None where the driver recorded none.
    nationality: str | None = None
    #: The leader's points less this entry's. Always available: it is arithmetic over the
    #: classification being drawn alone, so it survives a first round with no reference.
    gap_to_leader: int | None = None
    #: The change against the reference round, or None where it cannot be determined — the
    #: first round of a division, or an entry the reference round does not hold. Absent
    #: entirely rather than partly filled, and not a failure (FR-017).
    movement: object | None = None
    #: Round ordinal → its cells. Empty on a template declaring no round.
    cells: dict[int, RoundCells] = field(default_factory=dict)


@dataclass(frozen=True)
class RoundHeading:
    """One column of the grid."""

    ordinal: int
    number: str
    track: str | None = None

    #: The country the round is run in — the datum its flag resolves by (044).
    #: ``track`` is retained: it still names the round and decides the mystery case,
    #: it simply stopped being an asset datum when the heading moved to the flag class.
    country: str | None = None


@dataclass(frozen=True)
class StandingsDrawing:
    """One championship's classification, resolved and ready to project onto a template."""

    template_key: str
    division_name: str
    round_number: str
    result_status_label: str
    division_tier: str | None = None
    season_number: str | None = None
    race_name: str | None = None
    #: True where the league collects a driver's nationality at all. Where it does not, an
    #: empty flag field is exactly what was configured and raises nothing (XIV.4).
    nationality_collected: bool = True
    entries: list[StandingsEntry] = field(default_factory=list)
    rounds: list[RoundHeading] = field(default_factory=list)
    #: Constructors only: team name -> the team's currently configured seat count. A car
    #: beyond it is removed silently regardless of whether a driver was allocated to it —
    #: the ceiling `build_fill_spec` trims cars against is the team's own seats, never the
    #: template's declared room, which only bounds the fatal case. Empty on the drivers
    #: graphic.
    team_seat_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_drivers(self) -> bool:
        return self.template_key == DRIVERS_TEMPLATE_KEY

    @property
    def entry_count(self) -> int:
        return len(self.entries)


# ── 1. Resolution ─────────────────────────────────────────────────────────


def status_label(result_status: str | None) -> str:
    """The lifecycle label for a round's ``result_status``."""
    return _STATUS_LABELS.get(result_status or "PROVISIONAL", "Results")


def _entry_key(snapshot, *, drivers: bool) -> int:
    return snapshot.driver_user_id if drivers else snapshot.team_role_id


def resolve_drawing(
    *,
    template_key: str,
    division_name: str,
    round_number: str | int,
    result_status: str | None,
    snapshots: Sequence,
    display_names: Mapping[int, str],
    team_names: Mapping[int, str],
    movements: Mapping[int, object | None],
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
) -> StandingsDrawing:
    """Resolve every value a standings graphic draws.

    *snapshots* are the persisted ``DriverStandingsSnapshot`` or ``TeamStandingsSnapshot``
    rows of the round being drawn. Their ``standing_position`` is the order — already
    separated by the countback — and is read, never re-established (XIV.12).

    *movements* comes from ``standings_service.derive_movement`` and is carried through
    untouched. Nothing here subtracts points or compares positions.

    *rounds* is the division's calendar, headed for the grid. *round_session_results*
    maps a round's ordinal to its session results, keyed by :class:`SessionType` value — a
    round **absent** from the mapping is not yet run or was cancelled, and every cell of it
    is emptied (FR-022); a session type present in the outer mapping's value but absent from
    an entry means that round holds no session of that type. *team_seat_assignments* and
    *team_seat_counts* (keyed by ``team_role_id``, matching *team_names*) are
    constructors-only: the first feeds the car allocation of FR-026, the second the trim a
    team's own seat count applies over the template's declared room. *driver_display_names*
    names the drivers a constructors car draws — keyed by ``driver_user_id``, unlike
    *display_names* itself, which on the constructors graphic names its rows (the teams) and
    cannot also name the drivers inside their cars.
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
                gap_to_leader=gap_map.get(key),
                movement=movements.get(key),
                cells=cells,
            )
        )

    # Named by team, since that is what a row carries — the drawing addresses a row by its
    # ordinal, never by the role id resolve_drawing itself took the classification's keys from.
    seat_counts_by_name = {
        team_names[role_id]: count
        for role_id, count in (team_seat_counts or {}).items()
        if role_id in team_names
    }

    return StandingsDrawing(
        template_key=template_key,
        division_name=division_name,
        round_number=str(round_number),
        result_status_label=status_label(result_status),
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
_CELL_SUFFIX_BY_SESSION = {
    SessionType.SPRINT_QUALIFYING: "sprint_qualifying_result",
    SessionType.SPRINT_RACE: "sprint_race_result",
    SessionType.FEATURE_QUALIFYING: "feature_qualifying_result",
    SessionType.FEATURE_RACE: "feature_race_result",
}


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


def _drivers_for_team(team_role_id: int, session_map: Mapping[str, Sequence] | None) -> list[int]:
    """The distinct drivers a team's session results record for one round, id-ascending."""
    if not session_map:
        return []
    found: set[int] = set()
    for rows in session_map.values():
        for row in rows:
            if row.team_role_id == team_role_id:
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
    team_role_id: int,
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
        drivers_who_drove = _drivers_for_team(team_role_id, session_map)
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


# ── 2. Projection ─────────────────────────────────────────────────────────


def _row_fields_declared(declared, ordinal: int) -> list[str]:
    """Every id the template declares bearing *ordinal*, group included."""
    stem = f"{_ROW_PREFIX}_{ordinal}"
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def _round_ids(declared, ordinal: int) -> list[str]:
    """Every id bearing round *ordinal*, across every family it governs.

    A round's ordinal stands as ``round_<z>_*`` at top level, as ``row_<x>_round_<z>_*`` on
    a driver's row, and as ``row_<x>_round_<z>_driver_<w>_*`` on a constructor's car — the
    substring match reaches all three without distinguishing them, since a car id is simply
    a longer ``row_``-prefixed id carrying the same round marker. One capacity decision
    removes them all (XIV.12) — containment cannot carry the cells, a cell belonging to its
    row and its round both while a node of an SVG file has one parent (XIV.2).
    """
    round_stem = f"{_ROUND_PREFIX}_{ordinal}"
    ids = {
        name
        for name in declared
        if name == round_stem or name.startswith(f"{round_stem}_")
    }
    suffix = f"_{_ROUND_PREFIX}_{ordinal}"
    ids.update(
        name
        for name in declared
        if name.startswith(f"{_ROW_PREFIX}_") and (suffix in name)
    )
    return sorted(ids)


#: The prefix every highlight selector carries. A template says which highlights it wants by
#: declaring rules under these names, and gets none it does not name.
_HIGHLIGHT_SELECTOR = "highlight_"


def _highlight_paints(root) -> dict[str, str]:
    """Selector stem -> the fill it declares, read from the template's own stylesheet.

    The paints are the template's business and never the bot's (a decision taken in
    conversation, 2026-08-31): a league edits one ``<style>`` block rather than a row of
    configuration commands, and because the value is copied verbatim a ``fill:url(#…)``
    naming a gradient in the template's ``<defs>`` works with no machinery of its own.

    Only ``.highlight_*`` rules carrying a ``fill`` are collected; anything else the
    stylesheet holds is left alone. A template declaring no such rule yields an empty map,
    and every highlight below then resolves to None — which is how a template authored
    before this feature renders exactly as it did.
    """
    return {
        selector[1:]: block["fill"]
        for selector, block in stylesheet(root).items()
        if selector.startswith(f".{_HIGHLIGHT_SELECTOR}") and "fill" in block
    }


def _paint(
    paints: Mapping[str, str],
    kind: str,
    family: str,
    *,
    variants: Sequence[str] = ("",),
) -> str | None:
    """The fill a template gives *kind* on a cell of *family*, or None where it gives none.

    Two tiers, the narrower first: ``.highlight_sprint_p1`` before ``.highlight_p1``. A
    league wanting its sprint chips a shade darker than its feature ones declares both; one
    content with a single look declares only the second and pays nothing for the tier it did
    not use.

    *variants* walks a fallback within each tier — the raised qualifying glyph asks for
    ``_sup_text`` and settles for ``_text`` — and is ordered most specific first.
    """
    for variant in variants:
        for name in (
            f"{_HIGHLIGHT_SELECTOR}{family}_{kind}{variant}",
            f"{_HIGHLIGHT_SELECTOR}{kind}{variant}",
        ):
            paint = paints.get(name)
            if paint:
                return paint
    return None


def _project_highlight(
    field_id: str,
    suffix: str,
    cell: CellValue,
    declared,
    paints: Mapping[str, str],
    recolour: dict[str, str],
) -> None:
    """The chips beneath one cell, and the text colours that keep it readable.

    Two independent layers, as the specification asks: a **background** carrying the podium
    or points colour, and a **fastest-lap overlay** above it that may stand at the same time.
    Each is applied only where the template declares both the field to paint and a rule to
    paint it with, so a league opts in per cell and per kind and gets nothing it did not ask
    for.

    Neither rect is ever removed. They are authored transparent, so an unhighlighted cell
    contributes nothing at all to the spec — where removal would put a thousand ids into
    ``spec.remove`` per image and walk a subtree for each.

    The text colours run last and in one order: the **fastest lap wins** over the background
    beneath it, being the more specific signal. The raised qualifying glyph is recoloured
    with them, and this is not a highlight of the qualifying result — it says nothing about
    where the driver qualified. The chip spans the whole column and the glyph sits on top of
    it, so without this a P1 cell would draw the stylesheet's grey superscript on gold. That
    it overrides a colour the qualifying cell set for itself is deliberate: the chip is
    physically beneath the glyph, and legibility upon it is not optional.
    """
    family = suffix.split("_", 1)[0]
    stem = field_id[: -len("_result")]
    applied: str | None = None

    if cell.highlight:
        background_id = f"{stem}_background"
        paint = _paint(paints, cell.highlight, family)
        if paint and background_id in declared:
            recolour[background_id] = paint
            applied = cell.highlight

    if cell.fastest_lap:
        overlay_id = f"{stem}_{HIGHLIGHT_FASTEST_LAP}"
        paint = _paint(paints, HIGHLIGHT_FASTEST_LAP, family)
        if paint and overlay_id in declared:
            recolour[overlay_id] = paint
            applied = HIGHLIGHT_FASTEST_LAP

    if applied is None:
        return

    text_paint = _paint(paints, applied, family, variants=("_text",))
    if text_paint:
        recolour[field_id] = text_paint

    if not suffix.endswith("_race_result"):
        return
    sup_id = f"{stem[: -len('_race')]}_qualifying_result"
    if sup_id in declared:
        sup_paint = _paint(paints, applied, family, variants=("_sup_text", "_text"))
        if sup_paint:
            recolour[sup_id] = sup_paint


def _project_cells(
    cell_stem: str,
    sessions: Mapping[str, CellValue],
    declared,
    paints: Mapping[str, str],
    *,
    text: dict[str, str],
    empty_quietly: list[str],
    recolour: dict[str, str],
) -> None:
    """The four session cells hanging off one stem, filled and highlighted.

    Called by both grids — a drivers row's round, and a constructors car — so the cells of
    the two championships cannot drift apart.
    """
    for suffix in _CELL_SUFFIX_BY_SESSION.values():
        field_id = f"{cell_stem}_{suffix}"
        if field_id not in declared:
            continue
        cell = sessions.get(suffix) or CellValue()
        if cell.text:
            text[field_id] = cell.text
        else:
            empty_quietly.append(field_id)
        if paints:
            _project_highlight(field_id, suffix, cell, declared, paints, recolour)


def build_fill_spec(
    drawing: StandingsDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what leaves the canvas beside it.

    Raises :class:`StandingsDataError` where the template's rows cannot be counted — no row
    at all, or a gap in the numbering. Overflow is **not** raised here: it is reported
    through ``row_count`` so the render service issues the capacity problem in one place.
    """
    catalogue = catalogue_for(drawing.template_key)
    declared = FieldIndex(root).declared()

    try:
        capacity = catalogue.capacity(root) or 0
        round_capacity = catalogue.column_capacity(root) or 0
    except CapacityError as exc:
        raise StandingsDataError(str(exc)) from exc

    drawn = drawing.entries[:capacity]

    # Rounds of the division in excess of those the template declares are fatal, naming them
    # (FR-040). A template declaring **no** round at all is not overflowing: the grid is an
    # optional unit and such a template draws the classification alone (XIV.3).
    if round_capacity and len(drawing.rounds) > round_capacity:
        dropped = ", ".join(heading.number for heading in drawing.rounds[round_capacity:])
        raise StandingsDataError(
            f"the division holds {len(drawing.rounds)} rounds but the template declares "
            f"{round_capacity}. Enlarge the template, or rounds {dropped} would be "
            f"silently dropped."
        )
    drawn_rounds = drawing.rounds[:round_capacity]

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()
    recolour: dict[str, str] = {}
    paints = _highlight_paints(root)

    def put(field_id: str, value: str | None) -> None:
        """Fill where declared; empty rather than dash where the value does not apply.

        Every emptying here is of a value the data **determined** to be nothing — the gap of
        the first-placed entry, the previous position of an entry the reference round does
        not hold — so it is quiet and offends no mandatory field (XIV.3).
        """
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty_quietly.append(field_id)

    def put_optional(field_id: str, value: str | None) -> None:
        """A whole-graphic optional whose absence is worth a notice."""
        if field_id not in declared:
            return
        if value:
            text[field_id] = value
        else:
            empty.append(field_id)

    put("division_name", drawing.division_name)
    put("round_number", drawing.round_number)
    put("result_status", drawing.result_status_label)
    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)
    put_optional("race_name", drawing.race_name)

    for entry in drawn:
        stem = f"{_ROW_PREFIX}_{entry.ordinal}"
        # The **recorded** position, not the ordinal — see StandingsEntry.position. The two
        # part company exactly when a reserve who raced is filtered out of the drawing, and
        # the textual standings print the recorded one (XIV.7).
        put(f"{stem}_position", entry.position)
        put(f"{stem}_team_name", entry.team_name)
        put(f"{stem}_points", entry.points)
        if drawing.is_drivers:
            put(f"{stem}_driver_name", entry.driver_name)

        if f"{stem}_team_image" in declared:
            image_data[f"{stem}_team_image"] = ("team", entry.team_name)

        # The flag, in its three states. A nationality the league collects but this driver
        # did not state is an ordinary emptied optional and reports as one; a league that
        # switched collection off at its source has configured a graphic with no flags at
        # all, and that is a legitimate outcome raising nothing (XIV.4).
        flag_id = f"{stem}_driver_flag"
        if drawing.is_drivers and flag_id in declared:
            # The switch is read **first**. A driver who stated a nationality before the
            # league turned collection off still holds one, and testing the value first
            # would draw their flag while their team-mates went without — the graphic
            # disagreeing with itself, and with the preview, which blanks all of them.
            if not drawing.nationality_collected:
                empty_quietly.append(flag_id)
            elif entry.nationality:
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality))
            else:
                empty.append(flag_id)

        _project_movement(
            entry,
            stem,
            declared,
            text=text,
            empty_quietly=empty_quietly,
            remove=remove,
            image_data=image_data,
            off_canvas=off_canvas,
        )

        _project_grid_row(
            entry,
            stem,
            drawn_rounds,
            catalogue,
            declared,
            drivers=drawing.is_drivers,
            team_seat_counts=drawing.team_seat_counts,
            paints=paints,
            text=text,
            empty_quietly=empty_quietly,
            remove=remove,
            recolour=recolour,
        )

    # The round headings actually drawn — a column heading, so it draws the round's
    # **country flag** and never a circuit map: no circuit outline survives this size
    # (Constitution XIV.13, 044). The league's nationality-collection switch does not reach
    # it — it stands for the round, not for a driver.
    for heading in drawn_rounds:
        stem = f"{_ROUND_PREFIX}_{heading.ordinal}"
        put(f"{stem}_number", heading.number)
        flag_id = f"{stem}_flag"
        if flag_id in declared:
            if heading.country:
                image_data[flag_id] = ("flag", heading.country)
            else:
                remove.append(flag_id)

    # Rounds of the division's calendar beyond those the template declares are trimmed
    # silently, taking the heading and every row's cells of that round with them — one
    # capacity decision reaching every family the round governs (XIV.12).
    for ordinal in range(len(drawn_rounds) + 1, round_capacity + 1):
        group_id = f"{_ROUND_PREFIX}_{ordinal}_group"
        bearing = _round_ids(declared, ordinal)
        off_canvas.update(bearing)
        if group_id in declared:
            remove.append(group_id)
            remove.extend(
                name
                for name in bearing
                if name.startswith(f"{_ROW_PREFIX}_") and name not in remove
            )
        else:
            remove.extend(name for name in bearing if name not in remove)

    # Rows the template declares beyond the classification's entries. Each leaves by its
    # group, which this type makes mandatory, and every field it takes with it is off the
    # canvas and therefore not unresolved (XIV.3). No error is reported (FR-037).
    for ordinal in range(len(drawn) + 1, capacity + 1):
        group_id = f"{_ROW_PREFIX}_{ordinal}_group"
        off_canvas.update(_row_fields_declared(declared, ordinal))
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name
                for name in _row_fields_declared(declared, ordinal)
                if name not in remove
            )

    spec = FillSpec(
        root=root,
        image_type=drawing.template_key,
        text=text,
        empty=empty,
        empty_quietly=empty_quietly,
        remove=remove,
        recolour=recolour,
        off_canvas=off_canvas,
        row_count=drawing.entry_count,
        image_data=image_data,
        catalogue=catalogue,
        # Shorten the canvas to the rows this division actually fills, carrying the
        # caption band beneath them up with it (XIV.2, v7.1.0). A template declaring no
        # crop point is drawn at its full height, exactly as before.
        **row_crop_fields(declared, drawn=len(drawn), capacity=capacity),
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec


def _project_grid_row(
    entry: StandingsEntry,
    stem: str,
    drawn_rounds: Sequence[RoundHeading],
    catalogue,
    declared,
    *,
    drivers: bool,
    team_seat_counts: Mapping[str, int],
    paints: Mapping[str, str],
    text: dict[str, str],
    empty_quietly: list[str],
    remove: list[str],
    recolour: dict[str, str],
) -> None:
    """One row's cells across the rounds actually drawn.

    The drivers grid fills a session cell per round. The constructors grid additionally
    allocates each round's cars: a car beyond the row's team's own configured seats is
    trimmed silently regardless of any driver a substitution allocated to it — the fill
    ceiling is the team's seats, never the template's declared room, which bounds only the
    fatal case of a driven car the template has nowhere to put at all (FR-041).
    """
    for heading in drawn_rounds:
        cell = entry.cells.get(heading.ordinal)
        round_stem = f"{stem}_{_ROUND_PREFIX}_{heading.ordinal}"

        if drivers:
            _project_cells(
                round_stem,
                cell.sessions if cell else {},
                declared,
                paints,
                text=text,
                empty_quietly=empty_quietly,
                recolour=recolour,
            )
            continue

        car_nest = (
            catalogue.rows.nested.nested
            if catalogue.rows is not None and catalogue.rows.nested is not None
            else None
        )
        car_capacity = car_nest.declared_capacity(round_stem, declared) if car_nest else 0
        cars = cell.cars if cell else {}

        overflow = [ordinal for ordinal in cars if ordinal > car_capacity]
        if overflow:
            raise StandingsDataError(
                f"row {entry.ordinal}, round {heading.ordinal} records a driver in car "
                f"{max(overflow)} but the template declares only {car_capacity} car "
                f"{'slot' if car_capacity == 1 else 'slots'} there. Enlarge the template."
            )

        car_prefix = car_nest.prefix if car_nest else "driver"
        seat_count = team_seat_counts.get(entry.team_name)
        fill_ceiling = car_capacity if seat_count is None else min(car_capacity, seat_count)
        for car_ordinal in range(1, car_capacity + 1):
            car_stem = f"{round_stem}_{car_prefix}_{car_ordinal}"
            allocated = cars.get(car_ordinal) if car_ordinal <= fill_ceiling else None
            if allocated is None:
                group_id = f"{car_stem}_group"
                if group_id in declared:
                    remove.append(group_id)
                else:
                    remove.extend(
                        name
                        for name in declared
                        if (name == car_stem or name.startswith(f"{car_stem}_"))
                        and name not in remove
                    )
                continue

            name, sessions = allocated
            name_id = f"{car_stem}_name"
            if name_id in declared:
                if name:
                    text[name_id] = name
                else:
                    empty_quietly.append(name_id)
            _project_cells(
                car_stem,
                sessions,
                declared,
                paints,
                text=text,
                empty_quietly=empty_quietly,
                recolour=recolour,
            )


def _project_movement(
    entry: StandingsEntry,
    stem: str,
    declared,
    *,
    text: dict[str, str],
    empty_quietly: list[str],
    remove: list[str],
    image_data: dict[str, tuple[str, str]],
    off_canvas: set[str],
) -> None:
    """The three derived columns, and what happens when there is no record.

    A **block group** (XIV.2) wraps the number and the marker, which stand or fall together.
    Where the record is absent the group leaves whole; where the template declares none, the
    number is emptied and the marker removed one by one. The previous position is emptied in
    either case. None of it raises a notice: these are values the data determined to be
    absent, not values that could not be determined (XIV.3, XIV.4).
    """
    group_id = f"{stem}_position_change_group"
    number_id = f"{stem}_position_change"
    marker_id = f"{stem}_position_change_marker"
    previous_id = f"{stem}_previous_position"
    gap_id = f"{stem}_gap_to_leader"

    movement = entry.movement

    if movement is None:
        if previous_id in declared:
            empty_quietly.append(previous_id)
        if group_id in declared:
            remove.append(group_id)
            off_canvas.update({number_id, marker_id} & set(declared))
        else:
            for field_id in (number_id, marker_id):
                if field_id in declared:
                    empty_quietly.append(field_id)
        # The gap needs only the classification being drawn, so it is never in this state.
        _put_gap(entry, gap_id, declared, text=text, empty_quietly=empty_quietly)
        return

    if previous_id in declared:
        text[previous_id] = results_formatter.format_previous_position(
            movement.previous_position
        )
    if number_id in declared:
        text[number_id] = results_formatter.format_position_change(movement.change)
    if marker_id in declared:
        image_data[marker_id] = ("marker", movement.direction)

    _put_gap(entry, gap_id, declared, text=text, empty_quietly=empty_quietly)


def _put_gap(
    entry: StandingsEntry,
    gap_id: str,
    declared,
    *,
    text: dict[str, str],
    empty_quietly: list[str],
) -> None:
    """The gap to the leader — empty for the first-placed entry, and never otherwise absent.

    It is arithmetic over the classification being drawn alone, so unlike the other two
    columns it is available even where no earlier round holds standings. Tying it to the
    movement record once blanked it for every entry the reference round did not hold.
    """
    if gap_id not in declared:
        return
    if entry.ordinal == 1 or entry.gap_to_leader is None:
        empty_quietly.append(gap_id)
        return
    rendered = results_formatter.format_gap_to_leader(
        entry.gap_to_leader, is_leader=False
    )
    if rendered:
        text[gap_id] = rendered
    else:
        empty_quietly.append(gap_id)
