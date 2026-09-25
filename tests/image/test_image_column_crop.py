"""Narrowing a sheet to the rounds a division actually runs (2026-09-02).

The attendance sheet and both standings sheets provide twelve round columns. A division
running eight was drawn on all twelve, the surplus four removed and their width left
standing empty beside the eighth — 440px of a 1728-wide standings, a quarter of the picture.
This is `test_image_row_crop` turned on its side, and the same cut.

The part most at risk from a refactor is the **carry**. The vertical crop lifts a named
footer group; there is no group to name to the right of a column band, because the sanction
column of an attendance sheet is a heading plus one cell inside every row group. So the carry
is decided by geometry, and the crop point holds two coordinates to decide it with: its `x`
is the boundary chrome is carried in from, its `x` plus its `width` is where the canvas edge
falls. Read only the one and the sanction column is sliced off; read only the other and it
stands where it was drawn while the plate beside it rides in without it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_catalogues import column_crop_fields  # noqa: E402
from models.image_constants import NOTICE_CROP_POINT_OFF_CANVAS  # noqa: E402
from utils.svg_document import length  # noqa: E402
from utils.svg_fill import FillSpec, _left_edge, _path_rule_x, fill  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates"
SVG = "http://www.w3.org/2000/svg"

#: Every shipped sheet that narrows itself, and the columns each declares.
COLUMN_TEMPLATES = {
    "attendance_template.svg": 12,
    "standings_drivers_template.svg": 12,
    "standings_constructors_template.svg": 12,
}

#: A sheet of four columns 100 wide from x=100, with a 40-wide margin and a rule ruled
#: across the whole of it. Column z's crop point spans from its right edge to the edge the
#: canvas would take were z the last column drawn.
FIRST, PITCH, BEYOND, COLUMNS = 100, 100, 40, 4
CANVAS = FIRST + COLUMNS * PITCH + BEYOND  # 540


def _document(*, drawn: int, beyond: int = BEYOND, aside: bool = False) -> etree._Element:
    canvas = FIRST + COLUMNS * PITCH + beyond
    columns = []
    for z in range(1, COLUMNS + 1):
        edge = FIRST + z * PITCH
        columns.append(
            f'<g id="round_{z}_group">'
            f'<line x1="{edge - PITCH}" y1="20" x2="{edge - PITCH}" y2="180"/>'
            f'<text id="round_{z}_number" x="{edge - PITCH / 2}" y="30">{z}</text>'
            f'<rect id="round_{z}_horizontal_crop_point" x="{edge}" y="20" '
            f'width="{beyond}" height="0" fill="none"/>'
            f"</g>"
        )
    # Chrome standing beside the columns: a divider and a heading, as the sanction column is.
    beside = (
        f'<line id="aside_rule" x1="{FIRST + COLUMNS * PITCH + 16}" y1="20" '
        f'x2="{FIRST + COLUMNS * PITCH + 16}" y2="180"/>'
        f'<text id="aside_head" x="{FIRST + COLUMNS * PITCH + 24}" y="30">ASIDE</text>'
    ) if aside else ""
    body = (
        f'<line id="across" x1="20" y1="10" x2="{canvas - 20}" y2="10"/>'
        f'<rect id="band" x="20" y="40" width="{canvas - 40}" height="20"/>'
        f'<path id="drawn_rule" d="M20 70H{canvas - 20}"/>'
        f'<text id="division_name" x="20" y="90">D</text>'
        + "".join(columns) + beside
    )
    root = etree.fromstring(
        f'<svg xmlns="{SVG}" width="{canvas}" height="200" '
        f'viewBox="0 0 {canvas} 200">{body}</svg>'.encode()
    )
    # Drop the columns the division does not run, as the projection does.
    for z in range(drawn + 1, COLUMNS + 1):
        node = root.xpath(f'//*[@id="round_{z}_group"]')[0]
        node.getparent().remove(node)
    return root


def _fill(root, *, drawn: int, capacity: int = COLUMNS, **extra):
    declared = {e.get("id") for e in root.iter() if e.get("id")}
    return fill(
        FillSpec(
            root=root,
            text={"division_name": "Division"},
            **column_crop_fields(declared, drawn=drawn, capacity=capacity),
            **extra,
        )
    )


def _x(root, field_id: str) -> float:
    """Where *field_id* is drawn, following the translate the carry left on it."""
    node = root.xpath(f'//*[@id="{field_id}"]')[0]
    own = _left_edge(node)
    shift = 0.0
    parent = node
    while parent is not None:
        transform = parent.get("transform") or ""
        if "translate(" in transform:
            shift += float(transform.split("translate(")[1].split(",")[0])
        parent = parent.getparent()
    return own + shift


# ── The cut itself ────────────────────────────────────────────────────────


@pytest.mark.parametrize("drawn,expected", [(4, 540), (3, 440), (2, 340), (1, 240)])
def test_the_canvas_is_cut_where_the_crop_point_ends(drawn, expected):
    root = _document(drawn=drawn)
    _fill(root, drawn=drawn)
    assert int(root.get("width")) == expected
    assert root.get("viewBox").split()[2] == str(expected)


def test_the_height_is_untouched_by_the_horizontal_cut():
    root = _document(drawn=2)
    _fill(root, drawn=2)
    assert root.get("height") == "200"
    assert root.get("viewBox").split()[3] == "200"


def test_a_season_as_long_as_the_template_draws_is_not_cut():
    root = _document(drawn=COLUMNS)
    _fill(root, drawn=COLUMNS)
    assert int(root.get("width")) == CANVAS
    assert _x(root, "across") == 20


def test_a_template_declaring_no_crop_point_keeps_its_width():
    root = _document(drawn=2)
    for node in root.xpath('//*[contains(@id, "_horizontal_crop_point")]'):
        node.getparent().remove(node)
    _fill(root, drawn=2)
    assert int(root.get("width")) == CANVAS


def test_a_sheet_drawing_no_column_at_all_is_not_cut():
    """There is no round_0 crop point, and cutting at round 1's leaves a seasonless sheet."""
    root = _document(drawn=0)
    assert column_crop_fields({"round_1_horizontal_crop_point"}, drawn=0, capacity=4) == {}
    _fill(root, drawn=0)
    assert int(root.get("width")) == CANVAS


