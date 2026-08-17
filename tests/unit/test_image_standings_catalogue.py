"""Unit tests for the two standings field catalogues — T011.

Covers:
  1. Both catalogues' declared shape, and the part they share.
  2. Three-level id construction — row, round, car (XIV.11 nesting).
  3. Capacity counted from the template, for rows, rounds and cars alike (XIV.12).
  4. The optional unit: a template declaring no round is not faulty, and one declaring a
     round owes that round its number (XIV.3, v4.5.0).
  5. A gap at any of the three levels (XIV.11).
  6. Sibling detection — a driver field on the constructors template (XIV.3, v4.4.0).
  7. Asset classes, including the marker class the module now ships (XIV.13, v4.5.0).
  8. The shipped templates satisfying their own catalogues.
"""
from __future__ import annotations

import os
import re
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (
    STANDINGS_CONSTRUCTORS_CATALOGUE,
    STANDINGS_DRIVERS_CATALOGUE,
    CapacityError,
    catalogue_for,
    sibling_fields_declared,
    sibling_row_fields,
)

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

DRIVERS = "standings_drivers_template"
CONSTRUCTORS = "standings_constructors_template"

_SHARED_ROW_MANDATORY = (
    "group",
    "position",
    "team_name",
    "team_image",
    "points",
)

_TEMPLATE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "resources", "templates"
)


def _template(*ids: str) -> etree._Element:
    root = etree.Element(f"{{{SVG_NS}}}svg", nsmap={"svg": SVG_NS, "inkscape": INKSCAPE_NS})
    root.set("width", "1600")
    root.set("height", "2000")
    for name in ids:
        child = etree.SubElement(root, f"{{{SVG_NS}}}text")
        child.set("id", name)
    return root


def _rows(count: int, *extra: str) -> list[str]:
    """Every mandatory field of *count* contiguous rows, plus *extra* suffixes."""
    out: list[str] = []
    for index in range(1, count + 1):
        for suffix in _SHARED_ROW_MANDATORY + extra:
            out.append(f"row_{index}_{suffix}")
    return out


def _headings(count: int) -> list[str]:
    return [f"round_{z}_number" for z in range(1, count + 1)]


def _cells(rows: int, rounds: int) -> list[str]:
    return [
        f"row_{x}_round_{z}_feature_race_result"
        for x in range(1, rows + 1)
        for z in range(1, rounds + 1)
    ]


def _cars(rows: int, rounds: int, cars: int) -> list[str]:
    return [
        f"row_{x}_round_{z}_driver_{w}_feature_race_result"
        for x in range(1, rows + 1)
        for z in range(1, rounds + 1)
        for w in range(1, cars + 1)
    ]


def _shipped(key: str):
    from utils.svg_document import FieldIndex, load_svg

    doc = load_svg(os.path.join(_TEMPLATE_DIR, f"{key}.svg"))
    root = doc.root if hasattr(doc, "root") else doc
    return root, set(FieldIndex(root).declared())


# ── 1. Shape ──────────────────────────────────────────────────────────────


def test_both_standings_catalogues_are_registered_and_not_empty():
    assert catalogue_for(DRIVERS) is STANDINGS_DRIVERS_CATALOGUE
    assert catalogue_for(CONSTRUCTORS) is STANDINGS_CONSTRUCTORS_CATALOGUE
    assert not STANDINGS_DRIVERS_CATALOGUE.is_empty
    assert not STANDINGS_CONSTRUCTORS_CATALOGUE.is_empty


def test_the_two_catalogues_share_their_whole_graphic_fields():
    """Siblings share the declaration of their common part (XIV.10)."""
    assert (
        STANDINGS_DRIVERS_CATALOGUE.mandatory
        == STANDINGS_CONSTRUCTORS_CATALOGUE.mandatory
        == frozenset({"division_name", "round_number", "result_status"})
    )
    assert (
        STANDINGS_DRIVERS_CATALOGUE.optional
        == STANDINGS_CONSTRUCTORS_CATALOGUE.optional
    )


def test_the_lifecycle_label_is_mandatory_on_the_graphic():
    """XIV.16 (v4.5.0): the split with message text is not exclusive.

    The label is kept as message text *and* drawn, so a picture forwarded away from the
    message it rode on still says which phase it stands after.
    """
    assert "result_status" in STANDINGS_DRIVERS_CATALOGUE.mandatory


