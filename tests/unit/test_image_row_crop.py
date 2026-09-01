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
from utils.svg_fill import FillSpec, _path_rule, _translate_y, _translate_y_of, fill

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


# ── Rules ruled down the rows ─────────────────────────────────────────────
#
# A standings grid and an attendance sheet separate their round columns with a line ruled
# from the headings to just above the caption band. Cropping the canvas alone left it running
# to the new edge and straight through the band the crop had carried up.


def _ruled(**shapes):
    """A four-row template carrying whatever shapes a test names, keyed by id.

    Each value is the attributes of a `line`, or of a `rect` where it declares a height.
    """
    root = _document(rows=4)
    for element_id, attributes in shapes.items():
        tag = "rect" if "height" in attributes else "line"
        node = etree.SubElement(root, tag)
        node.set("id", element_id)
        for name, value in attributes.items():
            node.set(name, str(value))
    return root


def _shape(drawn, element_id):
    return drawn.xpath(f'//*[@id="{element_id}"]')[0]


def test_a_rule_down_the_rows_keeps_the_distance_it_held_from_the_bottom():
    """The whole of the fault: it was authored to stop above the band, not at the edge."""
    root = _ruled(separator={"x1": 50, "y1": 20, "x2": 50, "y2": 380})
    # 400 declared, cut to 100. The rule ended 20 above the bottom and must still.
    assert _shape(_rendered(root), "separator").get("y2") == "80"


def test_a_rule_that_reached_the_canvas_edge_reaches_the_new_one():
    """What cropping alone already did, so nothing that was right before changes."""
    root = _ruled(separator={"x1": 50, "y1": 20, "x2": 50, "y2": 400})
    assert _shape(_rendered(root), "separator").get("y2") == "100"


def test_a_rule_that_ends_above_the_cut_is_left_alone():
    root = _ruled(separator={"x1": 50, "y1": 20, "x2": 50, "y2": 60})
    assert _shape(_rendered(root), "separator").get("y2") == "60"


def test_a_rule_is_never_shortened_past_its_own_top():
    """A hair of a line is a mark on the picture; a negative one is a rendering fault."""
    root = _ruled(separator={"x1": 50, "y1": 90, "x2": 50, "y2": 380})
    assert _shape(_rendered(root), "separator").get("y2") == "90"


def test_a_rule_authored_upwards_is_shortened_at_its_lower_end():
    """y1 and y2 are ends, not an order: an editor writes them either way round."""
    root = _ruled(separator={"x1": 50, "y1": 380, "x2": 50, "y2": 20})
    shape = _shape(_rendered(root), "separator")
    assert (shape.get("y1"), shape.get("y2")) == ("80", "20")


def test_a_band_spanning_the_cut_loses_exactly_what_the_crop_removed():
    root = _ruled(band={"x": 0, "y": 40, "width": 10, "height": 340})
    assert _shape(_rendered(root), "band").get("height") == "40"


def test_a_transformed_subtree_is_left_exactly_as_it_is():
    """Under a scale or a rotate the element's own y is not a canvas y at all."""
    root = _document(rows=4)
    group = etree.SubElement(root, "g")
    group.set("transform", "scale(2)")
    line = etree.SubElement(group, "line")
    line.set("id", "separator")
    for name, value in (("x1", "50"), ("y1", "20"), ("x2", "50"), ("y2", "380")):
        line.set(name, value)
    assert _shape(_rendered(root), "separator").get("y2") == "380"


def test_a_rule_inside_the_footer_rides_up_and_is_not_shortened_as_well():
    """The band moves whole. Shortening a rule within it would take the move twice."""
    root = _document(rows=4)
    footer = root.xpath('//*[@id="footer_group"]')[0]
    line = etree.SubElement(footer, "line")
    line.set("id", "footer_rule")
    for name, value in (("x1", "0"), ("y1", "20"), ("x2", "200"), ("y2", "395")):
        line.set(name, value)

    drawn = _rendered(root)

    assert _shape(drawn, "footer_rule").get("y2") == "395"
    assert drawn.xpath('//*[@id="footer_group"]')[0].get("transform") == "translate(0,-300)"


def test_a_full_size_division_shortens_nothing():
    """There is nothing to take up, so every rule stands where it was authored."""
    root = _ruled(separator={"x1": 50, "y1": 20, "x2": 50, "y2": 380})
    declared = {el.get("id") for el in root.iter() if el.get("id")}
    result = fill(
        FillSpec(
            root=root,
            image_type="probe",
            text={f"row_{n}_name": "a" for n in range(1, 5)},
            **row_crop_fields(declared, drawn=4, capacity=4),
        )
    )
    assert _shape(etree.fromstring(result.svg), "separator").get("y2") == "380"


def test_a_rule_inside_a_positioned_layer_is_followed_into():
    """An editor puts its artwork in a translated layer as a matter of course."""
    root = _document(rows=4)
    layer = etree.SubElement(root, "g")
    layer.set("transform", "translate(0,10)")
    line = etree.SubElement(layer, "line")
    line.set("id", "separator")
    for name, value in (("x1", "50"), ("y1", "10"), ("x2", "50"), ("y2", "370")):
        line.set(name, value)

    # Drawn at 380 on the canvas, 20 above the bottom, so it ends 20 above the new one —
    # y=80 in the layer's own coordinates.
    assert _shape(_rendered(root), "separator").get("y2") == "70"


