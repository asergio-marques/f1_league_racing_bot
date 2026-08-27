"""Shortening a row template to the rows a division actually fills (XIV.2, v7.1.0).

The standings and attendance templates provide fifty rows; a division of twenty drivers was
drawn on all fifty, thirty of them blank. The calendar had cropped itself since 037, and this
generalises the mechanism to every image type drawn as a list of rows.

The part that needed the constitution amended, and the part most at risk from a refactor:
all four shipped templates carry a caption band **beneath** the last row, so the crop carries
that band up by the height it is about to remove instead of cutting it off.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import NOTICE_CROP_POINT_OFF_CANVAS
from models.image_catalogues import row_crop_fields
from utils.svg_document import length
from utils.svg_fill import FillSpec, _translate_y, fill

TEMPLATES = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"

#: Every shipped template that shortens itself, and the rows each declares.
ROW_TEMPLATES = {
    "standings_drivers_template.svg": 50,
    "attendance_template.svg": 50,
    "results_qualifying_template.svg": 22,
    "results_race_template.svg": 22,
    "standings_constructors_template.svg": 11,
}


def _document(*, rows: int, canvas: int = 400, pitch: int = 100, footer: bool = True):
    """A minimal template: *rows* rows, each with a crop point, and a caption band."""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="{canvas}" '
             f'viewBox="0 0 200 {canvas}">']
    for ordinal in range(1, rows + 1):
        crop = canvas - pitch * (rows - ordinal)
        parts.append(
            f'<g id="row_{ordinal}_group">'
            f'<text id="row_{ordinal}_name" y="{pitch * (ordinal - 1) + 10}">x</text>'
            f'<rect id="row_{ordinal}_vertical_crop_point" x="0" y="{crop}" '
            f'width="1" height="0"/>'
            f"</g>"
        )
    if footer:
        parts.append(
            f'<g id="footer_group">'
            f'<text id="caption" y="{canvas - 10}">CAPTION</text>'
            f"</g>"
        )
    parts.append("</svg>")
    return etree.fromstring("".join(parts).encode())


def _rendered(root):
    return etree.fromstring(
        fill(
            FillSpec(
                root=root,
                image_type="probe",
                text={"row_1_name": "a"},
                **row_crop_fields(
                    {el.get("id") for el in root.iter() if el.get("id")},
                    drawn=1,
                    capacity=4,
                ),
            )
        ).svg
    )


# ── The helper that decides the three keywords ────────────────────────────


def test_the_cut_is_taken_at_the_last_row_the_data_filled():
    declared = {f"row_{n}_vertical_crop_point" for n in range(1, 5)} | {"footer_group"}
    assert row_crop_fields(declared, drawn=2, capacity=4) == {
        "crop": "row_2_vertical_crop_point",
        "crop_is_final": False,
        "footer": "footer_group",
    }


def test_a_full_division_is_final_so_the_off_canvas_notice_may_fire():
    declared = {f"row_{n}_vertical_crop_point" for n in range(1, 5)}
    assert row_crop_fields(declared, drawn=4, capacity=4)["crop_is_final"] is True


def test_a_template_declaring_no_crop_point_is_not_cropped():
    """Every league's own template pre-dates v7.1.0 and must render at full height."""
    fields = row_crop_fields({"row_1_name", "row_2_name"}, drawn=2, capacity=4)
    assert fields["crop"] is None
    assert fields["footer"] is None


def test_a_template_declaring_no_footer_still_crops():
    declared = {f"row_{n}_vertical_crop_point" for n in range(1, 5)}
    assert row_crop_fields(declared, drawn=2, capacity=4)["footer"] is None


def test_a_graphic_with_no_rows_at_all_is_left_whole():
    """There is no `row_0` point, and one empty row band is worse than the full canvas."""
    declared = {f"row_{n}_vertical_crop_point" for n in range(1, 5)} | {"footer_group"}
    assert row_crop_fields(declared, drawn=0, capacity=4) == {}


# ── The crop, and the footer riding up with it ────────────────────────────


def test_one_row_of_four_cuts_the_canvas_to_that_row_s_point():
    root = _document(rows=4)
    drawn = _rendered(root)
    assert length(drawn.get("height")) == 100
    assert drawn.get("viewBox").split()[3] == "100"