def test_an_optional_heading_declares_the_group_that_may_wrap_it():
    """XIV.2: a declared `<field>_group` leaves wherever the field would be emptied."""
    for suffix in ("season_number", "division_tier", "race_name"):
        assert suffix in STANDINGS_DRIVERS_CATALOGUE.optional
        assert f"{suffix}_group" in STANDINGS_DRIVERS_CATALOGUE.optional


def test_the_constructors_row_carries_no_driver_field():
    """The graphic has no field carrying the nationality of a driver."""
    fields = STANDINGS_CONSTRUCTORS_CATALOGUE.rows.fields
    assert "driver_name" not in fields
    assert "driver_flag" not in fields
    assert {"driver_name", "driver_flag"} <= STANDINGS_DRIVERS_CATALOGUE.rows.fields


def test_the_movement_block_is_a_group_bearing_its_rows_ordinal():
    """A block group wrapping fields that stand or fall together (XIV.2)."""
    rows = STANDINGS_DRIVERS_CATALOGUE.rows
    assert {"position_change_group", "position_change", "position_change_marker"} <= rows.fields
    assert "position_change_group" not in rows.mandatory_fields
    assert rows.group_id(3) == "row_3_group"
    assert rows.field_id(3, "position_change_group") == "row_3_position_change_group"


# ── 2. Three-level id construction ────────────────────────────────────────


def test_a_drivers_cell_is_addressed_on_row_and_round():
    nest = STANDINGS_DRIVERS_CATALOGUE.rows.nested
    assert nest is not None
    assert nest.field_id("row_3", 7, "feature_race_result") == (
        "row_3_round_7_feature_race_result"
    )
    assert nest.group_id("row_3", 7) == "row_3_round_7_group"


def test_a_constructors_cell_is_addressed_on_row_round_and_car():
    round_nest = STANDINGS_CONSTRUCTORS_CATALOGUE.rows.nested
    car_nest = round_nest.nested
    assert car_nest is not None
    stem = round_nest.member_id("row_3", 7)
    assert stem == "row_3_round_7"
    assert car_nest.field_id(stem, 2, "name") == "row_3_round_7_driver_2_name"
    assert car_nest.group_id(stem, 2) == "row_3_round_7_driver_2_group"


def test_the_constructors_round_level_carries_no_field_of_its_own():
    """The wip-spec gives it no `row_<x>_round_<z>_group`; the cars are what it holds."""
    assert STANDINGS_CONSTRUCTORS_CATALOGUE.rows.nested.fields == frozenset()


def test_the_round_headings_stand_at_top_level_and_not_under_a_row():
    """A cell belongs to its row and its column both, and a node has one parent."""
    columns = STANDINGS_DRIVERS_CATALOGUE.columns
    assert columns is not None
    assert columns.field_id(4, "number") == "round_4_number"
    assert columns.group_id(4) == "round_4_group"


# ── 3. Capacity counted from the template ─────────────────────────────────


def test_rows_and_rounds_are_both_counted_from_the_template():
    root = _template(*_rows(3, "driver_name"), *_headings(5))
    assert STANDINGS_DRIVERS_CATALOGUE.capacity(root) == 3
    assert STANDINGS_DRIVERS_CATALOGUE.column_capacity(root) == 5


def test_row_capacity_and_column_capacity_are_kept_apart():
    """Conflating them would refuse a placement because a template drew few rounds."""
    root = _template(*_rows(9, "driver_name"), *_headings(2))
    assert STANDINGS_DRIVERS_CATALOGUE.capacity(root) == 9
    assert STANDINGS_DRIVERS_CATALOGUE.column_capacity(root) == 2


def test_cars_are_counted_per_containing_row():
    """XIV.12 (v4.5.0): the count belongs to the containing member."""
    root = _template(
        *_rows(2), *_headings(1), *_cars(2, 1, 2)
    )
    from utils.svg_document import FieldIndex

    declared = set(FieldIndex(root).declared())
    car_nest = STANDINGS_CONSTRUCTORS_CATALOGUE.rows.nested.nested
    assert car_nest.declared_capacity("row_1_round_1", declared) == 2
    assert car_nest.capacity_per_member is True


def test_known_ids_reach_every_level_of_the_grid():
    root = _template(
        *_rows(2, "driver_name"), *_headings(2), *_cells(2, 2)
    )
    known = STANDINGS_DRIVERS_CATALOGUE.all_known_ids(root)
    assert "row_1_round_2_feature_race_result" in known
    assert "row_2_round_1_sprint_qualifying_result" in known
    assert "round_2_number" in known


