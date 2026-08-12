"""Unit tests for the calendar field catalogue — T009.

Covers:
  1. The catalogue's declared shape (mandatory/optional/rows/assets).
  2. Capacity derived from the template rather than fixed in code.
  3. The two shapes XIV.11 forbids: no member at all, and a gap in the numbering.
  4. A round addressed by an Inkscape layer label counts as one addressed by @id.
  5. Valueless fields — the crop point must be present but is never filled.
  6. ``declared_capacities()`` still reports only fixed capacities, so the driver-seat
     guard in placement_service is untouched by a template-derived one.
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (
    CALENDAR_CATALOGUE,
    CATALOGUES,
    CapacityError,
    FieldCatalogue,
    RowSpec,
    catalogue_for,
    declared_capacities,
)

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"


def _template(*ids: str, labels: tuple[str, ...] = ()) -> etree._Element:
    """A minimal SVG root declaring *ids* as elements and *labels* as layers."""
    root = etree.Element(f"{{{SVG_NS}}}svg", nsmap={"svg": SVG_NS, "inkscape": INKSCAPE_NS})
    root.set("width", "1000")
    root.set("height", "2000")
    for name in ids:
        child = etree.SubElement(root, f"{{{SVG_NS}}}text")
        child.set("id", name)
    for label in labels:
        layer = etree.SubElement(root, f"{{{SVG_NS}}}g")
        layer.set(f"{{{INKSCAPE_NS}}}groupmode", "layer")
        layer.set(f"{{{INKSCAPE_NS}}}label", label)
    return root


def _rounds(count: int) -> list[str]:
    """Every mandatory field of *count* contiguous rounds."""
    out: list[str] = []
    for index in range(1, count + 1):
        for suffix in ("number", "country_name", "race_name", "date", "vertical_crop_point"):
            out.append(f"round_{index}_{suffix}")
    return out


# ── 1. Shape ──────────────────────────────────────────────────────────────


def test_calendar_catalogue_is_registered_and_not_empty():
    assert catalogue_for("calendar_template") is CALENDAR_CATALOGUE
    assert not CALENDAR_CATALOGUE.is_empty


def test_calendar_catalogue_classifies_its_whole_graphic_fields():
    assert CALENDAR_CATALOGUE.mandatory == frozenset({"division_name"})
    assert CALENDAR_CATALOGUE.optional == frozenset({"season_number", "division_tier"})


def test_calendar_rows_are_named_round_not_row():
    """XIV.11: a collection is named for the thing it repeats."""
    assert CALENDAR_CATALOGUE.rows is not None
    assert CALENDAR_CATALOGUE.rows.prefix == "round"
    assert CALENDAR_CATALOGUE.rows.field_id(10, "date") == "round_10_date"
    assert CALENDAR_CATALOGUE.rows.group_id(3) == "round_3_group"


def test_calendar_round_image_declares_the_track_asset_class():
    assert CALENDAR_CATALOGUE.asset_class_for("round_4_image") == "track"
    assert CALENDAR_CATALOGUE.asset_class_for("round_4_date") is None


def test_the_other_fourteen_catalogues_are_still_empty():
    populated = [key for key, cat in CATALOGUES.items() if not cat.is_empty]
    assert populated == ["calendar_template"]


# ── 2. Derived capacity ───────────────────────────────────────────────────


def test_capacity_is_derived_from_the_template():
    assert CALENDAR_CATALOGUE.rows.is_derived
    assert CALENDAR_CATALOGUE.capacity(_template(*_rounds(12))) == 12
    assert CALENDAR_CATALOGUE.capacity(_template(*_rounds(1))) == 1


def test_capacity_without_a_root_is_unknown_not_zero():
    """A caller that cannot supply the template must not read 'none' as 'nought'."""
    assert CALENDAR_CATALOGUE.capacity() is None


def test_mandatory_ids_span_the_derived_capacity():
    ids = CALENDAR_CATALOGUE.all_mandatory_ids(_template(*_rounds(3)))
    assert "division_name" in ids
    assert "round_3_vertical_crop_point" in ids
    assert "round_4_number" not in ids


# ── 3. The two forbidden shapes ───────────────────────────────────────────


def test_template_declaring_no_round_is_rejected():
    with pytest.raises(CapacityError, match="declares no `round`"):
        CALENDAR_CATALOGUE.capacity(_template("division_name"))


def test_gap_in_the_round_numbering_is_rejected():
    root = _template(*_rounds(2), "round_4_number")
    with pytest.raises(CapacityError, match="gap"):
        CALENDAR_CATALOGUE.capacity(root)


def test_gap_error_names_the_missing_ordinals():
    root = _template("round_1_number", "round_5_number")
    with pytest.raises(CapacityError, match="2, 3, 4"):
        CALENDAR_CATALOGUE.capacity(root)


# ── 4. Layer-label addressing (XIV.2) ─────────────────────────────────────


def test_a_round_addressed_by_layer_label_counts():
    """A manager sets the label; the editor generated the id they never saw."""
    root = _template(*_rounds(2), labels=("round_3_number",))
    assert CALENDAR_CATALOGUE.capacity(root) == 3


# ── 5. Valueless fields ───────────────────────────────────────────────────


def test_crop_point_is_mandatory_but_valueless():
    root = _template(*_rounds(2))
    assert "round_2_vertical_crop_point" in CALENDAR_CATALOGUE.all_mandatory_ids(root)
    assert "round_2_vertical_crop_point" in CALENDAR_CATALOGUE.valueless_ids(root)
    assert "round_2_date" not in CALENDAR_CATALOGUE.valueless_ids(root)


# ── 6. The driver-seat guard is untouched ─────────────────────────────────


def test_declared_capacities_excludes_a_template_derived_capacity():
    """placement_service guards seated drivers; a calendar's collection is rounds."""
    assert "calendar_template" not in declared_capacities()


def test_declared_capacities_still_reports_a_fixed_one():
    spec = RowSpec(prefix="row", capacity=20, fields=frozenset({"name"}))
    catalogue = FieldCatalogue(rows=spec)
    assert catalogue.capacity() == 20
    assert catalogue.capacity(_template()) == 20
