"""Unit tests for the standings projection onto a template — T022.

Covers `build_fill_spec` for the classification:
  1. Rows filled to the entry count; the position from the ordinal (XIV.11).
  2. Unused rows removed whole, their fields off the canvas (XIV.3, XIV.12).
  3. The movement block: removed whole where the record is absent, and emptied field by
     field where the template declares no group — neither raising a notice (FR-017).
  4. Assets: team image, flag and marker.
  5. The flag's three states, one of which reports nothing at all (FR-028, XIV.4).
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_standings_service import (
    CONSTRUCTORS_TEMPLATE_KEY,
    DRIVERS_TEMPLATE_KEY,
    CellValue,
    HIGHLIGHT_P1,
    HIGHLIGHT_P2,
    HIGHLIGHT_P3,
    HIGHLIGHT_POINTS,
    RACE_DATUM_PREFIX,
    RoundCells,
    RoundHeading,
    StandingsDataError,
    StandingsDrawing,
    StandingsEntry,
    build_fill_spec,
)
from services.standings_service import MOVEMENT_GAINED, MOVEMENT_LOST, Movement

SVG_NS = "http://www.w3.org/2000/svg"

_ROW_SUFFIXES = (
    "position",
    "driver_name",
    "team_name",
    "points",
    "gap_to_leader",
    "previous_position",
    "position_change",
)
_ROW_IMAGES = ("team_image", "driver_flag", "position_change_marker")


def _template(rows: int, *, groups: bool = True, movement_group: bool = True):
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    for name in ("division_name", "round_number", "result_status"):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", name)
    for index in range(1, rows + 1):
        parent = root
        if groups:
            parent = etree.SubElement(root, f"{{{SVG_NS}}}g")
            parent.set("id", f"row_{index}_group")
        for suffix in _ROW_SUFFIXES:
            etree.SubElement(parent, f"{{{SVG_NS}}}text").set(
                "id", f"row_{index}_{suffix}"
            )
        for suffix in _ROW_IMAGES:
            etree.SubElement(parent, f"{{{SVG_NS}}}image").set(
                "id", f"row_{index}_{suffix}"
            )
        if movement_group:
            block = etree.SubElement(parent, f"{{{SVG_NS}}}g")
            block.set("id", f"row_{index}_position_change_group")
    return root


def _entry(ordinal: int, **overrides) -> StandingsEntry:
    values = dict(
        ordinal=ordinal,
        position=str(ordinal),
        driver_name=f"Driver {ordinal}",
        team_name=f"Team {ordinal}",
        points=str(50 - ordinal),
        nationality="dutch",
        gap_to_leader=(ordinal - 1) * 6,
        movement=Movement(
            previous_position=ordinal,
            change=0,
            direction=MOVEMENT_GAINED,
        ),
    )
    values.update(overrides)
    return StandingsEntry(**values)


_CELL_SUFFIXES = (
    "sprint_qualifying_result",
    "sprint_race_result",
    "feature_qualifying_result",
    "feature_race_result",
)


#: The chips a race cell may declare beneath it. `<image>` slots carrying no href, authored
#: before the text so they paint under it, exactly as the shipped templates do.
_HIGHLIGHT_SUFFIXES = (
    "sprint_race_background",
    "sprint_race_fastest_lap",
    "sprint_qualifying_mark",
    "feature_race_background",
    "feature_race_fastest_lap",
    "feature_qualifying_mark",
)


def _highlight_rects(parent, stem: str) -> None:
    for suffix in _HIGHLIGHT_SUFFIXES:
        slot = etree.SubElement(parent, f"{{{SVG_NS}}}image")
        slot.set("id", f"{stem}_{suffix}")
        slot.set("preserveAspectRatio", "none")


def _grid_template(
    rows: int,
    rounds: int,
    *,
    drivers: bool = True,
    cars: int = 2,
    row_groups: bool = True,
    round_groups: bool = True,
    cell_groups: bool = True,
    round_flags: bool = True,
    highlights: bool = False,
    style: str | None = None,
):
    """A standings template declaring *rounds* round headings and, per row, either a
    session cell per round (drivers) or *cars* cars per round (constructors)."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
    if style is not None:
        defs = etree.SubElement(root, f"{{{SVG_NS}}}defs")
        etree.SubElement(defs, f"{{{SVG_NS}}}style").text = style
    for name in ("division_name", "round_number", "result_status"):
        etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", name)

    row_parents = {}
    for index in range(1, rows + 1):
        parent = root
        if row_groups:
            parent = etree.SubElement(root, f"{{{SVG_NS}}}g")
            parent.set("id", f"row_{index}_group")
        etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", f"row_{index}_position")
        etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", f"row_{index}_team_name")
        etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", f"row_{index}_points")
        etree.SubElement(parent, f"{{{SVG_NS}}}image").set("id", f"row_{index}_team_image")
        if drivers:
            etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", f"row_{index}_driver_name")
        row_parents[index] = parent

    for z in range(1, rounds + 1):
        parent = root
        if round_groups:
            parent = etree.SubElement(root, f"{{{SVG_NS}}}g")
            parent.set("id", f"round_{z}_group")
        etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", f"round_{z}_number")
        if round_flags:
            etree.SubElement(parent, f"{{{SVG_NS}}}image").set("id", f"round_{z}_flag")

    for index in range(1, rows + 1):
        row_parent = row_parents[index]
        for z in range(1, rounds + 1):
            round_stem = f"row_{index}_round_{z}"
            if drivers:
                parent = row_parent
                if cell_groups:
                    parent = etree.SubElement(row_parent, f"{{{SVG_NS}}}g")
                    parent.set("id", f"{round_stem}_group")
                if highlights:
                    _highlight_rects(parent, round_stem)
                for suffix in _CELL_SUFFIXES:
                    etree.SubElement(parent, f"{{{SVG_NS}}}text").set(
                        "id", f"{round_stem}_{suffix}"
                    )
            else:
                for w in range(1, cars + 1):
                    car_stem = f"{round_stem}_driver_{w}"
                    parent = row_parent
                    if cell_groups:
                        parent = etree.SubElement(row_parent, f"{{{SVG_NS}}}g")
                        parent.set("id", f"{car_stem}_group")
                    etree.SubElement(parent, f"{{{SVG_NS}}}text").set("id", f"{car_stem}_name")
                    if highlights:
                        _highlight_rects(parent, car_stem)
                    for suffix in _CELL_SUFFIXES:
                        etree.SubElement(parent, f"{{{SVG_NS}}}text").set(
                            "id", f"{car_stem}_{suffix}"
                        )
    return root