def test_the_caption_band_rides_up_by_exactly_what_the_crop_removed():
    root = _document(rows=4)
    drawn = _rendered(root)
    footer = drawn.xpath('//*[@id="footer_group"]')[0]
    # 400 declared, cut to 100: the band moves up by the 300 that went.
    assert footer.get("transform") == "translate(0,-300)"


def test_a_full_size_division_moves_the_footer_nowhere():
    """Its crop point stands at the canvas height, so there is nothing to take up."""
    root = _document(rows=4)
    declared = {el.get("id") for el in root.iter() if el.get("id")}
    result = fill(
        FillSpec(
            root=root,
            image_type="probe",
            text={f"row_{n}_name": "a" for n in range(1, 5)},
            **row_crop_fields(declared, drawn=4, capacity=4),
        )
    )
    drawn = etree.fromstring(result.svg)
    assert length(drawn.get("height")) == 400
    assert drawn.xpath('//*[@id="footer_group"]')[0].get("transform") is None
    # Only the crop notice is asserted on: a font substitution notice depends on what the
    # host happens to have installed, and would fail this test on one machine in three.
    assert not [
        notice
        for notice in result.notices
        if notice.notice_kind == NOTICE_CROP_POINT_OFF_CANVAS
    ], "a template cropping at its canvas height is not off it"


def test_a_template_with_no_footer_group_crops_exactly_as_it_always_did():
    root = _document(rows=4, footer=False)
    drawn = _rendered(root)
    assert length(drawn.get("height")) == 100
    assert not drawn.xpath('//*[@id="footer_group"]')


def test_a_footer_group_the_template_does_not_declare_is_reported():
    root = _document(rows=4, footer=False)
    declared = {el.get("id") for el in root.iter() if el.get("id")}
    result = fill(
        FillSpec(
            root=root,
            image_type="probe",
            text={"row_1_name": "a"},
            crop="row_1_vertical_crop_point",
            footer="footer_group",
            **{k: v for k, v in row_crop_fields(declared, drawn=1, capacity=4).items()
               if k == "crop_is_final"},
        )
    )
    assert any("footer_group" in reason for reason in result.unresolved)


# ── The translation itself ────────────────────────────────────────────────


def test_a_translation_is_prepended_to_a_transform_the_template_already_had():
    """Replacing it would move a band its editor had already placed sideways."""
    element = etree.fromstring(b'<g transform="translate(12,0) scale(2)"/>')
    _translate_y(element, -30)
    assert element.get("transform") == "translate(0,-30) translate(12,0) scale(2)"


def test_a_translation_onto_a_bare_element_leaves_no_stray_space():
    element = etree.fromstring(b"<g/>")
    _translate_y(element, -30)
    assert element.get("transform") == "translate(0,-30)"


# ── The shipped templates ─────────────────────────────────────────────────


@pytest.mark.parametrize("name,rows", sorted(ROW_TEMPLATES.items()))
def test_every_row_template_declares_a_contiguous_run_of_crop_points(name, rows):
    root = etree.parse(str(TEMPLATES / name)).getroot()
    found = {
        int(m.group(1))
        for el in root.iter()
        if el.get("id")
        for m in [re.match(r"^row_(\d+)_vertical_crop_point$", el.get("id"))]
        if m
    }
    assert found == set(range(1, rows + 1))


@pytest.mark.parametrize("name,rows", sorted(ROW_TEMPLATES.items()))
def test_the_last_row_s_crop_point_stands_at_the_declared_canvas_height(name, rows):
    """FR-026 — otherwise a full-size division is drawn short, and said to be."""
    root = etree.parse(str(TEMPLATES / name)).getroot()
    point = root.xpath(f'//*[@id="row_{rows}_vertical_crop_point"]')[0]
    assert length(point.get("y")) == length(root.get("height"))


@pytest.mark.parametrize("name,rows", sorted(ROW_TEMPLATES.items()))
def test_every_row_template_carries_its_caption_band_in_a_footer_group(name, rows):
    """Without it the crop would cut the captions off the bottom of every short graphic."""
    root = etree.parse(str(TEMPLATES / name)).getroot()
    footer = root.xpath('//*[@id="footer_group"]')
    assert footer, f"{name} declares no footer_group"
    captions = [
        el
        for el in footer[0].iter()
        if el.get("class") == "foot"
    ]
    assert captions, f"{name}'s footer_group holds no caption"
    assert not [
        el
        for el in root.iter()
        if el.get("class") == "foot" and el not in set(footer[0].iter())
    ], f"{name} draws a caption outside its footer_group, which the crop would cut off"
