"""The two attendance field catalogues (Constitution XIV.3, XIV.10, XIV.11, XIV.17).

Discharges the test obligations of
``specs/041-attendance-image-generation/contracts/attendance-catalogues.md`` and Part 1 of
``contracts/sibling-and-floor.md``.

The negative assertions here are not padding. What ``RSVP_CATALOGUE`` *omits* is the whole
substance of the static declaration (XIV.17): a field whose value changes while a check-in
call stands would leave a stale picture under a live message, and nothing in the module can
detect that.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from models.image_catalogues import (  # noqa: E402
    ATTENDANCE_CATALOGUE,
    RSVP_CATALOGUE,
    CapacityError,
    catalogue_for,
    sibling_fields_declared,
    sibling_keys,
)

SHEET = "attendance_template"
CALL = "rsvp_template"


# ── 1. Registration ───────────────────────────────────────────────────────


def test_both_catalogues_are_registered_against_their_template_slot():
    assert catalogue_for(SHEET) is ATTENDANCE_CATALOGUE
    assert catalogue_for(CALL) is RSVP_CATALOGUE


def test_neither_catalogue_is_empty():
    """An empty catalogue makes validity Layer 2 skip rather than pass (XIV.9)."""
    assert not ATTENDANCE_CATALOGUE.is_empty
    assert not RSVP_CATALOGUE.is_empty


# ── 2. The sheet's fields match the wip-spec, in both directions ──────────


def test_the_sheets_top_level_fields_are_exactly_the_wip_specs():
    assert ATTENDANCE_CATALOGUE.mandatory == {"division_name", "round_number"}
    assert ATTENDANCE_CATALOGUE.optional == {
        "season_number",
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name",
        "race_name_group",
        "autoreserve_group",
        "autoreserve_limit",
        "autosack_group",
        "autosack_limit",
        # The band beneath the rows, carried up when the sheet is cropped to the drivers
        # it actually holds (XIV.2, v7.1.0).
        "footer_group",
    }


def test_the_sheets_row_fields_are_exactly_the_wip_specs():
    rows = ATTENDANCE_CATALOGUE.rows
    assert rows.prefix == "row"
    assert rows.fields == {
        "group",
        "driver_name",
        "driver_flag",
        "team_name",
        "team_image",
        "points",
        "sanction",
        # Where the canvas is cut when this row is the last the data fill. Optional: a
        # template declaring none renders at full height, as every one did before v7.1.0.
        "vertical_crop_point",
    }
    assert rows.mandatory_fields == {"group", "driver_name", "points"}
    assert "vertical_crop_point" not in rows.mandatory_fields
    assert "vertical_crop_point" in rows.valueless_fields
    assert rows.assets == {"driver_flag": "flag", "team_image": "team"}


def test_the_sheet_draws_no_position_on_any_row():
    """The row ordinal is a place in the layout and not a datum (XIV.11, v4.6.0).

    The sheet is a record and not a classification: two drivers level on totals stand level,
    and a numbered row would publish a ranking the module never computed.
    """
    assert "position" not in ATTENDANCE_CATALOGUE.rows.fields


def test_the_sheet_carries_none_of_what_the_wip_spec_forbids_it():
    """No RSVP status, no pardon, no date of a round, no result of a session (FR-008)."""
    every = (
        set(ATTENDANCE_CATALOGUE.mandatory)
        | set(ATTENDANCE_CATALOGUE.optional)
        | set(ATTENDANCE_CATALOGUE.rows.fields)
        | set(ATTENDANCE_CATALOGUE.columns.fields)
        | set(ATTENDANCE_CATALOGUE.rows.nested.fields)
    )
    for forbidden in ("rsvp", "status", "pardon", "justification", "date", "result"):
        assert not any(forbidden in name for name in every), forbidden


def test_the_round_cells_hang_off_the_row_and_the_headings_stand_at_top_level():
    """A cell belongs to its row and its column both, and a node has one parent (XIV.2)."""
    nested = ATTENDANCE_CATALOGUE.rows.nested
    assert nested is not None
    assert nested.prefix == "round"
    assert nested.fields == {"points"}

    columns = ATTENDANCE_CATALOGUE.columns
    assert columns.prefix == "round"
    assert columns.fields == {"group", "number", "flag"}
    assert columns.mandatory_fields == {"number"}
    assert columns.assets == {"flag": "flag"}

    # The column carries chrome alone — no cell of any row.
    assert "points" not in columns.fields


def test_the_grid_is_optional_as_a_unit_on_both_of_its_id_families():
    """A template drawing no round draws the totals alone and is not faulty (FR-003)."""
    assert ATTENDANCE_CATALOGUE.columns.optional_unit is True
    assert ATTENDANCE_CATALOGUE.rows.nested.optional_unit is True


def test_the_rows_are_not_optional_as_a_unit():
    """A sheet template declaring no row at all is a fatal error (FR-041)."""
    assert ATTENDANCE_CATALOGUE.rows.optional_unit is False


# ── 3. The call's fields match the wip-spec, in both directions ───────────


def test_the_calls_top_level_fields_are_exactly_the_wip_specs():
    assert RSVP_CATALOGUE.mandatory == {
        "division_name",
        "round_number",
        "race_name",
        "round_format",
        "round_date",
        "round_time",
    }
    assert RSVP_CATALOGUE.optional == {
        "season_number",
        "season_number_group",
        "division_tier",
        "division_tier_group",
        "race_name_group",
        "track_name",
        "track_name_group",
        "country_name",
        "country_name_group",
        # The two imagery classes a round may be pictured by. The check-in graphic is
        # one of the two types that may declare a track-class field at all (044).
        "track_flag",
        "track_flag_group",
        "track_image",
        "track_image_group",
        "deadline_date",
        "deadline_time",
    }
    assert RSVP_CATALOGUE.assets == {"track_flag": "flag", "track_image": "track"}


def test_the_calls_sessions_are_an_ordinal_collection_optional_as_a_unit():
    rows = RSVP_CATALOGUE.rows
    assert rows.prefix == "session"
    assert rows.fields == {"group", "name"}
    assert rows.mandatory_fields == {"group", "name"}
    assert rows.optional_unit is True
    assert rows.nested is None


def test_the_call_declares_no_field_whose_value_can_change_while_it_stands():
    """The substance of the static declaration (XIV.17, v4.6.0).

    Everything the three buttons alter — the roster, each driver's status, the reserve
    distribution — lives in the embed, which is edited in place, and stays off the picture,
    which is not. Adding a field matching any of these is an amendment of the static
    declaration and not a catalogue edit; **no test can catch that judgement**, and this one
    only holds the line where it stands today.
    """
    every = (
        set(RSVP_CATALOGUE.mandatory)
        | set(RSVP_CATALOGUE.optional)
        | set(RSVP_CATALOGUE.rows.fields)
    )
    for mutable in ("driver", "team", "rsvp", "status", "points", "roster", "reserve"):
        assert not any(mutable in name for name in every), mutable


# ── 4. Mandatory ids without a template ───────────────────────────────────


def test_all_mandatory_ids_with_no_template_returns_the_top_level_set_alone():
    """The per-member sets are unknowable without a file to count (XIV.12)."""
    assert ATTENDANCE_CATALOGUE.all_mandatory_ids() == {"division_name", "round_number"}
    assert RSVP_CATALOGUE.all_mandatory_ids() == {
        "division_name",
        "round_number",
        "race_name",
        "round_format",
        "round_date",
        "round_time",
    }


# ── 5. Capacity counted from the template ─────────────────────────────────


def _svg(ids):
    import xml.etree.ElementTree as ET

    root = ET.Element("{http://www.w3.org/2000/svg}svg", {"width": "100", "height": "100"})
    for name in ids:
        ET.SubElement(root, "{http://www.w3.org/2000/svg}text", {"id": name})
    return root


def test_the_rows_are_counted_from_the_template():
    root = _svg([f"row_{n}_driver_name" for n in (1, 2, 3)])
    assert ATTENDANCE_CATALOGUE.rows.declared_capacity(root) == 3


def test_a_gap_in_the_row_numbering_is_fatal():
    root = _svg(["row_1_driver_name", "row_2_driver_name", "row_4_driver_name"])
    with pytest.raises(CapacityError) as exc:
        ATTENDANCE_CATALOGUE.rows.declared_capacity(root)
    assert "row" in str(exc.value)


def test_a_sheet_template_declaring_no_row_at_all_is_fatal():
    with pytest.raises(CapacityError):
        ATTENDANCE_CATALOGUE.rows.declared_capacity(_svg(["division_name"]))


def test_a_template_declaring_no_round_is_not_faulty():
    """The grid is an optional unit, so none is a legitimate answer (FR-003)."""
    root = _svg(["row_1_driver_name", "division_name"])
    assert ATTENDANCE_CATALOGUE.columns.declared_capacity(root) == 0


def test_a_template_declaring_no_session_is_not_faulty():
    assert RSVP_CATALOGUE.rows.declared_capacity(_svg(["division_name"])) == 0


def test_a_gap_in_the_session_numbering_is_fatal():
    root = _svg(["session_1_name", "session_3_name"])
    with pytest.raises(CapacityError) as exc:
        RSVP_CATALOGUE.rows.declared_capacity(root)
    assert "session" in str(exc.value)


def test_a_round_declaring_its_number_but_no_group_is_valid():
    """A template's choice, not a fault — the heading simply carries over."""
    root = _svg(["round_1_number", "round_2_number"])
    assert ATTENDANCE_CATALOGUE.columns.declared_capacity(root) == 2


