"""What Inkscape actually does with an injected palette.

The palette design rests on four behaviours of the rasteriser that a reading of the SVG
specification does not settle, and that were measured before the module was written. They
are pinned here because they are the reason the mechanism has the shape it has, and because
a later maintainer looking at `svg_palette` would otherwise see only an odd-looking
stylesheet and a special case for `<stop>`:

1. a later rule of equal specificity wins — the cascade is the whole mechanism;
2. a class rule beats a `fill=` presentation attribute, which is what lets a template keep a
   literal default colour that Inkscape and a browser both show;
3. a class on a `<g>` reaches its children;
4. a class rule does **not** reach a `<stop>`, so stops must be painted inline.

Point 4 is the expensive one to rediscover: it fails silently, drawing the authored colour
with no error anywhere.

The tests rasterise and read pixels, so they carry the `rasteriser` marker and do not run in
CI — see CLAUDE.md. Run them by hand on a host with Inkscape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lxml import etree  # noqa: E402

from services.image_render_service import rasterise  # noqa: E402
from utils.svg_document import parse_svg_bytes  # noqa: E402
from utils.svg_palette import apply_palette  # noqa: E402

pytestmark = pytest.mark.rasteriser

TIER = "#A78BFA"
HOUSE = "#3DD6F5"


def _pixel(tmp_path, root: etree._Element, xy, name="out.png"):
    png = rasterise(
        etree.tostring(root.getroottree(), xml_declaration=True, encoding="utf-8"),
        tmp_path / name,
        (200, 100),
    )
    from PIL import Image

    with Image.open(png) as image:
        return image.convert("RGB").getpixel(xy)


def _hex(rgb) -> str:
    return "#%02X%02X%02X" % rgb


def _svg(body: str, *, defs: str = "") -> etree._Element:
    return parse_svg_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100" '
        f'viewBox="0 0 200 100">{defs}{body}</svg>'.encode()
    )


def test_the_injected_rule_beats_the_templates_own_stylesheet(tmp_path):
    root = _svg(
        f'<rect x="0" y="0" width="200" height="100" class="accent colour-fill-accent"/>',
        defs=f"<style>.accent {{ fill:{HOUSE} }}</style>",
    )
    apply_palette(root, {"accent": TIER})
    assert _hex(_pixel(tmp_path, root, (100, 50))) == TIER


def test_the_injected_rule_beats_a_presentation_attribute(tmp_path):
    """This is what lets a template stay a valid picture that shows its own colours."""
    root = _svg(
        f'<rect x="0" y="0" width="200" height="100" fill="{HOUSE}" class="colour-fill-accent"/>'
    )
    apply_palette(root, {"accent": TIER})
    assert _hex(_pixel(tmp_path, root, (100, 50))) == TIER


def test_a_slot_on_a_group_reaches_its_children(tmp_path):
    root = _svg(
        '<g class="colour-fill-accent">'
        '<rect x="0" y="0" width="200" height="100"/></g>'
    )
    apply_palette(root, {"accent": TIER})
    assert _hex(_pixel(tmp_path, root, (100, 50))) == TIER


def test_a_stroke_slot_paints_the_stroke(tmp_path):
    root = _svg(
        '<rect x="20" y="20" width="160" height="60" fill="none" stroke-width="20" '
        'class="colour-stroke-rule"/>'
    )
    apply_palette(root, {"rule": TIER})
    assert _hex(_pixel(tmp_path, root, (100, 25))) == TIER


def test_a_gradient_stop_is_painted_because_it_is_written_inline(tmp_path):
    """The behaviour that forced the special case: a class rule alone would draw `HOUSE`."""
    root = _svg(
        '<rect x="0" y="0" width="200" height="100" fill="url(#g)"/>',
        defs='<linearGradient id="g" x1="0" x2="1">'
             f'<stop offset="0" stop-color="{HOUSE}" class="colour-stop-wash"/>'
             f'<stop offset="1" stop-color="{HOUSE}" class="colour-stop-wash"/>'
             "</linearGradient>",
    )
    apply_palette(root, {"wash": TIER})
    assert _hex(_pixel(tmp_path, root, (100, 50))) == TIER


def test_a_class_rule_alone_does_not_reach_a_stop(tmp_path):
    """The negative half of the rule above, stated so nobody 'simplifies' the inline write.

    If a future Inkscape gains support for this, the test fails and says so — at which point
    the special case may go. Until then, removing it silently stops recolouring gradients.
    """
    root = _svg(
        '<rect x="0" y="0" width="200" height="100" fill="url(#g)"/>',
        defs='<linearGradient id="g" x1="0" x2="1">'
             f'<stop offset="0" stop-color="{HOUSE}" class="stopmark"/>'
             f'<stop offset="1" stop-color="{HOUSE}" class="stopmark"/>'
             "</linearGradient>"
             f"<style>.stopmark {{ stop-color:{TIER} }}</style>",
    )
    assert _hex(_pixel(tmp_path, root, (100, 50))) == HOUSE


def test_an_unconfigured_slot_keeps_the_templates_colour(tmp_path):
    """With no palette, a marked template rasterises exactly as it did before the feature."""
    root = _svg(
        f'<rect x="0" y="0" width="200" height="100" fill="{HOUSE}" class="colour-fill-accent"/>'
    )
    apply_palette(root, {})
    assert _hex(_pixel(tmp_path, root, (100, 50))) == HOUSE


def test_a_custom_property_would_destroy_the_whole_stylesheet(tmp_path):
    """Why `var()` is not the mechanism, kept as evidence rather than a claim in a docstring.

    libcroco rejects the custom-property declaration and discards **every** rule in the
    block, so the unrelated `.other` rule below loses its fill too and the rect draws black.
    That is the failure mode the class-based design exists to avoid.
    """
    root = _svg(
        '<rect x="0" y="0" width="200" height="100" class="other"/>',
        defs=f"<style>:root {{ --tier:{TIER} }} .other {{ fill:{HOUSE} }}</style>",
    )
    assert _hex(_pixel(tmp_path, root, (100, 50))) != HOUSE