def test_known_ids_reach_the_third_level_on_the_constructors():
    root = _template(*_rows(2), *_headings(2), *_cars(2, 2, 2))
    known = STANDINGS_CONSTRUCTORS_CATALOGUE.all_known_ids(root)
    assert "row_2_round_2_driver_2_name" in known
    assert "row_1_round_1_driver_1_feature_race_result" in known


# ── 4. The optional unit ──────────────────────────────────────────────────


def test_a_template_declaring_no_round_is_not_faulty():
    """XIV.3 (v4.5.0): the round portion is optional as a unit.

    The classification's own fields are still owed — it is the *round* portion that a
    template may decline entire, drawing a classification alone.
    """
    root = _template(*_rows(4, "driver_name"))
    assert STANDINGS_DRIVERS_CATALOGUE.column_capacity(root) == 0
    mandatory = STANDINGS_DRIVERS_CATALOGUE.all_mandatory_ids(root)
    assert not [name for name in mandatory if re.match(r"^round_\d+_", name)]
    assert not [name for name in mandatory if re.search(r"_round_\d+_", name)]
    # and the classification is untouched by the round portion's absence
    assert "row_4_position" in mandatory
    assert "division_name" in mandatory


def test_the_round_the_standings_stand_after_is_not_a_member_of_the_grid():
    """`round_number` and `round_<z>_number` are different fields.

    The ordinal pattern requires digits, so the top-level heading is never miscounted as a
    member of the round collection.
    """
    root = _template(*_rows(1, "driver_name"), "round_number")
    assert STANDINGS_DRIVERS_CATALOGUE.column_capacity(root) == 0
    assert "round_number" in STANDINGS_DRIVERS_CATALOGUE.mandatory


def test_a_round_that_is_declared_owes_its_number():
    """Mandatory *within* an optional unit binds on the members declared."""
    root = _template(*_rows(2, "driver_name"), "round_1_group", "round_2_group")
    mandatory = STANDINGS_DRIVERS_CATALOGUE.all_mandatory_ids(root)
    assert "round_1_number" in mandatory
    assert "round_2_number" in mandatory


def test_the_cells_of_a_round_are_every_one_optional():
    """A template draws the sessions it has room for."""
    assert STANDINGS_DRIVERS_CATALOGUE.rows.nested.mandatory_fields == frozenset()
    assert (
        STANDINGS_CONSTRUCTORS_CATALOGUE.rows.nested.nested.mandatory_fields
        == frozenset()
    )


# ── 5. Gaps at any level ──────────────────────────────────────────────────


def test_a_gap_in_the_rows_is_fatal():
    root = _template("row_1_position", "row_2_position", "row_4_position")
    with pytest.raises(CapacityError) as exc:
        STANDINGS_DRIVERS_CATALOGUE.capacity(root)
    assert "gap" in str(exc.value)


def test_a_template_declaring_no_row_at_all_is_fatal():
    root = _template("division_name", "round_number", "result_status")
    with pytest.raises(CapacityError):
        STANDINGS_DRIVERS_CATALOGUE.capacity(root)


def test_a_gap_in_the_rounds_is_fatal():
    root = _template(*_rows(2, "driver_name"), "round_1_number", "round_3_number")
    with pytest.raises(CapacityError) as exc:
        STANDINGS_DRIVERS_CATALOGUE.column_capacity(root)
    assert "gap" in str(exc.value)


def test_a_gap_in_the_cars_of_a_round_is_fatal():
    from utils.svg_document import FieldIndex

    root = _template(
        "row_1_round_1_driver_1_name", "row_1_round_1_driver_3_name"
    )
    declared = set(FieldIndex(root).declared())
    car_nest = STANDINGS_CONSTRUCTORS_CATALOGUE.rows.nested.nested
    with pytest.raises(CapacityError) as exc:
        car_nest.declared_capacity("row_1_round_1", declared)
    assert "gap" in str(exc.value)


# ── 6. Sibling detection ──────────────────────────────────────────────────


def test_a_driver_field_belongs_to_the_drivers_row_alone():
    assert {"driver_name", "driver_flag"} <= sibling_row_fields(CONSTRUCTORS)


def test_the_drivers_row_inherits_no_field_from_its_championship_sibling():
    """Nothing of the constructors row is foreign to the drivers row, which is its superset.

    The drivers row is not sibling-free outright: constitution v4.6.0 widened the relation to
    the several graphics of one **source module**, and results, standings and verdicts are all
    the results module's. What the drivers row inherits from its own championship sibling is
    still nothing, which is what this test pins.
    """
    constructors_fields = set(STANDINGS_CONSTRUCTORS_CATALOGUE.rows.fields)
    assert constructors_fields & sibling_row_fields(DRIVERS) == set()

    # The results row fields are foreign to it, and are caught.
    assert {"tyre", "best_lap", "time"} <= sibling_row_fields(DRIVERS)


