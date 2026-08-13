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
