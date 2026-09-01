"""Unit tests for SVG loading, canvas reading and style resolution — T046.

The style cascade is needed twice: by the fastest-lap contrast check (FR-026a), and by
the recolour operation, which Constitution XIV.2 requires be merged into *inline* style
precisely because a presentation attribute loses to the template's own stylesheet. This
file pins that ordering down.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

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


# ── Inheritance ───────────────────────────────────────────────────────────
#
# `font-family` inherits in SVG, and every shipped template declares it once on the root
# `<svg>`. Until the ancestor walk existed those declarations reached nothing: each field
# resolved to no family at all, `resolve_family` reported a `sans-serif` nobody had asked
# for, and the module measured against a face Inkscape had not been told to draw.


def _family_of(root, field_id: str) -> str | None:
    return computed_style(FieldIndex(root).resolve(field_id), stylesheet(root)).get(
        "font-family"
    )


def test_font_family_inherits_from_the_root_svg():
    """The route every shipped template takes."""
    root = parse_svg_bytes(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        'font-family="Inter"><text id="name">x</text></svg>'.encode()
    )
    assert _family_of(root, "name") == "Inter"


def test_font_family_inherits_from_an_intermediate_group():
    """A `<g>` is as legitimate a place to declare it as the root, and groups are how a
    league re-authoring a template would set one section's face apart from another."""
    root = _doc('<g font-family="Inter"><text id="name">x</text></g>')
    assert _family_of(root, "name") == "Inter"


def test_font_family_inherits_from_a_stylesheet_rule_on_an_ancestor():
    root = _doc('<style>svg { font-family: Inter }</style><text id="name">x</text>')
    assert _family_of(root, "name") == "Inter"


def test_an_elements_own_family_beats_an_inherited_one():
    root = parse_svg_bytes(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        'font-family="Inter"><text id="name" font-family="Georgia">x</text></svg>'.encode()
    )
    assert _family_of(root, "name") == "Georgia"


def test_the_nearest_ancestor_wins():
    root = parse_svg_bytes(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        'font-family="Inter"><g font-family="Georgia">'
        '<text id="name">x</text></g></svg>'.encode()
    )
    assert _family_of(root, "name") == "Georgia"


def test_the_whole_declared_stack_is_inherited_not_just_its_first_family():
    """The stack *is* the fallback mechanism, and Inkscape walks all of it — so the module
    has to receive all of it, or the two disagree about the face from the second entry on.
    """
    root = parse_svg_bytes(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
        "font-family=\"Inter, 'Segoe UI', 'DejaVu Sans', sans-serif\">"
        '<text id="name">x</text></svg>'.encode()
    )
    assert _family_of(root, "name") == "Inter, 'Segoe UI', 'DejaVu Sans', sans-serif"


def test_opacity_does_not_inherit():
    """A group's opacity composites its subtree once. Inheriting it would fade every
    child by the group's factor a second time."""
    root = _doc('<g opacity="0.5"><rect id="plate"/></g>')
    style = computed_style(FieldIndex(root).resolve("plate"), stylesheet(root))
    assert "opacity" not in style


def test_a_fields_own_bounds_do_not_inherit():
    """`inline-size`, `shape-inside` and `max-lines` bound one field's box. A group
    declaring any of them must not re-bound every field beneath it."""
    root = _doc(
        "<style>g { inline-size: 200px; shape-inside: url(#box); max-lines: 2 }</style>"
        '<g><text id="name">x</text></g>'
    )
    style = computed_style(FieldIndex(root).resolve("name"), stylesheet(root))
    assert "inline-size" not in style
    assert "shape-inside" not in style
    assert "max-lines" not in style


#: The shipped templates, chosen deterministically rather than in directory order so the
#: parametrisation reads the same on every host (CLAUDE.md, 2026-08-26).
_SHIPPED_TEMPLATES = sorted(
    (Path(__file__).resolve().parents[2] / "resources" / "defaults" / "templates").glob(
        "*.svg"
    )
)


def test_every_shipped_template_is_found_to_have_templates():
    """Guards the guard: an empty glob would make the test below vacuously true."""
    assert len(_SHIPPED_TEMPLATES) == 15


@pytest.mark.parametrize("template", _SHIPPED_TEMPLATES, ids=lambda t: t.stem)
def test_every_text_field_in_a_shipped_template_receives_a_font_family(template):
    """Each template states its stack once on the root `<svg>`, so this fails for all
    fifteen at once if the ancestor walk is lost.

    It asserts on the declaration the field *receives*, never on the face a host resolves
    it to: which of Inter, Segoe UI or DejaVu Sans is installed differs across the Pi, CI
    and a Windows desktop, and an assertion on that would pass only where its author sat.
    """
    root = load_svg(str(template))
    rules = stylesheet(root)
    texts = list(root.iter("{http://www.w3.org/2000/svg}text"))
    assert texts, f"{template.name} declares no text at all"

    without = [
        t.get("id") or "<unnamed>"
        for t in texts
        if not computed_style(t, rules).get("font-family")
    ]
    assert not without, (
        f"{template.name}: {len(without)} text element(s) resolve to no font-family, "
        f"so the module would measure them against a face the rasteriser was never "
        f"told to draw — {sorted(set(without))[:5]}"
    )


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


# ── What a <defs> holds is never a field ──────────────────────────────────


def _with_defs() -> "object":
    return parse_svg_bytes(b"""<svg xmlns="http://www.w3.org/2000/svg"
         xmlns:inkscape="http://www.inkscape.org/namespaces/inkscape"
         width="100" height="100">
      <defs>
        <linearGradient id="highlightFastestLapGradient">
          <stop id="firstStop" offset="0" stop-color="#A020F0"/>
        </linearGradient>
        <clipPath id="someClip"><rect id="clipRect" width="10" height="10"/></clipPath>
      </defs>
      <rect id="row_1_feature_race_background" width="10" height="10"/>
    </svg>""")


def test_a_paint_server_is_not_declared_as_a_field():
    """A gradient must carry an id to be referenced, and is still not a field.

    `declared()` is what a catalogue checks a template against, so an indexed gradient
    would be reported as an id the catalogue cannot name — and a template would be refused
    for owning the very gradient its own stylesheet paints with.
    """
    root = _with_defs()
    index = FieldIndex(root)
    assert "row_1_feature_race_background" in index.declared()
    assert index.declared().isdisjoint(
        {"highlightFastestLapGradient", "firstStop", "someClip", "clipRect"}
    )


def test_a_paint_server_cannot_be_resolved_as_a_field_either():
    """Excluded from the index outright, not merely absent from `declared()`."""
    root = _with_defs()
    index = FieldIndex(root)
    assert index.resolve("highlightFastestLapGradient") is None
    assert "highlightFastestLapGradient" not in index
    assert index.resolve("row_1_feature_race_background") is not None
