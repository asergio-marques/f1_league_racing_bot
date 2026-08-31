"""The nine Constitution XIV invariants — T053, T054, T055.

These are the places where a plausible implementation of the fill engine is quietly
wrong. Each maps to a numbered invariant in
specs/035-image-module/contracts/render-service.md.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (  # noqa: E402
    NOTICE_FIELD_REDUCED,
    NOTICE_FONT_SUBSTITUTED,
)
from utils.font_metrics import resolve_family  # noqa: E402
from utils.svg_document import (  # noqa: E402
    computed_style,
    FieldIndex,
    parse_svg_bytes,
    stylesheet,
)
from utils.svg_fill import FillSpec, fill  # noqa: E402


def _doc(body: str, width: int = 1200, height: int = 675):
    return parse_svg_bytes(
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{width}" height="{height}">{body}</svg>'.encode()
    )


def _style_of(root, element_id):
    element = FieldIndex(root).resolve(element_id)
    return computed_style(element, stylesheet(root))


ARIAL = resolve_family("Arial")


def _non_font_notices(result):
    """Drop FONT_SUBSTITUTED: whether Arial itself is installed is a fact about the
    host, not the fitting behaviour these invariants pin."""
    return [n for n in result.notices if n.notice_kind != NOTICE_FONT_SUBSTITUTED]


# ══════════════════════════════════════════════════════════════════════════
# T053 — Invariants 1-3: recolour
# ══════════════════════════════════════════════════════════════════════════


def test_invariant_1_recolour_merges_and_preserves_other_declarations():
    """Overwriting `style` would discard what the template set on the same element."""
    root = _doc(
        '<text id="fl" style="font-variant-numeric:tabular-nums;letter-spacing:0.5px">x</text>'
    )
    fill(FillSpec(root=root, text={"fl": "1:23.456"}, recolour={"fl": "#A020F0"}))

    style = _style_of(root, "fl")
    assert style["fill"] == "#A020F0"
    assert style["font-variant-numeric"] == "tabular-nums", "sibling declaration lost"
    assert style["letter-spacing"] == "0.5px", "sibling declaration lost"


def test_invariant_2_recolour_is_written_inline_so_it_beats_the_stylesheet():
    """A presentation attribute would lose to the template's own stylesheet."""
    root = _doc('<style>#fl { fill: #111111; }</style><text id="fl">x</text>')
    fill(FillSpec(root=root, text={"fl": "1:23.456"}, recolour={"fl": "#A020F0"}))

    element = FieldIndex(root).resolve("fl")
    assert "fill:#A020F0" in element.get("style").replace(" ", "")
    assert _style_of(root, "fl")["fill"] == "#A020F0"


def test_invariant_3_recolour_does_not_consume_the_field():
    """A coloured field still has to be filled, or the unresolved check stops being honest."""
    root = _doc('<text id="fl">x</text><text id="other">y</text>')
    result = fill(
        FillSpec(
            root=root,
            recolour={"fl": "#A020F0"},
            text={"other": "filled"},
            expected_fields={"fl", "other"},
        )
    )

    assert any("`fl`" in problem for problem in result.unresolved)
    assert not any("`other`" in problem for problem in result.unresolved)


def test_recolour_of_an_unknown_field_is_a_problem():
    root = _doc('<text id="fl">x</text>')
    result = fill(FillSpec(root=root, recolour={"nope": "#A020F0"}))
    assert any("unknown field `nope`" in problem for problem in result.unresolved)


# ══════════════════════════════════════════════════════════════════════════
# T054 — Invariants 4-6: canvas and crop
# ══════════════════════════════════════════════════════════════════════════


def test_invariant_4_each_template_renders_at_its_own_declared_canvas():
    """No fixed canvas is assumed for any image type."""
    wide = fill(FillSpec(root=_doc('<text id="a">x</text>', 1200, 675), text={"a": "1"}))
    tall = fill(FillSpec(root=_doc('<text id="a">x</text>', 1200, 1212), text={"a": "1"}))

    assert wide.canvas == (1200, 675)
    assert tall.canvas == (1200, 1212)


def test_invariant_5_crop_rewrites_both_height_and_viewbox():
    """The cut is made in the SVG, not delegated to the rasteriser's export area."""
    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="876" '
        b'viewBox="0 0 1200 876"><rect id="cut" y="500"/></svg>'
    )
    result = fill(FillSpec(root=root, crop="cut"))

    assert root.get("height") == "500"
    assert root.get("viewBox") == "0 0 1200 500"
    assert result.canvas == (1200, 500)