def _cells(**sessions) -> RoundCells:
    """A row's round. A bare string stands for a cell carrying no highlight."""
    values = {suffix: CellValue() for suffix in _CELL_SUFFIXES}
    values.update(
        {
            suffix: value if isinstance(value, CellValue) else CellValue(text=value)
            for suffix, value in sessions.items()
        }
    )
    return RoundCells(sessions=values)


def _drawing(entries, **overrides) -> StandingsDrawing:
    values = dict(
        template_key=DRIVERS_TEMPLATE_KEY,
        division_name="Division 1",
        round_number="4",
        result_status_label="Final Results",
        entries=list(entries),
    )
    values.update(overrides)
    return StandingsDrawing(**values)


# ── 1. Rows and the ordinal ───────────────────────────────────────────────


def test_the_headings_are_filled():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert spec.text["division_name"] == "Division 1"
    assert spec.text["round_number"] == "4"
    assert spec.text["result_status"] == "Final Results"


def test_the_position_is_the_one_the_standings_recorded():
    spec = build_fill_spec(_drawing([_entry(1), _entry(2)]), _template(3))
    assert spec.text["row_1_position"] == "1"
    assert spec.text["row_2_position"] == "2"


def test_a_recorded_position_that_outruns_its_row_is_still_the_one_drawn():
    """XIV.7: the graphic may not disagree with the table about a value both draw.

    With the reserves toggle off, a reserve who raced holds a standing position but is not
    drawn — so the recorded positions run 1, 2, 4 while the rows run 1, 2, 3. The textual
    standings print the recorded position; so does the graphic.
    """
    entries = [_entry(1, position="1"), _entry(2, position="2"), _entry(3, position="4")]
    spec = build_fill_spec(_drawing(entries), _template(4))
    assert spec.text["row_3_position"] == "4"
    # the row itself is still addressed contiguously
    assert spec.text["row_3_driver_name"] == "Driver 3"


def test_each_row_carries_its_name_team_and_points():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert spec.text["row_1_driver_name"] == "Driver 1"
    assert spec.text["row_1_team_name"] == "Team 1"
    assert spec.text["row_1_points"] == "49"


def test_the_row_count_reports_the_entry_count_for_the_capacity_check():
    spec = build_fill_spec(_drawing([_entry(i) for i in range(1, 4)]), _template(5))
    assert spec.row_count == 3


def test_a_template_with_no_row_at_all_is_fatal():
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "10")
    root.set("height", "10")
    with pytest.raises(StandingsDataError):
        build_fill_spec(_drawing([_entry(1)]), root)


# ── 2. Unused rows ────────────────────────────────────────────────────────


def test_an_unused_row_leaves_by_its_group():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(3))
    assert "row_2_group" in spec.remove
    assert "row_3_group" in spec.remove
    assert "row_1_group" not in spec.remove


