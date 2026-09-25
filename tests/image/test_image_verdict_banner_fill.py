"""Projecting a verdict banner onto its template.

The banner's fill has one rule of its own worth pinning: **the round's optional trio stand
or fall together.** `race_name`, `country_name` and `track_flag` all come from the one
`tracks` row the round's circuit name matches, so a round matching none loses all three at
once — each with its own group where the template declares one, and none of them raising a
notice. A round the server holds no track record for is an ordinary state, not a degraded
render, which is the reading the forecasts already take of the same three fields.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from services.image_verdict_banner_service import (  # noqa: E402
    TEMPLATE_KEY,
    VerdictBannerDrawing,
    build_fill_spec,
    resolve_drawing,
)
from utils.svg_document import load_svg, parse_svg_bytes  # noqa: E402
from utils.svg_fill import fill  # noqa: E402

SHIPPED = (
    Path(__file__).resolve().parents[2]
    / "resources" / "defaults" / "templates" / "verdict_banner_template.svg"
)
RESOURCES = Path(__file__).resolve().parents[2] / "resources" / "defaults"
FLAGS = RESOURCES / "flags"
#: `resolve_configured_directories` appends the logo pair to whatever a caller lists,
#: so a render always has one. A test that omitted it would leave the slot unresolvable.
DIRECTORIES = {"flag": FLAGS, "division_logo": RESOURCES / "division-logos"}

#: A template declaring every field the catalogue admits, groups included — what a league's
#: own file may look like, and what the shipped one deliberately is not.
FULL_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="304">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b'<g id="season_number_group"><text id="season_number">1</text></g>'
    b'<g id="division_tier_group"><text id="division_tier">1</text></g>'
    b'<g id="race_name_group"><text id="race_name">R</text></g>'
    b'<g id="country_name_group"><text id="country_name">C</text></g>'
    b'<g id="track_flag_group"><image id="track_flag" width="150" height="100"/></g>'
    b'<image id="division_logo" width="160" height="120"/>'
    b"</svg>"
)

#: A bare file: the two mandatory fields and not one group.
BARE_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="304">'
    b'<text id="division_name">D</text>'
    b'<text id="round_number">1</text>'
    b"</svg>"
)

FULL = dict(
    division_name="Pit Wall Premier",
    round_number=8,
    season_number=5,
    division_tier=1,
    race_name="British Grand Prix",
    country_name="United Kingdom",
)


def _spec(svg: bytes, **overrides):
    root = parse_svg_bytes(svg)
    drawing = resolve_drawing(**{**FULL, **overrides})
    return root, build_fill_spec(drawing, root, asset_directories=DIRECTORIES)


# ── resolve_drawing ───────────────────────────────────────────────────────

def test_a_blank_reads_as_absent():
    """`LEFT JOIN tracks` gives NULL for no record and "" for a blank column."""
    drawing = resolve_drawing(division_name="D", round_number=1, race_name="   ",
                              country_name="")
    assert drawing.race_name is None
    assert drawing.country_name is None
    assert drawing.names_a_track is False


def test_a_round_with_a_track_record_names_one():
    assert resolve_drawing(**FULL).names_a_track is True


def test_the_drawing_names_its_own_template():
    assert VerdictBannerDrawing(division_name="D", round_number=1).template_key == TEMPLATE_KEY


# ── The full template ─────────────────────────────────────────────────────

def test_every_field_is_filled_when_the_round_holds_a_track():
    _, spec = _spec(FULL_SVG)
    assert spec.text == {
        "division_name": "Pit Wall Premier",
        "round_number": "8",
        "season_number": "5",
        "division_tier": "1",
        "race_name": "British Grand Prix",
        "country_name": "United Kingdom",
    }
    assert spec.remove == []
    assert spec.empty == []


def test_the_flag_resolves_from_the_country_and_never_from_a_path():
    _, spec = _spec(FULL_SVG)
    assert spec.image_data["track_flag"] == ("flag", "United Kingdom")


def test_the_division_logo_is_addressed_where_the_template_declares_the_slot():
    _, spec = _spec(FULL_SVG)
    assert spec.image_data["division_logo"] == ("division_logo", "Pit Wall Premier")


def test_a_template_declaring_no_logo_is_asked_for_none():
    _, spec = _spec(BARE_SVG)
    assert "division_logo" not in spec.image_data


# ── The round that matched no track record ────────────────────────────────

def test_the_optional_trio_leave_together():
    _, spec = _spec(FULL_SVG, race_name=None, country_name=None)
    assert set(spec.remove) == {"race_name_group", "country_name_group", "track_flag_group"}
    assert spec.empty == []


def test_nothing_is_reported_for_a_round_holding_no_track_record():
    """An ordinary state, not a degraded render — so no notice and nothing unresolved."""
    root, spec = _spec(FULL_SVG, race_name=None, country_name=None)
    result = fill(spec)
    assert result.unresolved == []
    assert [n.notice_kind for n in result.notices if n.notice_kind != "FONT_SUBSTITUTED"] == []


def test_a_removed_group_is_exempted_from_the_mandatory_check():
    """The group ids, as the forecasts record them.

    The fields inside are not listed here and do not need to be: `fill` detaches the whole
    subtree and records every id it held, so a field removed with its group is never read
    as one left unfilled.
    """
    _, spec = _spec(FULL_SVG, race_name=None, country_name=None)
    assert {"race_name_group", "country_name_group", "track_flag_group"} <= spec.off_canvas


def test_a_bare_template_loses_nothing_it_never_declared():
    _, spec = _spec(BARE_SVG, race_name=None, country_name=None)
    assert spec.remove == []
    assert spec.empty == []
    assert spec.text == {"division_name": "Pit Wall Premier", "round_number": "8"}


# ── The shipped file ──────────────────────────────────────────────────────

def test_the_shipped_banner_fills_and_resolves():
    root = load_svg(SHIPPED)
    spec = build_fill_spec(resolve_drawing(**FULL), root, asset_directories=DIRECTORIES)
    result = fill(spec)
    assert result.unresolved == []


def test_the_shipped_banner_fills_with_no_track_record_either():
    root = load_svg(SHIPPED)
    drawing = resolve_drawing(**{**FULL, "race_name": None, "country_name": None})
    result = fill(build_fill_spec(drawing, root, asset_directories=DIRECTORIES))
    assert result.unresolved == []
    #  The round keeps the headline, which is why it was put there.
    assert 'id="round_number"' in result.svg.decode("utf-8")
