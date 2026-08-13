"""Unit tests for the two results field catalogues — T013.

Covers:
  1. Both catalogues' declared shape, and the part they share.
  2. Capacity counted from the template rather than fixed in code (XIV.12).
  3. The two shapes XIV.11 forbids: no row at all, and a gap in the numbering.
  4. Sibling detection — a row field of the *other* results catalogue (XIV.3, v4.4.0).
  5. The per-field absent-datum fallback declaration (XIV.13, v4.4.0).
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (
    CATALOGUES,
    RESULTS_QUALIFYING_CATALOGUE,
    RESULTS_RACE_CATALOGUE,
    CapacityError,
    catalogue_for,
    declared_capacities,
    sibling_fields_declared,
    sibling_row_fields,
)

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

QUALIFYING = "results_qualifying_template"
RACE = "results_race_template"

_SHARED_ROW_MANDATORY = (
    "group",
    "position",
    "driver_name",
    "team_name",
    "team_image",
    "postrace_penalty",
    "appeal_penalty",
    "points",
)


def _template(*ids: str, labels: tuple[str, ...] = ()) -> etree._Element:
    root = etree.Element(f"{{{SVG_NS}}}svg", nsmap={"svg": SVG_NS, "inkscape": INKSCAPE_NS})
    root.set("width", "1600")
    root.set("height", "2000")
    for name in ids:
        child = etree.SubElement(root, f"{{{SVG_NS}}}text")
        child.set("id", name)
    for label in labels:
        layer = etree.SubElement(root, f"{{{SVG_NS}}}g")
        layer.set(f"{{{INKSCAPE_NS}}}groupmode", "layer")
        layer.set(f"{{{INKSCAPE_NS}}}label", label)
    return root


def _rows(count: int, *extra: str) -> list[str]:
    """Every mandatory field of *count* contiguous rows, plus *extra* suffixes."""
    out: list[str] = []
    for index in range(1, count + 1):
        for suffix in _SHARED_ROW_MANDATORY + extra:
            out.append(f"row_{index}_{suffix}")
    return out


# ── 1. Shape ──────────────────────────────────────────────────────────────


def test_both_results_catalogues_are_registered_and_not_empty():
    assert catalogue_for(QUALIFYING) is RESULTS_QUALIFYING_CATALOGUE
    assert catalogue_for(RACE) is RESULTS_RACE_CATALOGUE
    assert not RESULTS_QUALIFYING_CATALOGUE.is_empty
    assert not RESULTS_RACE_CATALOGUE.is_empty


def test_the_two_catalogues_share_their_whole_graphic_mandatory_fields():
    """Siblings share the declaration of their common part (XIV.10, v4.4.0)."""
    assert RESULTS_QUALIFYING_CATALOGUE.mandatory == RESULTS_RACE_CATALOGUE.mandatory
    assert RESULTS_QUALIFYING_CATALOGUE.mandatory == frozenset(
        {"division_name", "round_number", "race_name", "session_name", "result_status"}
    )


def test_column_groups_are_optional_on_both():
    for catalogue in (RESULTS_QUALIFYING_CATALOGUE, RESULTS_RACE_CATALOGUE):
        assert "postrace_penalty_group" in catalogue.optional
        assert "appeal_penalty_group" in catalogue.optional


def test_the_fastest_lap_block_belongs_to_the_race_catalogue_alone():
    for name in ("fastest_lap_group", "fastest_lap_driver_name", "fastest_lap_time"):
        assert name in RESULTS_RACE_CATALOGUE.optional
        assert name not in RESULTS_QUALIFYING_CATALOGUE.optional


def test_the_row_group_is_mandatory_and_valueless():
    """The template must provide the row; the render fills it or removes it whole."""
    for catalogue in (RESULTS_QUALIFYING_CATALOGUE, RESULTS_RACE_CATALOGUE):
        assert "group" in catalogue.rows.mandatory_fields
        assert "group" in catalogue.rows.valueless_fields


def test_the_driver_flag_is_the_only_optional_row_field_they_share():
    shared = RESULTS_QUALIFYING_CATALOGUE.rows.fields & RESULTS_RACE_CATALOGUE.rows.fields
    optional = shared - RESULTS_QUALIFYING_CATALOGUE.rows.mandatory_fields
    assert optional == frozenset({"driver_flag"})


def test_asset_classes_resolve_per_row_field():
    assert RESULTS_QUALIFYING_CATALOGUE.asset_class_for("row_3_tyre") == "tyre"
    assert RESULTS_QUALIFYING_CATALOGUE.asset_class_for("row_3_team_image") == "team"
    assert RESULTS_RACE_CATALOGUE.asset_class_for("row_12_driver_flag") == "flag"
    assert RESULTS_RACE_CATALOGUE.asset_class_for("row_3_tyre") is None


# ── 2. Capacity ───────────────────────────────────────────────────────────


def test_capacity_is_counted_from_the_template():
    root = _template(*_rows(5, "best_lap", "gap"))
    assert RESULTS_QUALIFYING_CATALOGUE.capacity(root) == 5


def test_capacity_counts_a_row_addressed_by_a_layer_label():
    """A manager sets the label; the identifier their editor generated is not the one."""
    root = _template(labels=tuple(_rows(3, "best_lap", "gap")))
    assert RESULTS_QUALIFYING_CATALOGUE.capacity(root) == 3


def test_a_template_declaring_no_row_at_all_is_fatal():
    root = _template("division_name", "round_number")
    with pytest.raises(CapacityError, match="declares no `row` at all"):
        RESULTS_QUALIFYING_CATALOGUE.capacity(root)


def test_a_gap_in_the_row_numbering_is_fatal():
    root = _template("row_1_position", "row_2_position", "row_4_position")
    with pytest.raises(CapacityError, match="has a gap"):
        RESULTS_RACE_CATALOGUE.capacity(root)


def test_a_template_derived_capacity_is_not_reported_to_the_seat_guard():
    """The row collection is entries, not seated drivers, so it must not bound placement."""
    assert QUALIFYING not in declared_capacities()
    assert RACE not in declared_capacities()


def test_mandatory_ids_cover_every_declared_row():
    root = _template(*_rows(2, "time", "fastest_lap", "ingame_penalty"))
    ids = RESULTS_RACE_CATALOGUE.all_mandatory_ids(root)
    assert "row_1_points" in ids
    assert "row_2_ingame_penalty" in ids
    assert "row_3_points" not in ids


# ── 3. Siblings ───────────────────────────────────────────────────────────


def test_each_results_catalogue_names_its_siblings_row_fields():
    assert sibling_row_fields(QUALIFYING) == {"time", "fastest_lap", "ingame_penalty"}
    assert sibling_row_fields(RACE) == {"best_lap", "gap", "tyre"}


def test_a_type_with_no_sibling_names_none():
    assert sibling_row_fields("calendar_template") == set()
    assert sibling_row_fields("lineup_template") == set()


def test_a_siblings_field_in_a_template_is_detected_and_named():
    found = sibling_fields_declared(RACE, ["row_1_gap", "row_1_time", "row_2_tyre"])
    assert found == ["row_1_gap", "row_2_tyre"]


def test_an_identifier_belonging_to_no_catalogue_is_not_a_fault():
    """A hand-authored SVG carries ids on every node; only catalogued ones are fields."""
    assert sibling_fields_declared(RACE, ["path4711", "layer1", "row_1_time"]) == []


def test_a_shared_row_field_is_never_a_siblings_field():
    assert sibling_fields_declared(QUALIFYING, ["row_1_points", "row_1_driver_name"]) == []


# ── 4. The absent-datum fallback declaration ──────────────────────────────


def test_the_tyre_declares_the_absent_datum_fallback():
    assert RESULTS_QUALIFYING_CATALOGUE.rows.fallback_when_absent == frozenset({"tyre"})
    assert RESULTS_QUALIFYING_CATALOGUE.draws_fallback_when_absent("row_4_tyre")


def test_no_other_results_field_declares_it():
    """Per field, never per class: a flag with no nationality is removed and reported."""
    assert not RESULTS_QUALIFYING_CATALOGUE.draws_fallback_when_absent("row_4_driver_flag")
    assert not RESULTS_QUALIFYING_CATALOGUE.draws_fallback_when_absent("row_4_team_image")
    assert not RESULTS_RACE_CATALOGUE.draws_fallback_when_absent("row_4_driver_flag")


def test_a_type_declaring_none_answers_false():
    assert not CATALOGUES["calendar_template"].draws_fallback_when_absent("round_1_image")