def test_the_fields_of_an_unused_row_are_off_the_canvas_not_unresolved():
    """XIV.3: a field a group removal took off the canvas is not unfilled."""
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert "row_2_position" in spec.off_canvas
    assert "row_2_team_image" in spec.off_canvas


def test_an_unused_row_without_a_group_is_removed_field_by_field():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2, groups=False))
    assert "row_2_position" in spec.remove
    assert "row_2_team_image" in spec.remove


# ── 3. The movement block ─────────────────────────────────────────────────


def test_a_determined_movement_fills_its_three_fields():
    movement = Movement(previous_position=5, change=3, direction=MOVEMENT_LOST)
    spec = build_fill_spec(
        _drawing([_entry(2, gap_to_leader=12, movement=movement)]), _template(2)
    )
    assert spec.text["row_2_gap_to_leader"] == "-12"
    assert spec.text["row_2_previous_position"] == "5"
    assert spec.text["row_2_position_change"] == "3"
    assert spec.image_data["row_2_position_change_marker"] == ("marker", MOVEMENT_LOST)


def test_the_leaders_gap_is_empty():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert "row_1_gap_to_leader" not in spec.text
    assert "row_1_gap_to_leader" in spec.empty_quietly


def test_an_absent_movement_removes_the_block_whole():
    """FR-017, and it raises no notice."""
    spec = build_fill_spec(_drawing([_entry(1, movement=None)]), _template(2))
    assert "row_1_position_change_group" in spec.remove
    assert "row_1_position_change" in spec.off_canvas
    assert "row_1_position_change_marker" in spec.off_canvas
    assert "row_1_position_change" not in spec.empty


def test_an_absent_movement_empties_the_previous_position_quietly():
    spec = build_fill_spec(_drawing([_entry(1, movement=None)]), _template(2))
    assert "row_1_previous_position" in spec.empty_quietly
    assert "row_1_previous_position" not in spec.empty


def test_without_a_group_the_number_is_emptied_and_the_marker_removed():
    spec = build_fill_spec(
        _drawing([_entry(1, movement=None)]), _template(2, movement_group=False)
    )
    assert "row_1_position_change" in spec.empty_quietly
    assert "row_1_position_change_marker" in spec.empty_quietly
    assert "row_1_position_change_group" not in spec.remove
    # nothing about an undeterminable movement is worth a notice
    assert not [name for name in spec.empty if "position_change" in name]


# ── 4. Assets ─────────────────────────────────────────────────────────────


def test_the_team_image_resolves_from_the_team_name():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert spec.image_data["row_1_team_image"] == ("team", "Team 1")


def test_the_marker_resolves_from_the_direction():
    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert spec.image_data["row_1_position_change_marker"] == ("marker", MOVEMENT_GAINED)


def test_the_flag_resolves_from_the_nationality():
    spec = build_fill_spec(_drawing([_entry(1, nationality="british")]), _template(2))
    assert spec.image_data["row_1_driver_flag"] == ("flag", "british")


# ── 5. The flag's three states ────────────────────────────────────────────


def test_a_driver_who_stated_no_nationality_empties_the_flag_with_a_notice():
    spec = build_fill_spec(_drawing([_entry(1, nationality=None)]), _template(2))
    assert "row_1_driver_flag" in spec.empty
    assert "row_1_driver_flag" not in spec.image_data


def test_nationality_switched_off_at_source_reports_nothing():
    """XIV.4: the graphic draws exactly what the league configured."""
    spec = build_fill_spec(
        _drawing([_entry(1, nationality=None)], nationality_collected=False),
        _template(2),
    )
    assert "row_1_driver_flag" in spec.empty_quietly
    assert "row_1_driver_flag" not in spec.empty


def test_the_switch_beats_a_nationality_the_driver_already_stated():
    """The switch is read before the value, so no driver keeps a flag the others lost.

    A league that turns collection off does not thereby erase what its drivers stated
    earlier. Testing the value first drew a flag for every driver who had answered before
    the switch and none for anyone since — one table disagreeing with itself, and with the
    preview, which blanks them all.
    """
    spec = build_fill_spec(
        _drawing(
            [_entry(1, nationality="british"), _entry(2, nationality=None)],
            nationality_collected=False,
        ),
        _template(2),
    )
    for ordinal in (1, 2):
        assert f"row_{ordinal}_driver_flag" not in spec.image_data
        assert f"row_{ordinal}_driver_flag" in spec.empty_quietly
        assert f"row_{ordinal}_driver_flag" not in spec.empty


def test_the_constructors_graphic_fills_no_flag_and_no_driver_name():
    spec = build_fill_spec(
        _drawing(
            [_entry(1, driver_name=None, nationality=None)],
            template_key=CONSTRUCTORS_TEMPLATE_KEY,
        ),
        _template(2),
    )
    assert "row_1_driver_name" not in spec.text
    assert "row_1_driver_flag" not in spec.image_data


