"""Unit tests for the lineup catalogue's two collection shapes — 047.

The team collection was **keyed** by the normalised team name until v6.0.0. It is now an
ordinary **ordinal** collection like every other in the module, and this file was rewritten
with it. What that removed:

  * `KeyedSpec`, and with it the whole idea of a template authored against one league;
  * `LineupBinding`, which existed only to tell a keyed catalogue which members existed;
  * `divergent_members`, the both-directions divergence a data-fixed capacity needed.

Covers:
  1. Enumeration from the template alone — no binding, because there is nothing to bind.
  2. `team_<x>_name` and `team_<x>_driver_<y>_name` mandatory throughout declared blocks.
  3. `team_<x>_group` optional; the reserve's group mandatory.
  4. Blocks numbered contiguously from 1; a gap is fatal.
  5. Seat slots counted **per block**, so blocks may differ in size.
  6. The seats are a *ceiling* (FR-018), exactly as the constructors grid's cars are.
  7. Reserve slots counted from the template through `singleton_capacity`, never `capacity`.
  8. Asset classes resolve for ordinal, nested and singleton fields alike.
  9. The calendar is untouched by any of it.
"""
from __future__ import annotations

import os
import sys

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import (
    CALENDAR_CATALOGUE,
    LINEUP_CATALOGUE,
    CapacityError,
    catalogue_for,
    declared_capacities,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _svg():
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")
    return root


def _node(root, field_id: str) -> None:
    child = etree.SubElement(root, f"{{{SVG_NS}}}text")
    child.set("id", field_id)


def _template(
    blocks=2,
    seats=2,
    reserve_slots: int = 3,
    *,
    division: bool = True,
    groups: bool = True,
    seats_by_block: dict[int, int] | None = None,
):
    """A lineup template declaring *blocks* ordinal team blocks.

    *seats_by_block* overrides the slot count for a named block, so a template whose
    blocks differ in size can be built — which is the whole point of counting per block.
    """
    root = _svg()
    if division:
        _node(root, "division_name")

    for block in range(1, blocks + 1):
        if groups:
            _node(root, f"team_{block}_group")
        _node(root, f"team_{block}_name")
        _node(root, f"team_{block}_image")
        count = (seats_by_block or {}).get(block, seats)
        for seat in range(1, count + 1):
            _node(root, f"team_{block}_driver_{seat}_name")
            _node(root, f"team_{block}_driver_{seat}_flag")
            _node(root, f"team_{block}_driver_{seat}_image")

    _node(root, "reserve_group")
    _node(root, "reserve_name")
    for slot in range(1, reserve_slots + 1):
        _node(root, f"reserve_driver_{slot}_name")
        _node(root, f"reserve_driver_{slot}_flag")
        _node(root, f"reserve_driver_{slot}_image")
    return root


# ── 1. Enumeration needs the template and nothing else ────────────────────


def test_every_id_is_enumerable_from_the_template_alone():
    """No binding, no division, no league. The file says what it holds."""
    ids = LINEUP_CATALOGUE.all_known_ids(_template(blocks=2, seats=2))

    assert "team_1_name" in ids
    assert "team_2_driver_2_image" in ids
    assert "division_name" in ids
    assert "reserve_driver_1_name" in ids


def test_no_id_bears_a_team_name():
    """The whole point: a field never names a datum of any league."""
    ids = LINEUP_CATALOGUE.all_known_ids(_template(blocks=3, seats=2))

    assert not any("red_bull" in name or "ferrari" in name for name in ids)
    assert all(
        name.startswith(("team_1", "team_2", "team_3", "reserve", "division", "season"))
        for name in ids
    )


# ── 2 & 3. Mandatory and optional ─────────────────────────────────────────


def test_team_name_and_seat_name_are_mandatory_throughout_declared_blocks():
    mandatory = LINEUP_CATALOGUE.all_mandatory_ids(_template(blocks=2, seats=2))

    for block in (1, 2):
        assert f"team_{block}_name" in mandatory
        for seat in (1, 2):
            assert f"team_{block}_driver_{seat}_name" in mandatory


def test_team_group_and_images_are_optional():
    root = _template(blocks=2, seats=2)
    mandatory = LINEUP_CATALOGUE.all_mandatory_ids(root)
    known = LINEUP_CATALOGUE.all_known_ids(root)

    for field_id in ("team_1_group", "team_1_image", "team_1_driver_1_flag"):
        assert field_id in known
        assert field_id not in mandatory


def test_a_template_declining_the_team_group_is_still_valid():
    """FR-004: the group is optional, the fields being removed one by one without it."""
    root = _template(blocks=2, seats=2, groups=False)

    mandatory = LINEUP_CATALOGUE.all_mandatory_ids(root)

    assert "team_1_name" in mandatory
    assert "team_1_group" not in mandatory


def test_the_reserve_group_is_optional_like_every_other():
    """The block became optional so a league may author a sheet without reserves.

    It was the module's one mandatory group until then; nothing about the reserve block
    is demanded now, and a template declaring none of it is valid.
    """
    root = _template(blocks=1, seats=1)

    assert "reserve_group" not in LINEUP_CATALOGUE.all_mandatory_ids(root)


# ── 4. Contiguity ─────────────────────────────────────────────────────────


def test_team_blocks_are_counted_from_the_template():
    assert LINEUP_CATALOGUE.capacity(_template(blocks=5, seats=2)) == 5
    assert LINEUP_CATALOGUE.capacity(_template(blocks=11, seats=2)) == 11


def test_a_gap_in_the_team_numbering_is_fatal():
    """FR-002: 1, 2, 4 is a fault of the template, not a template of three blocks."""
    root = _svg()
    _node(root, "division_name")
    for block in (1, 2, 4):
        _node(root, f"team_{block}_name")
        _node(root, f"team_{block}_driver_1_name")
    _node(root, "reserve_group")
    _node(root, "reserve_driver_1_name")

    with pytest.raises(CapacityError):
        LINEUP_CATALOGUE.capacity(root)


def test_capacity_is_unknown_without_a_template():
    assert LINEUP_CATALOGUE.capacity(None) is None


# ── 5. Seats counted per block ────────────────────────────────────────────


def test_blocks_may_declare_different_seat_counts():
    """FR-003: `<y>` runs to the count *that block* declares, not a count for all."""
    root = _template(blocks=3, seats=2, seats_by_block={1: 3, 3: 1})
    ids = LINEUP_CATALOGUE.all_known_ids(root)

    assert "team_1_driver_3_name" in ids
    assert "team_2_driver_2_name" in ids
    assert "team_2_driver_3_name" not in ids
    assert "team_3_driver_1_name" in ids
    assert "team_3_driver_2_name" not in ids


# ── 6. The seats are a ceiling, as the constructors grid's cars are ───────


def test_the_seat_nest_is_a_ceiling():
    """FR-018: one rule for every nested collection bounded by its containing member."""
    assert LINEUP_CATALOGUE.rows is not None
    assert LINEUP_CATALOGUE.rows.nested is not None
    assert LINEUP_CATALOGUE.rows.nested.capacity_per_member is True


# ── 7. The reserve ────────────────────────────────────────────────────────


def test_reserve_capacity_is_counted_from_the_template():
    assert LINEUP_CATALOGUE.singleton_capacity(_template(reserve_slots=5)) == 5


def test_reserve_capacity_is_unknown_without_a_template():
    assert LINEUP_CATALOGUE.singleton_capacity(None) is None


def test_a_gap_in_the_reserve_numbering_is_fatal():
    root = _svg()
    _node(root, "division_name")
    _node(root, "team_1_name")
    _node(root, "team_1_driver_1_name")
    _node(root, "reserve_group")
    for slot in (1, 2, 4):
        _node(root, f"reserve_driver_{slot}_name")

    with pytest.raises(CapacityError):
        LINEUP_CATALOGUE.singleton_capacity(root)


def test_reserve_seat_one_is_mandatory_and_the_rest_are_not():
    root = _template(reserve_slots=4)
    mandatory = LINEUP_CATALOGUE.all_mandatory_ids(root)

    assert "reserve_driver_1_name" in mandatory
    assert "reserve_driver_2_name" not in mandatory
    assert "reserve_driver_4_name" not in mandatory


def test_reserve_capacity_is_independent_of_the_team_block_count():
    """Research R1: `capacity()` reaches the team blocks; the reserve has its own reader."""
    assert LINEUP_CATALOGUE.singleton_capacity(_template(blocks=3, reserve_slots=4)) == 4
    assert LINEUP_CATALOGUE.singleton_capacity(_template(blocks=11, reserve_slots=4)) == 4
    assert LINEUP_CATALOGUE.capacity(_template(blocks=11, reserve_slots=4)) == 11


# ── 8. Asset classes ──────────────────────────────────────────────────────


def test_asset_classes_resolve_for_every_shape():
    assert LINEUP_CATALOGUE.asset_class_for("team_1_image") == "team"
    assert LINEUP_CATALOGUE.asset_class_for("team_2_driver_1_flag") == "flag"
    assert LINEUP_CATALOGUE.asset_class_for("team_2_driver_1_image") == "driver"
    assert LINEUP_CATALOGUE.asset_class_for("reserve_image") == "team"
    assert LINEUP_CATALOGUE.asset_class_for("reserve_driver_1_flag") == "flag"
    assert LINEUP_CATALOGUE.asset_class_for("division_name") is None


# ── 9. Nothing else moved ─────────────────────────────────────────────────


def test_the_calendar_is_untouched():
    assert catalogue_for("calendar_template") is CALENDAR_CATALOGUE
    assert CALENDAR_CATALOGUE.rows is not None
    assert CALENDAR_CATALOGUE.rows.prefix == "round"


def test_the_lineup_declares_no_fixed_capacity():
    """`declared_capacities` carries only capacities fixed in code, and guards *drivers*.

    The lineup belongs in neither sense: its block count is derived from the template, and
    it counts teams rather than drivers.
    """
    caps = declared_capacities()

    assert isinstance(caps, dict)
    assert "lineup_template" not in caps


# ── What the shipped file declares (047 FR-034 to FR-036) ─────────────────

import re as _re  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

SHIPPED = (
    _Path(__file__).resolve().parents[2]
    / "resources" / "defaults" / "templates" / "lineup_template.svg"
)


def _shipped_root():
    return etree.parse(str(SHIPPED)).getroot()


def test_the_shipped_template_declares_eleven_blocks_of_two_seats():
    """FR-036. Eleven covers the ten default constructor teams with one spare."""
    root = _shipped_root()

    assert LINEUP_CATALOGUE.capacity(root) == 11
    nest = LINEUP_CATALOGUE.rows.nested
    declared = {e.get("id") for e in root.iter() if e.get("id")}
    for block in range(1, 12):
        assert nest.declared_capacity(f"team_{block}", declared) == 2, block


def test_the_shipped_template_declares_ten_reserve_slots():
    """Ten is the shipped guess at how many stand-ins a league carries at once.

    It bounds this file and nothing else: a league authoring its own may declare any
    number, or none at all.
    """
    assert LINEUP_CATALOGUE.singleton_capacity(_shipped_root()) == 10


def test_the_shipped_template_declares_a_group_for_every_block():
    """FR-035: a league sees the removable group in a working example."""
    declared = {e.get("id") for e in _shipped_root().iter() if e.get("id")}

    for block in range(1, 12):
        assert f"team_{block}_group" in declared, block


def test_the_shipped_template_names_no_team_of_any_league():
    """FR-034, and the whole point of the change.

    Checked across ids, Inkscape labels, comments **and** drawn text — the invented names
    this file replaced lived in all four, and an id sweep alone would have missed three.
    """
    source = SHIPPED.read_text(encoding="utf-8")

    # no id or label bears anything but an ordinal after `team_`
    for attribute in _re.findall(r'(?:id|inkscape:label)="(team_[^"]*)"', source):
        assert _re.match(r"^team_\d+(_|$)", attribute), attribute

    for invented in (
        "Apex", "Aurora", "Basalt", "Halcyon", "Ironclad", "Kestrel",
        "Meridian", "Nimbus", "Nordwind", "Solstice", "Vanguard",
    ):
        assert invented not in source, invented

    # nor any real one
    for real in ("Red Bull", "Ferrari", "Mercedes", "McLaren", "Alpine", "Williams"):
        assert real not in source, real


def test_every_field_of_the_shipped_template_carries_a_placeholder():
    """The house convention across all fifteen: a field shows what belongs in it.

    Emptying them would leave a manager opening the file in an editor unable to see the
    layout, judge a font size, or find a field at all. The placeholders are generic — they
    name the *kind* of value, never a league's data.
    """
    root = _shipped_root()

    for element in root.iter(f"{{{SVG_NS}}}text"):
        if not element.get("id"):
            continue
        text = "".join(element.itertext()).strip()
        assert text, element.get("id")

    declared = {
        e.get("id"): "".join(e.itertext()).strip()
        for e in root.iter(f"{{{SVG_NS}}}text")
        if e.get("id")
    }
    assert declared["team_1_name"] == "Team Name"
    assert declared["team_11_driver_2_name"] == "Driver Name"
