"""Unit tests for SVG loading, canvas reading and style resolution — T046.

The style cascade is needed twice: by the fastest-lap contrast check (FR-026a), and by
the recolour operation, which Constitution XIV.2 requires be merged into *inline* style
precisely because a presentation attribute loses to the template's own stylesheet. This
file pins that ordering down.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from utils.svg_document import (  # noqa: E402
    SvgNoCanvasError,
    SvgParseError,
    canvas_of,
    computed_style,
    declarations,
    FieldIndex,
    length,
    load_svg,
    parse_svg_bytes,
    stylesheet,
)


# ── Canvas (Constitution XIV.1) ───────────────────────────────────────────


@pytest.mark.parametrize(
    "attrs,expected",
    [
        ('width="1200" height="675"', (1200, 675)),
        ('width="1200px" height="675px"', (1200, 675)),
        ('width="1200" height="1212"', (1200, 1212)),
        ('viewBox="0 0 800 600"', (800, 600)),
        ('viewBox="0,0,800,600"', (800, 600)),
        ('width="100%" height="100%" viewBox="0 0 640 480"', (640, 480)),
    ],
)
def test_canvas_is_read_from_the_root(attrs, expected):
    root = parse_svg_bytes(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}></svg>'.encode())
    assert canvas_of(root) == expected


def test_two_templates_declaring_different_sizes_each_keep_their_own():
    """No fixed canvas is assumed for any image type."""
    a = parse_svg_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="675"/>')
    b = parse_svg_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1400"/>')
    assert canvas_of(a) == (1200, 675)
    assert canvas_of(b) == (900, 1400)


def test_physical_units_convert_at_the_css_reference():
    root = parse_svg_bytes(b'<svg xmlns="http://www.w3.org/2000/svg" width="1in" height="2in"/>')
    assert canvas_of(root) == (96, 192)


def test_missing_canvas_raises():
    root = parse_svg_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
    with pytest.raises(SvgNoCanvasError):
        canvas_of(root)


def test_zero_and_negative_sizes_are_not_a_canvas():
    for attrs in ('width="0" height="675"', 'width="-10" height="675"'):
        root = parse_svg_bytes(f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}/>'.encode())
        with pytest.raises(SvgNoCanvasError):
            canvas_of(root)


def test_length_parsing():
    assert length("12") == 12.0
    assert length("12px") == 12.0
    assert length("12.5") == 12.5
    assert length(None) is None
    assert length("50%") is None
    assert length("auto") is None


# ── Parsing ───────────────────────────────────────────────────────────────


def test_junk_does_not_parse():
    with pytest.raises(SvgParseError):
        parse_svg_bytes(b"this is not markup")


def test_truncated_markup_does_not_parse():
    with pytest.raises(SvgParseError):
        parse_svg_bytes(b'<svg xmlns="http://www.w3.org/2000/svg"><g></svg>')


def test_non_svg_root_is_rejected(tmp_path):
    path = tmp_path / "x.svg"
    path.write_bytes(b"<html><body/></html>")
    with pytest.raises(SvgParseError):
        load_svg(path)


def test_field_index_maps_every_addressable_element():
    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        b'<g id="row_1"><text id="driver_1">x</text></g><rect/></svg>'
    )
    index = FieldIndex(root).by_id
    assert set(index) == {"row_1", "driver_1"}


# ── T046: the style cascade ───────────────────────────────────────────────


def _doc(body: str):
    return parse_svg_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">{body}</svg>'.encode()
    )


def test_declarations_parsing():
    assert declarations("fill:#fff; font-size:12px") == {
        "fill": "#fff",
        "font-size": "12px",
    }
    assert declarations(None) == {}
    assert declarations("") == {}


def test_presentation_attribute_is_resolved():
    root = _doc('<rect id="plate" fill="#111111"/>')
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#111111"


def test_inline_style_is_resolved():
    root = _doc('<rect id="plate" style="fill:#222222"/>')
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#222222"


def test_stylesheet_rule_by_class_is_resolved():
    root = _doc('<style>.plate { fill: #333333; }</style><rect id="plate" class="plate"/>')
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#333333"


def test_a_comment_containing_a_comma_does_not_disable_the_rule_after_it():
    """A selector group is split on commas, so an unstripped comment swallows the next rule.

    Every shipped results, lineup, attendance and verdict template documents `.dname` in a
    comment directly above it, and each of those comments contains a comma. Until comments
    were stripped, `.dname` matched nothing at all and the driver-name bounds those templates
    declared were silently inert — names ran across the column beside them with no notice.
    """
    root = _doc(
        "<style>"
        "/*  a name is of no length the league controls, so it is bounded  */"
        ".plate { fill: #555555; inline-size: 262px }"
        "</style>"
        '<rect id="plate" class="plate"/>'
    )
    style = computed_style(FieldIndex(root).resolve("plate"), stylesheet(root))
    assert style["fill"] == "#555555"
    assert style["inline-size"] == "262px"


def test_a_multi_line_comment_between_rules_is_stripped():
    root = _doc(
        "<style>.a { fill: #010101 }\n"
        "/*  one, two,\n    three  */\n"
        ".b { fill: #020202 }</style>"
        '<rect id="one" class="a"/><rect id="two" class="b"/>'
    )
    rules = stylesheet(root)
    index = FieldIndex(root)
    assert computed_style(index.resolve("one"), rules)["fill"] == "#010101"
    assert computed_style(index.resolve("two"), rules)["fill"] == "#020202"


def test_stylesheet_rule_by_id_is_resolved():
    root = _doc('<style>#plate { fill: #444444; }</style><rect id="plate"/>')
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#444444"


def test_stylesheet_rule_by_element_name_is_resolved():
    root = _doc("<style>rect { fill: #555555; }</style><rect id=\"plate\"/>")
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#555555"


def test_stylesheet_beats_presentation_attribute():
    """This is *why* Constitution XIV.2 requires a recolour be written inline."""
    root = _doc('<style>#plate { fill: #333333; }</style><rect id="plate" fill="#111111"/>')
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#333333"


def test_inline_style_beats_the_stylesheet():
    root = _doc(
        '<style>#plate { fill: #333333; }</style>'
        '<rect id="plate" fill="#111111" style="fill:#222222"/>'
    )
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#222222"


def test_id_rule_beats_class_rule():
    root = _doc(
        '<style>.plate { fill: #333333; } #plate { fill: #444444; }</style>'
        '<rect id="plate" class="plate"/>'
    )
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#444444"


def test_selector_lists_are_indexed_per_selector():
    root = _doc('<style>.a, .b { fill: #666666; }</style><rect id="plate" class="b"/>')
    element = FieldIndex(root).resolve("plate")
    assert computed_style(element, stylesheet(root))["fill"] == "#666666"


def test_unrelated_declarations_survive_alongside_fill():
    root = _doc(
        '<rect id="plate" style="fill:#222222;font-variant-numeric:tabular-nums"/>'
    )
    element = FieldIndex(root).resolve("plate")
    resolved = computed_style(element, stylesheet(root))
    assert resolved["fill"] == "#222222"
    assert resolved["font-variant-numeric"] == "tabular-nums"
