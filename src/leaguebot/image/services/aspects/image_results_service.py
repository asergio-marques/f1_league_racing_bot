"""Project one session's results drawing onto a template (039).

The drawing is composed by the results module, which owns the classification: every value in it is
decided and nothing is drawn (``leaguebot.results.services.publishing.results_drawing_composer``).
:func:`build_fill_spec` projects it onto a parsed template, deciding which rows leave, which
column groups leave, and which single row is recoloured.

**This module renders nothing and works nothing out.** Every value the graphic and the textual
table both draw arrives already formatted, which is Constitution XIV.7 as amended at v4.4.0, and
why no lap time, gap, interval, lap count or penalty is formatted below. See
specs/039-results-image-generation/contracts/shared-rendering.md.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Mapping

from leaguebot.image.models.results_drawing import (  # noqa: F401 — the models are image's, kept importable here
    QUALIFYING_TEMPLATE_KEY,
    RACE_TEMPLATE_KEY,
    FastestLapBlock,
    ResultsDataError,
    ResultsDrawing,
    ResultsEntry,
)
from leaguebot.image.models.image_catalogues import (
    DIVISION_LOGO_ASSET,
    DIVISION_LOGO_FIELD,
    CapacityError,
    catalogue_for,
    row_crop_fields,
)
from leaguebot.image.utils.svg_document import FieldIndex
from leaguebot.image.utils.svg_fill import FillSpec
from leaguebot.image.utils.country_data import country_for_nationality

log = logging.getLogger(__name__)

_ROW_PREFIX = "row"


def _row_fields_declared(declared, ordinal: int) -> list[str]:
    """Every id the template declares bearing *ordinal*, group included."""
    stem = f"{_ROW_PREFIX}_{ordinal}"
    return sorted(
        name for name in declared if name == stem or name.startswith(f"{stem}_")
    )


def build_fill_spec(
    drawing: ResultsDrawing,
    root,
    *,
    asset_directories: Mapping[str, Path] | None = None,
) -> FillSpec:
    """Project *drawing* onto *root*, deciding what leaves the canvas beside it.

    Raises :class:`ResultsDataError` where the template's rows cannot be counted — no row at
    all, or a gap in the numbering. Overflow is **not** raised here: it is reported through
    ``row_count`` so the render service issues the capacity problem in one place, naming the
    count, the capacity and the template.
    """
    catalogue = catalogue_for(drawing.template_key)
    index = FieldIndex(root)
    declared = index.declared()

    try:
        capacity = catalogue.capacity(root) or 0
    except CapacityError as exc:
        raise ResultsDataError(str(exc)) from exc

    drawn = drawing.entries[:capacity]

    text: dict[str, str] = {}
    empty: list[str] = []
    empty_quietly: list[str] = []
    remove: list[str] = []
    recolour: dict[str, str] = {}
    image_data: dict[str, tuple[str, str]] = {}
    off_canvas: set[str] = set()

    def put(field_id: str, value: str | None) -> None:
        """Fill where declared; empty rather than dash where the value does not apply.

        Every emptying here is of a value the data **determined** to be nothing — a
        sanction column of a phase not yet closed, the gap of the entry holding the
        reference lap — so it is quiet and offends no mandatory field (XIV.3).
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
    put("round_number", drawing.round_number)
    put("race_name", drawing.race_name)
    put("session_name", drawing.session_name)
    put("result_status", drawing.result_status_label)
    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)

    # Column groups (XIV.2, v4.4.0). Each wraps its column's heading and no cell of any
    # row, so it leaves whole while that phase stands open — a heading over a column of
    # empty cells being a heading the generation cannot otherwise reach. A template
    # declaring neither carries its heading over an emptied column, which is meant.
    if not drawing.penalty_phase_closed and "postrace_penalty_group" in declared:
        remove.append("postrace_penalty_group")
    if not drawing.appeal_phase_closed and "appeal_penalty_group" in declared:
        remove.append("appeal_penalty_group")

    for entry in drawn:
        stem = f"{_ROW_PREFIX}_{entry.ordinal}"
        # Filled from the ordinal itself — the renumbering is already persisted, and a
        # comparison here could only disagree with a fact (XIV.11, v4.4.0).
        put(f"{stem}_position", str(entry.ordinal))
        put(f"{stem}_driver_name", entry.driver_name)
        put(f"{stem}_team_name", entry.team_name)
        put(f"{stem}_points", entry.points)
        put(f"{stem}_postrace_penalty", entry.postrace_penalty)
        put(f"{stem}_appeal_penalty", entry.appeal_penalty)

        if f"{stem}_team_image" in declared:
            image_data[f"{stem}_team_image"] = ("team", entry.team_key or entry.team_name)

        # The flag. A nationality the league collects but this driver did not state is an
        # ordinary emptied optional and reports as one; a league that switched collection
        # off at its source has configured a graphic with no flags, and that raises nothing.
        flag_id = f"{stem}_driver_flag"
        if flag_id in declared:
            # The switch is read **first**: a driver who stated a nationality before the
            # league turned collection off still holds one, and testing the value first
            # would draw their flag alone among a table of blanks.
            if not drawing.nationality_collected:
                empty_quietly.append(flag_id)
            elif entry.nationality:
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality) or "")
            else:
                empty.append(flag_id)

        if drawing.is_qualifying:
            put(f"{stem}_best_lap", entry.best_lap)
            put(f"{stem}_gap", entry.gap)
            # Always offered to the resolver: an absent compound draws the tyre class's
            # fallback and reports nothing, which the catalogue declares (XIV.13, v4.4.0).
            if f"{stem}_tyre" in declared:
                image_data[f"{stem}_tyre"] = ("tyre", entry.tyre or "")
        else:
            put(f"{stem}_time", entry.time)
            put(f"{stem}_fastest_lap", entry.fastest_lap)
            put(f"{stem}_ingame_penalty", entry.ingame_penalty)
            # The module's one data-driven recolour. It does not consume the field: the
            # fastest lap is filled above exactly as any other cell is (XIV.2).
            if (
                entry.holds_fastest_lap
                and drawing.fastest_lap_colour
                and f"{stem}_fastest_lap" in declared
            ):
                recolour[f"{stem}_fastest_lap"] = drawing.fastest_lap_colour

    # The fastest-lap block: a block group (XIV.2, v4.4.0) wrapping fields that stand or
    # fall together. Removed whole where the session conferred no bonus; where the template
    # declares no group, the fields alone are emptied.
    if not drawing.is_qualifying:
        if drawing.fastest_lap is not None:
            put("fastest_lap_driver_name", drawing.fastest_lap.driver_name)
            put("fastest_lap_time", drawing.fastest_lap.lap_time)
        elif "fastest_lap_group" in declared:
            remove.append("fastest_lap_group")
            # The legend saying a lap is marked by colour is untrue of a session that
            # conferred no bonus, so it leaves with the plate. It carries a group of its
            # own rather than sitting inside the plate's, because it is drawn in the
            # footer band the crop carries up and a node cannot be in two groups.
            if "fastest_lap_legend_group" in declared:
                remove.append("fastest_lap_legend_group")
            off_canvas.update(
                name
                for name in declared
                if name.startswith("fastest_lap_") and name != "fastest_lap_group"
            )
        else:
            put("fastest_lap_driver_name", None)
            put("fastest_lap_time", None)

    # Rows the template declares beyond the session's entries. Each leaves by its group,
    # which this type makes mandatory, and every field it takes with it is off the canvas
    # and therefore not unresolved (XIV.3).
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