# ── 6. The widened sibling relation (contracts/sibling-and-floor.md, Part 1) ──


def test_the_two_attendance_graphics_are_siblings_in_both_directions():
    """They share a source module and not an aspect (XIV.3, v4.6.0)."""
    assert CALL in sibling_keys(SHEET)
    assert SHEET in sibling_keys(CALL)


def test_the_results_pair_is_still_a_sibling_relation():
    assert "results_race_template" in sibling_keys("results_qualifying_template")


def test_the_standings_pair_is_still_a_sibling_relation():
    assert "standings_constructors_template" in sibling_keys("standings_drivers_template")


def test_the_calendar_and_the_lineup_are_not_siblings_of_anything():
    """Their source module is None; without that guard they would pair with each other.

    A calendar template declaring a lineup's field states nothing about a calendar, and the
    constitution says so explicitly.
    """
    assert sibling_keys("calendar_template") == []
    assert sibling_keys("lineup_template") == []


def test_a_check_in_field_on_a_sheet_template_is_reported():
    """The overlap is at **top level**, which a rows-only comparison would miss entirely."""
    found = sibling_fields_declared(SHEET, ["round_format", "round_date", "division_name"])
    assert found == ["round_date", "round_format"]


def test_a_session_field_on_a_sheet_template_is_reported():
    assert sibling_fields_declared(SHEET, ["session_1_name"]) == ["session_1_name"]


