"""Crop arithmetic for the calendar, verified on the rasterised PNG — T011.

Constitution XIV.14: a render is verified as a PNG, never as an SVG in a browser. That is
not ceremony here — a crop that rewrites the root ``height`` but not the ``viewBox``
passes an attribute assertion and fails a pixel one, which is exactly the bug this file
exists to catch (SC-004).
"""
from __future__ import annotations

import os
import struct
import sys
from datetime import datetime, timezone
from types import SimpleNamespace as NS

import pytest
from lxml import etree

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_calendar_service import build_fill_spec, resolve_drawing
from services.image_render_service import converter_available, rasterise
from utils.svg_document import canvas_of
from utils.svg_fill import fill

SVG_NS = "http://www.w3.org/2000/svg"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"

TRACKS = {
    "Silverstone Circuit": NS(
        name="Silverstone Circuit", gp_name="British Grand Prix", country="United Kingdom"
    )
}

SUFFIXES = (
    "number",
    "country_name",
    "race_name",
    "track_name",
    "format",
    "date",
    "time",
    "vertical_crop_point",
)

#: Rounds stacked 100px apart, so round N's crop point sits at y = 100 * N and the
#: template's declared height equals the last round's crop point (as FR-026 expects).
ROUND_HEIGHT = 100
TEMPLATE_ROUNDS = 6


def _template(count: int = TEMPLATE_ROUNDS, *, final_crop: float | None = None):
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "400")
    root.set("height", str(ROUND_HEIGHT * count))
    # Inkscape writes a viewBox on every document it saves, so a realistic template has
    # one — and it is the half of the cut that a height-only rewrite would leave behind,
    # scaling the drawing instead of cropping it.
    root.set("viewBox", f"0 0 400 {ROUND_HEIGHT * count}")
    node = etree.SubElement(root, f"{{{SVG_NS}}}text")
    node.set("id", "division_name")
    node.set("y", "10")
    for index in range(1, count + 1):
        for suffix in SUFFIXES:
            child = etree.SubElement(root, f"{{{SVG_NS}}}text")
            child.set("id", f"round_{index}_{suffix}")
            if suffix == "vertical_crop_point":
                y = ROUND_HEIGHT * index
                if final_crop is not None and index == count:
                    y = final_crop
                child.set("y", str(y))
            else:
                child.set("y", str(ROUND_HEIGHT * (index - 1) + 10))
    return root


def _rounds(count: int):
    return [
        NS(
            round_number=index,
            format="NORMAL",
            track_name="Silverstone Circuit",
            scheduled_at=datetime(2026, 6, 4, 20, 0, tzinfo=timezone.utc),
        )
        for index in range(1, count + 1)
    ]


def _filled(division_rounds: int, template_rounds: int = TEMPLATE_ROUNDS, **kwargs):
    root = _template(template_rounds, **kwargs)
    drawing = resolve_drawing(
        division_name="Elite",
        division_tier=1,
        season_number=1,
        rounds=_rounds(division_rounds),
        tracks=TRACKS,
    )
    result = fill(build_fill_spec(drawing, root))
    return root, result


def _png_height(path) -> int:
    """Read the IHDR height straight from the PNG, with no image library."""
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    return struct.unpack(">I", data[20:24])[0]


# ── The SVG-level arithmetic ──────────────────────────────────────────────


@pytest.mark.parametrize("held", range(1, TEMPLATE_ROUNDS + 1))
def test_canvas_is_cut_at_the_final_round_s_crop_point(held):
    """SC-004: N rounds against a template of M ≥ N cuts at round N's crop point."""
    root, _ = _filled(held)
    assert canvas_of(root)[1] == ROUND_HEIGHT * held


def test_viewbox_is_rewritten_with_the_height():
    """The trap: a height rewritten alone scales rather than crops."""
    root, _ = _filled(3)
    assert float(root.get("viewBox").split()[3]) == float(ROUND_HEIGHT * 3)


def test_width_is_untouched_by_the_cut():
    root, _ = _filled(2)
    assert canvas_of(root)[0] == 400


# ── FR-026: the final crop point off the declared canvas ──────────────────


def test_final_crop_point_off_the_canvas_raises_a_notice_and_still_cuts():
    root, result = _filled(
        TEMPLATE_ROUNDS, final_crop=ROUND_HEIGHT * TEMPLATE_ROUNDS - 40
    )
    kinds = {n.notice_kind for n in result.notices}
    assert "CROP_POINT_OFF_CANVAS" in kinds
    assert canvas_of(root)[1] == ROUND_HEIGHT * TEMPLATE_ROUNDS - 40
    assert not result.unresolved, "a misplaced crop point must not be fatal"


