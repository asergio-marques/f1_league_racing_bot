"""Resolution and projection of the attendance sheet (041, US1).

Covers FR-007, FR-010–FR-020, FR-030, FR-031 and FR-039, and the floor obligations of
``specs/041-attendance-image-generation/contracts/sibling-and-floor.md`` Part 2.

The two rules worth stating twice here: an **empty cell means zero** and never an unknown, and
the **row ordinal is a place in the layout and not a datum** — a sheet is a record, not a
classification, and two drivers level on totals stand level.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_attendance_service import (  # noqa: E402
    SANCTION_ANNOTATION,
    AttendanceDataError,
    DriverRecord,
    RoundHeading,
    build_fill_spec,
    cell_text,
    resolve_drawing,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _svg(ids):
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": "1200", "height": "675"})
    for name in ids:
        tag = "image" if name.endswith(("_image", "_flag")) else "text"
        ET.SubElement(root, f"{{{SVG_NS}}}{tag}", {"id": name})
    return root


def _sheet_svg(rows=3, rounds=0, extras=()):
    ids = ["division_name", "round_number"]
    for r in range(1, rows + 1):
        ids += [
            f"row_{r}_group",
            f"row_{r}_driver_name",
            f"row_{r}_points",
            f"row_{r}_team_name",
            f"row_{r}_team_image",
            f"row_{r}_driver_flag",
            f"row_{r}_sanction",
        ]
        for z in range(1, rounds + 1):
            ids.append(f"row_{r}_round_{z}_points")
    for z in range(1, rounds + 1):
        ids += [f"round_{z}_group", f"round_{z}_number", f"round_{z}_image"]
    ids += list(extras)
    return _svg(ids)


def _records(*specs):
    return [
        DriverRecord(key=key, total=total, round_points=cells or {}, sanctioned=sanc)
        for key, total, cells, sanc in specs
    ]


# ── The floor ─────────────────────────────────────────────────────────────


def test_a_division_holding_no_driver_is_fatal_and_names_the_division():
    """XIV.12's floor, raised against the data before any template is in view."""
    with pytest.raises(AttendanceDataError) as exc:
        resolve_drawing(
            division_name="Division 1",
            round_number=3,
            records=[],
            display_names={},
        )
    assert "Division 1" in str(exc.value)
    assert "no driver" in str(exc.value)


def test_the_floor_names_the_division_and_not_the_template():
    """A template declaring one row is perfectly valid; the division is what is at fault."""
    with pytest.raises(AttendanceDataError) as exc:
        resolve_drawing(
            division_name="Division 1", round_number=3, records=[], display_names={}
        )
    assert "template" not in str(exc.value).lower()


def test_one_driver_against_a_ten_row_template_draws_with_no_error():
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 5, {}, False)),
        display_names={1: "Ayrton"},
        nationalities={1: "British"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=10))
    assert spec.text["row_1_driver_name"] == "Ayrton"
    assert "row_10_group" in spec.remove
    assert spec.empty == []


# ── Ordering, and the ordinal that is not a datum ─────────────────────────


def test_rows_are_ordered_by_total_descending():
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 2, {}, False), (2, 9, {}, False), (3, 5, {}, False)),
        display_names={1: "A", 2: "B", 3: "C"},
    )
    assert [e.points for e in drawing.entries] == ["9", "5", "2"]


def test_drivers_level_on_totals_are_ordered_alphabetically_by_resolved_name():
    """The tie-break reads the same string the graphic draws."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 4, {}, False), (2, 4, {}, False)),
        display_names={1: "Zoe", 2: "Alice"},
    )
    assert [e.driver_name for e in drawing.entries] == ["Alice", "Zoe"]


def test_the_sheet_draws_no_position_even_though_it_is_ordered():
    """XIV.11 (v4.6.0): a numbered row would publish a ranking never computed."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 9, {}, False), (2, 9, {}, False)),
        display_names={1: "A", 2: "B"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=2, extras=("row_1_position",)))
    assert "row_1_position" not in spec.text
    # Two drivers level on totals stand level: both draw the same points and no rank.
    assert spec.text["row_1_points"] == spec.text["row_2_points"] == "9"