def test_the_catalogue_travels_with_the_spec():
    """XIV.10: the fill pipeline and validity read the *same* object."""
    from models.image_catalogues import catalogue_for

    spec = build_fill_spec(_drawing([_entry(1)]), _template(2))
    assert spec.catalogue is catalogue_for(DRIVERS_TEMPLATE_KEY)
    assert spec.image_type == DRIVERS_TEMPLATE_KEY


def test_the_gap_is_drawn_even_where_the_movement_record_is_absent():
    """The bug the rasterised PNG caught, and no unit test did.

    The gap is arithmetic over the classification being drawn alone, so an entry the
    reference round does not hold still has one. Nesting it inside the movement record once
    blanked the whole gap column for every such entry — visible immediately in the PNG and
    invisible to every assertion that only checked the movement block.
    """
    spec = build_fill_spec(
        _drawing([_entry(1), _entry(4, gap_to_leader=18, movement=None)]), _template(4)
    )
    assert spec.text["row_4_gap_to_leader"] == "-18"
    # …while the movement block beside it is still removed whole
    assert "row_4_position_change_group" in spec.remove


# ── 6. The round grid ────────────────────────────────────────────────────


def test_round_headings_are_filled():
    root = _grid_template(1, 1)
    heading = RoundHeading(ordinal=1, number="7", country="United Kingdom")
    spec = build_fill_spec(_drawing([_entry(1, cells={1: _cells()})], rounds=[heading]), root)
    assert spec.text["round_1_number"] == "7"
    assert spec.image_data["round_1_flag"] == ("flag", "United Kingdom")


def test_a_round_with_no_country_removes_the_flag_without_a_notice():
    """A round whose circuit names no country the registry knows: no flag to draw.

    Distinct from the mystery round below, which *has* a datum and must draw it. The two
    were conflated until 2026-08-28, and the mystery column drew nothing at all.
    """
    root = _grid_template(1, 1)
    heading = RoundHeading(ordinal=1, number="1", country=None)
    spec = build_fill_spec(_drawing([_entry(1, cells={1: _cells()})], rounds=[heading]), root)
    assert "round_1_flag" in spec.remove
    assert "round_1_flag" not in spec.empty
    assert "round_1_flag" not in spec.image_data


def test_a_mystery_round_draws_the_mystery_flag_rather_than_no_flag():
    """044 FR-012 — both classes of a mystery round resolve the datum `Mystery`.

    The closed-set rule then finds the module's own `mystery.svg` even where a league's
    flag directory has none, so this is a substitution and never an absence.
    """
    root = _grid_template(1, 1)
    heading = RoundHeading(ordinal=1, number="10", track=None, country="Mystery")
    spec = build_fill_spec(_drawing([_entry(1, cells={1: _cells()})], rounds=[heading]), root)
    assert spec.image_data["round_1_flag"] == ("flag", "Mystery")
    assert "round_1_flag" not in spec.remove


def test_driver_grid_cells_are_filled():
    root = _grid_template(1, 1)
    heading = RoundHeading(ordinal=1, number="1")
    cells = _cells(feature_race_result="3")
    spec = build_fill_spec(_drawing([_entry(1, cells={1: cells})], rounds=[heading]), root)
    assert spec.text["row_1_round_1_feature_race_result"] == "3"
    assert "row_1_round_1_sprint_race_result" in spec.empty_quietly


def test_a_template_declaring_no_round_draws_a_classification_with_no_grid_error():
    """A template with no round field at all is not a fault — the grid is an optional unit."""
    root = _template(2)
    heading = RoundHeading(ordinal=1, number="1")
    spec = build_fill_spec(
        _drawing([_entry(1, cells={1: _cells(feature_race_result="1")})], rounds=[heading]),
        root,
    )
    assert "round_1_number" not in spec.text
    assert spec.text["row_1_driver_name"] == "Driver 1"


def test_more_rounds_than_the_template_declares_is_fatal():
    root = _grid_template(1, 1)
    headings = [RoundHeading(ordinal=1, number="1"), RoundHeading(ordinal=2, number="2")]
    entry = _entry(1, cells={1: _cells(), 2: _cells()})
    with pytest.raises(StandingsDataError):
        build_fill_spec(_drawing([entry], rounds=headings), root)


def test_excess_rounds_are_removed_from_every_family_at_once():
    root = _grid_template(1, 2)
    heading = RoundHeading(ordinal=1, number="1")
    spec = build_fill_spec(_drawing([_entry(1, cells={1: _cells()})], rounds=[heading]), root)
    assert "round_2_group" in spec.remove
    assert "row_1_round_2_group" in spec.remove
    assert "row_1_round_2_feature_race_result" in spec.off_canvas


