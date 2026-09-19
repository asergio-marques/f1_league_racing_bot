"""Where a calendar's cancellation overlay is drawn is checked, not only that it exists (#175).

The render keeps the overlay or removes it and does nothing else to it — it never moves it,
resizes it or restacks it. So a veil drawn under the round's text, left at another round's
coordinates after a copy, or below the cut renders perfectly in every check a template author
runs, and shows itself for the first time in a league's channel the day a round is cancelled.

Each rule here is either read off the tree — document order, which group a node sits in — or
compares the overlay against the fields of its **own** round, which share whatever transform
their group carries. Nothing depends on resolving a transform, because nothing in the codebase
can, and a template authored in a graphical editor would otherwise be refused for being drawn
the ordinary way.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_validity_service import calendar_overlay_faults_of  # noqa: E402

SVG_NS = "http://www.w3.org/2000/svg"
PACKAGED = (
    Path(__file__).resolve().parents[2]
    / "resources" / "defaults" / "templates" / "calendar_template.svg"
)

#: One round's card: the fields a round draws, and where this fixture puts them.
FIELDS = ("number", "country_name", "race_name", "date")
CARD_W, CARD_H, ROW_H = 600.0, 100.0, 140.0


def _template(
    rounds: int = 2,
    *,
    group: bool = True,
    overlay_first: bool = False,
    overlay_outside: bool = False,
    overlay_below_crop: bool = False,
    overlay_box: tuple[float, float, float, float] | None = None,
    overlay_measurable: bool = True,
    abreast: bool = False,
):
    """A calendar template, correct unless a keyword asks for one fault.

    *abreast* lays two rounds to a row, as the packaged template does, which is the layout in
    which a veil copied from the round beside it is a real mistake rather than a contrived one.
    """
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "1300")
    root.set("height", str(ROW_H * rounds))
    etree.SubElement(root, f"{{{SVG_NS}}}text").set("id", "division_name")

    for ordinal in range(1, rounds + 1):
        if abreast:
            left = 40.0 + ((ordinal - 1) % 2) * 640.0
            top = 20.0 + ((ordinal - 1) // 2) * ROW_H
            crop_y = 20.0 + (((ordinal - 1) // 2) + 1) * ROW_H
        else:
            left, top = 40.0, 20.0 + (ordinal - 1) * ROW_H
            crop_y = 20.0 + ordinal * ROW_H

        container = root
        if group:
            container = etree.SubElement(root, f"{{{SVG_NS}}}g")
            container.set("id", f"round_{ordinal}_group")

        def _overlay(into):
            node = etree.SubElement(into, f"{{{SVG_NS}}}g")
            node.set("id", f"round_{ordinal}_cancelled")
            if not overlay_measurable:
                etree.SubElement(node, f"{{{SVG_NS}}}path").set("d", "M0 0 L1 1")
                return node
            box = overlay_box or (left, top, CARD_W, CARD_H)
            veil = etree.SubElement(node, f"{{{SVG_NS}}}rect")
            for name, value in zip(("x", "y", "width", "height"), box):
                veil.set(name, str(value))
            return node

        if overlay_first:
            _overlay(container)

        for position, suffix in enumerate(FIELDS):
            child = etree.SubElement(container, f"{{{SVG_NS}}}text")
            child.set("id", f"round_{ordinal}_{suffix}")
            child.set("x", str(left + 20.0 + position))
            child.set("y", str(top + 40.0))

        crop = etree.SubElement(container, f"{{{SVG_NS}}}rect")
        crop.set("id", f"round_{ordinal}_vertical_crop_point")
        crop.set("x", str(left))
        crop.set("y", str(crop_y))

        if not overlay_first:
            if overlay_below_crop:
                node = _overlay(container)
                node[0].set("y", str(crop_y + 5.0))
            elif overlay_outside:
                _overlay(root)
            else:
                _overlay(container)

    return root


def _faults(root) -> list[str]:
    return calendar_overlay_faults_of(root, "calendar_template")


# ── The template drawn correctly ──────────────────────────────────────────


@pytest.mark.parametrize("abreast", [False, True])
def test_an_overlay_drawn_as_the_docs_say_passes(abreast):
    assert _faults(_template(4, abreast=abreast)) == []


def test_a_template_without_round_groups_is_judged_on_the_other_rules():
    """The group is optional, so its absence is not a fault and must not skip the rest."""
    assert _faults(_template(2, group=False)) == []
    assert _faults(_template(2, group=False, overlay_first=True))


def test_the_packaged_calendar_draws_every_overlay_where_it_belongs():
    root = etree.parse(str(PACKAGED)).getroot()
    assert _faults(root) == []


def test_nothing_is_checked_on_another_image_type():
    """Only the calendar declares the field; another type's template is not its business."""
    assert calendar_overlay_faults_of(_template(2, overlay_first=True), "lineup_template") == []


# ── One rule at a time ────────────────────────────────────────────────────


def test_an_overlay_drawn_before_its_round_is_refused():
    faults = _faults(_template(2, overlay_first=True))
    assert len(faults) == 2
    assert "`round_1_cancelled` is drawn before" in faults[0]
    assert "Draw it last of its round" in faults[0]


def test_an_overlay_outside_its_round_group_is_refused():
    faults = _faults(_template(2, overlay_outside=True))
    assert "outside `round_1_group`" in faults[0]


def test_an_overlay_at_or_below_the_crop_point_is_refused():
    faults = _faults(_template(2, overlay_below_crop=True))
    assert "at or below `round_1_vertical_crop_point`" in faults[0]


def test_an_overlay_left_at_another_round_s_position_is_refused():
    """The copy an author makes when they fill in the rest of the rounds: the id is right,
    the coordinates are the round they copied from. Two abreast, so it is the card beside it."""
    root = _template(2, abreast=True)
    veil = root.find(f".//*[@id='round_2_cancelled']")[0]
    veil.set("x", "40")  # round 1's column

    faults = _faults(root)

    assert len(faults) == 1
    assert "`round_2_cancelled` does not cover `round_2_country_name`" in faults[0]
    assert "copied from another round" in faults[0]


def test_an_overlay_too_small_to_veil_its_card_is_refused():
    faults = _faults(_template(1, overlay_box=(40.0, 20.0, 5.0, 5.0)))
    assert "does not cover" in faults[0]


def test_an_overlay_that_declares_no_box_is_not_measured():
    """A veil drawn as paths declares no box. Refusing it would be a rule about how it is
    drawn rather than where, so the covering rule is skipped and the rest still apply."""
    assert _faults(_template(2, overlay_measurable=False)) == []
    assert _faults(_template(2, overlay_measurable=False, overlay_first=True))


def test_a_round_whose_overlay_is_missing_is_left_to_the_catalogue():
    """Absence is the mandatory-field report's to make; this one says nothing about it."""
    root = _template(2)
    overlay = root.find(f".//*[@id='round_2_cancelled']")
    overlay.getparent().remove(overlay)

    assert _faults(root) == []
