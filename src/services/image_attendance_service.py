"""Resolve and project an attendance sheet — one division, as it stands after one round.

The sheet re-presents what the textual sheet shows and derives nothing of its own. The points
each round conferred are the sole value it carries that the textual sheet does not, and they
are **read** from the record the attendance module persisted for that round, never computed
here (Constitution XIV.7).

**An empty round cell means zero.** Six situations produce one — the round conferred none, its
attendance is not yet finalised, it is yet to be run, it is recorded as cancelled, the driver
holds no record for it, or a pardon waived every point it would have conferred. None of them is
a value that could not be *determined*, so none raises a notice: the sheet lists the points a
round conferred and never the reason (XIV.3, and the author's ruling of 2026-08-13).

**The row ordinal is a place in the layout and not a datum** (XIV.11, v4.6.0). The sheet is a
record and not a classification: rows are ordered by total accrued, two drivers level on totals
stand level, and no position is drawn. A numbered row would publish a ranking the module never
computed.

**The rows carry a floor** (XIV.12, v4.6.0): a division holding no driver has no sheet to draw,
and that is fatal rather than an empty canvas. It is raised against the concrete data before any
template is measured, as the calendar's floor is, so the report names the division rather than
complaining about a template that is not at fault.

See specs/041-attendance-image-generation/contracts/ — attendance-catalogues.md for the fields,
sibling-and-floor.md for the floor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

from models.image_catalogues import CapacityError, catalogue_for, row_crop_fields
from utils.svg_document import FieldIndex
from utils.svg_fill import FillSpec
from utils.country_data import country_for_nationality

ATTENDANCE_TEMPLATE_KEY = "attendance_template"

_ROW_PREFIX = "row"
_ROUND_PREFIX = "round"

#: The annotation a driver sanctioned upon this posting carries. The textual sheet appends
#: " *(reached point limit)*"; the graphic draws the plain literal, the emphasis being message
#: formatting rather than a value. It reads the same for the autoreserve and the autosack
#: sanctions: the sheet is not where they are told apart, and the verdict announced for the
#: driver names which was enforced.
SANCTION_ANNOTATION = "Reached point limit"


#: The two labels the one limit plate can carry. The sheet draws a single plate because a
#: league can only have a single limit: `/attendance config autoreserve` and
#: `/attendance config autosack` refuse each other, so a template declaring one block for each
#: would always draw one and delete the other, leaving a hole beside the survivor. The label
#: is therefore a field the projection fills, as `SANCTION_ANNOTATION` is a literal it draws.
LIMIT_LABEL_RESERVE = "RESERVE AT"
LIMIT_LABEL_SACK = "SACKED AT"

#: The two marks a driver's total may be drawn against, and the class they are artwork of.
#: `marker` is shared with the standings result chips and the position-change arrows: three
#: vocabularies of the module's own, closed all the way down, and one folder for a league to
#: redraw them in.
MARK_NEAR = "attendance_limit_near"
MARK_REACHED = "attendance_limit_reached"
MARK_ASSET_CLASS = "marker"

#: Every datum the projection can hand to the asset resolver, named so a test can hold the
#: packaged folder to it — a mark added here without a file would resolve to the fallback and
#: draw the wrong picture rather than fail.
MARK_DATA: tuple[str, ...] = (MARK_NEAR, MARK_REACHED)

#: How many points below the limit the near mark reaches. A driver on the limit less one or
#: less two is two points away or fewer; one on the limit less three is not marked at all.
MARK_NEAR_BAND = 2


class AttendanceDataError(Exception):
    """A fatal disagreement between a division and what an attendance sheet needs.

    Raised before anything is drawn. The caller turns it into a Problem, which abandons this
    graphic — and this division's graphic alone, the other divisions answering for themselves
    (XIV.4).
    """


@dataclass(frozen=True)
class DriverRecord:
    """One driver's attendance in one division, as the module persisted it.

    *key* identifies the driver for the name, nationality and team lookups — the Discord user
    id, matching what the textual sheet resolves by.

    *round_points* maps a round's **ordinal** to the points that round conferred. A missing
    ordinal and a stored ``None`` are the same as a stored ``0``: the round counted nothing.
    """

    key: int
    total: int
    round_points: Mapping[int, int | None] = field(default_factory=dict)
    sanctioned: bool = False


@dataclass(frozen=True)
class SheetEntry:
    """One row of the sheet, resolved and ready to be projected."""

    ordinal: int
    driver_name: str
    points: str
    team_name: str = ""
    nationality: str | None = None
    sanction: str = ""
    #: The mark drawn beneath the total, or None where the driver has earned none. It travels
    #: with the row rather than being derived at projection time for the reason the standings
    #: ``CellValue`` carries its highlight beside its text: they are two readings of one
    #: number, and deriving them apart is how a mark comes to disagree with the total above it.
    mark: str | None = None
    #: Round ordinal → the cell's text. An empty string is zero and is drawn empty.
    round_points: Mapping[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RoundHeading:
    """One column of the grid: a round the division holds, run or not."""

    ordinal: int
    number: str
    track: str | None = None

    #: The country the round is run in — the datum its flag resolves by (044).
    #: ``track`` is retained: it still names the round and decides the mystery case,
    #: it simply stopped being an asset datum when the heading moved to the flag class.
    country: str | None = None


@dataclass(frozen=True)
class AttendanceDrawing:
    """A division's attendance record, resolved and ready to project onto a template."""

    division_name: str
    round_number: str
    template_key: str = ATTENDANCE_TEMPLATE_KEY
    division_tier: str | None = None
    season_number: str | None = None
    race_name: str | None = None
    #: The one limit plate: what it is called, and the number on it. Both are None together
    #: where neither functionality is switched on, and the plate leaves the canvas whole.
    limit_label: str | None = None
    limit_value: str | None = None
    #: True where the league collects a driver's nationality at all. Where it does not, an
    #: empty flag field is exactly what was configured and raises nothing (XIV.4).
    nationality_collected: bool = True
    entries: list[SheetEntry] = field(default_factory=list)
    rounds: list[RoundHeading] = field(default_factory=list)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def round_count(self) -> int:
        return len(self.rounds)