def test_excess_rounds_are_removed_field_by_field_without_groups():
    root = _grid_template(1, 2, round_groups=False, cell_groups=False)
    heading = RoundHeading(ordinal=1, number="1")
    spec = build_fill_spec(_drawing([_entry(1, cells={1: _cells()})], rounds=[heading]), root)
    assert "round_2_number" in spec.remove
    assert "row_1_round_2_feature_race_result" in spec.remove


def test_a_car_someone_drove_is_filled():
    root = _grid_template(1, 1, drivers=False, cars=2)
    heading = RoundHeading(ordinal=1, number="1")
    sessions = {suffix: CellValue() for suffix in _CELL_SUFFIXES}
    sessions["feature_race_result"] = CellValue(text="1")
    cells = RoundCells(cars={1: ("Verstappen", sessions)})
    entry = _entry(1, driver_name=None, cells={1: cells})
    spec = build_fill_spec(
        _drawing([entry], rounds=[heading], template_key=CONSTRUCTORS_TEMPLATE_KEY), root
    )
    assert spec.text["row_1_round_1_driver_1_name"] == "Verstappen"
    assert spec.text["row_1_round_1_driver_1_feature_race_result"] == "1"


def test_a_car_nobody_drove_is_trimmed_silently():
    root = _grid_template(1, 1, drivers=False, cars=2)
    heading = RoundHeading(ordinal=1, number="1")
    sessions = {suffix: "" for suffix in _CELL_SUFFIXES}
    cells = RoundCells(cars={1: ("Verstappen", sessions)})  # car 2 never allocated
    entry = _entry(1, driver_name=None, cells={1: cells})
    spec = build_fill_spec(
        _drawing([entry], rounds=[heading], template_key=CONSTRUCTORS_TEMPLATE_KEY), root
    )
    assert "row_1_round_1_driver_2_group" in spec.remove
    assert "row_1_round_1_driver_2_group" not in spec.empty


def test_a_car_beyond_the_templates_declared_car_slots_is_fatal():
    """FR-041: a driver who drove exceeding the template's room there is fatal."""
    root = _grid_template(1, 1, drivers=False, cars=1)
    heading = RoundHeading(ordinal=1, number="1")
    sessions = {suffix: "" for suffix in _CELL_SUFFIXES}
    cells = RoundCells(cars={2: ("Substitute", sessions)})
    entry = _entry(1, driver_name=None, cells={1: cells})
    with pytest.raises(StandingsDataError):
        build_fill_spec(
            _drawing([entry], rounds=[heading], template_key=CONSTRUCTORS_TEMPLATE_KEY), root
        )


def test_a_car_beyond_the_teams_own_configured_seats_is_trimmed_even_when_allocated():
    """A car within the template's room but beyond the row's team's seats is still removed.

    The ceiling `build_fill_spec` fills against is the team's own configured seats, never
    the template's declared room — that only bounds the fatal case. A template padded for
    the division's largest team must not draw a car a smaller team's own roster has no room
    for, whatever the (cross-session) data happens to allocate there.
    """
    root = _grid_template(1, 1, drivers=False, cars=2)
    heading = RoundHeading(ordinal=1, number="1")
    sessions_1 = {suffix: "" for suffix in _CELL_SUFFIXES}
    sessions_2 = {suffix: "" for suffix in _CELL_SUFFIXES}
    cells = RoundCells(cars={1: ("Verstappen", sessions_1), 2: ("Substitute", sessions_2)})
    entry = _entry(1, driver_name=None, cells={1: cells})
    spec = build_fill_spec(
        _drawing(
            [entry],
            rounds=[heading],
            template_key=CONSTRUCTORS_TEMPLATE_KEY,
            team_seat_counts={"Team 1": 1},
        ),
        root,
    )
    assert spec.text["row_1_round_1_driver_1_name"] == "Verstappen"
    assert "row_1_round_1_driver_2_group" in spec.remove
    assert "row_1_round_1_driver_2_name" not in spec.text


# ── The highlight layers ──────────────────────────────────────────────────
#
# The chip is an asset; only the ink over it comes from the stylesheet. So the tests split:
# `spec.image_data` carries which mark is drawn, `spec.recolour` only the text colours.

#: A template may still *name* a fastest-lap ink; the render must ignore it, which is what
#: `test_the_fastest_lap_does_not_touch_the_text_colour` holds it to.
_INK = """
    .highlight_p1_text { fill: #0B0D10 }
    .highlight_fastest_lap_text { fill: #F2F5F8 }
"""

_ASSET = "marker"


def _chips(spec) -> dict:
    """Just the highlight slots of a spec's image data — the row's team badge is not one."""
    return {
        name: datum
        for name, datum in spec.image_data.items()
        if name.endswith(("_background", "_fastest_lap", "_qualifying_mark"))
    }