# ── An empty cell means zero ──────────────────────────────────────────────


@pytest.mark.parametrize("value", [None, 0])
def test_a_round_that_conferred_nothing_draws_an_empty_cell(value):
    assert cell_text(value) == ""


def test_a_round_that_conferred_points_draws_them():
    assert cell_text(3) == "3"


def test_every_emptying_of_a_cell_is_quiet():
    """None of the six cases is a value that could not be determined (FR-015)."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=2,
        records=_records((1, 0, {1: 0, 2: None}, False)),
        display_names={1: "A"},
        nationalities={1: "British"},
        rounds=[RoundHeading(1, "1", "Silverstone"), RoundHeading(2, "2", None)],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1, rounds=2))

    assert spec.text.get("row_1_round_1_points") is None
    assert "row_1_round_1_points" in spec.empty_quietly
    assert "row_1_round_2_points" in spec.empty_quietly
    assert spec.empty == []


def test_a_driver_holding_no_record_for_a_round_draws_as_one_the_round_cost_nothing():
    drawing = resolve_drawing(
        division_name="D",
        round_number=2,
        records=_records((1, 0, {}, False), (2, 0, {1: 0}, False)),
        display_names={1: "A", 2: "B"},
        rounds=[RoundHeading(1, "1", "Silverstone")],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=2, rounds=1))
    assert "row_1_round_1_points" in spec.empty_quietly
    assert "row_2_round_1_points" in spec.empty_quietly


def test_an_empty_cell_is_never_drawn_as_a_dash_or_a_zero():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 0, {1: 0}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(1, "1", "Silverstone")],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1, rounds=1))
    for value in spec.text.values():
        assert value not in ("-", "—", "0") or value == "0"
    assert "row_1_round_1_points" not in spec.text


# ── The sanction annotation ───────────────────────────────────────────────


def test_a_sanctioned_driver_carries_the_annotation():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 10, {}, True)),
        display_names={1: "A"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1))
    assert spec.text["row_1_sanction"] == SANCTION_ANNOTATION


def test_the_annotation_reads_the_same_for_both_sanctions():
    """The sheet is not where autoreserve and autosack are told apart (FR-017)."""
    assert SANCTION_ANNOTATION == "Reached point limit"


def test_every_other_driver_has_the_sanction_field_emptied_quietly():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        nationalities={1: "British"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1))
    assert "row_1_sanction" in spec.empty_quietly
    assert spec.empty == []


# ── The two point limits ──────────────────────────────────────────────────


def test_configured_limits_are_drawn():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        autoreserve_threshold=10,
        autosack_threshold=20,
    )
    spec = build_fill_spec(
        drawing,
        _sheet_svg(
            rows=1,
            extras=("autoreserve_group", "autoreserve_limit", "autosack_group", "autosack_limit"),
        ),
    )
    assert spec.text["autoreserve_limit"] == "10"
    assert spec.text["autosack_limit"] == "20"


def test_a_disabled_functionality_removes_its_block_whole_and_reports_nothing():
    """A configured absence: the graphic draws what the league configured (XIV.4)."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        nationalities={1: "British"},
        autoreserve_threshold=None,
        autosack_threshold=0,
    )
    spec = build_fill_spec(
        drawing,
        _sheet_svg(
            rows=1,
            extras=("autoreserve_group", "autoreserve_limit", "autosack_group", "autosack_limit"),
        ),
    )
    assert "autoreserve_group" in spec.remove
    assert "autosack_group" in spec.remove
    assert spec.empty == []


def test_without_a_block_group_the_limit_field_alone_is_emptied():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
    )
    spec = build_fill_spec(
        drawing, _sheet_svg(rows=1, extras=("autoreserve_limit", "autosack_limit"))
    )
    assert "autoreserve_limit" in spec.empty_quietly
    assert "autoreserve_group" not in spec.remove


# ── The team of a row, and the assets ─────────────────────────────────────