# ── 1. Resolution ─────────────────────────────────────────────────────────


def cell_text(points: int | None) -> str:
    """The text of one round cell.

    Zero and absent are the same picture and the same meaning. This is the whole of the
    six-case collapse: the sheet reports what a round conferred, and nothing conferred is
    nothing drawn.
    """
    return "" if not points else str(points)


def active_limit(
    *, autoreserve_threshold: int | None, autosack_threshold: int | None
) -> tuple[str, int] | None:
    """The one limit the sheet answers to — its label and its value — or None where neither is on.

    A league can only have one. `/attendance config autoreserve` and `/attendance config
    autosack` each refuse to be set while the other is active, so exactly one of the two is
    ever a number and the sheet has one plate and one set of marks rather than two of each.

    Auto-sack wins where a database somehow holds both, because that is what the enforcement
    does: ``enforce_attendance_sanctions`` tests the sack threshold first and moves on to the
    next driver, so a sheet marking against the reserve limit would warn of a sanction that
    could no longer be the one applied.

    0 and None are the same thing here as everywhere else in the module: switched off.
    """
    if autosack_threshold:
        return LIMIT_LABEL_SACK, autosack_threshold
    if autoreserve_threshold:
        return LIMIT_LABEL_RESERVE, autoreserve_threshold
    return None


def mark_for(total: int | None, limit: int | None) -> str | None:
    """The mark a driver's *total* earns against *limit*, or None where it earns none.

    Two tiers, and *limit* is whichever of the two thresholds is live — the mark says a driver
    is close to losing their seat and not which of the two ways they would lose it, exactly as
    ``SANCTION_ANNOTATION`` does once they have.

    ``>=`` at the limit, matching ``enforce_attendance_sanctions``: the sheet must not draw a
    driver as merely close when the same number has already sanctioned them.

    **A total of zero never marks.** The near band is otherwise anchored below zero for a limit
    of one or two, which would paint the mark across every driver on a clean sheet — a warning
    of nothing, drawn over almost the whole column.
    """
    if not limit or not total:
        return None
    if total >= limit:
        return MARK_REACHED
    if total >= max(1, limit - MARK_NEAR_BAND):
        return MARK_NEAR
    return None