def test_a_short_division_never_raises_the_crop_notice():
    """Cropping above the canvas is the whole point when a division is smaller."""
    _, result = _filled(2)
    assert not any(n.notice_kind == "CROP_POINT_OFF_CANVAS" for n in result.notices)


# ── The same arithmetic, through the rasteriser (XIV.14) ──────────────────


def _abreast_template(pairs: int = 3):
    """A template laying rounds **two abreast**, as the shipped one does.

    This is the layout that creates the bug below: the odd round out stands *beside* the
    final drawn round, above the cut, so it leaves by its group rather than by the crop —
    and its removal punches a hole in the middle of the numbering.
    """
    root = etree.Element(f"{{{SVG_NS}}}svg")
    root.set("width", "800")
    root.set("height", str(ROUND_HEIGHT * pairs))
    root.set("viewBox", f"0 0 800 {ROUND_HEIGHT * pairs}")
    node = etree.SubElement(root, f"{{{SVG_NS}}}text")
    node.set("id", "division_name")
    node.set("y", "5")
    # `season_number` as the shipped template declares it: an Inkscape **layer** holding
    # no single <text> child. The fill descends into a layer to find its text, finds none,
    # and must then fall back on the field's classification — optional here, so a notice.
    # A plain <text> element would never reach that branch, and a fixture using one would
    # let the regression below pass while proving nothing.
    layer = etree.SubElement(root, f"{{{SVG_NS}}}g")
    layer.set(f"{{{INKSCAPE_NS}}}groupmode", "layer")
    layer.set(f"{{{INKSCAPE_NS}}}label", "season_number")
    etree.SubElement(layer, f"{{{SVG_NS}}}rect").set("y", "5")
    for index in range(1, pairs * 2 + 1):
        row = (index - 1) // 2 + 1          # 1,1,2,2,3,3 …
        group = etree.SubElement(root, f"{{{SVG_NS}}}g")
        group.set("id", f"round_{index}_group")
        for suffix in SUFFIXES:
            child = etree.SubElement(group, f"{{{SVG_NS}}}text")
            child.set("id", f"round_{index}_{suffix}")
            child.set(
                "y",
                str(ROUND_HEIGHT * row if suffix == "vertical_crop_point"
                    else ROUND_HEIGHT * (row - 1) + 10),
            )
    return root


def test_a_surplus_round_beside_the_final_one_leaves_by_its_group():
    """Precondition for the regression below — prove the middle removal really happens."""
    root = _abreast_template(3)          # six rounds, three rows of two
    drawing = resolve_drawing(
        division_name="Elite", division_tier=1, season_number=1,
        rounds=_rounds(3), tracks=TRACKS,
    )
    spec = build_fill_spec(drawing, root)
    assert spec.remove == ["round_4_group"], spec.remove


def test_removing_a_middle_member_does_not_look_like_a_gap():
    """Regression: the catalogue is a fact about the template *as authored*.

    Removing `round_4_group` takes round 4 out of the tree, so a later re-count sees
    1,2,3,5,6 and reads it as a gap in the numbering. Anything asking the catalogue after
    a removal must therefore have asked before it.

    Found by rendering the **shipped** template for an odd division size and reading the
    result (XIV.14): every optional field came back "unresolved" because the re-count
    raised. A stacked template cannot reproduce it — its surplus rounds fall below the cut
    and leave by the crop, so nothing is ever removed from the middle.
    """
    root = _abreast_template(3)
    drawing = resolve_drawing(
        division_name="Elite", division_tier=1, season_number=1,
        rounds=_rounds(3), tracks=TRACKS,
    )
    result = fill(build_fill_spec(drawing, root))
    assert not result.unresolved, result.unresolved


@pytest.mark.skipif(not converter_available(), reason="rasteriser not installed")
@pytest.mark.parametrize("held", [1, 3, TEMPLATE_ROUNDS])
def test_rasterised_png_height_matches_the_crop_point(held, tmp_path):
    root, _ = _filled(held)
    destination = tmp_path / f"calendar_{held}.png"
    rasterise(etree.tostring(root), destination, canvas_of(root))

    assert destination.exists() and destination.stat().st_size > 0
    assert _png_height(destination) == ROUND_HEIGHT * held