def _highlighted(cell, *, style=_INK, highlights=True, rounds=1):
    """One drivers row whose round 1 holds *cell*, projected onto a template."""
    root = _grid_template(1, rounds, highlights=highlights, style=style)
    heading = RoundHeading(ordinal=1, number="1")
    entry = _entry(1, cells={1: _cells(feature_race_result=cell)})
    return build_fill_spec(_drawing([entry], rounds=[heading]), root)


def test_a_podium_cell_draws_the_chip_of_its_place():
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1))
    assert spec.image_data["row_1_round_1_feature_race_background"] == (_ASSET, "race_p1")


def test_each_podium_place_draws_its_own_chip():
    """The **kind** decides the chip; the datum names the session it was earned in.

    A race cell asks for `race_<kind>` and a qualifying one for `qualifying_<kind>`, so the
    two sets of marks are told apart by name in the one folder that now holds both.
    """
    for kind in (HIGHLIGHT_P1, HIGHLIGHT_P2, HIGHLIGHT_P3, HIGHLIGHT_POINTS):
        spec = _highlighted(CellValue(text="1", highlight=kind))
        assert spec.image_data["row_1_round_1_feature_race_background"] == (
            _ASSET,
            f"{RACE_DATUM_PREFIX}{kind}",
        )


def test_the_chip_is_the_datum_and_never_a_class_of_its_own():
    """One folder holds all nine marks, so a league configures one directory."""
    spec = _highlighted(CellValue(text="7", highlight=HIGHLIGHT_POINTS, fastest_lap=True))
    assert {cls for cls, _ in _chips(spec).values()} == {_ASSET}


def test_a_highlighted_cell_is_still_filled():
    """Drawing a chip beneath a cell does not stand in for filling it (XIV.2)."""
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1))
    assert spec.text["row_1_round_1_feature_race_result"] == "1"


def test_a_template_declaring_no_chip_slot_draws_none():
    """Opt-in per cell: nothing is invented for a template that drew no slot."""
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1), highlights=False)
    assert _chips(spec) == {}
    assert spec.recolour == {}


def test_an_unhighlighted_cell_contributes_nothing_to_the_spec():
    """The slots carry no href, so they draw nothing and are never removed."""
    spec = _highlighted(CellValue(text="12"))
    assert _chips(spec) == {}
    assert spec.recolour == {}
    assert not any("_background" in name for name in spec.remove)


def test_the_fastest_lap_mark_is_drawn_only_where_the_cell_holds_it():
    spec = _highlighted(CellValue(text="5", fastest_lap=True))
    assert spec.image_data["row_1_round_1_feature_race_fastest_lap"] == (_ASSET, "race_fastest_lap")
    spec = _highlighted(CellValue(text="5"))
    assert "row_1_round_1_feature_race_fastest_lap" not in spec.image_data


def test_the_two_layers_stand_together():
    """A winner who took the fastest lap gets both; the mark never replaces the chip."""
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1, fastest_lap=True))
    assert spec.image_data["row_1_round_1_feature_race_background"] == (_ASSET, "race_p1")
    assert spec.image_data["row_1_round_1_feature_race_fastest_lap"] == (_ASSET, "race_fastest_lap")


def test_a_sprint_cell_draws_the_same_chip_as_a_feature_one():
    """One look for both (decided 2026-08-31): the family no longer selects anything."""
    root = _grid_template(1, 1, highlights=True, style=_INK)
    heading = RoundHeading(ordinal=1, number="1")
    entry = _entry(1, cells={1: _cells(
        sprint_race_result=CellValue(text="1", highlight=HIGHLIGHT_P1),
        feature_race_result=CellValue(text="1", highlight=HIGHLIGHT_P1),
    )})
    spec = build_fill_spec(_drawing([entry], rounds=[heading]), root)
    assert (
        spec.image_data["row_1_round_1_sprint_race_background"]
        == spec.image_data["row_1_round_1_feature_race_background"]
    )


def test_the_text_colour_follows_the_highlight_that_was_applied():
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1))
    assert spec.recolour["row_1_round_1_feature_race_result"] == "#0B0D10"


def test_the_fastest_lap_does_not_touch_the_text_colour():
    """Regression: it did, and painted white numerals onto a gold plate.

    The mark took the ink while it was still a full-cell wash, and went on taking it once it
    became a corner triangle. It occupies a corner; the numerals sit inboard over the plate,
    so the plate is the only thing they are read against.
    """
    plain = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1))
    with_fl = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1, fastest_lap=True))
    assert with_fl.recolour == plain.recolour
    assert with_fl.recolour["row_1_round_1_feature_race_result"] == "#0B0D10"


