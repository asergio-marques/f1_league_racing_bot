"""Project an attendance sheet — one division, as it stands after one round — onto its template.

The sheet's models, and their resolution from the rows the attendance module composes, are
image's models (`leaguebot.image.models.attendance_drawing`); this file projects the resolved
drawing onto the template and declares nothing the attendance module composes.

**The row ordinal is a place in the layout and not a datum** (XIV.11, v4.6.0). The sheet is a
record and not a classification: rows are ordered by total accrued, two drivers level on totals
stand level, and no position is drawn. A numbered row would publish a ranking the module never
computed.

See specs/041-attendance-image-generation/contracts/ — attendance-catalogues.md for the fields,
sibling-and-floor.md for the floor.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from leaguebot.image.models.image_catalogues import (
    DIVISION_LOGO_ASSET,
    DIVISION_LOGO_FIELD,
    CapacityError,
    catalogue_for,
    column_crop_fields,
    row_crop_fields,
)
from leaguebot.image.utils.svg_document import FieldIndex
from leaguebot.image.utils.svg_fill import FillSpec
from leaguebot.image.utils.country_data import country_for_nationality

# The sheet's models and their resolution are image's models, which the attendance module
# composes its sheet through (`leaguebot.image.models.attendance_drawing`); this file projects them.
from leaguebot.image.models.attendance_drawing import (  # noqa: F401 — re-exported for image's own callers
    ATTENDANCE_TEMPLATE_KEY,
    SANCTION_ANNOTATION,
    LIMIT_LABEL_RESERVE,
    LIMIT_LABEL_SACK,
    MARK_NEAR,
    MARK_REACHED,
    MARK_ASSET_CLASS,
    MARK_DATA,
    MARK_NEAR_BAND,
    AttendanceDataError,
    DriverRecord,
    SheetEntry,
    RoundHeading,
    AttendanceDrawing,
    cell_text,
    active_limit,
    mark_for,
    resolve_drawing,
)

_ROW_PREFIX = "row"
_ROUND_PREFIX = "round"


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

    # The division's logo, where a league's own template declares the slot (2026-09-02).
    if DIVISION_LOGO_FIELD in declared:
        image_data[DIVISION_LOGO_FIELD] = (DIVISION_LOGO_ASSET, drawing.division_name)
    put("classification_label", drawing.classification_label)
    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)
    # See the standings sheet: a season-boundary sheet names no race, and its absence is
    # what the occasion determined rather than a fault worth a notice.
    if drawing.names_a_race:
        put_optional("race_name", drawing.race_name)
    else:
        put("race_name", None)

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
                image_data[f"{stem}_team_image"] = ("team", entry.team_key or entry.team_name)
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
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality) or "")
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
        # Narrow the canvas to the rounds this division actually runs, carrying the
        # sanction column in beside them (2026-09-02). A template declaring no horizontal
        # crop point is drawn at its full width, exactly as before.
        **column_crop_fields(
            declared, drawn=len(drawn_rounds), capacity=round_capacity
        ),
    )
    if asset_directories:
        spec.asset_directories = dict(asset_directories)
    return spec
