"""Unit tests for the lineup's three verification moments — T034.

Constitution XIV.9 (v4.3.0): **stand-ins warn, the real data refuses.** The lineup is the
first type to exercise both halves, and this file pins which half applies where.

Covers:
  1. Layer 2 evaluates the lineup **binding-free** — a stand-in finding can never make a
     template invalid everywhere.
  2. The reserve block is team-independent, so it is checkable the moment a template is
     named: a missing `reserve_group` is a rejection.
  3. Reserve slot contiguity is a rejection at that same moment.
  4. The lineup reports genuine depth 2, not a skip.
  5. `binding_from_teams` and `divergences` — one comparison, three severities.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import LINEUP_CATALOGUE, CapacityError
from services.image_lineup_service import binding_from_teams, divergences
from services.image_validity_service import (
    LAYER_CATALOGUE,
    CatalogueLayer,
)

SVG_NS = "http://www.w3.org/2000/svg"


def _root(*, reserve_group=True, reserve_slots=2, teams=None, division_name=True):
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", "600")

    def node(parent, fid, tag="text"):
        child = etree.SubElement(parent, f"{{{SVG_NS}}}{tag}")
        child.set("id", fid)
        return child

    if division_name:
        node(root, "division_name")
    for key, seats in (teams or {}).items():
        node(root, f"team_{key}_name")
        for seat in range(1, seats + 1):
            node(root, f"team_{key}_driver_{seat}_name")
    container = node(root, "reserve_group", "g") if reserve_group else root
    for slot in range(1, reserve_slots + 1):
        node(container, f"reserve_driver_{slot}_name")
    return root


# ── Layer 2 evaluates the lineup binding-free ─────────────────────────────


def test_layer_two_applies_to_the_lineup():
    assert CatalogueLayer().applies_to("lineup_template")


def test_binding_free_enumeration_never_asks_for_a_team_field():
    """A stand-in finding may not make a template invalid everywhere (research R4)."""
    ids = LINEUP_CATALOGUE.all_mandatory_ids(_root(teams={"red_bull": 2}))
    assert not any(name.startswith("team_") for name in ids)


def test_a_template_declaring_no_team_field_at_all_is_still_valid_at_naming():
    """The teams are unknowable with no division in view, so they are not required."""
    root = _root(teams={})
    missing = [
        name
        for name in LINEUP_CATALOGUE.all_mandatory_ids(root)
        if root.find(f".//*[@id='{name}']") is None and root.get("id") != name
    ]
    assert missing == []


# ── What *is* checkable at naming time ────────────────────────────────────


def test_a_missing_reserve_group_is_found_at_naming_time():
    """The reserve block is a singleton, so it depends on no team (research R4)."""
    ids = LINEUP_CATALOGUE.all_mandatory_ids(_root(reserve_group=False))
    assert "reserve_group" in ids


def test_a_missing_division_name_is_found_at_naming_time():
    assert "division_name" in LINEUP_CATALOGUE.all_mandatory_ids(_root())


def test_the_first_reserve_slot_is_required_at_naming_time():
    assert "reserve_driver_1_name" in LINEUP_CATALOGUE.all_mandatory_ids(_root())


def test_a_gap_in_the_reserve_numbering_is_fatal_at_naming_time():
    root = _root(reserve_slots=0)
    for slot in (1, 3):
        node = etree.SubElement(root, f"{{{SVG_NS}}}text")
        node.set("id", f"reserve_driver_{slot}_name")
    with pytest.raises(CapacityError):
        LINEUP_CATALOGUE.all_mandatory_ids(root)


def test_the_layer_reports_a_capacity_fault_as_its_own_reason():
    """CatalogueLayer catches CapacityError and names it, rather than crashing."""
    root = _root(reserve_slots=0)
    for slot in (1, 3):
        node = etree.SubElement(root, f"{{{SVG_NS}}}text")
        node.set("id", f"reserve_driver_{slot}_name")

    class _Ctx:
        template_key = "lineup_template"

        def resolve(self):
            return "unused"

        def tree(self, path):
            return root

    result = CatalogueLayer().check(_Ctx())
    assert result.passed is False
    assert "gap" in result.reason


def test_the_lineup_declares_depth_two_not_a_skip():
    assert CatalogueLayer().number == LAYER_CATALOGUE
    assert not LINEUP_CATALOGUE.is_empty


# ── One comparison, three severities ──────────────────────────────────────


def _teams(**seats):
    return [NS(name=name, max_seats=count, is_reserve=False) for name, count in seats.items()] + [
        NS(name="Reserve", max_seats=-1, is_reserve=True)
    ]


def test_binding_from_teams_excludes_the_reserve_team():
    binding = binding_from_teams(_teams(Alpine=2, Ferrari=2))
    assert binding.team_keys == ("alpine", "ferrari")
    assert "reserve" not in binding.team_keys


def test_binding_from_teams_skips_an_unusable_name_rather_than_raising():
    """The offending name is reported by the team-name check, not by this one."""
    teams = [NS(name="!!!", max_seats=2, is_reserve=False), NS(name="Alpine", max_seats=2, is_reserve=False)]
    assert binding_from_teams(teams).team_keys == ("alpine",)


def test_the_same_comparison_serves_every_moment():
    root = _root(teams={"alpine": 2})
    agreeing = binding_from_teams(_teams(Alpine=2))
    diverging = binding_from_teams(_teams(Alpine=2, Ferrari=2))

    assert divergences(root, agreeing) == []
    faults = divergences(root, diverging)
    assert len(faults) == 1
    assert "ferrari" in faults[0]


def test_a_seat_count_divergence_is_found():
    root = _root(teams={"alpine": 3})
    faults = divergences(root, binding_from_teams(_teams(Alpine=2)))
    assert any("alpine_driver_3_name" in f for f in faults)
