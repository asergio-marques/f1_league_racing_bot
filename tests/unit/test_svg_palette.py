"""Colour slots: what a template demands, and what injecting a palette writes.

The mechanism rests on two behaviours of the rasteriser that are not obvious and were
measured rather than assumed: a later stylesheet rule beats an earlier one *and* beats a
presentation attribute, and a class rule does **not** reach a gradient `<stop>`. The first
is why the palette is a stylesheet at all; the second is why stops are painted inline. Both
are pinned by `test_svg_palette_rasterised.py`, which needs Inkscape; everything here is
about the tree and needs nothing.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from utils.svg_document import SVG_NS, parse_svg_bytes, stylesheet  # noqa: E402
from utils.svg_palette import (  # noqa: E402
    InvalidSlot,
    apply_palette,
    colour_slots,
    declared_pairs,
    normalise_slot,
)


def _svg(body: str, *, defs: str = "") -> etree._Element:
    return parse_svg_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
        f"{defs}{body}</svg>".encode()
    )


def _injected_rules(root: etree._Element) -> dict[str, dict[str, str]]:
    return stylesheet(root)


# ── What a template demands ───────────────────────────────────────────────

def test_a_template_marking_nothing_demands_nothing():
    assert colour_slots(_svg('<rect class="accent" fill="#3DD6F5"/>')) == frozenset()


def test_each_prefix_is_recognised():
    root = _svg(
        '<rect class="colour-fill-accent"/>'
        '<path class="colour-stroke-rule"/>'
        '<stop class="colour-stop-wash"/>'
    )
    assert colour_slots(root) == {"accent", "rule", "wash"}
    assert declared_pairs(root) == {
        ("fill", "accent"), ("stroke", "rule"), ("stop", "wash"),
    }


def test_a_slot_is_found_beside_the_templates_own_classes():
    root = _svg('<text class="title colour-fill-accent mono">x</text>')
    assert colour_slots(root) == {"accent"}


def test_the_scan_reaches_into_defs():
    """A gradient's stops live in `<defs>`, which `FieldIndex` skips and this must not.

    A scan that skipped it would report a template complete while a slot it genuinely
    demands went unconfigured.
    """
    root = _svg(
        "<rect/>",
        defs='<linearGradient id="g"><stop class="colour-stop-wash"/></linearGradient>',
    )
    assert colour_slots(root) == {"wash"}


def test_a_slot_id_may_contain_hyphens_and_is_matched_whole():
    """`accent-dim` is one slot, not `accent` with something after it.

    The class attribute is split and each token matched entire for exactly this reason: a
    substring search finds `colour-fill-accent` inside `colour-fill-accent-dim` and paints
    the wrong slot.
    """
    root = _svg('<rect class="colour-fill-accent-dim"/>')
    assert colour_slots(root) == {"accent-dim"}


def test_a_class_that_merely_looks_like_a_slot_is_ignored():
    root = _svg('<rect class="colour-background-accent not-colour-fill-accent"/>')
    assert colour_slots(root) == frozenset()


# ── Injection ─────────────────────────────────────────────────────────────

def test_a_fill_slot_becomes_a_rule():
    root = _svg('<rect class="colour-fill-accent" fill="#3DD6F5"/>')
    apply_palette(root, {"accent": "#A78BFA"})
    assert _injected_rules(root)[".colour-fill-accent"] == {"fill": "#A78BFA"}


def test_a_stroke_slot_paints_stroke_and_not_fill():
    root = _svg('<path class="colour-stroke-rule"/>')
    apply_palette(root, {"rule": "#A78BFA"})
    assert _injected_rules(root)[".colour-stroke-rule"] == {"stroke": "#A78BFA"}


def test_the_injected_block_comes_after_the_templates_own():
    """Document order is the whole mechanism: equal specificity, so the last rule wins."""
    root = _svg(
        '<rect class="accent colour-fill-accent"/>',
        defs="<style>.accent { fill:#3DD6F5 }</style>",
    )
    apply_palette(root, {"accent": "#A78BFA"})
    styles = list(root.iter(f"{{{SVG_NS}}}style"))
    assert len(styles) == 2
    assert "#3DD6F5" in styles[0].text
    assert "#A78BFA" in styles[1].text


def test_defs_is_created_when_the_template_has_none():
    root = _svg('<rect class="colour-fill-accent"/>')
    apply_palette(root, {"accent": "#A78BFA"})
    defs = root.findall(f"{{{SVG_NS}}}defs")
    assert len(defs) == 1
    assert list(defs[0]) and defs[0][0].tag == f"{{{SVG_NS}}}style"


def test_an_existing_defs_is_reused_rather_than_a_second_one_added():
    root = _svg('<rect class="colour-fill-accent"/>', defs="<defs><style>.a{fill:red}</style></defs>")
    apply_palette(root, {"accent": "#A78BFA"})
    assert len(root.findall(f"{{{SVG_NS}}}defs")) == 1


def test_a_slot_the_palette_does_not_carry_is_left_at_the_templates_colour():
    root = _svg('<rect class="colour-fill-accent"/><rect class="colour-fill-wash"/>')
    apply_palette(root, {"accent": "#A78BFA"})
    rules = _injected_rules(root)
    assert ".colour-fill-accent" in rules
    assert ".colour-fill-wash" not in rules


def test_a_configured_slot_no_template_declares_writes_nothing():
    root = _svg('<rect class="accent"/>')
    apply_palette(root, {"accent": "#A78BFA"})
    assert not list(root.iter(f"{{{SVG_NS}}}style"))


def test_an_empty_palette_leaves_the_tree_untouched():
    before = etree.tostring(_svg('<rect class="colour-fill-accent"/>'))
    root = _svg('<rect class="colour-fill-accent"/>')
    apply_palette(root, {})
    assert etree.tostring(root) == before


def test_a_slot_used_only_for_fill_gets_no_stroke_rule():
    """Only the pairs a template actually declares are emitted, not every property."""
    root = _svg('<rect class="colour-fill-accent"/>')
    apply_palette(root, {"accent": "#A78BFA"})
    assert set(_injected_rules(root)) == {".colour-fill-accent"}


# ── Gradient stops ────────────────────────────────────────────────────────

def test_a_stop_is_painted_inline_and_not_by_a_rule():
    """Inkscape does not apply a class rule to a `<stop>`; an inline style it does apply."""
    root = _svg(
        "<rect/>",
        defs='<linearGradient id="g">'
             '<stop offset="0" class="colour-stop-wash" stop-color="#3DD6F5"/>'
             "</linearGradient>",
    )
    apply_palette(root, {"wash": "#A78BFA"})
    stop = root.iter(f"{{{SVG_NS}}}stop").__next__()
    assert "stop-color:#A78BFA" in stop.get("style")
    assert ".colour-stop-wash" not in _injected_rules(root)


def test_a_stop_keeps_the_other_declarations_it_carried():
    root = _svg(
        "<rect/>",
        defs='<linearGradient id="g">'
             '<stop class="colour-stop-wash" style="stop-opacity:0.5"/></linearGradient>',
    )
    apply_palette(root, {"wash": "#A78BFA"})
    style = root.iter(f"{{{SVG_NS}}}stop").__next__().get("style")
    assert "stop-opacity:0.5" in style and "stop-color:#A78BFA" in style


def test_a_template_with_only_stops_gains_no_stylesheet():
    root = _svg(
        "<rect/>",
        defs='<linearGradient id="g"><stop class="colour-stop-wash"/></linearGradient>',
    )
    apply_palette(root, {"wash": "#A78BFA"})
    assert not list(root.iter(f"{{{SVG_NS}}}style"))


# ── Slot validation: the one place league input reaches a stylesheet ──────

@pytest.mark.parametrize(
    "value",
    [
        "a { } body { fill:red }",      # closes the rule and opens another
        "accent}",
        "accent{fill:red}",
        "accent;fill:red",
        "accent /* comment */",
        "accent accent",
        "",
        "   ",
        None,
        "a" * 65,
        "Ünicode",
        "colour<script>",
    ],
)
def test_a_slot_that_could_escape_a_selector_is_refused(value):
    with pytest.raises(InvalidSlot):
        normalise_slot(value)


@pytest.mark.parametrize("value", ["accent", "accent-dim", "primary_color", "tier2", "a", "a" * 64])
def test_a_usable_slot_is_accepted(value):
    assert normalise_slot(value) == value


def test_a_slot_is_lower_cased_so_one_spelling_is_one_slot():
    assert normalise_slot("Accent") == "accent"
    assert normalise_slot("  ACCENT  ") == "accent"


def test_the_injected_rules_contain_only_what_was_asked_for():
    """A belt-and-braces check that a validated slot cannot carry a brace into the sheet."""
    root = _svg('<rect class="colour-fill-accent"/>')
    apply_palette(root, {normalise_slot("Accent"): "#A78BFA"})
    text = list(root.iter(f"{{{SVG_NS}}}style"))[0].text
    assert text.count("{") == 1 and text.count("}") == 1
