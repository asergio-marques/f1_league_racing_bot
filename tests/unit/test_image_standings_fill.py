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
):
    """A standings template declaring *rounds* round headings and, per row, either a
    session cell per round (drivers) or *cars* cars per round (constructors)."""
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1200")
    root.set("height", "675")
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
                    for suffix in _CELL_SUFFIXES:
                        etree.SubElement(parent, f"{{{SVG_NS}}}text").set(
                            "id", f"{car_stem}_{suffix}"
                        )
    return root


def _cells(**sessions) -> RoundCells:
    values = {suffix: "" for suffix in _CELL_SUFFIXES}
    values.update(sessions)
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
    root = _grid_template(1, 1)
    heading = RoundHeading(ordinal=1, number="1", country=None)
    spec = build_fill_spec(_drawing([_entry(1, cells={1: _cells()})], rounds=[heading]), root)
    assert "round_1_flag" in spec.remove
    assert "round_1_flag" not in spec.empty
    assert "round_1_flag" not in spec.image_data


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
    sessions = {suffix: "" for suffix in _CELL_SUFFIXES}
    sessions["feature_race_result"] = "1"
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