def resolve_drawing(
    *,
    division_name: str,
    round_number: str | int,
    records: Sequence[DriverRecord],
    display_names: Mapping[int, str],
    team_names: Mapping[int, str] | None = None,
    nationalities: Mapping[int, str | None] | None = None,
    rounds: Sequence[RoundHeading] | None = None,
    autoreserve_threshold: int | None = None,
    autosack_threshold: int | None = None,
    division_tier: str | int | None = None,
    season_number: str | int | None = None,
    race_name: str | None = None,
    nationality_collected: bool = True,
) -> AttendanceDrawing:
    """Resolve every value an attendance sheet draws.

    *records* are the drivers the textual sheet composes — every non-reserve driver of the
    division, every reserve distributed into a seat for the round, and every driver sanctioned
    upon this posting. Composing them is the caller's, as it is the textual sheet's; a driver
    sacked at an earlier round holds no seat and never reaches here.

    Raises :class:`AttendanceDataError` where the division holds no driver at all — the floor
    of XIV.12, raised here against the data and before any template is in view.
    """
    if not records:
        raise AttendanceDataError(
            f"the division `{division_name}` holds no driver at all, so there is no "
            f"attendance sheet to draw"
        )

    team_map = team_names or {}
    nationality_map = nationalities or {}

    # The one limit the plate names and the marks are measured against, decided once so a
    # row can never be marked against a limit other than the one drawn above it.
    limit = active_limit(
        autoreserve_threshold=autoreserve_threshold,
        autosack_threshold=autosack_threshold,
    )

    def order(record: DriverRecord) -> tuple[int, str]:
        # The textual sheet's own order: total descending, then alphabetical on the name
        # actually resolved — so the tie-break reads the same string the graphic draws.
        name = display_names.get(record.key, str(record.key))
        return (-(record.total or 0), name.lower())

    entries: list[SheetEntry] = []
    for record in sorted(records, key=order):
        entries.append(
            SheetEntry(
                ordinal=len(entries) + 1,
                driver_name=display_names.get(record.key, str(record.key)),
                points=str(record.total or 0),
                team_name=team_map.get(record.key, ""),
                nationality=nationality_map.get(record.key),
                sanction=SANCTION_ANNOTATION if record.sanctioned else "",
                mark=mark_for(record.total, limit[1] if limit else None),
                round_points={
                    ordinal: cell_text(value)
                    for ordinal, value in record.round_points.items()
                },
            )
        )

    return AttendanceDrawing(
        division_name=division_name,
        round_number=str(round_number),
        division_tier=None if division_tier is None else str(division_tier),
        season_number=None if season_number is None else str(season_number),
        race_name=race_name,
        # Both thresholds off is the functionality switched off, which is a configured
        # absence: the block leaves whole and nothing is reported (XIV.4).
        limit_label=limit[0] if limit else None,
        limit_value=str(limit[1]) if limit else None,
        nationality_collected=nationality_collected,
        entries=entries,
        rounds=list(rounds or []),
    )


# ── 2. Projection ─────────────────────────────────────────────────────────


def _ids_bearing(declared, stem: str) -> list[str]:
    """Every id the template declares that is *stem* or hangs beneath it."""
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def _round_ids(declared, ordinal: int) -> list[str]:
    """Every id bearing round *ordinal*, across **both** families it governs.

    A round's ordinal stands as ``round_<z>_*`` at top level and as
    ``row_<x>_round_<z>_points`` on every row. One capacity decision removes them all
    (XIV.12) — containment cannot carry the cells, a cell belonging to its row and its column
    both while a node of an SVG file has one parent (XIV.2).
    """
    ids = set(_ids_bearing(declared, f"{_ROUND_PREFIX}_{ordinal}"))
    suffix = f"_{_ROUND_PREFIX}_{ordinal}"
    ids.update(
        name
        for name in declared
        if name.startswith(f"{_ROW_PREFIX}_") and (suffix in name)
    )
    return sorted(ids)


