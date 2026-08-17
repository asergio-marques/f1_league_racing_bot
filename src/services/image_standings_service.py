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

from models.image_catalogues import CapacityError, catalogue_for
from utils import results_formatter
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec
from utils.country_data import country_for_nationality

DRIVERS_TEMPLATE_KEY = "standings_drivers_template"
CONSTRUCTORS_TEMPLATE_KEY = "standings_constructors_template"

_ROW_PREFIX = "row"

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


@dataclass(frozen=True)
class RoundCells:
    """The cells of one round on one row.

    Filled in US3 (the season grid). A cell's value is a finishing position or an outcome
    literal; an **empty string** means the data determined it to be nothing — no session of
    that type, a round unrun or cancelled, or a driver who took no part — and is emptied
    quietly rather than dashed (XIV.3).
    """

    #: Session key → cell text, for a drivers row.
    sessions: dict[str, str] = field(default_factory=dict)
    #: Car ordinal → (driver name or None, session key → cell text), for a constructors row.
    cars: dict[int, tuple[str | None, dict[str, str]]] = field(default_factory=dict)


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
    #: Round ordinal → its cells. Empty until US3.
    cells: dict[int, RoundCells] = field(default_factory=dict)


@dataclass(frozen=True)
class RoundHeading:
    """One column of the grid. Filled in US3."""

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
) -> StandingsDrawing:
    """Resolve every value a standings graphic draws.

    *snapshots* are the persisted ``DriverStandingsSnapshot`` or ``TeamStandingsSnapshot``
    rows of the round being drawn. Their ``standing_position`` is the order — already
    separated by the countback — and is read, never re-established (XIV.12).

    *movements* comes from ``standings_service.derive_movement`` and is carried through
    untouched. Nothing here subtracts points or compares positions.
    """
    drivers = template_key == DRIVERS_TEMPLATE_KEY
    reserves = reserve_user_ids or set()
    nationality_map = nationalities or {}
    gap_map = gaps or {}

    ordered = sorted(snapshots, key=lambda s: s.standing_position)

    entries: list[StandingsEntry] = []
    for snapshot in ordered:
        # Composition is the textual standings' own rule, called and not restated (XIV.7).
        if drivers and not results_formatter.driver_is_drawn(
            snapshot, reserves, show_reserves
        ):
            continue

        key = _entry_key(snapshot, drivers=drivers)
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
            )
        )

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
    )


# ── 2. Projection ─────────────────────────────────────────────────────────


def _row_fields_declared(declared, ordinal: int) -> list[str]:
    """Every id the template declares bearing *ordinal*, group included."""
    stem = f"{_ROW_PREFIX}_{ordinal}"
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


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
    except CapacityError as exc:
        raise StandingsDataError(str(exc)) from exc

    drawn = drawing.entries[:capacity]

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()

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
            if entry.nationality:
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality))
            elif drawing.nationality_collected:
                empty.append(flag_id)
            else:
                empty_quietly.append(flag_id)

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
        off_canvas=off_canvas,
        row_count=drawing.entry_count,
        image_data=image_data,
        catalogue=catalogue,
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec


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