def test_a_sheet_row_field_on_a_check_in_template_is_reported():
    found = sibling_fields_declared(CALL, ["row_1_driver_name", "row_1_points"])
    assert found == ["row_1_driver_name", "row_1_points"]


def test_a_shared_top_level_field_is_never_a_siblings_field():
    """Both graphics name the division, the season and the round — none is foreign."""
    shared = ["division_name", "division_tier", "season_number", "round_number"]
    assert sibling_fields_declared(SHEET, shared) == []
    assert sibling_fields_declared(CALL, shared) == []


def test_an_id_belonging_to_no_catalogue_is_not_a_fault():
    """A hand-authored SVG carries identifiers on every node it holds (XIV.3)."""
    declared = ["path4711", "layer1", "some_decorative_swoosh", "g12345"]
    assert sibling_fields_declared(SHEET, declared) == []
    assert sibling_fields_declared(CALL, declared) == []


def test_a_field_group_is_never_a_siblings_field():
    """XIV.2 lets any field be wrapped in a group named for it, whatever the type.

    The standings catalogues enumerate their groups and the results ones do not; the group
    form is derived rather than declared so a legitimate wrapper never reads as foreign.
    """
    assert sibling_fields_declared(SHEET, ["season_number_group"]) == []
    assert sibling_fields_declared(CALL, ["division_tier_group"]) == []
    assert (
        sibling_fields_declared("results_race_template", ["season_number_group"]) == []
    )


def test_every_shipped_template_passes_the_widened_check():
    """The widening adds no fault to a file that renders today."""
    import re

    from models.image_constants import TEMPLATE_COLUMNS

    directory = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
    for key, filename in TEMPLATE_COLUMNS.items():
        path = directory / filename
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        declared = set(re.findall(r'\bid="([^"]+)"', text)) | set(
            re.findall(r'inkscape:label="([^"]+)"', text)
        )
        assert sibling_fields_declared(key, declared) == [], key