def build_fill_spec(
    drawing: AttendanceDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what leaves the canvas beside it.

    Raises :class:`AttendanceDataError` where the template's rows cannot be counted — no row at
    all, or a gap in the numbering. Overflow is **not** raised here: it is reported through
    ``row_count`` so the render service issues the capacity problem in one place.
    """
    catalogue = catalogue_for(drawing.template_key)
    declared = FieldIndex(root).declared()

    try:
        capacity = catalogue.capacity(root) or 0
        round_capacity = catalogue.column_capacity(root) or 0
    except CapacityError as exc:
        raise AttendanceDataError(str(exc)) from exc

    drawn = drawing.entries[:capacity]
    drawn_rounds = drawing.rounds[:round_capacity]

    # Rounds of the division in excess of those the template declares are fatal, naming them
    # (FR-039). A template declaring **no** round at all is not overflowing: the grid is an
    # optional unit and such a template draws the totals alone (XIV.3).
    if round_capacity and len(drawing.rounds) > round_capacity:
        dropped = ", ".join(
            heading.number for heading in drawing.rounds[round_capacity:]
        )
        raise AttendanceDataError(
            f"the division holds {len(drawing.rounds)} rounds but the template declares "
            f"{round_capacity}. Enlarge the template, or rounds {dropped} would be "
            f"silently dropped."
        )

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()

    def put(field_id: str, value: str | None) -> None:
        """Fill where declared; empty rather than dash where the value does not apply.

        Every emptying here is of a value the data **determined** to be nothing — a round that
        conferred no points, a driver carrying no sanction — so it is quiet and offends no
        mandatory field (XIV.3).
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
    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)
    put_optional("race_name", drawing.race_name)

    # The one point limit, named by its own label. Both functionalities switched off takes the
    # whole block off the canvas; where the template declares no block group, the two fields
    # alone are emptied. Neither raises a notice — the graphic is drawing exactly what the
    # league configured (XIV.4).
    if drawing.limit_value:
        put("limit_label", drawing.limit_label)
        put("limit_value", drawing.limit_value)
    elif "limit_group" in declared:
        off_canvas.update(_ids_bearing(declared, "limit_group"))
        remove.append("limit_group")
    else:
        empty_quietly.extend(
            name for name in ("limit_label", "limit_value") if name in declared
        )

    # ── The rows ──────────────────────────────────────────────────────────
    for entry in drawn:
        stem = f"{_ROW_PREFIX}_{entry.ordinal}"
        put(f"{stem}_driver_name", entry.driver_name)
        put(f"{stem}_points", entry.points)
        put(f"{stem}_team_name", entry.team_name)
        put(f"{stem}_sanction", entry.sanction)

        # The mark beneath the total. A row earning none is **left alone**, neither filled nor
        # removed: the slot is an `<image>` the template ships with no href, which draws
        # nothing and which `_unreachable_links` passes over. Removing it instead would put
        # fifty ids into `spec.remove` per sheet and walk a subtree for each.
        mark_id = f"{stem}_points_background"
        if entry.mark and mark_id in declared:
            image_data[mark_id] = (MARK_ASSET_CLASS, entry.mark)

        if f"{stem}_team_image" in declared:
            if entry.team_name:
                image_data[f"{stem}_team_image"] = ("team", entry.team_name)
            else:
                remove.append(f"{stem}_team_image")

        # The flag, in its three states. A nationality the league collects but this driver did
        # not state is an ordinary emptied optional and reports as one; a league that switched
        # collection off at its source has configured a graphic with no flags at all, and that
        # is a legitimate outcome raising nothing (XIV.4).
        flag_id = f"{stem}_driver_flag"
        if flag_id in declared:
            # The switch is read **first**: a driver who stated a nationality before the
            # league turned collection off still holds one, and testing the value first
            # would draw their flag alone among a sheet of blanks.
            if not drawing.nationality_collected:
                remove.append(flag_id)
            elif entry.nationality:
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality))
            else:
                remove.append(flag_id)
                empty.append(flag_id)

        # The cells of the rounds actually drawn. Zero is empty (see ``cell_text``).
        for heading in drawn_rounds:
            put(
                f"{stem}_{_ROUND_PREFIX}_{heading.ordinal}_points",
                entry.round_points.get(heading.ordinal, ""),
            )

    # ── The round headings ────────────────────────────────────────────────
    for heading in drawn_rounds:
        stem = f"{_ROUND_PREFIX}_{heading.ordinal}"
        put(f"{stem}_number", heading.number)
        # A round stands here as a column heading, so it draws its **country flag** and
        # never a circuit map: no circuit outline survives this size (044, XIV.13). The
        # flag stands for the round, not for a driver, so the league's nationality
        # collection switch does not reach it.
        flag_id = f"{stem}_flag"
        if flag_id in declared:
            if heading.country:
                image_data[flag_id] = ("flag", heading.country)
            else:
                remove.append(flag_id)

    # ── What the template declares beyond the data ────────────────────────
    # Rows beyond the drivers: each leaves by its group, which this type makes mandatory, and
    # every field it takes with it is off the canvas and therefore not unresolved (XIV.3). No
    # error is reported.
    for ordinal in range(len(drawn) + 1, capacity + 1):
        group_id = f"{_ROW_PREFIX}_{ordinal}_group"
        off_canvas.update(_ids_bearing(declared, f"{_ROW_PREFIX}_{ordinal}"))
        if group_id in declared:
            remove.append(group_id)
        else:
            remove.extend(
                name
                for name in _ids_bearing(declared, f"{_ROW_PREFIX}_{ordinal}")
                if name not in remove
            )

    # Rounds beyond the division's calendar: the heading group leaves, and the cell of that
    # ordinal on **every** row leaves with it — through this rule, not through containment.
    for ordinal in range(len(drawn_rounds) + 1, round_capacity + 1):
        group_id = f"{_ROUND_PREFIX}_{ordinal}_group"
        bearing = _round_ids(declared, ordinal)
        off_canvas.update(bearing)
        if group_id in declared:
            remove.append(group_id)
            # The cells are not inside the heading group and must be named separately.
            remove.extend(
                name
                for name in bearing
                if name.startswith(f"{_ROW_PREFIX}_") and name not in remove
            )
        else:
            remove.extend(name for name in bearing if name not in remove)

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
        # Shorten the canvas to the rows this division actually fills, carrying the
        # caption band beneath them up with it (XIV.2, v7.1.0). A template declaring no
        # crop point is drawn at its full height, exactly as before.
        **row_crop_fields(declared, drawn=len(drawn), capacity=capacity),
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec
