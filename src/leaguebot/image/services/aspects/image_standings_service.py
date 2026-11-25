"""Project a standings graphic onto its template — one championship of one division.

Two templates draw one aspect: the driver championship and the constructor championship. They
share every field but the columns of their rows, and each is its own catalogue entry
(Constitution XIV.10).

**What this module does not do.** It resolves nothing and composes nothing: the classification,
its season grid and its movements are composed by the results module, which owns them
(``standings_drawing_composer``), into the plain :class:`StandingsDrawing` image declares, and
arrive here finished. It performs no arithmetic over points or positions: the gap to the leader,
the previous position and the position change are derived in
``leaguebot.results.services.standings.standings_service.derive_movement``, XIV.7 as amended at
v4.5.0 admitting them as a derived *presentation* on the condition that the derivation lives with
the data. A subtraction appearing in this file would break that contract silently.

See specs/040-standings-image-generation/contracts/ — standings-catalogue.md for the fields and
derived-columns.md for what may be derived and where.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from leaguebot.image.models.image_catalogues import (
    DIVISION_LOGO_ASSET,
    DIVISION_LOGO_FIELD,
    CapacityError,
    DeclaredNames,
    catalogue_for,
    column_crop_fields,
    row_crop_fields,
)
from leaguebot.image.models.standings_drawing import (  # noqa: F401 — the models are image's, kept importable here
    CELL_SUFFIXES,
    CONSTRUCTORS_TEMPLATE_KEY,
    CellValue,
    DRIVERS_TEMPLATE_KEY,
    FASTEST_LAP_DATUM,
    HIGHLIGHT_ASSET_CLASS,
    HIGHLIGHT_DATA,
    HIGHLIGHT_FASTEST_LAP,
    HIGHLIGHT_KINDS,
    HIGHLIGHT_P1,
    HIGHLIGHT_P2,
    HIGHLIGHT_P3,
    HIGHLIGHT_POINTS,
    QUALIFYING_DATUM_PREFIX,
    RACE_DATUM_PREFIX,
    RoundCells,
    RoundHeading,
    StandingsDataError,
    StandingsDrawing,
    StandingsEntry,
)
from leaguebot.image.utils.svg_document import FieldIndex, stylesheet
from leaguebot.image.utils.svg_fill import FillSpec
from leaguebot.image.utils.country_data import country_for_nationality

_ROW_PREFIX = "row"
_ROUND_PREFIX = "round"


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

    **Text colours only.** The chip itself is artwork of the ``marker`` class;
    what is left to the stylesheet is the *ink* drawn over it, which no asset could supply —
    the result is a text node of the template, sitting above the image.

    Only ``.highlight_*`` rules carrying a ``fill`` are collected. A template declaring none
    yields an empty map and no text is recoloured, which is how a template that draws its own
    chips but leaves the numbers alone renders exactly as its author drew it.
    """
    return {
        selector[1:]: block["fill"]
        for selector, block in stylesheet(root).items()
        if selector.startswith(f".{_HIGHLIGHT_SELECTOR}") and "fill" in block
    }


def _paint(
    paints: Mapping[str, str], kind: str, *, variants: Sequence[str] = ("_text",)
) -> str | None:
    """The ink a template gives *kind*, or None where it gives none.

    One tier. Sprint and feature share a look (decided 2026-08-31), so the per-family
    selector the first version carried is withdrawn rather than left half-used.

    *variants* walks a fallback — the raised qualifying glyph asks for ``_sup_text`` and
    settles for ``_text`` — and is ordered most specific first.
    """
    for variant in variants:
        paint = paints.get(f"{_HIGHLIGHT_SELECTOR}{kind}{variant}")
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
    image_data: dict[str, tuple[str, str]],
) -> None:
    """The chips beneath one cell, and the text colours that keep it readable.

    Two independent layers, as the specification asks: a **background** drawing the podium or
    points chip, and a **fastest-lap mark** above it that may stand at the same time. Each is
    an asset of the ``marker`` class, so what the mark actually looks like is a
    file a league may replace — which is how the fastest lap became a corner triangle rather
    than a wash over the whole cell.

    A cell that earns no highlight is **left alone**, neither filled nor removed. The slots
    are ``<image>`` elements the template ships with no href, which draw nothing and which
    ``_unreachable_links`` passes over; removing them instead would put a thousand ids into
    ``spec.remove`` per graphic and walk a subtree for each.

    **The ink follows the background and nothing else.** Neither corner mark may set it. Both
    occupy a corner while the text sits inboard over the plate, so the plate is the only thing
    the reader sees the glyph against, and the only thing its legibility can be judged from.
    This was learnt twice: the fastest lap took the ink while it was still a full-cell wash and
    kept doing so once it became a triangle, which painted white numerals onto a gold plate;
    and a qualifying mark did the same, painting a near-black glyph onto the bare row band.

    The raised qualifying glyph is recoloured with the plate for the same reason and not as a
    highlight of its own — it says nothing about where the driver qualified. The plate spans
    the whole column and the glyph sits on top of it, so without this a P1 cell would draw the
    stylesheet's grey superscript on gold.
    """
    stem = field_id[: -len("_result")]
    qualifying = suffix.endswith("_qualifying_result")
    applied: str | None = None

    if cell.highlight:
        slot_id = f"{stem}_mark" if qualifying else f"{stem}_background"
        prefix = QUALIFYING_DATUM_PREFIX if qualifying else RACE_DATUM_PREFIX
        datum = f"{prefix}{cell.highlight}"
        if slot_id in declared:
            image_data[slot_id] = (HIGHLIGHT_ASSET_CLASS, datum)
            applied = cell.highlight

    if qualifying:
        # **A qualifying mark colours no text.** The raised figure it stands for sits inboard
        # of the corner the mark occupies, and over the race plate, so it is the plate that
        # governs its ink. Taking the ink from the mark instead put a near-black glyph —
        # chosen to read on a *light* gold plate — onto a deliberately dark triangle, and on
        # the bare row band where the race earned no plate at all.
        return

    if cell.fastest_lap:
        overlay_id = f"{stem}_{HIGHLIGHT_FASTEST_LAP}"
        if overlay_id in declared:
            image_data[overlay_id] = (HIGHLIGHT_ASSET_CLASS, FASTEST_LAP_DATUM)
            # It does **not** become the applied highlight. See the ink rule below.

    if applied is None:
        return

    text_paint = _paint(paints, applied)
    if text_paint:
        recolour[field_id] = text_paint

    if not suffix.endswith("_race_result"):
        return
    sup_id = f"{stem[: -len('_race')]}_qualifying_result"
    if sup_id in declared:
        sup_paint = _paint(paints, applied, variants=("_sup_text", "_text"))
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
    image_data: dict[str, tuple[str, str]],
) -> None:
    """The four session cells hanging off one stem, filled and highlighted.

    Called by both grids — a drivers row's round, and a constructors car — so the cells of
    the two championships cannot drift apart.
    """
    for suffix in CELL_SUFFIXES:
        field_id = f"{cell_stem}_{suffix}"
        if field_id not in declared:
            continue
        cell = sessions.get(suffix) or CellValue()
        if cell.text:
            text[field_id] = cell.text
        else:
            empty_quietly.append(field_id)
        _project_highlight(
            field_id, suffix, cell, declared, paints, recolour, image_data
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
    # Indexed once: the constructors grid counts every cell's cars from it, which a plain set
    # would answer by scanning every name once per cell (#164).
    declared = DeclaredNames(FieldIndex(root).declared())

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

    # The division's logo, where a league's own template declares the slot (2026-09-02).
    if DIVISION_LOGO_FIELD in declared:
        image_data[DIVISION_LOGO_FIELD] = (DIVISION_LOGO_ASSET, drawing.division_name)
    put("classification_label", drawing.classification_label)
    put("result_status", drawing.result_status_label)
    put_optional("season_number", drawing.season_number)
    put_optional("division_tier", drawing.division_tier)
    # A season-boundary sheet names no race. The opening one stands before any has been
    # run and the final one is about the season rather than about the last round, so a
    # grand prix under `Opening Classification` would simply be untrue. Emptied *quietly*:
    # its absence is what the occasion determined, not a template shortcoming worth a
    # notice on every posting (XIV.3, XIV.4's configured absence).
    if drawing.names_a_race:
        put_optional("race_name", drawing.race_name)
    else:
        put("race_name", None)

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
            image_data[f"{stem}_team_image"] = ("team", entry.team_key or entry.team_name)

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
                image_data[flag_id] = ("flag", country_for_nationality(entry.nationality) or "")
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
            image_data=image_data,
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
        # Narrow the canvas to the rounds this division actually runs, carrying the
        # chrome standing beside them in with it (2026-09-02). A template declaring no
        # horizontal crop point is drawn at its full width, exactly as before.
        **column_crop_fields(
            declared, drawn=len(drawn_rounds), capacity=round_capacity
        ),
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
    image_data: dict[str, tuple[str, str]],
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
                image_data=image_data,
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
                image_data=image_data,
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
        text[previous_id] = movement.previous_position
    if number_id in declared:
        text[number_id] = movement.change
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
    """The gap to the leader — empty for the first-placed entry and for any entry level on
    points with it; never otherwise absent.

    It is worked out over the classification being drawn alone, so unlike the other two
    columns it is available even where no earlier round holds standings. Tying it to the
    movement record once blanked it for every entry the reference round did not hold.
    """
    if gap_id not in declared:
        return
    if entry.ordinal == 1 or not entry.gap_to_leader:
        empty_quietly.append(gap_id)
        return
    text[gap_id] = entry.gap_to_leader
