"""How the packaged check-in call is laid out (2026-09-01).

Three decisions taken over the shipped template, each of which a later edit could undo
without anything else in the suite noticing:

* the **grand prix** name takes the large line and the **circuit** name the line under it.
  They were the other way round, which put the circuit name on both lines wherever the
  preview supplied it (see ``test_image_preview_race_name``);
* the country is drawn as its flag and **not** written out beside it. It had a card of its
  own saying a second time what the flag says;
* the three facts that remain — format, date, start time — share the whole row the four
  used to, so that a date in any of the configured formats stands on one line.

A league's own template may still declare ``country_name``: the catalogue keeps the field,
and these tests speak only for what ships.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

SVG_NS = "http://www.w3.org/2000/svg"
ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "resources" / "defaults" / "templates" / "rsvp_template.svg"

CANVAS_LEFT = 48.0
CANVAS_RIGHT = 1152.0


@pytest.fixture(scope="module")
def root():
    return etree.parse(str(TEMPLATE)).getroot()


def _by_id(root, field_id):
    found = root.xpath(f'//*[@id="{field_id}"]')
    return found[0] if found else None


def _classes(element):
    return set((element.get("class") or "").split())


# --------------------------------------------------------------------------
# The two names of a round
# --------------------------------------------------------------------------

def test_the_grand_prix_name_takes_the_large_line(root):
    assert "track" in _classes(_by_id(root, "race_name"))


def test_the_circuit_name_takes_the_line_beneath_it(root):
    circuit = _by_id(root, "track_name")
    assert "sub" in _classes(circuit)
    assert float(circuit.get("y")) > float(_by_id(root, "race_name").get("y"))


def test_the_circuit_name_carries_a_group_so_it_can_leave(root):
    """``track_name`` is optional; the mandatory grand prix name above it is not."""
    group = _by_id(root, "track_name_group")
    assert group is not None
    assert _by_id(root, "track_name") in group.iter()


# --------------------------------------------------------------------------
# The country, drawn once
# --------------------------------------------------------------------------

def test_the_country_is_not_written_out(root):
    assert _by_id(root, "country_name") is None
    assert _by_id(root, "country_name_group") is None


def test_the_country_is_still_drawn_as_its_flag(root):
    assert _by_id(root, "track_flag") is not None


# --------------------------------------------------------------------------
# The row of facts
# --------------------------------------------------------------------------

FACTS = ("round_format", "round_date", "round_time")


def test_the_three_facts_stand_on_one_row(root):
    ys = {float(_by_id(root, field).get("y")) for field in FACTS}
    assert len(ys) == 1


def test_the_three_facts_are_given_equal_room(root):
    widths = {_by_id(root, field).get("style") for field in FACTS}
    assert len(widths) == 1
    assert "inline-size:310px" in widths.pop()


def test_the_row_of_cards_spans_the_canvas(root):
    """Whatever the cards' number, they reach both margins and are evenly sized."""
    row = sorted(
        (float(r.get("x")), float(r.get("width")))
        for r in root.iter(f"{{{SVG_NS}}}rect")
        if r.get("y") == "272"
    )
    assert len(row) == len(FACTS)
    assert row[0][0] == CANVAS_LEFT
    assert row[-1][0] + row[-1][1] == CANVAS_RIGHT
    assert len({width for _, width in row}) == 1
