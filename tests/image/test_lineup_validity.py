"""Unit tests for the lineup's verification moments — 047.

Constitution XIV.9: **stand-ins warn, the real data refuses.** The lineup used to exercise
both halves, being the one type whose fields were named after a league's own teams. Since
v6.0.0 it exercises neither: every field of it is verifiable against the **template alone**,
so there is no moment at which it can only be compared with a stand-in, and no divergence of
it is ever a warning (047 FR-024).

Covers:
  1. Layer 2 evaluates the lineup from the template, with no division in view.
  2. Every mandatory field — team blocks included — is checkable the moment a template is
     named: a missing `reserve_group`, `division_name` or `team_<x>_name` is a rejection.
  3. Contiguity of both collections is a rejection at that same moment.
  4. The lineup reports genuine depth 2, not a skip.
"""
from __future__ import annotations

import os
import sys
from types import SimpleNamespace as NS

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import LINEUP_CATALOGUE, CapacityError
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
    for ordinal, seats in enumerate(teams or [1], start=1):
        node(root, f"team_{ordinal}_name")
        for seat in range(1, seats + 1):
            node(root, f"team_{ordinal}_driver_{seat}_name")
    container = node(root, "reserve_group", "g") if reserve_group else root
    for slot in range(1, reserve_slots + 1):
        node(container, f"reserve_driver_{slot}_name")
    return root


# ── Layer 2 evaluates the lineup from the template alone ──────────────────


def test_layer_two_applies_to_the_lineup():
    assert CatalogueLayer().applies_to("lineup_template")


def test_the_team_blocks_are_checkable_with_no_division_in_view():
    """047 FR-024. The template says what it declares; no stand-in is needed."""
    ids = LINEUP_CATALOGUE.all_mandatory_ids(_root(teams=[2]))

    assert "team_1_name" in ids
    assert "team_1_driver_1_name" in ids
    assert "team_1_driver_2_name" in ids


def test_a_template_declaring_no_team_block_at_all_is_refused():
    """FR-020: at least one block, and at least one slot within it."""
    root = _root(teams=[])
    # `teams=[]` still yields the default single block, so strip it explicitly.
    for node in list(root):
        if (node.get("id") or "").startswith("team_"):
            root.remove(node)

    with pytest.raises(CapacityError):
        LINEUP_CATALOGUE.all_mandatory_ids(root)


def test_a_gap_in_the_team_numbering_is_refused_at_naming_time():
    root = _root(teams=[1])
    for ordinal in (2, 4):
        node = etree.SubElement(root, f"{{{SVG_NS}}}text")
        node.set("id", f"team_{ordinal}_name")

    with pytest.raises(CapacityError):
        LINEUP_CATALOGUE.all_mandatory_ids(root)


# ── What *is* checkable at naming time ────────────────────────────────────


def test_a_template_may_omit_the_reserve_block_entirely():
    """Declaring the block is how a template asks for reserves to be drawn.

    Nothing about the block is mandatory. A league that does not want reserves on its
    lineup sheet declares no slots and no group, and naming the file is not refused for
    it — the inversion of the rule that held until the block became optional.
    """
    ids = LINEUP_CATALOGUE.all_mandatory_ids(_root(reserve_group=False, reserve_slots=0))

    assert "reserve_group" not in ids
    assert "reserve_driver_1_name" not in ids
    # The rest of the lineup is demanded exactly as before.
    assert "division_name" in ids
    assert "team_1_name" in ids


def test_the_group_is_not_demanded_of_a_template_that_declares_slots_without_it():
    """The group is chrome, not a contract: slots may be declared loose."""
    assert "reserve_group" not in LINEUP_CATALOGUE.all_mandatory_ids(
        _root(reserve_group=False)
    )


def test_a_missing_division_name_is_found_at_naming_time():
    assert "division_name" in LINEUP_CATALOGUE.all_mandatory_ids(_root())


def test_the_first_reserve_slot_is_required_of_a_template_that_declares_slots():
    """Declaring the block half-way is still a fault, even though omitting it is not."""
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

# ── One comparison, one severity (047 FR-024) ─────────────────────────────


def test_the_lineup_is_measured_by_counts_and_never_by_names():
    """FR-025: a stand-in stands for how *many* members are drawn, never for which.

    The block count and the per-block slot count are the whole of what a division is
    measured against, and both are read from the template. There is nothing a division
    could be compared against approximately, so nothing to downgrade to a warning.
    """
    root = _root(teams=[2, 2, 2])

    assert LINEUP_CATALOGUE.capacity(root) == 3
    assert LINEUP_CATALOGUE.rows.nested.declared_capacity("team_1", 
        {e.get("id") for e in root.iter() if e.get("id")}) == 2