def test_the_team_of_a_row_is_the_seat_held_at_generation():
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        team_names={1: "Reserve"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1))
    assert spec.text["row_1_team_name"] == "Reserve"
    assert spec.image_data["row_1_team_image"] == ("team", "Reserve")


def test_a_recorded_nationality_resolves_a_flag():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        nationalities={1: "British"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1))
    assert spec.image_data["row_1_driver_flag"] == ("flag", "British")


def test_an_absent_nationality_removes_the_flag_and_reports_it():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        nationalities={1: None},
        nationality_collected=True,
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1))
    assert "row_1_driver_flag" in spec.remove
    assert "row_1_driver_flag" in spec.empty


def test_nationality_switched_off_at_its_source_reports_nothing():
    """A league that switched collection off has configured a sheet with no flags (XIV.4)."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        nationalities={1: None},
        nationality_collected=False,
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1))
    assert "row_1_driver_flag" in spec.remove
    assert spec.empty == []


# ── The grid ──────────────────────────────────────────────────────────────


def test_every_round_the_division_holds_is_drawn_run_or_not():
    """FR-016 — unlike the standings grid, which draws only rounds already run."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=2,
        records=_records((1, 4, {1: 2}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(z, str(z), f"Track {z}") for z in (1, 2, 3)],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1, rounds=3))
    assert spec.text["round_1_number"] == "1"
    assert spec.text["round_3_number"] == "3"
    assert "round_3_group" not in spec.remove


def test_a_round_the_template_declares_beyond_the_calendar_takes_its_cells_with_it():
    """One capacity governs both id families (XIV.12); containment cannot carry the cells."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {1: 2}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(1, "1", "Silverstone")],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=2, rounds=3))

    assert "round_2_group" in spec.remove
    assert "round_3_group" in spec.remove
    # The cells hang off the row, not the heading group, so they are named separately.
    assert "row_1_round_2_points" in spec.remove
    assert "row_2_round_3_points" in spec.remove


def test_without_a_heading_group_every_field_bearing_the_ordinal_is_removed_one_by_one():
    ids = ["division_name", "round_number", "row_1_group", "row_1_driver_name",
           "row_1_points", "round_1_number", "round_2_number", "row_1_round_2_points"]
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(1, "1", "Silverstone")],
    )
    spec = build_fill_spec(drawing, _svg(ids))
    assert "round_2_number" in spec.remove
    assert "row_1_round_2_points" in spec.remove


def test_a_template_declaring_no_round_draws_the_totals_alone():
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 4, {1: 2}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(1, "1", "Silverstone")],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1, rounds=0))
    assert spec.text["row_1_points"] == "4"
    assert not any("round_1" in name for name in spec.text)


def test_more_rounds_than_the_template_declares_is_fatal_and_names_them():
    drawing = resolve_drawing(
        division_name="D",
        round_number=3,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(z, str(z), None) for z in (1, 2, 3)],
    )
    with pytest.raises(AttendanceDataError) as exc:
        build_fill_spec(drawing, _sheet_svg(rows=1, rounds=2))
    assert "3" in str(exc.value)


def test_a_rounds_image_is_resolved_from_its_track():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        rounds=[RoundHeading(1, "1", "Silverstone Circuit")],
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=1, rounds=1))
    assert spec.image_data["round_1_image"] == ("track", "Silverstone Circuit")


# ── Unused rows ───────────────────────────────────────────────────────────


def test_unused_rows_leave_by_their_group_with_their_fields_off_canvas():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False)),
        display_names={1: "A"},
        nationalities={1: "British"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=3))
    assert "row_2_group" in spec.remove
    assert "row_3_group" in spec.remove
    assert "row_2_driver_name" in spec.off_canvas
    assert spec.empty == []


def test_the_row_count_reported_is_the_driver_count():
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=_records((1, 4, {}, False), (2, 3, {}, False)),
        display_names={1: "A", 2: "B"},
    )
    spec = build_fill_spec(drawing, _sheet_svg(rows=5))
    assert spec.row_count == 2