# ── The carry ─────────────────────────────────────────────────────────────


def test_chrome_beside_the_columns_is_carried_in():
    """The sanction column's divider and heading, in miniature."""
    root = _document(drawn=2, aside=True)
    before_rule, before_head = _x(root, "aside_rule"), _x(root, "aside_head")
    _fill(root, drawn=2)
    delta = CANVAS - 340
    assert _x(root, "aside_rule") == before_rule - delta
    assert _x(root, "aside_head") == before_head - delta


def test_a_carried_element_keeps_its_distance_from_the_new_edge():
    root = _document(drawn=2, aside=True)
    _fill(root, drawn=2)
    width = int(root.get("width"))
    # 16 from the last column's edge before, and 16 from it after.
    assert _x(root, "aside_rule") == FIRST + 2 * PITCH + 16
    assert width - _x(root, "aside_head") == CANVAS - (FIRST + COLUMNS * PITCH + 24)


def test_a_column_left_of_the_boundary_does_not_move():
    root = _document(drawn=2, aside=True)
    before = _x(root, "round_1_number")
    _fill(root, drawn=2)
    assert _x(root, "round_1_number") == before


def test_nothing_is_carried_twice():
    """Only the outermost element of a carried subtree moves."""
    root = _document(drawn=2, aside=True)
    _fill(root, drawn=2)
    shifts = [
        e.get("transform") for e in root.iter()
        if isinstance(e.tag, str) and "translate(" in (e.get("transform") or "")
    ]
    assert all(shift.count("translate(") == 1 for shift in shifts)


# ── What spans the cut ────────────────────────────────────────────────────