def test_a_fastest_lap_with_no_plate_beneath_it_sets_no_ink_at_all():
    """Nothing is under the numerals but the row band, which `.cell` already reads on."""
    spec = _highlighted(CellValue(text="12", fastest_lap=True))
    assert spec.image_data["row_1_round_1_feature_race_fastest_lap"] == (_ASSET, "race_fastest_lap")
    assert spec.recolour == {}


def test_no_text_colour_is_set_where_the_template_names_none():
    """A chip light enough to read through wants no ink of its own, and names none."""
    spec = _highlighted(CellValue(text="7", highlight=HIGHLIGHT_POINTS))
    assert spec.image_data["row_1_round_1_feature_race_background"] == (_ASSET, "race_points")
    assert "row_1_round_1_feature_race_result" not in spec.recolour


def test_the_raised_qualifying_glyph_is_recoloured_to_stay_legible_on_the_chip():
    """It sits on top of the chip; left grey it would be unreadable on gold.

    This says nothing about where the driver qualified — it is contrast, not a highlight.
    """
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1))
    assert spec.recolour["row_1_round_1_feature_qualifying_result"] == "#0B0D10"


def test_the_raised_glyph_prefers_a_rule_written_for_it():
    style = _INK + "\n.highlight_p1_sup_text { fill: #4A3B00 }"
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1), style=style)
    assert spec.recolour["row_1_round_1_feature_qualifying_result"] == "#4A3B00"
    assert spec.recolour["row_1_round_1_feature_race_result"] == "#0B0D10"


def test_a_comment_holding_a_comma_above_a_rule_does_not_disable_it():
    """A selector group is split on commas, so an unstripped comment would eat the rule.

    `stylesheet` strips comments first and its docstring records the bug; this pins that the
    ink rules are read through that fix rather than around it.
    """
    style = "/* gold, silver and bronze */\n.highlight_p1_text { fill: #0B0D10 }"
    spec = _highlighted(CellValue(text="1", highlight=HIGHLIGHT_P1), style=style)
    assert spec.recolour["row_1_round_1_feature_race_result"] == "#0B0D10"


def test_a_trimmed_round_leaves_its_chips_undrawn():
    root = _grid_template(1, 2, highlights=True, style=_INK)
    heading = RoundHeading(ordinal=1, number="1")
    entry = _entry(1, cells={1: _cells(
        feature_race_result=CellValue(text="1", highlight=HIGHLIGHT_P1)
    )})
    spec = build_fill_spec(_drawing([entry], rounds=[heading]), root)
    assert "row_1_round_2_feature_race_background" not in spec.image_data
    assert "row_1_round_2_feature_race_background" in spec.remove


def test_a_trimmed_row_leaves_its_chips_undrawn():
    root = _grid_template(2, 1, highlights=True, style=_INK)
    heading = RoundHeading(ordinal=1, number="1")
    entry = _entry(1, cells={1: _cells(
        feature_race_result=CellValue(text="1", highlight=HIGHLIGHT_P1)
    )})
    spec = build_fill_spec(_drawing([entry], rounds=[heading]), root)
    assert not any(key.startswith("row_2_") for key in _chips(spec))


def test_a_constructors_car_is_highlighted_by_the_same_rules():
    root = _grid_template(1, 1, drivers=False, cars=2, highlights=True, style=_INK)
    heading = RoundHeading(ordinal=1, number="1")
    sessions = {suffix: CellValue() for suffix in _CELL_SUFFIXES}
    sessions["feature_race_result"] = CellValue(
        text="1", highlight=HIGHLIGHT_P1, fastest_lap=True
    )
    entry = _entry(1, driver_name=None, cells={1: RoundCells(cars={1: ("Verstappen", sessions)})})
    spec = build_fill_spec(
        _drawing([entry], rounds=[heading], template_key=CONSTRUCTORS_TEMPLATE_KEY), root
    )
    stem = "row_1_round_1_driver_1_feature_race"
    assert spec.image_data[f"{stem}_background"] == (_ASSET, "race_p1")
    assert spec.image_data[f"{stem}_fastest_lap"] == (_ASSET, "race_fastest_lap")
    # The plate sets the ink; neither corner mark does.
    assert spec.recolour[f"{stem}_result"] == "#0B0D10"


# ── The qualifying mark ───────────────────────────────────────────────────
#
# A qualifying result earns the same four kinds a race result does, but takes a different
# picture: a corner triangle rather than a plate, so the two can stand on one cell at once.


def _both(race=None, qualifying=None, style=_INK):
    """One drivers row whose round 1 holds a race cell and its raised qualifying result."""
    root = _grid_template(1, 1, highlights=True, style=style)
    heading = RoundHeading(ordinal=1, number="1")
    entry = _entry(1, cells={1: _cells(
        feature_race_result=race or CellValue(),
        feature_qualifying_result=qualifying or CellValue(),
    )})
    return build_fill_spec(_drawing([entry], rounds=[heading]), root)