def test_crop_does_not_scale_the_drawing():
    """Rewriting height alone would scale rather than cut — the viewBox must follow."""
    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="876" '
        b'viewBox="0 0 1200 876"><rect id="cut" y="438"/></svg>'
    )
    fill(FillSpec(root=root, crop="cut"))

    _, _, vb_w, vb_h = root.get("viewBox").split()
    assert int(vb_w) == 1200, "width must be untouched by a vertical crop"
    assert int(vb_h) == int(root.get("height")), "viewBox height must track the canvas"


def test_invariant_6_unfilled_field_below_the_crop_is_not_a_problem():
    """A field the cut took off the canvas is not a field left unfilled."""
    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="876">'
        b'<text id="round_1" y="100">a</text>'
        b'<text id="round_9" y="700">b</text>'
        b'<rect id="cut" y="500"/></svg>'
    )
    result = fill(
        FillSpec(
            root=root,
            text={"round_1": "Silverstone"},
            crop="cut",
            expected_fields={"round_1", "round_9"},
        )
    )

    assert result.unresolved == [], result.unresolved


def test_unfilled_field_above_the_crop_is_still_a_problem():
    root = parse_svg_bytes(
        b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="876">'
        b'<text id="round_1" y="100">a</text>'
        b'<text id="round_2" y="200">b</text>'
        b'<rect id="cut" y="500"/></svg>'
    )
    result = fill(
        FillSpec(
            root=root,
            text={"round_1": "Silverstone"},
            crop="cut",
            expected_fields={"round_1", "round_2"},
        )
    )

    assert any("`round_2`" in problem for problem in result.unresolved)


def test_group_removal_takes_its_fields_with_it():
    root = _doc(
        '<g id="round_12_group"><text id="round_12">x</text></g><text id="round_1">y</text>'
    )
    result = fill(
        FillSpec(
            root=root,
            text={"round_1": "Monza"},
            remove=["round_12_group"],
            expected_fields={"round_1", "round_12"},
        )
    )

    assert result.unresolved == []
    assert "round_12" not in FieldIndex(root)


def test_removing_an_unknown_group_is_a_problem():
    root = _doc('<text id="a">x</text>')
    result = fill(FillSpec(root=root, remove=["nope"]))
    assert any("unknown group `nope`" in problem for problem in result.unresolved)


def test_unknown_crop_point_is_a_problem():
    root = _doc('<text id="a">x</text>')
    result = fill(FillSpec(root=root, crop="nope"))
    assert any("unknown crop point `nope`" in problem for problem in result.unresolved)


# ══════════════════════════════════════════════════════════════════════════
# T055 — Invariants 7-9: text bounds
# ══════════════════════════════════════════════════════════════════════════


WRAP_DOC = (
    '<rect id="box" x="10" y="20" width="300" height="120"/>'
    '<text id="justification" style="font-family:Arial;font-size:20px;'
    'line-height:1.3;shape-inside:url(#box)">placeholder</text>'
)


def _drawn_text(root, field_id):
    """Everything the field draws, whether as one string or as wrapped tspans."""
    element = FieldIndex(root).resolve(field_id)
    tspans = list(element)
    if tspans:
        return " ".join((t.text or "") for t in tspans)
    return element.text or ""


def test_invariant_7_short_text_is_not_reduced():
    root = _doc(WRAP_DOC)
    result = fill(FillSpec(root=root, text={"justification": "Short enough."}))

    assert _non_font_notices(result) == []
    assert "font-size:20px" in FieldIndex(root).resolve("justification").get("style")


def test_invariant_7_long_text_descends_in_half_pixel_steps():
    root = _doc(WRAP_DOC)
    body = "The stewards reviewed the incident at turn four in detail. " * 4
    fill(FillSpec(root=root, text={"justification": body}))

    style = FieldIndex(root).resolve("justification").get("style")
    size = float(style.split("font-size:")[1].split("px")[0])

    assert size < 20.0, "text that does not fit must be set down"
    assert (round(size * 2) / 2) == pytest.approx(size), "steps must be half a pixel"


def test_invariant_7_the_floor_raises_a_notice_but_stops_nothing():
    """v7.0.0: half the declared size is where a notice is owed, not where reducing stops.

    The pre-v7 engine stopped here and ellipsised. It now carries on down, because a cut value
    is a wrong value drawn confidently while a reduced one is merely small.
    """
    root = _doc(WRAP_DOC)
    body = "The stewards reviewed the incident at turn four in considerable detail. " * 60
    result = fill(FillSpec(root=root, text={"justification": body}))

    assert [n.notice_kind for n in _non_font_notices(result)] == [NOTICE_FIELD_REDUCED]

    style = FieldIndex(root).resolve("justification").get("style")
    size = float(style.split("font-size:")[1].split("px")[0])
    assert size < 10.0, "the reduction must continue past the 10px floor of a 20px field"


