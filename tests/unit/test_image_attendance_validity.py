"""Template validity and mismatch handling for the two attendance types (041, US4).

Covers FR-034–FR-042 and quickstart §§ 2, 4, 8 and 9.

The point of this file is that a faulty template is named **at the moment it is configured**,
not at the moment a round is posted. What can be checked against the template alone is checked
everywhere (XIV.9's structural check); what needs a division is compared against a stand-in at
season review and warns; what needs the concrete data is checked before the render and refuses.
"""
from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (  # noqa: E402
    CapacityError,
    catalogue_for,
    row_capacity_problem,
    sibling_fields_declared,
)
from services.image_attendance_service import (  # noqa: E402
    AttendanceDataError,
    DriverRecord,
    RoundHeading,
    build_fill_spec,
    resolve_drawing,
)

SVG_NS = "http://www.w3.org/2000/svg"
SHEET = "attendance_template"
CALL = "rsvp_template"


def _svg(ids):
    root = ET.Element(f"{{{SVG_NS}}}svg", {"width": "1200", "height": "675"})
    for name in ids:
        ET.SubElement(root, f"{{{SVG_NS}}}text", {"id": name})
    return root


def _sound_sheet(rows=1, rounds=0):
    ids = ["division_name", "round_number"]
    for r in range(1, rows + 1):
        ids += [f"row_{r}_group", f"row_{r}_driver_name", f"row_{r}_points"]
    for z in range(1, rounds + 1):
        ids += [f"round_{z}_group", f"round_{z}_number"]
    return ids


def _sound_call(sessions=0):
    ids = [
        "division_name", "round_number", "race_name",
        "round_format", "round_date", "round_time",
    ]
    for n in range(1, sessions + 1):
        ids += [f"session_{n}_group", f"session_{n}_name"]
    return ids


def _missing(template_key, ids):
    """The mandatory ids the catalogue demands that *ids* does not declare."""
    return sorted(catalogue_for(template_key).all_mandatory_ids(_svg(ids)) - set(ids))


# ── Division-independent fields, checked at every moment (FR-034) ─────────


def test_a_sound_sheet_declaring_no_round_is_accepted():
    assert _missing(SHEET, _sound_sheet(rows=1, rounds=0)) == []


def test_a_sound_call_declaring_no_session_is_accepted():
    assert _missing(CALL, _sound_call(sessions=0)) == []


@pytest.mark.parametrize("field", ["division_name", "round_number"])
def test_a_missing_mandatory_sheet_field_is_named(field):
    ids = [i for i in _sound_sheet() if i != field]
    assert field in _missing(SHEET, ids)


@pytest.mark.parametrize(
    "field", ["division_name", "round_number", "race_name", "round_format",
              "round_date", "round_time"]
)
def test_a_missing_mandatory_call_field_is_named(field):
    ids = [i for i in _sound_call() if i != field]
    assert field in _missing(CALL, ids)


def test_a_row_missing_its_driver_name_is_named():
    ids = [i for i in _sound_sheet(rows=2) if i != "row_2_driver_name"]
    assert "row_2_driver_name" in _missing(SHEET, ids)


def test_a_declared_round_owes_its_number(  # FR-005
):
    ids = _sound_sheet(rows=1, rounds=1)
    ids.remove("round_1_number")
    assert "round_1_number" in _missing(SHEET, ids)


def test_a_declared_session_owes_its_name_and_its_group():
    ids = _sound_call(sessions=1)
    ids.remove("session_1_name")
    assert "session_1_name" in _missing(CALL, ids)


# ── The three numberings (FR-041) ─────────────────────────────────────────


def test_a_sheet_declaring_no_row_at_all_is_refused():
    with pytest.raises(CapacityError) as exc:
        catalogue_for(SHEET).rows.declared_capacity(_svg(["division_name"]))
    assert "row" in str(exc.value)


def test_a_gap_in_the_row_numbering_names_the_rows():
    ids = ["division_name", "round_number",
           "row_1_driver_name", "row_2_driver_name", "row_5_driver_name"]
    with pytest.raises(CapacityError) as exc:
        catalogue_for(SHEET).rows.declared_capacity(_svg(ids))
    detail = str(exc.value)
    assert "row" in detail and "3" in detail


def test_a_gap_in_the_round_numbering_names_the_rounds():
    ids = _sound_sheet(rows=1) + ["round_1_number", "round_4_number"]
    with pytest.raises(CapacityError) as exc:
        catalogue_for(SHEET).columns.declared_capacity(_svg(ids))
    assert "round" in str(exc.value)


def test_a_gap_in_the_session_numbering_names_the_sessions():
    ids = _sound_call() + ["session_1_name", "session_3_name"]
    with pytest.raises(CapacityError) as exc:
        catalogue_for(CALL).rows.declared_capacity(_svg(ids))
    assert "session" in str(exc.value)


def test_each_numbering_names_which_of_the_three_is_at_fault():
    """A report naming "a gap" without saying where does not satisfy XIV.9.2."""
    row_ids = ["division_name", "row_1_driver_name", "row_3_driver_name"]
    session_ids = _sound_call() + ["session_1_name", "session_3_name"]

    with pytest.raises(CapacityError) as row_exc:
        catalogue_for(SHEET).rows.declared_capacity(_svg(row_ids))
    with pytest.raises(CapacityError) as session_exc:
        catalogue_for(CALL).rows.declared_capacity(_svg(session_ids))

    assert "row" in str(row_exc.value)
    assert "session" in str(session_exc.value)
    assert str(row_exc.value) != str(session_exc.value)


# ── The sibling fault (FR-041) ────────────────────────────────────────────


def test_a_sheet_template_holding_check_in_fields_is_named():
    found = sibling_fields_declared(SHEET, _sound_sheet() + ["round_format", "round_time"])
    assert found == ["round_format", "round_time"]


def test_a_check_in_template_holding_sheet_row_fields_is_named():
    found = sibling_fields_declared(CALL, _sound_call() + ["row_1_driver_name"])
    assert found == ["row_1_driver_name"]


# ── Generation-time mismatches (FR-037–FR-040) ────────────────────────────


def test_drivers_in_excess_of_the_rows_are_reported_through_the_row_count():
    """The render service issues the capacity problem in one place (FR-038)."""
    drawing = resolve_drawing(
        division_name="D",
        round_number=1,
        records=[DriverRecord(key=i, total=0) for i in range(1, 6)],
        display_names={i: f"D{i}" for i in range(1, 6)},
    )
    spec = build_fill_spec(drawing, _svg(_sound_sheet(rows=2)))
    assert spec.row_count == 5  # measured against a template declaring 2


def test_rounds_in_excess_of_the_template_are_fatal_and_name_them():
    drawing = resolve_drawing(
        division_name="D",
        round_number=4,
        records=[DriverRecord(key=1, total=0)],
        display_names={1: "A"},
        rounds=[RoundHeading(z, str(z), None) for z in (1, 2, 3, 4)],
    )
    with pytest.raises(AttendanceDataError) as exc:
        build_fill_spec(drawing, _svg(_sound_sheet(rows=1, rounds=2)))
    detail = str(exc.value)
    assert "3" in detail and "4" in detail


def test_a_division_holding_no_driver_is_fatal_and_names_it():
    with pytest.raises(AttendanceDataError) as exc:
        resolve_drawing(
            division_name="Division 2", round_number=1, records=[], display_names={}
        )
    assert "Division 2" in str(exc.value)


# ── The row ceiling at the assignment (FR-042) ────────────────────────────


def test_an_assignment_within_the_row_ceiling_is_allowed():
    root = _svg(_sound_sheet(rows=10))
    assert row_capacity_problem(SHEET, root, 10) is None


def test_an_assignment_past_the_row_ceiling_is_refused_naming_the_counts():
    root = _svg(_sound_sheet(rows=10))
    problem = row_capacity_problem(SHEET, root, 11)
    assert problem is not None
    assert "11" in problem and "10" in problem
    assert SHEET in problem


def test_the_ceiling_is_silent_for_a_template_that_cannot_be_counted():
    """An uncountable template is Layer 2's to report; it must not read as an overflow."""
    root = _svg(["division_name", "row_1_driver_name", "row_4_driver_name"])
    assert row_capacity_problem(SHEET, root, 99) is None


def test_the_ceiling_does_not_apply_to_a_type_with_no_rows():
    root = _svg(_sound_call())
    assert row_capacity_problem("calendar_template", root, 999) is None


# ── A template's choices that are not faults (quickstart § 2) ─────────────


def test_a_round_declaring_its_number_but_no_group_is_accepted():
    ids = _sound_sheet(rows=1) + ["round_1_number", "round_2_number"]
    assert catalogue_for(SHEET).columns.declared_capacity(_svg(ids)) == 2
    assert _missing(SHEET, ids) == []


def test_a_template_declaring_neither_grid_nor_sessions_is_accepted():
    assert _missing(SHEET, _sound_sheet(rows=3, rounds=0)) == []
    assert _missing(CALL, _sound_call(sessions=0)) == []