def test_a_qualifying_podium_draws_its_own_mark():
    spec = _both(qualifying=CellValue(text="2", highlight=HIGHLIGHT_P2))
    assert spec.image_data["row_1_round_1_feature_qualifying_mark"] == (_ASSET, "qualifying_p2")


def test_a_qualifying_points_finish_draws_the_points_mark():
    """The fourth requirement, and the reason the mark covers more than the podium."""
    spec = _both(qualifying=CellValue(text="7", highlight=HIGHLIGHT_POINTS))
    assert spec.image_data["row_1_round_1_feature_qualifying_mark"] == (
        _ASSET,
        "qualifying_points",
    )


def test_a_qualifying_result_outside_the_points_draws_nothing():
    spec = _both(qualifying=CellValue(text="14"))
    assert "row_1_round_1_feature_qualifying_mark" not in spec.image_data


def test_a_qualifying_mark_is_a_different_picture_from_the_race_plate():
    """Same kind, different datum — otherwise a pole-to-win cell would be one flat block."""
    spec = _both(
        race=CellValue(text="1", highlight=HIGHLIGHT_P1),
        qualifying=CellValue(text="1", highlight=HIGHLIGHT_P1),
    )
    assert spec.image_data["row_1_round_1_feature_race_background"] == (_ASSET, "race_p1")
    assert spec.image_data["row_1_round_1_feature_qualifying_mark"] == (_ASSET, "qualifying_p1")


def test_a_race_cell_never_draws_a_qualifying_datum():
    spec = _both(race=CellValue(text="3", highlight=HIGHLIGHT_P3))
    assert spec.image_data["row_1_round_1_feature_race_background"] == (_ASSET, "race_p3")
    assert not any(
        datum.startswith("qualifying_") for _cls, datum in _chips(spec).values()
    )


def test_all_three_marks_can_stand_on_one_cell():
    """A win from pole with the fastest lap — the busiest a cell gets."""
    spec = _both(
        race=CellValue(text="1", highlight=HIGHLIGHT_P1, fastest_lap=True),
        qualifying=CellValue(text="1", highlight=HIGHLIGHT_P1),
    )
    stem = "row_1_round_1_feature"
    assert _chips(spec) == {
        f"{stem}_race_background": (_ASSET, "race_p1"),
        f"{stem}_race_fastest_lap": (_ASSET, "race_fastest_lap"),
        f"{stem}_qualifying_mark": (_ASSET, "qualifying_p1"),
    }


def test_a_qualifying_cell_never_draws_a_fastest_lap():
    """A qualifying row carries no fastest-lap field, so the layer cannot arise there."""
    spec = _both(qualifying=CellValue(text="1", highlight=HIGHLIGHT_P1, fastest_lap=True))
    assert "row_1_round_1_feature_qualifying_fastest_lap" not in spec.image_data


def test_every_datum_the_projection_can_emit_has_a_packaged_file():
    """A kind added in code without artwork would resolve to the fallback and draw a lie."""
    from pathlib import Path

    from models.image_constants import packaged_directory_for
    from services.image_standings_service import HIGHLIGHT_DATA
    from utils.paths import PROJECT_ROOT

    packaged = Path(PROJECT_ROOT) / packaged_directory_for("marker")
    missing = [d for d in HIGHLIGHT_DATA if not (packaged / f"{d}.svg").is_file()]
    assert missing == [], f"no packaged artwork for: {missing}"


def test_a_qualifying_mark_colours_no_text():
    """Regression: it did, and the glyph vanished.

    The mark is deliberately much darker than the plates. Taking the raised figure's ink from
    it meant `.highlight_p1_text` — a near-black chosen to read on a *light* gold plate — was
    painted onto a dark triangle, and onto the bare row band wherever the race earned no plate
    of its own. The raised figure sits inboard of the corner the mark occupies and over the
    race plate, so it is the plate that governs its ink and the mark that governs none.
    """
    spec = _both(qualifying=CellValue(text="1", highlight=HIGHLIGHT_P1))
    assert spec.image_data["row_1_round_1_feature_qualifying_mark"] == (_ASSET, "qualifying_p1")
    assert spec.recolour == {}


def test_the_race_plate_still_colours_the_raised_figure_it_sits_under():
    """The other half of the same rule: the plate governs, and still does."""
    spec = _both(
        race=CellValue(text="1", highlight=HIGHLIGHT_P1),
        qualifying=CellValue(text="1", highlight=HIGHLIGHT_P1),
    )
    assert spec.recolour["row_1_round_1_feature_qualifying_result"] == "#0B0D10"