def test_invariant_7_text_is_never_cut_however_long():
    """The whole value is drawn, whatever it costs in size. Nothing is ellipsised."""
    root = _doc(WRAP_DOC)
    body = "The stewards reviewed the incident at turn four in considerable detail. " * 60
    fill(FillSpec(root=root, text={"justification": body}))

    drawn = _drawn_text(root, "justification")
    assert "…" not in drawn
    # Every word of the value survives, in order.
    assert drawn.split() == body.split()


def test_invariant_8_line_count_is_recomputed_at_the_reduced_leading():
    """The floor buys more room than the same line count set smaller.

    If leading did not scale, halving the size would give the same number of lines in
    the same box. Because it does scale, the box admits roughly twice as many.
    """
    from utils.svg_fill import _wrap  # noqa: PLC0415

    box_height, box_width, declared, ratio = 120.0, 300.0, 20.0, 1.3
    body = "The stewards reviewed the incident at turn four in detail. " * 8

    at_full = int(box_height // (declared * ratio))
    at_floor = int(box_height // ((declared / 2) * ratio))

    assert at_floor > at_full
    assert at_floor == pytest.approx(at_full * 2, abs=1)

    # And the reduced size genuinely fits more words per line as well.
    lines_full = _wrap(body, ARIAL, declared, box_width)
    lines_floor = _wrap(body, ARIAL, declared / 2, box_width)
    assert len(lines_floor) < len(lines_full)


def test_invariant_8_tspans_carry_absolute_y_at_the_reduced_leading():
    root = _doc(WRAP_DOC)
    body = "The stewards reviewed the incident at turn four in detail. " * 3
    fill(FillSpec(root=root, text={"justification": body}))

    element = FieldIndex(root).resolve("justification")
    tspans = list(element)
    assert len(tspans) >= 2

    ys = [float(t.get("y")) for t in tspans]
    gaps = {round(b - a, 3) for a, b in zip(ys, ys[1:])}
    assert len(gaps) == 1, "lines must be evenly spaced at the reduced leading"

    size = float(element.get("style").split("font-size:")[1].split("px")[0])
    assert gaps.pop() == pytest.approx(size * 1.3, abs=0.01)

    # Every line carries the field's own x.
    assert {t.get("x") for t in tspans} == {"10"}


def test_shape_inside_is_removed_not_set_to_none_after_lay_out():
    """Inkscape treats *any* shape-inside — `none` included — as flowed text.

    A `<text>` still carrying the declaration has its per-tspan positions ignored and
    collapses to the top edge of the canvas, so the declaration must go entirely.
    """
    root = _doc(WRAP_DOC)
    fill(FillSpec(root=root, text={"justification": "A short verdict."}))

    element = FieldIndex(root).resolve("justification")
    assert "shape-inside" not in (element.get("style") or "")
    assert "shape-inside" not in computed_style(element, stylesheet(root))


def test_shape_inside_from_an_id_rule_is_stripped_too():
    # The rule carries `line-height` because a wrapped field without one is a problem and
    # never reaches layout (XIV.5, v4.8.0). Inherited from the stylesheet counts, which is
    # the same route this test is exercising for `shape-inside`.
    root = _doc(
        '<style>#j { shape-inside: url(#box); font-size: 20px; line-height: 1.3; }</style>'
        '<rect id="box" x="10" y="20" width="300" height="120"/>'
        '<text id="j" style="font-family:Arial">placeholder</text>'
    )
    fill(FillSpec(root=root, text={"j": "A short verdict."}))

    element = FieldIndex(root).resolve("j")
    assert "shape-inside" not in computed_style(element, stylesheet(root))
    # The rule's other declarations survive.
    assert "font-size" in stylesheet(root).get("#j", {})


def test_wrap_consumes_the_rectangle_as_an_addressed_field():
    root = _doc(WRAP_DOC)
    result = fill(
        FillSpec(
            root=root,
            text={"justification": "A short verdict."},
            expected_fields={"justification", "box"},
        )
    )
    assert result.unresolved == []


NAME_DOC = (
    '<text id="driver_1" x="20" y="40" '
    'style="font-family:Arial;font-size:18px;inline-size:190px">x</text>'
)
LONG_NAME = "Bartholomew Fotheringay-Pemberton the Third"


def test_invariant_9_over_long_single_line_field_is_reduced_not_cut():
    """`inline-size` is the only bound on a Discord display name.

    A name is the one thing on a graphic a league cannot shorten, so v7.0.0 reduces it to fit
    rather than cutting it — the reader gets the whole name, merely smaller.
    """
    root = _doc(NAME_DOC)
    result = fill(FillSpec(root=root, text={"driver_1": LONG_NAME}))

    element = FieldIndex(root).resolve("driver_1")
    assert element.text == LONG_NAME, "the whole name must be drawn"
    assert "…" not in element.text

    size = float(element.get("style").split("font-size:")[1].split("px")[0])
    assert size < 18.0, "a name that does not fit must be set down"
    assert not list(element), "max-lines defaults to one: a name must not wrap"

    # A notice is owed only where the reduction passed below half the declared size.
    kinds = [n.notice_kind for n in _non_font_notices(result)]
    assert kinds in ([], [NOTICE_FIELD_REDUCED])
    assert (kinds == [NOTICE_FIELD_REDUCED]) == (size < 9.0)


def test_invariant_9_short_name_within_the_bound_is_untouched():
    root = _doc(NAME_DOC)
    result = fill(FillSpec(root=root, text={"driver_1": "Verstappen"}))

    element = FieldIndex(root).resolve("driver_1")
    assert _non_font_notices(result) == []
    assert element.text == "Verstappen"
    # A bounded field that fits keeps the size and the baseline the template drew, so
    # bounding a field that never overflows moves nothing on the graphic.
    assert "font-size:18px" in element.get("style")
    assert element.get("y") == "40"


def test_single_word_wider_than_the_box_is_reduced_whole():
    root = _doc(
        '<text id="d" x="0" y="30" '
        'style="font-family:Arial;font-size:18px;inline-size:40px">x</text>'
    )
    fill(FillSpec(root=root, text={"d": "Fotheringay-Pemberton"}))

    element = FieldIndex(root).resolve("d")
    assert element.text == "Fotheringay-Pemberton"
    assert "…" not in element.text


# ══════════════════════════════════════════════════════════════════════════
# `max-lines`: the budget, the CSS-declared box, and vertical centring (v7.0.0)
# ══════════════════════════════════════════════════════════════════════════


#: A two-line CSS box: 100px wide, 20px text on a 30px leading, centred on y=100.
BOXED = (
    '<text id="gp" x="50" y="100" style="font-family:Arial;font-size:20px;'
    'line-height:1.5;inline-size:100px;max-lines:{budget}">x</text>'
)


def _tspan_ys(root, field_id):
    return [float(t.get("y")) for t in FieldIndex(root).resolve(field_id)]


def test_max_lines_of_one_never_wraps():
    root = _doc(BOXED.format(budget=1))
    fill(FillSpec(root=root, text={"gp": "United States Grand Prix"}))

    element = FieldIndex(root).resolve("gp")
    assert not list(element), "a one-line budget must not produce tspans"
    assert element.text == "United States Grand Prix"


def test_max_lines_of_two_wraps_rather_than_reducing_as_far():
    """The point of a second line: the value is drawn larger than one line would allow."""
    value = "United States Grand Prix"
    sizes = {}
    for budget in (1, 2):
        root = _doc(BOXED.format(budget=budget))
        fill(FillSpec(root=root, text={"gp": value}))
        style = FieldIndex(root).resolve("gp").get("style")
        sizes[budget] = float(style.split("font-size:")[1].split("px")[0])

    assert sizes[2] > sizes[1], "two lines must buy a larger size than one"


def test_a_wrapped_field_is_centred_on_the_baseline_the_template_drew():
    """One line and two must share a centre, so bounding a field moves nothing when it fits.

    This is what lets a template be bounded without re-laying it out: a value that does not
    wrap lands exactly where the designer put it, and one that does grows half a line either
    side of that same point instead of pushing down onto whatever sits below.
    """
    root = _doc(BOXED.format(budget=2))
    fill(FillSpec(root=root, text={"gp": "United States Grand Prix"}))

    ys = _tspan_ys(root, "gp")
    assert len(ys) == 2, "this value must wrap to two lines"
    assert sum(ys) / len(ys) == pytest.approx(100.0), "the block must centre on y=100"

    # And a value that fits keeps the baseline untouched rather than merely centring on it.
    root = _doc(BOXED.format(budget=2))
    fill(FillSpec(root=root, text={"gp": "Imola"}))
    element = FieldIndex(root).resolve("gp")
    assert not list(element)
    assert element.get("y") == "100"


def test_a_wrapped_field_keeps_its_own_x_and_anchoring():
    """The calendar's date and time are anchored at their right edge.

    Taking x from a box's left edge instead — which the rectangle path does — would push an
    end-anchored field clean across the card.
    """
    root = _doc(
        '<text id="date" x="556" y="279" text-anchor="end" '
        'style="font-family:Arial;font-size:13px;line-height:1.2;'
        'inline-size:80px;max-lines:2">x</text>'
    )
    fill(FillSpec(root=root, text={"date": "01 January 2025"}))

    element = FieldIndex(root).resolve("date")
    assert element.get("text-anchor") == "end", "anchoring must survive"
    assert {t.get("x") for t in element} == {"556"}, "lines must carry the field's own x"


def test_prose_in_a_rectangle_is_laid_from_the_top_not_centred():
    """The `shape-inside` exception: prose floating mid-box reads as a mistake."""
    root = _doc(WRAP_DOC)  # rect at y=20, height 120, 20px text on a 1.3 leading
    fill(FillSpec(root=root, text={"justification": "One short line."}))

    element = FieldIndex(root).resolve("justification")
    ys = [float(t.get("y")) for t in element]
    assert ys == [pytest.approx(40.0)], "first baseline sits at the box top plus one size"


def test_max_lines_that_is_not_a_positive_whole_number_is_a_problem():
    for bad in ("0", "-2", "1.5", "many"):
        root = _doc(
            f'<text id="d" x="0" y="30" style="font-family:Arial;font-size:18px;'
            f'inline-size:100px;max-lines:{bad}">x</text>'
        )
        result = fill(FillSpec(root=root, text={"d": "Anything"}))
        assert result.unresolved, f"`max-lines:{bad}` must be fatal"
        assert any("max-lines" in problem for problem in result.unresolved)
        assert any("`d`" in problem for problem in result.unresolved)


def test_a_multi_line_budget_with_no_width_to_wrap_against_is_a_problem():
    root = _doc(
        '<text id="d" x="0" y="30" '
        'style="font-family:Arial;font-size:18px;line-height:1.2;max-lines:2">x</text>'
    )
    result = fill(FillSpec(root=root, text={"d": "Anything at all"}))

    assert any("inline-size" in problem for problem in result.unresolved)


def test_a_multi_line_budget_with_no_leading_is_a_problem():
    """No default leading may be substituted: it decides where the second line lands."""
    root = _doc(
        '<text id="d" x="0" y="30" '
        'style="font-family:Arial;font-size:18px;inline-size:100px;max-lines:2">x</text>'
    )
    result = fill(FillSpec(root=root, text={"d": "Anything at all"}))

    assert any("line-height" in problem for problem in result.unresolved)


def test_inline_size_is_removed_not_set_to_auto():
    """Inkscape treats *any* inline-size — `auto` included — as SVG2 flowed text.

    Left in place it re-flows the lines this module already measured: it concatenates the
    adjacent tspans, losing the space between them, and re-breaks the result. "…Enzo e" +
    "Dino Ferrari" comes back as "…Enzo" / "eDino Ferrari". Verified against Inkscape 1.x,
    where `auto` rasterises byte-identically to the declared length.
    """
    root = _doc(
        '<defs><style>.t { font-size:20px; inline-size:100px; max-lines:2; '
        'line-height:1.5 }</style></defs>'
        '<text id="gp" class="t" x="50" y="100">x</text>'
    )
    fill(FillSpec(root=root, text={"gp": "United States Grand Prix"}))

    element = FieldIndex(root).resolve("gp")
    assert len(list(element)) == 2, "this value must wrap, or the test proves nothing"
    assert "inline-size" not in (element.get("style") or "")
    # And the class rule it came from is stripped too, an inline `auto` being no defence.
    assert "inline-size" not in computed_style(element, stylesheet(root))


def test_max_lines_supersedes_the_rectangles_own_height():
    """A rectangle deep enough for four lines holds two where the field declares two."""
    root = _doc(
        '<rect id="box" x="10" y="20" width="300" height="120"/>'
        '<text id="justification" style="font-family:Arial;font-size:20px;'
        'line-height:1.3;shape-inside:url(#box);max-lines:2">placeholder</text>'
    )
    body = "The stewards reviewed the incident at turn four in some detail."
    fill(FillSpec(root=root, text={"justification": body}))

    assert len(_tspan_ys(root, "justification")) <= 2


def test_field_without_a_bound_is_never_truncated():
    root = _doc('<text id="d" style="font-family:Arial;font-size:18px">x</text>')
    name = "Bartholomew Fotheringay-Pemberton the Third"
    result = fill(FillSpec(root=root, text={"d": name}))

    assert _non_font_notices(result) == []
    assert FieldIndex(root).resolve("d").text == name


# ══════════════════════════════════════════════════════════════════════════
# Image fill, and reporting rather than raising
# ══════════════════════════════════════════════════════════════════════════


def test_image_fill_rewrites_href():
    """A relative path is anchored to the project root and leaves as a `file://` URI.

    It used to be written through untouched. The rasteriser reads the filled SVG out of a
    temporary directory and resolves a relative href against *that*, so the file was
    silently absent from the picture while every check upstream reported success. See
    `_as_href`.
    """
    import utils.paths as paths

    root = _doc('<image id="track" xlink:href="placeholder.svg"/>')
    result = fill(FillSpec(root=root, images={"track": "resources/defaults/tracks/fallback.svg"}))

    element = FieldIndex(root).resolve("track")
    expected = (Path(paths.PROJECT_ROOT) / "resources/defaults/tracks/fallback.svg").as_uri()
    assert element.get("{http://www.w3.org/1999/xlink}href") == expected
    assert element.get("href") == expected
    assert result.unresolved == []


def test_an_absolute_image_path_still_becomes_a_file_uri(tmp_path):
    """The behaviour that already stood, kept: an absolute path is never joined to."""
    asset = tmp_path / "monza.svg"
    asset.write_text("<svg/>", encoding="utf-8")

    root = _doc('<image id="track" xlink:href="placeholder.svg"/>')
    fill(FillSpec(root=root, images={"track": str(asset)}))

    element = FieldIndex(root).resolve("track")
    assert element.get("href") == asset.as_uri()


@pytest.mark.parametrize(
    "href",
    ["data:image/png;base64,AAAA", "https://example.invalid/flag.svg", "file:///tmp/a.svg"],
)
def test_an_href_carrying_a_scheme_is_passed_through(href):
    """A URI is not a path and must not be joined to the project root."""
    root = _doc('<image id="track" xlink:href="placeholder.svg"/>')
    fill(FillSpec(root=root, images={"track": href}))

    assert FieldIndex(root).resolve("track").get("href") == href


def test_a_template_authored_relative_href_still_resolves(monkeypatch, tmp_path):
    """A template may point at a file beside itself, and templates live under the root.

    Pinned rather than assumed: anchoring is only safe because the project root is the
    right base for a template-authored reference as well as a configured one.
    """
    import utils.paths as paths

    beside = tmp_path / "templates"
    beside.mkdir()
    (beside / "badge.svg").write_text("<svg/>", encoding="utf-8")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path)

    root = _doc('<image id="track" xlink:href="placeholder.svg"/>')
    fill(FillSpec(root=root, images={"track": "templates/badge.svg"}))

    element = FieldIndex(root).resolve("track")
    assert element.get("href") == (tmp_path / "templates" / "badge.svg").as_uri()


def test_unknown_image_field_is_a_problem():
    root = _doc('<image id="track"/>')
    result = fill(FillSpec(root=root, images={"nope": "x.svg"}))
    assert any("unknown image field `nope`" in p for p in result.unresolved)


def test_fill_reports_rather_than_raises_on_every_disagreement():
    """A data disagreement must never raise — the caller decides what to do."""
    root = _doc('<text id="a">x</text>')
    result = fill(
        FillSpec(
            root=root,
            text={"missing": "v"},
            images={"also_missing": "x.svg"},
            recolour={"gone": "#FFF"},
            remove=["absent_group"],
            crop="no_such_point",
            expected_fields={"a"},
        )
    )
    assert len(result.unresolved) >= 5
    assert isinstance(result.svg, bytes)


def test_result_svg_is_serialisable_and_wellformed():
    root = _doc('<text id="a">x</text>')
    result = fill(FillSpec(root=root, text={"a": "value"}))
    reparsed = parse_svg_bytes(result.svg)
    assert FieldIndex(reparsed).resolve("a").text == "value"


def test_expected_fields_omitted_means_only_unknown_fields_are_caught():
    root = _doc('<text id="a">x</text><text id="b">y</text>')
    result = fill(FillSpec(root=root, text={"a": "v"}))
    assert result.unresolved == []


# ---------------------------------------------------------------------------
# An absent datum drawing the class fallback (Constitution XIV.13, v4.4.0) — T015
#
# The three outcomes of a *sought* asset are unchanged. This is the fourth case: a
# field whose catalogue declares that having no datum at all is a state worth
# depicting. The fallback stands for the absence, so nothing is reported for it.
# ---------------------------------------------------------------------------

def _image_doc():
    return _doc('<image id="row_1_tyre" xlink:href="x.svg" width="40" height="40"/>')


def _tyre_dir(tmp_path, *, with_fallback: bool):
    directory = tmp_path / "tyres"
    directory.mkdir()
    (directory / "soft.svg").write_text("<svg/>", encoding="utf-8")
    if with_fallback:
        (directory / "fallback.svg").write_text("<svg/>", encoding="utf-8")
    return directory


def _qualifying_catalogue():
    from models.image_catalogues import RESULTS_QUALIFYING_CATALOGUE

    return RESULTS_QUALIFYING_CATALOGUE


def test_absent_datum_draws_the_class_fallback_and_reports_nothing(tmp_path):
    root = _image_doc()
    result = fill(
        FillSpec(
            root=root,
            image_type="results_qualifying_template",
            image_data={"row_1_tyre": ("tyre", "")},
            asset_directories={"tyre": _tyre_dir(tmp_path, with_fallback=True)},
            catalogue=_qualifying_catalogue(),
        )
    )
    assert result.unresolved == []
    assert result.notices == []
    href = FieldIndex(root).resolve("row_1_tyre").get(
        "{http://www.w3.org/1999/xlink}href"
    )
    assert href.endswith("fallback.svg")


def test_absent_datum_removes_the_field_where_the_class_has_no_fallback(
    tmp_path, monkeypatch
):
    """The declaration is inert without a fallback — and still not fatal, and still quiet.

    "Without a fallback" means **neither tier** since v6.0.0 (047 FR-043), so the packaged
    directory is put out of view here. With it in view the packaged tyre fallback answers,
    the field is drawn, and this branch is never reached.
    """
    import utils.paths as paths_module

    monkeypatch.setattr(paths_module, "PROJECT_ROOT", tmp_path / "elsewhere", raising=False)
    root = _image_doc()
    result = fill(
        FillSpec(
            root=root,
            image_type="results_qualifying_template",
            image_data={"row_1_tyre": ("tyre", "")},
            asset_directories={"tyre": _tyre_dir(tmp_path, with_fallback=False)},
            catalogue=_qualifying_catalogue(),
        )
    )
    assert result.unresolved == []
    assert result.notices == []
    assert FieldIndex(root).resolve("row_1_tyre") is None


def test_a_recorded_datum_still_resolves_its_own_file(tmp_path):
    root = _image_doc()
    result = fill(
        FillSpec(
            root=root,
            image_type="results_qualifying_template",
            image_data={"row_1_tyre": ("tyre", "Soft")},
            asset_directories={"tyre": _tyre_dir(tmp_path, with_fallback=True)},
            catalogue=_qualifying_catalogue(),
        )
    )
    assert result.unresolved == []
    assert result.notices == []
    href = FieldIndex(root).resolve("row_1_tyre").get(
        "{http://www.w3.org/1999/xlink}href"
    )
    assert href.endswith("soft.svg")


def test_a_missing_file_for_a_recorded_datum_still_raises_its_notice(tmp_path):
    """The declaration covers an absent *datum*, never a missing *file*."""
    root = _image_doc()
    result = fill(
        FillSpec(
            root=root,
            image_type="results_qualifying_template",
            image_data={"row_1_tyre": ("tyre", "Intermediate")},
            asset_directories={"tyre": _tyre_dir(tmp_path, with_fallback=True)},
            catalogue=_qualifying_catalogue(),
        )
    )
    assert result.unresolved == []
    assert [n.field_id for n in result.notices] == ["row_1_tyre"]


def test_an_undeclared_field_with_an_absent_datum_behaves_as_it_always_did(tmp_path):
    """A flag with no nationality is not this case: the fallback is drawn *with* a notice."""
    root = _doc('<image id="row_1_driver_flag" xlink:href="x.svg" width="40" height="40"/>')
    directory = tmp_path / "flags"
    directory.mkdir()
    (directory / "fallback.svg").write_text("<svg/>", encoding="utf-8")
    result = fill(
        FillSpec(
            root=root,
            image_type="results_qualifying_template",
            image_data={"row_1_driver_flag": ("flag", "")},
            asset_directories={"flag": directory},
            catalogue=_qualifying_catalogue(),
        )
    )
    assert result.unresolved == []
    assert [n.field_id for n in result.notices] == ["row_1_driver_flag"]


# ══════════════════════════════════════════════════════════════════════════
# 043 — the wrapping contract completed (XIV.5, v4.8.0)
#
# specs/043-verdicts-image-generation/contracts/text-wrapping.md. Verdicts is the
# module's first type to draw prose a person wrote, and these are the clauses the
# pipeline did not yet hold.
# ══════════════════════════════════════════════════════════════════════════


LONG_WORD = "A" * 400


def test_wrapped_field_breaks_a_word_wider_than_its_box_within_itself():
    """An over-wide word became its own over-wide line, running off the canvas."""
    root = _doc(WRAP_DOC)
    result = fill(FillSpec(root=root, text={"justification": LONG_WORD}))

    assert result.unresolved == []
    element = FieldIndex(root).resolve("justification")
    size = float(element.get("style").split("font-size:")[1].split("px")[0])
    resolved = resolve_family("Arial")

    from utils.font_metrics import measure  # noqa: PLC0415

    tspans = list(element)
    assert len(tspans) > 1, "a 400-character word must be broken across lines"
    for tspan in tspans:
        assert measure(tspan.text or "", resolved, size) <= 300.0 + 0.5, (
            f"line wider than its box: {tspan.text!r}"
        )


def test_single_line_field_breaks_a_word_wider_than_its_room_within_itself():
    """A word too wide is broken within itself, never cut (XIV.5, v7.0.0).

    400 characters in a 120px box fit at no font size at all, so the budget of one line cannot
    be honoured. The engine draws the whole value over budget and raises the notice rather than
    trimming to the budget, because trimming is the cut this version withdrew.
    """
    root = _doc(
        '<text id="dn" x="0" y="30" '
        'style="font-family:Arial;font-size:20px;inline-size:120px">x</text>'
    )
    result = fill(FillSpec(root=root, text={"dn": LONG_WORD}))

    assert result.unresolved == []
    drawn = _drawn_text(root, "dn").replace(" ", "")
    assert drawn == LONG_WORD, "every character must survive"
    assert "…" not in drawn
    assert [n.notice_kind for n in _non_font_notices(result)] == [NOTICE_FIELD_REDUCED]


def test_wrapped_field_with_no_resolvable_line_height_is_a_problem():
    """No default leading may be substituted: it decides how much prose is drawn."""
    root = _doc(
        '<rect id="box" x="10" y="20" width="300" height="120"/>'
        '<text id="justification" style="font-family:Arial;font-size:20px;'
        'shape-inside:url(#box)">placeholder</text>'
    )
    result = fill(FillSpec(root=root, text={"justification": "Anything at all."}))

    assert result.unresolved, "a wrapped field with no leading must be fatal"
    assert any("line-height" in problem for problem in result.unresolved)
    assert any("justification" in problem for problem in result.unresolved)


def test_inherited_line_height_satisfies_the_wrapped_field():
    """It may be declared on the field or inherited by it — the stylesheet counts."""
    root = _doc(
        '<style>#justification { line-height: 1.3; }</style>'
        '<rect id="box" x="10" y="20" width="300" height="120"/>'
        '<text id="justification" style="font-family:Arial;font-size:20px;'
        'shape-inside:url(#box)">placeholder</text>'
    )
    result = fill(FillSpec(root=root, text={"justification": "Anything at all."}))
    assert result.unresolved == []


def test_wrapped_field_whose_rectangle_has_no_extent_is_a_problem():
    """It silently wrote one unwrapped line: no error, and text across the canvas."""
    root = _doc(
        '<rect id="box" x="10" y="20"/>'
        '<text id="justification" style="font-family:Arial;font-size:20px;'
        'line-height:1.3;shape-inside:url(#box)">placeholder</text>'
    )
    result = fill(FillSpec(root=root, text={"justification": "Anything at all."}))

    assert result.unresolved, "a rectangle with no extent must be fatal"
    assert any("justification" in problem for problem in result.unresolved)
    assert any("box" in problem for problem in result.unresolved)


def test_wrapped_field_naming_a_missing_rectangle_is_a_problem():
    """Already held; pinned here beside its two siblings so the family stays together."""
    root = _doc(
        '<text id="justification" style="font-family:Arial;font-size:20px;'
        'line-height:1.3;shape-inside:url(#nope)">placeholder</text>'
    )
    result = fill(FillSpec(root=root, text={"justification": "Anything at all."}))

    assert any("nope" in problem for problem in result.unresolved)


# ── The packaged tier reaches the fill pipeline (047 FR-044) ──────────────


def test_the_fill_pipeline_passes_the_packaged_directory(tmp_path, monkeypatch):
    """One call site serves every graphic, so wiring it here wires it everywhere."""
    import utils.paths as paths_module
    from utils.svg_fill import _packaged_directory

    packaged = tmp_path / "resources" / "defaults" / "flags"
    packaged.mkdir(parents=True)
    monkeypatch.setattr(paths_module, "PROJECT_ROOT", tmp_path, raising=False)

    assert _packaged_directory("flag") == packaged


def test_an_unknown_asset_class_has_no_packaged_directory():
    from utils.svg_fill import _packaged_directory

    assert _packaged_directory("nonesuch") is None


def test_a_packaged_directory_that_is_not_there_leaves_the_tier_empty(tmp_path, monkeypatch):
    import utils.paths as paths_module
    from utils.svg_fill import _packaged_directory

    monkeypatch.setattr(paths_module, "PROJECT_ROOT", tmp_path, raising=False)

    assert _packaged_directory("flag") is None