def test_a_driver_field_on_the_constructors_template_is_reported():
    declared = [*_rows(2), "row_1_driver_name", "row_2_driver_flag"]
    found = sibling_fields_declared(CONSTRUCTORS, declared)
    assert found == ["row_1_driver_name", "row_2_driver_flag"]


def test_an_id_belonging_to_no_catalogue_is_not_a_sibling_fault():
    """A hand-authored SVG carries identifiers on every node it holds (XIV.3)."""
    declared = [*_rows(1), "row_1_decorative_swoosh", "path4821"]
    assert sibling_fields_declared(CONSTRUCTORS, declared) == []


# ── 7. Assets ─────────────────────────────────────────────────────────────


def test_each_asset_field_names_its_class():
    cat = STANDINGS_DRIVERS_CATALOGUE
    assert cat.asset_class_for("row_1_team_image") == "team"
    assert cat.asset_class_for("row_1_driver_flag") == "flag"
    assert cat.asset_class_for("row_1_position_change_marker") == "marker"
    assert cat.asset_class_for("round_3_flag") == "flag"
    assert cat.asset_class_for("round_3_image") is None


def test_the_constructors_graphic_resolves_no_flag():
    assert STANDINGS_CONSTRUCTORS_CATALOGUE.asset_class_for("row_1_driver_flag") is None


def test_a_grid_cell_is_text_and_carries_no_asset():
    """The row pattern matches its id, so the lookup must fall through rather than stop."""
    assert (
        STANDINGS_DRIVERS_CATALOGUE.asset_class_for("row_1_round_2_feature_race_result")
        is None
    )
    assert (
        STANDINGS_CONSTRUCTORS_CATALOGUE.asset_class_for("row_1_round_2_driver_1_name")
        is None
    )


def test_no_standings_field_draws_a_fallback_for_an_absent_datum():
    """An absent nationality removes the flag with a notice; there is no absence to depict."""
    assert STANDINGS_DRIVERS_CATALOGUE.rows.fallback_when_absent == frozenset()
    assert STANDINGS_CONSTRUCTORS_CATALOGUE.rows.fallback_when_absent == frozenset()


# ── 8. The shipped templates ──────────────────────────────────────────────


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_the_shipped_template_satisfies_its_own_catalogue(key):
    root, declared = _shipped(key)
    catalogue = catalogue_for(key)
    assert catalogue.all_mandatory_ids(root) - declared == set()


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_the_shipped_template_declares_nothing_its_catalogue_cannot_name(key):
    root, declared = _shipped(key)
    catalogue = catalogue_for(key)
    assert sorted(declared - catalogue.all_known_ids(root)) == []


@pytest.mark.parametrize("key", [DRIVERS, CONSTRUCTORS])
def test_the_shipped_template_carries_no_sibling_field(key):
    _, declared = _shipped(key)
    assert sibling_fields_declared(key, declared) == []


# --------------------------------------------------------------------------
# 044 — a round heading is a country flag, never a circuit map
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "catalogue",
    [STANDINGS_DRIVERS_CATALOGUE, STANDINGS_CONSTRUCTORS_CATALOGUE],
    ids=["drivers", "constructors"],
)
def test_the_round_heading_draws_a_flag_and_not_a_track_map(catalogue):
    """A round is a column heading here, at a size no circuit outline survives.

    Only the calendar and the check-in graphic may declare a track-class field
    (Constitution XIV.13).
    """
    columns = catalogue.columns
    assert "flag" in columns.fields
    assert "image" not in columns.fields, "a standings round must not draw a map"
    assert columns.assets.get("flag") == "flag"
    assert "track" not in columns.assets.values()


@pytest.mark.parametrize(
    "catalogue",
    [STANDINGS_DRIVERS_CATALOGUE, STANDINGS_CONSTRUCTORS_CATALOGUE],
    ids=["drivers", "constructors"],
)
def test_no_field_of_a_standings_catalogue_draws_the_track_class(catalogue):
    classes = set(catalogue.columns.assets.values()) | set(catalogue.rows.assets.values())
    assert "track" not in classes


def test_the_round_heading_flag_id_names_its_class():
    """XIV.11 — an id must name the class it draws."""
    columns = STANDINGS_DRIVERS_CATALOGUE.columns
    assert columns.prefix == "round"
    assert "flag" in columns.fields