def test_a_rule_ruled_across_the_sheet_keeps_its_margin():
    root = _document(drawn=2)
    _fill(root, drawn=2)
    width = int(root.get("width"))
    assert float(root.xpath('//*[@id="across"]')[0].get("x2")) == width - 20


def test_a_band_spanning_the_cut_is_narrowed_not_moved():
    root = _document(drawn=2)
    _fill(root, drawn=2)
    band = root.xpath('//*[@id="band"]')[0]
    assert float(band.get("x")) == 20
    assert float(band.get("x")) + float(band.get("width")) == int(root.get("width")) - 20


def test_a_path_drawing_one_horizontal_rule_is_pulled_in():
    root = _document(drawn=2)
    _fill(root, drawn=2)
    left, right, _ = _path_rule_x(root.xpath('//*[@id="drawn_rule"]')[0].get("d"))
    assert left == 20
    assert right == int(root.get("width")) - 20


def test_a_path_drawing_more_than_a_rule_is_left_alone():
    assert _path_rule_x("M20 70H200V180") is None
    assert _path_rule_x("M20 70C30 40 60 40 70 70") is None
    assert _path_rule_x("M20 70L200 180") is None, "a diagonal is not a rule across"


# ── The last declared column ──────────────────────────────────────────────


def test_the_last_columns_crop_point_off_the_canvas_is_reported():
    root = _document(drawn=COLUMNS, beyond=BEYOND)
    root.set("width", str(CANVAS + 60))
    root.set("viewBox", f"0 0 {CANVAS + 60} 200")
    result = _fill(root, drawn=COLUMNS)
    kinds = [n.notice_kind for n in result.notices]
    assert NOTICE_CROP_POINT_OFF_CANVAS in kinds


def test_a_shorter_season_cropping_further_in_reports_nothing():
    """It crops further in by design; only the last declared column may be off the canvas."""
    root = _document(drawn=2)
    result = _fill(root, drawn=2)
    assert NOTICE_CROP_POINT_OFF_CANVAS not in [n.notice_kind for n in result.notices]


# ── The shipped sheets ────────────────────────────────────────────────────


@pytest.mark.parametrize("filename,columns", sorted(COLUMN_TEMPLATES.items()))
def test_a_shipped_sheet_declares_a_crop_point_for_every_column(filename, columns):
    root = etree.parse(str(TEMPLATES / filename)).getroot()
    points = root.xpath('//*[contains(@id, "_horizontal_crop_point")]')
    assert len(points) == columns, filename


@pytest.mark.parametrize("filename,columns", sorted(COLUMN_TEMPLATES.items()))
def test_the_last_columns_crop_point_ends_at_the_declared_width(filename, columns):
    """So a division running as long a season as the sheet draws is drawn whole."""
    root = etree.parse(str(TEMPLATES / filename)).getroot()
    node = root.xpath(f'//*[@id="round_{columns}_horizontal_crop_point"]')[0]
    edge = length(node.get("x")) + length(node.get("width"))
    assert edge == length(root.get("width")), filename


@pytest.mark.parametrize("filename,columns", sorted(COLUMN_TEMPLATES.items()))
def test_a_shipped_crop_point_starts_at_its_own_columns_right_edge(filename, columns):
    """The boundary is the column band's edge, never the canvas edge.

    Reading the canvas edge as the boundary is what left the attendance sanction column
    behind, and it is invisible on the two standings sheets — nothing stands beside their
    columns, so the two x's differ only by the margin there.
    """
    root = etree.parse(str(TEMPLATES / filename)).getroot()
    edges = sorted(
        length(root.xpath(f'//*[@id="round_{z}_horizontal_crop_point"]')[0].get("x"))
        for z in range(1, columns + 1)
    )
    pitches = {round(b - a, 3) for a, b in zip(edges, edges[1:])}
    assert len(pitches) == 1, (filename, pitches)
    span = length(
        root.xpath('//*[@id="round_1_horizontal_crop_point"]')[0].get("width")
    )
    assert edges[0] + span < length(root.get("width")), filename
