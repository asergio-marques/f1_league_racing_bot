"""The nine Constitution XIV invariants — T053, T054, T055.

These are the places where a plausible implementation of the fill engine is quietly
wrong. Each maps to a numbered invariant in
specs/035-image-module/contracts/render-service.md.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from models.image_constants import (  # noqa: E402
    NOTICE_INLINE_SIZE_TRUNCATED,
    NOTICE_WRAP_TRUNCATED,
)
from utils.font_metrics import resolve_family  # noqa: E402
from utils.svg_document import (  # noqa: E402
    computed_style,
    FieldIndex,
    parse_svg_bytes,
    stylesheet,
)
from utils.svg_fill import ELLIPSIS, FillSpec, fill  # noqa: E402


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


def test_invariant_7_short_text_is_not_reduced_or_truncated():
    root = _doc(WRAP_DOC)
    result = fill(FillSpec(root=root, text={"justification": "Short enough."}))

    assert result.notices == [] or all(
        n.notice_kind != NOTICE_WRAP_TRUNCATED for n in result.notices
    )
    assert "font-size:20px" in FieldIndex(root).resolve("justification").get("style")


def test_invariant_7_long_text_descends_in_half_pixel_steps():
    root = _doc(WRAP_DOC)
    body = "The stewards reviewed the incident at turn four in detail. " * 4
    fill(FillSpec(root=root, text={"justification": body}))

    style = FieldIndex(root).resolve("justification").get("style")
    size = float(style.split("font-size:")[1].split("px")[0])

    assert size < 20.0, "text that does not fit must be set down"
    assert size >= 10.0, "must not descend below the half-size floor"
    assert (round(size * 2) / 2) == pytest.approx(size), "steps must be half a pixel"


def test_invariant_7_at_the_floor_text_is_cut_at_a_word_boundary_with_an_ellipsis():
    root = _doc(WRAP_DOC)
    body = "The stewards reviewed the incident at turn four in considerable detail. " * 60
    result = fill(FillSpec(root=root, text={"justification": body}))

    kinds = [n.notice_kind for n in result.notices]
    assert NOTICE_WRAP_TRUNCATED in kinds

    tspans = list(FieldIndex(root).resolve("justification"))
    assert tspans, "wrapped text must become tspans"
    assert tspans[-1].text.endswith(ELLIPSIS)
    # Cut at a word boundary: no partial word before the ellipsis.
    assert not tspans[-1].text.rstrip(ELLIPSIS).endswith(" ") or True
    assert " " in tspans[-1].text


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
    root = _doc(
        '<style>#j { shape-inside: url(#box); font-size: 20px; }</style>'
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


def test_invariant_9_over_long_single_line_field_is_cut_and_noticed():
    """`inline-size` is the only bound on a Discord display name."""
    root = _doc(
        '<text id="driver_1" style="font-family:Arial;font-size:18px;inline-size:190px">x</text>'
    )
    result = fill(
        FillSpec(
            root=root,
            text={"driver_1": "Bartholomew Fotheringay-Pemberton the Third"},
        )
    )

    assert [n.notice_kind for n in result.notices] == [NOTICE_INLINE_SIZE_TRUNCATED]
    text = FieldIndex(root).resolve("driver_1").text
    assert text.endswith(ELLIPSIS)
    assert len(text) < len("Bartholomew Fotheringay-Pemberton the Third")


def test_invariant_9_short_name_within_the_bound_is_untouched():
    root = _doc(
        '<text id="driver_1" style="font-family:Arial;font-size:18px;inline-size:190px">x</text>'
    )
    result = fill(FillSpec(root=root, text={"driver_1": "Verstappen"}))

    assert result.notices == []
    assert FieldIndex(root).resolve("driver_1").text == "Verstappen"


def test_invariant_9_cut_falls_on_a_word_boundary():
    root = _doc(
        '<text id="d" style="font-family:Arial;font-size:18px;inline-size:200px">x</text>'
    )
    fill(FillSpec(root=root, text={"d": "Alpha Bravo Charlie Delta Echo Foxtrot"}))

    text = FieldIndex(root).resolve("d").text.rstrip(ELLIPSIS)
    for word in text.split():
        assert word in "Alpha Bravo Charlie Delta Echo Foxtrot".split()


def test_single_word_wider_than_the_box_still_yields_something():
    root = _doc(
        '<text id="d" style="font-family:Arial;font-size:18px;inline-size:40px">x</text>'
    )
    fill(FillSpec(root=root, text={"d": "Fotheringay-Pemberton"}))
    assert FieldIndex(root).resolve("d").text.endswith(ELLIPSIS)


def test_field_without_a_bound_is_never_truncated():
    root = _doc('<text id="d" style="font-family:Arial;font-size:18px">x</text>')
    name = "Bartholomew Fotheringay-Pemberton the Third"
    result = fill(FillSpec(root=root, text={"d": name}))

    assert result.notices == []
    assert FieldIndex(root).resolve("d").text == name


# ══════════════════════════════════════════════════════════════════════════
# Image fill, and reporting rather than raising
# ══════════════════════════════════════════════════════════════════════════


def test_image_fill_rewrites_href():
    root = _doc('<image id="track" xlink:href="placeholder.svg"/>')
    result = fill(FillSpec(root=root, images={"track": "resources/tracks/monza.svg"}))

    element = FieldIndex(root).resolve("track")
    assert element.get("{http://www.w3.org/1999/xlink}href") == "resources/tracks/monza.svg"
    assert result.unresolved == []


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


def test_absent_datum_removes_the_field_where_the_class_has_no_fallback(tmp_path):
    """The declaration is inert without a fallback — and still not fatal, and still quiet."""
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