def test_a_rule_drawn_with_a_pen_is_shortened_like_one_drawn_with_a_line():
    """The commonest export of all: a straight rule written as a path."""
    root = _ruled()
    path = etree.SubElement(root, "path")
    path.set("id", "separator")
    path.set("d", "M50 20V380")

    assert _shape(_rendered(root), "separator").get("d") == "M50 20V80"


def test_a_path_that_draws_more_than_one_straight_rule_is_left_alone():
    """Its geometry is its own; a lower end cannot be moved without reading the shape."""
    root = _ruled()
    for element_id, d in (
        ("curve", "M50 20C50 100 50 300 50 380"),
        ("diagonal", "M50 20L120 380"),
        ("two_segments", "M50 20V380L120 380"),
    ):
        path = etree.SubElement(root, "path")
        path.set("id", element_id)
        path.set("d", d)

    drawn = _rendered(root)

    assert _shape(drawn, "curve").get("d") == "M50 20C50 100 50 300 50 380"
    assert _shape(drawn, "diagonal").get("d") == "M50 20L120 380"
    assert _shape(drawn, "two_segments").get("d") == "M50 20V380L120 380"


@pytest.mark.parametrize(
    "d,shortened",
    [
        ("M50 20V380", "M50 20V80"),
        ("m50 20v360", "M50 20v60"),
        ("M50 20L50 380", "M50 20L50 80"),
        ("M50 20l0 360", "M50 20l0 60"),
        # Drawn upwards: the move is the lower end, so the move is what comes up.
        ("M50 380V20", "M50 80V20"),
        ("M50 380l0 -360", "M50 80l0 -60"),
    ],
)
def test_a_rule_is_written_back_in_the_command_it_was_authored_in(d, shortened):
    """A file keeps the shape its author gave it, absolute or relative, either way up."""
    rule = _path_rule(d)
    assert rule is not None
    top, bottom, rewrite = rule
    assert (top, bottom) == (20, 380)
    assert rewrite(80.0) == shortened


@pytest.mark.parametrize(
    "transform,offset",
    [
        (None, 0),
        ("", 0),
        ("translate(0,10)", 10),
        ("translate(5)", 0),                      # along x alone
        ("translate(0,-30) translate(12,0)", -30),  # what _translate_y prepends
        ("scale(2)", None),
        ("translate(0,10) scale(2)", None),
        ("matrix(1,0,0,1,0,10)", None),
        ("rotate(90) ", None),
    ],
)
def test_only_a_transform_that_purely_translates_is_followed(transform, offset):
    """Under anything else the element's own y is not a canvas y, and nothing can be read."""
    assert _translate_y_of(transform) == offset


def test_the_geometry_of_a_clip_is_not_shortened_with_the_canvas():
    """A shape inside a definition is borrowed geometry, not something drawn where it stands.

    An editor exports a frame as a clip over the whole canvas. Shortening its rectangle would
    cut the content it frames — the opposite of what a shorter canvas asks for — and every
    template a league draws in Figma or Illustrator carries one.
    """
    root = _document(rows=4)
    defs = etree.SubElement(root, "defs")
    clip = etree.SubElement(defs, "clipPath")
    clip.set("id", "frame")
    shape = etree.SubElement(clip, "rect")
    shape.set("id", "frame_shape")
    for name, value in (("x", "0"), ("y", "0"), ("width", "200"), ("height", "400")):
        shape.set(name, value)

    drawn = _rendered(root)

    assert _shape(drawn, "frame_shape").get("height") == "400"


@pytest.mark.parametrize(
    "name,rows",
    sorted((n, r) for n, r in ROW_TEMPLATES.items() if n.startswith(("standings", "attendance"))),
)
def test_a_shipped_grid_keeps_its_separators_clear_of_the_caption_band(name, rows):
    """The templates the fault was found on, drawn at two fifths of their rows."""
    root = etree.parse(str(TEMPLATES / name)).getroot()
    canvas = length(root.get("height"))
    authored = {
        length(line.get("y2"))
        for line in root.iter("{http://www.w3.org/2000/svg}line")
        if length(line.get("y1")) is not None
        and length(line.get("y2")) is not None
        and length(line.get("y2")) - length(line.get("y1")) > canvas / 4
    }
    assert authored, f"{name} rules no column separator to follow the crop"
    margin = canvas - max(authored)

    drawn = rows * 2 // 5
    declared = {el.get("id") for el in root.iter() if el.get("id")}
    result = fill(
        FillSpec(
            root=root,
            image_type="probe",
            **row_crop_fields(declared, drawn=drawn, capacity=rows),
        )
    )
    finished = etree.fromstring(result.svg)
    height = length(finished.get("height"))
    assert height < canvas, "the probe must actually shorten the template"

    ends = {
        length(line.get("y2"))
        for line in finished.iter("{http://www.w3.org/2000/svg}line")
        if length(line.get("y1")) is not None
        and length(line.get("y2")) is not None
        and length(line.get("y2")) - length(line.get("y1")) > height / 4
    }
    assert ends and max(ends) == height - margin


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
